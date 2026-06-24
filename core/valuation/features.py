from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ETFFeatures:
    valuation_percentile: float
    premium_discount: float
    turnover_amount: float
    fund_size: float
    share_change_ratio: float
    tracking_error: float
    concentration_ratio: float


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


def extract_etf_features(input_data: Mapping[str, object]) -> ETFFeatures:
    valuation = input_data.get("valuation_inputs")
    if not isinstance(valuation, Mapping):
        valuation = input_data.get("valuation")
    valuation_map = valuation if isinstance(valuation, Mapping) else {}

    liquidity = input_data.get("liquidity_inputs")
    liquidity_map = liquidity if isinstance(liquidity, Mapping) else {}

    tracking = input_data.get("tracking_inputs")
    tracking_map = tracking if isinstance(tracking, Mapping) else {}

    holdings = input_data.get("holdings_inputs")
    holdings_map = holdings if isinstance(holdings, Mapping) else {}

    return ETFFeatures(
        valuation_percentile=_safe_float(valuation_map.get("valuation_percentile"), 50.0),
        premium_discount=_safe_float(valuation_map.get("premium_discount"), 0.0),
        turnover_amount=_safe_float(liquidity_map.get("turnover_amount")),
        fund_size=_safe_float(liquidity_map.get("fund_size")),
        share_change_ratio=_safe_float(liquidity_map.get("share_change_ratio")),
        tracking_error=_safe_float(tracking_map.get("tracking_error")),
        concentration_ratio=_safe_float(holdings_map.get("concentration_ratio")),
    )
