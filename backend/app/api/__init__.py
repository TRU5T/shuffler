"""HTTP layer."""

from __future__ import annotations

from fastapi import APIRouter

from . import routes_connection, routes_execute, routes_queue, routes_scan, routes_suggest

api_router = APIRouter(prefix="/api")
api_router.include_router(routes_connection.router, tags=["connection"])
api_router.include_router(routes_scan.router, tags=["scan"])
api_router.include_router(routes_queue.router, tags=["queue"])
api_router.include_router(routes_execute.router, tags=["execute"])
api_router.include_router(routes_suggest.router, tags=["suggest"])

__all__ = ["api_router"]
