"""Consolidation planning and the queue simulator.

A job says "put everything under <source_relpath> onto <target_disk>". The
planner turns that into a concrete operation list and, crucially, a per-disk
byte delta. Replaying those deltas over the queue in order is what lets the UI
show running free-space totals so you can plan several moves before committing.
"""

from __future__ import annotations

import posixpath
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from .models import (
    Conflict,
    ConflictMode,
    Disk,
    DiskProjection,
    Job,
    JobPlan,
    JobStatus,
    Operation,
    OpKind,
    QueuePlan,
)
from .scan import ScanIndex, classify

#: (disk_name, relpath_below_scan_root) -> absolute path
PathBuilder = Callable[[str, str], str]


@dataclass
class JobComputation:
    operations: list[Operation] = field(default_factory=list)
    deltas: dict[str, int] = field(default_factory=dict)
    move_bytes: int = 0
    reclaim_bytes: int = 0
    move_files: int = 0
    delete_files: int = 0
    conflicts: list[Conflict] = field(default_factory=list)
    blocked_reason: str | None = None

    @property
    def unresolved(self) -> list[Conflict]:
        return [c for c in self.conflicts if not c.resolved]


def _suffixed(relpath: str, disk: str) -> str:
    stem, ext = posixpath.splitext(relpath)
    return f"{stem} ({disk}){ext}"


def detect_conflicts(index: ScanIndex, source_relpath: str, target_disk: str) -> list[Conflict]:
    """Files under the source that exist on more than one disk.

    Every one of these needs an explicit decision before the job can run: only
    the user knows whether a 3.8 GB and an 888 MB copy of the same episode are
    a redundant duplicate or two encodes worth keeping.
    """
    conflicts: list[Conflict] = []
    for relpath, copies in index.files_under(source_relpath):
        if len(copies) < 2:
            continue
        sizes = {disk: entry.size for disk, entry in copies.items()}
        conflicts.append(
            Conflict(
                relpath=relpath,
                copies=dict(sorted(sizes.items())),
                kind=classify(list(sizes.values())),
            )
        )
    conflicts.sort(key=lambda c: c.relpath)
    return conflicts


def compute_job(
    index: ScanIndex,
    job: Job,
    path_for: PathBuilder,
) -> JobComputation:
    """Re-derive a job's operations and byte deltas from the current index.

    Resolutions already recorded on the job are preserved by relative path, so
    a rescan does not discard the decisions you have made.
    """
    result = JobComputation()

    node = index.node(job.source_relpath)
    if node is None:
        result.blocked_reason = f"{job.source_relpath or '/'} is not in the current scan"
        return result
    if job.target_disk not in {d.name for d in index.disks}:
        result.blocked_reason = f"target disk {job.target_disk} is not present"
        return result

    previous = {c.relpath: c for c in job.conflicts}
    fresh = detect_conflicts(index, job.source_relpath, job.target_disk)
    for conflict in fresh:
        prior = previous.get(conflict.relpath)
        if prior and prior.copies == conflict.copies:
            conflict.mode = prior.mode
            conflict.keep_disk = prior.keep_disk
    result.conflicts = fresh
    conflict_paths = {c.relpath for c in fresh}

    def add(disk: str, amount: int) -> None:
        result.deltas[disk] = result.deltas.get(disk, 0) + amount

    def emit_move(disk_from: str, relpath: str, size: int, dst_relpath: str | None = None) -> None:
        renamed = dst_relpath is not None and dst_relpath != relpath
        result.operations.append(
            Operation(
                kind=OpKind.MOVE,
                src=path_for(disk_from, relpath),
                dst=path_for(job.target_disk, dst_relpath or relpath),
                size=size,
                relpath=relpath,
                renamed=renamed,
            )
        )
        add(job.target_disk, size)
        add(disk_from, -size)
        result.move_bytes += size
        result.move_files += 1

    def emit_delete(disk: str, relpath: str, size: int) -> None:
        result.operations.append(
            Operation(kind=OpKind.DELETE, src=path_for(disk, relpath), size=size, relpath=relpath)
        )
        add(disk, -size)
        result.reclaim_bytes += size
        result.delete_files += 1

    source_disks: set[str] = set()

    # Straightforward relocations: one copy, wrong disk.
    for relpath, copies in index.files_under(job.source_relpath):
        if relpath in conflict_paths:
            continue
        disk = next(iter(copies))
        if disk == job.target_disk:
            continue
        source_disks.add(disk)
        emit_move(disk, relpath, copies[disk].size)

    # Conflicts, in the order the user sees them.
    for conflict in sorted(fresh, key=lambda c: c.relpath):
        if not conflict.resolved or conflict.mode is ConflictMode.SKIP:
            continue
        sizes = conflict.copies
        if conflict.mode is ConflictMode.KEEP_BOTH:
            keeper = max(sizes, key=lambda d: (sizes[d], d))
            for disk in sorted(sizes):
                if disk == job.target_disk:
                    continue
                source_disks.add(disk)
                dst_rel = conflict.relpath if disk == keeper else _suffixed(conflict.relpath, disk)
                # The canonical name is already taken if the target holds a copy.
                if disk == keeper and job.target_disk in sizes:
                    dst_rel = _suffixed(conflict.relpath, disk)
                emit_move(disk, conflict.relpath, sizes[disk], dst_rel)
            continue

        winner = conflict.winner()
        if winner is None or winner not in sizes:
            continue
        for disk in sorted(sizes):
            if disk == winner:
                continue
            source_disks.add(disk)
            emit_delete(disk, conflict.relpath, sizes[disk])
        if winner != job.target_disk:
            source_disks.add(winner)
            emit_move(winner, conflict.relpath, sizes[winner])

    # Deletes must land before the move that reuses the same destination name.
    result.operations.sort(key=lambda op: (op.relpath, 0 if op.kind is OpKind.DELETE else 1))

    for disk in sorted(source_disks):
        if disk == job.target_disk:
            continue
        result.operations.append(
            Operation(
                kind=OpKind.PRUNE,
                src=path_for(disk, job.source_relpath),
                relpath=job.source_relpath,
            )
        )

    if not result.move_files and not result.delete_files:
        if result.unresolved:
            result.blocked_reason = f"{len(result.unresolved)} conflict(s) still need a decision"
        else:
            result.blocked_reason = "nothing to move: already consolidated on this disk"
    elif result.unresolved:
        result.blocked_reason = f"{len(result.unresolved)} conflict(s) still need a decision"

    return result


class Planner:
    """The ordered queue of consolidation jobs."""

    def __init__(self, reserve_bytes: int = 0) -> None:
        self.jobs: list[Job] = []
        self.reserve_bytes = reserve_bytes

    # --- queue mutation ---------------------------------------------------

    def add(self, source_relpath: str, target_disk: str, conflicts: list[Conflict]) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            source_relpath=source_relpath,
            target_disk=target_disk,
            conflicts=conflicts,
            position=len(self.jobs),
        )
        self.jobs.append(job)
        self._renumber()
        return job

    def get(self, job_id: str) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)

    def remove(self, job_id: str) -> bool:
        before = len(self.jobs)
        self.jobs = [j for j in self.jobs if j.id != job_id]
        self._renumber()
        return len(self.jobs) != before

    def clear(self) -> None:
        self.jobs = [j for j in self.jobs if j.status is JobStatus.RUNNING]
        self._renumber()

    def drop_finished(self) -> int:
        """Remove jobs that have nothing left to do.

        A finished job is history, not work. Failed jobs stay so they can be
        retried; cancelled and completed ones do not.
        """
        before = len(self.jobs)
        self.jobs = [
            j for j in self.jobs if j.status not in (JobStatus.DONE, JobStatus.CANCELLED)
        ]
        self._renumber()
        return before - len(self.jobs)

    def retry(self, job_id: str) -> Job | None:
        """Return a failed job to the queue.

        The conflict decisions are kept, and the operations are recomputed
        against the current index on the next plan, so files that did make it
        across before the failure are simply no longer part of the work.
        """
        job = self.get(job_id)
        if job is None or job.status is not JobStatus.FAILED:
            return None
        job.status = JobStatus.DRAFT
        job.error = None
        job.bytes_done = 0
        return job

    def reorder(self, order: list[str]) -> None:
        rank = {job_id: i for i, job_id in enumerate(order)}
        self.jobs.sort(key=lambda j: rank.get(j.id, len(rank)))
        self._renumber()

    def _renumber(self) -> None:
        for i, job in enumerate(self.jobs):
            job.position = i

    def resolve(
        self,
        job_id: str,
        relpath: str,
        mode: ConflictMode | None,
        keep_disk: str | None = None,
    ) -> Job | None:
        job = self.get(job_id)
        if job is None:
            return None
        for conflict in job.conflicts:
            if conflict.relpath == relpath:
                conflict.mode = mode
                conflict.keep_disk = keep_disk if mode is ConflictMode.KEEP_DISK else None
                break
        return job

    def resolve_all(
        self,
        job_id: str,
        mode: ConflictMode | None,
        keep_disk: str | None = None,
        only_unresolved: bool = False,
    ) -> Job | None:
        job = self.get(job_id)
        if job is None:
            return None
        for conflict in job.conflicts:
            if only_unresolved and conflict.resolved:
                continue
            if mode is ConflictMode.KEEP_DISK and keep_disk not in conflict.copies:
                # This file has no copy on the chosen disk, so the bulk action
                # cannot apply; leave it for an individual decision.
                continue
            conflict.mode = mode
            conflict.keep_disk = keep_disk if mode is ConflictMode.KEEP_DISK else None
        return job

    # --- simulation -------------------------------------------------------

    def plan(self, index: ScanIndex | None, path_for: PathBuilder) -> QueuePlan:
        disks_now = list(index.disks) if index else []
        running_used = {d.name: d.used for d in disks_now}

        job_plans: list[JobPlan] = []
        blocked: list[str] = []
        total_move = total_reclaim = 0

        seen_paths: list[str] = []

        for job in self.jobs:
            if index is None:
                job_plans.append(
                    JobPlan(job=job, disks_after={}, blocked_reason="no active scan")
                )
                continue

            computation = compute_job(index, job, path_for)
            job.conflicts = computation.conflicts
            job.move_bytes = computation.move_bytes
            job.reclaim_bytes = computation.reclaim_bytes
            job.move_files = computation.move_files
            job.delete_files = computation.delete_files

            reason = computation.blocked_reason
            overlap = self._overlap(job.source_relpath, seen_paths)
            if overlap is not None:
                reason = reason or f"overlaps an earlier job on {overlap or '/'}"
            seen_paths.append(job.source_relpath)

            if job.status not in (JobStatus.DONE, JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED):
                job.status = JobStatus.DRAFT if reason else JobStatus.READY
                # Re-planning clears a stale failure note, but the reason a
                # failed job failed is the most useful thing on the screen.
                job.error = None

            if job.status is not JobStatus.DONE:
                for disk_name, delta in computation.deltas.items():
                    if disk_name in running_used:
                        running_used[disk_name] += delta
                total_move += computation.move_bytes
                total_reclaim += computation.reclaim_bytes

            projections: dict[str, DiskProjection] = {}
            job_overflow = False
            for disk in disks_now:
                delta = computation.deltas.get(disk.name, 0)
                used_after = running_used[disk.name]
                free_after = disk.total - used_after
                overflow = free_after < 0 or (delta > 0 and free_after < self.reserve_bytes)
                job_overflow = job_overflow or overflow
                projections[disk.name] = DiskProjection(
                    name=disk.name,
                    total=disk.total,
                    used_before=disk.used,
                    free_before=disk.free,
                    used_after=used_after,
                    free_after=free_after,
                    delta=delta,
                    overflow=overflow,
                )

            if job_overflow:
                reason = reason or f"{job.target_disk} would drop below the reserve"

            if reason and job.status not in (JobStatus.DONE, JobStatus.CANCELLED):
                blocked.append(f"{job.source_relpath or '/'} -> {job.target_disk}: {reason}")

            job_plans.append(
                JobPlan(
                    job=job,
                    disks_after=projections,
                    overflow=job_overflow,
                    blocked_reason=reason,
                )
            )

        final: dict[str, DiskProjection] = {}
        for disk in disks_now:
            used_after = running_used.get(disk.name, disk.used)
            free_after = disk.total - used_after
            final[disk.name] = DiskProjection(
                name=disk.name,
                total=disk.total,
                used_before=disk.used,
                free_before=disk.free,
                used_after=used_after,
                free_after=free_after,
                delta=used_after - disk.used,
                overflow=free_after < 0
                or (used_after > disk.used and free_after < self.reserve_bytes),
            )

        runnable = [
            jp for jp in job_plans if jp.job.status in (JobStatus.READY, JobStatus.RUNNING)
        ]
        return QueuePlan(
            disks_now=disks_now,
            jobs=job_plans,
            final=final,
            total_move_bytes=total_move,
            total_reclaim_bytes=total_reclaim,
            ready=bool(runnable) and not blocked,
            blocked_reasons=blocked,
        )

    @staticmethod
    def _overlap(relpath: str, seen: list[str]) -> str | None:
        for other in seen:
            if relpath == other:
                return other
            if not other or relpath.startswith(other + "/"):
                return other
            if not relpath or other.startswith(relpath + "/"):
                return other
        return None

    def operations_for(
        self, index: ScanIndex, job: Job, path_for: PathBuilder
    ) -> list[Operation]:
        return compute_job(index, job, path_for).operations
