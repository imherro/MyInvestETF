from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from statistics import mean, pstdev
from typing import Any

from core.decision import build_decision_signal
from core.factors import build_factor_exposure, factor_exposure_to_dict
from core.market import build_market_regime_v2, build_market_structure, market_regime_v2_to_dict
from core.risk import PricePoint, build_drawdown_state, normalize_price_series


@dataclass(frozen=True)
class ReplayPoint:
    date: str
    score: float
    regime: str
    taxonomy_type: str | None
    score_band: str
    trend_state: str
    confidence: float
    component_scores: dict[str, float]
    factor_contributions: dict[str, float]
    adjusted_weights: dict[str, float]
    current_drawdown: float
    input_window_end: str | None
    factor_as_of_date: str | None
    no_future_data: bool


@dataclass(frozen=True)
class ReplayReport:
    etf: str
    taxonomy_type: str | None
    time_series: dict[str, object]
    stability: dict[str, object]
    drawdown_sensitivity: dict[str, object]
    consistency_score: float
    validation: dict[str, object]
    points: list[ReplayPoint]
    constraints: dict[str, bool]


def _date_key(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits or str(value)


def _safe_float(value: object, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return number


def _as_mapping(value: object | None) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _points_until(points: list[PricePoint], as_of_date: str) -> list[PricePoint]:
    as_of_key = _date_key(as_of_date)
    return [point for point in points if _date_key(point.trade_date) <= as_of_key]


def _sample_timeline(timeline: list[str], max_points: int) -> list[str]:
    if max_points <= 0 or len(timeline) <= max_points:
        return timeline
    if max_points == 1:
        return [timeline[-1]]
    last_index = len(timeline) - 1
    indexes = {
        round(index * last_index / (max_points - 1))
        for index in range(max_points)
    }
    indexes.add(last_index)
    return [timeline[index] for index in sorted(indexes)]


def _last_date(points: list[PricePoint]) -> str | None:
    return points[-1].trade_date if points else None


def _valuation_for_date(
    valuation_signal: Mapping[str, object] | None,
    *,
    as_of_date: str,
    valuation_as_of_date: str | None,
) -> dict[str, object]:
    if not valuation_signal:
        return {}
    if valuation_as_of_date and _date_key(as_of_date) >= _date_key(valuation_as_of_date):
        return dict(valuation_signal)
    model_type = str(valuation_signal.get("valuation_model_type") or "")
    return {
        "valuation_model_type": model_type or None,
        "undervalued_score": 50.0,
        "valuation_tolerance_score": 50.0,
        "factor_premium_score": 50.0,
        "cash_like_safety_score": 50.0,
        "liquidity_score": 50.0,
        "risk_adjusted_score": 50.0,
        "source": "neutral_valuation_before_research_date",
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    x_mean = mean(xs)
    y_mean = mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    x_var = sum((x - x_mean) ** 2 for x in xs)
    y_var = sum((y - y_mean) ** 2 for y in ys)
    if x_var <= 0 or y_var <= 0:
        return None
    return numerator / (x_var**0.5 * y_var**0.5)


def _regime_durations(regimes: list[str]) -> list[dict[str, object]]:
    if not regimes:
        return []
    durations: list[dict[str, object]] = []
    current = regimes[0]
    duration = 1
    for value in regimes[1:]:
        if value == current:
            duration += 1
            continue
        durations.append({"regime": current, "duration": duration})
        current = value
        duration = 1
    durations.append({"regime": current, "duration": duration})
    return durations


def _transition_matrix(regimes: list[str]) -> dict[str, dict[str, float]]:
    counts: dict[str, dict[str, int]] = {}
    for previous, current in zip(regimes, regimes[1:]):
        counts.setdefault(previous, {})
        counts[previous][current] = counts[previous].get(current, 0) + 1
    matrix: dict[str, dict[str, float]] = {}
    for source, targets in counts.items():
        total = sum(targets.values())
        matrix[source] = {target: round(count / total, 6) for target, count in targets.items()} if total else {}
    return matrix


def _factor_stability(points: list[ReplayPoint]) -> dict[str, object]:
    components = ("momentum", "flow", "valuation", "risk")
    lag1_ic: dict[str, float | None] = {}
    volatility: dict[str, float] = {}
    for component in components:
        series = [float(point.factor_contributions.get(component, 0.0)) for point in points]
        lag1_ic[component] = round(_pearson(series[:-1], series[1:]), 6) if len(series) >= 4 and _pearson(series[:-1], series[1:]) is not None else None
        volatility[component] = round(pstdev(series), 6) if len(series) >= 2 else 0.0
    return {"lag1_component_ic": lag1_ic, "contribution_volatility": volatility}


def _stability(points: list[ReplayPoint]) -> dict[str, object]:
    scores = [point.score for point in points]
    regimes = [point.regime for point in points]
    flips = sum(1 for previous, current in zip(regimes, regimes[1:]) if previous != current)
    dominant_count = 0
    for point in points:
        contribution_total = sum(abs(value) for value in point.factor_contributions.values())
        if contribution_total > 0 and max(abs(value) for value in point.factor_contributions.values()) / contribution_total >= 0.60:
            dominant_count += 1
    return {
        "score_mean": round(mean(scores), 6) if scores else None,
        "score_std": round(pstdev(scores), 6) if len(scores) >= 2 else 0.0,
        "score_range": round(max(scores) - min(scores), 6) if scores else None,
        "regime_flip_rate": round(flips / max(len(regimes) - 1, 1), 6) if regimes else None,
        "regime_duration_distribution": _regime_durations(regimes),
        "regime_transition_matrix": _transition_matrix(regimes),
        "dominant_factor_rate": round(dominant_count / len(points), 6) if points else None,
        "factor_stability_ic": _factor_stability(points),
        "taxonomy_consistency_drift": 0.0,
        "observations": len(points),
    }


def _drawdown_sensitivity(points: list[ReplayPoint]) -> dict[str, object]:
    scores = [point.score for point in points]
    drawdowns = [point.current_drawdown for point in points]
    correlation = _pearson(scores, drawdowns)
    return {
        "score_vs_drawdown_correlation": round(correlation, 6) if correlation is not None else None,
        "max_drawdown_observed": round(max(drawdowns), 6) if drawdowns else None,
        "observations": len(points),
    }


def _consistency_score(stability: Mapping[str, object], validation: Mapping[str, object]) -> float:
    score_std = _safe_float(stability.get("score_std"), 0.0) or 0.0
    flip_rate = _safe_float(stability.get("regime_flip_rate"), 0.0) or 0.0
    dominant_rate = _safe_float(stability.get("dominant_factor_rate"), 0.0) or 0.0
    taxonomy_drift = _safe_float(stability.get("taxonomy_consistency_drift"), 0.0) or 0.0
    violation_penalty = 35.0 if validation.get("violations") else 0.0
    raw = 100.0 - score_std * 1.2 - flip_rate * 35.0 - dominant_rate * 15.0 - taxonomy_drift * 40.0 - violation_penalty
    return round(max(0.0, min(100.0, raw)), 6)


def build_replay_report(
    *,
    etf_code: str,
    price_series_by_code: Mapping[str, list[object]],
    taxonomy_by_code: Mapping[str, object] | None = None,
    valuation_signal: Mapping[str, object] | None = None,
    valuation_as_of_date: str | None = None,
    min_observations: int = 45,
    max_points: int = 180,
) -> ReplayReport:
    normalized_by_code = {
        code: normalize_price_series(rows)
        for code, rows in price_series_by_code.items()
    }
    etf_points = normalized_by_code.get(etf_code, [])
    taxonomy_map = {code: _as_mapping(value) for code, value in (taxonomy_by_code or {}).items()}
    taxonomy_profile = taxonomy_map.get(etf_code, {})
    taxonomy_type = str(taxonomy_profile.get("etf_type") or "") or None
    timeline = [point.trade_date for point in etf_points[min_observations - 1 :]]
    timeline = _sample_timeline(timeline, max_points)

    replay_points: list[ReplayPoint] = []
    violations: list[str] = []
    for as_of_date in timeline:
        as_of_prices = {
            code: _points_until(points, as_of_date)
            for code, points in normalized_by_code.items()
        }
        etf_as_of_prices = as_of_prices.get(etf_code, [])
        if len(etf_as_of_prices) < min_observations:
            continue
        structure = build_market_structure(as_of_prices, taxonomy_map)
        regime = build_market_regime_v2(etf_code, etf_as_of_prices, structure)
        exposure = factor_exposure_to_dict(
            build_factor_exposure(
                etf_code=etf_code,
                price_series=etf_as_of_prices,
                taxonomy_profile=taxonomy_profile,
                as_of_date=as_of_date,
                lag_days=1,
            )
        )
        valuation_for_date = _valuation_for_date(
            valuation_signal,
            as_of_date=as_of_date,
            valuation_as_of_date=valuation_as_of_date,
        )
        decision = build_decision_signal(
            etf_code=etf_code,
            factor_exposure=exposure,
            market_regime=regime,
            taxonomy_profile=taxonomy_profile,
            valuation_signal=valuation_for_date,
        )
        drawdown = build_drawdown_state(etf_as_of_prices)
        input_window_end = _last_date(etf_as_of_prices)
        factor_as_of = exposure.get("as_of_date")
        no_future = True
        if input_window_end is not None and _date_key(input_window_end) > _date_key(as_of_date):
            no_future = False
            violations.append(f"{as_of_date}: price input window ends after as_of_date")
        if factor_as_of is not None and _date_key(str(factor_as_of)) > _date_key(as_of_date):
            no_future = False
            violations.append(f"{as_of_date}: factor as_of_date after replay date")
        replay_points.append(
            ReplayPoint(
                date=as_of_date,
                score=decision.score,
                regime=decision.state.regime,
                taxonomy_type=decision.taxonomy_type,
                score_band=decision.state.score_band,
                trend_state=decision.state.trend_state,
                confidence=decision.confidence,
                component_scores=decision.component_scores,
                factor_contributions=decision.factor_contributions,
                adjusted_weights=decision.adjusted_weights,
                current_drawdown=drawdown.current_drawdown,
                input_window_end=input_window_end,
                factor_as_of_date=str(factor_as_of) if factor_as_of is not None else None,
                no_future_data=no_future,
            )
        )

    stability = _stability(replay_points)
    validation = {
        "as_of_enforced": True,
        "no_future_data": not violations,
        "violations": violations,
        "valuation_policy": (
            "valuation_signal is used only on or after valuation_as_of_date; earlier replay dates use neutral valuation"
        ),
        "valuation_as_of_date": valuation_as_of_date,
        "recomputed_historical_consistency": True,
        "minimum_observations": min_observations,
    }
    time_series = {
        "score_series": [{"date": point.date, "score": point.score} for point in replay_points],
        "regime_series": [{"date": point.date, "regime": point.regime} for point in replay_points],
        "factor_series": [
            {"date": point.date, "factor_contributions": point.factor_contributions}
            for point in replay_points
        ],
    }
    drawdown_sensitivity = _drawdown_sensitivity(replay_points)
    consistency = _consistency_score(stability, validation)
    return ReplayReport(
        etf=etf_code,
        taxonomy_type=taxonomy_type,
        time_series=time_series,
        stability=stability,
        drawdown_sensitivity=drawdown_sensitivity,
        consistency_score=consistency,
        validation=validation,
        points=replay_points,
        constraints={
            "read_only": True,
            "research_only": True,
            "contains_trade_orders": False,
            "contains_cash_amounts": False,
            "contains_share_counts": False,
            "executes_rebalance": False,
        },
    )


def replay_report_to_dict(report: ReplayReport) -> dict[str, Any]:
    return asdict(report)
