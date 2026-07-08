from __future__ import annotations

import html
import json
import mimetypes
import re
import time
from contextlib import closing
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from core.decision import build_decision_signal, decision_signal_to_dict
from core.factors import (
    DEFAULT_FACTOR_REGISTRY,
    build_factor_exposure,
    compute_factor_ic,
    factor_definition_to_dict,
    factor_exposure_to_dict,
    factor_ic_summary_to_dict,
    get_factor_definition,
)
from core.governance import (
    build_data_quality_report,
    build_factor_quality_report,
    build_regime_quality_report,
    build_report_quality_report,
    build_research_health_report,
    research_health_report_to_dict,
)
from core.interpreter import DecisionInterpreter
from core.market import (
    build_market_context,
    build_market_regime_v2,
    build_market_structure,
    market_context_to_dict,
    market_regime_v2_to_dict,
    market_structure_to_dict,
)
from core.replay import build_replay_report, replay_report_to_dict
from core.strategy import ContrarianModeEngine, StrategyRouter, contrarian_signal_to_dict, strategy_decision_to_dict
from core.taxonomy import classify_etf, taxonomy_profile_to_dict, taxonomy_type_matches_valuation_model
from core.valuation import infer_valuation_model_type, sleeve_for_valuation_model

from .config import DB_PATH, DEFAULT_HOST, DEFAULT_PORT, FOOTER_SCRIPT_URL, HEADER_SCRIPT_URL, ROOT, STATIC_ASSET_VERSION
from .db import (
    connect,
    get_known_leader,
    get_latest_leader,
    latest_report,
    list_daily_prices,
    list_latest_leaders,
    list_queue,
    list_queue_for_etf,
    list_research_runs,
    list_trackable_history,
    queue_source_label,
    rows_to_dicts,
    valuation_runs,
)
from .config import LEADER_INDEX_URL
from .leader_index import enqueue_requested_etf

ETF_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
BULL_MARKET_START_DATE = "2024-09-24"
SYSTEM_NAME = "MyInvestETF"
SYSTEM_VERSION = "0.1.0"
SYSTEM_DESCRIPTION = "ETF 研究、类型化估值、研究队列和只读 Web 展示系统。"
HEALTH_CACHE_TTL_SECONDS = 120.0
_HEALTH_CACHE: dict[str, object] = {"created_at": 0.0, "payload": None}
ASK_PRESET_QUESTIONS = ["现在能不能参与？", "风险大不大？", "当前是什么状态？"]


def esc(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def load_json(value: str | None, fallback: object) -> object:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


MODEL_TYPE_LABELS = {
    "broad_index": "宽基估值",
    "mainline_theme": "主线估值",
    "factor_defensive": "收益防御估值",
    "cash_like": "现金替代监控",
}

REGIME_LABELS = {
    "risk_on": "风险偏积极",
    "risk_off": "风险收缩",
    "shock": "冲击/急跌",
    "rotation": "轮动/震荡",
}

TAXONOMY_LABELS = {
    "broad_index_core": "核心宽基",
    "broad_index_growth": "成长宽基",
    "broad_index_value": "价值宽基",
    "sector_cyclical": "周期行业",
    "sector_structural": "结构行业",
    "theme_lifecycle": "主题生命周期",
    "factor_strategy": "策略因子",
    "cash_equivalent": "现金替代",
    "bond_etf": "债券ETF",
    "commodity_etf": "商品ETF",
}

LIFECYCLE_LABELS = {
    "early": "早期",
    "expansion": "扩张",
    "crowded": "拥挤",
    "distribution": "派发",
    "collapse": "退潮",
}

SLEEVE_LABELS = {
    "core_wide_etf": "核心宽基仓",
    "mainline_etf": "主线进攻仓",
    "defensive_quality": "收益防御仓",
    "cash_like": "现金替代仓",
}

DEFENSIVE_FACTOR_BAND_BY_REGIME = {
    "risk_on": ("2%-5%", "靠近下沿"),
    "rotation": ("5%-8%", "中位配置"),
    "risk_off": ("8%-12%", "靠近上沿"),
    "shock": ("8%-12%", "靠近上沿"),
}


def defensive_factor_guidance(regime: object) -> dict[str, str]:
    regime_key = str(regime or "")
    band, stance = DEFENSIVE_FACTOR_BAND_BY_REGIME.get(regime_key, ("2%-12%", "等待market状态确认"))
    return {
        "band": band,
        "stance": stance,
        "regime": regime_key or "unknown",
        "mapping": "risk_on 靠近下沿 2%-5%；rotation 中位 5%-8%；risk_off/shock 靠近上沿 8%-12%",
        "explanation": (
            f"当前 market 状态为 {REGIME_LABELS.get(regime_key, regime_key or '待入库')}，"
            f"防御因子仓参考 {band}（{stance}）。这是组合层防御因子仓区间，不是单只 ETF 比例。"
        ),
    }


def leader_model_info(row: object | None) -> dict[str, object]:
    if row is None:
        return {"valuation_model_type": "mainline_theme", "sleeve_key": "mainline_etf"}
    raw = load_json(_row_value(row, "raw_json"), {})
    raw_map = raw if isinstance(raw, dict) else {}
    model_type = str(raw_map.get("valuation_model_type") or "")
    if not model_type:
        model_type = infer_valuation_model_type(
            {
                "code": _row_value(row, "code"),
                "name": _row_value(row, "name"),
                "theme": _row_value(row, "theme"),
                "category_key": raw_map.get("category_key"),
            }
        )
    sleeve_key = str(raw_map.get("sleeve_key") or sleeve_for_valuation_model(model_type))
    return {
        "valuation_model_type": model_type,
        "valuation_model_label": MODEL_TYPE_LABELS.get(model_type, model_type),
        "sleeve_key": sleeve_key,
        "sleeve_label": SLEEVE_LABELS.get(sleeve_key, sleeve_key),
    }


def leader_category_key(row: object) -> str:
    raw = load_json(_row_value(row, "raw_json"), {})
    raw_map = raw if isinstance(raw, dict) else {}
    return str(raw_map.get("category_key") or _row_value(row, "theme") or "")


def _raw_map(row: object | None) -> dict[str, object]:
    if row is None:
        return {}
    raw = load_json(_row_value(row, "raw_json"), {})
    return raw if isinstance(raw, dict) else {}


def taxonomy_profile_from_sources(
    *,
    code: str,
    leader: object | None = None,
    latest: object | None = None,
    fallback_name: str | None = None,
) -> dict[str, object]:
    latest_raw = _raw_map(latest)
    leader_raw = _raw_map(leader)
    model_info = leader_model_info(leader)
    model_type = latest_raw.get("valuation_model_type") or model_info.get("valuation_model_type")
    sleeve_key = latest_raw.get("sleeve_key") or model_info.get("sleeve_key")
    raw_profile = latest_raw.get("taxonomy_profile")
    if isinstance(raw_profile, dict) and taxonomy_type_matches_valuation_model(
        raw_profile.get("etf_type"),
        model_type,
    ):
        return raw_profile

    source: dict[str, object] = {
        **leader_raw,
        **latest_raw,
        "code": code,
        "etf_code": code,
        "name": _row_value(leader, "name") or _row_value(latest, "name") or fallback_name or code,
        "theme": _row_value(leader, "theme") or leader_raw.get("theme") or latest_raw.get("theme"),
        "category_key": leader_raw.get("category_key") or _row_value(leader, "theme"),
        "market": load_json(_row_value(leader, "market_json"), {}) if leader is not None else {},
        "scores": load_json(_row_value(leader, "scores_json"), {}) if leader is not None else {},
        "risk_flags": load_json(_row_value(leader, "risk_flags_json"), []) if leader is not None else [],
        "valuation_model_type": model_type,
        "sleeve_key": sleeve_key,
    }
    product_profile = latest_raw.get("product_profile")
    if isinstance(product_profile, dict):
        source["product_profile"] = product_profile
    return taxonomy_profile_to_dict(classify_etf(source))


def leader_to_summary(row: object) -> dict[str, object]:
    market = load_json(row["market_json"], {})
    scores = load_json(row["scores_json"], {})
    upstream_signal = upstream_signal_summary(row)
    model_info = leader_model_info(row)
    return {
        "code": row["code"],
        "name": row["name"],
        **model_info,
        "theme": row["theme"],
        "category_key": leader_category_key(row),
        "taxonomy_profile": taxonomy_profile_from_sources(code=str(row["code"]), leader=row),
        "themes": load_json(row["themes_json"], []),
        "deep_rating": row["deep_rating"],
        "deep_label": row["deep_label"],
        "deep_score": row["deep_score"],
        "shadow_observation_eligible": bool(row["shadow_observation_eligible"]),
        "candidate": {
            "leader_tier": row["candidate_leader_tier"],
            "leader_claim": row["candidate_leader_claim"],
            "evidence_score": row["candidate_evidence_score"],
            "evidence_count": row["candidate_evidence_count"],
            "hard_evidence_count": row["candidate_hard_evidence_count"],
        },
        "market": market,
        "scores": scores,
        "theme_signal": upstream_signal,
        "upstream_signal": upstream_signal,
        "risk_flags": load_json(row["risk_flags_json"], []),
        "data_gaps": load_json(row["data_gaps_json"], []),
        "links": {
            "page": f"/etfs/{row['code']}",
            "research_gateway": f"/research?etf={row['code']}",
            "api": f"/api/etfs/{row['code']}",
            "xueqiu": row["xueqiu_url"],
        },
    }


def research_run_to_summary(row: object) -> dict[str, object]:
    valuation_signal = valuation_signal_summary(row)
    raw = load_json(row["raw_json"], {})
    raw_map = raw if isinstance(raw, dict) else {}
    return {
        "id": row["id"],
        "task_type": row["task_type"],
        "research_date": row["research_date"],
        "status": row["status"],
        "valuation_model_type": raw_map.get("valuation_model_type"),
        "sleeve_key": raw_map.get("sleeve_key"),
        "title": row["title"],
        "summary": row["summary"],
        "valuation": {
            "low": row["valuation_low"],
            "mid": row["valuation_mid"],
            "high": row["valuation_high"],
            "unit": row["valuation_unit"],
            "method": row["valuation_method"],
            "confidence": row["valuation_confidence"],
        },
        "industry_position": row["industry_position"],
        "competition_landscape": row["competition_landscape"],
        "upstream_downstream": row["upstream_downstream"],
        "annual_growth": row["annual_growth"],
        "multi_bagger_potential": row["multi_bagger_potential"],
        "heavy_position_view": row["heavy_position_view"],
        "valuation_signal": valuation_signal,
        "taxonomy_profile": raw_map.get("taxonomy_profile"),
        "market_context": raw_map.get("market_context"),
        "evidence": load_json(row["evidence_json"], []),
        "assumptions": load_json(row["assumptions_json"], []),
        "risks": load_json(row["risks_json"], []),
    }


def latest_research_run(runs: list[object]) -> dict[str, object] | None:
    for row in runs:
        if row["task_type"] == "research":
            return research_run_to_summary(row)
    if runs:
        return research_run_to_summary(runs[0])
    return None


def valuation_history_payload(runs: list[object]) -> list[dict[str, object]]:
    history = []
    for row in runs:
        history.append(
            {
                "research_date": row["research_date"],
                "low": row["valuation_low"],
                "mid": row["valuation_mid"],
                "high": row["valuation_high"],
                "unit": row["valuation_unit"],
                "method": row["valuation_method"],
                "confidence": row["valuation_confidence"],
                "heavy_position_view": row["heavy_position_view"],
            }
        )
    return history


def render_layout(title: str, body: str) -> bytes:
    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} | MyInvestETF</title>
  <link rel="stylesheet" href="/static/styles.css?v={STATIC_ASSET_VERSION}">
</head>
<body>
  <div data-myinvest-header></div>
  <main>
{body}
  </main>
  <div data-myinvest-footer></div>
  <script src="{HEADER_SCRIPT_URL}" data-target="[data-myinvest-header]" defer></script>
  <script src="{FOOTER_SCRIPT_URL}" data-target="[data-myinvest-footer]" defer></script>
</body>
</html>
"""
    return html_text.encode("utf-8")


def fmt_num(value: object, digits: int = 2) -> str:
    if value is None:
        return "待入库"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return esc(value)


def fmt_calc_num(value: object, digits: int = 4) -> str:
    if value is None:
        return "待入库"
    try:
        decimal_value = Decimal(str(value))
        quant = Decimal("1").scaleb(-digits)
        return format(decimal_value.quantize(quant, rounding=ROUND_HALF_UP), f".{digits}f")
    except (InvalidOperation, TypeError, ValueError):
        return esc(value)


def fmt_percentile(value: object, digits: int = 2) -> str:
    if value is None:
        return "待入库"
    try:
        return f"{float(value):.{digits}f}%"
    except (TypeError, ValueError):
        return esc(value)


def fmt_ratio_percent(value: object, digits: int = 2, *, signed: bool = False) -> str:
    if value is None:
        return "待入库"
    try:
        number = float(value) * 100.0
    except (TypeError, ValueError):
        return esc(value)
    sign = "+" if signed else ""
    return f"{number:{sign}.{digits}f}%"


def _num(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _signal_bucket(score: object, *, strong: float, weak: float) -> str:
    number = _num(score)
    if number is None:
        return "unknown"
    if number >= strong:
        return "strong"
    if number >= weak:
        return "watch"
    return "weak"


def _bucket_label(bucket: str, *, kind: str) -> str:
    if kind in {"upstream", "theme"}:
        return {
            "strong": "主题主线信号强",
            "watch": "主题主线可跟踪",
            "weak": "主题主线偏弱",
            "not_applicable": "不依赖行业主线",
            "unknown": "等待主题主线信号",
        }.get(bucket, "等待主题主线信号")
    if kind == "market":
        return {
            "strong": "市场仓位信号偏积极",
            "watch": "市场仓位信号中性",
            "weak": "市场仓位信号偏谨慎",
            "unknown": "等待市场仓位信号",
        }.get(bucket, "等待市场仓位信号")
    if kind == "product":
        return {
            "high": "产品估值或回撤机会较好",
            "medium": "产品信号中性",
            "low": "产品估值或风险压力较高",
            "unknown": "等待产品估值",
        }.get(bucket, "等待产品估值")
    return {
        "high": "ETF估值与底仓适配较好",
        "medium": "ETF估值与底仓适配中性",
        "low": "ETF估值或拥挤压力较高",
        "unknown": "等待ETF估值",
    }.get(bucket, "等待ETF估值")


def upstream_signal_summary(row: object | None) -> dict[str, object]:
    if row is None:
        return {
            "source": "theme.okbbc.com/api/latest",
            "theme": None,
            "bucket": "unknown",
            "label": _bucket_label("unknown", kind="upstream"),
            "applies": True,
            "explanation": "未找到主题主线接口入库信号。",
        }
    model_type = str(leader_model_info(row).get("valuation_model_type") or "")
    scores_value = load_json(row["scores_json"], {})
    market_value = load_json(row["market_json"], {})
    risk_flags_value = load_json(row["risk_flags_json"], [])
    themes_value = load_json(row["themes_json"], [])
    scores = scores_value if isinstance(scores_value, dict) else {}
    market = market_value if isinstance(market_value, dict) else {}
    risk_flags = risk_flags_value if isinstance(risk_flags_value, list) else []
    themes = themes_value if isinstance(themes_value, list) else []
    theme_binding = _num(scores.get("theme_binding"))
    leader_score = _num(row["deep_score"])
    evidence_quality = _num(scores.get("evidence_quality") or row["candidate_evidence_score"])
    trading_structure = _num(scores.get("trading_structure"))
    if model_type != "mainline_theme":
        return {
            "source": "theme.okbbc.com/api/latest",
            "theme": row["theme"],
            "themes": themes,
            "bucket": "not_applicable",
            "label": _bucket_label("not_applicable", kind="theme"),
            "applies": False,
            "theme_binding": theme_binding,
            "leader_score": leader_score,
            "evidence_quality": evidence_quality,
            "trading_structure": trading_structure,
            "rating": f"{row['deep_rating'] or ''} {row['deep_label'] or ''}".strip(),
            "leader_claim": row["candidate_leader_claim"],
            "market": {
                "r5": market.get("r5"),
                "r20": market.get("r20"),
                "r60": market.get("r60"),
                "turnover_rate": market.get("turnover_rate"),
            },
            "risk_flags": risk_flags,
            "explanation": "该ETF不是行业主线/主题ETF，不使用theme研究作为确认条件。",
        }

    anchor_score = theme_binding if theme_binding is not None else leader_score
    bucket = _signal_bucket(anchor_score, strong=80.0, weak=60.0)
    if bucket == "strong" and leader_score is not None and leader_score < 65.0:
        bucket = "watch"
    label = _bucket_label(bucket, kind="upstream")
    parts = [
        f"主题绑定 {fmt_num(theme_binding)}",
        f"主线强度 {fmt_num(leader_score)}",
        f"证据质量 {fmt_num(evidence_quality)}",
        f"交易结构 {fmt_num(trading_structure)}",
    ]
    return {
        "source": "theme.okbbc.com/api/latest",
        "theme": row["theme"],
        "themes": themes,
        "bucket": bucket,
        "label": label,
        "applies": True,
        "theme_binding": theme_binding,
        "leader_score": leader_score,
        "evidence_quality": evidence_quality,
        "trading_structure": trading_structure,
        "rating": f"{row['deep_rating'] or ''} {row['deep_label'] or ''}".strip(),
        "leader_claim": row["candidate_leader_claim"],
        "market": {
            "r5": market.get("r5"),
            "r20": market.get("r20"),
            "r60": market.get("r60"),
            "turnover_rate": market.get("turnover_rate"),
        },
        "risk_flags": risk_flags,
        "explanation": "；".join(parts),
    }


def market_signal_summary(
    market_regime: dict[str, object] | None = None,
    market_context: dict[str, object] | None = None,
) -> dict[str, object]:
    regime_map = market_regime if isinstance(market_regime, dict) else {}
    context_map = market_context if isinstance(market_context, dict) else {}
    context_regime = context_map.get("regime") if isinstance(context_map.get("regime"), dict) else {}
    evidence = regime_map.get("evidence") if isinstance(regime_map.get("evidence"), dict) else {}
    structure = regime_map.get("structure") if isinstance(regime_map.get("structure"), dict) else {}
    regime = str(regime_map.get("regime") or context_regime.get("regime") or "")
    confidence = _num(regime_map.get("confidence") or context_regime.get("confidence"))
    if regime == "risk_on":
        bucket = "strong"
        suggested_position = "权益仓位可偏积极"
    elif regime == "rotation":
        bucket = "watch"
        suggested_position = "维持中性，等待结构确认"
    elif regime in {"risk_off", "shock"}:
        bucket = "weak"
        suggested_position = "控制权益仓位，降低进攻暴露"
    else:
        bucket = "unknown"
        suggested_position = "等待market研究建议仓位"
    label = _bucket_label(bucket, kind="market")
    return {
        "source": "market研究/本地市场状态层",
        "bucket": bucket,
        "label": label,
        "regime": regime or None,
        "confidence": confidence,
        "suggested_position": suggested_position,
        "breadth_score": structure.get("breadth_score") or evidence.get("breadth_score"),
        "liquidity_score": structure.get("liquidity_score") or evidence.get("liquidity_score"),
        "price_trend_score": structure.get("price_trend_score") or evidence.get("price_trend_score"),
        "defensive_factor_guidance": defensive_factor_guidance(regime),
        "explanation": (
            f"市场状态 {REGIME_LABELS.get(regime, regime or '待入库')}，"
            f"建议仓位口径：{suggested_position}。"
        ),
    }


def product_signal_summary(valuation_signal: dict[str, object]) -> dict[str, object]:
    bucket = str(valuation_signal.get("bucket") or "unknown")
    return {
        "source": valuation_signal.get("source") or "MyInvestETF deterministic valuation",
        "bucket": bucket,
        "label": _bucket_label(bucket, kind="product"),
        "valuation_model_type": valuation_signal.get("valuation_model_type"),
        "valuation_model_label": valuation_signal.get("valuation_model_label"),
        "sleeve_key": valuation_signal.get("sleeve_key"),
        "sleeve_label": valuation_signal.get("sleeve_label"),
        "undervalued_score": valuation_signal.get("undervalued_score"),
        "liquidity_score": valuation_signal.get("liquidity_score"),
        "tracking_score": valuation_signal.get("tracking_score"),
        "risk_adjusted_score": valuation_signal.get("risk_adjusted_score"),
        "drawdown_opportunity_score": valuation_signal.get("drawdown_opportunity_score"),
        "explanation": valuation_signal.get("explanation") or "等待ETF完整深研入库。",
    }


def valuation_signal_explanation(
    *,
    model_type: str,
    undervalued_score: object,
    liquidity_score: object,
    tracking_score: object,
    portfolio_role_score: object,
    risk_adjusted_score: object,
    mainline_validity_score: object,
    valuation_tolerance_score: object,
    crowding_risk_score: object,
    factor_premium_score: object,
    cash_like_safety_score: object,
) -> str:
    if model_type == "mainline_theme":
        lead = (
            f"主线有效性 {fmt_num(mainline_validity_score)}；估值容错 {fmt_num(valuation_tolerance_score)}；"
            f"拥挤风险 {fmt_num(crowding_risk_score)}"
        )
    elif model_type == "factor_defensive":
        lead = f"防御因子溢价 {fmt_num(factor_premium_score)}"
    elif model_type == "cash_like":
        lead = f"现金替代安全 {fmt_num(cash_like_safety_score)}"
    else:
        lead = f"宽基估值安全 {fmt_num(undervalued_score)}"
    return (
        f"{lead}；流动性 {fmt_num(liquidity_score)}；跟踪质量 {fmt_num(tracking_score)}；"
        f"仓位角色 {fmt_num(portfolio_role_score)}；风险调整 {fmt_num(risk_adjusted_score)}"
    )


def valuation_signal_summary(row: object | None) -> dict[str, object]:
    if row is None:
        return {
            "source": "MyInvestETF deterministic valuation",
            "valuation_model_type": None,
            "valuation_model_label": "待估值",
            "sleeve_key": None,
            "sleeve_label": "待分类",
            "bucket": "unknown",
            "label": _bucket_label("unknown", kind="valuation"),
            "explanation": "等待ETF完整深研入库。",
        }
    raw = load_json(_row_value(row, "raw_json"), {})
    valuation = raw.get("valuation") if isinstance(raw, dict) else {}
    conclusion = raw.get("conclusion") if isinstance(raw, dict) else {}
    model_type = str(raw.get("valuation_model_type") or "") if isinstance(raw, dict) else ""
    sleeve_key = str(raw.get("sleeve_key") or "") if isinstance(raw, dict) else ""
    undervalued_score = _num(valuation.get("undervalued_score")) if isinstance(valuation, dict) else None
    risk_adjusted_score = _num(valuation.get("risk_adjusted_score")) if isinstance(valuation, dict) else None
    liquidity_score = _num(valuation.get("liquidity_score")) if isinstance(valuation, dict) else None
    tracking_score = _num(valuation.get("tracking_score")) if isinstance(valuation, dict) else None
    portfolio_role_score = _num(valuation.get("portfolio_role_score")) if isinstance(valuation, dict) else None
    current_price = _num(valuation.get("current_price")) if isinstance(valuation, dict) else None
    nav = _num(valuation.get("nav")) if isinstance(valuation, dict) else None
    premium_discount = _num(valuation.get("premium_discount")) if isinstance(valuation, dict) else None
    valuation_percentile = _num(valuation.get("valuation_percentile")) if isinstance(valuation, dict) else None
    mainline_validity_score = _num(valuation.get("mainline_validity_score")) if isinstance(valuation, dict) else None
    valuation_tolerance_score = _num(valuation.get("valuation_tolerance_score")) if isinstance(valuation, dict) else None
    crowding_risk_score = _num(valuation.get("crowding_risk_score")) if isinstance(valuation, dict) else None
    factor_premium_score = _num(valuation.get("factor_premium_score")) if isinstance(valuation, dict) else None
    cash_like_safety_score = _num(valuation.get("cash_like_safety_score")) if isinstance(valuation, dict) else None
    if undervalued_score is None:
        bucket = "unknown"
    elif undervalued_score >= 70.0:
        bucket = "high"
    elif undervalued_score >= 40.0:
        bucket = "medium"
    else:
        bucket = "low"
    label = _bucket_label(bucket, kind="valuation")
    return {
        "source": "MyInvestETF deterministic valuation",
        "valuation_model_type": model_type or None,
        "valuation_model_label": MODEL_TYPE_LABELS.get(model_type, model_type or "待估值"),
        "sleeve_key": sleeve_key or None,
        "sleeve_label": SLEEVE_LABELS.get(sleeve_key, sleeve_key or "待分类"),
        "bucket": bucket,
        "label": label,
        "undervalued_score": undervalued_score,
        "liquidity_score": liquidity_score,
        "tracking_score": tracking_score,
        "portfolio_role_score": portfolio_role_score,
        "current_price": current_price,
        "nav": nav,
        "premium_discount": premium_discount,
        "valuation_percentile": valuation_percentile,
        "risk_adjusted_score": risk_adjusted_score,
        "mainline_validity_score": mainline_validity_score,
        "valuation_tolerance_score": valuation_tolerance_score,
        "crowding_risk_score": crowding_risk_score,
        "factor_premium_score": factor_premium_score,
        "cash_like_safety_score": cash_like_safety_score,
        "valuation_range": {
            "low": _row_value(row, "valuation_low"),
            "mid": _row_value(row, "valuation_mid"),
            "high": _row_value(row, "valuation_high"),
            "unit": _row_value(row, "valuation_unit"),
            "method": _row_value(row, "valuation_method"),
        },
        "raw_grade": _row_value(row, "heavy_position_view"),
        "raw_summary": conclusion.get("summary") if isinstance(conclusion, dict) else None,
        "explanation": valuation_signal_explanation(
            model_type=model_type,
            undervalued_score=undervalued_score,
            liquidity_score=liquidity_score,
            tracking_score=tracking_score,
            portfolio_role_score=portfolio_role_score,
            risk_adjusted_score=risk_adjusted_score,
            mainline_validity_score=mainline_validity_score,
            valuation_tolerance_score=valuation_tolerance_score,
            crowding_risk_score=crowding_risk_score,
            factor_premium_score=factor_premium_score,
            cash_like_safety_score=cash_like_safety_score,
        ),
    }


def _bounded_score(value: float) -> float:
    return max(0.0, min(100.0, value))


def _valuation_bucketed(signal: dict[str, object]) -> dict[str, object]:
    enriched = dict(signal)
    undervalued_score = _num(enriched.get("undervalued_score"))
    if undervalued_score is None:
        bucket = "unknown"
    elif undervalued_score >= 70.0:
        bucket = "high"
    elif undervalued_score >= 40.0:
        bucket = "medium"
    else:
        bucket = "low"
    enriched["bucket"] = bucket
    enriched["label"] = _bucket_label(bucket, kind="valuation")
    return enriched


def valuation_signal_with_drawdown_context(
    valuation_signal: dict[str, object],
    taxonomy_profile: dict[str, object] | None,
    market_regime_v2: dict[str, object] | None,
) -> dict[str, object]:
    model_type = str(valuation_signal.get("valuation_model_type") or "")
    taxonomy_type = str((taxonomy_profile or {}).get("etf_type") or "")
    if model_type != "factor_defensive" and taxonomy_type != "factor_strategy":
        return valuation_signal
    evidence = market_regime_v2.get("evidence") if isinstance(market_regime_v2, dict) else {}
    if not isinstance(evidence, dict):
        return valuation_signal
    current_drawdown = _num(evidence.get("current_drawdown"))
    if current_drawdown is None or current_drawdown < 0.12:
        return valuation_signal
    valuation_percentile = _num(valuation_signal.get("valuation_percentile"))
    drawdown_score = _bounded_score(55.0 + max(0.0, current_drawdown - 0.10) / 0.15 * 40.0)
    if valuation_percentile is None:
        opportunity_score = drawdown_score
    else:
        percentile_score = _bounded_score(50.0 + max(0.0, 35.0 - valuation_percentile) / 35.0 * 45.0)
        opportunity_score = max(drawdown_score, percentile_score * 0.55 + drawdown_score * 0.45)
    enriched = dict(valuation_signal)
    current_undervalued = _num(enriched.get("undervalued_score")) or 0.0
    enriched["drawdown_opportunity_score"] = round(opportunity_score, 6)
    enriched["drawdown_opportunity_label"] = (
        f"当前回撤 {fmt_ratio_percent(current_drawdown)}，估值分位 {fmt_percentile(valuation_percentile)}，"
        "按收益防御ETF识别为深回撤估值机会"
    )
    if opportunity_score > current_undervalued:
        enriched["undervalued_score"] = round(opportunity_score, 6)
        enriched["explanation"] = (
            f"{enriched.get('explanation') or ''}；深回撤机会 {fmt_num(opportunity_score)} "
            f"来自当前回撤 {fmt_ratio_percent(current_drawdown)} 和估值分位 {fmt_percentile(valuation_percentile)}"
        ).strip("；")
    return _valuation_bucketed(enriched)


def decision_matrix_summary(
    upstream_signal: dict[str, object],
    valuation_signal: dict[str, object],
    *,
    market_signal: dict[str, object] | None = None,
    taxonomy_profile: dict[str, object] | None = None,
    product_signal: dict[str, object] | None = None,
) -> dict[str, object]:
    market_signal = market_signal or market_signal_summary()
    product_signal = product_signal or product_signal_summary(valuation_signal)
    theme_signal = upstream_signal
    model_type = str(valuation_signal.get("valuation_model_type") or "")
    taxonomy_type = str((taxonomy_profile or {}).get("etf_type") or "")
    if not model_type:
        if taxonomy_type in {"broad_index_core", "broad_index_growth", "broad_index_value"}:
            model_type = "broad_index"
        elif taxonomy_type == "factor_strategy":
            model_type = "factor_defensive"
        elif taxonomy_type == "cash_equivalent":
            model_type = "cash_like"
        elif taxonomy_type in {"theme_lifecycle", "sector_cyclical", "sector_structural"}:
            model_type = "mainline_theme"

    market_bucket = str(market_signal.get("bucket") or "unknown")
    theme_bucket = str(theme_signal.get("bucket") or "unknown")
    valuation_bucket = str(valuation_signal.get("bucket") or "unknown")
    product_bucket = str(product_signal.get("bucket") or valuation_bucket)
    theme_applicable = model_type == "mainline_theme"

    if product_bucket == "unknown":
        conclusion = "等待ETF估值、流动性和跟踪质量验证。"
        posture = "待完整深研"
    elif model_type == "mainline_theme":
        if theme_bucket == "unknown":
            conclusion = "等待theme研究确认行业主线；market仓位信号和ETF产品信号只能先作为观察依据。"
            posture = "等待主线确认"
        elif market_bucket == "weak" and product_bucket in {"high", "medium"}:
            conclusion = "theme主线和ETF产品信号可跟踪，但market仓位信号偏谨慎，主线进攻仓位应先按观察处理。"
            posture = "主线观察"
        elif theme_bucket == "strong" and product_bucket == "high":
            conclusion = "market未限制，theme行业主线与ETF产品信号匹配，可作为主线进攻候选研究。"
            posture = "主线进攻候选"
        elif theme_bucket == "strong" and product_bucket in {"medium", "low"}:
            conclusion = "theme行业主线较强，但ETF估值、拥挤或流动性仍需观察，更适合作为工具仓跟踪。"
            posture = "工具仓跟踪"
        elif theme_bucket in {"watch", "weak"} and product_bucket == "high":
            conclusion = "ETF产品估值与流动性较好，但theme主线强度未充分确认，适合作为观察型配置工具。"
            posture = "观察型工具"
        else:
            conclusion = "theme主线、产品估值或拥挤状态没有形成共振，优先等待证据更清晰。"
            posture = "观察"
    elif model_type == "broad_index":
        if product_bucket == "high" and market_bucket in {"strong", "watch", "unknown"}:
            conclusion = "宽基ETF不依赖行业主线；market仓位信号与宽基估值/流动性支持作为核心宽基候选研究。"
            posture = "核心宽基候选"
        elif product_bucket == "high":
            conclusion = "宽基ETF不依赖行业主线；估值和流动性较好，但market仓位信号偏谨慎，适合作为仓位观察对象。"
            posture = "仓位观察"
        elif product_bucket == "medium":
            conclusion = "宽基ETF不依赖行业主线；当前产品估值与流动性中性，应主要跟随market建议仓位调整。"
            posture = "核心宽基观察"
        else:
            conclusion = "宽基ETF不依赖行业主线；当前估值、回撤或风险调整不足，等待更好的市场仓位或估值条件。"
            posture = "暂缓底仓"
    elif model_type == "factor_defensive":
        if product_bucket == "high":
            conclusion = "策略型收益防御ETF不依赖行业主线；估值、流动性、回撤机会或防御因子信号较好，可作为收益防御候选研究。"
            posture = "收益防御候选"
        elif product_bucket == "medium":
            conclusion = "策略型收益防御ETF不依赖行业主线；产品信号中性，结合market仓位建议作为收益防御观察工具。"
            posture = "收益防御观察"
        else:
            conclusion = "策略型收益防御ETF不依赖行业主线；当前策略因子、估值或风险调整不足，等待回撤或因子信号改善。"
            posture = "等待策略信号"
    elif model_type == "cash_like":
        if product_bucket in {"high", "medium"}:
            conclusion = "现金替代ETF不依赖行业主线或主线深研，重点看安全性、流动性、期限和信用风险。"
            posture = "现金替代监控"
        else:
            conclusion = "现金替代ETF不依赖行业主线；当前安全性或流动性信号不足，暂不作为现金替代优选。"
            posture = "现金替代暂缓"
    elif market_bucket == "unknown":
        conclusion = "等待market仓位信号和ETF产品结构验证。"
        posture = "待确认"
    else:
        conclusion = "按market仓位信号和ETF产品估值适配做类型化解释，等待更多研究证据。"
        posture = "观察"
    return {
        "market_bucket": market_bucket,
        "theme_bucket": theme_bucket,
        "product_bucket": product_bucket,
        "upstream_bucket": theme_bucket,
        "valuation_bucket": valuation_bucket,
        "market_label": market_signal.get("label"),
        "theme_label": theme_signal.get("label"),
        "product_label": product_signal.get("label"),
        "upstream_label": theme_signal.get("label"),
        "valuation_label": valuation_signal.get("label"),
        "theme_applicable": theme_applicable,
        "valuation_model_type": model_type or None,
        "posture": posture,
        "conclusion": conclusion,
        "rule": "market position signal + type-specific ETF product signal; theme signal only applies to mainline/industry ETF",
        "market_signal": market_signal,
        "theme_signal": theme_signal,
        "product_signal": product_signal,
    }


def portfolio_use_view(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "待入库"
    display = {
        "工具仓可用": "阶段性工具仓可用，不等于当前买入",
        "底仓候选": "底仓候选，仍需结合估值和市场状态",
        "估值或拥挤暂缓": "估值或拥挤暂缓，等待风险释放",
        "观察": "观察，等待证据更清晰",
        "不适合底仓": "不适合底仓",
    }
    return display.get(text, text)


def score_state(value: object, *, kind: str = "default") -> str:
    number = _num(value)
    if number is None:
        return "待入库"
    if kind == "valuation_safety":
        if number >= 85:
            return "估值安全边际较高"
        if number >= 70:
            return "估值相对可接受"
        if number >= 50:
            return "估值中性，需结合增长验证"
        return "估值压力较高"
    if kind == "evidence_quality":
        if number >= 85:
            return "证据强，龙头判断较扎实"
        if number >= 70:
            return "证据较充分"
        if number >= 60:
            return "证据可观察，仍需深研确认"
        return "证据偏弱"
    if kind == "deep_score":
        if number >= 80:
            return "高优先级深研对象"
        if number >= 70:
            return "可跟踪深研对象"
        if number >= 60:
            return "观察型候选"
        return "低优先级候选"
    if number >= 85:
        return "强"
    if number >= 70:
        return "较好"
    if number >= 60:
        return "中性"
    return "偏弱"


def score_signal(value: object, *, kind: str = "default") -> tuple[str, str]:
    number = _num(value)
    if number is None:
        return "unknown", "待入库"
    if kind == "valuation_safety":
        if number >= 85:
            return "safe", "低估/安全"
        if number >= 70:
            return "ok", "估值可接受"
        if number >= 50:
            return "watch", "估值中性"
        return "danger", "估值危险"
    if kind == "evidence_quality":
        if number >= 85:
            return "safe", "证据可信"
        if number >= 70:
            return "ok", "证据较足"
        if number >= 60:
            return "watch", "需确认"
        return "danger", "证据偏弱"
    if kind == "deep_score":
        if number >= 80:
            return "safe", "高优先级"
        if number >= 70:
            return "ok", "可跟踪"
        if number >= 60:
            return "watch", "观察"
        return "danger", "低优先"
    if number >= 85:
        return "safe", "强"
    if number >= 70:
        return "ok", "较好"
    if number >= 60:
        return "watch", "中性"
    return "danger", "偏弱"


def ratio_state(label: str, value: object) -> str:
    number = _num(value)
    if number is None:
        return "待入库"
    if label == "PE TTM":
        if number <= 0:
            return "亏损或口径不适用"
        if number < 15:
            return "低市盈率区间"
        if number < 30:
            return "中等市盈率区间"
        if number < 60:
            return "较高市盈率，需增长兑现"
        return "高市盈率，需强增长支撑"
    if label == "PB":
        if number < 1:
            return "低于净资产定价"
        if number < 3:
            return "常见市净率区间"
        if number < 6:
            return "较高市净率，需高 ROE 支撑"
        return "高市净率，需强盈利质量支撑"
    return "行情快照"


def ratio_signal(label: str, value: object) -> tuple[str, str]:
    number = _num(value)
    if number is None:
        return "unknown", "待入库"
    if label == "PE TTM":
        if number <= 0:
            return "danger", "亏损/异常"
        if number < 15:
            return "safe", "低估"
        if number < 30:
            return "ok", "合理"
        if number < 60:
            return "watch", "偏贵"
        return "danger", "危险"
    if label == "PB":
        if number < 1:
            return "safe", "资产折价"
        if number < 3:
            return "ok", "合理"
        if number < 6:
            return "watch", "偏贵"
        return "danger", "危险"
    return "neutral", "行情快照"


def metric_explanation(label: str, value: object) -> tuple[str, str]:
    if label in {"深研", "深研分"}:
        return (
            "综合入口评分",
            f"{score_state(value, kind='deep_score')}。衡量这只ETF是否值得进入深研队列，不等于最终底仓结论。",
        )
    if label == "当前价格":
        return (
            "价格快照",
            "优先使用本地日行情最新收盘价；如果行情未缓存，则使用最新完整深研中的 current_price 或入口收盘价。",
        )
    if label == "估值分位":
        return (
            "估值百分位",
            "底层指数或估值输入的历史分位，数值越高通常表示越接近历史偏贵区间。",
        )
    if label == "收盘":
        return (
            "行情快照",
            "基准数据中的收盘价，单位通常为元/股；它是价格参照，不代表合理估值。",
        )
    if label in {"PE TTM", "PB"}:
        return (
            "估值倍数",
            f"{ratio_state(label, value)}。这是入口快照口径，仍需结合行业、增长、ROE 和现金流判断。",
        )
    if label == "证据质量":
        return (
            "龙头证据强度",
            f"{score_state(value, kind='evidence_quality')}。分数越高，说明支持龙头地位的硬证据越充分。",
        )
    if label == "估值安全":
        return (
            "入口估值安全度",
            f"{score_state(value, kind='valuation_safety')}。分数越高，表示入口筛选看估值越不紧张；最终参考区间以ETF完整深研为准。",
        )
    if label == "组合使用判断":
        return (
            "组合使用判断",
            "说明这只ETF更适合作为长期底仓、阶段性工具、继续观察，还是暂缓；它不是买卖指令。",
        )
    return ("指标说明", "入口展示指标，用于辅助筛选和跟踪。")


def metric_signal(label: str, value: object) -> tuple[str, str]:
    if label in {"深研", "深研分"}:
        return score_signal(value, kind="deep_score")
    if label == "当前价格":
        return "neutral", "价格快照"
    if label == "估值分位":
        return "neutral", "百分位"
    if label == "收盘":
        return "neutral", "行情快照"
    if label in {"PE TTM", "PB"}:
        return ratio_signal(label, value)
    if label == "证据质量":
        return score_signal(value, kind="evidence_quality")
    if label == "估值安全":
        return score_signal(value, kind="valuation_safety")
    if label == "组合使用判断":
        return "neutral", "研究判断"
    return "neutral", "参考"


def metric(label: str, value: object, unit: str = "") -> str:
    shown = fmt_num(value) if isinstance(value, (int, float)) else esc(value or "待入库")
    tooltip_title, tooltip_body = metric_explanation(label, value)
    signal_class, signal_label = metric_signal(label, value)
    tooltip_text = f"{tooltip_title}：{tooltip_body}"
    return f"""<div class="metric metric-signal-{esc(signal_class)}" tabindex="0" title="{esc(tooltip_text)}" aria-label="{esc(label)}：{esc(shown)}{esc(unit)}。{esc(signal_label)}。{esc(tooltip_text)}">
      <span>{esc(label)}</span>
      <strong>{shown}{esc(unit)}</strong>
      <small class="metric-signal-label">{esc(signal_label)}</small>
      <div class="metric-tooltip" role="tooltip">
        <b>{esc(tooltip_title)}</b>
        <em>{esc(tooltip_body)}</em>
      </div>
    </div>"""


def compact_metric(label: str, value: object, unit: str = "") -> str:
    shown = fmt_num(value) if isinstance(value, (int, float)) else esc(value or "待入库")
    tooltip_title, tooltip_body = metric_explanation(label, value)
    signal_class, signal_label = metric_signal(label, value)
    tooltip_text = f"{tooltip_title}：{tooltip_body}"
    return f"""<div class="compact-metric compact-metric-{esc(signal_class)}" title="{esc(tooltip_text)}" aria-label="{esc(label)}：{esc(shown)}{esc(unit)}。{esc(signal_label)}">
      <span>{esc(label)}</span>
      <strong>{shown}{esc(unit)}</strong>
      <small>{esc(signal_label)}</small>
    </div>"""


def render_current_decision_summary(
    decision_matrix: dict[str, object],
    valuation_signal: dict[str, object],
    adaptive_decision_signal: dict[str, object],
    current_price: object,
) -> str:
    posture = decision_matrix.get("posture") or "待完整深研"
    conclusion = (
        decision_matrix.get("conclusion")
        or valuation_signal.get("explanation")
        or "等待ETF完整深研入库。"
    )
    valuation_label = valuation_signal.get("label") or "等待ETF估值"
    valuation_range = valuation_signal.get("valuation_range") if isinstance(valuation_signal.get("valuation_range"), dict) else {}
    range_text = " / ".join(
        [
            fmt_num(valuation_range.get("low")),
            fmt_num(valuation_range.get("mid")),
            fmt_num(valuation_range.get("high")),
        ]
    )
    state = adaptive_decision_signal.get("state") if isinstance(adaptive_decision_signal.get("state"), dict) else {}
    state_code = state.get("state_code") if isinstance(state, dict) else None
    score = adaptive_decision_signal.get("score")
    confidence = adaptive_decision_signal.get("confidence")
    state_label = state_code or "待入库"
    score_hint = f"Decision {fmt_num(score)} / 置信度 {fmt_ratio_percent(confidence)}"
    return f"""<section class="decision-hero" aria-label="当前研究结论">
        <div class="decision-hero-main">
          <span>当前研究结论</span>
          <strong>{esc(posture)}</strong>
          <p>{esc(conclusion)}</p>
        </div>
        <div class="decision-hero-grid">
          <div>
            <span>估值状态</span>
            <strong>{esc(valuation_label)}</strong>
          </div>
          <div>
            <span>当前价格</span>
            <strong>{fmt_num(current_price)}</strong>
          </div>
          <div>
            <span>参考低 / 中 / 高</span>
            <strong>{esc(range_text)}</strong>
          </div>
          <div>
            <span>状态机</span>
            <strong>{esc(state_label)}</strong>
            <small>{esc(score_hint)}</small>
          </div>
        </div>
      </section>"""


def _ask_answer_summary(answer: dict[str, object]) -> str:
    final_answer = answer.get("final_answer") if isinstance(answer.get("final_answer"), dict) else {}
    decision = answer.get("decision") if isinstance(answer.get("decision"), dict) else {}
    regime = answer.get("regime") if isinstance(answer.get("regime"), dict) else {}
    taxonomy = answer.get("taxonomy") if isinstance(answer.get("taxonomy"), dict) else {}
    risk = answer.get("risk") if isinstance(answer.get("risk"), dict) else {}
    reasoning = final_answer.get("reasoning") if isinstance(final_answer.get("reasoning"), list) else []
    risk_notes = final_answer.get("risk_notes") if isinstance(final_answer.get("risk_notes"), list) else []
    if not risk_notes and isinstance(risk.get("warnings"), list):
        risk_notes = risk.get("warnings")  # type: ignore[assignment]
    reasoning_items = "".join(f"<li>{esc(item)}</li>" for item in reasoning[:4])
    risk_items = "".join(f"<li>{esc(item)}</li>" for item in risk_notes[:4])
    if not reasoning_items:
        reasoning_items = "<li>等待解释入库。</li>"
    if not risk_items:
        risk_items = "<li>暂无阻断性风险提示。</li>"
    return f"""<div class="ask-answer-summary">
            <strong>{esc(final_answer.get("headline") or "等待结论")}</strong>
            <div class="ask-result-grid">
              <span>评分 {esc(decision.get("score") if decision.get("score") is not None else "待入库")}</span>
              <span>状态 {esc(regime.get("state") or "unknown")}</span>
              <span>类型 {esc(taxonomy.get("type") or "unknown")}</span>
              <span>置信度 {esc(final_answer.get("confidence") if final_answer.get("confidence") is not None else "待入库")}</span>
            </div>
            <div class="ask-answer-columns">
              <div>
                <span>依据</span>
                <ul>{reasoning_items}</ul>
              </div>
              <div>
                <span>风险</span>
                <ul>{risk_items}</ul>
              </div>
            </div>
          </div>"""


def render_ask_widget(code: str, common_answers: list[dict[str, object]] | None = None) -> str:
    api_path = f"/api/ask/{quote(code)}"
    default_question = "现在能不能参与？"
    answers = common_answers or []
    common_items = "".join(
        f"""<article class="ask-qa-item">
          <h3>{esc(answer.get("question") or "常用问题")}</h3>
          {_ask_answer_summary(answer)}
        </article>"""
        for answer in answers
    )
    if not common_items:
        common_items = """<article class="ask-qa-item">
          <h3>常用问题</h3>
          <div class="ask-answer-summary"><strong>等待研究数据入库。</strong></div>
        </article>"""
    return f"""<section class="ask-widget" data-ask-widget aria-label="问这个ETF">
        <div class="ask-heading-row">
          <div>
            <h2>问这个ETF</h2>
            <p class="muted">统一结论、风险提示、状态依据</p>
          </div>
          <a class="code-link ask-api-link" href="{esc(api_path)}?q={quote(default_question)}">API</a>
        </div>
        <form class="ask-form" action="{esc(api_path)}" method="get">
          <input type="search" name="q" value="{esc(default_question)}" aria-label="ETF问题" autocomplete="off">
          <button type="submit">提问</button>
        </form>
        <div class="ask-common">
          <div class="ask-common-heading">
            <h3>常用问题</h3>
          </div>
          <div class="ask-common-list">{common_items}</div>
        </div>
        <div class="ask-result ask-result-empty" data-ask-result>等待自定义提问。</div>
        <script>
        (() => {{
          const widgets = document.querySelectorAll("[data-ask-widget]");
          for (const widget of widgets) {{
            if (widget.dataset.bound === "1") continue;
            widget.dataset.bound = "1";
            const form = widget.querySelector(".ask-form");
            const input = widget.querySelector("input[name='q']");
            const result = widget.querySelector("[data-ask-result]");
            const endpoint = form.getAttribute("action");
            const textNode = (tag, className, text) => {{
              const node = document.createElement(tag);
              if (className) node.className = className;
              node.textContent = text == null || text === "" ? "待入库" : String(text);
              return node;
            }};
            const addList = (title, items) => {{
              const wrap = document.createElement("div");
              wrap.className = "ask-result-list";
              wrap.appendChild(textNode("span", "", title));
              const list = document.createElement("ul");
              for (const item of items.slice(0, 4)) {{
                const li = document.createElement("li");
                li.textContent = String(item);
                list.appendChild(li);
              }}
              wrap.appendChild(list);
              return wrap;
            }};
            const renderAnswer = (payload) => {{
              result.className = "ask-result";
              const finalAnswer = payload.final_answer || {{}};
              const conclusion = finalAnswer.conclusion || {{}};
              const decision = payload.decision || {{}};
              const regime = payload.regime || {{}};
              const taxonomy = payload.taxonomy || {{}};
              const head = document.createElement("div");
              head.className = "ask-result-head";
              const title = textNode("strong", "", finalAnswer.headline || "等待结论");
              const badge = textNode("span", "ask-result-badge", conclusion.type || "unknown");
              head.appendChild(title);
              head.appendChild(badge);
              const grid = document.createElement("div");
              grid.className = "ask-result-grid";
              grid.appendChild(textNode("span", "", `评分 ${{decision.score ?? "待入库"}}`));
              grid.appendChild(textNode("span", "", `状态 ${{regime.state || "unknown"}}`));
              grid.appendChild(textNode("span", "", `类型 ${{taxonomy.type || "unknown"}}`));
              grid.appendChild(textNode("span", "", `置信度 ${{finalAnswer.confidence ?? "待入库"}}`));
              result.replaceChildren(head, grid);
              if (Array.isArray(finalAnswer.reasoning) && finalAnswer.reasoning.length) {{
                result.appendChild(addList("依据", finalAnswer.reasoning));
              }}
              if (Array.isArray(finalAnswer.risk_notes) && finalAnswer.risk_notes.length) {{
                result.appendChild(addList("风险", finalAnswer.risk_notes));
              }}
            }};
            const ask = async (question) => {{
              const trimmed = String(question || "").trim();
              if (!trimmed) return;
              result.className = "ask-result ask-result-empty";
              result.textContent = "生成中...";
              try {{
                const response = await fetch(`${{endpoint}}?q=${{encodeURIComponent(trimmed)}}`, {{
                  headers: {{ Accept: "application/json" }},
                }});
                const payload = await response.json();
                renderAnswer(payload);
              }} catch (error) {{
                result.className = "ask-result ask-result-error";
                result.textContent = "暂时无法生成结论。";
              }}
            }};
            form.addEventListener("submit", (event) => {{
              event.preventDefault();
              ask(input.value);
            }});
          }}
        }})();
        </script>
      </section>"""


def build_common_ask_answers(
    *,
    code: str,
    decision_signal: dict[str, object],
    taxonomy_profile: dict[str, object],
    market_regime: dict[str, object],
    governance_report: dict[str, object],
) -> list[dict[str, object]]:
    interpreter = DecisionInterpreter()
    return [
        interpreter.interpret(
            code,
            question,
            decision_signal=decision_signal,
            taxonomy_profile=taxonomy_profile,
            market_regime=market_regime,
            governance_report=governance_report,
        )
        for question in ASK_PRESET_QUESTIONS
    ]


def xueqiu_url_for_code(code: object, preferred_url: object | None = None) -> str:
    if preferred_url:
        return str(preferred_url)
    text = str(code)
    if "." not in text:
        return "https://xueqiu.com/"
    symbol, exchange = text.split(".", 1)
    return f"https://xueqiu.com/S/{exchange.upper()}{symbol}"


def etf_page_link(code: object, label: object) -> str:
    safe_code = esc(code)
    return f"""<a class="table-link" href="/etfs/{safe_code}">{esc(label)}</a>"""


def xueqiu_etf_link(code: object, preferred_url: object | None = None) -> str:
    return (
        f"""<a class="code-link" href="{esc(xueqiu_url_for_code(code, preferred_url))}" """
        f"""target="_blank" rel="noopener noreferrer">{esc(code)}</a>"""
    )


def queue_display_rows(queue: list[object]) -> list[dict[str, object]]:
    rows_by_code: dict[str, dict[str, object]] = {}
    for row in queue:
        code = str(row["code"])
        item = rows_by_code.setdefault(
            code,
            {
                "priority": row["priority"],
                "stage": row["stage"],
                "source_labels": [],
                "code": code,
                "name": row["name"],
                "tasks": [],
                "keywords": [],
            },
        )
        label = queue_source_label(row["source_type"])
        if label not in item["source_labels"]:
            item["source_labels"].append(label)
        task_label = f"{row['task_type']}:{row['status']}"
        item["tasks"].append(task_label)
        keyword = str(row["task_keyword"] or "")
        if keyword and keyword not in item["keywords"]:
            item["keywords"].append(keyword)
    return list(rows_by_code.values())


def render_queue_rows(queue: list[object]) -> str:
    if not queue:
        return '<tr><td colspan="7" class="empty-cell">当前队列为空。</td></tr>'
    rows = queue_display_rows(queue)
    return "".join(
        f"""<tr>
      <td>{esc(row['priority'])}</td>
      <td>{esc(row['stage'])}</td>
      <td>{esc(' / '.join(row['source_labels']))}</td>
      <td>{xueqiu_etf_link(row['code'])}</td>
      <td>{etf_page_link(row['code'], row['name'])}</td>
      <td>{esc(' / '.join(row['tasks']))}</td>
      <td>{esc('；'.join(row['keywords']))}</td>
    </tr>"""
        for row in rows
    )


def _api_endpoint(
    method: str,
    path: str,
    purpose: str,
    parameters: list[dict[str, object]],
    returns: str,
    read_only: bool,
) -> dict[str, object]:
    return {
        "method": method,
        "path": path,
        "purpose": purpose,
        "parameters": parameters,
        "returns": returns,
        "read_only": read_only,
    }


def api_catalog(base_url: str) -> dict[str, object]:
    groups = [
        {
            "name": "文档入口",
            "description": "接口目录、OpenAPI 描述和轻量文档页面。",
            "endpoints": [
                _api_endpoint("GET", "/api", "返回当前系统全部公开接口目录。", [], "接口目录 JSON。", True),
                _api_endpoint("GET", "/docs", "浏览器文档入口，指向 /api 和 /openapi.json。", [], "HTML 文档页。", True),
                _api_endpoint("GET", "/redoc", "ReDoc 风格文档入口，当前提供轻量 HTML 文档页。", [], "HTML 文档页。", True),
                _api_endpoint("GET", "/openapi.json", "返回当前公开接口的 OpenAPI 3.0 描述。", [], "OpenAPI JSON。", True),
            ],
        },
        {
            "name": "Web 页面",
            "description": "给人看的页面入口；主动研究入口可能写入本地队列。",
            "endpoints": [
                _api_endpoint("GET", "/", "ETF 首页，展示研究代表、队列和接口说明。", [], "HTML 页面。", True),
                _api_endpoint(
                    "GET",
                    "/etfs/{code}",
                    "单只 ETF 详情页，展示估值图、研究历史、队列状态和风险说明。",
                    [{"name": "code", "in": "path", "required": True, "description": "ETF 代码，例如 510300.SH。"}],
                    "HTML 页面。",
                    True,
                ),
                _api_endpoint(
                    "GET",
                    "/research",
                    "主动研究入口；若 ETF 尚未入库，会写入本地研究队列并跳转详情页。",
                    [
                        {"name": "etf", "in": "query", "required": True, "description": "ETF 代码，例如 510300.SH。"},
                        {"name": "name", "in": "query", "required": False, "description": "ETF 名称，用于新请求入队展示。"},
                    ],
                    "303 跳转到 /etfs/{code}；可能创建本地队列任务。",
                    False,
                ),
            ],
        },
        {
            "name": "当前数据",
            "description": "当前 ETF 池、当前主结果和当前列表。",
            "endpoints": [
                _api_endpoint("GET", "/api/index", "对外主结果接口，返回当前 ETF 池和入口约束。", [], "myinvestetf.index.v1 JSON。", True),
                _api_endpoint("GET", "/api/etfs", "返回当前 ETF 列表及当前报告摘要。", [], "report 与 items 数组。", True),
            ],
        },
        {
            "name": "历史数据",
            "description": "单只 ETF 的研究历史、参考价格区间历史和队列历史。",
            "endpoints": [
                _api_endpoint(
                    "GET",
                    "/api/etfs/{code}",
                    "返回单只 ETF 的 leader、taxonomy、研究运行、市场/主题/产品信号、决策矩阵、队列状态和历史记录。",
                    [{"name": "code", "in": "path", "required": True, "description": "ETF 代码，例如 510300.SH。"}],
                    "leader_summary、taxonomy_profile、research_runs、regime_v2、market_signal.defensive_factor_guidance、theme_signal、product_signal、decision_matrix、queue、trackable_history。",
                    True,
                ),
                _api_endpoint(
                    "GET",
                    "/api/etf/{code}/profile",
                    "返回单只 ETF 的 taxonomy profile，不触发重新分类以外的写入动作。",
                    [{"name": "code", "in": "path", "required": True, "description": "ETF 代码，例如 510300.SH。"}],
                    "type、subtype、lifecycle、confidence、classification_reasons。",
                    True,
                ),
            ],
        },
        {
            "name": "分析结果",
            "description": "当前完整深研结果、类型化估值输出和因子分析。",
            "endpoints": [
                _api_endpoint("GET", "/api/latest", "对外研究成果接口，汇总所有 ETF 的最新深研、参考价格历史、Regime v2、市场/主题/产品信号和类型化决策矩阵。", [], "myinvestetf.research.v2 JSON；包含 etfs[].regime_v2 与 etfs[].market_signal.defensive_factor_guidance。", True),
                _api_endpoint(
                    "GET",
                    "/api/factors/{etf}",
                    "返回单只 ETF 的 point-in-time 标准化因子暴露。",
                    [{"name": "etf", "in": "path", "required": True, "description": "ETF 代码，例如 510300.SH。"}],
                    "taxonomy_profile 与 factor_exposure。",
                    True,
                ),
                _api_endpoint(
                    "GET",
                    "/api/factors/exposure/{etf}",
                    "返回单只 ETF 的 factor exposure，作为 /api/factors/{etf} 的显式别名。",
                    [{"name": "etf", "in": "path", "required": True, "description": "ETF 代码，例如 510300.SH。"}],
                    "taxonomy_profile 与 factor_exposure。",
                    True,
                ),
                _api_endpoint(
                    "GET",
                    "/api/factors/ic/{factor}",
                    "返回单个因子的 5/20/60 日 IC 摘要。",
                    [{"name": "factor", "in": "path", "required": True, "description": "因子名，例如 price_momentum_20。"}],
                    "factor definition 与 IC summaries。",
                    True,
                ),
                _api_endpoint(
                    "GET",
                    "/api/score/{etf}",
                    "返回单只 ETF 的 Regime-Aware DecisionSignal 研究评分。",
                    [{"name": "etf", "in": "path", "required": True, "description": "ETF 代码，例如 510300.SH。"}],
                    "score、regime、factor_contributions、adjusted_weights、state、confidence。",
                    True,
                ),
                _api_endpoint(
                    "GET",
                    "/api/ask/{etf}",
                    "对单只 ETF 的自然语言问题做只读决策解释，最终回答统一由 AnswerPolicyEngine 生成。",
                    [
                        {"name": "etf", "in": "path", "required": True, "description": "ETF 代码，例如 510300.SH。"},
                        {"name": "q", "in": "query", "required": False, "description": "自然语言问题，例如 现在能不能参与？"},
                    ],
                    "intent、decision、regime、final_answer、risk。",
                    True,
                ),
                _api_endpoint(
                    "GET",
                    "/api/score/decompose/{etf}",
                    "返回单只 ETF 的评分组件、动态权重和贡献拆解。",
                    [{"name": "etf", "in": "path", "required": True, "description": "ETF 代码，例如 510300.SH。"}],
                    "component_scores、factor_contributions、adjusted_weights、factor_effectiveness。",
                    True,
                ),
                _api_endpoint(
                    "GET",
                    "/api/decision/state/{etf}",
                    "返回单只 ETF 的状态机输出，不包含交易动作。",
                    [{"name": "etf", "in": "path", "required": True, "description": "ETF 代码，例如 510300.SH。"}],
                    "regime、score_band、trend_state、state_code、confidence。",
                    True,
                ),
                _api_endpoint(
                    "GET",
                    "/api/strategy/contrarian/{etf}",
                    "返回单只 ETF 的 Contrarian Mode 抄底概率模式，不覆盖原 Decision Score。",
                    [{"name": "etf", "in": "path", "required": True, "description": "ETF 代码，例如 510300.SH。"}],
                    "enabled、reversal_probability、exhaustion_score、capitulation_score、conditions、final_view。",
                    True,
                ),
                _api_endpoint(
                    "GET",
                    "/api/strategy/route/{etf}",
                    "返回单只 ETF 的 Strategy Router 策略编排结果，在 trend、contrarian、neutral 间选择。",
                    [{"name": "etf", "in": "path", "required": True, "description": "ETF 代码，例如 510300.SH。"}],
                    "active_mode、confidence、reasoning、suppressed_mode、signals、final_interpretation。",
                    True,
                ),
                _api_endpoint(
                    "GET",
                    "/api/replay/{etf}",
                    "返回单只 ETF 的历史 DecisionSignal 回放报告。",
                    [{"name": "etf", "in": "path", "required": True, "description": "ETF 代码，例如 510300.SH。"}],
                    "ReplayReport，包括 score_series、regime_series、factor_series、stability、validation。",
                    True,
                ),
                _api_endpoint(
                    "GET",
                    "/api/replay/{etf}/stability",
                    "返回单只 ETF 的回放稳定性指标。",
                    [{"name": "etf", "in": "path", "required": True, "description": "ETF 代码，例如 510300.SH。"}],
                    "score_std、regime_flip_rate、duration distribution、factor stability、consistency_score。",
                    True,
                ),
                _api_endpoint(
                    "GET",
                    "/api/replay/{etf}/regime-path",
                    "返回单只 ETF 的历史 regime path 和状态切换矩阵。",
                    [{"name": "etf", "in": "path", "required": True, "description": "ETF 代码，例如 510300.SH。"}],
                    "regime_series、regime_duration_distribution、regime_transition_matrix。",
                    True,
                ),
                _api_endpoint("GET", "/api/market/structure", "返回市场结构层，包含宽度、流动性和离散度。", [], "market_structure JSON。", True),
                _api_endpoint("GET", "/api/market/breadth", "返回市场宽度摘要。", [], "breadth JSON。", True),
                _api_endpoint("GET", "/api/market/liquidity", "返回流动性结构摘要。", [], "liquidity JSON。", True),
                _api_endpoint("GET", "/api/market/regime-v2", "返回结构驱动的 Regime v2，用于状态感知评分。", [], "market_structure 与 per ETF regime_v2。", True),
            ],
        },
        {
            "name": "系统状态",
            "description": "本地研究队列、任务状态和研究健康度。",
            "endpoints": [
                _api_endpoint("GET", "/api/queue", "返回本地 ETF 深研队列。", [], "items 队列数组。", True),
                _api_endpoint("GET", "/api/health/system", "返回系统级研究可信度健康报告。", [], "ResearchHealthReport JSON。", True),
                _api_endpoint("GET", "/api/health/data", "返回数据完整性、陈旧度、对齐和覆盖率检查。", [], "data_quality JSON。", True),
                _api_endpoint("GET", "/api/health/factors", "返回因子 IC 有效性、衰减和冗余检查。", [], "factor_quality JSON。", True),
                _api_endpoint("GET", "/api/health/regime", "返回 regime 稳定性、过敏和确认度检查。", [], "regime_quality JSON。", True),
                _api_endpoint("GET", "/api/health/report", "返回研究报告完整性、一致性、泄漏风险和可解释性 gate。", [], "report_quality JSON。", True),
            ],
        },
    ]
    total_endpoints = sum(len(group["endpoints"]) for group in groups)
    return {
        "schema_version": "myinvestetf.api_catalog.v1",
        "system": {
            "name": SYSTEM_NAME,
            "version": SYSTEM_VERSION,
            "description": SYSTEM_DESCRIPTION,
        },
        "base_url": base_url,
        "docs": {
            "docs": "/docs",
            "redoc": "/redoc",
            "openapi_json": "/openapi.json",
        },
        "recommended_entrypoints": [
            {"path": "/api/latest", "reason": "读取当前 ETF 深研成果和决策矩阵。"},
            {"path": "/api/index", "reason": "读取当前 ETF 池和入口约束。"},
            {"path": "/api/queue", "reason": "读取当前待研究队列。"},
            {"path": "/api/etfs/{code}", "reason": "读取单只 ETF 详情、taxonomy、历史和队列状态。"},
            {"path": "/api/etf/{code}/profile", "reason": "读取单只 ETF taxonomy profile。"},
            {"path": "/api/factors/{etf}", "reason": "读取单只 ETF 标准化因子暴露。"},
            {"path": "/api/score/{etf}", "reason": "读取单只 ETF 状态感知研究评分。"},
            {"path": "/api/ask/{etf}", "reason": "按问题读取统一策略层生成的 ETF 决策解释。"},
            {"path": "/api/strategy/contrarian/{etf}", "reason": "读取极端回撤下的抄底概率模式。"},
            {"path": "/api/strategy/route/{etf}", "reason": "读取自动策略路由和冲突仲裁结果。"},
            {"path": "/api/replay/{etf}", "reason": "读取单只 ETF 历史评分回放和稳定性验证。"},
            {"path": "/api/health/system", "reason": "读取系统研究可信度总览。"},
            {"path": "/api/market/regime-v2", "reason": "读取结构驱动市场状态。"},
        ],
        "safety": {
            "api_catalog_read_only": True,
            "api_endpoints_read_only": True,
            "non_read_only_public_routes": ["/research"],
            "boundaries": [
                "/api 只返回接口说明，不触发重计算、写入、交易、同步或外部请求。",
                "所有 /api/* 接口只读取本地数据库和内存生成的说明。",
                "系统不提供交易下单、现金金额、份额数量或券商写入接口。",
                "/research 是 Web 主动研究入口，可能写入本地研究队列；它不属于 /api 只读接口。",
            ],
        },
        "groups": groups,
        "total_endpoints": total_endpoints,
    }


def render_api_overview(catalog: dict[str, object]) -> str:
    entrypoints = catalog.get("recommended_entrypoints") if isinstance(catalog, dict) else []
    groups = catalog.get("groups") if isinstance(catalog, dict) else []
    safety = catalog.get("safety") if isinstance(catalog, dict) else {}
    entry_html = "".join(
        f"<li><code>{esc(item.get('path'))}</code>：{esc(item.get('reason'))}</li>"
        for item in (entrypoints if isinstance(entrypoints, list) else [])
        if isinstance(item, dict)
    )
    group_html = "".join(
        f"<li>{esc(group.get('name'))} <span>{esc(len(group.get('endpoints') or []))} 个</span></li>"
        for group in (groups if isinstance(groups, list) else [])
        if isinstance(group, dict)
    )
    boundary_items = safety.get("boundaries") if isinstance(safety, dict) else []
    boundary_html = "".join(f"<li>{esc(item)}</li>" for item in (boundary_items if isinstance(boundary_items, list) else []))
    return f"""<section class="content section-block api-overview">
      <div class="section-heading-row">
        <div>
          <h2>接口说明</h2>
          <p class="muted">统一接口目录：<a class="text-link" href="/api"><code>GET /api</code></a>，共 {esc(catalog.get('total_endpoints'))} 个公开入口。</p>
        </div>
        <span class="section-count">{esc(catalog.get('total_endpoints'))} 个</span>
      </div>
      <div class="api-overview-grid">
        <div>
          <h3>推荐入口</h3>
          <ul>{entry_html}</ul>
        </div>
        <div>
          <h3>分组</h3>
          <ul>{group_html}</ul>
        </div>
        <div>
          <h3>安全边界</h3>
          <ul>{boundary_html}</ul>
        </div>
      </div>
    </section>"""


def is_broad_index_leader(row: object) -> bool:
    return leader_model_info(row).get("valuation_model_type") == "broad_index"


def is_defensive_leader(row: object) -> bool:
    return leader_model_info(row).get("valuation_model_type") == "factor_defensive"


def render_etf_cards(
    rows: list[object],
    research_by_code: dict[str, object] | None = None,
    prices_by_code: dict[str, list[object]] | None = None,
) -> str:
    research_by_code = research_by_code or {}
    prices_by_code = prices_by_code or {}
    cards = []
    for row in rows:
        code = str(_row_value(row, "code"))
        market = load_json(_row_value(row, "market_json"), {})
        model_info = leader_model_info(row)
        category_key = leader_category_key(row)
        taxonomy_profile = taxonomy_profile_from_sources(code=code, leader=row, latest=research_by_code.get(code))
        etf_type = str(taxonomy_profile.get("etf_type") or "")
        latest = research_by_code.get(code)
        current_price = _display_current_price(latest, prices_by_code.get(code, []), market)
        reference_mid = _row_value(latest, "valuation_mid") if latest is not None else None
        position_view = _row_value(latest, "heavy_position_view") if latest is not None else None
        position_view_display = portfolio_use_view(position_view)
        cards.append(
            f"""<article class="etf-card">
        <div class="etf-card-top">
          <div>
            <a class="etf-title" href="/etfs/{esc(code)}">{esc(_row_value(row, 'name'))}</a>
            <div class="etf-code">{xueqiu_etf_link(code, _row_value(row, 'xueqiu_url'))}</div>
          </div>
          <a class="text-link card-action" href="/etfs/{esc(code)}">查看</a>
        </div>
        <div class="badges">
          <span class="badge badge-strong">{esc(_row_value(row, 'deep_rating') or '')} {esc(_row_value(row, 'deep_label') or '')}</span>
          <span class="badge">{esc(category_key)}</span>
          <span class="badge">{esc(TAXONOMY_LABELS.get(etf_type, etf_type or '待分类'))}</span>
          <span class="badge">{esc(model_info.get('valuation_model_label'))}</span>
          <span class="badge">{esc(model_info.get('sleeve_label'))}</span>
        </div>
        <div class="compact-metrics">
          {compact_metric("深研", _row_value(row, "deep_score"))}
          {compact_metric("当前价格", current_price)}
          {compact_metric("参考中枢", reference_mid)}
          {compact_metric("组合使用判断", position_view_display)}
        </div>
      </article>"""
        )
    return "".join(cards) if cards else '<p class="empty">暂无代表 ETF。</p>'


def render_home() -> bytes:
    with closing(connect(DB_PATH)) as conn:
        report = latest_report(conn)
        leaders = list_latest_leaders(conn)
        queue = list_queue(conn)
    if not report:
        body = """
    <section class="page-band">
      <div class="content">
        <h1>可跟踪ETF</h1>
        <p class="muted">本地还没有入库数据。先运行 <code>python scripts/ingest_index.py</code>。</p>
      </div>
    </section>
"""
        return render_layout("可跟踪ETF", body)

    leader_by_code = {row["code"]: row for row in leaders}
    queue_codes = []
    for row in queue:
        code = row["code"]
        if code not in queue_codes:
            queue_codes.append(code)
    display_leaders = [leader_by_code[code] for code in queue_codes if code in leader_by_code]
    if not display_leaders:
        display_leaders = leaders

    broad_leaders = [row for row in display_leaders if is_broad_index_leader(row)]
    defensive_leaders = [row for row in display_leaders if is_defensive_leader(row)]
    mainline_leaders = [
        row for row in display_leaders if not is_broad_index_leader(row) and not is_defensive_leader(row)
    ]
    research_by_code: dict[str, object] = {}
    prices_by_code: dict[str, list[object]] = {}
    with closing(connect(DB_PATH)) as conn:
        for row in display_leaders:
            code = str(row["code"])
            runs = list_research_runs(conn, code)
            latest = next((item for item in runs if item["task_type"] == "research"), runs[0] if runs else None)
            if latest is not None:
                research_by_code[code] = latest
            prices_by_code[code] = list_daily_prices(conn, code, limit=1)

    queue_summary_count = len(queue_display_rows(queue))
    queue_rows = render_queue_rows(queue)
    api_overview_section = render_api_overview(api_catalog(""))
    body = f"""
    <section class="page-band">
      <div class="content">
        <div class="page-title-row">
          <div>
            <h1>ETF研究代表</h1>
            <p class="muted">ETF 池来自 <code>theme_ranking.top_etf</code>、<code>result.etf_top</code>、本地核心宽基种子和收益防御种子。</p>
            <p class="muted">当前 ETF 池 {esc(len(leaders))} 只；主屏显示 {esc(len(display_leaders))} 只研究代表：核心宽基 {esc(len(broad_leaders))} 只，收益防御 {esc(len(defensive_leaders))} 只，主线代表 {esc(len(mainline_leaders))} 只。</p>
          </div>
          <div class="report-box">
            <span>report_id</span>
            <strong>{esc(report['report_id'])}</strong>
            <span>basis_date {esc(report['basis_date'])}</span>
          </div>
        </div>
      </div>
    </section>
    <section class="content representative-section">
      <div class="section-heading-row">
        <div>
          <h2>核心宽基ETF</h2>
          <p class="muted">来源为本地核心宽基种子和宽基类别代表，不从主线代表推导。</p>
        </div>
        <span class="section-count">{esc(len(broad_leaders))} 只</span>
      </div>
      <div class="etf-grid">{render_etf_cards(broad_leaders, research_by_code, prices_by_code)}</div>
    </section>
    <section class="content representative-section">
      <div class="section-heading-row">
        <div>
          <h2>收益防御ETF</h2>
          <p class="muted">纳入自由现金流和红利低波收益防御代表；自由现金流当前使用 159201.SZ，按收益防御估值逻辑研究，不从主线强度推导。</p>
        </div>
        <span class="section-count">{esc(len(defensive_leaders))} 只</span>
      </div>
      <div class="etf-grid">{render_etf_cards(defensive_leaders, research_by_code, prices_by_code)}</div>
    </section>
    <section class="content representative-section">
      <div class="section-heading-row">
        <div>
          <h2>主线ETF代表</h2>
          <p class="muted">来源为 <code>theme_ranking.top_etf</code>，每条主线只保留一个流动性代表。</p>
        </div>
        <span class="section-count">{esc(len(mainline_leaders))} 只</span>
      </div>
      <div class="etf-grid">{render_etf_cards(mainline_leaders, research_by_code, prices_by_code)}</div>
    </section>
    <section class="content section-block">
      <h2>ETF深研队列</h2>
      <p class="muted">按 ETF 合并显示：{esc(queue_summary_count)} 只 ETF，{esc(len(queue))} 个研究任务。</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>优先级</th><th>阶段</th><th>来源</th><th>代码</th><th>名称</th><th>任务状态</th><th>任务关键词</th></tr></thead>
          <tbody>{queue_rows}</tbody>
        </table>
      </div>
    </section>
    {api_overview_section}
"""
    return render_layout("可跟踪ETF", body)


def render_empty_section(title: str) -> str:
    return f"""<section class="section-block">
      <h2>{esc(title)}</h2>
      <p class="empty">等待ETF深研入库。</p>
    </section>"""


def short_date(value: object) -> str:
    text = str(value or "")
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text[5:]
    return text


def _chart_x(index: int, count: int, left: float, width: float) -> float:
    if count <= 1:
        return left + width / 2.0
    return left + width * index / (count - 1)


def _chart_y(value: float, lower: float, upper: float, top: float, height: float) -> float:
    if upper <= lower:
        return top + height / 2.0
    return top + (upper - value) / (upper - lower) * height


def _chart_y_domain(values: list[float]) -> tuple[float, float]:
    clean_values = [float(value) for value in values if value == value]
    if not clean_values:
        return 0.0, 1.0
    lower = min(clean_values)
    upper = max(clean_values)
    center = (lower + upper) / 2.0
    span = upper - lower
    if span <= 0:
        pad = max(abs(center) * 0.015, 0.01)
    else:
        pad = max(span * 0.08, abs(center) * 0.002, 0.01)
    return lower - pad, upper + pad


def _latest_reference_level_lines(
    point: dict[str, object],
    *,
    y_min: float,
    y_max: float,
    top: float,
    plot_height: float,
    plot_bottom: float,
    left: float,
    plot_right: float,
) -> str:
    levels = [
        ("high", "高", "最新参考高位", float(point["high"])),
        ("mid", "中枢", "最新参考中枢", float(point["mid"])),
        ("low", "低", "最新参考低位", float(point["low"])),
    ]
    label_height = 18.0
    label_width = 88.0
    label_x = left + 8.0
    min_label_y = top + 2.0
    max_label_y = plot_bottom - label_height - 2.0
    label_gap = 2.0
    positioned_labels = []
    for suffix, short_label, full_label, value in levels:
        y = _chart_y(value, y_min, y_max, top, plot_height)
        positioned_labels.append(
            {
                "suffix": suffix,
                "short_label": short_label,
                "full_label": full_label,
                "value": value,
                "y": y,
                "label_y": min(max(y - label_height / 2.0, min_label_y), max_label_y),
            }
        )
    positioned_labels.sort(key=lambda item: float(item["label_y"]))
    previous_y = min_label_y - label_height - label_gap
    for item in positioned_labels:
        item["label_y"] = max(float(item["label_y"]), previous_y + label_height + label_gap)
        previous_y = float(item["label_y"])
    if positioned_labels and float(positioned_labels[-1]["label_y"]) > max_label_y:
        overflow = float(positioned_labels[-1]["label_y"]) - max_label_y
        for item in positioned_labels:
            item["label_y"] = max(min_label_y, float(item["label_y"]) - overflow)

    lines = []
    for item in positioned_labels:
        suffix = item["suffix"]
        y = float(item["y"])
        label_y = float(item["label_y"])
        label_text = f"{item['short_label']} {fmt_num(item['value'])}"
        title = f"{item['full_label']} {fmt_num(item['value'])}"
        lines.append(
            f"""<g class="reference-level reference-level-{suffix}">
            <title>{esc(title)}</title>
            <line class="reference-level-line reference-level-line-{suffix}" x1="{left:.1f}" y1="{y:.1f}" x2="{plot_right:.1f}" y2="{y:.1f}"></line>
            <rect class="reference-level-label-bg reference-level-label-bg-{suffix}" x="{label_x:.1f}" y="{label_y:.1f}" width="{label_width:.1f}" height="{label_height:.1f}" rx="4"></rect>
            <text class="reference-level-label reference-level-label-{suffix}" x="{label_x + 7.0:.1f}" y="{label_y + 12.8:.1f}">{esc(label_text)}</text>
          </g>"""
        )
    return "".join(lines)


def _valuation_chart_points(runs: list[object]) -> list[dict[str, object]]:
    points: list[dict[str, object]] = []
    for row in runs:
        try:
            low = float(row["valuation_low"])
            mid = float(row["valuation_mid"])
            high = float(row["valuation_high"])
        except (KeyError, TypeError, ValueError):
            continue
        if high < low:
            low, high = high, low
        points.append(
            {
                "date": str(row["research_date"]),
                "low": low,
                "mid": mid,
                "high": high,
                "method": row["valuation_method"] or "待入库",
                "grade": row["heavy_position_view"] or "待入库",
            }
        )
    return points


def _row_value(row: object, key: str) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        if isinstance(row, dict):
            return row.get(key)
        return None


def _daily_price_points(prices: list[object]) -> list[dict[str, object]]:
    points: list[dict[str, object]] = []
    for row in prices:
        try:
            open_price = float(_row_value(row, "open_price"))
            high_price = float(_row_value(row, "high_price"))
            low_price = float(_row_value(row, "low_price"))
            close_price = float(_row_value(row, "close_price"))
        except (TypeError, ValueError):
            continue
        if high_price < low_price:
            high_price, low_price = low_price, high_price
        points.append(
            {
                "date": str(_row_value(row, "trade_date")),
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
            }
        )
    return points


def _latest_cached_close(prices: list[object]) -> float | None:
    points = _daily_price_points(prices)
    if not points:
        return None
    points.sort(key=lambda item: str(item["date"]))
    return _num(points[-1].get("close"))


def _research_current_price(row: object | None) -> float | None:
    if row is None:
        return None
    raw = load_json(_row_value(row, "raw_json"), {})
    valuation = raw.get("valuation") if isinstance(raw, dict) else {}
    if not isinstance(valuation, dict):
        return None
    return _num(valuation.get("current_price"))


def _display_current_price(latest: object | None, prices: list[object], market: object) -> float | None:
    cached_close = _latest_cached_close(prices)
    if cached_close is not None:
        return cached_close
    research_price = _research_current_price(latest)
    if research_price is not None:
        return research_price
    if isinstance(market, dict):
        return _num(market.get("close"))
    return None


def market_context_from_prices(code: str, prices: list[object] | None) -> dict[str, object]:
    return market_context_to_dict(build_market_context(code, etf_prices=prices or []))


def market_structure_from_connection(conn: object) -> tuple[object, dict[str, object], dict[str, list[object]], dict[str, object]]:
    leaders = list_latest_leaders(conn)
    price_series_by_code = {
        str(row["code"]): list_daily_prices(conn, str(row["code"]), start_date=BULL_MARKET_START_DATE)
        for row in leaders
    }
    taxonomy_by_code = {
        str(row["code"]): taxonomy_profile_from_sources(code=str(row["code"]), leader=row)
        for row in leaders
    }
    structure = build_market_structure(price_series_by_code, taxonomy_by_code)
    return structure, market_structure_to_dict(structure), price_series_by_code, taxonomy_by_code


def _market_context_label(context: dict[str, object]) -> str:
    drawdown = context.get("drawdown")
    if isinstance(drawdown, dict) and not drawdown.get("data_points"):
        return "待行情入库"
    regime = context.get("regime")
    if not isinstance(regime, dict):
        return "待行情入库"
    value = str(regime.get("regime") or "")
    return REGIME_LABELS.get(value, value or "待行情入库")


def render_market_context(context: dict[str, object] | None) -> str:
    context = context or {}
    regime = context.get("regime") if isinstance(context.get("regime"), dict) else {}
    drawdown = context.get("drawdown") if isinstance(context.get("drawdown"), dict) else {}
    data_points = drawdown.get("data_points") if isinstance(drawdown, dict) else None
    as_of_date = drawdown.get("as_of_date") if isinstance(drawdown, dict) else None
    note = (
        "市场状态和回撤会进入状态感知研究评分；收益防御ETF的深回撤会额外形成机会分。"
        if data_points
        else "本地尚未缓存足够行情，等待 update_etf_prices 后生成市场状态和回撤上下文。"
    )
    return f"""<section class="section-block">
        <h2>市场状态与回撤</h2>
        <p class="muted">{esc(note)}</p>
        <div class="signal-grid">
          {signal_item("市场状态", _market_context_label(context), f"置信度 {fmt_ratio_percent(regime.get('confidence') if isinstance(regime, dict) else None)}")}
          {signal_item("当前回撤", fmt_ratio_percent(drawdown.get("current_drawdown") if isinstance(drawdown, dict) else None), as_of_date)}
          {signal_item("滚动最大回撤", fmt_ratio_percent(drawdown.get("max_drawdown_rolling") if isinstance(drawdown, dict) else None))}
          {signal_item("回撤分位", fmt_percentile(drawdown.get("drawdown_percentile") if isinstance(drawdown, dict) else None))}
          {signal_item("修复速度", fmt_ratio_percent(drawdown.get("recovery_speed") if isinstance(drawdown, dict) else None, digits=3, signed=True), "日均，从本轮低点计算")}
          {signal_item("持续天数", drawdown.get("duration_days") if isinstance(drawdown, dict) else None, "交易日")}
        </div>
      </section>"""


def render_market_regime_v2(regime: dict[str, object] | None, structure: dict[str, object] | None) -> str:
    regime = regime or {}
    structure = structure or {}
    nested = regime.get("structure") if isinstance(regime.get("structure"), dict) else {}
    return f"""<section class="section-block">
        <h2>结构化市场状态</h2>
        <p class="muted">Regime v2 = 40%价格趋势 + 30%宽度 + 20%流动性 + 10%波动；用于状态、动态权重和最终研究评分解释。</p>
        <div class="signal-grid">
          {signal_item("Regime v2", REGIME_LABELS.get(str(regime.get("regime") or ""), regime.get("regime")))}
          {signal_item("确认强度", regime.get("confirmation_level"))}
          {signal_item("宽度贡献", fmt_ratio_percent((regime.get("evidence") or {}).get("breadth_contribution") if isinstance(regime.get("evidence"), dict) else None))}
          {signal_item("流动性贡献", fmt_ratio_percent((regime.get("evidence") or {}).get("liquidity_contribution") if isinstance(regime.get("evidence"), dict) else None))}
          {signal_item("市场宽度", fmt_ratio_percent(nested.get("breadth_score") if isinstance(nested, dict) else None))}
          {signal_item("流动性宽度", fmt_ratio_percent(nested.get("liquidity_score") if isinstance(nested, dict) else None))}
          {signal_item("离散度分", fmt_ratio_percent(nested.get("dispersion_score") if isinstance(nested, dict) else None))}
          {signal_item("样本数", structure.get("observations"))}
        </div>
        <p class="signal-note">{esc(regime.get("explanation") or "等待结构化市场状态。")}</p>
      </section>"""


def render_taxonomy_profile(profile: dict[str, object] | None) -> str:
    profile = profile or {}
    etf_type = str(profile.get("etf_type") or "")
    lifecycle = str(profile.get("lifecycle_stage") or "")
    reasons = profile.get("classification_reasons")
    reason_items = "".join(
        f"<li>{esc(reason)}</li>"
        for reason in (reasons if isinstance(reasons, list) else [])
        if str(reason).strip()
    )
    return f"""<section class="section-block">
        <h2>ETF分类画像</h2>
        <p class="muted">taxonomy 决定产品类型、研究路由和 DecisionSignal 权重；自由现金流、红利低波等收益防御 ETF 会单独识别。</p>
        <div class="signal-grid">
          {signal_item("ETF类型", TAXONOMY_LABELS.get(etf_type, etf_type or "待分类"))}
          {signal_item("子类", profile.get("subtype"))}
          {signal_item("生命周期", LIFECYCLE_LABELS.get(lifecycle, lifecycle or "不适用"))}
          {signal_item("分类置信度", fmt_ratio_percent(profile.get("classification_confidence")))}
          {signal_item("兼容估值模型", profile.get("legacy_valuation_model_type"))}
          {signal_item("兼容五仓角色", profile.get("legacy_sleeve_key"))}
        </div>
        <ul class="risk-list">{reason_items or '<li>等待分类依据入库。</li>'}</ul>
      </section>"""


def factor_exposure_from_prices(code: str, prices: list[object] | None, taxonomy_profile: dict[str, object] | None) -> dict[str, object]:
    return factor_exposure_to_dict(
        build_factor_exposure(
            etf_code=code,
            price_series=prices or [],
            taxonomy_profile=taxonomy_profile,
            lag_days=1,
        )
    )


def decision_signal_from_inputs(
    *,
    code: str,
    factor_exposure: dict[str, object] | None,
    market_regime_v2: dict[str, object] | None,
    taxonomy_profile: dict[str, object] | None,
    valuation_signal: dict[str, object] | None,
) -> dict[str, object]:
    return decision_signal_to_dict(
        build_decision_signal(
            etf_code=code,
            factor_exposure=factor_exposure,
            market_regime=market_regime_v2,
            taxonomy_profile=taxonomy_profile,
            valuation_signal=valuation_signal,
        )
    )


def contrarian_signal_from_inputs(
    *,
    code: str,
    market_context: dict[str, object] | None,
    market_regime_v2: dict[str, object] | None,
    market_structure: dict[str, object] | None,
    factor_exposure: dict[str, object] | None,
    governance_report: dict[str, object] | None,
    decision_signal: dict[str, object] | None,
) -> dict[str, object]:
    engine = ContrarianModeEngine(
        {
            "etf_code": code,
            "drawdown": (market_context or {}).get("drawdown") if isinstance(market_context, dict) else {},
            "regime_v2": market_regime_v2 or {},
            "market_structure": market_structure or {},
        },
        factor_exposure or {},
        governance_report or {},
    )
    return contrarian_signal_to_dict(engine.adjust_decision(decision_signal or {}))


def strategy_decision_from_inputs(
    *,
    code: str,
    decision_signal: dict[str, object] | None,
    contrarian_signal: dict[str, object] | None,
    market_regime_v2: dict[str, object] | None,
    governance_report: dict[str, object] | None,
) -> dict[str, object]:
    router = StrategyRouter(
        decision_signal or {},
        contrarian_signal or {},
        market_regime_v2 or {},
        governance_report or {},
    )
    return strategy_decision_to_dict(router.route(code))


def render_factor_exposure(exposure: dict[str, object] | None) -> str:
    exposure = exposure or {}
    factors = exposure.get("factors")
    factor_items = factors if isinstance(factors, list) else []
    if not factor_items:
        return """<section class="section-block">
        <h2>因子暴露</h2>
        <p class="empty">本地行情不足，暂未生成标准化因子。</p>
      </section>"""
    rows = "".join(
        f"""<tr>
      <td>{esc(item.get('factor_name'))}</td>
      <td>{esc(item.get('factor_type'))}</td>
      <td>{fmt_num(item.get('raw_value'), 6)}</td>
      <td>{fmt_num(item.get('z_score'), 4)}</td>
      <td>{fmt_percentile(item.get('percentile'))}</td>
      <td>{esc(item.get('as_of_date'))}</td>
    </tr>"""
        for item in factor_items
        if isinstance(item, dict)
    )
    attribution = exposure.get("attribution")
    attribution_text = "；".join(
        f"{name} {fmt_ratio_percent(weight)}"
        for name, weight in (attribution.items() if isinstance(attribution, dict) else [])
    )
    return f"""<section class="section-block">
        <h2>因子暴露</h2>
        <p class="muted">因子采用 point-in-time lag 1，对齐到 {esc(exposure.get('as_of_date') or '待入库')}；用于暴露解释、IC验证和 DecisionSignal 组件评分。</p>
        <div class="table-wrap">
          <table>
            <thead><tr><th>因子</th><th>类型</th><th>原始值</th><th>Z分</th><th>分位</th><th>as_of_date</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        <p class="signal-note">因子贡献占比：{esc(attribution_text or '待入库')}</p>
      </section>"""


def render_decision_signal(signal: dict[str, object] | None) -> str:
    signal = signal or {}
    state = signal.get("state") if isinstance(signal.get("state"), dict) else {}
    component_scores = signal.get("component_scores") if isinstance(signal.get("component_scores"), dict) else {}
    contributions = signal.get("factor_contributions") if isinstance(signal.get("factor_contributions"), dict) else {}
    adjusted_weights = signal.get("adjusted_weights") if isinstance(signal.get("adjusted_weights"), dict) else {}
    component_rows = "".join(
        f"""<tr>
      <td>{esc(name)}</td>
      <td>{fmt_num(component_scores.get(name))}</td>
      <td>{fmt_ratio_percent(adjusted_weights.get(name))}</td>
      <td>{fmt_num(contributions.get(name))}</td>
    </tr>"""
        for name in ["momentum", "flow", "valuation", "risk"]
    )
    return f"""<section class="section-block">
        <h2>状态感知研究评分</h2>
        <p class="muted">Decision Engine 根据 Regime v2、taxonomy、因子暴露和类型化估值动态调整权重；只输出研究评分、状态和解释，不输出交易动作。</p>
        <div class="signal-grid">
          {signal_item("Decision Score", fmt_num(signal.get("score")))}
          {signal_item("状态码", state.get("state_code") if isinstance(state, dict) else None)}
          {signal_item("评分带", state.get("score_band") if isinstance(state, dict) else None)}
          {signal_item("趋势状态", state.get("trend_state") if isinstance(state, dict) else None)}
          {signal_item("Regime", REGIME_LABELS.get(str((state or {}).get("regime") or ""), (state or {}).get("regime")) if isinstance(state, dict) else None)}
          {signal_item("置信度", fmt_ratio_percent(signal.get("confidence")))}
        </div>
        <div class="table-wrap compact-table">
          <table>
            <thead><tr><th>组件</th><th>组件分</th><th>动态权重</th><th>贡献分</th></tr></thead>
            <tbody>{component_rows}</tbody>
          </table>
        </div>
        <p class="signal-note">{esc(signal.get("explanation") or "等待决策评分输入。")}</p>
      </section>"""


def render_contrarian_signal(signal: dict[str, object] | None) -> str:
    signal = signal or {}
    scores = signal.get("scores") if isinstance(signal.get("scores"), dict) else {}
    conditions = signal.get("conditions") if isinstance(signal.get("conditions"), dict) else {}
    adjusted = signal.get("adjusted_interpretation") if isinstance(signal.get("adjusted_interpretation"), dict) else {}
    evidence = signal.get("evidence") if isinstance(signal.get("evidence"), dict) else {}
    final_view = str(adjusted.get("final_view") or signal.get("final_view") or "not_active")
    final_label = {
        "probabilistic_bottom_zone": "概率底部观察区",
        "normal": "普通模式观察",
        "not_active": "未触发",
    }.get(final_view, final_view)
    condition_details = {
        "drawdown_extreme": (
            f"当前回撤 {fmt_ratio_percent(evidence.get('current_drawdown'))}，"
            f"回撤分位 {fmt_ratio_percent(evidence.get('drawdown_percentile'))}，"
            f"历史极值接近度 {fmt_ratio_percent(evidence.get('extreme_proximity'))}"
        ),
        "regime_stress": f"当前市场状态 {REGIME_LABELS.get(str(evidence.get('regime') or ''), evidence.get('regime') or '待入库')}",
        "volatility_stress": f"20日波动 {fmt_ratio_percent(evidence.get('volatility_20'))}，触发线约 2.80%",
        "liquidity_stress": (
            f"市场流动性 {fmt_ratio_percent(evidence.get('liquidity_score'))}，"
            f"资金流分 {fmt_ratio_percent(evidence.get('flow_score'))}"
        ),
        "governance_allowed": f"系统健康闸口 {evidence.get('governance_gate') or '待入库'}，reject 时禁止触发",
    }
    condition_items = "".join(
        f"""<li>
          <strong>{esc(label)}：{esc('满足' if bool(conditions.get(key)) else '未满足')}</strong>
          <span>{esc(condition_details.get(key, ''))}</span>
        </li>"""
        for key, label in [
            ("drawdown_extreme", "极端回撤"),
            ("regime_stress", "压力状态"),
            ("volatility_stress", "波动压力"),
            ("liquidity_stress", "流动性压力"),
            ("governance_allowed", "系统健康允许"),
        ]
    )
    return f"""<section class="section-block">
        <h2>抄底概率模式</h2>
        <p class="muted">Contrarian Mode 是极端回撤下的再解释层，只输出概率底部观察，不覆盖 Decision Score，不输出交易动作。</p>
        <div class="signal-grid">
          {signal_item("模式状态", "ON" if signal.get("enabled") else "OFF", final_label)}
          {signal_item("反转概率", fmt_ratio_percent(scores.get("reversal_probability") if isinstance(scores, dict) else None))}
          {signal_item("趋势衰竭", fmt_ratio_percent(scores.get("exhaustion_score") if isinstance(scores, dict) else None))}
          {signal_item("恐慌释放", fmt_ratio_percent(scores.get("capitulation_score") if isinstance(scores, dict) else None))}
          {signal_item("原始Decision", fmt_num(adjusted.get("original_decision_score") if isinstance(adjusted, dict) else None))}
          {signal_item("解释后分", fmt_num(adjusted.get("risk_adjusted_score") if isinstance(adjusted, dict) else None))}
          {signal_item("当前回撤", fmt_ratio_percent(evidence.get("current_drawdown") if isinstance(evidence, dict) else None))}
          {signal_item("历史极值接近度", fmt_ratio_percent(evidence.get("extreme_proximity") if isinstance(evidence, dict) else None))}
        </div>
        <ul class="risk-list">{condition_items}</ul>
        <p class="signal-note">{esc(adjusted.get("explanation") if isinstance(adjusted, dict) else "等待抄底概率模式输入。")}</p>
      </section>"""


def render_strategy_decision(decision: dict[str, object] | None) -> str:
    decision = decision or {}
    reasoning = decision.get("reasoning") if isinstance(decision.get("reasoning"), dict) else {}
    signals = decision.get("signals") if isinstance(decision.get("signals"), dict) else {}
    active_mode = str(decision.get("active_mode") or "neutral")
    mode_label = {
        "trend": "顺势模式",
        "contrarian": "抄底概率模式",
        "neutral": "中性观察",
    }.get(active_mode, active_mode)
    reason_items = "".join(
        f"<li>{esc(label)}：{esc(reasoning.get(key))}</li>"
        for key, label in [
            ("regime_reason", "市场状态"),
            ("flow_reason", "资金/流动性"),
            ("drawdown_reason", "回撤"),
            ("governance_reason", "治理"),
        ]
    )
    return f"""<section class="section-block">
        <h2>策略路由</h2>
        <p class="muted">Strategy Router 在顺势模式、抄底概率模式和中性观察之间做解释层选择；不修改原始 Decision Score。</p>
        <div class="signal-grid">
          {signal_item("当前模式", mode_label)}
          {signal_item("路由置信度", fmt_ratio_percent(decision.get("confidence")))}
          {signal_item("顺势分", fmt_ratio_percent(signals.get("trend_score") if isinstance(signals, dict) else None))}
          {signal_item("抄底分", fmt_ratio_percent(signals.get("contrarian_score") if isinstance(signals, dict) else None))}
          {signal_item("原始Decision", fmt_ratio_percent(signals.get("decision_score") if isinstance(signals, dict) else None))}
          {signal_item("被抑制模式", decision.get("suppressed_mode") or "无")}
        </div>
        <ul class="risk-list">{reason_items}</ul>
        <p class="signal-note">{esc(decision.get("final_interpretation") or "等待策略路由输入。")}</p>
      </section>"""


def _parsed_date(value: object) -> object | None:
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def _valuation_price_start(runs: list[object]) -> str | None:
    return BULL_MARKET_START_DATE if runs else None


def _render_plain_valuation_chart(points: list[dict[str, object]]) -> str:
    if not points:
        return render_empty_section("ETF参考价格区间历史")

    width = 760.0
    height = 320.0
    left = 64.0
    right = 24.0
    top = 28.0
    bottom = 52.0
    plot_width = width - left - right
    plot_height = height - top - bottom
    plot_right = width - right
    plot_bottom = height - bottom
    lows = [float(item["low"]) for item in points]
    highs = [float(item["high"]) for item in points]
    y_min, y_max = _chart_y_domain(lows + highs)

    positioned = []
    count = len(points)
    for index, point in enumerate(points):
        x = _chart_x(index, count, left, plot_width)
        positioned.append(
            {
                **point,
                "x": x,
                "y_low": _chart_y(float(point["low"]), y_min, y_max, top, plot_height),
                "y_mid": _chart_y(float(point["mid"]), y_min, y_max, top, plot_height),
                "y_high": _chart_y(float(point["high"]), y_min, y_max, top, plot_height),
            }
        )

    tick_lines = []
    for index in range(5):
        value = y_max - (y_max - y_min) * index / 4.0
        y = _chart_y(value, y_min, y_max, top, plot_height)
        tick_lines.append(
            f"""<g>
          <line class="valuation-grid-line" x1="{left:.1f}" y1="{y:.1f}" x2="{width - right:.1f}" y2="{y:.1f}"></line>
          <text class="valuation-axis-label" x="{left - 10:.1f}" y="{y + 4:.1f}" text-anchor="end">{fmt_num(value)}</text>
        </g>"""
        )

    if count > 1:
        upper_points = " ".join(f"{item['x']:.1f},{item['y_high']:.1f}" for item in positioned)
        lower_points = " ".join(f"{item['x']:.1f},{item['y_low']:.1f}" for item in reversed(positioned))
        band_svg = f"""<polygon class="valuation-band" points="{upper_points} {lower_points}"></polygon>"""
        high_line = f"""<polyline class="valuation-boundary-line" points="{upper_points}"></polyline>"""
        low_line = f"""<polyline class="valuation-boundary-line" points="{" ".join(f"{item['x']:.1f},{item['y_low']:.1f}" for item in positioned)}"></polyline>"""
        mid_line = f"""<polyline class="valuation-mid-line" points="{" ".join(f"{item['x']:.1f},{item['y_mid']:.1f}" for item in positioned)}"></polyline>"""
    else:
        band_svg = ""
        high_line = ""
        low_line = ""
        mid_line = ""

    label_step = max(1, (count + 5) // 6)
    x_labels = []
    markers = []
    for index, item in enumerate(positioned):
        if index % label_step == 0 or index == count - 1:
            x_labels.append(
                f"""<text class="valuation-date-label" x="{item['x']:.1f}" y="{height - 18:.1f}" text-anchor="middle">{esc(short_date(item['date']))}</text>"""
            )
        tooltip = (
            f"{item['date']} | 保守 {fmt_num(item['low'])} | 合理 {fmt_num(item['mid'])} | "
            f"乐观 {fmt_num(item['high'])} | {item['method']} | {item['grade']}"
        )
        markers.append(
            f"""<g class="valuation-point">
          <title>{esc(tooltip)}</title>
          <line class="valuation-whisker" x1="{item['x']:.1f}" y1="{item['y_high']:.1f}" x2="{item['x']:.1f}" y2="{item['y_low']:.1f}"></line>
          <circle class="valuation-mid-dot" cx="{item['x']:.1f}" cy="{item['y_mid']:.1f}" r="4.5"></circle>
        </g>"""
        )

    latest_reference_lines = _latest_reference_level_lines(
        positioned[-1],
        y_min=y_min,
        y_max=y_max,
        top=top,
        plot_height=plot_height,
        plot_bottom=plot_bottom,
        left=left,
        plot_right=plot_right,
    )

    return f"""<section class="section-block">
      <h2>ETF参考价格区间历史</h2>
      <div class="valuation-chart">
        <svg class="valuation-history-svg" viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="ETF参考价格区间随时间变化图">
          <title>ETF参考价格区间随时间变化图</title>
          <line class="valuation-axis-line" x1="{left:.1f}" y1="{top:.1f}" x2="{left:.1f}" y2="{plot_bottom:.1f}"></line>
          <line class="valuation-axis-line" x1="{left:.1f}" y1="{plot_bottom:.1f}" x2="{plot_right:.1f}" y2="{plot_bottom:.1f}"></line>
          <text class="valuation-axis-title" x="{left:.1f}" y="16" text-anchor="start">价格 CNY/fund_share</text>
          {''.join(tick_lines)}
          {band_svg}
          {high_line}
          {low_line}
          {mid_line}
          {latest_reference_lines}
          {''.join(markers)}
          {''.join(x_labels)}
        </svg>
        <div class="valuation-legend">
          <span><i class="legend-reference-level"></i>最新低/中枢/高</span>
          <span><i class="legend-band"></i>保守-乐观区间</span>
          <span><i class="legend-line"></i>参考价格中枢</span>
          <span><i class="legend-dot"></i>单次完整深研</span>
        </div>
        <p class="chart-note">2024-09-24以来收盘价待入库，当前仅显示完整深研生成的参考价格区间。</p>
      </div>
    </section>"""


def _price_index_on_or_after(price_dates: list[object], date_value: object) -> int:
    target = _parsed_date(date_value)
    if target is None:
        return 0
    for index, price_date in enumerate(price_dates):
        if price_date is not None and price_date >= target:
            return index
    return max(len(price_dates) - 1, 0)


def _render_close_price_valuation_chart(
    valuation_points: list[dict[str, object]],
    price_points: list[dict[str, object]],
) -> str:
    if len(price_points) < 2:
        return _render_plain_valuation_chart(valuation_points)

    width = 760.0
    height = 360.0
    left = 64.0
    right = 24.0
    top = 30.0
    bottom = 58.0
    plot_width = width - left - right
    plot_height = height - top - bottom
    plot_right = width - right
    plot_bottom = height - bottom

    close_prices = [float(item["close"]) for item in price_points]
    valuation_lows = [float(item["low"]) for item in valuation_points]
    valuation_highs = [float(item["high"]) for item in valuation_points]
    valuation_mids = [float(item["mid"]) for item in valuation_points]
    y_min, y_max = _chart_y_domain(close_prices + valuation_lows + valuation_mids + valuation_highs)

    price_dates = [_parsed_date(item["date"]) for item in price_points]
    price_count = len(price_points)
    spacing = plot_width / (price_count - 1)

    tick_lines = []
    for index in range(5):
        value = y_max - (y_max - y_min) * index / 4.0
        y = _chart_y(value, y_min, y_max, top, plot_height)
        tick_lines.append(
            f"""<g>
          <line class="valuation-grid-line" x1="{left:.1f}" y1="{y:.1f}" x2="{plot_right:.1f}" y2="{y:.1f}"></line>
          <text class="valuation-axis-label" x="{left - 10:.1f}" y="{y + 4:.1f}" text-anchor="end">{fmt_num(value)}</text>
        </g>"""
        )

    close_line_points = []
    label_step = max(1, (price_count + 5) // 6)
    x_labels = []
    for index, item in enumerate(price_points):
        x = _chart_x(index, price_count, left, plot_width)
        y_close = _chart_y(float(item["close"]), y_min, y_max, top, plot_height)
        close_line_points.append(f"{x:.1f},{y_close:.1f}")
        if index % label_step == 0 or index == price_count - 1:
            x_labels.append(
                f"""<text class="valuation-date-label" x="{x:.1f}" y="{height - 18:.1f}" text-anchor="middle">{esc(short_date(item['date']))}</text>"""
            )

    first_price = price_points[0]
    last_price = price_points[-1]
    current_price = float(last_price["close"])
    y_current = _chart_y(current_price, y_min, y_max, top, plot_height)
    current_label = f"当前价 {fmt_num(current_price)}"
    current_label_width = 96.0
    current_label_height = 20.0
    current_label_x = max(left + 6.0, plot_right - current_label_width)
    current_label_y = min(max(y_current - current_label_height - 6.0, top + 4.0), plot_bottom - current_label_height - 4.0)
    close_tooltip = (
        f"收盘价折线 | 起点 {first_price['date']} 收 {fmt_num(first_price['close'])} | "
        f"终点 {last_price['date']} 收 {fmt_num(last_price['close'])}"
    )

    positioned_valuations = []
    for point in valuation_points:
        price_index = _price_index_on_or_after(price_dates, point["date"])
        x = _chart_x(price_index, price_count, left, plot_width)
        positioned_valuations.append(
            {
                **point,
                "price_index": price_index,
                "x": x,
                "y_low": _chart_y(float(point["low"]), y_min, y_max, top, plot_height),
                "y_mid": _chart_y(float(point["mid"]), y_min, y_max, top, plot_height),
                "y_high": _chart_y(float(point["high"]), y_min, y_max, top, plot_height),
            }
        )

    bands = []
    boundary_lines = []
    mid_lines = []
    markers = []
    for index, item in enumerate(positioned_valuations):
        start_x = float(item["x"])
        if index + 1 < len(positioned_valuations):
            end_x = float(positioned_valuations[index + 1]["x"])
            if end_x <= start_x:
                end_x = min(plot_right, start_x + spacing)
        else:
            end_x = plot_right
        width_value = max(end_x - start_x, 2.0)
        band_y = float(item["y_high"])
        band_height = max(float(item["y_low"]) - band_y, 1.0)
        tooltip = (
            f"{item['date']} 起 | 低位 {fmt_num(item['low'])} | 中枢 {fmt_num(item['mid'])} | "
            f"乐观 {fmt_num(item['high'])} | {item['method']} | {item['grade']}"
        )
        bands.append(
            f"""<rect class="valuation-step-band" x="{start_x:.1f}" y="{band_y:.1f}" width="{width_value:.1f}" height="{band_height:.1f}">
          <title>{esc(tooltip)}</title>
        </rect>"""
        )
        boundary_lines.append(
            f"""<line class="valuation-step-boundary-line" x1="{start_x:.1f}" y1="{item['y_high']:.1f}" x2="{end_x:.1f}" y2="{item['y_high']:.1f}"></line>
          <line class="valuation-step-boundary-line" x1="{start_x:.1f}" y1="{item['y_low']:.1f}" x2="{end_x:.1f}" y2="{item['y_low']:.1f}"></line>"""
        )
        mid_lines.append(
            f"""<line class="valuation-mid-line" x1="{start_x:.1f}" y1="{item['y_mid']:.1f}" x2="{end_x:.1f}" y2="{item['y_mid']:.1f}"></line>"""
        )
        markers.append(
            f"""<g class="valuation-point">
          <title>{esc(tooltip)}</title>
          <line class="valuation-whisker" x1="{start_x:.1f}" y1="{item['y_high']:.1f}" x2="{start_x:.1f}" y2="{item['y_low']:.1f}"></line>
          <circle class="valuation-mid-dot" cx="{start_x:.1f}" cy="{item['y_mid']:.1f}" r="4.5"></circle>
        </g>"""
        )

    latest_reference_lines = _latest_reference_level_lines(
        positioned_valuations[-1],
        y_min=y_min,
        y_max=y_max,
        top=top,
        plot_height=plot_height,
        plot_bottom=plot_bottom,
        left=left,
        plot_right=plot_right,
    )

    first_date = price_points[0]["date"]
    last_date = price_points[-1]["date"]
    return f"""<section class="section-block">
      <h2>ETF参考价格区间历史</h2>
      <div class="valuation-chart">
        <svg class="valuation-history-svg" viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="2024-09-24以来收盘价折线叠加ETF参考价格区间图">
          <title>2024-09-24以来收盘价折线叠加ETF参考价格区间图</title>
          <line class="valuation-axis-line" x1="{left:.1f}" y1="{top:.1f}" x2="{left:.1f}" y2="{plot_bottom:.1f}"></line>
          <line class="valuation-axis-line" x1="{left:.1f}" y1="{plot_bottom:.1f}" x2="{plot_right:.1f}" y2="{plot_bottom:.1f}"></line>
          <text class="valuation-axis-title" x="{left:.1f}" y="16" text-anchor="start">价格 CNY/fund_share</text>
          <text class="valuation-range-label" x="{plot_right:.1f}" y="16" text-anchor="end">{esc(short_date(first_date))} - {esc(short_date(last_date))}</text>
          {''.join(tick_lines)}
          <g class="close-price-layer">
            <title>{esc(close_tooltip)}</title>
            <polyline class="close-price-line" points="{' '.join(close_line_points)}"></polyline>
          </g>
          <g class="valuation-overlay-layer">
            {''.join(bands)}
            {''.join(boundary_lines)}
            {''.join(mid_lines)}
            {''.join(markers)}
          </g>
          <g class="reference-level-layer">
            {latest_reference_lines}
          </g>
          <g class="current-price-layer">
            <title>当前价格 {fmt_num(current_price)}，截至 {esc(last_price['date'])}</title>
            <line class="current-price-line" x1="{left:.1f}" y1="{y_current:.1f}" x2="{plot_right:.1f}" y2="{y_current:.1f}"></line>
            <rect class="current-price-label-bg" x="{current_label_x:.1f}" y="{current_label_y:.1f}" width="{current_label_width:.1f}" height="{current_label_height:.1f}" rx="4"></rect>
            <text class="current-price-label" x="{current_label_x + current_label_width - 8:.1f}" y="{current_label_y + 14:.1f}" text-anchor="end">{esc(current_label)}</text>
          </g>
          {''.join(x_labels)}
        </svg>
        <div class="valuation-legend">
          <span><i class="legend-close-line"></i>2024-09-24以来收盘价折线</span>
          <span><i class="legend-current-line"></i>当前价格</span>
          <span><i class="legend-reference-level"></i>最新低/中枢/高</span>
          <span><i class="legend-band"></i>保守-乐观区间</span>
          <span><i class="legend-line"></i>参考价格中枢</span>
          <span><i class="legend-dot"></i>完整深研点</span>
        </div>
      </div>
    </section>"""


def render_valuation_chart(runs: list[object], prices: list[object] | None = None) -> str:
    points = _valuation_chart_points(runs)
    if not points:
        return render_empty_section("ETF参考价格区间历史")
    price_points = _daily_price_points(prices or [])
    if price_points:
        return _render_close_price_valuation_chart(points, price_points)
    return _render_plain_valuation_chart(points)


def _reference_formula_text(method: object, model_type: object) -> str:
    method_text = str(method or "")
    model_text = str(model_type or "")
    if method_text == "theme-strength+valuation-tolerance" or model_text == "mainline_theme":
        return (
            "主线ETF：中枢 = 基准价格 * (1 + 主线强度调整 + 估值容错调整 - 拥挤度调整 - 折溢价调整)，"
            "低位/高位使用中枢上下12%的情景带宽。"
        )
    if method_text == "factor-premium+style-opportunity-cost" or model_text == "factor_defensive":
        return (
            "自由现金流、红利低波等防御因子ETF：中枢 = 基准价格 * "
            "(1 + 因子溢价调整 - 风格机会成本调整 - 折溢价调整)，低位/高位使用中枢上下7%的情景带宽。"
        )
    if method_text == "cash-like-liquidity-monitor" or model_text == "cash_like":
        return "短融、日利等现金替代ETF：中枢主要锚定基金净值并扣除折溢价影响，低位/高位使用上下1%的监控带宽。"
    if method_text == "NAV+index-valuation":
        return "基础ETF估值：中枢 = 基准价格 * (1 + 估值分位调整 - 折溢价调整)，低位/高位使用中枢上下8%的情景带宽。"
    return (
        "宽基ETF：中枢 = 基准价格 * "
        "(1 + 估值分位调整 + 股权风险溢价调整 + ROE调整 - 市场位置过热调整 - 折溢价调整)，"
        "低位/高位使用中枢上下8%的情景带宽。"
    )


def render_reference_price_explanation(latest: object | None, valuation_signal: dict[str, object]) -> str:
    valuation_range = valuation_signal.get("valuation_range")
    if not latest or not isinstance(valuation_range, dict) or valuation_range.get("mid") is None:
        return """<section class="section-block reference-price-explanation">
        <h2>参考价格口径</h2>
        <p class="muted">等待ETF完整深研入库后，将显示参考价格低位、中枢和高位的计算依据。</p>
      </section>"""

    low = _num(valuation_range.get("low"))
    mid = _num(valuation_range.get("mid"))
    high = _num(valuation_range.get("high"))
    method = valuation_range.get("method") or _row_value(latest, "valuation_method") or "待入库"
    model_type = valuation_signal.get("valuation_model_type")
    nav = _num(valuation_signal.get("nav"))
    current_price = _num(valuation_signal.get("current_price"))
    basis = nav if nav and nav > 0 else current_price
    basis_label = "单位净值 NAV" if nav and nav > 0 else "当前价格"
    adjustment = (mid / basis - 1.0) if mid is not None and basis and basis > 0 else None
    band_width = (high / mid - 1.0) if high is not None and mid and mid > 0 else None
    formula_text = _reference_formula_text(method, model_type)
    basis_text = f"{basis_label} {fmt_num(basis, 4)}" if basis is not None else "基准价格待入库"
    calculation_text = (
        f"本页采用 {method}；{basis_text}；综合调整 {fmt_ratio_percent(adjustment, signed=True)}；"
        f"带宽约 +/-{fmt_ratio_percent(band_width)}。"
    )
    if basis is not None and adjustment is not None and band_width is not None and low is not None and mid is not None and high is not None:
        adjustment_sign = "-" if adjustment < 0 else "+"
        expanded_text = (
            f"中枢 {fmt_calc_num(mid)} = {fmt_calc_num(basis)} * (1 {adjustment_sign} {fmt_ratio_percent(abs(adjustment))})；"
            f"低位 {fmt_calc_num(low)} = {fmt_calc_num(mid)} * (1 - {fmt_ratio_percent(band_width)})；"
            f"高位 {fmt_calc_num(high)} = {fmt_calc_num(mid)} * (1 + {fmt_ratio_percent(band_width)})。"
        )
    else:
        expanded_text = "等待基准价格、综合调整或带宽完整入库后展开计算。"
    return f"""<section class="section-block reference-price-explanation">
        <h2>参考价格口径</h2>
        <p>这里的低位、中枢、高位是ETF深研模型给出的参考价格区间，用来判断估值位置和仓位适配，不是交易指令。</p>
        <div class="reference-formula-grid">
          <div>
            <span>参考低位</span>
            <strong>{fmt_num(low)}</strong>
            <p>中枢价格乘以 (1 - 带宽)，表示估值更有安全垫时的观察下沿。</p>
          </div>
          <div>
            <span>参考中枢</span>
            <strong>{fmt_num(mid)}</strong>
            <p>以净值或当前价为基准，叠加当前ETF类型对应的估值、溢价和风险因子调整。</p>
          </div>
          <div>
            <span>参考高位</span>
            <strong>{fmt_num(high)}</strong>
            <p>中枢价格乘以 (1 + 带宽)，表示估值容忍度上沿，不代表应该追高。</p>
          </div>
        </div>
        <p class="formula-note"><strong>方法公式：</strong>{esc(formula_text)}</p>
        <p class="formula-note"><strong>本页计算：</strong>{esc(calculation_text)}</p>
        <p class="formula-note"><strong>本页数字：</strong>{esc(expanded_text)}</p>
      </section>"""


def render_etf_queue_status(queue: list[object]) -> str:
    if not queue:
        return ""
    rows = "".join(
        f"""<tr>
      <td>{esc(row['task_type'])}</td>
      <td>{esc(queue_source_label(row['source_type']))}</td>
      <td>{esc(row['status'])}</td>
      <td>{esc(row['task_keyword'])}</td>
      <td>{esc(row['updated_at'])}</td>
    </tr>"""
        for row in queue
    )
    return f"""<section class="section-block">
        <h2>研究队列状态</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>类型</th><th>来源</th><th>状态</th><th>任务关键词</th><th>更新时间</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </section>"""


def signal_item(label: str, value: object, detail: object | None = None) -> str:
    detail_html = f"<small>{esc(detail)}</small>" if detail is not None else ""
    return f"""<div class="signal-item">
      <span>{esc(label)}</span>
      <strong>{esc(value if value is not None else '待入库')}</strong>
      {detail_html}
    </div>"""


def render_signal_matrix(
    upstream_signal: dict[str, object],
    valuation_signal: dict[str, object],
    matrix: dict[str, object],
    *,
    market_signal: dict[str, object] | None = None,
    theme_signal: dict[str, object] | None = None,
    product_signal: dict[str, object] | None = None,
) -> str:
    matrix_market = matrix.get("market_signal") if isinstance(matrix.get("market_signal"), dict) else None
    matrix_theme = matrix.get("theme_signal") if isinstance(matrix.get("theme_signal"), dict) else None
    matrix_product = matrix.get("product_signal") if isinstance(matrix.get("product_signal"), dict) else None
    market_signal = market_signal or matrix_market or market_signal_summary()
    theme_signal = theme_signal or matrix_theme or upstream_signal
    product_signal = product_signal or matrix_product or product_signal_summary(valuation_signal)
    theme_risk_flags = theme_signal.get("risk_flags")
    if not isinstance(theme_risk_flags, list):
        theme_risk_flags = []
    risk_text = "；".join(str(item) for item in theme_risk_flags) or "暂无主题风险提示"
    theme_applicability = "适用" if theme_signal.get("applies") is not False else "不适用"
    valuation_range = valuation_signal.get("valuation_range")
    range_text = "等待ETF估值"
    if isinstance(valuation_range, dict) and valuation_range.get("mid") is not None:
        range_text = (
            f"{fmt_num(valuation_range.get('low'))} / {fmt_num(valuation_range.get('mid'))} / "
            f"{fmt_num(valuation_range.get('high'))}"
        )
    model_type = str(valuation_signal.get("valuation_model_type") or "")
    defensive_guidance = market_signal.get("defensive_factor_guidance")
    defensive_guidance = defensive_guidance if isinstance(defensive_guidance, dict) else defensive_factor_guidance(market_signal.get("regime"))
    defensive_guidance_html = ""
    if model_type == "mainline_theme":
        model_specific_items = (
            signal_item("主线有效性", fmt_num(valuation_signal.get("mainline_validity_score")))
            + signal_item("估值容错", fmt_num(valuation_signal.get("valuation_tolerance_score")))
            + signal_item("拥挤风险", fmt_num(valuation_signal.get("crowding_risk_score")))
        )
    elif model_type == "factor_defensive":
        model_specific_items = (
            signal_item("防御因子溢价", fmt_num(valuation_signal.get("factor_premium_score")))
            + signal_item("深回撤机会", fmt_num(valuation_signal.get("drawdown_opportunity_score")), valuation_signal.get("drawdown_opportunity_label"))
            + signal_item("防御因子仓带", defensive_guidance.get("band"), defensive_guidance.get("stance"))
        )
        defensive_guidance_html = (
            f"""<p class="signal-note">防御因子仓位口径：{esc(defensive_guidance.get("mapping"))}。</p>
            <p class="signal-note">{esc(defensive_guidance.get("explanation"))}</p>"""
        )
    elif model_type == "cash_like":
        model_specific_items = signal_item("现金替代安全", fmt_num(valuation_signal.get("cash_like_safety_score")))
    else:
        model_specific_items = signal_item("宽基估值安全", fmt_num(valuation_signal.get("undervalued_score")))
    return f"""<section class="section-block">
        <h2>市场 / 主题 / 产品信号矩阵</h2>
        <div class="signal-matrix">
          <div class="signal-panel signal-panel-market">
            <h3>市场仓位信号</h3>
            <p class="muted">来自 market 研究/本地市场状态层，用于判断总权益风险暴露，不等同于行业主线。</p>
            <div class="signal-grid">
              {signal_item("市场状态", REGIME_LABELS.get(str(market_signal.get("regime") or ""), market_signal.get("regime")))}
              {signal_item("仓位信号", market_signal.get("label"))}
              {signal_item("建议仓位口径", market_signal.get("suggested_position"))}
              {signal_item("置信度", fmt_ratio_percent(market_signal.get("confidence")))}
              {signal_item("宽度", fmt_num(market_signal.get("breadth_score")))}
              {signal_item("流动性结构", fmt_num(market_signal.get("liquidity_score")))}
            </div>
            <p class="signal-note">{esc(market_signal.get("explanation") or "等待market研究入库。")}</p>
          </div>
          <div class="signal-panel signal-panel-theme">
            <h3>主题主线信号</h3>
            <p class="muted">只对主线/行业/主题 ETF 生效；宽基、自由现金流、红利低波等策略型 ETF 不用等待行业主线确认。</p>
            <div class="signal-grid">
              {signal_item("适用性", theme_applicability)}
              {signal_item("所属主题", theme_signal.get("theme"))}
              {signal_item("主线状态", theme_signal.get("label"), theme_signal.get("rating"))}
              {signal_item("主题绑定", fmt_num(theme_signal.get("theme_binding")))}
              {signal_item("主线强度", fmt_num(theme_signal.get("leader_score")))}
              {signal_item("证据质量", fmt_num(theme_signal.get("evidence_quality")))}
              {signal_item("交易结构", fmt_num(theme_signal.get("trading_structure")))}
            </div>
            <p class="signal-note">主题说明：{esc(theme_signal.get("explanation") or theme_signal.get("leader_claim") or "待入库")}</p>
            <p class="signal-note">主题风险：{esc(risk_text)}</p>
          </div>
          <div class="signal-panel signal-panel-valuation">
            <h3>产品估值与仓位适配</h3>
            <p class="muted">来自 MyInvestETF 确定性评分；不同 ETF 类型使用不同估值依据。</p>
            <div class="signal-grid">
              {signal_item("估值框架", valuation_signal.get("valuation_model_label"))}
              {signal_item("五仓角色", valuation_signal.get("sleeve_label"))}
              {signal_item("产品状态", product_signal.get("label"))}
              {signal_item("参考价格区间", range_text, valuation_signal.get("source"))}
              {signal_item("估值分位", fmt_percentile(valuation_signal.get("valuation_percentile")))}
              {model_specific_items}
              {signal_item("流动性", fmt_num(valuation_signal.get("liquidity_score")))}
              {signal_item("跟踪质量", fmt_num(valuation_signal.get("tracking_score")))}
              {signal_item("仓位角色", fmt_num(valuation_signal.get("portfolio_role_score")))}
              {signal_item("风险调整", fmt_num(valuation_signal.get("risk_adjusted_score")))}
            </div>
            <p class="signal-note">ETF模型原始标签：{esc(valuation_signal.get("raw_grade") or "待入库")}</p>
            {defensive_guidance_html}
          </div>
          <div class="matrix-conclusion">
            <span>矩阵结论</span>
            <strong>{esc(matrix.get("posture"))}</strong>
            <p>{esc(matrix.get("conclusion"))}</p>
          </div>
        </div>
      </section>"""


def valuation_liquidity_text(latest: object | None, valuation_signal: dict[str, object]) -> str:
    if not latest:
        return "等待ETF完整深研入库。"
    valuation_range = valuation_signal.get("valuation_range")
    method = None
    if isinstance(valuation_range, dict):
        method = valuation_range.get("method")
    method = method or _row_value(latest, "valuation_method") or "待入库"
    return (
        f"方法 {method}; "
        f"估值分位 {fmt_percentile(valuation_signal.get('valuation_percentile'))}; "
        f"折溢价 {fmt_num(valuation_signal.get('premium_discount'), 4)}; "
        f"流动性分 {fmt_num(valuation_signal.get('liquidity_score'))}; "
        f"跟踪分 {fmt_num(valuation_signal.get('tracking_score'))}"
    )


def render_trackable_history(rows: list[object]) -> str:
    if not rows:
        return """<section class="section-block">
        <h2>可跟踪ETF历史</h2>
        <p class="empty">尚未在本地记录中被列为 可跟踪ETF。</p>
      </section>"""
    body = "".join(
        f"""<tr>
      <td>{esc(row['basis_date'] or short_date(row['generated_at'] or row['fetched_at']))}</td>
      <td>{esc(row['deep_rating'] or '')} {esc(row['deep_label'] or '')}</td>
      <td>{fmt_num(row['deep_score'])}</td>
      <td>{esc(row['theme'] or '待入库')}</td>
      <td>{esc(row['candidate_leader_claim'] or '待入库')}</td>
      <td>{esc(row['report_id'])}</td>
    </tr>"""
        for row in rows
    )
    return f"""<section class="section-block">
        <h2>可跟踪ETF历史</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>日期</th><th>评级</th><th>深研分</th><th>主题</th><th>龙头证据</th><th>report_id</th></tr></thead>
            <tbody>{body}</tbody>
          </table>
        </div>
      </section>"""


def _first_queue_name(queue: list[object], code: str) -> str:
    if queue:
        return str(queue[0]["name"] or code)
    return code


def _etf_exists(conn: object, code: str) -> tuple[bool, str | None]:
    leader = get_latest_leader(conn, code) or get_known_leader(conn, code)
    if leader is not None:
        return True, str(leader["name"])
    runs = list_research_runs(conn, code)
    if runs:
        return True, str(runs[0]["name"])
    queue = list_queue_for_etf(conn, code)
    if queue:
        return True, str(queue[0]["name"])
    return False, None


def normalize_etf_query(params: dict[str, list[str]]) -> tuple[str | None, str | None]:
    etf = (params.get("etf") or params.get("code") or [""])[0].strip().upper()
    name = (params.get("name") or [""])[0].strip()
    return (etf or None), (name or None)


def render_etf_page(code: str) -> bytes:
    if not ETF_CODE_RE.match(code):
        return render_layout("无效代码", "<section class=\"content\"><h1>无效ETF代码</h1></section>")
    with closing(connect(DB_PATH)) as conn:
        leader = get_latest_leader(conn, code) or get_known_leader(conn, code)
        runs = list_research_runs(conn, code)
        etf_queue = list_queue_for_etf(conn, code)
        trackable_history = list_trackable_history(conn, code)
        chart_runs = valuation_runs(conn, code)
        price_start = _valuation_price_start(chart_runs)
        chart_prices = list_daily_prices(conn, code, start_date=price_start) if price_start else []
        context_prices = list_daily_prices(conn, code, start_date=BULL_MARKET_START_DATE)
        market_structure_obj, market_structure, _universe_prices, _taxonomy_by_code = market_structure_from_connection(conn)
        report = latest_report(conn)
    if leader is None and not runs and not etf_queue:
        return render_layout(
            "未找到",
            f"""<section class="content">
        <div class="section-block">
          <h1>未找到 {esc(code)}</h1>
          <p class="muted">可以通过 <a class="text-link" href="/research?etf={esc(code)}">加入ETF深研队列</a> 生成研究页面。</p>
        </div>
      </section>""",
        )

    market = load_json(leader["market_json"], {}) if leader is not None else {}
    scores = load_json(leader["scores_json"], {}) if leader is not None else {}
    risk_flags = load_json(leader["risk_flags_json"], []) if leader is not None else []
    latest = next((dict(row) for row in runs if row["task_type"] == "research"), dict(runs[0]) if runs else {})
    risks = load_json(latest.get("risks_json"), []) if latest else []
    risk_items = "".join(f"<li>{esc(item)}</li>" for item in (risks or risk_flags or []))
    etf_name = (
        str(leader["name"])
        if leader is not None
        else str((latest or {}).get("name") or _first_queue_name(etf_queue, code))
    )
    etf_theme = leader["theme"] if leader is not None else "其他请求"
    etf_claim = leader["candidate_leader_claim"] if leader is not None else "主动研究请求"
    xueqiu_url = leader["xueqiu_url"] if leader is not None else None
    model_info = leader_model_info(leader)
    upstream_signal = upstream_signal_summary(leader)
    valuation_signal = valuation_signal_summary(latest if latest else None)
    if valuation_signal.get("valuation_model_type") is None:
        valuation_signal.update(model_info)
    taxonomy_profile = taxonomy_profile_from_sources(code=code, leader=leader, latest=latest if latest else None, fallback_name=etf_name)
    etf_type = str(taxonomy_profile.get("etf_type") or "")
    price_cache_for_display = chart_prices or context_prices
    market_context = market_context_from_prices(code, context_prices)
    market_regime_v2 = market_regime_v2_to_dict(build_market_regime_v2(code, context_prices, market_structure_obj))
    valuation_signal = valuation_signal_with_drawdown_context(valuation_signal, taxonomy_profile, market_regime_v2)
    market_signal = market_signal_summary(market_regime_v2, market_context)
    product_signal = product_signal_summary(valuation_signal)
    decision_matrix = decision_matrix_summary(
        upstream_signal,
        valuation_signal,
        market_signal=market_signal,
        taxonomy_profile=taxonomy_profile,
        product_signal=product_signal,
    )
    factor_exposure = factor_exposure_from_prices(code, context_prices, taxonomy_profile)
    adaptive_decision_signal = decision_signal_from_inputs(
        code=code,
        factor_exposure=factor_exposure,
        market_regime_v2=market_regime_v2,
        taxonomy_profile=taxonomy_profile,
        valuation_signal=valuation_signal,
    )
    health_payload = research_health_payload()
    governance_report = (
        health_payload.get("health_report")
        if isinstance(health_payload.get("health_report"), dict)
        else {}
    )
    contrarian_signal = contrarian_signal_from_inputs(
        code=code,
        market_context=market_context,
        market_regime_v2=market_regime_v2,
        market_structure=market_structure,
        factor_exposure=factor_exposure,
        governance_report=governance_report,
        decision_signal=adaptive_decision_signal,
    )
    strategy_decision = strategy_decision_from_inputs(
        code=code,
        decision_signal=adaptive_decision_signal,
        contrarian_signal=contrarian_signal,
        market_regime_v2=market_regime_v2,
        governance_report=governance_report,
    )
    common_ask_answers = build_common_ask_answers(
        code=code,
        decision_signal=adaptive_decision_signal,
        taxonomy_profile=taxonomy_profile,
        market_regime=market_regime_v2,
        governance_report=governance_report,
    )
    current_price = _display_current_price(latest if latest else None, price_cache_for_display, market)
    rating_label = (
        f"{leader['deep_rating'] or ''} {leader['deep_label'] or ''}".strip()
        if leader is not None
        else (queue_source_label(etf_queue[0]["source_type"]) if etf_queue else "待研究")
    )
    report_date = report["basis_date"] if report else ""
    queue_status_section = render_etf_queue_status(etf_queue)
    ask_widget_section = render_ask_widget(code, common_ask_answers)
    signal_matrix_section = render_signal_matrix(
        upstream_signal,
        valuation_signal,
        decision_matrix,
        market_signal=market_signal,
        theme_signal=upstream_signal,
        product_signal=product_signal,
    )
    trackable_history_section = render_trackable_history(trackable_history)

    history_rows = "".join(
        f"""<tr>
      <td>{esc(row['research_date'])}</td>
      <td>{esc(row['task_type'])}</td>
      <td>{esc(row['status'])}</td>
      <td>{esc(row['valuation_method'] or '待入库')}</td>
      <td>{fmt_num(row['valuation_low'])} / {fmt_num(row['valuation_mid'])} / {fmt_num(row['valuation_high'])}</td>
      <td>{esc(portfolio_use_view(row['heavy_position_view']))}</td>
    </tr>"""
        for row in runs
    )
    if not history_rows:
        history_rows = "<tr><td colspan=\"6\" class=\"empty-cell\">等待ETF深研入库。</td></tr>"

    body = f"""
    <section class="page-band">
      <div class="content">
        <div class="page-title-row">
          <div>
            <h1>{esc(etf_name)}</h1>
            <p class="muted">{xueqiu_etf_link(code, xueqiu_url)} · {esc(etf_theme)} · {esc(etf_claim)}</p>
          </div>
          <div class="report-box">
            <span>研究来源</span>
            <strong>{esc(rating_label)}</strong>
            <span>{esc(report_date)}</span>
          </div>
        </div>
        {render_current_decision_summary(decision_matrix, valuation_signal, adaptive_decision_signal, current_price)}
        <div class="summary-grid">
          {metric("深研分", leader["deep_score"] if leader is not None else None)}
          {metric("当前价格", current_price)}
          {metric("Decision Score", adaptive_decision_signal.get("score"))}
          {metric("估值分位", fmt_percentile(valuation_signal.get("valuation_percentile")))}
          {metric("ETF分类", TAXONOMY_LABELS.get(etf_type, etf_type or "待分类"))}
          {metric("PE TTM", market.get("pe_ttm"))}
          {metric("PB", market.get("pb"))}
          {metric("估值框架", model_info.get("valuation_model_label"))}
          {metric("五仓角色", model_info.get("sleeve_label"))}
          {metric("证据质量", scores.get("evidence_quality"))}
          {metric("估值安全", scores.get("valuation_safety"))}
        </div>
      </div>
    </section>
    <section class="content">
      {signal_matrix_section}
      {render_taxonomy_profile(taxonomy_profile)}
      {render_factor_exposure(factor_exposure)}
      {render_decision_signal(adaptive_decision_signal)}
      {render_contrarian_signal(contrarian_signal)}
      {render_strategy_decision(strategy_decision)}
      {render_market_regime_v2(market_regime_v2, market_structure)}
      {render_market_context(market_context)}
      {render_valuation_chart(chart_runs, chart_prices)}
      {render_reference_price_explanation(latest if latest else None, valuation_signal)}
      {trackable_history_section}
      <section class="two-col">
        <div class="section-block">
          <h2>产品结构</h2>
          <p>{esc(latest.get('industry_position') or '等待ETF完整深研入库。')}</p>
        </div>
        <div class="section-block">
          <h2>持仓披露</h2>
          <p>{esc(latest.get('competition_landscape') or '等待ETF完整深研入库。')}</p>
        </div>
      </section>
      <section class="two-col">
        <div class="section-block">
          <h2>前十大持仓</h2>
          <p>{esc(latest.get('upstream_downstream') or '等待ETF完整深研入库。')}</p>
        </div>
        <div class="section-block">
          <h2>估值与流动性</h2>
          <p>{esc(valuation_liquidity_text(latest if latest else None, valuation_signal))}</p>
        </div>
      </section>
      <section class="two-col">
        <div class="section-block">
          <h2>组合角色</h2>
          <p>{esc(latest.get('multi_bagger_potential') or '等待ETF完整深研入库。')}</p>
        </div>
        <div class="section-block">
          <h2>组合使用判断</h2>
          <p>{esc(decision_matrix.get('conclusion') or portfolio_use_view(latest.get('heavy_position_view')) or '等待ETF完整深研入库。')}</p>
        </div>
      </section>
      <section class="section-block">
        <h2>风险与证伪</h2>
        <ul class="risk-list">{risk_items or '<li>等待ETF深研入库。</li>'}</ul>
      </section>
      {ask_widget_section}
      {queue_status_section}
      <section class="section-block">
        <h2>研究历史</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>日期</th><th>类型</th><th>状态</th><th>估值方法</th><th>参考价格低 / 中枢 / 高</th><th>组合使用判断</th></tr></thead>
            <tbody>{history_rows}</tbody>
          </table>
        </div>
      </section>
    </section>
"""
    return render_layout(f"{etf_name} {code}", body)


def api_etfs() -> bytes:
    with closing(connect(DB_PATH)) as conn:
        report = latest_report(conn)
        leaders = list_latest_leaders(conn)
    return json.dumps(
        {"report": dict(report) if report else None, "items": [leader_to_summary(row) for row in leaders]},
        ensure_ascii=False,
    ).encode("utf-8")


def api_index() -> bytes:
    with closing(connect(DB_PATH)) as conn:
        report = latest_report(conn)
        leaders = list_latest_leaders(conn)
    items = [leader_to_summary(row) for row in leaders]
    payload = {
        "schema_version": "myinvestetf.index.v1",
        "page": {
            "title": "MyInvestETF",
            "primary_endpoint": "/api/index",
            "latest_endpoint": "/api/latest",
            "primary_result_path": "key_results.primary_output.items",
        },
        "source": {
            "upstream_endpoint": LEADER_INDEX_URL,
            "upstream_result_path": "result.theme_ranking[].top_etf + result.etf_top",
            "compatible_result_path": "key_results.primary_output.items",
            "source_policy": (
                "default to theme.okbbc.com/api/latest theme_ranking[].top_etf plus result.etf_top; "
                "/api/index keeps the ETF pool and appends local core broad-index ETF seeds; "
                "research queue lists independent core broad-index representatives first, then one representative per mainline theme"
            ),
        },
        "report": dict(report) if report else None,
        "key_results": {
            "primary_output": {
                "title": "可跟踪ETF",
                "count": len(items),
                "items": items,
            }
        },
        "links": {
            "web": "/",
            "latest": "/api/latest",
            "queue": "/api/queue",
            "etfs": "/api/etfs",
        },
        "constraints": {
            "read_only": True,
            "research_only": True,
            "contains_trade_orders": False,
            "contains_cash_amounts": False,
            "contains_share_counts": False,
        },
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def api_latest() -> bytes:
    with closing(connect(DB_PATH)) as conn:
        report = latest_report(conn)
        leaders = list_latest_leaders(conn)
        market_structure_obj, _market_structure, _universe_prices, _taxonomy_by_code = market_structure_from_connection(conn)
        etfs = []
        research_run_count = 0
        complete_research_count = 0
        for leader in leaders:
            runs = list_research_runs(conn, leader["code"])
            reference_runs_for_etf = valuation_runs(conn, leader["code"])
            context_prices = list_daily_prices(conn, leader["code"], start_date=BULL_MARKET_START_DATE)
            market_context = market_context_from_prices(leader["code"], context_prices)
            regime_v2 = market_regime_v2_to_dict(build_market_regime_v2(str(leader["code"]), context_prices, market_structure_obj))
            research_run_count += len(runs)
            complete_research_count += len(reference_runs_for_etf)
            latest = latest_research_run(runs)
            leader_summary = leader_to_summary(leader)
            taxonomy_profile = taxonomy_profile_from_sources(code=str(leader["code"]), leader=leader, latest=runs[0] if runs else None)
            latest_valuation_signal = latest["valuation_signal"] if latest else valuation_signal_summary(None)
            latest_valuation_signal = valuation_signal_with_drawdown_context(
                latest_valuation_signal,
                taxonomy_profile,
                regime_v2,
            )
            latest_market_signal = market_signal_summary(regime_v2, market_context)
            latest_product_signal = product_signal_summary(latest_valuation_signal)
            decision_matrix = decision_matrix_summary(
                leader_summary["upstream_signal"],
                latest_valuation_signal,
                market_signal=latest_market_signal,
                taxonomy_profile=taxonomy_profile,
                product_signal=latest_product_signal,
            )
            etfs.append(
                {
                    "leader": leader_summary,
                    "research": {
                        "latest": latest,
                        "reference_value_history": valuation_history_payload(reference_runs_for_etf),
                        "run_count": len(runs),
                    },
                    "taxonomy_profile": taxonomy_profile,
                    "market_context": market_context,
                    "regime_v2": regime_v2,
                    "market_signal": latest_market_signal,
                    "theme_signal": leader_summary["upstream_signal"],
                    "product_signal": latest_product_signal,
                    "decision_matrix": decision_matrix,
                }
            )
    payload = {
        "schema_version": "myinvestetf.research.v2",
        "report": dict(report) if report else None,
        "summary": {
            "etf_count": len(etfs),
            "research_run_count": research_run_count,
            "complete_research_count": complete_research_count,
        },
        "etfs": etfs,
        "constraints": {
            "read_only": True,
            "research_only": True,
            "contains_trade_orders": False,
            "contains_cash_amounts": False,
            "contains_share_counts": False,
        },
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def api_etf(code: str) -> bytes:
    with closing(connect(DB_PATH)) as conn:
        leader = get_latest_leader(conn, code) or get_known_leader(conn, code)
        runs = rows_to_dicts(list_research_runs(conn, code))
        queue = rows_to_dicts(list_queue_for_etf(conn, code))
        trackable = rows_to_dicts(list_trackable_history(conn, code))
        context_prices = list_daily_prices(conn, code, start_date=BULL_MARKET_START_DATE)
        market_structure_obj, _market_structure, _universe_prices, _taxonomy_by_code = market_structure_from_connection(conn)
    for row in queue:
        row["source_label"] = queue_source_label(row.get("source_type"))
    leader_summary = leader_to_summary(leader) if leader else None
    latest = next((row for row in runs if row.get("task_type") == "research"), runs[0] if runs else None)
    taxonomy_profile = taxonomy_profile_from_sources(code=code, leader=leader, latest=latest)
    market_context = market_context_from_prices(code, context_prices)
    regime_v2 = market_regime_v2_to_dict(build_market_regime_v2(code, context_prices, market_structure_obj))
    current_valuation_signal = valuation_signal_with_drawdown_context(
        valuation_signal_summary(latest) if latest else valuation_signal_summary(None),
        taxonomy_profile,
        regime_v2,
    )
    current_market_signal = market_signal_summary(regime_v2, market_context)
    current_product_signal = product_signal_summary(current_valuation_signal)
    decision_matrix = decision_matrix_summary(
        leader_summary["upstream_signal"] if leader_summary else upstream_signal_summary(None),
        current_valuation_signal,
        market_signal=current_market_signal,
        taxonomy_profile=taxonomy_profile,
        product_signal=current_product_signal,
    )
    return json.dumps(
        {
            "leader": dict(leader) if leader else None,
            "leader_summary": leader_summary,
            "upstream_signal": leader_summary["upstream_signal"] if leader_summary else upstream_signal_summary(None),
            "market_signal": current_market_signal,
            "theme_signal": leader_summary["upstream_signal"] if leader_summary else upstream_signal_summary(None),
            "product_signal": current_product_signal,
            "research_runs": runs,
            "taxonomy_profile": taxonomy_profile,
            "market_context": market_context,
            "regime_v2": regime_v2,
            "valuation_signal": current_valuation_signal,
            "decision_matrix": decision_matrix,
            "queue": queue,
            "trackable_history": trackable,
        },
        ensure_ascii=False,
    ).encode("utf-8")


def api_etf_profile(code: str) -> bytes:
    with closing(connect(DB_PATH)) as conn:
        leader = get_latest_leader(conn, code) or get_known_leader(conn, code)
        runs = rows_to_dicts(list_research_runs(conn, code))
        queue = rows_to_dicts(list_queue_for_etf(conn, code))
    latest = next((row for row in runs if row.get("task_type") == "research"), runs[0] if runs else None)
    fallback_name = _first_queue_name(queue, code) if queue else code
    profile = taxonomy_profile_from_sources(code=code, leader=leader, latest=latest, fallback_name=fallback_name)
    payload = {
        "schema_version": "myinvestetf.etf_profile.v1",
        "code": code,
        "name": _row_value(leader, "name") or (latest or {}).get("name") or fallback_name,
        "taxonomy_profile": profile,
        "type": profile.get("etf_type"),
        "subtype": profile.get("subtype"),
        "lifecycle": profile.get("lifecycle_stage"),
        "confidence": profile.get("classification_confidence"),
        "classification_reasons": profile.get("classification_reasons"),
        "legacy_valuation_model_type": profile.get("legacy_valuation_model_type"),
        "legacy_sleeve_key": profile.get("legacy_sleeve_key"),
        "constraints": {
            "read_only": True,
            "research_only": True,
            "contains_trade_orders": False,
            "contains_cash_amounts": False,
            "contains_share_counts": False,
        },
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def api_factors_for_etf(code: str) -> bytes:
    with closing(connect(DB_PATH)) as conn:
        leader = get_latest_leader(conn, code) or get_known_leader(conn, code)
        runs = rows_to_dicts(list_research_runs(conn, code))
        queue = rows_to_dicts(list_queue_for_etf(conn, code))
        prices = list_daily_prices(conn, code, start_date=BULL_MARKET_START_DATE)
    latest = next((row for row in runs if row.get("task_type") == "research"), runs[0] if runs else None)
    fallback_name = _first_queue_name(queue, code) if queue else code
    taxonomy_profile = taxonomy_profile_from_sources(code=code, leader=leader, latest=latest, fallback_name=fallback_name)
    exposure = factor_exposure_from_prices(code, prices, taxonomy_profile)
    payload = {
        "schema_version": "myinvestetf.factor_exposure.v1",
        "code": code,
        "taxonomy_profile": taxonomy_profile,
        "factor_exposure": exposure,
        "constraints": {
            "read_only": True,
            "research_only": True,
            "point_in_time": True,
            "contains_trade_orders": False,
            "contains_cash_amounts": False,
            "contains_share_counts": False,
        },
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def api_factor_ic(factor_name: str) -> bytes:
    definition = get_factor_definition(factor_name)
    if definition is None:
        return json.dumps(
            {
                "schema_version": "myinvestetf.factor_ic.v1",
                "factor": factor_name,
                "error": "unknown_factor",
                "constraints": {"read_only": True},
            },
            ensure_ascii=False,
        ).encode("utf-8")
    with closing(connect(DB_PATH)) as conn:
        leaders = list_latest_leaders(conn)
        price_series_by_code = {
            str(row["code"]): list_daily_prices(conn, str(row["code"]), start_date=BULL_MARKET_START_DATE)
            for row in leaders
        }
    summaries = [factor_ic_summary_to_dict(item) for item in compute_factor_ic(definition, price_series_by_code)]
    payload = {
        "schema_version": "myinvestetf.factor_ic.v1",
        "factor": factor_definition_to_dict(definition),
        "summaries": summaries,
        "constraints": {
            "read_only": True,
            "research_only": True,
            "point_in_time": True,
            "contains_trade_orders": False,
            "contains_cash_amounts": False,
            "contains_share_counts": False,
        },
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def api_market_structure() -> bytes:
    with closing(connect(DB_PATH)) as conn:
        _structure_obj, structure, _prices, _taxonomy = market_structure_from_connection(conn)
    return json.dumps(
        {
            "schema_version": "myinvestetf.market_structure.v1",
            "market_structure": structure,
            "constraints": {
                "read_only": True,
                "research_only": True,
                "contains_trade_orders": False,
                "contains_cash_amounts": False,
                "contains_share_counts": False,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")


def api_market_breadth() -> bytes:
    with closing(connect(DB_PATH)) as conn:
        _structure_obj, structure, _prices, _taxonomy = market_structure_from_connection(conn)
    payload = {
        "schema_version": "myinvestetf.market_breadth.v1",
        "as_of_date": structure.get("as_of_date"),
        "index_breadth": structure.get("index_breadth"),
        "sector_breadth": structure.get("sector_breadth"),
        "advance_decline_ratio": structure.get("advance_decline_ratio"),
        "breadth_score": structure.get("breadth_score"),
        "breadth_contribution": structure.get("contributions", {}).get("breadth") if isinstance(structure.get("contributions"), dict) else None,
        "observations": structure.get("observations"),
        "data_gaps": structure.get("data_gaps"),
        "constraints": {"read_only": True, "research_only": True},
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def api_market_liquidity() -> bytes:
    with closing(connect(DB_PATH)) as conn:
        _structure_obj, structure, _prices, _taxonomy = market_structure_from_connection(conn)
    payload = {
        "schema_version": "myinvestetf.market_liquidity.v1",
        "as_of_date": structure.get("as_of_date"),
        "liquidity_breadth": structure.get("liquidity_breadth"),
        "liquidity_score": structure.get("liquidity_score"),
        "liquidity_contribution": structure.get("contributions", {}).get("liquidity") if isinstance(structure.get("contributions"), dict) else None,
        "observations": structure.get("observations"),
        "data_gaps": structure.get("data_gaps"),
        "constraints": {"read_only": True, "research_only": True},
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def api_market_regime_v2() -> bytes:
    with closing(connect(DB_PATH)) as conn:
        structure_obj, structure, price_series_by_code, taxonomy_by_code = market_structure_from_connection(conn)
    items = []
    for code, prices in price_series_by_code.items():
        regime = market_regime_v2_to_dict(build_market_regime_v2(code, prices, structure_obj))
        items.append(
            {
                "code": code,
                "taxonomy_profile": taxonomy_by_code.get(code),
                "regime_v2": regime,
            }
        )
    return json.dumps(
        {
            "schema_version": "myinvestetf.market_regime_v2.v1",
            "market_structure": structure,
            "items": items,
            "constraints": {
                "read_only": True,
                "research_only": True,
                "feeds_decision_signal": True,
                "contains_trade_orders": False,
                "contains_cash_amounts": False,
                "contains_share_counts": False,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")


def decision_signal_payload_for_etf(code: str) -> dict[str, object]:
    if not ETF_CODE_RE.match(code):
        return {
            "schema_version": "myinvestetf.decision_signal.v1",
            "code": code,
            "error": "invalid_etf_code",
            "constraints": {"read_only": True, "research_only": True},
        }
    with closing(connect(DB_PATH)) as conn:
        leader = get_latest_leader(conn, code) or get_known_leader(conn, code)
        runs = rows_to_dicts(list_research_runs(conn, code))
        queue = rows_to_dicts(list_queue_for_etf(conn, code))
        prices = list_daily_prices(conn, code, start_date=BULL_MARKET_START_DATE)
        structure_obj, structure, _price_series_by_code, _taxonomy_by_code = market_structure_from_connection(conn)
    latest = next((row for row in runs if row.get("task_type") == "research"), runs[0] if runs else None)
    fallback_name = _first_queue_name(queue, code) if queue else code
    taxonomy_profile = taxonomy_profile_from_sources(code=code, leader=leader, latest=latest, fallback_name=fallback_name)
    valuation_signal = valuation_signal_summary(latest) if latest else valuation_signal_summary(None)
    if valuation_signal.get("valuation_model_type") is None:
        valuation_signal.update(leader_model_info(leader))
    factor_exposure = factor_exposure_from_prices(code, prices, taxonomy_profile)
    market_regime_v2 = market_regime_v2_to_dict(build_market_regime_v2(code, prices, structure_obj))
    valuation_signal = valuation_signal_with_drawdown_context(valuation_signal, taxonomy_profile, market_regime_v2)
    decision_signal = decision_signal_from_inputs(
        code=code,
        factor_exposure=factor_exposure,
        market_regime_v2=market_regime_v2,
        taxonomy_profile=taxonomy_profile,
        valuation_signal=valuation_signal,
    )
    return {
        "schema_version": "myinvestetf.decision_signal.v1",
        "code": code,
        "name": _row_value(leader, "name") or (latest or {}).get("name") or fallback_name,
        "taxonomy_profile": taxonomy_profile,
        "market_structure": structure,
        "regime_v2": market_regime_v2,
        "factor_exposure": factor_exposure,
        "valuation_signal": valuation_signal,
        "decision_signal": decision_signal,
        "constraints": {
            "read_only": True,
            "research_only": True,
            "contains_trade_orders": False,
            "contains_cash_amounts": False,
            "contains_share_counts": False,
            "executes_rebalance": False,
        },
    }


def contrarian_signal_payload_for_etf(code: str) -> dict[str, object]:
    if not ETF_CODE_RE.match(code):
        return {
            "schema_version": "myinvestetf.contrarian_signal.v1",
            "code": code,
            "error": "invalid_etf_code",
            "constraints": {"read_only": True, "research_only": True},
        }
    decision_payload = decision_signal_payload_for_etf(code)
    if decision_payload.get("error"):
        return {
            "schema_version": "myinvestetf.contrarian_signal.v1",
            "code": code,
            "error": decision_payload.get("error"),
            "constraints": decision_payload.get("constraints", {"read_only": True, "research_only": True}),
        }
    with closing(connect(DB_PATH)) as conn:
        prices = list_daily_prices(conn, code, start_date=BULL_MARKET_START_DATE)
    market_context = market_context_from_prices(code, prices)
    health_payload = research_health_payload()
    governance_report = health_payload.get("health_report") if isinstance(health_payload.get("health_report"), dict) else {}
    contrarian_signal = contrarian_signal_from_inputs(
        code=code,
        market_context=market_context,
        market_regime_v2=decision_payload.get("regime_v2") if isinstance(decision_payload.get("regime_v2"), dict) else {},
        market_structure=decision_payload.get("market_structure") if isinstance(decision_payload.get("market_structure"), dict) else {},
        factor_exposure=decision_payload.get("factor_exposure") if isinstance(decision_payload.get("factor_exposure"), dict) else {},
        governance_report=governance_report,
        decision_signal=decision_payload.get("decision_signal") if isinstance(decision_payload.get("decision_signal"), dict) else {},
    )
    return {
        "schema_version": "myinvestetf.contrarian_signal.v1",
        "code": code,
        "name": decision_payload.get("name"),
        "market_context": market_context,
        "regime_v2": decision_payload.get("regime_v2"),
        "decision_signal": decision_payload.get("decision_signal"),
        "contrarian_signal": contrarian_signal,
        "constraints": {
            "read_only": True,
            "research_only": True,
            "does_not_override_decision_score": True,
            "contains_trade_orders": False,
            "contains_cash_amounts": False,
            "contains_share_counts": False,
            "executes_rebalance": False,
        },
    }


def strategy_route_payload_for_etf(code: str) -> dict[str, object]:
    if not ETF_CODE_RE.match(code):
        return {
            "schema_version": "myinvestetf.strategy_route.v1",
            "code": code,
            "error": "invalid_etf_code",
            "constraints": {"read_only": True, "research_only": True},
        }
    contrarian_payload = contrarian_signal_payload_for_etf(code)
    if contrarian_payload.get("error"):
        return {
            "schema_version": "myinvestetf.strategy_route.v1",
            "code": code,
            "error": contrarian_payload.get("error"),
            "constraints": contrarian_payload.get("constraints", {"read_only": True, "research_only": True}),
        }
    health_payload = research_health_payload()
    governance_report = health_payload.get("health_report") if isinstance(health_payload.get("health_report"), dict) else {}
    strategy_decision = strategy_decision_from_inputs(
        code=code,
        decision_signal=contrarian_payload.get("decision_signal") if isinstance(contrarian_payload.get("decision_signal"), dict) else {},
        contrarian_signal=contrarian_payload.get("contrarian_signal") if isinstance(contrarian_payload.get("contrarian_signal"), dict) else {},
        market_regime_v2=contrarian_payload.get("regime_v2") if isinstance(contrarian_payload.get("regime_v2"), dict) else {},
        governance_report=governance_report,
    )
    return {
        "schema_version": "myinvestetf.strategy_route.v1",
        "code": code,
        "name": contrarian_payload.get("name"),
        "decision_signal": contrarian_payload.get("decision_signal"),
        "contrarian_signal": contrarian_payload.get("contrarian_signal"),
        "strategy_decision": strategy_decision,
        "constraints": {
            "read_only": True,
            "research_only": True,
            "does_not_override_decision_score": True,
            "contains_trade_orders": False,
            "contains_cash_amounts": False,
            "contains_share_counts": False,
            "executes_rebalance": False,
        },
    }


def api_score_for_etf(code: str) -> bytes:
    return json.dumps(decision_signal_payload_for_etf(code), ensure_ascii=False).encode("utf-8")


def api_contrarian_for_etf(code: str) -> bytes:
    return json.dumps(contrarian_signal_payload_for_etf(code), ensure_ascii=False).encode("utf-8")


def api_strategy_route_for_etf(code: str) -> bytes:
    return json.dumps(strategy_route_payload_for_etf(code), ensure_ascii=False).encode("utf-8")


def api_ask_for_etf(code: str, query: str) -> bytes:
    params = parse_qs(query)
    question = str((params.get("q") or [""])[0] or "")
    payload = decision_signal_payload_for_etf(code)
    if payload.get("error"):
        response = {
            "schema_version": "myinvestetf.ask.v1",
            "code": code,
            "question": question,
            "error": payload.get("error"),
            "constraints": payload.get("constraints", {"read_only": True, "research_only": True}),
        }
        return json.dumps(response, ensure_ascii=False).encode("utf-8")

    health_payload = research_health_payload()
    governance_report = (
        health_payload.get("health_report")
        if isinstance(health_payload.get("health_report"), dict)
        else {}
    )
    interpretation = DecisionInterpreter().interpret(
        code,
        question,
        decision_signal=payload.get("decision_signal"),
        taxonomy_profile=payload.get("taxonomy_profile"),
        market_regime=payload.get("regime_v2"),
        governance_report=governance_report,
    )
    response = {
        "schema_version": "myinvestetf.ask.v1",
        "code": code,
        "name": payload.get("name"),
        "question": question,
        "intent": interpretation["intent"],
        "decision": interpretation["decision"],
        "regime": interpretation["regime"],
        "taxonomy": interpretation["taxonomy"],
        "final_answer": interpretation["final_answer"],
        "risk": interpretation["risk"],
        "explanation": interpretation["explanation"],
        "constraints": {
            "read_only": True,
            "research_only": True,
            "no_trade_orders": True,
            "contains_cash_amounts": False,
            "contains_share_counts": False,
            "executes_rebalance": False,
            "final_answer_policy": "AnswerPolicyEngine",
        },
    }
    return json.dumps(response, ensure_ascii=False).encode("utf-8")


def api_score_decompose_for_etf(code: str) -> bytes:
    payload = decision_signal_payload_for_etf(code)
    signal = payload.get("decision_signal") if isinstance(payload.get("decision_signal"), dict) else {}
    decomposed = {
        "schema_version": "myinvestetf.score_decomposition.v1",
        "code": code,
        "score": signal.get("score") if isinstance(signal, dict) else None,
        "regime": (signal.get("state") or {}).get("regime") if isinstance(signal.get("state"), dict) else None,
        "component_scores": signal.get("component_scores") if isinstance(signal, dict) else {},
        "factor_contributions": signal.get("factor_contributions") if isinstance(signal, dict) else {},
        "adjusted_weights": signal.get("adjusted_weights") if isinstance(signal, dict) else {},
        "factor_effectiveness": signal.get("factor_effectiveness") if isinstance(signal, dict) else {},
        "inputs": signal.get("inputs") if isinstance(signal, dict) else {},
        "constraints": payload.get("constraints", {"read_only": True}),
    }
    if payload.get("error"):
        decomposed["error"] = payload.get("error")
    return json.dumps(decomposed, ensure_ascii=False).encode("utf-8")


def api_decision_state_for_etf(code: str) -> bytes:
    payload = decision_signal_payload_for_etf(code)
    signal = payload.get("decision_signal") if isinstance(payload.get("decision_signal"), dict) else {}
    state = signal.get("state") if isinstance(signal, dict) and isinstance(signal.get("state"), dict) else {}
    state_payload = {
        "schema_version": "myinvestetf.decision_state.v1",
        "code": code,
        "score": signal.get("score") if isinstance(signal, dict) else None,
        "confidence": signal.get("confidence") if isinstance(signal, dict) else None,
        "state": state,
        "explanation": state.get("explanation") if isinstance(state, dict) else None,
        "constraints": payload.get("constraints", {"read_only": True}),
    }
    if payload.get("error"):
        state_payload["error"] = payload.get("error")
    return json.dumps(state_payload, ensure_ascii=False).encode("utf-8")


def replay_report_payload_for_etf(code: str) -> dict[str, object]:
    if not ETF_CODE_RE.match(code):
        return {
            "schema_version": "myinvestetf.replay_report.v1",
            "code": code,
            "error": "invalid_etf_code",
            "constraints": {"read_only": True, "research_only": True},
        }
    with closing(connect(DB_PATH)) as conn:
        leader = get_latest_leader(conn, code) or get_known_leader(conn, code)
        runs = rows_to_dicts(list_research_runs(conn, code))
        queue = rows_to_dicts(list_queue_for_etf(conn, code))
        leaders = list_latest_leaders(conn)
        price_series_by_code = {
            str(row["code"]): list_daily_prices(conn, str(row["code"]), start_date=BULL_MARKET_START_DATE)
            for row in leaders
        }
        if code not in price_series_by_code:
            price_series_by_code[code] = list_daily_prices(conn, code, start_date=BULL_MARKET_START_DATE)
        taxonomy_by_code = {
            str(row["code"]): taxonomy_profile_from_sources(code=str(row["code"]), leader=row)
            for row in leaders
        }
    latest = next((row for row in runs if row.get("task_type") == "research"), runs[0] if runs else None)
    fallback_name = _first_queue_name(queue, code) if queue else code
    taxonomy_profile = taxonomy_profile_from_sources(code=code, leader=leader, latest=latest, fallback_name=fallback_name)
    taxonomy_by_code[code] = taxonomy_profile
    valuation_signal = valuation_signal_summary(latest) if latest else valuation_signal_summary(None)
    if valuation_signal.get("valuation_model_type") is None:
        valuation_signal.update(leader_model_info(leader))
    valuation_as_of_date = str((latest or {}).get("research_date") or "") or None
    report = replay_report_to_dict(
        build_replay_report(
            etf_code=code,
            price_series_by_code=price_series_by_code,
            taxonomy_by_code=taxonomy_by_code,
            valuation_signal=valuation_signal,
            valuation_as_of_date=valuation_as_of_date,
            min_observations=45,
            max_points=24,
        )
    )
    return {
        "schema_version": "myinvestetf.replay_report.v1",
        "code": code,
        "name": _row_value(leader, "name") or (latest or {}).get("name") or fallback_name,
        "replay_report": report,
        "constraints": report.get("constraints", {"read_only": True}),
    }


def api_replay_for_etf(code: str) -> bytes:
    return json.dumps(replay_report_payload_for_etf(code), ensure_ascii=False).encode("utf-8")


def api_replay_stability_for_etf(code: str) -> bytes:
    payload = replay_report_payload_for_etf(code)
    report = payload.get("replay_report") if isinstance(payload.get("replay_report"), dict) else {}
    stability_payload = {
        "schema_version": "myinvestetf.replay_stability.v1",
        "code": code,
        "stability": report.get("stability") if isinstance(report, dict) else {},
        "drawdown_sensitivity": report.get("drawdown_sensitivity") if isinstance(report, dict) else {},
        "consistency_score": report.get("consistency_score") if isinstance(report, dict) else None,
        "validation": report.get("validation") if isinstance(report, dict) else {},
        "constraints": payload.get("constraints", {"read_only": True}),
    }
    if payload.get("error"):
        stability_payload["error"] = payload.get("error")
    return json.dumps(stability_payload, ensure_ascii=False).encode("utf-8")


def api_replay_regime_path_for_etf(code: str) -> bytes:
    payload = replay_report_payload_for_etf(code)
    report = payload.get("replay_report") if isinstance(payload.get("replay_report"), dict) else {}
    time_series = report.get("time_series") if isinstance(report.get("time_series"), dict) else {}
    stability = report.get("stability") if isinstance(report.get("stability"), dict) else {}
    regime_payload = {
        "schema_version": "myinvestetf.replay_regime_path.v1",
        "code": code,
        "regime_series": time_series.get("regime_series") if isinstance(time_series, dict) else [],
        "regime_duration_distribution": stability.get("regime_duration_distribution") if isinstance(stability, dict) else [],
        "regime_transition_matrix": stability.get("regime_transition_matrix") if isinstance(stability, dict) else {},
        "validation": report.get("validation") if isinstance(report, dict) else {},
        "constraints": payload.get("constraints", {"read_only": True}),
    }
    if payload.get("error"):
        regime_payload["error"] = payload.get("error")
    return json.dumps(regime_payload, ensure_ascii=False).encode("utf-8")


def _health_replay_code(codes: list[str]) -> str | None:
    for preferred in ["510300.SH", "510210.SH", "510050.SH"]:
        if preferred in codes:
            return preferred
    return codes[0] if codes else None


def _sample_price_series_for_health(
    price_series_by_code: dict[str, list[object]],
    *,
    required_code: str | None,
    max_codes: int = 8,
    max_rows: int = 120,
) -> dict[str, list[object]]:
    selected: list[str] = []
    if required_code and required_code in price_series_by_code:
        selected.append(required_code)
    for code in price_series_by_code:
        if code not in selected:
            selected.append(code)
        if len(selected) >= max_codes:
            break
    return {code: list(price_series_by_code.get(code, []))[-max_rows:] for code in selected}


def research_health_payload() -> dict[str, object]:
    cached_at = _num(_HEALTH_CACHE.get("created_at")) or 0.0
    cached_payload = _HEALTH_CACHE.get("payload")
    if isinstance(cached_payload, dict) and time.time() - cached_at < HEALTH_CACHE_TTL_SECONDS:
        return cached_payload
    with closing(connect(DB_PATH)) as conn:
        leaders = list_latest_leaders(conn)
        price_series_by_code = {
            str(row["code"]): list_daily_prices(conn, str(row["code"]), start_date=BULL_MARKET_START_DATE)
            for row in leaders
        }
        taxonomy_by_code = {
            str(row["code"]): taxonomy_profile_from_sources(code=str(row["code"]), leader=row)
            for row in leaders
        }
        runs_by_code = {
            str(row["code"]): rows_to_dicts(list_research_runs(conn, str(row["code"])))
            for row in leaders
        }
        leader_by_code = {str(row["code"]): row for row in leaders}
    codes = list(price_series_by_code)
    replay_code = _health_replay_code(codes)
    ic_price_series_by_code = _sample_price_series_for_health(price_series_by_code, required_code=replay_code)
    data_quality = build_data_quality_report(price_series_by_code, min_observations=45)
    factor_exposures_by_code = {
        code: factor_exposure_from_prices(code, prices, taxonomy_by_code.get(code))
        for code, prices in price_series_by_code.items()
    }
    ic_summaries_by_factor = {
        definition.name: [factor_ic_summary_to_dict(item) for item in compute_factor_ic(definition, ic_price_series_by_code)]
        for definition in DEFAULT_FACTOR_REGISTRY
    }
    factor_quality = build_factor_quality_report(ic_summaries_by_factor, factor_exposures_by_code)
    structure = build_market_structure(price_series_by_code, taxonomy_by_code)
    current_regimes = [
        market_regime_v2_to_dict(build_market_regime_v2(code, prices, structure))
        for code, prices in price_series_by_code.items()
    ]
    replay_stability: dict[str, object] = {}
    if replay_code is not None:
        replay_runs = runs_by_code.get(replay_code, [])
        replay_latest = next((row for row in replay_runs if row.get("task_type") == "research"), replay_runs[0] if replay_runs else None)
        replay_valuation = valuation_signal_summary(replay_latest) if replay_latest else valuation_signal_summary(None)
        if replay_valuation.get("valuation_model_type") is None:
            replay_valuation.update(leader_model_info(leader_by_code.get(replay_code)))
        replay_report = replay_report_to_dict(
            build_replay_report(
                etf_code=replay_code,
                price_series_by_code=price_series_by_code,
                taxonomy_by_code=taxonomy_by_code,
                valuation_signal=replay_valuation,
                valuation_as_of_date=str((replay_latest or {}).get("research_date") or "") or None,
                min_observations=45,
                max_points=24,
            )
        )
        replay_stability = replay_report.get("stability", {}) if isinstance(replay_report.get("stability"), dict) else {}
    regime_quality = build_regime_quality_report(replay_stability, current_regimes)
    latest_runs = [
        latest
        for runs in runs_by_code.values()
        if (latest := next((row for row in runs if row.get("task_type") == "research"), runs[0] if runs else None)) is not None
    ]
    report_quality = build_report_quality_report(latest_runs)
    health = research_health_report_to_dict(
        build_research_health_report(
            data_quality=data_quality,
            factor_quality=factor_quality,
            regime_quality=regime_quality,
            report_quality=report_quality,
        )
    )
    payload = {
        "schema_version": "myinvestetf.research_health.v1",
        "replay_reference_etf": replay_code,
        "health_report": health,
        "constraints": health.get("constraints", {"read_only": True}),
    }
    _HEALTH_CACHE["created_at"] = time.time()
    _HEALTH_CACHE["payload"] = payload
    return payload


def api_health_system() -> bytes:
    return json.dumps(research_health_payload(), ensure_ascii=False).encode("utf-8")


def api_health_data() -> bytes:
    payload = research_health_payload()
    report = payload.get("health_report") if isinstance(payload.get("health_report"), dict) else {}
    return json.dumps(
        {
            "schema_version": "myinvestetf.health_data.v1",
            "data_quality": report.get("data_quality") if isinstance(report, dict) else {},
            "constraints": payload.get("constraints", {"read_only": True}),
        },
        ensure_ascii=False,
    ).encode("utf-8")


def api_health_factors() -> bytes:
    payload = research_health_payload()
    report = payload.get("health_report") if isinstance(payload.get("health_report"), dict) else {}
    return json.dumps(
        {
            "schema_version": "myinvestetf.health_factors.v1",
            "factor_quality": report.get("factor_quality") if isinstance(report, dict) else {},
            "constraints": payload.get("constraints", {"read_only": True}),
        },
        ensure_ascii=False,
    ).encode("utf-8")


def api_health_regime() -> bytes:
    payload = research_health_payload()
    report = payload.get("health_report") if isinstance(payload.get("health_report"), dict) else {}
    return json.dumps(
        {
            "schema_version": "myinvestetf.health_regime.v1",
            "regime_quality": report.get("regime_quality") if isinstance(report, dict) else {},
            "replay_reference_etf": payload.get("replay_reference_etf"),
            "constraints": payload.get("constraints", {"read_only": True}),
        },
        ensure_ascii=False,
    ).encode("utf-8")


def api_health_report() -> bytes:
    payload = research_health_payload()
    report = payload.get("health_report") if isinstance(payload.get("health_report"), dict) else {}
    return json.dumps(
        {
            "schema_version": "myinvestetf.health_report.v1",
            "report_quality": report.get("report_quality") if isinstance(report, dict) else {},
            "constraints": payload.get("constraints", {"read_only": True}),
        },
        ensure_ascii=False,
    ).encode("utf-8")


def api_queue() -> bytes:
    with closing(connect(DB_PATH)) as conn:
        rows = rows_to_dicts(list_queue(conn))
    for row in rows:
        row["source_label"] = queue_source_label(row.get("source_type"))
    return json.dumps({"items": rows}, ensure_ascii=False).encode("utf-8")


def api_catalog_json(base_url: str) -> bytes:
    return json.dumps(api_catalog(base_url), ensure_ascii=False, indent=2).encode("utf-8")


def openapi_json(base_url: str) -> bytes:
    catalog = api_catalog(base_url)
    paths: dict[str, object] = {}
    for group in catalog["groups"]:  # type: ignore[index]
        if not isinstance(group, dict):
            continue
        for endpoint in group.get("endpoints") or []:
            if not isinstance(endpoint, dict):
                continue
            method = str(endpoint.get("method") or "GET").lower()
            path = str(endpoint.get("path") or "")
            parameters = []
            for parameter in endpoint.get("parameters") or []:
                if not isinstance(parameter, dict):
                    continue
                parameters.append(
                    {
                        "name": parameter.get("name"),
                        "in": parameter.get("in"),
                        "required": bool(parameter.get("required")),
                        "description": parameter.get("description"),
                        "schema": {"type": "string"},
                    }
                )
            paths.setdefault(path, {})
            path_item = paths[path]
            if isinstance(path_item, dict):
                response_code = "303" if str(endpoint.get("returns") or "").startswith("303") else "200"
                path_item[method] = {
                    "summary": endpoint.get("purpose"),
                    "description": f"返回内容：{endpoint.get('returns')}；只读：{endpoint.get('read_only')}",
                    "parameters": parameters,
                    "responses": {
                        response_code: {
                            "description": str(endpoint.get("returns") or "OK"),
                        }
                    },
                }
    payload = {
        "openapi": "3.0.3",
        "info": {
            "title": SYSTEM_NAME,
            "version": SYSTEM_VERSION,
            "description": SYSTEM_DESCRIPTION,
        },
        "servers": [{"url": base_url or "/"}],
        "paths": paths,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def docs_html(title: str, base_url: str) -> bytes:
    catalog = api_catalog(base_url)
    groups = catalog.get("groups")
    group_items = []
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            endpoints = group.get("endpoints") or []
            endpoint_items = "".join(
                f"<li><code>{esc(ep.get('method'))} {esc(ep.get('path'))}</code>：{esc(ep.get('purpose'))}</li>"
                for ep in endpoints
                if isinstance(ep, dict)
            )
            group_items.append(
                f"""<section>
        <h2>{esc(group.get('name'))}</h2>
        <p>{esc(group.get('description'))}</p>
        <ul>{endpoint_items}</ul>
      </section>"""
            )
    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} | {SYSTEM_NAME}</title>
  <link rel="stylesheet" href="/static/styles.css?v={STATIC_ASSET_VERSION}">
</head>
<body>
  <main class="content docs-page">
    <h1>{esc(title)}</h1>
    <p class="muted">{esc(SYSTEM_DESCRIPTION)}</p>
    <p><a class="text-link" href="/api"><code>GET /api</code></a> · <a class="text-link" href="/openapi.json"><code>/openapi.json</code></a></p>
    {"".join(group_items)}
  </main>
</body>
</html>"""
    return html_text.encode("utf-8")


class MyInvestETFHandler(BaseHTTPRequestHandler):
    server_version = "MyInvestETF/0.1"

    def request_base_url(self) -> str:
        host = self.headers.get("Host") or f"{DEFAULT_HOST}:{DEFAULT_PORT}"
        proto = self.headers.get("X-Forwarded-Proto") or "http"
        return f"{proto}://{host}"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/":
            self.send_bytes(render_home(), "text/html; charset=utf-8")
            return
        if path == "/api":
            self.send_bytes(api_catalog_json(self.request_base_url()), "application/json; charset=utf-8")
            return
        if path == "/openapi.json":
            self.send_bytes(openapi_json(self.request_base_url()), "application/json; charset=utf-8")
            return
        if path == "/docs":
            self.send_bytes(docs_html("接口文档", self.request_base_url()), "text/html; charset=utf-8")
            return
        if path == "/redoc":
            self.send_bytes(docs_html("ReDoc", self.request_base_url()), "text/html; charset=utf-8")
            return
        if path == "/research":
            self.handle_research_gateway(parsed.query)
            return
        if path == "/api/index":
            self.send_bytes(api_index(), "application/json; charset=utf-8")
            return
        if path == "/api/latest":
            self.send_bytes(api_latest(), "application/json; charset=utf-8")
            return
        if path == "/api/etfs":
            self.send_bytes(api_etfs(), "application/json; charset=utf-8")
            return
        if path == "/api/queue":
            self.send_bytes(api_queue(), "application/json; charset=utf-8")
            return
        if path == "/api/health/system":
            self.send_bytes(api_health_system(), "application/json; charset=utf-8")
            return
        if path == "/api/health/data":
            self.send_bytes(api_health_data(), "application/json; charset=utf-8")
            return
        if path == "/api/health/factors":
            self.send_bytes(api_health_factors(), "application/json; charset=utf-8")
            return
        if path == "/api/health/regime":
            self.send_bytes(api_health_regime(), "application/json; charset=utf-8")
            return
        if path == "/api/health/report":
            self.send_bytes(api_health_report(), "application/json; charset=utf-8")
            return
        if path == "/api/market/structure":
            self.send_bytes(api_market_structure(), "application/json; charset=utf-8")
            return
        if path == "/api/market/breadth":
            self.send_bytes(api_market_breadth(), "application/json; charset=utf-8")
            return
        if path == "/api/market/liquidity":
            self.send_bytes(api_market_liquidity(), "application/json; charset=utf-8")
            return
        if path == "/api/market/regime-v2":
            self.send_bytes(api_market_regime_v2(), "application/json; charset=utf-8")
            return
        if path.startswith("/api/score/decompose/"):
            code = path.removeprefix("/api/score/decompose/").upper()
            self.send_bytes(api_score_decompose_for_etf(code), "application/json; charset=utf-8")
            return
        if path.startswith("/api/score/"):
            code = path.removeprefix("/api/score/").upper()
            self.send_bytes(api_score_for_etf(code), "application/json; charset=utf-8")
            return
        if path.startswith("/api/ask/"):
            code = path.removeprefix("/api/ask/").upper()
            self.send_bytes(api_ask_for_etf(code, parsed.query), "application/json; charset=utf-8")
            return
        if path.startswith("/api/decision/state/"):
            code = path.removeprefix("/api/decision/state/").upper()
            self.send_bytes(api_decision_state_for_etf(code), "application/json; charset=utf-8")
            return
        if path.startswith("/api/strategy/contrarian/"):
            code = path.removeprefix("/api/strategy/contrarian/").upper()
            self.send_bytes(api_contrarian_for_etf(code), "application/json; charset=utf-8")
            return
        if path.startswith("/api/strategy/route/"):
            code = path.removeprefix("/api/strategy/route/").upper()
            self.send_bytes(api_strategy_route_for_etf(code), "application/json; charset=utf-8")
            return
        if path.startswith("/api/replay/") and path.endswith("/stability"):
            code = path.removeprefix("/api/replay/").removesuffix("/stability").strip("/").upper()
            self.send_bytes(api_replay_stability_for_etf(code), "application/json; charset=utf-8")
            return
        if path.startswith("/api/replay/") and path.endswith("/regime-path"):
            code = path.removeprefix("/api/replay/").removesuffix("/regime-path").strip("/").upper()
            self.send_bytes(api_replay_regime_path_for_etf(code), "application/json; charset=utf-8")
            return
        if path.startswith("/api/replay/"):
            code = path.removeprefix("/api/replay/").upper()
            self.send_bytes(api_replay_for_etf(code), "application/json; charset=utf-8")
            return
        if path.startswith("/api/factors/ic/"):
            factor_name = path.removeprefix("/api/factors/ic/").strip()
            self.send_bytes(api_factor_ic(factor_name), "application/json; charset=utf-8")
            return
        if path.startswith("/api/factors/exposure/"):
            code = path.removeprefix("/api/factors/exposure/").upper()
            self.send_bytes(api_factors_for_etf(code), "application/json; charset=utf-8")
            return
        if path.startswith("/api/factors/"):
            code = path.removeprefix("/api/factors/").upper()
            self.send_bytes(api_factors_for_etf(code), "application/json; charset=utf-8")
            return
        if path.startswith("/api/etf/") and path.endswith("/profile"):
            code = path.removeprefix("/api/etf/").removesuffix("/profile").strip("/").upper()
            self.send_bytes(api_etf_profile(code), "application/json; charset=utf-8")
            return
        if path.startswith("/api/etfs/") and path.endswith("/profile"):
            code = path.removeprefix("/api/etfs/").removesuffix("/profile").strip("/").upper()
            self.send_bytes(api_etf_profile(code), "application/json; charset=utf-8")
            return
        if path.startswith("/api/etfs/"):
            code = path.removeprefix("/api/etfs/").upper()
            self.send_bytes(api_etf(code), "application/json; charset=utf-8")
            return
        if path.startswith("/etfs/"):
            code = path.removeprefix("/etfs/").upper()
            self.send_bytes(render_etf_page(code), "text/html; charset=utf-8")
            return
        if path.startswith("/static/"):
            self.send_static(path.removeprefix("/static/"))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def handle_research_gateway(self, query: str) -> None:
        code, requested_name = normalize_etf_query(parse_qs(query))
        if code is None or not ETF_CODE_RE.match(code):
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid etf code")
            return
        with closing(connect(DB_PATH)) as conn:
            exists, known_name = _etf_exists(conn, code)
        queued = False
        if not exists:
            enqueue_requested_etf(code, name=requested_name or known_name or code)
            queued = True
        location = f"/etfs/{quote(code)}"
        if queued:
            location += "?queued=1"
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, relative_path: str) -> None:
        safe = Path(relative_path)
        if safe.is_absolute() or ".." in safe.parts:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        path = ROOT / "web" / "static" / safe
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_bytes(path.read_bytes(), content_type)

    def log_message(self, format: str, *args: object) -> None:
        return


def run(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    httpd = ThreadingHTTPServer((host, port), MyInvestETFHandler)
    print(f"MyInvestETF Web running at http://{host}:{port}/", flush=True)
    httpd.serve_forever()
