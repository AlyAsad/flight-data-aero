"""Date helpers for the 7-day view."""

from __future__ import annotations

from datetime import date, timedelta

from .config import MAX_DAYS_AHEAD, WEEK_HALF_SPAN


def week_dates(center: date) -> list[date]:
    return [center + timedelta(days=i) for i in range(-WEEK_HALF_SPAN, WEEK_HALF_SPAN + 1)]


def is_past(day: date, today: date | None = None) -> bool:
    return day < (today or date.today())


def is_not_on_sale(day: date, today: date | None = None) -> bool:
    return (day - (today or date.today())).days > MAX_DAYS_AHEAD


def is_fetchable(day: date, today: date | None = None) -> bool:
    return not is_past(day, today) and not is_not_on_sale(day, today)
