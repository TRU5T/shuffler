"""FastAPI application entry point.

In production the built SPA is served from the same origin as the API; in
development Vite serves the UI on :5173 and proxies /api here.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import api_router
from .config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("shuffler")

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    from .service import service

    log.info("Shuffler %s starting", __version__)
    log.info(
        "backend=%s dry_run=%s",
        service.connection.storage_backend,
        service.connection.dry_run,
    )
    if service.connection.dry_run:
        log.info("DRY RUN is enabled: the executor will not modify any files")
    if service.index is not None:
        log.info("restored scan %s of %s", service.index.id, service.index.root or "/")
    if service.planner.jobs:
        log.info("restored %d queued job(s)", len(service.planner.jobs))

    yield

    service.executor.stop()


app = FastAPI(
    title="Unraid Disk Shuffler",
    version=__version__,
    lifespan=lifespan,
    description=(
        "Index the same directory across every Unraid data disk, find duplicates, "
        "and plan consolidation moves with live free-space projections."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/api/version")
def version() -> dict[str, str | bool]:
    return {
        "version": __version__,
        "backend": settings.storage_backend,
        "dry_run": settings.dry_run,
    }


def _mount_spa() -> None:
    dist = settings.static_dir
    index_html = dist / "index.html"
    if not index_html.is_file():
        log.warning("frontend build not found at %s; serving API only", dist)

        @app.get("/")
        def no_ui() -> JSONResponse:
            return JSONResponse(
                {
                    "detail": "UI not built. Run `npm run build` in frontend/, "
                    "or use the Vite dev server on :5173.",
                    "docs": "/docs",
                }
            )

        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        candidate = dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_html)


_mount_spa()
