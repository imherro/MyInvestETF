from __future__ import annotations

from dataclasses import dataclass

from .features import ETFFeatures


@dataclass(frozen=True)
class ETFValuationSignal:
    undervalued_score: float
    liquidity_score: float
    tracking_score: float
    portfolio_role_score: float
    risk_adjusted_score: float
    mainline_validity_score: float
    valuation_tolerance_score: float
    crowding_risk_score: float
    factor_premium_score: float
    cash_like_safety_score: float


def _score(value: float) -> float:
    return max(0.0, min(100.0, value))


def _liquidity_score(features: ETFFeatures) -> float:
    turnover_score = _score(features.turnover_amount / 10_000_000.0 * 35.0)
    size_score = _score(features.fund_size / 1_000_000_000.0 * 45.0)
    share_flow_bonus = _score(50.0 + features.share_change_ratio * 100.0) * 0.20
    return _score(turnover_score + size_score + share_flow_bonus)


def build_etf_signal(
    *,
    features: ETFFeatures,
    base_role_score: float = 60.0,
    model_type: str = "broad_index",
) -> ETFValuationSignal:
    broad_undervalued = _score(
        100.0
        - features.valuation_percentile
        - abs(features.premium_discount) * 250.0
        + (features.equity_risk_premium - 50.0) * 0.25
        + (features.roe - 50.0) * 0.15
    )
    liquidity_score = _liquidity_score(features)
    tracking_score = _score(100.0 - features.tracking_error * 800.0 - abs(features.premium_discount) * 200.0)
    concentration_penalty = _score(features.concentration_ratio * 60.0)
    mainline_validity_score = _score(features.theme_strength * 0.45 + features.fund_flow_score * 0.35 + liquidity_score * 0.20)
    valuation_tolerance_score = _score(features.valuation_tolerance - features.crowding_score * 0.25 - max(0.0, features.valuation_percentile - 65.0) * 0.45)
    crowding_risk_score = _score(features.crowding_score + abs(features.premium_discount) * 150.0)
    factor_premium_score = _score(
        features.dividend_spread * 0.35
        + features.fcf_yield * 0.30
        + features.quality_score * 0.25
        - features.style_opportunity_cost * 0.20
    )
    factor_value_opportunity_score = _score(factor_premium_score * 0.55 + broad_undervalued * 0.45)
    cash_like_safety_score = _score(
        features.yield_stability * 0.45
        + liquidity_score * 0.35
        + tracking_score * 0.20
        - features.duration_risk * 0.35
        - features.credit_risk * 0.35
        - abs(features.premium_discount) * 400.0
    )

    if model_type == "mainline_theme":
        undervalued_score = valuation_tolerance_score
        portfolio_role_score = _score(base_role_score + mainline_validity_score * 0.35 - crowding_risk_score * 0.20 + liquidity_score * 0.15)
        risk_adjusted_score = _score(
            mainline_validity_score * 0.35
            + valuation_tolerance_score * 0.25
            + liquidity_score * 0.15
            + tracking_score * 0.10
            + portfolio_role_score * 0.15
            - crowding_risk_score * 0.20
        )
    elif model_type == "factor_defensive":
        undervalued_score = factor_value_opportunity_score
        portfolio_role_score = _score(base_role_score + factor_value_opportunity_score * 0.25 + tracking_score * 0.15 - concentration_penalty)
        risk_adjusted_score = _score(
            factor_value_opportunity_score * 0.35
            + liquidity_score * 0.15
            + tracking_score * 0.20
            + portfolio_role_score * 0.20
            - features.style_opportunity_cost * 0.15
        )
    elif model_type == "cash_like":
        undervalued_score = cash_like_safety_score
        portfolio_role_score = _score(base_role_score + cash_like_safety_score * 0.35 + liquidity_score * 0.20)
        risk_adjusted_score = _score(cash_like_safety_score * 0.55 + liquidity_score * 0.25 + tracking_score * 0.20)
    else:
        undervalued_score = broad_undervalued
        portfolio_role_score = _score(base_role_score - concentration_penalty + liquidity_score * 0.25 + features.market_position_score * 0.10)
        risk_adjusted_score = _score(
            undervalued_score * 0.35
            + liquidity_score * 0.20
            + tracking_score * 0.25
            + portfolio_role_score * 0.20
        )


    return ETFValuationSignal(
        undervalued_score=undervalued_score,
        liquidity_score=liquidity_score,
        tracking_score=tracking_score,
        portfolio_role_score=portfolio_role_score,
        risk_adjusted_score=risk_adjusted_score,
        mainline_validity_score=mainline_validity_score,
        valuation_tolerance_score=valuation_tolerance_score,
        crowding_risk_score=crowding_risk_score,
        factor_premium_score=factor_premium_score,
        cash_like_safety_score=cash_like_safety_score,
    )
