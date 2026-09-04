"""Queue execution control and the live progress stream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..models import ExecutionState, ProgressEvent
from ..service import service

router = APIRouter()


class StartRequest(BaseModel):
    #: Omit to use the stored setting. Explicit False is what actually writes data.
    dry_run: bool | None = None
    #: Must be exactly "MOVE MY FILES" to run for real; ignored in dry-run.
    confirm: str | None = None


class StartResponse(BaseModel):
    started: bool
    message: str
    state: ExecutionState


CONFIRM_PHRASE = "MOVE MY FILES"


@router.get("/execute/state", response_model=ExecutionState)
def state() -> ExecutionState:
    return service.executor.state


@router.post("/execute/start", response_model=StartResponse)
def start(request: StartRequest) -> StartResponse:
    if service.index is None:
        raise HTTPException(status_code=409, detail="no active scan; run a scan first")

    dry_run = service.connection.dry_run if request.dry_run is None else request.dry_run
    if not dry_run and request.confirm != CONFIRM_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f"a live run must be confirmed with the exact phrase {CONFIRM_PHRASE!r}",
        )

    started, message = service.executor.start(
        index=service.index,
        planner=service.planner,
        backend=service.backend(),
        path_for=service.path_for,
        dry_run=dry_run,
        prune_stop_for=service.prune_stop_for,
        persist=service.persist_jobs,
    )
    if started:
        service.persist_jobs()
    return StartResponse(started=started, message=message, state=service.executor.state)


@router.post("/execute/pause", response_model=ExecutionState)
def pause() -> ExecutionState:
    service.executor.pause()
    return service.executor.state


@router.post("/execute/resume", response_model=ExecutionState)
def resume() -> ExecutionState:
    service.executor.resume()
    return service.executor.state


@router.post("/execute/stop", response_model=ExecutionState)
def stop() -> ExecutionState:
    service.executor.stop()
    return service.executor.state


@router.get("/execute/log", response_model=list[ProgressEvent])
def log() -> list[ProgressEvent]:
    return list(service.executor.replay())


@router.get("/execute/events")
async def events(request: Request) -> StreamingResponse:
    loop = asyncio.get_running_loop()
    queue = service.executor.subscribe(loop)

    async def stream() -> AsyncIterator[str]:
        try:
            # Replay first so a client that connects mid-run sees the whole log.
            for event in service.executor.replay():
                yield f"data: {event.model_dump_json()}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {event.model_dump_json()}\n\n"
        finally:
            service.executor.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
