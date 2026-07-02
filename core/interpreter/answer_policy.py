from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Literal

from .question_router import QuestionIntent, question_intent_to_dict


ConclusionType = Literal["participate", "observe", "avoid"]


@dataclass(frozen=True)
class FinalAnswer:
    headline: str
    conclusion: dict[str, object]
    reasoning: list[str]
    risk_notes: list[str]
    confidence: float


def _mapping(value: object | None) -> dict[str, Any]:
    if value is None:
        return {}
    if is_dataclass(value) and not isinstance(value, QuestionIntent):
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


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _nested(source: Mapping[str, Any], key: str) -> dict[str, Any]:
    return _mapping(source.get(key))


def _intent_dict(intent: QuestionIntent | Mapping[str, object] | None) -> dict[str, Any]:
    if isinstance(intent, QuestionIntent):
        return question_intent_to_dict(intent)
    return _mapping(intent)


def _gate_status(report: Mapping[str, Any]) -> str:
    value = str(report.get("gate_status") or "unknown")
    return value if value in {"pass", "warn", "reject"} else "unknown"


def _health_warnings(governance: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    for key in ("data_quality", "regime_quality", "factor_quality", "report_quality"):
        report = _nested(governance, key)
        status = _gate_status(report)
        if status in {"warn", "reject"}:
            warnings.append(f"{key}: {status}")
        if key == "regime_quality" and report.get("overfit_warning") is True:
            warnings.append("regime_quality: overfit_warning")
        for list_key in ("missing_fields", "stale_items", "unstable_factors", "ic_decay_alerts", "rejection_reasons"):
            value = report.get(list_key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                warnings.extend(str(item) for item in value[:2])
    return warnings


def _conclusion_type(score: float | None, regime: str, governance: Mapping[str, Any]) -> ConclusionType:
    data_status = _gate_status(_nested(governance, "data_quality"))
    regime_status = _gate_status(_nested(governance, "regime_quality"))
    report_status = _gate_status(_nested(governance, "report_quality"))
    if "reject" in {data_status, regime_status, report_status}:
        return "avoid"
    if regime == "shock":
        return "avoid"
    if score is not None and score >= 75.0:
        return "participate"
    if score is not None and score >= 60.0:
        return "observe"
    return "avoid"


def _headline(conclusion_type: ConclusionType, intent_type: str) -> str:
    label_by_type = {
        "buy_assessment": "参与评估",
        "risk_assessment": "风险判断",
        "market_state": "状态判断",
        "comparison": "比较意图",
        "unknown": "结构解释",
    }
    result_by_type = {
        "participate": "结构支持参与评估",
        "observe": "可观察，等待更清晰条件",
        "avoid": "结构不支持参与",
    }
    return f"{label_by_type.get(intent_type, '结构解释')}：{result_by_type[conclusion_type]}"


class AnswerPolicyEngine:
    """Centralized policy for final ETF interpretation answers."""

    def generate_answer(
        self,
        *,
        decision_signal: object | None,
        regime: object | None,
        intent: QuestionIntent | Mapping[str, object] | None,
        governance: object | None,
        taxonomy: object | None,
    ) -> FinalAnswer:
        signal = _mapping(decision_signal)
        regime_map = _mapping(regime)
        state = _nested(signal, "state")
        governance_map = _mapping(governance)
        taxonomy_map = _mapping(taxonomy)
        intent_map = _intent_dict(intent)
        regime_state = str(state.get("regime") or regime_map.get("regime") or "unknown")
        score = _safe_float(signal.get("score"))
        signal_confidence = _safe_float(signal.get("confidence"), 0.0) or 0.0
        intent_confidence = _safe_float(intent_map.get("confidence"), 0.0) or 0.0
        warnings = _health_warnings(governance_map)
        conclusion_type = _conclusion_type(score, regime_state, governance_map)
        confidence_penalty = 0.18 if conclusion_type == "avoid" and warnings else 0.0
        confidence = _clamp(signal_confidence * 0.65 + intent_confidence * 0.25 + 0.10 - confidence_penalty)
        taxonomy_type = str(taxonomy_map.get("etf_type") or signal.get("taxonomy_type") or "unknown")
        intent_type = str(intent_map.get("type") or "unknown")
        score_text = "待入库" if score is None else f"{score:.2f}"
        reasoning = [
            f"问题意图为 {intent_type}，关注点为 {intent_map.get('focus') or 'unknown'}。",
            f"市场状态为 {regime_state}，Decision Score 为 {score_text}。",
            f"ETF taxonomy 为 {taxonomy_type}，最终回答由统一 AnswerPolicyEngine 生成。",
        ]
        risk_notes = warnings[:5]
        if regime_state == "shock":
            risk_notes.insert(0, "regime: shock")
        if not risk_notes:
            risk_notes.append("governance: no blocking warning")
        return FinalAnswer(
            headline=_headline(conclusion_type, intent_type),
            conclusion={
                "type": conclusion_type,
                "non_trading": True,
            },
            reasoning=reasoning,
            risk_notes=risk_notes,
            confidence=round(confidence, 6),
        )


def final_answer_to_dict(answer: FinalAnswer) -> dict[str, object]:
    return asdict(answer)
