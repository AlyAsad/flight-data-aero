"""Database backends: a local SQLite file, or Turso (hosted, SQLite-compatible).

Both expose the same tiny interface, so the cache code doesn't care which one
is in use. Turso is used when TURSO_DATABASE_URL is set (e.g. on Vercel, where
there is no persistent disk); it is reached over its HTTP API with httpx, so no
extra client library is needed.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import httpx

Params = Sequence[Any]
Row = dict[str, Any]


class DatabaseError(Exception):
    """A statement failed (bad SQL, constraint, or the remote database is unreachable)."""


class Database(Protocol):
    def query(self, sql: str, params: Params = ()) -> list[Row]:
        """Run a SELECT and return rows as dicts."""
        ...

    def execute(self, sql: str, params: Params = ()) -> int:
        """Run a write and return the number of affected rows."""
        ...

    def execute_batch(self, statements: Sequence[tuple[str, Params]]) -> None:
        """Run several writes in one round trip."""
        ...

    def close(self) -> None: ...


class SqliteDatabase:
    """Local SQLite file (or ":memory:"), shared safely across threads."""

    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()

    def query(self, sql: str, params: Params = ()) -> list[Row]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    def execute(self, sql: str, params: Params = ()) -> int:
        with self._lock, self._conn:
            return self._conn.execute(sql, tuple(params)).rowcount

    def execute_batch(self, statements: Sequence[tuple[str, Params]]) -> None:
        with self._lock, self._conn:
            for sql, params in statements:
                self._conn.execute(sql, tuple(params))

    def close(self) -> None:
        self._conn.close()


def _encode(value: Any) -> dict[str, Any]:
    """Python value -> Turso (Hrana) argument."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "text", "value": str(value)}


def _decode(cell: dict[str, Any]) -> Any:
    kind = cell.get("type")
    if kind == "null":
        return None
    if kind == "integer":
        return int(cell["value"])
    if kind == "float":
        return float(cell["value"])
    return cell.get("value")


class TursoDatabase:
    """Turso over its HTTP "pipeline" API (https://docs.turso.tech/sdk/http/reference)."""

    def __init__(self, url: str, auth_token: str, client: httpx.Client | None = None):
        base = url.strip().rstrip("/")
        if base.startswith("libsql://"):
            base = "https://" + base[len("libsql://") :]
        self._endpoint = f"{base}/v2/pipeline"
        self._headers = {"Authorization": f"Bearer {auth_token}"}
        self._client = client or httpx.Client(timeout=15)

    def _pipeline(self, statements: Sequence[tuple[str, Params]]) -> list[dict[str, Any]]:
        requests = [
            {"type": "execute", "stmt": {"sql": sql, "args": [_encode(p) for p in params]}}
            for sql, params in statements
        ]
        requests.append({"type": "close"})
        try:
            resp = self._client.post(
                self._endpoint, json={"requests": requests}, headers=self._headers
            )
            resp.raise_for_status()
            results = resp.json()["results"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise DatabaseError(f"Turso request failed: {exc}") from exc
        out = []
        for result in results[: len(statements)]:
            if result.get("type") != "ok":
                message = (result.get("error") or {}).get("message", "unknown error")
                raise DatabaseError(f"Turso statement failed: {message}")
            out.append(result["response"]["result"])
        return out

    def query(self, sql: str, params: Params = ()) -> list[Row]:
        [result] = self._pipeline([(sql, params)])
        names = [c["name"] for c in result["cols"]]
        return [dict(zip(names, map(_decode, row), strict=True)) for row in result["rows"]]

    def execute(self, sql: str, params: Params = ()) -> int:
        [result] = self._pipeline([(sql, params)])
        return int(result.get("affected_row_count") or 0)

    def execute_batch(self, statements: Sequence[tuple[str, Params]]) -> None:
        if statements:
            self._pipeline(statements)

    def close(self) -> None:
        self._client.close()
