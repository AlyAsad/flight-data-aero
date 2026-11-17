from datetime import date, timedelta

from app.cache import RouteKey, utcnow
from app.models import Completeness, Direction, EntryStatus, ProviderMode
from tests.conftest import make_flight

D = date(2026, 12, 31)
KEY = RouteKey(Direction.FROM_UK, "NCL", "KHI", D)


def test_store_and_read(cache):
    cache.store_result(
        KEY,
        [make_flight("NCL", "KHI", D, 400)],
        provider="fake",
        completeness=Completeness.FULL,
        ttl_days=2,
    )
    e = cache.get(KEY)
    assert e.status is EntryStatus.OK and len(e.flights) == 1
    assert e.flights[0].fetched_at == e.fetched_at
    assert not e.is_expired()
    assert e.is_expired(utcnow() + timedelta(days=2, seconds=1))


def test_empty_result_is_stored_as_empty(cache):
    cache.store_result(KEY, [], provider="fake", completeness=Completeness.FULL, ttl_days=2)
    assert cache.get(KEY).status is EntryStatus.EMPTY


def test_ttl_change_recomputes_expiry(cache):
    now = utcnow()
    cache.store_result(KEY, [], provider="f", completeness=Completeness.FULL, ttl_days=2, now=now)
    cache.recompute_expiry(5)
    assert cache.get(KEY).expires_at == now + timedelta(days=5)


def test_error_cached_15_minutes(cache):
    now = utcnow()
    cache.store_error(KEY, "boom", provider="f", now=now)
    e = cache.get(KEY)
    assert e.status is EntryStatus.ERROR
    assert e.expires_at == now + timedelta(minutes=15)
    assert e.in_error_backoff(now) and not e.in_error_backoff(now + timedelta(minutes=16))


def test_error_keeps_previous_data(cache):
    old = utcnow() - timedelta(days=3)
    cache.store_result(
        KEY,
        [make_flight("NCL", "KHI", D, 400)],
        provider="f",
        completeness=Completeness.FULL,
        ttl_days=2,
        now=old,
    )
    cache.store_error(KEY, "boom", provider="f")
    e = cache.get(KEY)
    assert e.status is EntryStatus.OK and len(e.flights) == 1
    assert e.is_expired() and e.error_message == "boom" and e.in_error_backoff()


def test_partial_never_replaces_fresh_full(cache):
    cache.store_result(
        KEY,
        [make_flight("NCL", "KHI", D, 400)],
        provider="f",
        completeness=Completeness.FULL,
        ttl_days=2,
    )
    stored = cache.store_result(
        KEY, [], provider="s", completeness=Completeness.PARTIAL, ttl_days=2
    )
    assert stored is False
    assert cache.get(KEY).completeness is Completeness.FULL


def test_full_replaces_partial(cache):
    cache.store_result(KEY, [], provider="s", completeness=Completeness.PARTIAL, ttl_days=2)
    cache.store_result(
        KEY,
        [make_flight("NCL", "KHI", D, 1)],
        provider="s",
        completeness=Completeness.FULL,
        ttl_days=2,
    )
    assert cache.get(KEY).completeness is Completeness.FULL


def test_expire_all_and_clear(cache):
    cache.store_result(KEY, [], provider="f", completeness=Completeness.FULL, ttl_days=2)
    assert cache.expire_all() == 1
    assert cache.get(KEY).is_expired()
    assert cache.clear() == 1
    assert cache.get(KEY) is None


def test_get_many_filters(cache):
    for a in ("LHR", "LGW"):
        cache.store_result(
            RouteKey(Direction.FROM_UK, a, "KHI", D),
            [],
            provider="f",
            completeness=Completeness.FULL,
            ttl_days=1,
        )
    cache.store_result(
        RouteKey(Direction.TO_UK, "LHR", "KHI", D),
        [],
        provider="f",
        completeness=Completeness.FULL,
        ttl_days=1,
    )
    got = cache.get_many(Direction.FROM_UK, "KHI", ["LHR", "LGW", "STN"], [D])
    assert {k.uk_airport for k in got} == {"LHR", "LGW"}


def test_settings_seeded_from_env_and_persisted(settings):
    s = settings.get()
    assert s.ttl_days == 2 and s.provider_mode is ProviderMode.AUTO
    settings.save(s.model_copy(update={"ttl_days": 4}))
    assert settings.get().ttl_days == 4


def test_serpapi_cap_is_never_exceeded(usage):
    assert all(usage.try_reserve(3) for _ in range(3))
    assert usage.try_reserve(3) is False
    assert usage.used() == 3
