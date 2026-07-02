from __future__ import annotations

from statistics import mean, pstdev

from core.risk import PricePoint

from .models import FactorDefinition, FactorValue
from .point_in_time import point_in_time_prices


def _percentile(values: list[float], current: float) -> float:
    if not values:
        return 0.0
    return sum(1 for value in values if value <= current) / len(values) * 100.0


def _factor_history(definition: FactorDefinition, points: list[PricePoint], max_window: int = 252) -> list[float]:
    min_points = max(definition.lookback_window + 1, 2)
    values: list[float] = []
    for end_index in range(min_points - 1, len(points)):
        raw = definition.compute_fn(points[: end_index + 1])
        if raw is not None:
            values.append(float(raw))
    return values[-max_window:]


def build_factor_value(
    definition: FactorDefinition,
    *,
    etf_code: str,
    price_series: list[object],
    as_of_date: str | None = None,
    lag_days: int = 1,
    history_window: int = 252,
) -> FactorValue | None:
    del etf_code
    points = point_in_time_prices(price_series, as_of_date=as_of_date, lag_days=lag_days)
    if not points:
        return None
    raw = definition.compute_fn(points)
    if raw is None:
        return None
    history = _factor_history(definition, points, max_window=history_window)
    if not history:
        history = [float(raw)]
    avg = mean(history)
    std = pstdev(history) if len(history) >= 2 else 0.0
    z_score = (float(raw) - avg) / std if std > 0 else 0.0
    percentile = _percentile(history, float(raw))
    return FactorValue(
        factor_name=definition.name,
        factor_type=definition.factor_type,
        raw_value=round(float(raw), 6),
        normalized_value=round(percentile / 100.0, 6),
        z_score=round(z_score, 6),
        percentile=round(percentile, 6),
        as_of_date=points[-1].trade_date,
        lookback_window=definition.lookback_window,
        source=definition.source,
        leakage_guard=f"point_in_time_lag_{lag_days}",
    )
