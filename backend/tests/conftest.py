from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from pydantic_settings import SettingsConfigDict

from app.cache import Database, FlightCache, SettingsStore, UsageStore
from app.config import EnvSettings
from app.events import EventBus
from app.fetcher import Fetcher, ProviderManager
from app.models import FlightOption, UKGroup
from app.providers.base import ProviderError, Segment, build_flight_option
from app.providers.serpapi import SerpApiProvider

FIXTURES = Path(__file__).parent / "fixtures"

GROUPS = (
    UKGroup(id="LON", name="London", airports=["LHR", "LGW", "STN", "LTN"]),
    UKGroup(id="MAN", name="Manchester", airports=["MAN"]),
    UKGroup(id="NCL", name="Newcastle", airports=["NCL"]),
)


def make_flight(
    origin: str, dest: str, day: date, price: float | None, provider="fake"
) -> FlightOption:
    dep = datetime(day.year, day.month, day.day, 10, 0)
    return build_flight_option(
        [Segment(origin, dest, dep, dep + timedelta(hours=9), 540, "Test Air", None, None)],
        provider=provider,
        price_gbp=price,
    )


class FakeProvider:
    """Records calls; returns one flight priced by airport, or raises when told to."""

    def __init__(self, name: str = "fast_flights", fail: bool = False):
        self.name = name
        self.fail = fail
        self.empty_for: set[str] = set()  # airports that return no flights
        self.calls: list[tuple[str, str, date]] = []

    async def search_one_way(self, origin: str, destination: str, date: date) -> list[FlightOption]:
        self.calls.append((origin, destination, date))
        if self.fail:
            raise ProviderError("boom")
        flights = []
        for o in origin.split(","):
            for d in destination.split(","):
                if o in self.empty_for or d in self.empty_for:
                    continue
                flights.append(make_flight(o, d, date, 300 + len(self.calls)))
        return flights


class FakeSerpApi(SerpApiProvider):
    def __init__(self, available: bool = True, reserve=lambda: True):
        super().__init__("key" if available else "", reserve=reserve)
        self.inner = FakeProvider(name="serpapi")
        self.reject_over: int | None = None

    @property
    def calls(self):
        return self.inner.calls

    async def search_one_way(self, origin, destination, date):
        from app.providers.base import TooManyAirportsError

        n = max(len(origin.split(",")), len(destination.split(",")))
        if self.reject_over is not None and n > self.reject_over:
            self.inner.calls.append((origin, destination, date))
            raise TooManyAirportsError("too many airports")
        return await self.inner.search_one_way(origin, destination, date)


async def no_sleep(_seconds: float) -> None:
    return None


class IsolatedEnv(EnvSettings):
    """EnvSettings that ignores the developer's .env and environment variables."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, *args, **kwargs):
        return (init_settings,)


@pytest.fixture
def env(tmp_path) -> EnvSettings:
    return IsolatedEnv(db_path=tmp_path / "test.db", serpapi_api_key="")


@pytest.fixture
def db(env) -> Database:
    database = Database(env.db_path)
    yield database
    database.close()


@pytest.fixture
def cache(db) -> FlightCache:
    return FlightCache(db)


@pytest.fixture
def settings(db, env) -> SettingsStore:
    return SettingsStore(db, env)


@pytest.fixture
def usage(db) -> UsageStore:
    return UsageStore(db)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def ff() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def serp() -> FakeSerpApi:
    return FakeSerpApi()


@pytest.fixture
def providers(ff, serp, settings, usage, bus) -> ProviderManager:
    return ProviderManager(ff, serp, settings, usage, bus)


@pytest.fixture
def fetcher(cache, settings, providers, bus) -> Fetcher:
    return Fetcher(cache, settings, providers, bus, GROUPS, default_batch_size=7, sleep=no_sleep)
