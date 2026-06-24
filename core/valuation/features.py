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
    equity_risk_premium: float
    roe: float
    market_position_score: float
    theme_strength: float
    fund_flow_score: float
    crowding_score: float
    valuation_tolerance: float
    dividend_spread: float
    fcf_yield: float
    quality_score: float
    style_opportunity_cost: float
    duration_risk: float
    credit_risk: float
    yield_stability: float


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

    specific = input_data.get("model_specific_inputs")
    specific_map = specific if isinstance(specific, Mapping) else {}

    return ETFFeatures(
        valuation_percentile=_safe_float(valuation_map.get("valuation_percentile"), 50.0),
        premium_discount=_safe_float(valuation_map.get("premium_discount"), 0.0),
        turnover_amount=_safe_float(liquidity_map.get("turnover_amount")),
        fund_size=_safe_float(liquidity_map.get("fund_size")),
        share_change_ratio=_safe_float(liquidity_map.get("share_change_ratio")),
        tracking_error=_safe_float(tracking_map.get("tracking_error")),
        concentration_ratio=_safe_float(holdings_map.get("concentration_ratio")),
        equity_risk_premium=_safe_float(specific_map.get("equity_risk_premium"), 50.0),
        roe=_safe_float(specific_map.get("roe"), 50.0),
        market_position_score=_safe_float(specific_map.get("market_position_score"), 50.0),
        theme_strength=_safe_float(specific_map.get("theme_strength"), 50.0),
        fund_flow_score=_safe_float(specific_map.get("fund_flow_score"), 50.0),
        crowding_score=_safe_float(specific_map.get("crowding_score"), 50.0),
        valuation_tolerance=_safe_float(specific_map.get("valuation_tolerance"), 50.0),
        dividend_spread=_safe_float(specific_map.get("dividend_spread"), 50.0),
        fcf_yield=_safe_float(specific_map.get("fcf_yield"), 50.0),
        quality_score=_safe_float(specific_map.get("quality_score"), 50.0),
        style_opportunity_cost=_safe_float(specific_map.get("style_opportunity_cost"), 50.0),
        duration_risk=_safe_float(specific_map.get("duration_risk"), 20.0),
        credit_risk=_safe_float(specific_map.get("credit_risk"), 20.0),
        yield_stability=_safe_float(specific_map.get("yield_stability"), 80.0),
    )
