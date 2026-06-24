from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from core.observability import TraceRecorder
from core.schema.etf_report import ETFResearchReport
from core.task.state import compute_task_run_id
from core.valuation import (
    ETFFeatures,
    ETFReferenceValueRange,
    ETFValuationSignal,
    build_etf_signal,
    extract_etf_features,
    normalize_sleeve_key,
    normalize_valuation_model_type,
    reference_range_from_inputs,
)

from .conclusion import build_conclusion


REPORT_VERSION = "v2.0.0"
VALUATION_ENGINE_VERSION = "etf_valuation_engine.v2.type-aware"


def _safe_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return number


def _safe_str(value: object, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, str) else []


def _round_float(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def _canonical(value: object) -> object:
    if is_dataclass(value):
        return _canonical(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        return _round_float(value)
    return value


def compute_report_hash(
    *,
    etf_code: str,
    feature_inputs: object,
    valuation_outputs: object,
    signal_outputs: object,
) -> str:
    payload = {
        "report_version": REPORT_VERSION,
        "etf_code": etf_code,
        "feature_inputs": _canonical(feature_inputs),
        "valuation_outputs": _canonical(valuation_outputs),
        "signal_outputs": _canonical(signal_outputs),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _top_holdings(holdings_inputs: Mapping[str, Any]) -> list[str]:
    raw_holdings = _as_sequence(holdings_inputs.get("top_holdings"))
    result = []
    for item in raw_holdings:
        if isinstance(item, Mapping):
            name = _safe_str(item.get("name") or item.get("stock_name") or item.get("code"), "")
            weight = item.get("weight")
            result.append(f"{name} {weight}" if weight is not None else name)
        else:
            text = str(item).strip()
            if text:
                result.append(text)
    return result


def _role_score(input_data: Mapping[str, Any], sleeve_key: str = "") -> float:
    role = _safe_str(input_data.get("portfolio_role"), "")
    role_inputs = _as_mapping(input_data.get("role_inputs"))
    explicit = role_inputs.get("base_role_score")
    if explicit is not None:
        return _safe_float(explicit, 60.0)
    if sleeve_key == "core_wide_etf":
        return 78.0
    if sleeve_key == "defensive_quality":
        return 72.0
    if sleeve_key == "cash_like":
        return 80.0
    if sleeve_key == "mainline_etf":
        return 58.0
    if any(keyword in role for keyword in ["底仓", "宽基", "现金", "债券"]):
        return 75.0
    if any(keyword in role for keyword in ["行业", "主题", "卫星", "进攻"]):
        return 55.0
    return 60.0


def _valuation_confidence(features: ETFFeatures, value_range: ETFReferenceValueRange) -> str:
    if value_range.mid <= 0:
        return "low"
    if features.turnover_amount > 20_000_000 and features.fund_size > 1_000_000_000:
        return "high"
    return "medium"


def _risk_block(features: ETFFeatures, input_data: Mapping[str, Any]) -> dict[str, object]:
    risk_input = _as_mapping(input_data.get("risk_signals") or input_data.get("risk"))
    invalidation_conditions = [
        str(item).strip()
        for item in _as_sequence(risk_input.get("invalidation_conditions"))
        if str(item).strip()
    ]
    if not invalidation_conditions:
        invalidation_conditions = [
            "跟踪误差显著扩大",
            "成交额或规模持续萎缩",
            "底层指数估值分位进入过热区间",
        ]
    return {
        "liquidity_risk": _safe_str(
            risk_input.get("liquidity_risk"),
            "流动性风险来自成交额、基金规模和份额变化。",
        ),
        "tracking_risk": _safe_str(
            risk_input.get("tracking_risk"),
            "跟踪风险来自跟踪误差、折溢价和指数复制质量。",
        ),
        "concentration_risk": _safe_str(
            risk_input.get("concentration_risk"),
            "持仓集中度需要结合披露滞后的前十大持仓观察。",
        ),
        "sentiment_risk": _safe_str(
            risk_input.get("sentiment_risk"),
            "主题拥挤或市场风险偏好变化会影响 ETF 的阶段性定价。",
        ),
        "invalidation_conditions": invalidation_conditions,
    }


def build_etf_report(input_data: Mapping[str, Any], trace_recorder: TraceRecorder | None = None) -> ETFResearchReport:
    etf_code = _safe_str(input_data.get("etf_code") or input_data.get("code"), "000000.SH")
    etf_name = _safe_str(input_data.get("etf_name") or input_data.get("name"), etf_code)
    research_date = _safe_str(input_data.get("research_date"), "1970-01-01")
    task_type = _safe_str(input_data.get("task_type"), "valuation")
    run_id = compute_task_run_id(etf_code, task_type, research_date, "etf_research_report.v1")

    product_inputs = _as_mapping(input_data.get("product_profile") or input_data.get("product_inputs"))
    holdings_inputs = _as_mapping(input_data.get("holdings_profile") or input_data.get("holdings_inputs"))
    valuation_inputs = _as_mapping(input_data.get("valuation_inputs") or input_data.get("valuation"))
    model_hint = input_data.get("valuation_model_type") or product_inputs.get("valuation_model_type")
    model_type = normalize_valuation_model_type(model_hint, input_data)
    sleeve_key = normalize_sleeve_key(input_data.get("sleeve_key") or product_inputs.get("sleeve_key"), model_type)
    model_specific_inputs = _as_mapping(input_data.get("model_specific_inputs"))
    features = extract_etf_features(input_data)

    if trace_recorder is not None:
        trace_recorder.record(
            run_id=run_id,
            stage="feature",
            input_payload={
                "valuation_inputs": valuation_inputs,
                "model_specific_inputs": model_specific_inputs,
                "valuation_model_type": model_type,
                "liquidity_inputs": input_data.get("liquidity_inputs") or {},
                "tracking_inputs": input_data.get("tracking_inputs") or {},
                "holdings_inputs": holdings_inputs,
            },
            output_payload=features,
            diff_metrics={
                "valuation_percentile": _round_float(features.valuation_percentile),
                "premium_discount": _round_float(features.premium_discount),
                "tracking_error": _round_float(features.tracking_error),
            },
        )

    value_range = reference_range_from_inputs(
        dict(valuation_inputs),
        model_type=model_type,
        model_specific_inputs=dict(model_specific_inputs),
    )
    if trace_recorder is not None:
        trace_recorder.record(
            run_id=run_id,
            stage="valuation",
            input_payload={
                "features": features,
                "valuation_inputs": valuation_inputs,
                "model_specific_inputs": model_specific_inputs,
                "valuation_model_type": model_type,
            },
            output_payload=value_range,
            diff_metrics={
                "reference_value_mid": _round_float(value_range.mid),
                "nav": _round_float(_safe_float(valuation_inputs.get("nav") or valuation_inputs.get("unit_nav"))),
                "valuation_percentile": _round_float(features.valuation_percentile),
            },
        )

    signal = build_etf_signal(features=features, base_role_score=_role_score(input_data, sleeve_key), model_type=model_type)
    if trace_recorder is not None:
        trace_recorder.record(
            run_id=run_id,
            stage="signal",
            input_payload={"features": features, "value_range": value_range},
            output_payload=signal,
            diff_metrics={
                "undervalued_score": _round_float(signal.undervalued_score),
                "liquidity_score": _round_float(signal.liquidity_score),
                "tracking_score": _round_float(signal.tracking_score),
                "risk_adjusted_score": _round_float(signal.risk_adjusted_score),
            },
        )

    conclusion = build_conclusion(signal, model_type=model_type)
    report_hash = compute_report_hash(
        etf_code=etf_code,
        feature_inputs={
            "valuation_model_type": model_type,
            "sleeve_key": sleeve_key,
            "product": product_inputs,
            "holdings": holdings_inputs,
            "valuation": valuation_inputs,
            "model_specific": model_specific_inputs,
        },
        valuation_outputs=value_range,
        signal_outputs=signal,
    )
    risk = _risk_block(features, input_data)
    evidence_items = [
        item for item in _as_sequence(input_data.get("evidence")) if isinstance(item, Mapping)
    ] or [
        {
            "source": "deterministic-etf-report-assembler",
            "date": research_date,
            "url": "local",
            "purpose": "schema-first ETF report assembly",
            "detail": "ETF valuation, liquidity, tracking and role scores assembled without LLM calculation.",
        }
    ]

    payload = {
        "schema_version": "etf_research_report.v1",
        "report_version": REPORT_VERSION,
        "report_hash": report_hash,
        "etf_code": etf_code,
        "etf_name": etf_name,
        "source_report_id": input_data.get("source_report_id"),
        "task_type": task_type,
        "research_date": research_date,
        "status": _safe_str(input_data.get("status"), "complete"),
        "valuation_model_type": model_type,
        "sleeve_key": sleeve_key,
        "title": _safe_str(input_data.get("title"), f"{etf_name}ETF估值刷新"),
        "summary": _safe_str(input_data.get("summary"), conclusion.summary),
        "product_profile": {
            "fund_type": _safe_str(product_inputs.get("fund_type"), "ETF"),
            "tracking_index": product_inputs.get("tracking_index"),
            "asset_class": _safe_str(product_inputs.get("asset_class"), "待确认"),
            "valuation_model_type": model_type,
            "sleeve_key": sleeve_key,
            "portfolio_role": _safe_str(input_data.get("portfolio_role") or product_inputs.get("portfolio_role"), "观察工具"),
            "fee_note": _safe_str(product_inputs.get("fee_note"), "费率数据待补充。"),
            "liquidity_note": _safe_str(
                product_inputs.get("liquidity_note"),
                f"成交额与规模输入用于流动性评分，当前流动性分 {signal.liquidity_score:.1f}。",
            ),
            "tracking_note": _safe_str(
                product_inputs.get("tracking_note"),
                f"跟踪质量由跟踪误差和折溢价共同约束，当前跟踪分 {signal.tracking_score:.1f}。",
            ),
        },
        "holdings_profile": {
            "holdings_disclosure_date": holdings_inputs.get("holdings_disclosure_date"),
            "top_holdings": _top_holdings(holdings_inputs),
            "concentration_note": _safe_str(
                holdings_inputs.get("concentration_note"),
                f"前十大集中度输入为 {features.concentration_ratio:.2%}，需结合披露滞后观察。",
            ),
            "overlap_note": _safe_str(
                holdings_inputs.get("overlap_note"),
                "组合重叠数据如缺失，应在 data_gaps 中明确披露。",
            ),
            "disclosure_lag_note": _safe_str(
                holdings_inputs.get("disclosure_lag_note"),
                "fund_portfolio 只代表已披露季报持仓，不等同实时完整底仓。",
            ),
        },
        "valuation": {
            "current_price": _round_float(_safe_float(valuation_inputs.get("current_price"))),
            "nav": _round_float(_safe_float(valuation_inputs.get("nav") or valuation_inputs.get("unit_nav"))),
            "premium_discount": _round_float(features.premium_discount),
            "underlying_pe": _round_float(_safe_float(valuation_inputs.get("underlying_pe"))),
            "underlying_pb": _round_float(_safe_float(valuation_inputs.get("underlying_pb"))),
            "valuation_percentile": _round_float(features.valuation_percentile),
            "reference_value_low": _round_float(value_range.low),
            "reference_value_mid": _round_float(value_range.mid),
            "reference_value_high": _round_float(value_range.high),
            "unit": _safe_str(valuation_inputs.get("unit"), "CNY/fund_share"),
            "method": value_range.method,
            "confidence": _valuation_confidence(features, value_range),
            "key_assumptions": [
                f"reference range generated by deterministic {model_type} rules",
                "fund_portfolio holdings are disclosed and lagged, not real-time complete holdings",
            ],
            "engine_version": VALUATION_ENGINE_VERSION,
            "undervalued_score": _round_float(signal.undervalued_score),
            "liquidity_score": _round_float(signal.liquidity_score),
            "tracking_score": _round_float(signal.tracking_score),
            "portfolio_role_score": _round_float(signal.portfolio_role_score),
            "risk_adjusted_score": _round_float(signal.risk_adjusted_score),
            "mainline_validity_score": _round_float(signal.mainline_validity_score),
            "valuation_tolerance_score": _round_float(signal.valuation_tolerance_score),
            "crowding_risk_score": _round_float(signal.crowding_risk_score),
            "factor_premium_score": _round_float(signal.factor_premium_score),
            "cash_like_safety_score": _round_float(signal.cash_like_safety_score),
        },
        "base_position_view": conclusion.grade,
        "risk": risk,
        "conclusion": {
            "grade": conclusion.grade,
            "confidence": conclusion.confidence,
            "summary": conclusion.summary,
        },
        "evidence": evidence_items,
        "assumptions": [
            "same input gives same ETFResearchReport and report_hash",
            "LLM is not used during deterministic assembly",
        ],
        "data_gaps": [
            str(item).strip()
            for item in _as_sequence(input_data.get("data_gaps"))
            if str(item).strip()
        ],
    }
    report = ETFResearchReport(**payload)
    if trace_recorder is not None:
        trace_recorder.record(
            run_id=run_id,
            stage="report",
            input_payload={
                "features": features,
                "valuation_outputs": value_range,
                "signal_outputs": signal,
                "risk": risk,
                "conclusion": conclusion,
            },
            output_payload=report.model_dump(mode="json"),
            diff_metrics={
                "etf_code": etf_code,
                "report_hash": report.report_hash,
                "undervalued_score": _round_float(signal.undervalued_score),
                "risk_adjusted_score": _round_float(signal.risk_adjusted_score),
            },
        )
    return report
