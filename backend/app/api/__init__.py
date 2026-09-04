"""HTTP layer."""

from __future__ import annotations

from fastapi import APIRouter

from . import routes_connection, routes_execute, routes_queue, routes_scan

api_router = APIRouter(prefix="/api")
api_router.include_router(routes_connection.router, tags=["connection"])
api_router.include_router(routes_scan.router, tags=["scan"])
api_router.include_router(routes_queue.router, tags=["queue"])
api_router.include_router(routes_execute.router, tags=["execute"])

__all__ = ["api_router"]
