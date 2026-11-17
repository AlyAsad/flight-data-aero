"""FastAPI application: API routes plus (optionally) the built frontend."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import FRONTEND_DIST, get_env
from .state import AppState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(state: AppState | None = None, serve_frontend: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        services = state or AppState.build(get_env())
        app.state.services = services
        services.fetcher.start()
        try:
            yield
        finally:
            await services.fetcher.stop()
            await services.providers.serpapi.aclose()
            services.db.close()

    app = FastAPI(title="UK Multi-Airport Flight Finder", lifespan=lifespan)
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
