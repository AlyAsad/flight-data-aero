import asyncio
from datetime import date, timedelta

import pytest

from app.cache import RouteKey, utcnow
from app.fetcher import BATCH_SIZE_KEY, TIER_OTHER_GROUPS, TIER_SELECTED_DAY, needs_fetch
from app.models import Completeness, Direction, EntryStatus, ProviderMode
from app.providers.base import ProviderUnavailableError
from tests.conftest import GROUPS, make_flight

CENTER = date.today() + timedelta(days=30)
FROM = Direction.FROM_UK


async def drain(fetcher):
    """Run jobs inline until the queue is empty."""
    while (job := fetcher._take()) is not None:
        try:
            await fetcher._run_job(job)
        finally:
            fetcher._running.pop(job.key, None)
            fetcher.progress.done += job.planned_route_days


def set_mode(settings, mode):
    settings.save(settings.get().model_copy(update={"provider_mode": mode}))


class TestPlanning:
    def test_priority_order(self, fetcher):
        n = fetcher.plan_view(FROM, "KHI", CENTER, "NCL")
        assert n == 3 * 7
        order = []
        while (job := fetcher._take()) is not None:
            order.append(job)
        assert (order[0].key.group_id, order[0].key.date) == ("NCL", CENTER)
        assert order[0].priority[0] == TIER_SELECTED_DAY
        assert all(j.key.group_id == "NCL" for j in order[:7])
        assert all(j.priority[0] == TIER_OTHER_GROUPS for j in order[7:])
        # Selected city's other days run nearest-first.
        dists = [abs((j.key.date - CENTER).days) for j in order[1:7]]
        assert dists == sorted(dists)

    def test_route_day_progress_counts_airports(self, fetcher):
        fetcher.plan_view(FROM, "KHI", CENTER, "NCL")
        assert fetcher.progress.total == 7 * (4 + 1 + 1)

    def test_skips_past_and_not_on_sale(self, fetcher):
        assert fetcher.plan_view(FROM, "KHI", date.today() - timedelta(days=10), "NCL") == 0
        far = date.today() + timedelta(days=400)
        assert fetcher.plan_view(FROM, "KHI", far, "NCL") == 0

    def test_replan_replaces_other_views(self, fetcher):
        fetcher.plan_view(FROM, "KHI", CENTER, "NCL")
        fetcher.plan_view(FROM, "DXB", CENTER, "MAN")
        assert {k.foreign for k in fetcher.queued_keys()} == {"DXB"}


class TestNeedsFetch:
    def test_rules(self, cache):
        now = utcnow()
        key = RouteKey(FROM, "NCL", "KHI", CENTER)
        assert needs_fetch(None, detail=False, force_after=None, now=now)
        cache.store_result(key, [], provider="s", completeness=Completeness.PARTIAL, ttl_days=2)
        e = cache.get(key)
        assert not needs_fetch(e, detail=False, force_after=None, now=now)
        assert needs_fetch(e, detail=True, force_after=None, now=now)
        assert needs_fetch(e, detail=False, force_after=utcnow(), now=now)
        assert needs_fetch(e, detail=False, force_after=None, now=now + timedelta(days=3))


class TestFastFlightsRun:
    async def test_fetches_each_airport_once_and_caches(self, fetcher, ff, cache):
        fetcher.plan_view(FROM, "KHI", CENTER, "NCL")
        await drain(fetcher)
        assert len(ff.calls) == 7 * 6
        assert ff.calls[0] == ("NCL", "KHI", CENTER)
        e = cache.get(RouteKey(FROM, "LGW", "KHI", CENTER))
        assert e.status is EntryStatus.OK and e.completeness is Completeness.FULL
        # Reload within TTL: nothing to do, no provider calls.
        assert fetcher.plan_view(FROM, "KHI", CENTER, "NCL") == 0
        await drain(fetcher)
        assert len(ff.calls) == 42

    async def test_to_uk_direction_swaps_endpoints(self, fetcher, ff):
        fetcher.plan_view(Direction.TO_UK, "KHI", CENTER, "NCL")
        job = fetcher._take()
        await fetcher._run_job(job)
        assert ff.calls[0] == ("KHI", "NCL", CENTER)

    async def test_refresh_day_refetches(self, fetcher, ff):
        fetcher.plan_view(FROM, "KHI", CENTER, "NCL")
        await drain(fetcher)
        before = len(ff.calls)
        fetcher.refresh_day(FROM, "KHI", GROUPS[0], CENTER)
        await drain(fetcher)
        assert len(ff.calls) == before + 4

    async def test_refresh_week_refetches_view(self, fetcher, ff):
        fetcher.plan_view(FROM, "KHI", CENTER, "NCL")
        await drain(fetcher)
        fetcher.plan_view(FROM, "KHI", CENTER, "NCL", force=True)
        await drain(fetcher)
        assert len(ff.calls) == 84

    async def test_errors_are_cached_and_not_retried_immediately(
        self, fetcher, ff, cache, settings
    ):
        set_mode(settings, ProviderMode.FAST_FLIGHTS)
        ff.fail = True
        fetcher.plan_view(FROM, "KHI", CENTER, "NCL")
        await drain(fetcher)
        e = cache.get(RouteKey(FROM, "NCL", "KHI", CENTER))
        assert e.status is EntryStatus.ERROR
        calls = len(ff.calls)
        assert fetcher.plan_view(FROM, "KHI", CENTER, "NCL") == 0
        await drain(fetcher)
        assert len(ff.calls) == calls


class TestAutoSwitching:
    async def test_switches_to_serpapi_after_three_failures(self, fetcher, ff, serp, providers):
        ff.fail = True
        fetcher.plan_view(FROM, "KHI", CENTER, "NCL")
        await drain(fetcher)
        assert len(ff.calls) == 3
        assert providers.serpapi_until is not None
        assert len(serp.calls) > 0

    async def test_stays_on_fast_flights_without_key(self, cache, settings, usage, bus, ff):
        from app.fetcher import Fetcher, ProviderManager
        from tests.conftest import FakeSerpApi, no_sleep

        pm = ProviderManager(ff, FakeSerpApi(available=False), settings, usage, bus)
        f = Fetcher(cache, settings, pm, bus, GROUPS, sleep=no_sleep)
        ff.fail = True
        f.plan_view(FROM, "KHI", CENTER, "NCL")
        await drain(f)
        assert pm.serpapi_until is None
        assert len(ff.calls) == 42

    def test_serpapi_mode_requires_key(self, settings, usage, bus, ff):
        from app.fetcher import ProviderManager
        from tests.conftest import FakeSerpApi

        set_mode(settings, ProviderMode.SERPAPI)
        pm = ProviderManager(ff, FakeSerpApi(available=False), settings, usage, bus)
        with pytest.raises(ProviderUnavailableError):
            pm.active()

    def test_cap_reached_falls_back_to_fast_flights_in_auto(self, providers, usage, settings, ff):
        settings.save(settings.get().model_copy(update={"serpapi_monthly_cap": 0}))
        providers.serpapi_until = utcnow() + timedelta(minutes=30)
        assert providers.active() is ff


class TestSerpApiRun:
    async def test_detail_and_overview_queries(self, fetcher, serp, settings, cache):
        set_mode(settings, ProviderMode.SERPAPI)
        fetcher.plan_view(FROM, "KHI", CENTER, "NCL")
        await drain(fetcher)
        details = [c for c in serp.calls if c[0] == "NCL"]
        overviews = [c for c in serp.calls if "," in c[0]]
        assert len(details) == 7
        assert len(overviews) == 7  # LON+MAN (+NCL) fit one batch: one per day
        assert len(serp.calls) == 14
        lon = cache.get(RouteKey(FROM, "LHR", "KHI", CENTER))
        assert lon.completeness is Completeness.PARTIAL
        ncl = cache.get(RouteKey(FROM, "NCL", "KHI", CENTER))
        assert ncl.completeness is Completeness.FULL  # overview never overwrote detail

    async def test_selecting_city_upgrades_partial_to_full(self, fetcher, serp, settings, cache):
        set_mode(settings, ProviderMode.SERPAPI)
        fetcher.plan_view(FROM, "KHI", CENTER, "NCL")
        await drain(fetcher)
        before = len(serp.calls)
        fetcher.plan_view(FROM, "KHI", CENTER, "LON")
        await drain(fetcher)
        assert len(serp.calls) == before + 7
        assert cache.get(RouteKey(FROM, "LTN", "KHI", CENTER)).completeness is Completeness.FULL

    async def test_too_many_airports_halves_batch(self, fetcher, serp, settings):
        set_mode(settings, ProviderMode.SERPAPI)
        serp.reject_over = 4
        fetcher.plan_view(FROM, "KHI", CENTER, "NCL")
        await drain(fetcher)
        assert int(settings.get_value(BATCH_SIZE_KEY)) <= 4


class TestWorkers:
    async def test_worker_pool_drains_queue_and_emits_events(self, fetcher, ff, bus):
        events = []

        async def listen():
            async for name, _ in bus.subscribe():
                events.append(name)

        listener = asyncio.create_task(listen())
        await asyncio.sleep(0)
        fetcher.start()
        fetcher.plan_view(FROM, "KHI", CENTER, "NCL")
        for _ in range(200):
            await asyncio.sleep(0.01)
            if fetcher.queue_size == 0:
                break
        await fetcher.stop()
        listener.cancel()
        assert fetcher.queue_size == 0
        assert len(ff.calls) == 42
        assert "route_day_done" in events and "queue_size" in events


def test_make_flight_helper():
    assert make_flight("NCL", "KHI", CENTER, 1).price_gbp == 1
