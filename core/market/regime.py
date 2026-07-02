from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from statistics import pstdev
from typing import Any, Literal

from core.risk.drawdown import DrawdownState, PricePoint, build_drawdown_state, normalize_price_series
from .structure import MarketStructure

RegimeValue = Literal["risk_on", "risk_off", "shock", "rotation"]
ConfirmationLevel = Literal["weak", "medium", "strong"]


@dataclass(frozen=True)
class MarketRegime:
    regime: RegimeValue
    confidence: float
    as_of_date: str | None = None
    evidence: dict[str, float | str | None] | None = None
    data_points: int = 0


@dataclass(frozen=True)
class MarketContext:
    etf_code: str
    regime: MarketRegime
    drawdown: DrawdownState


@dataclass(frozen=True)
class MarketRegimeV2:
    regime: RegimeValue
    confidence: float
    structure: dict[str, float]
    confirmation_level: ConfirmationLevel
    explanation: str
    evidence: dict[str, float | str | None]
    as_of_date: str | None = None


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _return_between(points: list[PricePoint], days: int) -> float:
    if len(points) <= days:
        return 0.0
    base = points[-days - 1].close
    latest = points[-1].close
    return latest / base - 1.0 if base > 0 else 0.0


def _daily_returns(points: list[PricePoint], days: int) -> list[float]:
    start = max(1, len(points) - days)
    returns = []
    for index in range(start, len(points)):
        previous = points[index - 1].close
        if previous > 0:
            returns.append(points[index].close / previous - 1.0)
    return returns


def _liquidity_trend(points: list[PricePoint], days: int = 20) -> float | None:
    amounts = [point.amount for point in points if point.amount is not None and point.amount > 0]
    if len(amounts) < days * 2:
        return None
    recent = amounts[-days:]
    previous = amounts[-days * 2 : -days]
    previous_avg = sum(previous) / len(previous)
    if previous_avg <= 0:
        return None
    return sum(recent) / len(recent) / previous_avg - 1.0


def detect_market_regime(points: list[PricePoint], drawdown: DrawdownState) -> MarketRegime:
    if not points:
        return MarketRegime(
            regime="rotation",
            confidence=0.0,
            evidence={"reason": "no_price_series"},
            data_points=0,
        )

    momentum_20 = _return_between(points, 20)
    momentum_60 = _return_between(points, 60)
    momentum_120 = _return_between(points, 120)
    returns_20 = _daily_returns(points, 20)
    volatility_20 = pstdev(returns_20) if len(returns_20) >= 2 else 0.0
    liquidity_trend = _liquidity_trend(points)
    liquidity_score = liquidity_trend if liquidity_trend is not None else 0.0

    evidence: dict[str, float | str | None] = {
        "momentum_20": round(momentum_20, 6),
        "momentum_60": round(momentum_60, 6),
        "momentum_120": round(momentum_120, 6),
        "volatility_20": round(volatility_20, 6),
        "liquidity_trend_20": round(liquidity_trend, 6) if liquidity_trend is not None else None,
        "current_drawdown": drawdown.current_drawdown,
        "drawdown_percentile": drawdown.drawdown_percentile,
    }

    if len(points) < 20:
        evidence["reason"] = "insufficient_price_series"
        return MarketRegime(
            regime="rotation",
            confidence=round(_clamp(len(points) / 40.0), 6),
            as_of_date=points[-1].trade_date,
            evidence=evidence,
            data_points=len(points),
        )

    if volatility_20 >= 0.035 and (momentum_20 <= -0.06 or drawdown.current_drawdown >= 0.10):
        confidence = 0.55 + min(0.30, volatility_20 * 4.0) + min(0.15, drawdown.current_drawdown)
        regime: RegimeValue = "shock"
    elif momentum_60 >= 0.05 and drawdown.current_drawdown <= 0.08 and liquidity_score >= -0.20:
        confidence = 0.50 + min(0.30, momentum_60 * 1.5) + min(0.20, max(0.0, liquidity_score) * 0.5)
        regime = "risk_on"
    elif momentum_60 <= -0.05 or drawdown.current_drawdown >= 0.12:
        confidence = 0.50 + min(0.30, abs(momentum_60) * 1.5) + min(0.20, drawdown.current_drawdown)
        regime = "risk_off"
    else:
        confidence = 0.45 + min(0.20, abs(momentum_20) * 1.0) + min(0.15, volatility_20 * 2.0)
        regime = "rotation"

    return MarketRegime(
        regime=regime,
        confidence=round(_clamp(confidence), 6),
        as_of_date=points[-1].trade_date,
        evidence=evidence,
        data_points=len(points),
    )


def build_market_context(
    etf_code: str,
    etf_prices: Iterable[object] | None = None,
    index_prices: Iterable[object] | None = None,
) -> MarketContext:
    regime_points = normalize_price_series(index_prices) if index_prices is not None else normalize_price_series(etf_prices)
    drawdown = build_drawdown_state(etf_prices)
    regime = detect_market_regime(regime_points, drawdown)
    return MarketContext(etf_code=etf_code, regime=regime, drawdown=drawdown)


def build_market_regime_v2(
    etf_code: str,
    etf_prices: Iterable[object] | None,
    market_structure: MarketStructure,
) -> MarketRegimeV2:
    points = normalize_price_series(etf_prices)
    drawdown = build_drawdown_state(points)
    momentum_20 = _return_between(points, 20)
    momentum_60 = _return_between(points, 60)
    volatility_20 = pstdev(_daily_returns(points, 20)) if len(_daily_returns(points, 20)) >= 2 else 0.0
    trend_score = _clamp(0.5 + momentum_60 * 2.0)
    volatility_score = _clamp(1.0 - volatility_20 / 0.04)
    composite = (
        trend_score * 0.40
        + market_structure.breadth_score * 0.30
        + market_structure.liquidity_score * 0.20
        + volatility_score * 0.10
    )

    if (drawdown.current_drawdown >= 0.10 and volatility_20 >= 0.03) or (
        market_structure.breadth_score < 0.30 and trend_score < 0.40
    ):
        regime: RegimeValue = "shock"
    elif composite >= 0.62 and market_structure.breadth_score >= 0.55:
        regime = "risk_on"
    elif composite <= 0.38 or market_structure.breadth_score < 0.35:
        regime = "risk_off"
    else:
        regime = "rotation"

    bullish_price = trend_score >= 0.56
    strong_structure = market_structure.breadth_score >= 0.56 and market_structure.liquidity_score >= 0.50
    weak_structure = market_structure.breadth_score < 0.45 or market_structure.liquidity_score < 0.40
    if bullish_price and strong_structure:
        confirmation: ConfirmationLevel = "strong"
        explanation = "price trend and market breadth confirm each other"
    elif bullish_price and weak_structure:
        confirmation = "weak"
        explanation = "price bullish but breadth or liquidity confirmation is weak"
    elif not bullish_price and strong_structure:
        confirmation = "medium"
        explanation = "market structure is healthier than ETF price trend"
    else:
        confirmation = "medium" if regime == "rotation" else "weak"
        explanation = "price and structure do not provide strong confirmation"

    confidence = _clamp(0.35 + abs(composite - 0.50) + market_structure.observations / 300.0)
    return MarketRegimeV2(
        regime=regime,
        confidence=round(confidence, 6),
        structure={
            "breadth_score": market_structure.breadth_score,
            "liquidity_score": market_structure.liquidity_score,
            "dispersion_score": market_structure.dispersion_score,
            "price_trend_score": round(trend_score, 6),
            "volatility_score": round(volatility_score, 6),
        },
        confirmation_level=confirmation,
        explanation=explanation,
        evidence={
            "etf_code": etf_code,
            "momentum_20": round(momentum_20, 6),
            "momentum_60": round(momentum_60, 6),
            "volatility_20": round(volatility_20, 6),
            "current_drawdown": drawdown.current_drawdown,
            "composite_score": round(composite, 6),
            "breadth_contribution": round(market_structure.breadth_score * 0.30, 6),
            "liquidity_contribution": round(market_structure.liquidity_score * 0.20, 6),
        },
        as_of_date=points[-1].trade_date if points else market_structure.as_of_date,
    )


def market_context_to_dict(context: MarketContext) -> dict[str, Any]:
    return asdict(context)


def market_regime_v2_to_dict(regime: MarketRegimeV2) -> dict[str, Any]:
    return asdict(regime)
