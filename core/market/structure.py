from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from statistics import mean, pstdev
from typing import Any

from core.risk import PricePoint, normalize_price_series


@dataclass(frozen=True)
class MarketStructure:
    as_of_date: str | None
    index_breadth: float
    sector_breadth: float
    advance_decline_ratio: float
    liquidity_breadth: float
    dispersion: float
    breadth_score: float
    liquidity_score: float
    dispersion_score: float
    observations: int
    contributions: dict[str, float]
    data_gaps: list[str]


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _date_key(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits or value


def _latest_return(points: list[PricePoint]) -> float | None:
    if len(points) < 2:
        return None
    previous = points[-2].close
    return points[-1].close / previous - 1.0 if previous > 0 else None


def _liquidity_expanded(points: list[PricePoint]) -> bool | None:
    amounts = [point.amount for point in points if point.amount is not None and point.amount > 0]
    if len(amounts) < 25:
        return None
    recent = amounts[-5:]
    previous = amounts[-25:-5]
    previous_avg = mean(previous)
    if previous_avg <= 0:
        return None
    return mean(recent) / previous_avg > 1.0


def _taxonomy_type(taxonomy_by_code: Mapping[str, object] | None, code: str) -> str:
    if not taxonomy_by_code:
        return "unknown"
    raw = taxonomy_by_code.get(code)
    if isinstance(raw, Mapping):
        return str(raw.get("etf_type") or "unknown")
    return str(raw or "unknown")


def build_market_structure(
    price_series_by_code: Mapping[str, list[object]],
    taxonomy_by_code: Mapping[str, object] | None = None,
) -> MarketStructure:
    returns_by_code: dict[str, float] = {}
    liquidity_flags: list[bool] = []
    as_of_dates: list[str] = []
    data_gaps: list[str] = []
    for code, rows in price_series_by_code.items():
        points = normalize_price_series(rows)
        if not points:
            data_gaps.append(f"{code}: missing price series")
            continue
        as_of_dates.append(points[-1].trade_date)
        latest_return = _latest_return(points)
        if latest_return is not None:
            returns_by_code[code] = latest_return
        liquidity_flag = _liquidity_expanded(points)
        if liquidity_flag is not None:
            liquidity_flags.append(liquidity_flag)

    observations = len(returns_by_code)
    if observations == 0:
        return MarketStructure(
            as_of_date=None,
            index_breadth=0.5,
            sector_breadth=0.5,
            advance_decline_ratio=1.0,
            liquidity_breadth=0.5,
            dispersion=0.0,
            breadth_score=0.5,
            liquidity_score=0.5,
            dispersion_score=0.5,
            observations=0,
            contributions={"breadth": 0.5, "liquidity": 0.5, "dispersion": 0.5},
            data_gaps=data_gaps or ["missing ETF price universe"],
        )

    up_count = sum(1 for value in returns_by_code.values() if value > 0)
    down_count = sum(1 for value in returns_by_code.values() if value < 0)
    index_breadth = up_count / observations
    advance_decline_ratio = up_count / max(down_count, 1)

    grouped: dict[str, list[float]] = {}
    for code, value in returns_by_code.items():
        grouped.setdefault(_taxonomy_type(taxonomy_by_code, code), []).append(value)
    positive_groups = sum(1 for values in grouped.values() if values and mean(values) > 0)
    sector_breadth = positive_groups / len(grouped) if grouped else index_breadth

    liquidity_breadth = sum(1 for flag in liquidity_flags if flag) / len(liquidity_flags) if liquidity_flags else 0.5
    dispersion = pstdev(list(returns_by_code.values())) if observations >= 2 else 0.0
    dispersion_score = 1.0 - _clamp(dispersion / 0.035)
    breadth_score = _clamp(index_breadth * 0.6 + sector_breadth * 0.4)
    liquidity_score = _clamp(liquidity_breadth)

    return MarketStructure(
        as_of_date=max(as_of_dates, key=_date_key) if as_of_dates else None,
        index_breadth=round(index_breadth, 6),
        sector_breadth=round(sector_breadth, 6),
        advance_decline_ratio=round(advance_decline_ratio, 6),
        liquidity_breadth=round(liquidity_breadth, 6),
        dispersion=round(dispersion, 6),
        breadth_score=round(breadth_score, 6),
        liquidity_score=round(liquidity_score, 6),
        dispersion_score=round(dispersion_score, 6),
        observations=observations,
        contributions={
            "breadth": round(breadth_score, 6),
            "liquidity": round(liquidity_score, 6),
            "dispersion": round(dispersion_score, 6),
        },
        data_gaps=data_gaps,
    )


def market_structure_to_dict(structure: MarketStructure) -> dict[str, Any]:
    return asdict(structure)
