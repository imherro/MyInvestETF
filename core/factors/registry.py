from __future__ import annotations

from dataclasses import asdict
from statistics import pstdev
from typing import Any

from core.risk import PricePoint, build_drawdown_state

from .models import FactorDefinition


def _return_between(points: list[PricePoint], days: int) -> float | None:
    if len(points) <= days:
        return None
    base = points[-days - 1].close
    latest = points[-1].close
    return latest / base - 1.0 if base > 0 else None


def _daily_returns(points: list[PricePoint], days: int) -> list[float]:
    start = max(1, len(points) - days)
    returns = []
    for index in range(start, len(points)):
        previous = points[index - 1].close
        if previous > 0:
            returns.append(points[index].close / previous - 1.0)
    return returns


def _momentum_20(points: list[PricePoint]) -> float | None:
    return _return_between(points, 20)


def _momentum_60(points: list[PricePoint]) -> float | None:
    return _return_between(points, 60)


def _volatility_20(points: list[PricePoint]) -> float | None:
    returns = _daily_returns(points, 20)
    return pstdev(returns) if len(returns) >= 2 else None


def _drawdown_current(points: list[PricePoint]) -> float | None:
    if len(points) < 2:
        return None
    return build_drawdown_state(points).current_drawdown


def _liquidity_trend_20(points: list[PricePoint]) -> float | None:
    amounts = [point.amount for point in points if point.amount is not None and point.amount > 0]
    if len(amounts) < 40:
        return None
    recent = amounts[-20:]
    previous = amounts[-40:-20]
    previous_avg = sum(previous) / len(previous)
    if previous_avg <= 0:
        return None
    return sum(recent) / len(recent) / previous_avg - 1.0


DEFAULT_FACTOR_REGISTRY: tuple[FactorDefinition, ...] = (
    FactorDefinition(
        name="price_momentum_20",
        factor_type="momentum",
        category="price_trend",
        lookback_window=20,
        source="etf_daily_prices.close_price",
        valid_universe=("sector_cyclical", "sector_structural", "theme_lifecycle", "commodity_etf"),
        compute_fn=_momentum_20,
    ),
    FactorDefinition(
        name="price_momentum_60",
        factor_type="momentum",
        category="price_trend",
        lookback_window=60,
        source="etf_daily_prices.close_price",
        valid_universe=("broad_index_core", "broad_index_growth", "broad_index_value", "factor_strategy", "bond_etf"),
        compute_fn=_momentum_60,
    ),
    FactorDefinition(
        name="volatility_20",
        factor_type="risk",
        category="risk_profile",
        lookback_window=20,
        source="etf_daily_prices.close_price",
        valid_universe=(
            "broad_index_core",
            "broad_index_growth",
            "broad_index_value",
            "sector_cyclical",
            "sector_structural",
            "theme_lifecycle",
            "factor_strategy",
            "cash_equivalent",
            "bond_etf",
            "commodity_etf",
        ),
        compute_fn=_volatility_20,
    ),
    FactorDefinition(
        name="drawdown_current",
        factor_type="risk",
        category="drawdown",
        lookback_window=120,
        source="etf_daily_prices.close_price",
        valid_universe=(
            "broad_index_core",
            "broad_index_growth",
            "broad_index_value",
            "sector_cyclical",
            "sector_structural",
            "theme_lifecycle",
            "factor_strategy",
            "cash_equivalent",
            "bond_etf",
            "commodity_etf",
        ),
        compute_fn=_drawdown_current,
    ),
    FactorDefinition(
        name="liquidity_trend_20",
        factor_type="flow",
        category="liquidity",
        lookback_window=40,
        source="etf_daily_prices.amount",
        valid_universe=("sector_cyclical", "sector_structural", "theme_lifecycle", "factor_strategy", "cash_equivalent"),
        compute_fn=_liquidity_trend_20,
    ),
)


def get_factor_definition(name: str) -> FactorDefinition | None:
    for definition in DEFAULT_FACTOR_REGISTRY:
        if definition.name == name:
            return definition
    return None


def select_factor_definitions(taxonomy_type: str | None) -> list[FactorDefinition]:
    if not taxonomy_type:
        return list(DEFAULT_FACTOR_REGISTRY)
    selected = [definition for definition in DEFAULT_FACTOR_REGISTRY if taxonomy_type in definition.valid_universe]
    return selected or list(DEFAULT_FACTOR_REGISTRY)


def factor_definition_to_dict(definition: FactorDefinition) -> dict[str, Any]:
    payload = asdict(definition)
    payload.pop("compute_fn", None)
    return payload
