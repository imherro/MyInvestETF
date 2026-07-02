from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Literal


ComponentType = Literal["momentum", "flow", "valuation", "risk"]
COMPONENT_TYPES: tuple[ComponentType, ...] = ("momentum", "flow", "valuation", "risk")

BASE_WEIGHT_MATRIX: dict[str, dict[ComponentType, float]] = {
    "risk_on": {"momentum": 0.50, "flow": 0.30, "valuation": 0.10, "risk": 0.10},
    "risk_off": {"momentum": 0.10, "flow": 0.20, "valuation": 0.40, "risk": 0.30},
    "shock": {"momentum": 0.10, "flow": 0.30, "valuation": 0.20, "risk": 0.40},
    "rotation": {"momentum": 0.25, "flow": 0.25, "valuation": 0.30, "risk": 0.20},
}

REGIME_FACTOR_EFFECTIVENESS: dict[str, dict[ComponentType, float]] = {
    "risk_on": {"momentum": 1.20, "flow": 1.10, "valuation": 0.75, "risk": 0.85},
    "risk_off": {"momentum": 0.70, "flow": 0.90, "valuation": 1.25, "risk": 1.20},
    "shock": {"momentum": 0.60, "flow": 1.15, "valuation": 0.90, "risk": 1.35},
    "rotation": {"momentum": 0.90, "flow": 1.00, "valuation": 1.05, "risk": 1.05},
}

TAXONOMY_WEIGHT_ADJUSTMENTS: dict[str, dict[ComponentType, float]] = {
    "broad_index_core": {"momentum": -0.04, "flow": -0.04, "valuation": 0.06, "risk": 0.02},
    "broad_index_growth": {"momentum": 0.04, "flow": -0.02, "valuation": 0.02, "risk": -0.04},
    "broad_index_value": {"momentum": -0.05, "flow": -0.02, "valuation": 0.09, "risk": -0.02},
    "sector_cyclical": {"momentum": 0.06, "flow": 0.04, "valuation": -0.03, "risk": -0.07},
    "sector_structural": {"momentum": 0.08, "flow": 0.03, "valuation": -0.05, "risk": -0.06},
    "theme_lifecycle": {"momentum": 0.10, "flow": 0.07, "valuation": -0.08, "risk": -0.09},
    "factor_strategy": {"momentum": -0.06, "flow": -0.05, "valuation": 0.10, "risk": 0.01},
    "cash_equivalent": {"momentum": -0.25, "flow": 0.10, "valuation": -0.10, "risk": 0.25},
    "bond_etf": {"momentum": -0.10, "flow": 0.02, "valuation": 0.03, "risk": 0.05},
    "commodity_etf": {"momentum": 0.08, "flow": 0.02, "valuation": -0.05, "risk": -0.05},
}


@dataclass(frozen=True)
class DecisionState:
    regime: str
    score_band: str
    trend_state: str
    taxonomy_type: str | None
    state_code: str
    explanation: str


@dataclass(frozen=True)
class DecisionSignal:
    etf_code: str
    score: float
    regime: dict[str, Any]
    taxonomy_type: str | None
    component_scores: dict[str, float]
    factor_contributions: dict[str, float]
    adjusted_weights: dict[str, float]
    factor_effectiveness: dict[str, float]
    state: DecisionState
    explanation: str
    confidence: float
    inputs: dict[str, Any]
    constraints: dict[str, bool]


def _mapping(value: object | None) -> dict[str, Any]:
    if value is None:
        return {}
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _safe_float(value: object, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return number


def _unit_from_score(value: object, default: float | None = None) -> float | None:
    number = _safe_float(value)
    if number is None:
        return default
    return _clamp(number / 100.0)


def _normalize(weights: Mapping[ComponentType, float]) -> dict[str, float]:
    clipped = {name: max(0.02, float(weights.get(name, 0.0))) for name in COMPONENT_TYPES}
    total = sum(clipped.values())
    if total <= 0:
        return {name: round(1.0 / len(COMPONENT_TYPES), 6) for name in COMPONENT_TYPES}
    return {name: round(clipped[name] / total, 6) for name in COMPONENT_TYPES}


def _regime_value(regime: Mapping[str, Any]) -> str:
    value = str(regime.get("regime") or "rotation")
    return value if value in BASE_WEIGHT_MATRIX else "rotation"


def _taxonomy_type(taxonomy_profile: Mapping[str, Any]) -> str | None:
    value = str(taxonomy_profile.get("etf_type") or "")
    return value or None


def build_adjusted_weights(regime: str, taxonomy_type: str | None) -> dict[str, float]:
    base = dict(BASE_WEIGHT_MATRIX.get(regime, BASE_WEIGHT_MATRIX["rotation"]))
    adjustments = TAXONOMY_WEIGHT_ADJUSTMENTS.get(taxonomy_type or "", {})
    taxonomy_adjusted = {
        name: base[name] + float(adjustments.get(name, 0.0))
        for name in COMPONENT_TYPES
    }
    effectiveness = REGIME_FACTOR_EFFECTIVENESS.get(regime, REGIME_FACTOR_EFFECTIVENESS["rotation"])
    effective = {
        name: taxonomy_adjusted[name] * effectiveness[name]
        for name in COMPONENT_TYPES
    }
    return _normalize(effective)


def _factor_rows(factor_exposure: object | None) -> list[dict[str, Any]]:
    exposure = _mapping(factor_exposure)
    rows = exposure.get("factors")
    if not isinstance(rows, list):
        return []
    return [_mapping(row) for row in rows]


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _factor_component_score(rows: list[dict[str, Any]], factor_type: str) -> float | None:
    values: list[float] = []
    for row in rows:
        if str(row.get("factor_type") or "") != factor_type:
            continue
        value = _safe_float(row.get("normalized_value"))
        if value is None:
            continue
        if factor_type == "risk":
            value = 1.0 - value
        values.append(_clamp(value))
    return _average(values)


def _valuation_component_score(valuation_signal: Mapping[str, Any], taxonomy_type: str | None) -> tuple[float, str]:
    model_type = str(valuation_signal.get("valuation_model_type") or "")
    candidates: list[tuple[str, object]] = []
    if model_type == "mainline_theme" or taxonomy_type == "theme_lifecycle":
        candidates.extend(
            [
                ("valuation_tolerance_score", valuation_signal.get("valuation_tolerance_score")),
                ("undervalued_score", valuation_signal.get("undervalued_score")),
            ]
        )
    elif model_type == "factor_defensive" or taxonomy_type == "factor_strategy":
        candidates.extend(
            [
                ("factor_premium_score", valuation_signal.get("factor_premium_score")),
                ("undervalued_score", valuation_signal.get("undervalued_score")),
            ]
        )
    elif model_type == "cash_like" or taxonomy_type == "cash_equivalent":
        candidates.extend(
            [
                ("cash_like_safety_score", valuation_signal.get("cash_like_safety_score")),
                ("risk_adjusted_score", valuation_signal.get("risk_adjusted_score")),
            ]
        )
    else:
        candidates.extend(
            [
                ("undervalued_score", valuation_signal.get("undervalued_score")),
                ("risk_adjusted_score", valuation_signal.get("risk_adjusted_score")),
            ]
        )
    for source, value in candidates:
        unit = _unit_from_score(value)
        if unit is not None:
            return unit, source
    return 0.50, "default_neutral_valuation"


def _component_scores(
    *,
    factor_exposure: object | None,
    regime: Mapping[str, Any],
    taxonomy_type: str | None,
    valuation_signal: Mapping[str, Any],
) -> tuple[dict[str, float], dict[str, Any]]:
    rows = _factor_rows(factor_exposure)
    structure = regime.get("structure") if isinstance(regime.get("structure"), Mapping) else {}
    trend_fallback = _safe_float(_mapping(structure).get("price_trend_score"), 0.50)
    momentum = _factor_component_score(rows, "momentum")
    flow = _factor_component_score(rows, "flow")
    flow_fallback = _unit_from_score(valuation_signal.get("liquidity_score"))
    risk = _factor_component_score(rows, "risk")
    valuation_risk = _unit_from_score(valuation_signal.get("risk_adjusted_score"))
    valuation, valuation_source = _valuation_component_score(valuation_signal, taxonomy_type)

    if momentum is None:
        momentum = trend_fallback if trend_fallback is not None else 0.50
    if flow is None:
        flow = flow_fallback if flow_fallback is not None else 0.50
    if risk is None:
        risk = valuation_risk if valuation_risk is not None else 0.50
    elif valuation_risk is not None:
        risk = risk * 0.60 + valuation_risk * 0.40

    scores = {
        "momentum": round(_clamp(momentum) * 100.0, 6),
        "flow": round(_clamp(flow) * 100.0, 6),
        "valuation": round(_clamp(valuation) * 100.0, 6),
        "risk": round(_clamp(risk) * 100.0, 6),
    }
    sources = {
        "factor_count": len(rows),
        "factor_names_by_type": {
            name: [str(row.get("factor_name")) for row in rows if row.get("factor_type") == name]
            for name in COMPONENT_TYPES
        },
        "fallbacks": {
            "momentum": "regime.structure.price_trend_score" if not sources_for_type(rows, "momentum") else "factor_exposure",
            "flow": "valuation_signal.liquidity_score" if not sources_for_type(rows, "flow") else "factor_exposure",
            "risk": "valuation_signal.risk_adjusted_score" if not sources_for_type(rows, "risk") else "factor_exposure+valuation_signal",
            "valuation": valuation_source,
        },
    }
    return scores, sources


def sources_for_type(rows: list[dict[str, Any]], factor_type: str) -> list[str]:
    return [str(row.get("factor_name")) for row in rows if str(row.get("factor_type") or "") == factor_type]


def _score_band(score: float) -> str:
    if score >= 70.0:
        return "strong"
    if score >= 55.0:
        return "watch"
    if score >= 40.0:
        return "weak"
    return "avoid_research"


def _trend_state(component_scores: Mapping[str, float], regime: Mapping[str, Any]) -> str:
    structure = regime.get("structure") if isinstance(regime.get("structure"), Mapping) else {}
    trend = _safe_float(_mapping(structure).get("price_trend_score"))
    if trend is None:
        trend = _clamp(float(component_scores.get("momentum", 50.0)) / 100.0)
    if trend >= 0.60:
        return "uptrend"
    if trend <= 0.40:
        return "downtrend"
    return "range"


def _state_explanation(regime: str, score_band: str, trend_state: str, taxonomy_type: str | None) -> str:
    return (
        f"regime={regime}, score_band={score_band}, trend_state={trend_state}, "
        f"taxonomy={taxonomy_type or 'unknown'}; scoring uses regime/taxonomy-adjusted weights only for research ranking."
    )


def _confidence(
    *,
    regime: Mapping[str, Any],
    taxonomy_profile: Mapping[str, Any],
    factor_count: int,
) -> float:
    regime_confidence = _safe_float(regime.get("confidence"), 0.45) or 0.45
    taxonomy_confidence = _safe_float(taxonomy_profile.get("classification_confidence"), 0.55) or 0.55
    factor_coverage = _clamp(factor_count / 4.0)
    return round(_clamp(regime_confidence * 0.40 + taxonomy_confidence * 0.30 + factor_coverage * 0.30), 6)


def build_decision_signal(
    *,
    etf_code: str,
    factor_exposure: object | None,
    market_regime: object | None,
    taxonomy_profile: object | None,
    valuation_signal: object | None = None,
) -> DecisionSignal:
    regime = _mapping(market_regime)
    taxonomy = _mapping(taxonomy_profile)
    valuation = _mapping(valuation_signal)
    regime_name = _regime_value(regime)
    taxonomy_type = _taxonomy_type(taxonomy)
    adjusted_weights = build_adjusted_weights(regime_name, taxonomy_type)
    component_scores, input_sources = _component_scores(
        factor_exposure=factor_exposure,
        regime=regime,
        taxonomy_type=taxonomy_type,
        valuation_signal=valuation,
    )
    contributions = {
        name: round(component_scores[name] * adjusted_weights[name], 6)
        for name in COMPONENT_TYPES
    }
    score = round(sum(contributions.values()), 6)
    score_band = _score_band(score)
    trend = _trend_state(component_scores, regime)
    state = DecisionState(
        regime=regime_name,
        score_band=score_band,
        trend_state=trend,
        taxonomy_type=taxonomy_type,
        state_code=f"{regime_name}:{score_band}:{trend}",
        explanation=_state_explanation(regime_name, score_band, trend, taxonomy_type),
    )
    top_component = max(contributions, key=contributions.get)
    explanation = (
        f"Score {score:.2f} is driven by {top_component} under {regime_name}; "
        f"weights are adjusted by taxonomy {taxonomy_type or 'unknown'} and regime-factor effectiveness."
    )
    factor_count = int(input_sources.get("factor_count") or 0)
    return DecisionSignal(
        etf_code=etf_code,
        score=score,
        regime=regime,
        taxonomy_type=taxonomy_type,
        component_scores=component_scores,
        factor_contributions=contributions,
        adjusted_weights=adjusted_weights,
        factor_effectiveness={
            name: round(REGIME_FACTOR_EFFECTIVENESS.get(regime_name, REGIME_FACTOR_EFFECTIVENESS["rotation"])[name], 6)
            for name in COMPONENT_TYPES
        },
        state=state,
        explanation=explanation,
        confidence=_confidence(regime=regime, taxonomy_profile=taxonomy, factor_count=factor_count),
        inputs=input_sources,
        constraints={
            "read_only": True,
            "research_only": True,
            "contains_trade_orders": False,
            "contains_cash_amounts": False,
            "contains_share_counts": False,
            "executes_rebalance": False,
        },
    )


def decision_signal_to_dict(signal: DecisionSignal) -> dict[str, Any]:
    return asdict(signal)
