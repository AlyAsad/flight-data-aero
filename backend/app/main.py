"""FastAPI application: API routes plus (optionally) the built frontend."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .cache import utcnow
from .config import FRONTEND_DIST, get_env
from .snapshot import SNAPSHOT_INTERVAL_SECONDS, GcsSnapshot
from .state import AppState

USAGE_SYNC_INTERVAL_SECONDS = 30 * 60
log = logging.getLogger(__name__)


async def _sync_serpapi_usage(services: AppState) -> None:
    """Keep the local SerpApi counter at least as high as SerpApi's own count."""
    while True:
        remote = await services.providers.serpapi.account_usage()
        if remote is not None:
            used = services.usage.sync_remote(remote, utcnow())
            log.info("SerpApi usage synced: %d this month", used)
        await asyncio.sleep(USAGE_SYNC_INTERVAL_SECONDS)


async def _save_snapshots(services: AppState, snapshot: GcsSnapshot) -> None:
    while True:
        await asyncio.sleep(SNAPSHOT_INTERVAL_SECONDS)
        if await snapshot.save(services.db):
            log.info("cache snapshot saved")


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(state: AppState | None = None, serve_frontend: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        env = state.env if state else get_env()
        snapshot = GcsSnapshot(env.cache_snapshot_uri) if env.cache_snapshot_uri else None
        if snapshot and state is None:
            await snapshot.restore(env.db_path)  # must happen before the DB is opened
        services = state or AppState.build(env)
        app.state.services = services
        # CACHE_TTL_DAYS may have changed since entries were stored.
        services.cache.recompute_expiry(services.env.cache_ttl_days)
        services.fetcher.start()
        tasks = [asyncio.create_task(_sync_serpapi_usage(services))]
        if snapshot:
            tasks.append(asyncio.create_task(_save_snapshots(services, snapshot)))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await services.fetcher.stop()
            if snapshot:
                await snapshot.save(services.db)  # host is stopping: keep the cache
                await snapshot.aclose()
            await services.providers.serpapi.aclose()
            services.db.close()

    app = FastAPI(title="Aero", lifespan=lifespan)
    app.include_router(router)

    if serve_frontend and (FRONTEND_DIST / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            candidate = (FRONTEND_DIST / path).resolve()
            if path and candidate.is_file() and FRONTEND_DIST.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()
