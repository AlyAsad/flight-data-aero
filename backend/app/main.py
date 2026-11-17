"""FastAPI application: the API, plus the built frontend when running locally.

The same app runs locally (`make start`) and on Vercel. The built frontend in
frontend/dist is mounted after the API routes; Vercel promotes those static
files to its CDN at build time.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import FRONTEND_DIST, get_env
from .state import AppState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(state: AppState | None = None, serve_frontend: bool = True) -> FastAPI:
    services = state or AppState.build(get_env())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await services.providers.serpapi.aclose()
            services.db.close()

    app = FastAPI(title="Aero", lifespan=lifespan)
    app.state.services = services
    app.include_router(router)

    if serve_frontend and (FRONTEND_DIST / "index.html").exists():
        # Mounted after the API router, so /api routes always win.
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app


app = create_app()
