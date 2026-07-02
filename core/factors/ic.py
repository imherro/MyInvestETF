from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean, pstdev
from typing import Any

from core.risk import PricePoint, normalize_price_series

from .models import FactorDefinition


@dataclass(frozen=True)
class FactorICSummary:
    factor_name: str
    horizon_days: int
    ic_mean: float
    ic_std: float
    ic_decay: float
    observations: int
    as_of_date: str | None
    leakage_guard: str


def _pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3 or len(xs) != len(ys):
        return 0.0
    x_mean = mean(xs)
    y_mean = mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True))
    x_var = sum((x - x_mean) ** 2 for x in xs)
    y_var = sum((y - y_mean) ** 2 for y in ys)
    denominator = (x_var * y_var) ** 0.5
    return numerator / denominator if denominator > 0 else 0.0


def _ic_pairs(definition: FactorDefinition, points: list[PricePoint], horizon_days: int) -> tuple[list[float], list[float]]:
    min_points = max(definition.lookback_window + 1, 2)
    factor_values: list[float] = []
    forward_returns: list[float] = []
    last_factor_index = len(points) - horizon_days - 1
    for index in range(min_points - 1, last_factor_index + 1):
        raw = definition.compute_fn(points[: index + 1])
        current = points[index].close
        future = points[index + horizon_days].close
        if raw is None or current <= 0:
            continue
        factor_values.append(float(raw))
        forward_returns.append(future / current - 1.0)
    return factor_values, forward_returns


def _chunk_ic(xs: list[float], ys: list[float], chunk_size: int = 40) -> list[float]:
    chunks: list[float] = []
    for start in range(0, len(xs), chunk_size):
        chunk_x = xs[start : start + chunk_size]
        chunk_y = ys[start : start + chunk_size]
        if len(chunk_x) >= 5:
            chunks.append(_pearson(chunk_x, chunk_y))
    return chunks


def compute_factor_ic(
    definition: FactorDefinition,
    price_series_by_code: dict[str, list[object]],
    *,
    horizons: tuple[int, ...] = (5, 20, 60),
) -> list[FactorICSummary]:
    summaries: list[FactorICSummary] = []
    previous_abs_ic: float | None = None
    as_of_dates = []
    for horizon in horizons:
        all_factor_values: list[float] = []
        all_forward_returns: list[float] = []
        for rows in price_series_by_code.values():
            points = normalize_price_series(rows)
            if points:
                as_of_dates.append(points[-1].trade_date)
            xs, ys = _ic_pairs(definition, points, horizon)
            all_factor_values.extend(xs)
            all_forward_returns.extend(ys)
        ic = _pearson(all_factor_values, all_forward_returns)
        chunks = _chunk_ic(all_factor_values, all_forward_returns)
        ic_std = pstdev(chunks) if len(chunks) >= 2 else 0.0
        abs_ic = abs(ic)
        decay = 0.0 if previous_abs_ic is None else previous_abs_ic - abs_ic
        previous_abs_ic = abs_ic
        summaries.append(
            FactorICSummary(
                factor_name=definition.name,
                horizon_days=horizon,
                ic_mean=round(ic, 6),
                ic_std=round(ic_std, 6),
                ic_decay=round(decay, 6),
                observations=len(all_factor_values),
                as_of_date=max(as_of_dates) if as_of_dates else None,
                leakage_guard="factor_date_before_forward_return_window",
            )
        )
    return summaries


def factor_ic_summary_to_dict(summary: FactorICSummary) -> dict[str, Any]:
    return asdict(summary)
