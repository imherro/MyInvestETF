from __future__ import annotations

from dataclasses import dataclass


REFERENCE_BAND_WIDTH = 0.08


@dataclass(frozen=True)
class ETFReferenceValueRange:
    low: float
    mid: float
    high: float
    method: str


def _safe_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return number


def reference_range_from_nav(
    *,
    nav: float,
    valuation_percentile: float,
    premium_discount: float = 0.0,
    band_width: float = REFERENCE_BAND_WIDTH,
) -> ETFReferenceValueRange:
    basis = nav if nav > 0 else 0.0
    percentile = max(0.0, min(100.0, valuation_percentile))
    valuation_adjustment = (50.0 - percentile) / 1000.0
    premium_adjustment = -premium_discount / 2.0
    mid = max(0.0, basis * (1.0 + valuation_adjustment + premium_adjustment))
    low = max(0.0, mid * (1.0 - band_width))
    high = max(low, mid * (1.0 + band_width))
    return ETFReferenceValueRange(low=low, mid=mid, high=high, method="NAV+index-valuation")


def _score(value: object, default: float = 50.0) -> float:
    return max(0.0, min(100.0, _safe_float(value, default)))


def _range_from_basis(*, basis: float, adjustment: float, band_width: float, method: str) -> ETFReferenceValueRange:
    mid = max(0.0, basis * (1.0 + adjustment))
    low = max(0.0, mid * (1.0 - band_width))
    high = max(low, mid * (1.0 + band_width))
    return ETFReferenceValueRange(low=low, mid=mid, high=high, method=method)


def reference_range_from_inputs(
    valuation_inputs: dict[str, object],
    *,
    model_type: str = "broad_index",
    model_specific_inputs: dict[str, object] | None = None,
) -> ETFReferenceValueRange:
    nav = _safe_float(valuation_inputs.get("nav") or valuation_inputs.get("unit_nav"))
    price = _safe_float(valuation_inputs.get("current_price"))
    if nav <= 0:
        nav = price
    specific = model_specific_inputs or {}
    premium_discount = _safe_float(valuation_inputs.get("premium_discount"))
    valuation_percentile = _score(valuation_inputs.get("valuation_percentile"))

    if model_type == "cash_like":
        return _range_from_basis(
            basis=nav,
            adjustment=-premium_discount / 2.0,
            band_width=0.01,
            method="cash-like-liquidity-monitor",
        )

    if model_type == "mainline_theme":
        theme_strength = _score(specific.get("theme_strength"))
        crowding = _score(specific.get("crowding_score"))
        valuation_tolerance = _score(specific.get("valuation_tolerance"))
        adjustment = (theme_strength - 50.0) / 1000.0 + (valuation_tolerance - 50.0) / 1600.0 - crowding / 1800.0 - premium_discount / 2.0
        return _range_from_basis(
            basis=nav,
            adjustment=adjustment,
            band_width=0.12,
            method="theme-strength+valuation-tolerance",
        )

    if model_type == "factor_defensive":
        dividend_spread = _score(specific.get("dividend_spread"))
        fcf_yield = _score(specific.get("fcf_yield"))
        quality = _score(specific.get("quality_score"))
        opportunity_cost = _score(specific.get("style_opportunity_cost"))
        factor_premium = (dividend_spread + fcf_yield + quality) / 3.0
        adjustment = (factor_premium - 50.0) / 1300.0 - opportunity_cost / 2000.0 - premium_discount / 2.0
        return _range_from_basis(
            basis=nav,
            adjustment=adjustment,
            band_width=0.07,
            method="factor-premium+style-opportunity-cost",
        )

    erp = _score(specific.get("equity_risk_premium"))
    roe = _score(specific.get("roe"))
    market_position = _score(specific.get("market_position_score"))
    adjustment = (50.0 - valuation_percentile) / 1000.0 + (erp - 50.0) / 1800.0 + (roe - 50.0) / 2200.0 - max(0.0, market_position - 70.0) / 2500.0 - premium_discount / 2.0
    return _range_from_basis(
        basis=nav,
        adjustment=adjustment,
        band_width=REFERENCE_BAND_WIDTH,
        method="broad-index-valuation+ERP",
    )
