"""Scanning, tree browsing and duplicate reporting."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..backends.base import StorageError
from ..models import DuplicateGroup, ScanSummary, TreeNode
from ..scan import normalise_root
from ..service import ScanBusy, service
from .. import db

router = APIRouter()


class ScanRequest(BaseModel):
    root: str


class ScanState(BaseModel):
    scanning: bool
    scan: ScanSummary | None = None


class TreeResponse(BaseModel):
    node: TreeNode
    children: list[TreeNode]
    breadcrumbs: list[TreeNode]


class DuplicatesResponse(BaseModel):
    groups: list[DuplicateGroup]
    total_groups: int
    total_wasted_bytes: int


def _require_index():
    if service.index is None:
        raise HTTPException(status_code=409, detail="no active scan; run a scan first")
    return service.index


@router.post("/scan", response_model=ScanSummary)
def start_scan(request: ScanRequest) -> ScanSummary:
    try:
        return service.scan(request.root)
    except ScanBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/scan", response_model=ScanState)
def active_scan() -> ScanState:
    index = service.index
    return ScanState(scanning=service.scanning, scan=index.summary() if index else None)


@router.get("/scans", response_model=list[ScanSummary])
def scan_history() -> list[ScanSummary]:
    import json

    from ..models import Disk

    out: list[ScanSummary] = []
    for row in db.list_scans():
        out.append(
            ScanSummary(
                id=row["id"],
                root=row["root"],
                created_at=row["created_at"],
                disks=[Disk(**d) for d in json.loads(row["disks"])],
                total_files=row["total_files"],
                distinct_files=row["distinct_files"],
                total_bytes=row["total_bytes"],
                dup_files=row["dup_files"],
                dup_wasted_bytes=row["dup_wasted_bytes"],
                duration_seconds=row["duration_seconds"],
            )
        )
    return out


@router.post("/scans/{scan_id}/activate", response_model=ScanSummary)
def activate_scan(scan_id: str) -> ScanSummary:
    summary = service.load_scan(scan_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="unknown scan")
    return summary


@router.get("/tree", response_model=TreeResponse)
def tree(path: str = Query(default="")) -> TreeResponse:
    index = _require_index()
    relpath = path.strip("/")
    node = index.node(relpath)
    if node is None:
        raise HTTPException(status_code=404, detail=f"{path or '/'} is not in the current scan")

    breadcrumbs: list[TreeNode] = []
    if relpath:
        parts = relpath.split("/")
        for depth in range(len(parts)):
            crumb = index.node("/".join(parts[: depth + 1]))
            if crumb:
                breadcrumbs.append(crumb)
    return TreeResponse(node=node, children=index.children(relpath), breadcrumbs=breadcrumbs)


@router.get("/duplicates", response_model=DuplicatesResponse)
def duplicates(
    under: str = Query(default=""),
    kind: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> DuplicatesResponse:
    index = _require_index()
    if kind not in (None, "identical", "variant"):
        raise HTTPException(status_code=400, detail="kind must be 'identical' or 'variant'")
    groups, total, wasted = index.duplicates(
        under=under.strip("/"), kind=kind, limit=limit, offset=offset  # type: ignore[arg-type]
    )
    return DuplicatesResponse(groups=groups, total_groups=total, total_wasted_bytes=wasted)


@router.get("/fragmented", response_model=list[TreeNode])
def fragmented(
    under: str = Query(default=""),
    min_disks: int = Query(default=2, ge=2),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[TreeNode]:
    index = _require_index()
    return index.fragmented(under=under.strip("/"), min_disks=min_disks, limit=limit)


@router.get("/normalise-root")
def normalise(path: str = Query(...)) -> dict[str, str]:
    """Preview how a pasted path will be interpreted as a per-disk subpath."""
    return {"input": path, "root": normalise_root(path)}
