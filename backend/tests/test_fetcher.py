from datetime import date, timedelta

import pytest

from app.cache import RouteKey, utcnow
from app.fetcher import (
    BATCH_SIZE_KEY,
    TIER_OTHER_GROUPS,
    TIER_SELECTED_DAY,
    Fetcher,
    ProviderManager,
    needs_fetch,
)
from app.models import Completeness, Direction, EntryStatus, FetchJob, ProviderMode
from app.providers.base import ProviderUnavailableError
from tests.conftest import GROUPS, FakeSerpApi, make_flight, no_sleep

CENTER = date.today() + timedelta(days=30)
FROM = Direction.FROM_UK


async def drain(fetcher, direction=FROM, foreign="KHI", center=CENTER, group="NCL", max_rounds=10):
    """Act like the browser: plan the view, run its jobs, repeat until nothing is left."""
    results = []
    for _ in range(max_rounds):
        jobs = fetcher.plan(direction, foreign, center, group)
        if not jobs:
            return results
        for job in jobs:
            results.append(await fetcher.run(job))
    return results


def set_mode(settings, mode):
    settings.save(settings.get().model_copy(update={"provider_mode": mode}))


def job(group="NCL", day=CENTER, detail=True, force=False, foreign="KHI", direction=FROM):
    return FetchJob(
        direction=direction, foreign=foreign, group=group, date=day, detail=detail, force=force
    )


class TestPlanning:
    def test_priority_order(self, fetcher):
        jobs = fetcher.plan(FROM, "KHI", CENTER, "NCL")
        assert len(jobs) == 3 * 7
        assert (jobs[0].group, jobs[0].date, jobs[0].detail) == ("NCL", CENTER, True)
        assert all(j.group == "NCL" and j.detail for j in jobs[:7])
        assert not any(j.detail for j in jobs[7:])
        # Selected city's other days run nearest-first.
        dists = [abs((j.date - CENTER).days) for j in jobs[1:7]]
        assert dists == sorted(dists)
        assert TIER_SELECTED_DAY < TIER_OTHER_GROUPS

    def test_route_days_count_airports(self, fetcher):
        jobs = fetcher.plan(FROM, "KHI", CENTER, "NCL")
        assert sum(j.route_days for j in jobs) == 7 * (4 + 1 + 1)

    def test_skips_past_and_not_on_sale(self, fetcher):
        assert fetcher.plan(FROM, "KHI", date.today() - timedelta(days=10), "NCL") == []
        assert fetcher.plan(FROM, "KHI", date.today() + timedelta(days=400), "NCL") == []

    def test_groups_are_read_on_every_plan(self, cache, settings, providers, claims):
        enabled = list(GROUPS)
        f = Fetcher(cache, settings, providers, claims, lambda: enabled, sleep=no_sleep)
        assert {j.group for j in f.plan(FROM, "KHI", CENTER, "NCL")} == {"LON", "MAN", "NCL"}
        enabled.pop(0)
        assert {j.group for j in f.plan(FROM, "KHI", CENTER, "NCL")} == {"MAN", "NCL"}


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
        results = await drain(fetcher)
        assert {r.status for r in results} == {"done"}
        assert len(ff.calls) == 7 * 6
        assert ff.calls[0] == ("NCL", "KHI", CENTER)
        e = cache.get(RouteKey(FROM, "LGW", "KHI", CENTER))
        assert e.status is EntryStatus.OK and e.completeness is Completeness.FULL
        # Reload within TTL: nothing to plan, no provider calls.
        assert fetcher.plan(FROM, "KHI", CENTER, "NCL") == []
        assert (await fetcher.run(job())).status == "skipped"
        assert len(ff.calls) == 42

    async def test_to_uk_direction_swaps_endpoints(self, fetcher, ff):
        await fetcher.run(job(direction=Direction.TO_UK))
        assert ff.calls[0] == ("KHI", "NCL", CENTER)

    async def test_result_suggests_next_delay(self, fetcher, settings):
        settings.save(settings.get().model_copy(update={"delay_seconds": (0.5, 0.5)}))
        assert (await fetcher.run(job())).next_delay_seconds == 0.5

    async def test_refresh_refetches_only_data_older_than_two_minutes(self, fetcher, ff, cache):
        await fetcher.run(job(group="LON"))
        assert len(ff.calls) == 4
        # Just fetched: a refresh is ignored (protects against hammering the button).
        assert (await fetcher.run(job(group="LON", force=True))).status == "skipped"
        old = utcnow() - timedelta(minutes=5)
        cache.store_result(
            RouteKey(FROM, "LHR", "KHI", CENTER),
            [],
            provider="fake",
            completeness=Completeness.FULL,
            ttl_days=2,
            now=old,
        )
        assert (await fetcher.run(job(group="LON", force=True))).status == "done"
        assert ff.calls[-1] == ("LHR", "KHI", CENTER) and len(ff.calls) == 5

    async def test_refresh_week_plan_includes_old_data(self, fetcher, cache):
        await drain(fetcher)
        assert fetcher.plan(FROM, "KHI", CENTER, "NCL", force=True) == []
        old = utcnow() - timedelta(minutes=5)
        cache.store_result(
            RouteKey(FROM, "MAN", "KHI", CENTER),
            [],
            provider="fake",
            completeness=Completeness.FULL,
            ttl_days=2,
            now=old,
        )
        jobs = fetcher.plan(FROM, "KHI", CENTER, "NCL", force=True)
        assert [(j.group, j.date, j.force) for j in jobs] == [("MAN", CENTER, True)]

    async def test_errors_are_cached_and_not_retried_immediately(
        self, fetcher, ff, cache, settings
    ):
        set_mode(settings, ProviderMode.FAST_FLIGHTS)
        ff.fail = True
        result = await fetcher.run(job())
        assert result.status == "error" and result.error == "boom"
        assert cache.get(RouteKey(FROM, "NCL", "KHI", CENTER)).status is EntryStatus.ERROR
        assert (await fetcher.run(job())).status == "skipped"
        assert len(ff.calls) == 1
        # A refresh retries a failed day straight away (no two-minute wait).
        assert (await fetcher.run(job(force=True))).status == "error"
        assert len(ff.calls) == 2
        ff.fail = False
        assert (await fetcher.run(job(force=True))).status == "done"
        assert cache.get(RouteKey(FROM, "NCL", "KHI", CENTER)).status is EntryStatus.OK

    async def test_busy_when_another_request_holds_the_claim(self, fetcher, claims, ff):
        assert claims.claim(f"from_uk:KHI:NCL:{CENTER.isoformat()}", 90)
        assert (await fetcher.run(job())).status == "busy"
        assert ff.calls == []

    async def test_claim_is_released_after_running(self, fetcher, claims):
        await fetcher.run(job())
        assert claims.claim(f"from_uk:KHI:NCL:{CENTER.isoformat()}", 90)

    async def test_unknown_group_and_past_dates(self, fetcher):
        assert (await fetcher.run(job(group="XXX"))).status == "error"
        assert (await fetcher.run(job(day=date.today() - timedelta(days=1)))).status == "skipped"


class TestAutoSwitching:
    @pytest.fixture(autouse=True)
    def _auto_mode(self, settings):
        set_mode(settings, ProviderMode.AUTO)

    async def test_switches_to_serpapi_after_three_failures(self, fetcher, ff, serp, providers):
        ff.fail = True
        await drain(fetcher)
        assert len(ff.calls) == 3
        assert providers.serpapi_until is not None
        assert len(serp.calls) > 0

    async def test_provider_health_is_shared_through_the_database(
        self, providers, ff, serp, settings, usage
    ):
        providers.record_failure(ff, "x")
        providers.record_failure(ff, "x")
        other_instance = ProviderManager(ff, serp, settings, usage)
        assert other_instance.consecutive_ff_failures == 2
        other_instance.record_failure(ff, "x")
        assert providers.serpapi_until is not None and providers.active() is serp
        assert providers.last_error["fast_flights"] == "x"

    async def test_stays_on_fast_flights_without_key(self, cache, settings, usage, claims, ff):
        pm = ProviderManager(ff, FakeSerpApi(available=False), settings, usage)
        f = Fetcher(cache, settings, pm, claims, GROUPS, sleep=no_sleep)
        ff.fail = True
        await drain(f)
        assert pm.serpapi_until is None
        assert len(ff.calls) == 42

    def test_serpapi_mode_requires_key(self, settings, usage, ff):
        set_mode(settings, ProviderMode.SERPAPI)
        pm = ProviderManager(ff, FakeSerpApi(available=False), settings, usage)
        with pytest.raises(ProviderUnavailableError):
            pm.active()

    def test_cap_reached_falls_back_to_fast_flights(self, providers, settings, ff):
        settings.save(settings.get().model_copy(update={"serpapi_monthly_cap": 0}))
        providers.serpapi_until = utcnow() + timedelta(minutes=30)
        assert providers.active() is ff


class TestSerpApiRun:
    async def test_detail_and_overview_queries(self, fetcher, serp, settings, cache):
        set_mode(settings, ProviderMode.SERPAPI)
        await drain(fetcher)
        details = [c for c in serp.calls if c[0] == "NCL"]
        overviews = [c for c in serp.calls if "," in c[0]]
        assert len(details) == 7
        assert len(overviews) == 7  # LON+MAN (+NCL) fit one batch: one per day
        assert len(serp.calls) == 14
        assert cache.get(RouteKey(FROM, "LHR", "KHI", CENTER)).completeness is Completeness.PARTIAL
        assert cache.get(RouteKey(FROM, "NCL", "KHI", CENTER)).completeness is Completeness.FULL

    async def test_selecting_city_upgrades_partial_to_full(self, fetcher, serp, settings, cache):
        set_mode(settings, ProviderMode.SERPAPI)
        await drain(fetcher)
        before = len(serp.calls)
        await drain(fetcher, group="LON")
        assert len(serp.calls) == before + 7
        assert cache.get(RouteKey(FROM, "LTN", "KHI", CENTER)).completeness is Completeness.FULL

    async def test_too_many_airports_halves_batch(self, fetcher, serp, settings):
        set_mode(settings, ProviderMode.SERPAPI)
        serp.reject_over = 4
        await drain(fetcher)
        assert int(settings.get_value(BATCH_SIZE_KEY)) <= 4


class TestDelay:
    def test_delay_within_configured_range(self, fetcher, settings):
        settings.save(settings.get().model_copy(update={"delay_seconds": (0.1, 0.2)}))
        assert all(0.1 <= fetcher.delay_seconds() <= 0.2 for _ in range(50))

    def test_backs_off_after_fast_flights_failures(self, fetcher, settings, providers):
        settings.save(settings.get().model_copy(update={"delay_seconds": (0.5, 0.5)}))
        providers.consecutive_ff_failures = 2
        assert fetcher.delay_seconds() == 2.0
        providers.consecutive_ff_failures = 20
        assert fetcher.delay_seconds() == 30.0


class TestDoubleCheckEmpty:
    @pytest.fixture(autouse=True)
    def _auto_mode(self, settings):
        set_mode(settings, ProviderMode.AUTO)

    async def test_auto_rechecks_empty_airports_with_serpapi(self, fetcher, ff, serp, cache):
        ff.empty_for = {"NCL", "STN"}
        await drain(fetcher)
        # One SerpApi query per empty group-day: NCL x7 and STN (in London) x7.
        assert sorted(c[0] for c in serp.calls) == ["NCL"] * 7 + ["STN"] * 7
        e = cache.get(RouteKey(FROM, "NCL", "KHI", CENTER))
        assert e.provider == "serpapi" and e.status is EntryStatus.OK
        assert e.completeness is Completeness.FULL
        assert cache.get(RouteKey(FROM, "LHR", "KHI", CENTER)).provider == "fast_flights"
        assert fetcher.plan(FROM, "KHI", CENTER, "NCL") == []

    async def test_serpapi_also_empty_is_stored_as_empty(self, fetcher, ff, serp, cache):
        ff.empty_for = {"NCL"}
        serp.inner.empty_for = {"NCL"}
        await fetcher.run(job())
        e = cache.get(RouteKey(FROM, "NCL", "KHI", CENTER))
        assert e.status is EntryStatus.EMPTY and e.provider == "serpapi"

    async def test_failed_double_check_keeps_fast_flights_result(self, fetcher, ff, serp, cache):
        ff.empty_for = {"NCL"}
        serp.inner.fail = True
        await fetcher.run(job())
        e = cache.get(RouteKey(FROM, "NCL", "KHI", CENTER))
        assert e.status is EntryStatus.EMPTY and e.provider == "fast_flights"

    async def test_not_in_fast_flights_mode(self, fetcher, ff, serp, settings):
        set_mode(settings, ProviderMode.FAST_FLIGHTS)
        ff.empty_for = {"NCL"}
        await fetcher.run(job())
        assert serp.calls == []

    async def test_respects_cap(self, fetcher, ff, serp, settings):
        settings.save(settings.get().model_copy(update={"serpapi_monthly_cap": 0}))
        ff.empty_for = {"NCL"}
        await fetcher.run(job())
        assert serp.calls == []


def test_make_flight_helper():
    assert make_flight("NCL", "KHI", CENTER, 1).price_gbp == 1
