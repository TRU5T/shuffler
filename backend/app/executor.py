"""Queue execution.

Ordering is the safety contract: for every relocated file we copy to the target
disk, verify the destination size, and only then delete the source. Empty
directories are pruned after a job's files are done. With `dry_run` enabled
(the default) every operation is announced but nothing is touched.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator

from .backends.base import StorageBackend, StorageError
from .models import ExecutionState, Job, JobStatus, Operation, OpKind, ProgressEvent
from .planner import PathBuilder, Planner, compute_job
from .scan import ScanIndex

HISTORY_LIMIT = 2000

#: Progress events are emitted no more often than this. A copy reports every
#: half second per file, and several files in flight would otherwise flood the
#: event stream with more updates than a UI can use.
EMIT_INTERVAL = 0.4

#: Transfer rate is averaged over this trailing window. Long enough to smooth
#: out the lumpiness of disk writes, short enough to react to a real slowdown.
RATE_WINDOW = 6.0


class RateMeter:
    """Transfer rate over a trailing window.

    A running average over the whole queue would be dragged down by a slow
    start and would barely move once a long run is underway, so samples older
    than the window are discarded.
    """

    def __init__(self, window: float = RATE_WINDOW) -> None:
        self.window = window
        self._samples: deque[tuple[float, int]] = deque()

    def observe(self, total_bytes: int, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._samples.append((now, total_bytes))
        cutoff = now - self.window
        while len(self._samples) > 2 and self._samples[0][0] < cutoff:
            self._samples.popleft()

    @property
    def rate(self) -> float:
        if len(self._samples) < 2:
            return 0.0
        (first_ts, first_bytes), (last_ts, last_bytes) = self._samples[0], self._samples[-1]
        elapsed = last_ts - first_ts
        if elapsed <= 0:
            return 0.0
        return max(0.0, (last_bytes - first_bytes) / elapsed)

    def eta(self, remaining: int) -> float | None:
        rate = self.rate
        if rate <= 0 or remaining <= 0:
            return None
        return remaining / rate

    def reset(self) -> None:
        self._samples.clear()


class Executor:
    def __init__(self) -> None:
        self.state = ExecutionState()
        self.history: deque[ProgressEvent] = deque(maxlen=HISTORY_LIMIT)
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._sub_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._rate = RateMeter()
        self._last_emit = 0.0

    # --- event fan-out ----------------------------------------------------

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._sub_lock:
            self._subscribers.append((loop, queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._sub_lock:
            self._subscribers = [(l, q) for l, q in self._subscribers if q is not queue]

    def _emit(self, event: ProgressEvent) -> None:
        event.dry_run = self.state.dry_run
        event.ops_done = self.state.ops_done
        event.ops_total = self.state.ops_total
        event.bytes_per_second = self.state.bytes_per_second
        event.eta_seconds = self.state.eta_seconds
        # Progress events are the bulk of a long run and are worthless once
        # superseded, so they stream to subscribers without filling the replay
        # buffer that a late-joining client receives.
        if event.type != "op_progress":
            self.history.append(event)
        with self._sub_lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except (RuntimeError, asyncio.QueueFull):
                continue

    # --- control ----------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        index: ScanIndex,
        planner: Planner,
        backend: StorageBackend,
        path_for: PathBuilder,
        dry_run: bool,
        prune_stop_for: PathBuilder,
        persist: Callable[[], None] | None = None,
    ) -> tuple[bool, str]:
        if self.running:
            return False, "the queue is already running"

        # Re-plan before selecting work: statuses and blocked reasons must
        # reflect the index and resolutions as they stand right now.
        plans = {jp.job.id: jp for jp in planner.plan(index, path_for).jobs}
        jobs = [j for j in planner.jobs if j.status is JobStatus.READY]
        if not jobs:
            return False, "no jobs are ready to run"

        blocked = [
            f"{j.source_relpath or '/'}: {plans[j.id].blocked_reason}"
            for j in jobs
            if j.id in plans and plans[j.id].blocked_reason
        ]
        if blocked:
            return False, "; ".join(blocked)

        self._stop.clear()
        self._pause.clear()
        self.history.clear()
        self._rate.reset()
        self._last_emit = 0.0
        self.state = ExecutionState(
            running=True,
            dry_run=dry_run,
            started_at=time.time(),
            ops_total=0,
            bytes_total=0,
        )
        self._thread = threading.Thread(
            target=self._run,
            args=(index, planner, backend, path_for, prune_stop_for, jobs, persist),
            name="shuffler-executor",
            daemon=True,
        )
        self._thread.start()
        return True, f"started {len(jobs)} job(s)"

    def pause(self) -> None:
        if self.running:
            self._pause.set()
            self.state.paused = True
            self._emit(ProgressEvent(type="log", message="paused"))

    def resume(self) -> None:
        if self.running:
            self._pause.clear()
            self.state.paused = False
            self._emit(ProgressEvent(type="log", message="resumed"))

    def stop(self) -> None:
        if self.running:
            self._stop.set()
            self._pause.clear()
            self._emit(ProgressEvent(type="log", message="stopping after the current operation"))

    def _wait_if_paused(self) -> None:
        while self._pause.is_set() and not self._stop.is_set():
            time.sleep(0.2)

    # --- worker -----------------------------------------------------------

    def _run(
        self,
        index: ScanIndex,
        planner: Planner,
        backend: StorageBackend,
        path_for: PathBuilder,
        prune_stop_for: PathBuilder,
        jobs: list[Job],
        persist: Callable[[], None] | None = None,
    ) -> None:
        try:
            batches: list[tuple[Job, list[Operation]]] = []
            for job in jobs:
                batches.append((job, compute_job(index, job, path_for).operations))

            self.state.ops_total = sum(len(ops) for _, ops in batches)
            self.state.bytes_total = sum(
                op.size for _, ops in batches for op in ops if op.kind is not OpKind.PRUNE
            )
            self._emit(
                ProgressEvent(
                    type="queue_start",
                    message=(
                        f"{len(batches)} job(s), {self.state.ops_total} operation(s)"
                        + (" - DRY RUN, nothing will be written" if self.state.dry_run else "")
                    ),
                    bytes_total=self.state.bytes_total,
                )
            )

            for job, operations in batches:
                if self._stop.is_set():
                    job.status = JobStatus.CANCELLED
                    continue
                self.state.current_job = job.id
                job.status = JobStatus.RUNNING
                job.bytes_done = 0
                self._emit(
                    ProgressEvent(
                        type="job_start",
                        job_id=job.id,
                        relpath=job.source_relpath,
                        message=f"consolidating {job.source_relpath or '/'} onto {job.target_disk}",
                    )
                )
                try:
                    self._run_job(backend, job, operations, prune_stop_for)
                except StorageError as exc:
                    job.status = JobStatus.FAILED
                    job.error = str(exc)
                    self.state.last_error = str(exc)
                    self._emit(ProgressEvent(type="error", job_id=job.id, message=str(exc)))
                    self._emit(
                        ProgressEvent(
                            type="log",
                            message="halting the queue so nothing further is touched",
                        )
                    )
                    if persist is not None:
                        persist()
                    break

                if self._stop.is_set() and job.status is JobStatus.RUNNING:
                    job.status = JobStatus.CANCELLED
                    self._emit(ProgressEvent(type="log", job_id=job.id, message="cancelled"))
                    if persist is not None:
                        persist()
                    break

                job.status = JobStatus.DONE
                self._emit(
                    ProgressEvent(
                        type="job_done",
                        job_id=job.id,
                        relpath=job.source_relpath,
                        message=f"{job.move_files} moved, {job.delete_files} deleted",
                    )
                )
                if self.state.dry_run:
                    # A dry run did not actually finish the work, so the job
                    # stays ready to run for real. Marking it done would leave
                    # a finished-looking row that still needs to be executed.
                    job.status = JobStatus.READY
                else:
                    planner.remove(job.id)
                if persist is not None:
                    persist()
        except Exception as exc:  # pragma: no cover - defensive
            self.state.last_error = str(exc)
            self._emit(ProgressEvent(type="error", message=f"executor crashed: {exc}"))
        finally:
            self.state.running = False
            self.state.paused = False
            self.state.current_job = None
            self.state.current_file = None
            self.state.file_bytes_done = 0
            self.state.file_bytes_total = 0
            self.state.bytes_per_second = 0.0
            self.state.eta_seconds = None
            self.state.finished_at = time.time()
            self._emit(
                ProgressEvent(
                    type="queue_done",
                    message="dry run complete" if self.state.dry_run else "queue complete",
                    bytes_done=self.state.bytes_done,
                    bytes_total=self.state.bytes_total,
                )
            )

    def _run_job(
        self,
        backend: StorageBackend,
        job: Job,
        operations: list[Operation],
        prune_stop_for: PathBuilder,
    ) -> None:
        dry = self.state.dry_run
        for op in operations:
            if self._stop.is_set():
                return
            self._wait_if_paused()
            if self._stop.is_set():
                return

            self._emit(
                ProgressEvent(
                    type="op_start",
                    job_id=job.id,
                    relpath=op.relpath,
                    message=self._describe(op, dry),
                    bytes_done=self.state.bytes_done,
                    bytes_total=self.state.bytes_total,
                )
            )

            if op.kind is OpKind.MOVE:
                self._do_move(backend, job, op, dry)
            elif op.kind is OpKind.DELETE:
                if not dry:
                    backend.delete_file(op.src)
                self.state.bytes_done += op.size
                job.bytes_done += op.size
                self._observe_rate()
            elif op.kind is OpKind.PRUNE:
                if not dry:
                    backend.prune_empty_dirs(op.src)
                    # Never climb above the scanned root, so a share folder that
                    # happens to be empty on this disk is left alone.
                    stop_at = prune_stop_for(self._disk_of(op.src, backend), "")
                    backend.prune_empty_parents(op.src, stop_at)

            self.state.ops_done += 1
            self._emit(
                ProgressEvent(
                    type="op_done",
                    job_id=job.id,
                    relpath=op.relpath,
                    bytes_done=self.state.bytes_done,
                    bytes_total=self.state.bytes_total,
                    file_bytes_done=self.state.file_bytes_done,
                    file_bytes_total=self.state.file_bytes_total,
                )
            )
            self.state.current_file = None
            self.state.file_bytes_done = 0
            self.state.file_bytes_total = 0

    def _do_move(self, backend: StorageBackend, job: Job, op: Operation, dry: bool) -> None:
        assert op.dst is not None
        if dry:
            self.state.bytes_done += op.size
            job.bytes_done += op.size
            return

        actual = backend.size_of(op.src)
        if actual is None:
            self._emit(
                ProgressEvent(
                    type="log",
                    job_id=job.id,
                    relpath=op.relpath,
                    message=f"skipped, source has gone: {op.src}",
                )
            )
            self.state.bytes_done += op.size
            return

        base = self.state.bytes_done
        last = [0]
        self.state.current_file = op.relpath
        self.state.file_bytes_total = actual

        def on_progress(done: int, total: int) -> None:
            delta = done - last[0]
            last[0] = done
            self.state.bytes_done = base + done
            self.state.file_bytes_done = done
            job.bytes_done += delta
            self._observe_rate()

            now = time.time()
            if now - self._last_emit < EMIT_INTERVAL:
                return
            self._last_emit = now
            self._emit(
                ProgressEvent(
                    type="op_progress",
                    job_id=job.id,
                    relpath=op.relpath,
                    bytes_done=self.state.bytes_done,
                    bytes_total=self.state.bytes_total,
                    file_bytes_done=done,
                    file_bytes_total=total or actual,
                )
            )

        written = backend.copy_file(op.src, op.dst, actual, on_progress)
        if written != actual:
            raise StorageError(
                f"verification failed for {op.relpath}: wrote {written} bytes, expected {actual}. "
                "The source has been left in place."
            )
        backend.delete_file(op.src)
        self.state.bytes_done = base + actual
        self.state.file_bytes_done = actual
        self._observe_rate()

    def _observe_rate(self) -> None:
        self._rate.observe(self.state.bytes_done)
        self.state.bytes_per_second = self._rate.rate
        self.state.eta_seconds = self._rate.eta(
            max(0, self.state.bytes_total - self.state.bytes_done)
        )

    @staticmethod
    def _disk_of(path: str, backend: StorageBackend) -> str:
        root = backend.mount_root.rstrip("/")
        remainder = path[len(root) + 1 :] if path.startswith(root + "/") else path
        return remainder.split("/", 1)[0]

    @staticmethod
    def _describe(op: Operation, dry: bool) -> str:
        prefix = "[dry run] " if dry else ""
        if op.kind is OpKind.MOVE:
            suffix = " (renamed)" if op.renamed else ""
            return f"{prefix}move {op.relpath}{suffix}"
        if op.kind is OpKind.DELETE:
            return f"{prefix}delete redundant copy {op.src}"
        return f"{prefix}prune empty folders under {op.src}"

    def replay(self) -> Iterator[ProgressEvent]:
        yield from list(self.history)
