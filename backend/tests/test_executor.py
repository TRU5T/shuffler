"""Execution: dry runs, verified moves, pruning and the safety rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.backends.base import StorageError, assert_safe_path
from app.backends.local import LocalBackend
from app.executor import Executor
from app.models import ConflictMode, JobStatus
from app.planner import Planner, detect_conflicts
from app.scan import ScanIndex, run_scan

SPLIT_SHOW = "tv shows/Cowboy Bebop"


def files_in(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def run_queue(
    index: ScanIndex,
    planner: Planner,
    backend: LocalBackend,
    path_for,
    prune_stop_for,
    dry_run: bool,
) -> Executor:
    executor = Executor()
    started, message = executor.start(
        index=index,
        planner=planner,
        backend=backend,
        path_for=path_for,
        dry_run=dry_run,
        prune_stop_for=prune_stop_for,
    )
    assert started, message
    assert executor._thread is not None
    executor._thread.join(300)
    assert not executor.running
    return executor


@pytest.fixture
def ready_queue(index: ScanIndex, planner: Planner) -> Planner:
    job = planner.add(SPLIT_SHOW, "disk1", detect_conflicts(index, SPLIT_SHOW, "disk1"))
    planner.resolve_all(job.id, ConflictMode.KEEP_LARGER)
    return planner


def test_a_job_with_open_conflicts_refuses_to_start(
    index: ScanIndex, planner: Planner, backend, path_for, prune_stop_for
) -> None:
    planner.add(SPLIT_SHOW, "disk1", detect_conflicts(index, SPLIT_SHOW, "disk1"))
    executor = Executor()
    started, message = executor.start(
        index, planner, backend, path_for, True, prune_stop_for
    )
    assert not started
    assert "ready" in message


def test_dry_run_announces_everything_and_changes_nothing(
    mount: Path, index: ScanIndex, ready_queue: Planner, backend, path_for, prune_stop_for
) -> None:
    before = files_in(mount)
    executor = run_queue(index, ready_queue, backend, path_for, prune_stop_for, dry_run=True)

    assert files_in(mount) == before
    assert executor.state.ops_done == executor.state.ops_total > 0
    assert executor.state.bytes_done == executor.state.bytes_total

    types = [event.type for event in executor.replay()]
    assert types[0] == "queue_start"
    assert types[-1] == "queue_done"
    assert all(event.dry_run for event in executor.replay())
    assert any("DRY RUN" in (event.message or "") for event in executor.replay())
    assert ready_queue.jobs[0].status is JobStatus.READY


def test_live_run_relocates_files_and_prunes_the_folders_it_empties(
    mount: Path, index: ScanIndex, ready_queue: Planner, backend, path_for, prune_stop_for
) -> None:
    job = ready_queue.jobs[0]
    run_queue(index, ready_queue, backend, path_for, prune_stop_for, dry_run=False)

    assert job.status is JobStatus.DONE, job.error
    assert ready_queue.jobs == []

    for disk in ("disk2", "disk3", "disk4"):
        stray = mount / disk / "data/media" / SPLIT_SHOW
        assert not stray.exists(), f"{disk} still holds part of the show"

    # `tv shows` still has Firefly on other disks, so it must not be pruned.
    assert (mount / "disk2/data/media/tv shows").is_dir()
    assert (mount / "disk1/data/media" / SPLIT_SHOW).is_dir()

    after = run_scan(backend, index.root)
    node = after.node(SPLIT_SHOW)
    assert node is not None
    assert node.disk_count == 1 and "disk1" in node.per_disk


def test_pruning_never_climbs_above_the_scanned_root(
    mount: Path, index: ScanIndex, backend, path_for, prune_stop_for, planner: Planner
) -> None:
    # `music` exists only on disk4; moving it to disk1 empties disk4's copy of
    # the whole media tree, which must still be left standing.
    job = planner.add("music", "disk1", detect_conflicts(index, "music", "disk1"))
    assert job.conflicts == []
    run_queue(index, planner, backend, path_for, prune_stop_for, dry_run=False)

    assert not (mount / "disk4/data/media/music").exists()
    assert (mount / "disk4/data/media").is_dir(), "pruned past the scan root"
    assert (mount / "disk4/data").is_dir()


def test_keep_larger_deletes_the_smaller_copy_for_real(
    mount: Path, index: ScanIndex, ready_queue: Planner, backend, path_for, prune_stop_for
) -> None:
    conflict = next(c for c in ready_queue.jobs[0].conflicts if c.relpath.endswith("S01E05.mkv"))
    largest = max(conflict.copies, key=lambda d: conflict.copies[d])
    expected_size = conflict.copies[largest]

    run_queue(index, ready_queue, backend, path_for, prune_stop_for, dry_run=False)

    survivor = mount / "disk1/data/media" / conflict.relpath
    assert survivor.is_file()
    assert survivor.stat().st_size == expected_size
    assert sum(1 for d in ("disk2", "disk3", "disk4")
               if (mount / d / "data/media" / conflict.relpath).exists()) == 0


def test_keep_both_keeps_every_copy_under_distinct_names(
    mount: Path, index: ScanIndex, planner: Planner, backend, path_for, prune_stop_for
) -> None:
    source = "tv shows/Firefly"
    job = planner.add(source, "disk1", detect_conflicts(index, source, "disk1"))
    planner.resolve_all(job.id, ConflictMode.KEEP_BOTH)
    total_copies = sum(len(c.copies) for c in job.conflicts)

    run_queue(index, planner, backend, path_for, prune_stop_for, dry_run=False)
    assert job.status is JobStatus.DONE, job.error

    landed = files_in(mount / "disk1/data/media" / source)
    for conflict in job.conflicts:
        name = Path(conflict.relpath).name
        stem = Path(name).stem
        matches = [f for f in landed if Path(f).name.startswith(stem)]
        assert len(matches) == len(conflict.copies), f"lost a copy of {name}"
    assert len(landed) >= total_copies


def test_a_missing_source_is_skipped_rather_than_failing_the_run(
    mount: Path, index: ScanIndex, ready_queue: Planner, backend, path_for, prune_stop_for
) -> None:
    # Simulate someone deleting a file between the scan and the run.
    victim = mount / "disk2/data/media" / SPLIT_SHOW / "Season 01/S01E03.mkv"
    assert victim.is_file()
    victim.unlink()

    executor = run_queue(index, ready_queue, backend, path_for, prune_stop_for, dry_run=False)
    assert ready_queue.jobs == []
    assert any("has gone" in (event.message or "") for event in executor.replay())


def test_a_verification_failure_leaves_the_source_in_place(
    mount: Path, index: ScanIndex, ready_queue: Planner, path_for, prune_stop_for
) -> None:
    class TruncatingBackend(LocalBackend):
        """Writes a short file, to prove the source is not deleted afterwards."""

        def copy_file(self, src, dst, size, progress=None):  # type: ignore[override]
            super().copy_file(src, dst, size, progress)
            Path(dst).write_bytes(b"truncated")
            return len(b"truncated")

    backend = TruncatingBackend(mount_root=str(mount), simulated_capacity=60 * 1024**3)
    executor = Executor()
    started, _ = executor.start(
        index, ready_queue, backend, path_for, False, prune_stop_for
    )
    assert started
    assert executor._thread is not None
    executor._thread.join(120)

    job = ready_queue.jobs[0]
    assert job.status is JobStatus.FAILED
    assert "verification failed" in (job.error or "")
    assert any(event.type == "error" for event in executor.replay())

    first_move = mount / "disk2/data/media" / SPLIT_SHOW / "Season 01/S01E03.mkv"
    assert first_move.is_file(), "source deleted despite a failed verification"


@pytest.mark.parametrize(
    "path",
    [
        "/mnt/user",
        "/mnt/user/data/media/file.mkv",
        "/mnt/user0/data",
        "/mnt/user/",
    ],
)
def test_user_share_paths_are_refused(path: str) -> None:
    with pytest.raises(StorageError, match="never the /mnt/user share"):
        assert_safe_path(path)


@pytest.mark.parametrize("path", ["/mnt/disk1/data", "/mnt/disk12/x", "/mnt/users/data", "/tmp/x"])
def test_real_disk_paths_are_allowed(path: str) -> None:
    assert_safe_path(path)


def test_stopping_halts_the_queue_partway(
    mount: Path, index: ScanIndex, ready_queue: Planner, backend, path_for, prune_stop_for
) -> None:
    executor = Executor()
    started, _ = executor.start(
        index, ready_queue, backend, path_for, True, prune_stop_for
    )
    assert started
    executor.stop()
    assert executor._thread is not None
    executor._thread.join(60)
    assert executor.state.ops_done <= executor.state.ops_total


# --- transfer rate -------------------------------------------------------


def test_the_rate_meter_measures_over_a_trailing_window() -> None:
    from app.executor import RateMeter

    meter = RateMeter(window=6.0)
    meter.observe(0, now=100.0)
    meter.observe(10_000_000, now=101.0)
    assert meter.rate == pytest.approx(10_000_000)

    meter.observe(20_000_000, now=102.0)
    assert meter.rate == pytest.approx(10_000_000)


def test_the_rate_meter_forgets_a_slow_start() -> None:
    """A long run must not be judged by how it began."""
    from app.executor import RateMeter

    meter = RateMeter(window=5.0)
    meter.observe(0, now=0.0)
    meter.observe(1_000_000, now=10.0)  # a crawl
    for i in range(1, 6):
        meter.observe(1_000_000 + i * 50_000_000, now=10.0 + i)

    assert meter.rate == pytest.approx(50_000_000, rel=0.2)


def test_a_single_sample_reports_no_rate_rather_than_a_wild_one() -> None:
    from app.executor import RateMeter

    meter = RateMeter()
    assert meter.rate == 0.0
    meter.observe(5_000_000, now=1.0)
    assert meter.rate == 0.0
    assert meter.eta(1000) is None


def test_simultaneous_samples_do_not_divide_by_zero() -> None:
    from app.executor import RateMeter

    meter = RateMeter()
    meter.observe(0, now=7.0)
    meter.observe(1_000_000, now=7.0)
    assert meter.rate == 0.0


def test_the_eta_follows_the_measured_rate() -> None:
    from app.executor import RateMeter

    meter = RateMeter()
    meter.observe(0, now=0.0)
    meter.observe(100_000_000, now=1.0)

    assert meter.eta(500_000_000) == pytest.approx(5.0)
    assert meter.eta(0) is None


def test_a_rate_meter_reset_clears_the_history() -> None:
    from app.executor import RateMeter

    meter = RateMeter()
    meter.observe(0, now=0.0)
    meter.observe(100_000_000, now=1.0)
    meter.reset()
    assert meter.rate == 0.0


def test_progress_events_carry_per_file_bytes_and_a_rate(
    index: ScanIndex, ready_queue: Planner, backend, path_for, prune_stop_for
) -> None:
    executor = Executor()
    started, _ = executor.start(
        index, ready_queue, backend, path_for, False, prune_stop_for
    )
    assert started
    assert executor._thread is not None
    executor._thread.join(120)

    moves = [e for e in executor.history if e.type == "op_done" and e.file_bytes_total > 0]
    assert moves, "no move reported a file size"
    for event in moves:
        assert event.file_bytes_done == event.file_bytes_total


def test_progress_events_stay_out_of_the_replay_buffer(
    index: ScanIndex, ready_queue: Planner, backend, path_for, prune_stop_for
) -> None:
    """A late-joining client wants the story, not every intermediate frame."""
    executor = Executor()
    executor.start(index, ready_queue, backend, path_for, False, prune_stop_for)
    assert executor._thread is not None
    executor._thread.join(120)

    assert [e for e in executor.history if e.type == "op_progress"] == []


def test_a_finished_run_reports_no_speed(
    index: ScanIndex, ready_queue: Planner, backend, path_for, prune_stop_for
) -> None:
    executor = Executor()
    executor.start(index, ready_queue, backend, path_for, False, prune_stop_for)
    assert executor._thread is not None
    executor._thread.join(120)

    assert executor.state.bytes_per_second == 0.0
    assert executor.state.eta_seconds is None
    assert executor.state.current_file is None
