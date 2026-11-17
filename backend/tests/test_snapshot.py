import sqlite3

import httpx
import pytest

from app.cache import Database
from app.snapshot import METADATA_TOKEN_URL, GcsSnapshot, parse_gs_uri


def test_parse_gs_uri():
    assert parse_gs_uri("gs://aero-cache/flights.db") == ("aero-cache", "flights.db")
    assert parse_gs_uri("gs://b/dir/x.db") == ("b", "dir/x.db")
    for bad in ("aero-cache/flights.db", "gs://bucket", "gs:///x.db"):
        with pytest.raises(ValueError):
            parse_gs_uri(bad)


class FakeGcs:
    """In-memory stand-in for the metadata server and the GCS JSON API."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.token_requests = 0
        self.uploads = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(METADATA_TOKEN_URL):
            assert request.headers["Metadata-Flavor"] == "Google"
            self.token_requests += 1
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer tok"
        if request.method == "POST":
            assert request.url.path == "/upload/storage/v1/b/bkt/o"
            self.objects[request.url.params["name"]] = request.content
            self.uploads += 1
            return httpx.Response(200, json={})
        name = request.url.path.rsplit("/o/", 1)[1].replace("%2F", "/")
        if name not in self.objects:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, content=self.objects[name])


@pytest.fixture
def gcs():
    return FakeGcs()


def snapshot(gcs: FakeGcs) -> GcsSnapshot:
    return GcsSnapshot(
        "gs://bkt/aero/flights.db", httpx.AsyncClient(transport=httpx.MockTransport(gcs.handler))
    )


async def test_save_then_restore_round_trip(tmp_path, gcs):
    db = Database(tmp_path / "a.db")
    with db.tx() as c:
        c.execute("INSERT INTO settings(key, value) VALUES('hello', 'world')")
    snap = snapshot(gcs)
    assert await snap.save(db) is True
    assert await snap.save(db) is False  # unchanged: no second upload
    assert gcs.uploads == 1 and gcs.token_requests == 1

    restored = tmp_path / "restored" / "flights.db"
    assert await snapshot(gcs).restore(restored) is True
    row = (
        sqlite3.connect(restored).execute("SELECT value FROM settings WHERE key='hello'").fetchone()
    )
    assert row == ("world",)


async def test_restore_without_snapshot_starts_empty(tmp_path, gcs):
    assert await snapshot(gcs).restore(tmp_path / "x.db") is False
    assert not (tmp_path / "x.db").exists()


async def test_failures_never_raise(tmp_path):
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    snap = GcsSnapshot("gs://bkt/x.db", httpx.AsyncClient(transport=httpx.MockTransport(broken)))
    assert await snap.restore(tmp_path / "x.db") is False
    assert await snap.save(Database(tmp_path / "y.db")) is False
