from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal


ActiveMode = Literal["trend", "contrarian", "neutral"]
SuppressedMode = Literal["trend", "contrarian"] | None


@dataclass(frozen=True)
class StrategyDecision:
    active_mode: ActiveMode
    confidence: float
    reasoning: dict[str, str]
    suppressed_mode: SuppressedMode
    signals: dict[str, float]
    final_interpretation: str
    constraints: dict[str, bool]


def _mapping(value: object | None) -> dict[str, Any]:
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


def _unit_score(value: object, default: float = 0.50) -> float:
    number = _safe_float(value)
    if number is None:
        return default
    if number > 1.0:
        number = number / 100.0
    return _clamp(number)


class StrategyRouter:
    def __init__(self, decision_engine: object | None, contrarian_engine: object | None, regime_engine: object | None, governance: object | None):
        self.decision_signal = _mapping(decision_engine)
        self.contrarian_signal = _mapping(contrarian_engine)
        self.regime = _mapping(regime_engine)
        self.governance = _mapping(governance)

    def route(self, etf_code: str) -> StrategyDecision:
        context = self._context(etf_code)
        if context["governance_gate"] == "reject":
            active_mode: ActiveMode = "neutral"
            suppressed = self._stronger_mode(context)
            confidence = min(0.35, context["governance_confidence"])
            return self._decision(active_mode, confidence, suppressed, context, "governance reject，策略路由降级为 neutral。")

        trend_candidate = bool(
            context["regime"] == "risk_on"
            and context["flow_positive"]
            and context["momentum_positive"]
            and not context["drawdown_extreme"]
            and context["trend_score"] >= 0.55
        )
        contrarian_candidate = bool(
            context["contrarian_enabled"]
            or (
                context["drawdown_extreme"]
                and context["regime"] in {"risk_off", "shock"}
                and context["contrarian_score"] >= 0.60
            )
        )

        if trend_candidate and contrarian_candidate:
            return self.resolve_conflict(self.decision_signal, self.contrarian_signal)
        if context["governance_gate"] == "warn" and abs(context["trend_score"] - context["contrarian_score"]) < 0.15:
            active_mode = "neutral"
            suppressed = self._stronger_mode(context)
        elif contrarian_candidate:
            active_mode = "contrarian"
            suppressed = "trend" if context["trend_score"] >= 0.55 else None
        elif trend_candidate:
            active_mode = "trend"
            suppressed = "contrarian" if context["contrarian_score"] >= 0.55 else None
        else:
            active_mode = "neutral"
            suppressed = self._stronger_mode(context) if max(context["trend_score"], context["contrarian_score"]) >= 0.55 else None

        confidence = self._confidence(active_mode, context)
        return self._decision(active_mode, confidence, suppressed, context, self._interpretation(active_mode, context))

    def resolve_conflict(self, trend_signal: object, contrarian_signal: object) -> StrategyDecision:
        context = self._context(str(_mapping(trend_signal).get("etf_code") or "UNKNOWN"))
        regime_confidence = context["regime_confidence"]
        if context["regime"] == "risk_on" and regime_confidence >= 0.60:
            active_mode: ActiveMode = "trend"
            suppressed: SuppressedMode = "contrarian"
        elif context["regime"] in {"risk_off", "shock"} and regime_confidence >= 0.55:
            active_mode = "contrarian"
            suppressed = "trend"
        else:
            active_mode = "neutral"
            suppressed = self._stronger_mode(context)
        confidence = self._confidence(active_mode, context) * 0.90
        return self._decision(active_mode, round(_clamp(confidence), 6), suppressed, context, self._interpretation(active_mode, context))

    def _context(self, etf_code: str) -> dict[str, Any]:
        state = _mapping(self.decision_signal.get("state"))
        components = _mapping(self.decision_signal.get("component_scores"))
        contrarian_scores = _mapping(self.contrarian_signal.get("scores"))
        contrarian_conditions = _mapping(self.contrarian_signal.get("conditions"))
        adjusted = _mapping(self.contrarian_signal.get("adjusted_interpretation"))

        regime_name = str(self.regime.get("regime") or state.get("regime") or "rotation")
        decision_score = _unit_score(self.decision_signal.get("score"))
        momentum_score = _unit_score(components.get("momentum"))
        flow_score = _unit_score(components.get("flow"))
        trend_score = _clamp(decision_score * 0.55 + momentum_score * 0.25 + flow_score * 0.20)
        contrarian_score = _unit_score(contrarian_scores.get("reversal_probability"), default=0.0)
        governance_gate, governance_confidence = self._governance_status()
        return {
            "etf_code": etf_code,
            "regime": regime_name,
            "regime_confidence": _unit_score(self.regime.get("confidence"), default=0.50),
            "governance_gate": governance_gate,
            "governance_confidence": governance_confidence,
            "decision_score": round(decision_score, 6),
            "momentum_score": round(momentum_score, 6),
            "flow_score": round(flow_score, 6),
            "trend_score": round(trend_score, 6),
            "contrarian_score": round(contrarian_score, 6),
            "contrarian_enabled": bool(self.contrarian_signal.get("enabled")),
            "drawdown_extreme": bool(contrarian_conditions.get("drawdown_extreme")),
            "regime_stress": bool(contrarian_conditions.get("regime_stress")),
            "liquidity_stress": bool(contrarian_conditions.get("liquidity_stress")),
            "contrarian_final_view": str(adjusted.get("final_view") or self.contrarian_signal.get("final_view") or "not_active"),
            "momentum_positive": momentum_score >= 0.55,
            "flow_positive": flow_score >= 0.55,
        }

    def _governance_status(self) -> tuple[str, float]:
        health = _mapping(self.governance.get("health_report") or self.governance)
        gate = str(health.get("gate_status") or "pass")
        score = _safe_float(health.get("system_health_score"), 75.0) or 75.0
        confidence = _clamp(score / 100.0)
        if gate == "reject":
            confidence *= 0.35
        elif gate == "warn":
            confidence *= 0.75
        return gate, round(confidence, 6)

    def _confidence(self, active_mode: ActiveMode, context: Mapping[str, Any]) -> float:
        mode_score = 0.50
        if active_mode == "trend":
            mode_score = float(context["trend_score"])
        elif active_mode == "contrarian":
            mode_score = float(context["contrarian_score"])
        else:
            mode_score = 1.0 - abs(float(context["trend_score"]) - float(context["contrarian_score"]))
        confidence = mode_score * 0.50 + float(context["regime_confidence"]) * 0.25 + float(context["governance_confidence"]) * 0.25
        if context["governance_gate"] == "warn":
            confidence *= 0.85
        return round(_clamp(confidence), 6)

    def _stronger_mode(self, context: Mapping[str, Any]) -> SuppressedMode:
        if float(context["trend_score"]) > float(context["contrarian_score"]):
            return "trend"
        if float(context["contrarian_score"]) > float(context["trend_score"]):
            return "contrarian"
        return None

    def _reasoning(self, context: Mapping[str, Any]) -> dict[str, str]:
        regime = str(context["regime"])
        return {
            "regime_reason": f"regime={regime}, confidence={float(context['regime_confidence']):.2f}",
            "flow_reason": f"flow_score={float(context['flow_score']):.2f}, positive={bool(context['flow_positive'])}",
            "drawdown_reason": f"drawdown_extreme={bool(context['drawdown_extreme'])}, contrarian_score={float(context['contrarian_score']):.2f}",
            "governance_reason": f"gate={context['governance_gate']}, confidence={float(context['governance_confidence']):.2f}",
        }

    def _interpretation(self, active_mode: ActiveMode, context: Mapping[str, Any]) -> str:
        if active_mode == "trend":
            return "当前由顺势模式主导：趋势、资金或风险偏好更支持按 DecisionSignal 解读。"
        if active_mode == "contrarian":
            return "当前由抄底概率模式主导：这是极端回撤下的概率底部观察，不是趋势买点。"
        if context["governance_gate"] == "warn":
            return "当前 governance 为 warn 或信号冲突，策略路由保持 neutral。"
        return "当前趋势与抄底信号未形成清晰主导，策略路由保持 neutral。"

    def _decision(
        self,
        active_mode: ActiveMode,
        confidence: float,
        suppressed_mode: SuppressedMode,
        context: Mapping[str, Any],
        final_interpretation: str,
    ) -> StrategyDecision:
        return StrategyDecision(
            active_mode=active_mode,
            confidence=round(_clamp(confidence), 6),
            reasoning=self._reasoning(context),
            suppressed_mode=suppressed_mode,
            signals={
                "trend_score": round(float(context["trend_score"]), 6),
                "contrarian_score": round(float(context["contrarian_score"]), 6),
                "decision_score": round(float(context["decision_score"]), 6),
                "momentum_score": round(float(context["momentum_score"]), 6),
                "flow_score": round(float(context["flow_score"]), 6),
            },
            final_interpretation=final_interpretation,
            constraints={
                "read_only": True,
                "research_only": True,
                "does_not_override_decision_score": True,
                "contains_trade_orders": False,
                "contains_cash_amounts": False,
                "contains_share_counts": False,
                "executes_rebalance": False,
            },
        )


def strategy_decision_to_dict(decision: StrategyDecision) -> dict[str, Any]:
    return asdict(decision)
