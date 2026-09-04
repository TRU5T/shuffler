"""The consolidation queue: previewing, queueing, resolving and reordering."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..backends.base import StorageError
from ..models import (
    Conflict,
    ConflictMode,
    DiskProjection,
    Job,
    Operation,
    QueuePlan,
)
from ..planner import compute_job, detect_conflicts
from ..models import Job as JobModel
from ..service import service

router = APIRouter()


class AddJobRequest(BaseModel):
    source_relpath: str
    target_disk: str


class PreviewResponse(BaseModel):
    source_relpath: str
    target_disk: str
    conflicts: list[Conflict]
    move_bytes: int
    reclaim_bytes: int
    move_files: int
    delete_files: int
    #: Cumulative state assuming the already-queued jobs run first.
    disks_after: dict[str, DiskProjection]
    overflow: bool
    blocked_reason: str | None = None


class ResolveRequest(BaseModel):
    relpath: str
    mode: ConflictMode | None = None
    keep_disk: str | None = None


class ResolveAllRequest(BaseModel):
    mode: ConflictMode | None = None
    keep_disk: str | None = None
    only_unresolved: bool = False


class ReorderRequest(BaseModel):
    order: list[str]


def _require_index():
    if service.index is None:
        raise HTTPException(status_code=409, detail="no active scan; run a scan first")
    return service.index


def _plan() -> QueuePlan:
    if service.planner.drop_finished():
        service.persist_jobs()
    return service.planner.plan(service.index, service.path_for)


@router.get("/queue", response_model=QueuePlan)
def get_queue() -> QueuePlan:
    return _plan()


@router.post("/queue/preview", response_model=PreviewResponse)
def preview(request: AddJobRequest) -> PreviewResponse:
    index = _require_index()
    source = request.source_relpath.strip("/")
    if index.node(source) is None:
        raise HTTPException(status_code=404, detail=f"{source or '/'} is not in the current scan")
    if request.target_disk not in {d.name for d in index.disks}:
        raise HTTPException(status_code=400, detail=f"unknown disk {request.target_disk!r}")

    baseline = _plan().final
    candidate = JobModel(
        id="preview",
        source_relpath=source,
        target_disk=request.target_disk,
        conflicts=detect_conflicts(index, source, request.target_disk),
    )
    computation = compute_job(index, candidate, service.path_for)

    projections: dict[str, DiskProjection] = {}
    overflow = False
    reserve = service.planner.reserve_bytes
    for disk in index.disks:
        used_before = baseline[disk.name].used_after if disk.name in baseline else disk.used
        delta = computation.deltas.get(disk.name, 0)
        used_after = used_before + delta
        free_after = disk.total - used_after
        disk_overflow = free_after < 0 or (delta > 0 and free_after < reserve)
        overflow = overflow or disk_overflow
        projections[disk.name] = DiskProjection(
            name=disk.name,
            total=disk.total,
            used_before=used_before,
            free_before=disk.total - used_before,
            used_after=used_after,
            free_after=free_after,
            delta=delta,
            overflow=disk_overflow,
        )

    return PreviewResponse(
        source_relpath=source,
        target_disk=request.target_disk,
        conflicts=computation.conflicts,
        move_bytes=computation.move_bytes,
        reclaim_bytes=computation.reclaim_bytes,
        move_files=computation.move_files,
        delete_files=computation.delete_files,
        disks_after=projections,
        overflow=overflow,
        blocked_reason=computation.blocked_reason,
    )


@router.post("/queue", response_model=QueuePlan)
def add_job(request: AddJobRequest) -> QueuePlan:
    try:
        service.add_job(request.source_relpath, request.target_disk)
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    plan = _plan()
    service.persist_jobs()
    return plan


@router.delete("/queue/{job_id}", response_model=QueuePlan)
def remove_job(job_id: str) -> QueuePlan:
    if not service.planner.remove(job_id):
        raise HTTPException(status_code=404, detail="unknown job")
    plan = _plan()
    service.persist_jobs()
    return plan


@router.delete("/queue", response_model=QueuePlan)
def clear_queue() -> QueuePlan:
    service.planner.clear()
    plan = _plan()
    service.persist_jobs()
    return plan


@router.post("/queue/{job_id}/retry", response_model=QueuePlan)
def retry_job(job_id: str) -> QueuePlan:
    if service.planner.get(job_id) is None:
        raise HTTPException(status_code=404, detail="unknown job")
    if service.planner.retry(job_id) is None:
        raise HTTPException(status_code=409, detail="only a failed job can be retried")
    plan = _plan()
    service.persist_jobs()
    return plan


@router.post("/queue/reorder", response_model=QueuePlan)
def reorder(request: ReorderRequest) -> QueuePlan:
    service.planner.reorder(request.order)
    plan = _plan()
    service.persist_jobs()
    return plan


@router.post("/queue/{job_id}/resolve", response_model=QueuePlan)
def resolve(job_id: str, request: ResolveRequest) -> QueuePlan:
    if request.mode is ConflictMode.KEEP_DISK and not request.keep_disk:
        raise HTTPException(status_code=400, detail="keep_disk is required for keep_disk mode")
    if service.planner.resolve(job_id, request.relpath, request.mode, request.keep_disk) is None:
        raise HTTPException(status_code=404, detail="unknown job")
    plan = _plan()
    service.persist_jobs()
    return plan


@router.post("/queue/{job_id}/resolve-all", response_model=QueuePlan)
def resolve_all(job_id: str, request: ResolveAllRequest) -> QueuePlan:
    if request.mode is ConflictMode.KEEP_DISK and not request.keep_disk:
        raise HTTPException(status_code=400, detail="keep_disk is required for keep_disk mode")
    job = service.planner.resolve_all(
        job_id, request.mode, request.keep_disk, request.only_unresolved
    )
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    plan = _plan()
    service.persist_jobs()
    return plan


@router.get("/queue/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    job = service.planner.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    _plan()
    return job


@router.get("/queue/{job_id}/operations", response_model=list[Operation])
def job_operations(job_id: str) -> list[Operation]:
    index = _require_index()
    job = service.planner.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return compute_job(index, job, service.path_for).operations
