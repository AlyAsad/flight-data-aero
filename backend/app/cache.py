"""Persistence: route-day cache entries, settings, SerpApi usage and fetch claims.

Works on any `db.Database` backend (a local SQLite file, or Turso when deployed).
Every operation is a single statement or batch, because on serverless hosts
many short-lived instances can share one database.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import TypeAdapter

from .config import ERROR_CACHE_MINUTES, EnvSettings
from .db import Database, SqliteDatabase, TursoDatabase
from .models import (
    AppSettings,
    Completeness,
    Direction,
    EntryStatus,
    FlightOption,
    ProviderMode,
)

_flights_adapter = TypeAdapter(list[FlightOption])

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS route_days (
        direction       TEXT NOT NULL,
        uk_airport      TEXT NOT NULL,
        foreign_airport TEXT NOT NULL,
        date            TEXT NOT NULL,
        status          TEXT NOT NULL,
        completeness    TEXT NOT NULL,
        provider        TEXT NOT NULL,
        flights_json    TEXT NOT NULL,
        fetched_at      TEXT NOT NULL,
        expires_at      TEXT NOT NULL,
        error_message   TEXT,
        retry_after     TEXT,
        PRIMARY KEY (direction, uk_airport, foreign_airport, date)
    )""",
    "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS serpapi_usage (month TEXT PRIMARY KEY, count INTEGER NOT NULL)",
    # A short-lived lock per group-day, so two visitors don't fetch the same thing at once.
    "CREATE TABLE IF NOT EXISTS claims (key TEXT PRIMARY KEY, until TEXT NOT NULL)",
]

APPLIED_TTL_KEY = "applied_ttl_days"


def open_database(env: EnvSettings) -> Database:
    """Turso when TURSO_DATABASE_URL is set, otherwise the local SQLite file."""
    db: Database
    if env.turso_database_url:
        db = TursoDatabase(env.turso_database_url, env.turso_auth_token)
    else:
        db = SqliteDatabase(env.db_path)
    db.execute_batch([(sql, ()) for sql in SCHEMA])
    return db


def utcnow() -> datetime:
    return datetime.now(UTC)


def _ts(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(frozen=True)
class RouteKey:
    direction: Direction
    uk_airport: str
    foreign_airport: str
    date: date


@dataclass
class CacheEntry:
    key: RouteKey
    status: EntryStatus
    completeness: Completeness
    provider: str
    flights: list[FlightOption]
    fetched_at: datetime
    expires_at: datetime
    error_message: str | None = None
    retry_after: datetime | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or utcnow()) >= self.expires_at

    def in_error_backoff(self, now: datetime | None = None) -> bool:
        return self.retry_after is not None and (now or utcnow()) < self.retry_after

    @property
    def has_data(self) -> bool:
        return self.status in (EntryStatus.OK, EntryStatus.EMPTY)


class SettingsStore:
    """Settings persisted in the database; `.env` values seed the first run."""

    def __init__(self, db: Database, env: EnvSettings):
        self._db = db
        self._env = env

    def _defaults(self) -> AppSettings:
        mode = self._env.provider_mode
        return AppSettings(
            ttl_days=self._env.cache_ttl_days,
            provider_mode=ProviderMode(mode) if mode in ProviderMode else ProviderMode.FAST_FLIGHTS,
            serpapi_monthly_cap=min(self._env.serpapi_monthly_cap, 250),
            concurrency=self._env.fetch_concurrency,
            delay_seconds=self._env.fetch_delay_seconds,
        )

    def get(self) -> AppSettings:
        raw = self.get_value("app")
        if raw is None:
            settings = self._defaults()
            self.save(settings)
            return settings
        # Cache expiry is server configuration (CACHE_TTL_DAYS), never user-editable.
        stored = AppSettings.model_validate_json(raw)
        return stored.model_copy(update={"ttl_days": self._env.cache_ttl_days})

    def save(self, settings: AppSettings) -> None:
        self.set_value("app", settings.model_dump_json())

    def get_value(self, key: str) -> str | None:
        rows = self._db.query("SELECT value FROM settings WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def set_value(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


class UsageStore:
    """Monthly SerpApi search counter with an atomic cap check."""

    def __init__(self, db: Database):
        self._db = db

    @staticmethod
    def month_key(now: datetime | None = None) -> str:
        return (now or utcnow()).strftime("%Y-%m")

    def used(self, now: datetime | None = None) -> int:
        rows = self._db.query(
            "SELECT count FROM serpapi_usage WHERE month = ?", (self.month_key(now),)
        )
        return int(rows[0]["count"]) if rows else 0

    def sync_remote(self, remote_used: int, now: datetime | None = None) -> int:
        """Raise the local count to SerpApi's own figure if that is higher (never lowers it)."""
        self._db.execute(
            "INSERT INTO serpapi_usage(month, count) VALUES(?, ?) "
            "ON CONFLICT(month) DO UPDATE SET count = MAX(count, excluded.count)",
            (self.month_key(now), remote_used),
        )
        return self.used(now)

    def try_reserve(self, cap: int, now: datetime | None = None) -> bool:
        """Count one search if doing so keeps usage within `cap`. Returns False otherwise.

        One statement, so concurrent instances can't both take the last search.
        """
        if cap <= 0:
            return False
        changed = self._db.execute(
            "INSERT INTO serpapi_usage(month, count) VALUES(?, 1) "
            "ON CONFLICT(month) DO UPDATE SET count = count + 1 WHERE serpapi_usage.count < ?",
            (self.month_key(now), cap),
        )
        return changed > 0


class ClaimStore:
    """Short-lived locks so only one request fetches a given group-day at a time."""

    def __init__(self, db: Database):
        self._db = db

    def claim(self, key: str, seconds: float, now: datetime | None = None) -> bool:
        now = now or utcnow()
        changed = self._db.execute(
            "INSERT INTO claims(key, until) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET until = excluded.until WHERE claims.until < ?",
            (key, _ts(now + timedelta(seconds=seconds)), _ts(now)),
        )
        return changed > 0

    def release(self, key: str) -> None:
        self._db.execute("DELETE FROM claims WHERE key = ?", (key,))


class FlightCache:
    """Route-day cache keyed by (direction, uk_airport, foreign_airport, date)."""

    def __init__(self, db: Database):
        self._db = db

    # ------------------------------------------------------------------ reads

    @staticmethod
    def _row_to_entry(row: dict[str, Any]) -> CacheEntry:
        fetched_at = datetime.fromisoformat(row["fetched_at"])
        flights = _flights_adapter.validate_json(row["flights_json"])
        for f in flights:
            f.fetched_at = fetched_at
        return CacheEntry(
            key=RouteKey(
                Direction(row["direction"]),
                row["uk_airport"],
                row["foreign_airport"],
                date.fromisoformat(row["date"]),
            ),
            status=EntryStatus(row["status"]),
            completeness=Completeness(row["completeness"]),
            provider=row["provider"],
            flights=flights,
            fetched_at=fetched_at,
            expires_at=datetime.fromisoformat(row["expires_at"]),
            error_message=row["error_message"],
            retry_after=_parse_ts(row["retry_after"]),
        )

    def get(self, key: RouteKey) -> CacheEntry | None:
        rows = self._db.query(
            "SELECT * FROM route_days WHERE direction = ? AND uk_airport = ? "
            "AND foreign_airport = ? AND date = ?",
            (key.direction.value, key.uk_airport, key.foreign_airport, key.date.isoformat()),
        )
        return self._row_to_entry(rows[0]) if rows else None

    def get_many(
        self, direction: Direction, foreign: str, uk_airports: Iterable[str], dates: Iterable[date]
    ) -> dict[RouteKey, CacheEntry]:
        airports, days = list(uk_airports), [d.isoformat() for d in dates]
        if not airports or not days:
            return {}
        sql = (
            "SELECT * FROM route_days WHERE direction = ? AND foreign_airport = ? "
            f"AND uk_airport IN ({','.join('?' * len(airports))}) "
            f"AND date IN ({','.join('?' * len(days))})"
        )
        rows = self._db.query(sql, (direction.value, foreign, *airports, *days))
        entries = (self._row_to_entry(r) for r in rows)
        return {e.key: e for e in entries}

    # ------------------------------------------------------------------ writes

    def store_result(
        self,
        key: RouteKey,
        flights: list[FlightOption],
        *,
        provider: str,
        completeness: Completeness,
        ttl_days: int,
        now: datetime | None = None,
    ) -> bool:
        """Store a successful fetch. Returns False if skipped to protect better data.

        Partial (overview) data never replaces unexpired full data.
        """
        now = now or utcnow()
        if completeness is Completeness.PARTIAL:
            existing = self.get(key)
            if (
                existing is not None
                and existing.has_data
                and existing.completeness is Completeness.FULL
                and not existing.is_expired(now)
            ):
                return False
        status = EntryStatus.OK if flights else EntryStatus.EMPTY
        payload = _flights_adapter.dump_json(
            [f.model_copy(update={"fetched_at": None}) for f in flights]
        ).decode()
        self._db.execute(
            "INSERT OR REPLACE INTO route_days VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
            (
                key.direction.value,
                key.uk_airport,
                key.foreign_airport,
                key.date.isoformat(),
                status.value,
                completeness.value,
                provider,
                payload,
                _ts(now),
                _ts(now + timedelta(days=ttl_days)),
            ),
        )
        return True

    def store_error(
        self, key: RouteKey, message: str, *, provider: str, now: datetime | None = None
    ) -> None:
        """Record a failed fetch, cached for 15 minutes.

        If earlier data exists it is kept (shown as stale); only the error and
        retry time are recorded alongside it.
        """
        now = now or utcnow()
        retry = now + timedelta(minutes=ERROR_CACHE_MINUTES)
        where = (key.direction.value, key.uk_airport, key.foreign_airport, key.date.isoformat())
        existing = self.get(key)
        if existing is not None and existing.has_data:
            self._db.execute(
                "UPDATE route_days SET error_message = ?, retry_after = ? "
                "WHERE direction = ? AND uk_airport = ? AND foreign_airport = ? AND date = ?",
                (message, _ts(retry), *where),
            )
        else:
            self._db.execute(
                "INSERT OR REPLACE INTO route_days VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    *where,
                    EntryStatus.ERROR.value,
                    Completeness.FULL.value,
                    provider,
                    "[]",
                    _ts(now),
                    _ts(retry),
                    message,
                    _ts(retry),
                ),
            )

    def recompute_expiry(self, ttl_days: int) -> None:
        """Apply a new TTL to all successful entries, based on their fetched_at."""
        rows = self._db.query(
            "SELECT rowid AS rid, fetched_at FROM route_days WHERE status != ?",
            (EntryStatus.ERROR.value,),
        )
        self._db.execute_batch(
            [
                (
                    "UPDATE route_days SET expires_at = ? WHERE rowid = ?",
                    (
                        _ts(datetime.fromisoformat(r["fetched_at"]) + timedelta(days=ttl_days)),
                        r["rid"],
                    ),
                )
                for r in rows
            ]
        )

    def apply_ttl_if_changed(self, ttl_days: int, settings: SettingsStore) -> bool:
        """Re-apply CACHE_TTL_DAYS to stored entries, only when it changed since last time."""
        if settings.get_value(APPLIED_TTL_KEY) == str(ttl_days):
            return False
        self.recompute_expiry(ttl_days)
        settings.set_value(APPLIED_TTL_KEY, str(ttl_days))
        return True

    def expire_all(self, now: datetime | None = None) -> int:
        """Mark every entry expired (data is kept and shown as stale)."""
        return self._db.execute(
            "UPDATE route_days SET expires_at = ?, retry_after = NULL", (_ts(now or utcnow()),)
        )

    def clear(self) -> int:
        return self._db.execute("DELETE FROM route_days")
