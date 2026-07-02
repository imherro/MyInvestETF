from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PricePoint:
    trade_date: str
    close: float
    amount: float | None = None
    volume: float | None = None


@dataclass(frozen=True)
class DrawdownState:
    current_drawdown: float
    max_drawdown_rolling: float
    drawdown_percentile: float
    recovery_speed: float
    duration_days: int
    drawdown_acceleration: float = 0.0
    as_of_date: str | None = None
    peak_date: str | None = None
    trough_date: str | None = None
    data_points: int = 0


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _mapping_value(row: object, keys: tuple[str, ...]) -> object | None:
    if isinstance(row, Mapping):
        for key in keys:
            if key in row:
                return row[key]
        return None
    for key in keys:
        try:
            return getattr(row, key)
        except AttributeError:
            continue
    for key in keys:
        try:
            return row[key]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            continue
    return None


def _sort_date_key(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits or value


def normalize_price_series(rows: Iterable[object] | None) -> list[PricePoint]:
    points: list[PricePoint] = []
    for row in rows or []:
        trade_date = _mapping_value(row, ("trade_date", "date", "ts_date"))
        close = _safe_float(_mapping_value(row, ("close", "close_price", "price", "latest_close")))
        if trade_date is None or close is None or close <= 0:
            continue
        points.append(
            PricePoint(
                trade_date=str(trade_date),
                close=close,
                amount=_safe_float(_mapping_value(row, ("amount", "turnover_amount"))),
                volume=_safe_float(_mapping_value(row, ("volume", "vol"))),
            )
        )
    return sorted(points, key=lambda item: _sort_date_key(item.trade_date))


def _empty_state() -> DrawdownState:
    return DrawdownState(
        current_drawdown=0.0,
        max_drawdown_rolling=0.0,
        drawdown_percentile=0.0,
        recovery_speed=0.0,
        duration_days=0,
        data_points=0,
    )


def build_drawdown_state(price_series: Iterable[object] | None, window: int | None = None) -> DrawdownState:
    points = normalize_price_series(price_series)
    if not points:
        return _empty_state()

    if window is not None and window > 0:
        points = points[-window:]
    if len(points) == 1:
        return DrawdownState(
            current_drawdown=0.0,
            max_drawdown_rolling=0.0,
            drawdown_percentile=100.0,
            recovery_speed=0.0,
            duration_days=0,
            as_of_date=points[-1].trade_date,
            peak_date=points[-1].trade_date,
            trough_date=points[-1].trade_date,
            data_points=1,
        )

    running_peak = points[0].close
    running_peak_index = 0
    drawdowns: list[float] = []
    peak_indexes: list[int] = []
    for index, point in enumerate(points):
        if point.close >= running_peak:
            running_peak = point.close
            running_peak_index = index
        drawdown = max(0.0, (running_peak - point.close) / running_peak) if running_peak > 0 else 0.0
        drawdowns.append(drawdown)
        peak_indexes.append(running_peak_index)

    latest_index = len(points) - 1
    latest_close = points[-1].close
    latest_peak_index = peak_indexes[-1]
    latest_peak_close = points[latest_peak_index].close
    current_drawdown = max(0.0, (latest_peak_close - latest_close) / latest_peak_close) if latest_peak_close > 0 else 0.0
    max_drawdown = max(drawdowns) if drawdowns else 0.0

    less_or_equal = sum(1 for value in drawdowns if value <= current_drawdown)
    drawdown_percentile = (less_or_equal / len(drawdowns)) * 100.0 if drawdowns else 0.0

    current_leg = points[latest_peak_index:]
    trough_offset, trough_point = min(enumerate(current_leg), key=lambda pair: pair[1].close)
    trough_index = latest_peak_index + trough_offset
    days_from_trough = latest_index - trough_index
    if days_from_trough > 0 and trough_point.close > 0:
        recovery_speed = (latest_close / trough_point.close - 1.0) / days_from_trough
    else:
        recovery_speed = 0.0

    prior_drawdown = drawdowns[-6] if len(drawdowns) >= 6 else drawdowns[0]
    drawdown_acceleration = current_drawdown - prior_drawdown

    return DrawdownState(
        current_drawdown=round(current_drawdown, 6),
        max_drawdown_rolling=round(max_drawdown, 6),
        drawdown_percentile=round(drawdown_percentile, 6),
        recovery_speed=round(recovery_speed, 6),
        duration_days=latest_index - latest_peak_index,
        drawdown_acceleration=round(drawdown_acceleration, 6),
        as_of_date=points[-1].trade_date,
        peak_date=points[latest_peak_index].trade_date,
        trough_date=points[trough_index].trade_date,
        data_points=len(points),
    )


def drawdown_state_to_dict(state: DrawdownState) -> dict[str, Any]:
    return asdict(state)
