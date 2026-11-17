"""Priority fetch queue, worker pool and provider switching.

Jobs are *group-days* (one UK city group, one date). At execution time the
worker decides how to satisfy a job with the active provider:

* fast-flights: one request per UK airport that still needs data (full).
* SerpApi, selected city ("detail"): one multi-airport request for the group (full).
* SerpApi, other cities ("overview"): one request for the whole batch the group
  belongs to (partial); that single request satisfies every group in the batch.

A job is complete when every airport in its group has fresh enough data, so
overlapping work (batches, refreshes) is naturally de-duplicated.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import count

from .cache import CacheEntry, FlightCache, RouteKey, SettingsStore, UsageStore, utcnow
from .config import AUTO_FAILOVER_MINUTES, AUTO_FAILOVER_THRESHOLD, ERROR_CACHE_MINUTES
from .dates import is_fetchable, week_dates
from .events import EventBus
from .models import Completeness, Direction, FlightOption, ProviderMode, UKGroup
from .providers.base import (
    FlightProvider,
    ProviderError,
    ProviderUnavailableError,
    TooManyAirportsError,
)
from .providers.serpapi import SerpApiProvider
from .uk_groups import pack_batches

log = logging.getLogger(__name__)

BATCH_SIZE_KEY = "serpapi_batch_max_airports"

# Priority tiers (lower runs first).
TIER_SELECTED_DAY = 0
TIER_SELECTED_WEEK = 1
TIER_OTHER_GROUPS = 2


def route_endpoints(direction: Direction, uk: str, foreign: str) -> tuple[str, str]:
    """(origin, destination) for a query."""
    return (uk, foreign) if direction is Direction.FROM_UK else (foreign, uk)


@dataclass(frozen=True)
class JobKey:
    direction: Direction
    foreign: str
    group_id: str
    date: date


@dataclass
class Job:
    key: JobKey
    group: UKGroup
    priority: tuple[int, int, int]
    detail: bool  # selected city: needs complete (non-overview) data
    force_after: datetime | None = None  # refresh: data must be newer than this
    seq: int = 0
    planned_route_days: int = 0

    def sort_key(self) -> tuple[tuple[int, int, int], int]:
        return (self.priority, self.seq)


def needs_fetch(
    entry: CacheEntry | None,
    *,
    detail: bool,
    force_after: datetime | None,
    now: datetime,
) -> bool:
    """Whether one airport's route-day must be (re)fetched."""
    if entry is None:
        return True
    # A refresh forces a fetch unless it already failed after the refresh was requested.
    failed_since_refresh = (
        force_after is not None
        and entry.retry_after is not None
        and entry.retry_after - timedelta(minutes=ERROR_CACHE_MINUTES) >= force_after
    )
    if force_after is not None and entry.fetched_at < force_after and not failed_since_refresh:
        return True
    if entry.in_error_backoff(now):
        return False
    if entry.is_expired(now):
        return True
    return detail and entry.has_data and entry.completeness is Completeness.PARTIAL


@dataclass
class ProgressCounter:
    total: int = 0
    done: int = 0


class ProviderManager:
    """Chooses the provider per request according to the mode setting."""

    def __init__(
        self,
        fast_flights: FlightProvider,
        serpapi: SerpApiProvider,
        settings: SettingsStore,
        usage: UsageStore,
        bus: EventBus,
    ):
        self.fast_flights = fast_flights
        self.serpapi = serpapi
        self._settings = settings
        self._usage = usage
        self._bus = bus
        self.consecutive_ff_failures = 0
        self.serpapi_until: datetime | None = None
        self.last_error: dict[str, str | None] = {fast_flights.name: None, serpapi.name: None}
        self._last_active: str | None = None

    def serpapi_usable(self) -> bool:
        cap = self._settings.get().serpapi_monthly_cap
        return self.serpapi.available and self._usage.used() < cap

    def active(self, now: datetime | None = None) -> FlightProvider:
        now = now or utcnow()
        mode = self._settings.get().provider_mode
        if mode is ProviderMode.FAST_FLIGHTS:
            chosen: FlightProvider = self.fast_flights
        elif mode is ProviderMode.SERPAPI:
            if not self.serpapi.available:
                raise ProviderUnavailableError("SerpApi mode selected but no API key is set")
            if not self.serpapi_usable():
                raise ProviderUnavailableError("SerpApi monthly cap reached")
            chosen = self.serpapi
        elif self.serpapi_until and now < self.serpapi_until and self.serpapi_usable():
            chosen = self.serpapi
        else:
            if self.serpapi_until and now >= self.serpapi_until:
                self.serpapi_until = None
            chosen = self.fast_flights
        self._note_switch(chosen.name)
        return chosen

    def peek_active_name(self) -> str | None:
        try:
            return self.active().name
        except ProviderUnavailableError:
            return None

    def _note_switch(self, name: str) -> None:
        if self._last_active is not None and self._last_active != name:
            self._bus.publish(
                "provider_switched",
                {"provider": name, "previous": self._last_active, "reason": self.switch_reason()},
            )
        self._last_active = name

    def switch_reason(self) -> str | None:
        if self.serpapi_until:
            return "fast-flights failing, using SerpApi"
        return None

    def record_success(self, provider: FlightProvider) -> None:
        self.last_error[provider.name] = None
        if provider is self.fast_flights:
            self.consecutive_ff_failures = 0

    def record_failure(self, provider: FlightProvider, message: str) -> None:
        self.last_error[provider.name] = message
        if provider is not self.fast_flights:
            return
        self.consecutive_ff_failures += 1
        settings = self._settings.get()
        if (
            settings.provider_mode is ProviderMode.AUTO
            and self.consecutive_ff_failures >= AUTO_FAILOVER_THRESHOLD
            and self.serpapi_usable()
        ):
            self.serpapi_until = utcnow() + timedelta(minutes=AUTO_FAILOVER_MINUTES)
            self.consecutive_ff_failures = 0
            log.warning("fast-flights failing; using SerpApi for %d min", AUTO_FAILOVER_MINUTES)

    def reset(self) -> None:
        self.serpapi_until = None
        self.consecutive_ff_failures = 0


class Fetcher:
    def __init__(
        self,
        cache: FlightCache,
        settings: SettingsStore,
        providers: ProviderManager,
        bus: EventBus,
        groups: Sequence[UKGroup],
        default_batch_size: int = 7,
        sleep=asyncio.sleep,
    ):
        self._cache = cache
        self._settings = settings
        self.providers = providers
        self._bus = bus
        self._groups = list(groups)
        self._group_order = {g.id: i for i, g in enumerate(self._groups)}
        self._default_batch_size = default_batch_size
        self._sleep = sleep
        self._pending: dict[JobKey, Job] = {}
        self._running: dict[JobKey, Job] = {}
        self._view: tuple | None = None
        self._seq = count()
        self._wakeup = asyncio.Event()
        self._workers: dict[int, asyncio.Task] = {}
        self._target_workers = 0
        self.progress = ProgressCounter()

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self.set_concurrency(self._settings.get().concurrency)

    async def stop(self) -> None:
        self._target_workers = 0
        tasks = list(self._workers.values())
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()

    def set_concurrency(self, n: int) -> None:
        self._target_workers = n
        for idx in range(n):
            task = self._workers.get(idx)
            if task is None or task.done():
                self._workers[idx] = asyncio.create_task(self._worker(idx), name=f"fetch-{idx}")
        self._wakeup.set()  # let surplus workers notice and exit

    # ------------------------------------------------------------------ batches

    @property
    def batch_size(self) -> int:
        stored = self._settings.get_value(BATCH_SIZE_KEY)
        return int(stored) if stored else self._default_batch_size

    def batches(self) -> list[list[UKGroup]]:
        return pack_batches(self._groups, self.batch_size)

    # ------------------------------------------------------------------ queue state

    @property
    def queue_size(self) -> int:
        return len(self._pending) + len(self._running)

    def queued_keys(self) -> set[JobKey]:
        return set(self._pending) | set(self._running)

    def _airports_needing_fetch(self, job: Job, now: datetime | None = None) -> list[str]:
        now = now or utcnow()
        entries = self._cache.get_many(
            job.key.direction, job.key.foreign, job.group.airports, [job.key.date]
        )
        return [
            a
            for a in job.group.airports
            if needs_fetch(
                entries.get(RouteKey(job.key.direction, a, job.key.foreign, job.key.date)),
                detail=job.detail,
                force_after=job.force_after,
                now=now,
            )
        ]

    def plan_view(
        self,
        direction: Direction,
        foreign: str,
        center: date,
        selected_group: str | None,
        selected_date: date | None = None,
        force: bool = False,
    ) -> int:
        """Queue every route-day of a week view that needs fetching, in priority order.

        Pending jobs from other views are dropped (re-prioritisation); running
        jobs finish. Returns the number of queued group-days for this view.
        """
        now = utcnow()
        selected_date = selected_date or center
        force_after = now if force else None
        new_jobs: dict[JobKey, Job] = {}
        for group in self._groups:
            is_selected = group.id == selected_group
            for day in week_dates(center):
                if not is_fetchable(day):
                    continue
                if is_selected:
                    tier = TIER_SELECTED_DAY if day == selected_date else TIER_SELECTED_WEEK
                else:
                    tier = TIER_OTHER_GROUPS
                key = JobKey(direction, foreign, group.id, day)
                if key in self._running:
                    continue
                existing = self._pending.get(key)
                job = Job(
                    key=key,
                    group=group,
                    priority=(tier, abs((day - selected_date).days), self._group_order[group.id]),
                    detail=is_selected,
                    force_after=force_after or (existing.force_after if existing else None),
                    seq=next(self._seq),
                )
                needed = self._airports_needing_fetch(job, now)
                if needed:
                    job.planned_route_days = len(needed)
                    new_jobs[key] = job

        # Replace pending work: jobs from other views are dropped, jobs for this
        # view are re-prioritised. Running jobs finish untouched.
        self._pending = new_jobs
        view = (direction, foreign, center, selected_group, selected_date)
        planned = sum(j.planned_route_days for j in new_jobs.values())
        running = sum(j.planned_route_days for j in self._running.values())
        if force or view != self._view:
            self.progress = ProgressCounter(total=planned + running)
        else:
            self.progress.total = self.progress.done + planned + running
        self._view = view
        self._publish_queue()
        self._wakeup.set()
        return len(new_jobs) + sum(
            1 for k in self._running if k.direction is direction and k.foreign == foreign
        )

    def refresh_day(self, direction: Direction, foreign: str, group: UKGroup, day: date) -> bool:
        if not is_fetchable(day):
            return False
        key = JobKey(direction, foreign, group.id, day)
        job = Job(
            key=key,
            group=group,
            priority=(-1, 0, 0),  # explicit user action: run first
            detail=True,
            force_after=utcnow(),
            seq=next(self._seq),
            planned_route_days=len(group.airports),
        )
        self._pending[key] = job
        self.progress.total += job.planned_route_days
        self._publish_queue()
        self._wakeup.set()
        return True

    def clear_queue(self) -> None:
        self._pending.clear()
        self.progress = ProgressCounter()
        self._publish_queue()

    def _take(self) -> Job | None:
        candidates = [j for k, j in self._pending.items() if k not in self._running]
        if not candidates:
            return None
        job = min(candidates, key=Job.sort_key)
        del self._pending[job.key]
        self._running[job.key] = job
        return job

    def _publish_queue(self) -> None:
        self._bus.publish(
            "queue_size",
            {
                "pending": len(self._pending),
                "running": len(self._running),
                "total": self.progress.total,
                "done": min(self.progress.done, self.progress.total),
            },
        )

    # ------------------------------------------------------------------ workers

    async def _worker(self, idx: int) -> None:
        while True:
            if idx >= self._target_workers:
                return
            job = self._take()
            if job is None:
                self._wakeup.clear()
                await self._wakeup.wait()
                continue
            try:
                await self._run_job(job)
            except asyncio.CancelledError:
                raise
            except Exception:  # never let one job kill the worker
                log.exception("unexpected error running %s", job.key)
            finally:
                self._running.pop(job.key, None)
                self.progress.done += job.planned_route_days
                self._publish_queue()

    async def _delay(self) -> None:
        lo, hi = self._settings.get().delay_seconds
        await self._sleep(random.uniform(lo, hi))

    async def _run_job(self, job: Job) -> None:
        airports = self._airports_needing_fetch(job)
        if not airports:
            self._emit_done(job.key)
            return
        try:
            provider = self.providers.active()
        except ProviderUnavailableError as exc:
            self._record_errors(job, airports, str(exc), provider_name="none")
            return

        if isinstance(provider, SerpApiProvider):
            if job.detail:
                await self._fetch_group(provider, job)
            else:
                await self._fetch_overview(provider, job)
        else:
            await self._fetch_airports(provider, job, airports)

    async def _fetch_airports(
        self, provider: FlightProvider, job: Job, airports: list[str]
    ) -> None:
        k = job.key
        errors: list[str] = []
        for i, airport in enumerate(airports):
            if i:
                await self._delay()
            origin, dest = route_endpoints(k.direction, airport, k.foreign)
            try:
                flights = await provider.search_one_way(origin, dest, k.date)
            except ProviderError as exc:
                self.providers.record_failure(provider, str(exc))
                self._cache.store_error(
                    RouteKey(k.direction, airport, k.foreign, k.date),
                    str(exc),
                    provider=provider.name,
                )
                errors.append(str(exc))
                continue
            self.providers.record_success(provider)
            self._store(job, airport, flights, provider.name, Completeness.FULL)
        await self._delay()
        if errors and len(errors) == len(airports):
            self._emit_error(k, errors[0])
        else:
            self._emit_done(k)

    async def _fetch_group(self, provider: SerpApiProvider, job: Job) -> None:
        k = job.key
        flights = await self._serpapi_search(provider, job, job.group.airports)
        if flights is None:
            return
        by_airport = self._split_by_uk_airport(k.direction, flights)
        for airport in job.group.airports:
            self._store(job, airport, by_airport.get(airport, []), provider.name, Completeness.FULL)
        self._emit_done(k)

    async def _fetch_overview(self, provider: SerpApiProvider, job: Job) -> None:
        k = job.key
        while True:
            batch = next((b for b in self.batches() if job.group in b), [job.group])
            airports = [a for g in batch for a in g.airports]
            try:
                flights = await self._serpapi_search(provider, job, airports, raise_too_many=True)
            except TooManyAirportsError:
                if len(airports) <= 1 or len(batch) == 1:
                    self._record_errors(job, job.group.airports, "too many airports", provider.name)
                    return
                new_size = max(1, len(airports) // 2)
                log.warning(
                    "SerpApi rejected %d airports; batch size -> %d", len(airports), new_size
                )
                self._settings.set_value(BATCH_SIZE_KEY, str(new_size))
                continue
            break
        if flights is None:
            return
        by_airport = self._split_by_uk_airport(k.direction, flights)
        for group in batch:
            for airport in group.airports:
                # Absence in an overview may mean "crowded out", so it is stored as a
                # *partial* empty result (never authoritative; detail queries replace it).
                self._store(
                    job, airport, by_airport.get(airport, []), provider.name, Completeness.PARTIAL
                )
            self._emit_done(JobKey(k.direction, k.foreign, group.id, k.date))

    async def _serpapi_search(
        self,
        provider: SerpApiProvider,
        job: Job,
        airports: list[str],
        raise_too_many: bool = False,
    ) -> list[FlightOption] | None:
        k = job.key
        origin, dest = route_endpoints(k.direction, ",".join(airports), k.foreign)
        try:
            flights = await provider.search_one_way(origin, dest, k.date)
        except TooManyAirportsError:
            if raise_too_many:
                raise
            self._record_errors(job, job.group.airports, "too many airports", provider.name)
            return None
        except ProviderError as exc:
            self.providers.record_failure(provider, str(exc))
            self._record_errors(job, job.group.airports, str(exc), provider.name)
            return None
        finally:
            await self._delay()
        self.providers.record_success(provider)
        return flights

    @staticmethod
    def _split_by_uk_airport(
        direction: Direction, flights: list[FlightOption]
    ) -> dict[str, list[FlightOption]]:
        out: dict[str, list[FlightOption]] = {}
        for f in flights:
            uk = f.origin_airport if direction is Direction.FROM_UK else f.destination_airport
            out.setdefault(uk, []).append(f)
        return out

    def _store(
        self,
        job: Job,
        airport: str,
        flights: list[FlightOption],
        provider: str,
        completeness: Completeness,
    ) -> None:
        k = job.key
        self._cache.store_result(
            RouteKey(k.direction, airport, k.foreign, k.date),
            flights,
            provider=provider,
            completeness=completeness,
            ttl_days=self._settings.get().ttl_days,
        )

    def _record_errors(
        self, job: Job, airports: list[str], message: str, provider_name: str
    ) -> None:
        k = job.key
        for airport in airports:
            self._cache.store_error(
                RouteKey(k.direction, airport, k.foreign, k.date), message, provider=provider_name
            )
        self._emit_error(k, message)

    def _emit_done(self, key: JobKey) -> None:
        self._bus.publish("route_day_done", _key_payload(key))

    def _emit_error(self, key: JobKey, message: str) -> None:
        self._bus.publish("route_day_error", {**_key_payload(key), "error": message})


def _key_payload(key: JobKey) -> dict[str, str]:
    return {
        "direction": key.direction.value,
        "foreign": key.foreign,
        "group": key.group_id,
        "date": key.date.isoformat(),
    }


__all__ = ["Fetcher", "Job", "JobKey", "ProviderManager", "needs_fetch", "route_endpoints"]
