"""Application service container, built once per process in the lifespan."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from .cache import Database, FlightCache, SettingsStore, UsageStore
from .config import EnvSettings
from .events import EventBus
from .fetcher import Fetcher, ProviderManager
from .models import UKGroup
from .providers.base import FlightProvider
from .providers.fast_flights import FastFlightsProvider
from .providers.serpapi import SerpApiProvider
from .summary import SummaryBuilder
from .uk_groups import GroupStore, load_groups


@dataclass
class AppState:
    env: EnvSettings
    db: Database
    cache: FlightCache
    settings: SettingsStore
    usage: UsageStore
    bus: EventBus
    providers: ProviderManager
    fetcher: Fetcher
    summaries: SummaryBuilder
    group_store: GroupStore

    @property
    def groups(self) -> tuple[UKGroup, ...]:
        """Currently enabled UK groups."""
        return self.group_store.enabled()

    @classmethod
    def build(
        cls,
        env: EnvSettings,
        *,
        fast_flights: FlightProvider | None = None,
        serpapi: SerpApiProvider | None = None,
        groups: tuple[UKGroup, ...] | None = None,
        sleep=None,
    ) -> AppState:
        db = Database(env.db_path)
        cache = FlightCache(db)
        settings = SettingsStore(db, env)
        usage = UsageStore(db)
        bus = EventBus()
        group_store = GroupStore(settings, groups if groups is not None else load_groups())
        serp = serpapi or SerpApiProvider(
            env.serpapi_api_key,
            reserve=lambda: usage.try_reserve(settings.get().serpapi_monthly_cap),
        )
        providers = ProviderManager(
            fast_flights or FastFlightsProvider(env.google_consent_cookie),
            serp,
            settings,
            usage,
            bus,
        )
        kwargs = {"sleep": sleep} if sleep else {}
        fetcher = Fetcher(
            cache,
            settings,
            providers,
            bus,
            group_store.enabled(),
            default_batch_size=env.serpapi_batch_max_airports,
            **kwargs,
        )
        return cls(
            env=env,
            db=db,
            cache=cache,
            settings=settings,
            usage=usage,
            bus=bus,
            providers=providers,
            fetcher=fetcher,
            summaries=SummaryBuilder(cache),
            group_store=group_store,
        )


def get_state(request: Request) -> AppState:
    return request.app.state.services
