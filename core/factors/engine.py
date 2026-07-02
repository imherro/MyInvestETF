from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .models import FactorExposure, FactorValue
from .registry import select_factor_definitions
from .standardization import build_factor_value


def _factor_attribution(factors: list[FactorValue]) -> dict[str, float]:
    if not factors:
        return {}
    total = sum(abs(factor.z_score) for factor in factors)
    if total <= 0:
        equal = round(1.0 / len(factors), 6)
        return {factor.factor_name: equal for factor in factors}
    return {factor.factor_name: round(abs(factor.z_score) / total, 6) for factor in factors}


def build_factor_exposure(
    *,
    etf_code: str,
    price_series: list[object],
    taxonomy_profile: dict[str, object] | None = None,
    as_of_date: str | None = None,
    lag_days: int = 1,
) -> FactorExposure:
    taxonomy_type = str((taxonomy_profile or {}).get("etf_type") or "") or None
    definitions = select_factor_definitions(taxonomy_type)
    factors = [
        value
        for definition in definitions
        if (
            value := build_factor_value(
                definition,
                etf_code=etf_code,
                price_series=price_series,
                as_of_date=as_of_date,
                lag_days=lag_days,
            )
        )
        is not None
    ]
    return FactorExposure(
        etf_code=etf_code,
        as_of_date=factors[0].as_of_date if factors else None,
        taxonomy_type=taxonomy_type,
        selected_factor_names=[definition.name for definition in definitions],
        factors=factors,
        attribution=_factor_attribution(factors),
        leakage_guard=f"point_in_time_lag_{lag_days}",
    )


def factor_exposure_to_dict(exposure: FactorExposure) -> dict[str, Any]:
    return asdict(exposure)
