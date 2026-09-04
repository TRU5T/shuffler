"""Application state: connection, active scan, queue and executor.

A single instance is shared by the HTTP layer. It owns persistence so a restart
resumes with the last scan and the queue you were building.
"""

from __future__ import annotations

import json
import threading

from . import db
from .backends import build_backend
from .backends.base import StorageBackend, StorageError
from .config import settings
from .executor import Executor
from .models import (
    Conflict,
    ConnectionSettings,
    Disk,
    FileEntry,
    HealthResult,
    Job,
    JobStatus,
    ScanSummary,
)
from .planner import Planner, detect_conflicts
from .scan import ScanIndex, normalise_root, run_scan

SETTINGS_KEY = "connection"
ACTIVE_SCAN_KEY = "active_scan"


class ScanBusy(RuntimeError):
    pass


class Service:
    def __init__(self) -> None:
        db.init_db()
        self.connection = self._load_connection()
        self.planner = Planner(reserve_bytes=self.connection.reserve_bytes)
        self.executor = Executor()
        self.index: ScanIndex | None = None
        self._backend: StorageBackend | None = None
        self._backend_key: str = ""
        self._scan_lock = threading.Lock()
        self._scanning = False
        self._restore()

    # --- connection -------------------------------------------------------

    def _load_connection(self) -> ConnectionSettings:
        stored = db.get_setting(SETTINGS_KEY) or {}
        merged = ConnectionSettings(
            storage_backend=stored.get("storage_backend", settings.storage_backend),
            ssh_host=stored.get("ssh_host", settings.ssh_host),
            ssh_port=stored.get("ssh_port", settings.ssh_port),
            ssh_user=stored.get("ssh_user", settings.ssh_user),
            ssh_key_path=stored.get("ssh_key_path", settings.ssh_key_path),
            ssh_password=stored.get("ssh_password") or settings.ssh_password or None,
            mount_root=stored.get("mount_root", settings.mount_root),
            dry_run=stored.get("dry_run", settings.dry_run),
            reserve_bytes=stored.get("reserve_bytes", settings.reserve_bytes),
        )
        merged.has_password = bool(merged.ssh_password)
        return merged

    def _persist_connection(self) -> None:
        db.set_setting(SETTINGS_KEY, self.connection.model_dump(exclude={"has_password"}))

    def public_connection(self) -> ConnectionSettings:
        """The connection settings with the password stripped."""
        payload = self.connection.model_copy()
        payload.has_password = bool(self.connection.ssh_password)
        payload.ssh_password = None
        return payload

    def update_connection(self, patch: dict) -> ConnectionSettings:
        for field, value in patch.items():
            if field in {"has_password"} or value is None:
                continue
            if hasattr(self.connection, field):
                setattr(self.connection, field, value)
        if patch.get("ssh_password") == "":
            self.connection.ssh_password = None
        self.connection.has_password = bool(self.connection.ssh_password)
        # Local fixture start leaves mount_root pointing at the synthetic tree.
        # That path does not exist on Unraid, so SSH would log in and then fail
        # listing disks. Drop it when talking to a real box.
        if (
            self.connection.storage_backend == "ssh"
            and "shuffler-fixtures" in (self.connection.mount_root or "")
        ):
            self.connection.mount_root = "/mnt"
        self.planner.reserve_bytes = self.connection.reserve_bytes
        self._persist_connection()
        self._backend = None
        return self.public_connection()

    # --- backend ----------------------------------------------------------

    def _key(self) -> str:
        c = self.connection
        return json.dumps(
            [
                c.storage_backend,
                c.ssh_host,
                c.ssh_port,
                c.ssh_user,
                c.ssh_key_path,
                bool(c.ssh_password),
                c.mount_root,
            ]
        )

    def backend(self) -> StorageBackend:
        key = self._key()
        if self._backend is None or key != self._backend_key:
            if self._backend is not None:
                self._backend.close()
            c = self.connection
            self._backend = build_backend(
                c.storage_backend,
                host=c.ssh_host,
                port=c.ssh_port,
                user=c.ssh_user,
                password=c.ssh_password or "",
                key_path=c.ssh_key_path,
                timeout=settings.ssh_timeout,
                mount_root=c.mount_root,
                disk_pattern=settings.disk_pattern,
                simulated_capacity=settings.simulated_disk_capacity,
            )
            self._backend_key = key
        return self._backend

    def health(self) -> HealthResult:
        try:
            backend = self.backend()
            message = backend.check()
            return HealthResult(
                ok=True,
                backend=self.connection.storage_backend,
                dry_run=self.connection.dry_run,
                message=message,
                disks=backend.list_disks(),
            )
        except StorageError as exc:
            return HealthResult(
                ok=False,
                backend=self.connection.storage_backend,
                dry_run=self.connection.dry_run,
                message=str(exc),
            )
        except Exception as exc:  # pragma: no cover - unexpected transport errors
            return HealthResult(
                ok=False,
                backend=self.connection.storage_backend,
                dry_run=self.connection.dry_run,
                message=f"{type(exc).__name__}: {exc}",
            )

    def disks(self) -> list[Disk]:
        return self.backend().list_disks()

    # --- paths ------------------------------------------------------------

    def path_for(self, disk: str, relpath: str) -> str:
        root = self.index.root if self.index else ""
        parts = [p for p in (root, relpath) if p]
        return self.backend().full_path(disk, "/".join(parts))

    def prune_stop_for(self, disk: str, _relpath: str = "") -> str:
        root = self.index.root if self.index else ""
        return self.backend().full_path(disk, root)

    # --- scanning ---------------------------------------------------------

    @property
    def scanning(self) -> bool:
        return self._scanning

    def scan(self, root: str) -> ScanSummary:
        if not self._scan_lock.acquire(blocking=False):
            raise ScanBusy("a scan is already running")
        self._scanning = True
        try:
            backend = self.backend()
            index = run_scan(backend, root)
            self.index = index
            summary = index.summary()
            db.save_scan(
                scan_id=index.id,
                root=index.root,
                created_at=index.created_at,
                disks=[d.model_dump() for d in index.disks],
                totals={
                    "total_files": summary.total_files,
                    "distinct_files": summary.distinct_files,
                    "total_bytes": summary.total_bytes,
                    "dup_files": summary.dup_files,
                    "dup_wasted_bytes": summary.dup_wasted_bytes,
                },
                duration_seconds=index.duration_seconds,
                rows=index.db_rows(),
            )
            db.set_setting(ACTIVE_SCAN_KEY, index.id)
            self._prune_scan_history()
            return summary
        finally:
            self._scanning = False
            self._scan_lock.release()

    def _prune_scan_history(self, keep: int = 5) -> None:
        for row in db.list_scans()[keep:]:
            db.delete_scan(row["id"])

    def load_scan(self, scan_id: str) -> ScanSummary | None:
        row = db.get_scan(scan_id)
        if row is None:
            return None
        files: dict[str, dict[str, FileEntry]] = {}
        for f in db.get_scan_files(scan_id):
            files.setdefault(f["relpath"], {})[f["disk"]] = FileEntry(
                size=f["size"], mtime=f["mtime"]
            )
        self.index = ScanIndex(
            scan_id=row["id"],
            root=row["root"],
            disks=[Disk(**d) for d in json.loads(row["disks"])],
            files=files,
            created_at=row["created_at"],
            duration_seconds=row["duration_seconds"],
        )
        db.set_setting(ACTIVE_SCAN_KEY, scan_id)
        return self.index.summary()

    def refresh_disk_usage(self) -> list[Disk]:
        """Re-read free space and fold it into the active scan.

        The index caches the capacity figures from scan time. Folding fresh
        numbers back in keeps the queue projections consistent with what the
        disk strip displays, which matters after a run has moved data.
        """
        disks = self.disks()
        if self.index is not None:
            live = {d.name for d in disks}
            # Keep disks the scan knew about even if they have since gone away,
            # so their contribution does not silently vanish from the plan.
            stale = [d for d in self.index.disks if d.name not in live]
            self.index.disks = self.backend().sort_disks(list(disks) + stale)
        return disks

    # --- queue ------------------------------------------------------------

    def add_job(self, source_relpath: str, target_disk: str) -> Job:
        if self.index is None:
            raise StorageError("run a scan before queueing work")
        source = normalise_root(source_relpath) if source_relpath.startswith("/") else source_relpath
        if self.index.node(source) is None:
            raise StorageError(f"{source or '/'} is not part of the current scan")
        if target_disk not in {d.name for d in self.index.disks}:
            raise StorageError(f"unknown target disk {target_disk!r}")
        conflicts: list[Conflict] = detect_conflicts(self.index, source, target_disk)
        job = self.planner.add(source, target_disk, conflicts)
        self._persist_jobs()
        return job

    def _persist_jobs(self) -> None:
        db.clear_jobs()
        for job in self.planner.jobs:
            db.save_job(job.id, job.position, job.created_at, job.model_dump(mode="json"))

    def persist_jobs(self) -> None:
        self._persist_jobs()

    # --- startup restore --------------------------------------------------

    def _restore(self) -> None:
        active = db.get_setting(ACTIVE_SCAN_KEY)
        if active:
            try:
                self.load_scan(active)
            except Exception:
                self.index = None
        for payload in db.load_jobs():
            try:
                job = Job(**payload)
            except Exception:
                continue
            if job.status is JobStatus.DONE or job.status is JobStatus.CANCELLED:
                # Finished work is not restored: it would just sit in the queue.
                continue
            if job.status is JobStatus.RUNNING:
                # Nothing survives a restart, so a job recorded as running was
                # interrupted. Marking it failed keeps the UI honest and stops
                # the planner treating a phantom job as already in progress.
                job.status = JobStatus.FAILED
                job.error = "interrupted by a restart, and was not completed"
            self.planner.jobs.append(job)
        self.planner._renumber()


service = Service()
