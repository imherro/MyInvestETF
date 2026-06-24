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


def reference_range_from_inputs(valuation_inputs: dict[str, object]) -> ETFReferenceValueRange:
    nav = _safe_float(valuation_inputs.get("nav") or valuation_inputs.get("unit_nav"))
    price = _safe_float(valuation_inputs.get("current_price"))
    if nav <= 0:
        nav = price
    return reference_range_from_nav(
        nav=nav,
        valuation_percentile=_safe_float(valuation_inputs.get("valuation_percentile"), 50.0),
        premium_discount=_safe_float(valuation_inputs.get("premium_discount")),
    )
