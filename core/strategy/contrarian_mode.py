from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from math import exp
from typing import Any, Literal


FinalView = Literal["probabilistic_bottom_zone", "normal", "not_active"]


@dataclass(frozen=True)
class ContrarianSignal:
    enabled: bool
    scores: dict[str, float]
    conditions: dict[str, bool]
    adjusted_interpretation: dict[str, object]
    evidence: dict[str, object]
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


def _unit(value: object, default: float = 0.0) -> float:
    number = _safe_float(value)
    if number is None:
        return default
    if number > 1.0:
        number = number / 100.0
    return _clamp(number)


def _sigmoid(value: float) -> float:
    if value >= 20:
        return 1.0
    if value <= -20:
        return 0.0
    return 1.0 / (1.0 + exp(-value))


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


class ContrarianModeEngine:
    def __init__(self, market_context: object | None, factor_engine: object | None, governance: object | None):
        self.market_context = _mapping(market_context)
        self.factor_engine = _mapping(factor_engine)
        self.governance = _mapping(governance)

    def detect(self, etf_code: str) -> dict[str, bool]:
        metrics = self._metrics(etf_code)
        governance_allowed = metrics["governance_gate"] != "reject"
        return {
            "drawdown_extreme": bool(metrics["drawdown_extreme"]),
            "regime_stress": bool(metrics["regime_stress"]),
            "liquidity_stress": bool(metrics["liquidity_stress"]),
            "volatility_stress": bool(metrics["volatility_stress"]),
            "governance_allowed": governance_allowed,
        }

    def compute_probability(self, etf_code: str) -> ContrarianSignal:
        metrics = self._metrics(etf_code)
        conditions = self.detect(etf_code)
        drawdown_extreme_score = float(metrics["drawdown_extreme_score"])
        volatility_unit = float(metrics["volatility_unit"])
        breadth_stress = 1.0 - float(metrics["breadth_score"])
        liquidity_stress_unit = float(metrics["liquidity_stress_unit"])
        recovery_component = float(metrics["recovery_component"])
        acceleration_exhaustion = float(metrics["acceleration_exhaustion"])
        flow_stabilization = float(metrics["flow_stabilization"])
        governance_confidence = float(metrics["governance_confidence"])

        capitulation_score = _clamp(
            drawdown_extreme_score * 0.45
            + volatility_unit * 0.25
            + breadth_stress * 0.20
            + liquidity_stress_unit * 0.10
        )
        exhaustion_score = _clamp(
            drawdown_extreme_score * 0.30
            + flow_stabilization * 0.25
            + recovery_component * 0.20
            + acceleration_exhaustion * 0.15
            + volatility_unit * 0.10
        )
        drawdown_probability = _sigmoid((drawdown_extreme_score - 0.70) * 5.0)
        reversal_probability = _clamp(
            drawdown_probability * 0.35
            + capitulation_score * 0.25
            + exhaustion_score * 0.25
            + governance_confidence * 0.15
        )

        enabled = bool(
            conditions["drawdown_extreme"]
            and conditions["regime_stress"]
            and conditions["volatility_stress"]
            and conditions["governance_allowed"]
        )
        if enabled:
            final_view: FinalView = "probabilistic_bottom_zone"
        elif conditions["drawdown_extreme"] or reversal_probability >= 0.55:
            final_view = "normal"
        else:
            final_view = "not_active"

        return ContrarianSignal(
            enabled=enabled,
            scores={
                "reversal_probability": round(reversal_probability, 6),
                "exhaustion_score": round(exhaustion_score, 6),
                "capitulation_score": round(capitulation_score, 6),
            },
            conditions={
                "drawdown_extreme": conditions["drawdown_extreme"],
                "regime_stress": conditions["regime_stress"],
                "liquidity_stress": conditions["liquidity_stress"],
            },
            adjusted_interpretation={
                "risk_adjusted_score": None,
                "confidence": round(governance_confidence, 6),
                "final_view": final_view,
                "explanation": self._explanation(enabled, final_view),
            },
            evidence={
                "etf_code": etf_code,
                "current_drawdown": metrics["current_drawdown"],
                "max_drawdown_rolling": metrics["max_drawdown_rolling"],
                "drawdown_percentile": metrics["drawdown_percentile"],
                "extreme_proximity": metrics["extreme_proximity"],
                "regime": metrics["regime"],
                "volatility_20": metrics["volatility_20"],
                "volatility_stress": conditions["volatility_stress"],
                "breadth_score": metrics["breadth_score"],
                "liquidity_score": metrics["liquidity_score"],
                "flow_score": metrics["flow_score"],
                "governance_gate": metrics["governance_gate"],
            },
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

    def adjust_decision(self, decision_signal: object) -> ContrarianSignal:
        signal = self.compute_probability(self._decision_etf_code(decision_signal))
        decision = _mapping(decision_signal)
        score = _safe_float(decision.get("score"))
        probability = signal.scores["reversal_probability"] * 100.0
        if score is None:
            risk_adjusted_score = None
        elif signal.enabled:
            risk_adjusted_score = round(_clamp((score * 0.75 + probability * 0.25) / 100.0) * 100.0, 6)
        else:
            risk_adjusted_score = round(score, 6)
        adjusted = dict(signal.adjusted_interpretation)
        adjusted["risk_adjusted_score"] = risk_adjusted_score
        adjusted["original_decision_score"] = round(score, 6) if score is not None else None
        return ContrarianSignal(
            enabled=signal.enabled,
            scores=signal.scores,
            conditions=signal.conditions,
            adjusted_interpretation=adjusted,
            evidence=signal.evidence,
            constraints=signal.constraints,
        )

    def _metrics(self, etf_code: str) -> dict[str, object]:
        regime = _mapping(self.market_context.get("regime_v2") or self.market_context.get("regime"))
        drawdown = _mapping(self.market_context.get("drawdown"))
        structure = _mapping(self.market_context.get("market_structure") or regime.get("structure"))
        evidence = _mapping(regime.get("evidence"))

        current_drawdown = _safe_float(drawdown.get("current_drawdown"), _safe_float(evidence.get("current_drawdown"), 0.0)) or 0.0
        max_drawdown = _safe_float(drawdown.get("max_drawdown_rolling"), current_drawdown) or current_drawdown
        drawdown_percentile = _unit(drawdown.get("drawdown_percentile"), default=0.0)
        extreme_proximity = _clamp(current_drawdown / max_drawdown) if max_drawdown > 0 else 0.0
        drawdown_extreme_score = max(drawdown_percentile, extreme_proximity, _clamp(current_drawdown / 0.25))
        drawdown_extreme = drawdown_percentile >= 0.85 or extreme_proximity >= 0.90 or current_drawdown >= 0.20

        regime_name = str(regime.get("regime") or "rotation")
        regime_stress = regime_name in {"risk_off", "shock"}
        volatility_20 = _safe_float(evidence.get("volatility_20"), 0.0) or 0.0
        volatility_unit = _clamp(volatility_20 / 0.04)
        volatility_stress = volatility_unit >= 0.70 or volatility_20 >= 0.028
        breadth_score = _unit(structure.get("breadth_score"), default=0.50)
        liquidity_score = _unit(structure.get("liquidity_score"), default=0.50)
        flow_score = self._flow_score(default=liquidity_score)
        liquidity_stress_unit = max(1.0 - liquidity_score, 1.0 - flow_score)
        liquidity_stress = liquidity_stress_unit >= 0.55

        recovery_speed = _safe_float(drawdown.get("recovery_speed"), 0.0) or 0.0
        recovery_component = _clamp(0.50 + recovery_speed * 25.0)
        acceleration = _safe_float(drawdown.get("drawdown_acceleration"), 0.0) or 0.0
        acceleration_exhaustion = _clamp(1.0 - max(0.0, acceleration) / 0.04)
        flow_stabilization = _clamp(1.0 - abs(flow_score - 0.50) * 2.0)
        governance_gate, governance_confidence = self._governance_status()

        return {
            "etf_code": etf_code,
            "current_drawdown": round(current_drawdown, 6),
            "max_drawdown_rolling": round(max_drawdown, 6),
            "drawdown_percentile": round(drawdown_percentile, 6),
            "extreme_proximity": round(extreme_proximity, 6),
            "drawdown_extreme_score": round(drawdown_extreme_score, 6),
            "drawdown_extreme": drawdown_extreme,
            "regime": regime_name,
            "regime_stress": regime_stress,
            "volatility_20": round(volatility_20, 6),
            "volatility_unit": round(volatility_unit, 6),
            "volatility_stress": volatility_stress,
            "breadth_score": round(breadth_score, 6),
            "liquidity_score": round(liquidity_score, 6),
            "flow_score": round(flow_score, 6),
            "liquidity_stress_unit": round(liquidity_stress_unit, 6),
            "liquidity_stress": liquidity_stress,
            "recovery_component": round(recovery_component, 6),
            "acceleration_exhaustion": round(acceleration_exhaustion, 6),
            "flow_stabilization": round(flow_stabilization, 6),
            "governance_gate": governance_gate,
            "governance_confidence": round(governance_confidence, 6),
        }

    def _flow_score(self, *, default: float) -> float:
        factors = self.factor_engine.get("factors")
        if not isinstance(factors, list):
            return default
        values: list[float] = []
        for row in factors:
            item = _mapping(row)
            if str(item.get("factor_type") or "") != "flow":
                continue
            value = _safe_float(item.get("normalized_value"))
            if value is not None:
                values.append(_clamp(value))
        average = _average(values)
        return average if average is not None else default

    def _governance_status(self) -> tuple[str, float]:
        health = _mapping(self.governance.get("health_report") or self.governance)
        gate = str(health.get("gate_status") or "pass")
        system_score = _safe_float(health.get("system_health_score"), 75.0) or 75.0
        confidence = _clamp(system_score / 100.0)
        if gate == "reject":
            confidence *= 0.35
        elif gate == "warn":
            confidence *= 0.75
        return gate, confidence

    def _decision_etf_code(self, decision_signal: object) -> str:
        decision = _mapping(decision_signal)
        return str(decision.get("etf_code") or self.market_context.get("etf_code") or "UNKNOWN")

    def _explanation(self, enabled: bool, final_view: FinalView) -> str:
        if enabled:
            return "进入抄底概率模式：这是概率底部观察区，不是趋势买点，也不覆盖原始 Decision Score。"
        if final_view == "normal":
            return "存在极端回撤或反转概率线索，但未满足完整压力触发条件，仍按普通 DecisionSignal 解释。"
        return "未进入抄底概率模式，沿用普通状态感知研究评分解释。"


def contrarian_signal_to_dict(signal: ContrarianSignal) -> dict[str, Any]:
    payload = asdict(signal)
    scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else {}
    adjusted = payload.get("adjusted_interpretation") if isinstance(payload.get("adjusted_interpretation"), dict) else {}
    payload["reversal_probability"] = scores.get("reversal_probability")
    payload["exhaustion_score"] = scores.get("exhaustion_score")
    payload["capitulation_score"] = scores.get("capitulation_score")
    payload["final_view"] = adjusted.get("final_view")
    return payload
