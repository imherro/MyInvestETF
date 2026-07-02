from __future__ import annotations

from core.risk import PricePoint, normalize_price_series


def _date_key(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits or value


def point_in_time_prices(
    price_series: list[object] | None,
    *,
    as_of_date: str | None = None,
    lag_days: int = 1,
) -> list[PricePoint]:
    points = normalize_price_series(price_series)
    if as_of_date:
        max_key = _date_key(as_of_date)
        points = [point for point in points if _date_key(point.trade_date) <= max_key]
    if lag_days > 0 and len(points) > lag_days:
        return points[:-lag_days]
    if lag_days > 0:
        return []
    return points


def assert_no_future_data(points: list[PricePoint], *, target_date: str | None) -> bool:
    if not target_date or not points:
        return True
    target_key = _date_key(target_date)
    return all(_date_key(point.trade_date) <= target_key for point in points)
