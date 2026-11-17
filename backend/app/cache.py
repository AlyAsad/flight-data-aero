"""SQLite persistence: route-day cache entries, user settings and SerpApi usage."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pydantic import TypeAdapter

from .config import ERROR_CACHE_MINUTES, EnvSettings
from .models import (
    AppSettings,
    Completeness,
    Direction,
    EntryStatus,
    FlightOption,
    ProviderMode,
)

_flights_adapter = TypeAdapter(list[FlightOption])

SCHEMA = """
CREATE TABLE IF NOT EXISTS route_days (
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
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS serpapi_usage (
    month TEXT PRIMARY KEY,
    count INTEGER NOT NULL
);
"""


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


class Database:
    """Thin thread-safe wrapper around a single SQLite connection."""

    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self.tx() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._conn:
            yield self._conn

    def close(self) -> None:
        self._conn.close()


class SettingsStore:
    """User settings persisted in SQLite; `.env` values seed the first run."""

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
        with self._db.tx() as c:
            row = c.execute("SELECT value FROM settings WHERE key = 'app'").fetchone()
        if row is None:
            settings = self._defaults()
            self.save(settings)
            return settings
        # Cache expiry is server configuration (CACHE_TTL_DAYS), never user-editable.
        stored = AppSettings.model_validate_json(row["value"])
        return stored.model_copy(update={"ttl_days": self._env.cache_ttl_days})

    def save(self, settings: AppSettings) -> None:
        with self._db.tx() as c:
            c.execute(
                "INSERT INTO settings(key, value) VALUES('app', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (settings.model_dump_json(),),
            )

    def get_value(self, key: str) -> str | None:
        with self._db.tx() as c:
            row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_value(self, key: str, value: str) -> None:
        with self._db.tx() as c:
            c.execute(
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
        with self._db.tx() as c:
            row = c.execute(
                "SELECT count FROM serpapi_usage WHERE month = ?", (self.month_key(now),)
            ).fetchone()
        return row["count"] if row else 0

    def sync_remote(self, remote_used: int, now: datetime | None = None) -> int:
        """Raise the local count to SerpApi's own figure if that is higher.

        Free hosts wipe the local database on restart, so the authoritative
        count from SerpApi's Account API keeps the cap enforced. Taking the max
        never under-counts.
        """
        month = self.month_key(now)
        with self._db.tx() as c:
            c.execute(
                "INSERT INTO serpapi_usage(month, count) VALUES(?, ?) "
                "ON CONFLICT(month) DO UPDATE SET count = MAX(count, excluded.count)",
                (month, remote_used),
            )
        return self.used(now)

    def try_reserve(self, cap: int, now: datetime | None = None) -> bool:
        """Count one search if doing so keeps usage within `cap`. Returns False otherwise."""
        month = self.month_key(now)
        with self._db.tx() as c:
            row = c.execute("SELECT count FROM serpapi_usage WHERE month = ?", (month,)).fetchone()
            used = row["count"] if row else 0
            if used >= cap:
                return False
            c.execute(
                "INSERT INTO serpapi_usage(month, count) VALUES(?, 1) "
                "ON CONFLICT(month) DO UPDATE SET count = count + 1",
                (month,),
            )
            return True


class FlightCache:
    """Route-day cache keyed by (direction, uk_airport, foreign_airport, date)."""

    def __init__(self, db: Database):
        self._db = db

    # ------------------------------------------------------------------ reads

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> CacheEntry:
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
        with self._db.tx() as c:
            row = c.execute(
                "SELECT * FROM route_days WHERE direction = ? AND uk_airport = ? "
                "AND foreign_airport = ? AND date = ?",
                (key.direction.value, key.uk_airport, key.foreign_airport, key.date.isoformat()),
            ).fetchone()
        return self._row_to_entry(row) if row else None

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
        with self._db.tx() as c:
            rows = c.execute(sql, (direction.value, foreign, *airports, *days)).fetchall()
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
        with self._db.tx() as c:
            c.execute(
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
        existing = self.get(key)
        with self._db.tx() as c:
            if existing is not None and existing.has_data:
                c.execute(
                    "UPDATE route_days SET error_message = ?, retry_after = ? "
                    "WHERE direction = ? AND uk_airport = ? AND foreign_airport = ? AND date = ?",
                    (
                        message,
                        _ts(retry),
                        key.direction.value,
                        key.uk_airport,
                        key.foreign_airport,
                        key.date.isoformat(),
                    ),
                )
            else:
                c.execute(
                    "INSERT OR REPLACE INTO route_days VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        key.direction.value,
                        key.uk_airport,
                        key.foreign_airport,
                        key.date.isoformat(),
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
        with self._db.tx() as c:
            rows = c.execute(
                "SELECT rowid, fetched_at FROM route_days WHERE status != ?",
                (EntryStatus.ERROR.value,),
            ).fetchall()
            c.executemany(
                "UPDATE route_days SET expires_at = ? WHERE rowid = ?",
                [
                    (_ts(datetime.fromisoformat(r["fetched_at"]) + timedelta(days=ttl_days)), r[0])
                    for r in rows
                ],
            )

    def expire_all(self, now: datetime | None = None) -> int:
        """Mark every entry expired (data is kept and shown as stale)."""
        now_s = _ts(now or utcnow())
        with self._db.tx() as c:
            cur = c.execute("UPDATE route_days SET expires_at = ?, retry_after = NULL", (now_s,))
            return cur.rowcount

    def clear(self) -> int:
        with self._db.tx() as c:
            return c.execute("DELETE FROM route_days").rowcount
