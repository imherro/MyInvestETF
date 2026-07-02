from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from core.risk import PricePoint


FactorType = Literal["momentum", "flow", "risk", "valuation", "style"]


@dataclass(frozen=True)
class FactorValue:
    factor_name: str
    factor_type: FactorType
    raw_value: float
    normalized_value: float
    z_score: float
    percentile: float
    as_of_date: str
    lookback_window: int
    source: str
    leakage_guard: str


@dataclass(frozen=True)
class FactorDefinition:
    name: str
    factor_type: FactorType
    category: str
    lookback_window: int
    source: str
    valid_universe: tuple[str, ...]
    compute_fn: Callable[[list[PricePoint]], float | None]


@dataclass(frozen=True)
class FactorExposure:
    etf_code: str
    as_of_date: str | None
    taxonomy_type: str | None
    selected_factor_names: list[str]
    factors: list[FactorValue]
    attribution: dict[str, float]
    leakage_guard: str
