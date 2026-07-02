from __future__ import annotations

from typing import Literal


ETFType = Literal[
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
]

ThemeLifecycleStage = Literal["early", "expansion", "crowded", "distribution", "collapse"]

ETF_TYPES: tuple[str, ...] = (
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
)

THEME_LIFECYCLE_STAGES: tuple[str, ...] = ("early", "expansion", "crowded", "distribution", "collapse")
