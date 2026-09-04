"""Pydantic models shared between the storage backends, planner and HTTP API."""

from __future__ import annotations

import time
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, computed_field


class Disk(BaseModel):
    name: str
    path: str
    total: int = 0
    used: int = 0
    free: int = 0

    @property
    def percent_used(self) -> float:
        return (self.used / self.total * 100) if self.total else 0.0


class FileEntry(BaseModel):
    size: int
    mtime: float = 0.0


class HealthResult(BaseModel):
    ok: bool
    backend: str
    dry_run: bool
    message: str = ""
    disks: list[Disk] = Field(default_factory=list)


class DiskUsage(BaseModel):
    bytes: int = 0
    files: int = 0


class TreeNode(BaseModel):
    name: str
    relpath: str
    is_dir: bool
    #: Bytes consumed across every disk, counting each copy of a duplicate.
    total_bytes: int = 0
    #: Files on disk, counting each copy of a duplicate, so it stays consistent
    #: with total_bytes and with the per-disk breakdown.
    total_files: int = 0
    #: Distinct relative paths, so a file on three disks counts once.
    distinct_files: int = 0
    per_disk: dict[str, DiskUsage] = Field(default_factory=dict)
    #: How many disks hold any part of this node. >1 means it is fragmented.
    disk_count: int = 0
    #: Files beneath this node that exist on more than one disk.
    dup_files: int = 0
    #: Bytes that would be reclaimed if every duplicate kept only its largest copy.
    dup_wasted_bytes: int = 0
    has_children: bool = False


DuplicateKind = Literal["identical", "variant"]


class DuplicateGroup(BaseModel):
    relpath: str
    kind: DuplicateKind
    #: disk name -> size in bytes
    copies: dict[str, int]
    wasted_bytes: int


class ScanSummary(BaseModel):
    id: str
    root: str
    created_at: float
    disks: list[Disk]
    total_files: int
    distinct_files: int = 0
    total_bytes: int
    dup_files: int
    dup_wasted_bytes: int
    duration_seconds: float = 0.0


class ConflictMode(str, Enum):
    """How to resolve a file that exists on more than one disk."""

    KEEP_LARGER = "keep_larger"
    KEEP_SMALLER = "keep_smaller"
    KEEP_DISK = "keep_disk"
    KEEP_BOTH = "keep_both"
    SKIP = "skip"


class Conflict(BaseModel):
    relpath: str
    #: disk name -> size in bytes
    copies: dict[str, int]
    kind: DuplicateKind
    mode: ConflictMode | None = None
    #: Required when mode is KEEP_DISK.
    keep_disk: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved(self) -> bool:
        if self.mode is None:
            return False
        if self.mode is ConflictMode.KEEP_DISK:
            return self.keep_disk in self.copies
        return True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def survivor(self) -> str | None:
        """Which disk keeps this file once resolved, for display purposes."""
        if not self.resolved or self.mode in (ConflictMode.SKIP, ConflictMode.KEEP_BOTH):
            return None
        return self.winner()

    def winner(self) -> str | None:
        """The disk whose copy survives, or None for modes that keep/skip all."""
        if self.mode is ConflictMode.KEEP_LARGER:
            return max(self.copies, key=lambda d: (self.copies[d], d))
        if self.mode is ConflictMode.KEEP_SMALLER:
            return min(self.copies, key=lambda d: (self.copies[d], d))
        if self.mode is ConflictMode.KEEP_DISK:
            return self.keep_disk
        return None


class JobStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(BaseModel):
    id: str
    source_relpath: str
    target_disk: str
    status: JobStatus = JobStatus.DRAFT
    conflicts: list[Conflict] = Field(default_factory=list)
    #: Bytes that will land on the target disk.
    move_bytes: int = 0
    #: Bytes reclaimed by deleting redundant copies (not counting moves).
    reclaim_bytes: int = 0
    move_files: int = 0
    delete_files: int = 0
    position: int = 0
    created_at: float = Field(default_factory=time.time)
    error: str | None = None
    bytes_done: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unresolved_conflicts(self) -> int:
        return sum(1 for c in self.conflicts if not c.resolved)


class DiskProjection(BaseModel):
    name: str
    total: int
    used_before: int
    free_before: int
    used_after: int
    free_after: int
    delta: int
    overflow: bool = False


class JobPlan(BaseModel):
    job: Job
    #: Cumulative per-disk state after this job runs, keyed by disk name.
    disks_after: dict[str, DiskProjection]
    overflow: bool = False
    blocked_reason: str | None = None


class QueuePlan(BaseModel):
    disks_now: list[Disk]
    jobs: list[JobPlan]
    final: dict[str, DiskProjection]
    total_move_bytes: int = 0
    total_reclaim_bytes: int = 0
    ready: bool = False
    blocked_reasons: list[str] = Field(default_factory=list)


class OpKind(str, Enum):
    MOVE = "move"
    DELETE = "delete"
    PRUNE = "prune"


class Operation(BaseModel):
    kind: OpKind
    src: str
    dst: str | None = None
    size: int = 0
    relpath: str = ""
    #: Set when a KEEP_BOTH resolution forced a rename at the destination.
    renamed: bool = False


class ProgressEvent(BaseModel):
    type: Literal[
        "queue_start",
        "job_start",
        "op_start",
        "op_progress",
        "op_done",
        "job_done",
        "queue_done",
        "log",
        "error",
    ]
    ts: float = Field(default_factory=time.time)
    job_id: str | None = None
    relpath: str | None = None
    message: str | None = None
    bytes_done: int = 0
    bytes_total: int = 0
    ops_done: int = 0
    ops_total: int = 0
    dry_run: bool = False
    #: Progress through the file currently being copied.
    file_bytes_done: int = 0
    file_bytes_total: int = 0
    #: Measured over a trailing window, so it reflects the current rate rather
    #: than an average dragged down by the start of the queue.
    bytes_per_second: float = 0.0
    eta_seconds: float | None = None


class ExecutionState(BaseModel):
    running: bool = False
    paused: bool = False
    dry_run: bool = True
    current_job: str | None = None
    ops_done: int = 0
    ops_total: int = 0
    bytes_done: int = 0
    bytes_total: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    last_error: str | None = None
    #: The file being copied right now, so a poll of this endpoint shows the
    #: same detail as the event stream.
    current_file: str | None = None
    file_bytes_done: int = 0
    file_bytes_total: int = 0
    bytes_per_second: float = 0.0
    eta_seconds: float | None = None


class ConnectionSettings(BaseModel):
    storage_backend: Literal["local", "ssh"] = "ssh"
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_key_path: str = ""
    #: Write-only from the API's perspective; never returned to the client.
    ssh_password: str | None = None
    mount_root: str = "/mnt"
    dry_run: bool = True
    reserve_bytes: int = 10 * 1024**3
    #: True when a password is stored, so the UI can show it is set.
    has_password: bool = False
