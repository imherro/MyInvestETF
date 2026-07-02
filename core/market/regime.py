from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from statistics import pstdev
from typing import Any, Literal

from core.risk.drawdown import DrawdownState, PricePoint, build_drawdown_state, normalize_price_series

RegimeValue = Literal["risk_on", "risk_off", "shock", "rotation"]


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


def market_context_to_dict(context: MarketContext) -> dict[str, Any]:
    return asdict(context)
