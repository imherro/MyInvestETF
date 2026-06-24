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


def _score(value: float) -> float:
    return max(0.0, min(100.0, value))


def build_etf_signal(*, features: ETFFeatures, base_role_score: float = 60.0) -> ETFValuationSignal:
    undervalued_score = _score(100.0 - features.valuation_percentile - abs(features.premium_discount) * 250.0)

    turnover_score = _score(features.turnover_amount / 10_000_000.0 * 35.0)
    size_score = _score(features.fund_size / 1_000_000_000.0 * 45.0)
    share_flow_bonus = _score(50.0 + features.share_change_ratio * 100.0) * 0.20
    liquidity_score = _score(turnover_score + size_score + share_flow_bonus)

    tracking_score = _score(100.0 - features.tracking_error * 800.0 - abs(features.premium_discount) * 200.0)
    concentration_penalty = _score(features.concentration_ratio * 60.0)
    portfolio_role_score = _score(base_role_score - concentration_penalty + liquidity_score * 0.25)

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
    )
