from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any, Literal

from .question_router import QuestionIntent, parse_question, question_intent_to_dict


DecisionBand = Literal["low", "medium", "high"]
DirectionalBias = Literal["bullish", "neutral", "bearish"]


def _mapping(value: object | None) -> dict[str, Any]:
    if value is None:
        return {}
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _safe_float(value: object, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return number


def _nested(source: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = source.get(key)
    return _mapping(value)


def _score_band(score: float | None) -> DecisionBand:
    if score is None:
        return "low"
    if score >= 75.0:
        return "high"
    if score >= 60.0:
        return "medium"
    return "low"


def _directional_bias(score: float | None, regime: str) -> DirectionalBias:
    if regime == "shock":
        return "bearish"
    if score is None:
        return "neutral"
    if score >= 75.0:
        return "bullish"
    if score >= 60.0:
        return "neutral"
    return "bearish"


def _final_answer(score: float | None, regime: str, intent_type: str) -> str:
    if regime == "shock":
        base = "当前市场结构不稳定，不适合基于趋势做参与评估。"
    elif score is not None and score >= 75.0:
        base = "结构支持参与评估，但仍应等待清晰触发条件并分批观察。"
    elif score is not None and score >= 60.0:
        base = "中性偏积极，适合继续观察或做小范围验证。"
    else:
        base = "结构不支持参与，风险或证据不充分。"
    if intent_type == "risk_assessment":
        base = f"风险判断：{base}"
    elif intent_type == "market_state":
        base = f"状态判断：{base}"
    elif intent_type == "comparison":
        base = f"比较意图已识别；当前输出仅解释单只ETF。{base}"
    elif intent_type == "buy_assessment":
        base = f"参与评估：{base}"
    return f"{base}本输出只作研究解释，不构成交易指令。"


def _status_label(status: object) -> str:
    text = str(status or "unknown")
    if text == "pass":
        return "pass"
    if text == "warn":
        return "warn"
    if text == "reject":
        return "reject"
    return "unknown"


def _warnings_from_health(health: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    data_quality = _nested(health, "data_quality")
    regime_quality = _nested(health, "regime_quality")
    report_quality = _nested(health, "report_quality")
    factor_quality = _nested(health, "factor_quality")

    for label, report in [
        ("data_quality", data_quality),
        ("regime_quality", regime_quality),
        ("factor_quality", factor_quality),
        ("report_quality", report_quality),
    ]:
        status = _status_label(report.get("gate_status"))
        if status in {"warn", "reject"}:
            warnings.append(f"{label}: {status}")

    if regime_quality.get("overfit_warning") is True:
        warnings.append("regime_quality: overfit_warning")

    for key in ("missing_fields", "stale_items", "unstable_factors", "ic_decay_alerts", "rejection_reasons"):
        value = data_quality.get(key) or factor_quality.get(key) or report_quality.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            warnings.extend(str(item) for item in value[:3])

    return warnings


class DecisionInterpreter:
    """Convert existing ETF decision outputs into a human-readable answer.

    The interpreter is deliberately read-only: callers provide already-built
    decision, taxonomy, regime and governance payloads. This layer does not
    fetch data, recompute scores, write state, or emit trade orders.
    """

    def __init__(self, decision_engine: object | None = None, governance_engine: object | None = None) -> None:
        self.decision_engine = decision_engine
        self.governance = governance_engine

    def interpret(
        self,
        etf_code: str,
        question: str,
        *,
        decision_signal: object | None = None,
        taxonomy_profile: object | None = None,
        market_regime: object | None = None,
        governance_report: object | None = None,
        question_intent: QuestionIntent | None = None,
    ) -> dict[str, Any]:
        """Return structured interpretation for one ETF and one question."""

        intent = question_intent or parse_question(question, etf_code=etf_code)
        intent_payload = question_intent_to_dict(intent)
        signal = _mapping(decision_signal)
        taxonomy = _mapping(taxonomy_profile)
        health = _mapping(governance_report)
        state = _nested(signal, "state")
        signal_regime = _nested(signal, "regime")
        fallback_regime = _mapping(market_regime)

        regime_state = str(
            state.get("regime")
            or signal_regime.get("regime")
            or fallback_regime.get("regime")
            or "unknown"
        )
        confidence = _safe_float(signal.get("confidence"))
        if confidence is None:
            confidence = _safe_float(signal_regime.get("confidence"), _safe_float(fallback_regime.get("confidence"), 0.0))
        score = _safe_float(signal.get("score"))
        band = _score_band(score)
        bias = _directional_bias(score, regime_state)
        regime_quality = _nested(health, "regime_quality")
        data_quality = _nested(health, "data_quality")
        warnings = _warnings_from_health(health)

        taxonomy_type = str(taxonomy.get("etf_type") or signal.get("taxonomy_type") or "unknown")
        taxonomy_subtype = str(taxonomy.get("subtype") or "unknown")
        score_text = "待入库" if score is None else f"{score:.2f}"
        confidence_value = round(confidence or 0.0, 6)
        explanation = [
            f"问题意图为 {intent.type}，关注点为 {intent.focus}。",
            f"市场状态为 {regime_state}，置信度 {confidence_value:.2f}。",
            f"ETF 类型为 {taxonomy_type}，子类 {taxonomy_subtype}。",
            f"综合评分 {score_text}，评分带 {band}，方向偏向 {bias}。",
        ]
        if warnings:
            explanation.append(f"治理层提示：{warnings[0]}。")

        return {
            "question": question,
            "etf": etf_code,
            "regime": {
                "state": regime_state,
                "confidence": confidence_value,
            },
            "taxonomy": {
                "type": taxonomy_type,
                "subtype": taxonomy_subtype,
            },
            "intent": intent_payload,
            "decision": {
                "score": score,
                "band": band,
                "directional_bias": bias,
            },
            "explanation": explanation,
            "risk": {
                "regime_stability": _status_label(regime_quality.get("gate_status")),
                "data_quality": _status_label(data_quality.get("gate_status")),
                "warnings": warnings,
            },
            "final_answer": _final_answer(score, regime_state, intent.type),
        }
