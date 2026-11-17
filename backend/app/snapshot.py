"""Persist the SQLite database to Google Cloud Storage across restarts.

Cloud Run (and similar hosts) wipe the local disk whenever an instance stops,
which is at most ~15 minutes after the last request. With CACHE_SNAPSHOT_URI
set, the database is downloaded at startup, uploaded every few minutes while
it has changed, and uploaded once more during shutdown.

Uses the GCS JSON API with a token from the instance metadata server, so no
extra dependencies or key files are needed on Google Cloud.
"""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from .cache import Database

log = logging.getLogger(__name__)

METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)
GCS_API = "https://storage.googleapis.com"
SNAPSHOT_INTERVAL_SECONDS = 5 * 60


def parse_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"CACHE_SNAPSHOT_URI must look like gs://bucket/object, got {uri!r}")
    bucket, _, obj = uri[len("gs://") :].partition("/")
    if not bucket or not obj:
        raise ValueError(f"CACHE_SNAPSHOT_URI must look like gs://bucket/object, got {uri!r}")
    return bucket, obj


class GcsSnapshot:
    def __init__(self, uri: str, client: httpx.AsyncClient | None = None):
        self.bucket, self.object = parse_gs_uri(uri)
        self._client = client or httpx.AsyncClient(timeout=30)
        self._token: str | None = None
        self._token_expiry = 0.0
        self._saved_changes: int | None = None

    async def _auth(self) -> dict[str, str]:
        if self._token is None or time.monotonic() > self._token_expiry:
            resp = await self._client.get(METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"})
            resp.raise_for_status()
            data = resp.json()
            self._token = data["access_token"]
            self._token_expiry = time.monotonic() + int(data.get("expires_in", 300)) - 60
        return {"Authorization": f"Bearer {self._token}"}

    async def restore(self, db_path: Path) -> bool:
        """Download the snapshot to `db_path`. Returns False if there is none yet."""
        url = f"{GCS_API}/storage/v1/b/{self.bucket}/o/{quote(self.object, safe='')}"
        try:
            resp = await self._client.get(url, params={"alt": "media"}, headers=await self._auth())
            if resp.status_code == 404:
                log.info("no cache snapshot at gs://%s/%s yet", self.bucket, self.object)
                return False
            resp.raise_for_status()
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("cache snapshot restore failed (starting empty): %s", exc)
            return False
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_bytes(resp.content)
        log.info("restored cache snapshot (%d KB)", len(resp.content) // 1024)
        return True

    async def save(self, db: Database, force: bool = False) -> bool:
        """Upload the database if it changed since the last save. Never raises."""
        changes = db.total_changes
        if not force and changes == self._saved_changes:
            return False
        try:
            with tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / "snapshot.db"
                db.backup_to(copy)
                resp = await self._client.post(
                    f"{GCS_API}/upload/storage/v1/b/{self.bucket}/o",
                    params={"uploadType": "media", "name": self.object},
                    headers={**await self._auth(), "Content-Type": "application/octet-stream"},
                    content=copy.read_bytes(),
                )
                resp.raise_for_status()
        except (httpx.HTTPError, KeyError, ValueError, OSError) as exc:
            log.warning("cache snapshot save failed: %s", exc)
            return False
        self._saved_changes = changes
        return True

    async def aclose(self) -> None:
        await self._client.aclose()
