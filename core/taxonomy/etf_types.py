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

TAXONOMY_TYPES_BY_VALUATION_MODEL: dict[str, frozenset[str]] = {
    "broad_index": frozenset({"broad_index_core", "broad_index_growth", "broad_index_value"}),
    "mainline_theme": frozenset({"theme_lifecycle", "sector_cyclical", "sector_structural"}),
    "factor_defensive": frozenset({"factor_strategy"}),
    "cash_like": frozenset({"cash_equivalent"}),
}


def taxonomy_type_matches_valuation_model(etf_type: object, valuation_model_type: object) -> bool:
    model = str(valuation_model_type or "")
    if not model:
        return True
    allowed = TAXONOMY_TYPES_BY_VALUATION_MODEL.get(model)
    if allowed is None:
        return True
    return str(etf_type or "") in allowed
