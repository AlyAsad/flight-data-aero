"""Assemble UI-facing summaries from per-airport cache entries."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from .cache import CacheEntry, FlightCache, RouteKey, utcnow
from .dates import is_not_on_sale, is_past, week_dates
from .models import (
    CacheMeta,
    Completeness,
    DayStatus,
    Direction,
    FetchJob,
    FlightOption,
    GroupWeek,
    RouteDaySummary,
    UKGroup,
    WeekSummary,
)


def _sort_flights(flights: list[FlightOption]) -> list[FlightOption]:
    return sorted(flights, key=lambda f: (f.price_gbp is None, f.price_gbp or 0, f.departure_local))


def combine_entries(
    entries: Sequence[CacheEntry | None],
    day: date,
    *,
    now: datetime,
    today: date | None = None,
) -> tuple[list[FlightOption], CacheMeta]:
    """Merge one group's per-airport entries for a day into flights + metadata."""
    if is_past(day, today):
        return [], CacheMeta(status=DayStatus.PAST)
    if is_not_on_sale(day, today):
        return [], CacheMeta(status=DayStatus.NOT_ON_SALE)

    present = [e for e in entries if e is not None]
    with_data = [e for e in present if e.has_data]
    missing = len(present) < len(entries)
    flights = _sort_flights([f for e in with_data for f in e.flights])
    errors = [e.error_message for e in present if e.error_message]
    errors_without_data = [e for e in present if not e.has_data]

    if flights:
        status = DayStatus.OK
    elif errors_without_data:
        status = DayStatus.ERROR
    elif missing or not with_data:
        status = DayStatus.PENDING
    else:
        status = DayStatus.EMPTY

    meta = CacheMeta(
        status=status,
        fetched_at=min((e.fetched_at for e in with_data), default=None),
        expires_at=min((e.expires_at for e in with_data), default=None),
        is_stale=any(e.is_expired(now) for e in with_data),
        is_partial=any(e.completeness is Completeness.PARTIAL for e in with_data),
        providers=sorted({e.provider for e in with_data}),
        error=errors[0] if errors else None,
    )
    return flights, meta


def _day_summary(
    group: UKGroup, day: date, flights: list[FlightOption], meta: CacheMeta, queued: bool
) -> RouteDaySummary:
    priced = [f for f in flights if f.price_gbp is not None]
    cheapest = min(priced, key=lambda f: f.price_gbp or 0, default=None)
    return RouteDaySummary(
        group_id=group.id,
        date=day,
        cheapest_price=cheapest.price_gbp if cheapest else None,
        cheapest_flight_id=cheapest.id if cheapest else None,
        option_count=len(flights),
        status=meta.status,
        fetched_at=meta.fetched_at,
        expires_at=meta.expires_at,
        is_stale=meta.is_stale,
        is_partial=meta.is_partial,
        loading=queued,
        error=meta.error,
    )


def _group_status(days: list[RouteDaySummary]) -> DayStatus:
    statuses = {d.status for d in days}
    if DayStatus.OK in statuses:
        return DayStatus.OK
    if DayStatus.PENDING in statuses or any(d.loading for d in days):
        return DayStatus.PENDING
    if DayStatus.ERROR in statuses:
        return DayStatus.ERROR
    if DayStatus.EMPTY in statuses:
        return DayStatus.EMPTY
    if statuses == {DayStatus.PAST}:
        return DayStatus.PAST
    return DayStatus.NOT_ON_SALE


class SummaryBuilder:
    def __init__(self, cache: FlightCache):
        self._cache = cache

    def group_days(
        self,
        direction: Direction,
        foreign: str,
        group: UKGroup,
        days: list[date],
        queued: set[tuple[str, date]],
        now: datetime | None = None,
    ) -> list[tuple[date, list[FlightOption], CacheMeta, bool]]:
        now = now or utcnow()
        entries = self._cache.get_many(direction, foreign, group.airports, days)
        out = []
        for day in days:
            day_entries = [
                entries.get(RouteKey(direction, a, foreign, day)) for a in group.airports
            ]
            is_queued = (group.id, day) in queued
            flights, meta = combine_entries(day_entries, day, now=now)
            out.append((day, flights, meta, is_queued))
        return out

    def week(
        self,
        direction: Direction,
        foreign: str,
        center: date,
        groups: Sequence[UKGroup],
        jobs: list[FetchJob],
    ) -> WeekSummary:
        now = utcnow()
        days = week_dates(center)
        queued = {(j.group, j.date) for j in jobs}
        group_weeks: list[GroupWeek] = []
        for group in groups:
            summaries = [
                _day_summary(group, day, flights, meta, is_queued)
                for day, flights, meta, is_queued in self.group_days(
                    direction, foreign, group, days, queued, now
                )
            ]
            prices = [d.cheapest_price for d in summaries if d.cheapest_price is not None]
            group_weeks.append(
                GroupWeek(
                    group_id=group.id,
                    name=group.name,
                    airports=group.airports,
                    days=summaries,
                    cheapest_price=min(prices, default=None),
                    status=_group_status(summaries),
                )
            )
        all_prices = [
            d.cheapest_price for g in group_weeks for d in g.days if d.cheapest_price is not None
        ]
        pending = len(jobs)
        return WeekSummary(
            direction=direction,
            foreign=foreign,
            center_date=center,
            dates=days,
            groups=group_weeks,
            jobs=jobs,
            pending_count=pending,
            min_price=min(all_prices, default=None),
            max_price=max(all_prices, default=None),
        )
