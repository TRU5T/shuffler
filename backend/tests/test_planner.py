"""Conflict resolution, operation generation and the queue simulator."""

from __future__ import annotations

from app.models import ConflictMode, Job, JobStatus, OpKind
from app.planner import Planner, compute_job, detect_conflicts
from app.scan import ScanIndex

SPLIT_SHOW = "tv shows/Cowboy Bebop"
DUPED_SHOW = "tv shows/Firefly"
CONSOLIDATED = "music"


def make_job(index: ScanIndex, source: str, target: str) -> Job:
    return Job(
        id="test",
        source_relpath=source,
        target_disk=target,
        conflicts=detect_conflicts(index, source, target),
    )


def test_conflicts_are_only_files_living_on_several_disks(index: ScanIndex) -> None:
    conflicts = detect_conflicts(index, SPLIT_SHOW, "disk1")
    assert {c.relpath.split("/")[-1] for c in conflicts} == {"S01E05.mkv", "S01E06.mkv"}
    assert all(len(c.copies) > 1 for c in conflicts)
    assert all(not c.resolved for c in conflicts)


def test_a_job_with_open_conflicts_is_blocked(index: ScanIndex, path_for) -> None:
    result = compute_job(index, make_job(index, SPLIT_SHOW, "disk1"), path_for)
    assert result.unresolved
    assert "decision" in (result.blocked_reason or "")


def test_single_copy_files_simply_relocate(index: ScanIndex, path_for) -> None:
    job = make_job(index, SPLIT_SHOW, "disk1")
    for conflict in job.conflicts:
        conflict.mode = ConflictMode.SKIP
    result = compute_job(index, job, path_for)

    assert result.delete_files == 0
    assert result.move_files == 4
    # Everything the target gains, the source disks lose.
    assert result.deltas["disk1"] == result.move_bytes
    assert sum(result.deltas.values()) == 0


def test_keep_larger_moves_the_big_copy_and_deletes_the_rest(index: ScanIndex, path_for) -> None:
    job = make_job(index, SPLIT_SHOW, "disk1")
    for conflict in job.conflicts:
        conflict.mode = ConflictMode.KEEP_LARGER
    result = compute_job(index, job, path_for)

    assert not result.unresolved
    assert result.blocked_reason is None

    e05 = next(c for c in job.conflicts if c.relpath.endswith("S01E05.mkv"))
    largest_disk = max(e05.copies, key=lambda d: e05.copies[d])
    smallest_disk = min(e05.copies, key=lambda d: e05.copies[d])

    moves = {op.relpath: op for op in result.operations if op.kind is OpKind.MOVE}
    deletes = [op for op in result.operations if op.kind is OpKind.DELETE]

    assert moves[e05.relpath].size == e05.copies[largest_disk]
    assert any(op.relpath == e05.relpath and f"/{smallest_disk}/" in op.src for op in deletes)


def test_keep_smaller_is_the_mirror_image(index: ScanIndex, path_for) -> None:
    job = make_job(index, SPLIT_SHOW, "disk1")
    for conflict in job.conflicts:
        conflict.mode = ConflictMode.KEEP_SMALLER
    result = compute_job(index, job, path_for)

    e05 = next(c for c in job.conflicts if c.relpath.endswith("S01E05.mkv"))
    moved = next(op for op in result.operations if op.kind is OpKind.MOVE and op.relpath == e05.relpath)
    assert moved.size == min(e05.copies.values())


def test_keep_disk_honours_the_exact_choice_even_when_smaller(index: ScanIndex, path_for) -> None:
    job = make_job(index, SPLIT_SHOW, "disk1")
    e05 = next(c for c in job.conflicts if c.relpath.endswith("S01E05.mkv"))
    smallest = min(e05.copies, key=lambda d: e05.copies[d])
    e05.mode = ConflictMode.KEEP_DISK
    e05.keep_disk = smallest
    next(c for c in job.conflicts if c is not e05).mode = ConflictMode.SKIP

    result = compute_job(index, job, path_for)
    moved = next(op for op in result.operations if op.kind is OpKind.MOVE and op.relpath == e05.relpath)
    assert moved.size == e05.copies[smallest]
    assert e05.survivor == smallest


def test_keep_both_renames_so_nothing_is_overwritten(index: ScanIndex, path_for) -> None:
    job = make_job(index, DUPED_SHOW, "disk1")
    for conflict in job.conflicts:
        conflict.mode = ConflictMode.KEEP_BOTH
    result = compute_job(index, job, path_for)

    assert result.delete_files == 0
    destinations = [op.dst for op in result.operations if op.kind is OpKind.MOVE]
    assert len(destinations) == len(set(destinations)), "keep_both produced colliding destinations"
    assert any(op.renamed for op in result.operations if op.kind is OpKind.MOVE)


def test_skip_leaves_a_file_completely_alone(index: ScanIndex, path_for) -> None:
    job = make_job(index, SPLIT_SHOW, "disk1")
    for conflict in job.conflicts:
        conflict.mode = ConflictMode.SKIP
    result = compute_job(index, job, path_for)

    skipped = {c.relpath for c in job.conflicts}
    assert not any(op.relpath in skipped for op in result.operations)


def test_deletes_are_ordered_before_the_move_that_reuses_the_name(
    index: ScanIndex, path_for
) -> None:
    job = make_job(index, SPLIT_SHOW, "disk1")
    for conflict in job.conflicts:
        conflict.mode = ConflictMode.KEEP_LARGER
    operations = compute_job(index, job, path_for).operations

    for conflict in job.conflicts:
        kinds = [op.kind for op in operations if op.relpath == conflict.relpath]
        assert kinds.index(OpKind.DELETE) < kinds.index(OpKind.MOVE)


def test_every_source_disk_gets_a_prune_and_the_target_does_not(
    index: ScanIndex, path_for
) -> None:
    job = make_job(index, SPLIT_SHOW, "disk1")
    for conflict in job.conflicts:
        conflict.mode = ConflictMode.KEEP_LARGER
    operations = compute_job(index, job, path_for).operations

    prunes = [op for op in operations if op.kind is OpKind.PRUNE]
    assert prunes
    assert not any("/disk1/" in op.src for op in prunes)
    assert len({op.src for op in prunes}) == len(prunes)


def test_an_already_consolidated_folder_has_nothing_to_do(index: ScanIndex, path_for) -> None:
    result = compute_job(index, make_job(index, CONSOLIDATED, "disk4"), path_for)
    assert result.operations == []
    assert "already consolidated" in (result.blocked_reason or "")


def test_a_missing_source_or_disk_is_rejected(index: ScanIndex, path_for) -> None:
    missing_path = compute_job(index, make_job(index, "nope/nothing", "disk1"), path_for)
    assert "not in the current scan" in (missing_path.blocked_reason or "")

    job = make_job(index, SPLIT_SHOW, "disk99")
    assert "not present" in (compute_job(index, job, path_for).blocked_reason or "")


def test_the_queue_projects_cumulative_disk_state(index: ScanIndex, path_for) -> None:
    planner = Planner(reserve_bytes=0)
    first = planner.add(SPLIT_SHOW, "disk1", detect_conflicts(index, SPLIT_SHOW, "disk1"))
    planner.resolve_all(first.id, ConflictMode.KEEP_LARGER)
    second = planner.add(DUPED_SHOW, "disk4", detect_conflicts(index, DUPED_SHOW, "disk4"))
    planner.resolve_all(second.id, ConflictMode.KEEP_LARGER)

    plan = planner.plan(index, path_for)
    assert plan.ready
    assert [jp.job.status for jp in plan.jobs] == [JobStatus.READY, JobStatus.READY]

    # The second job's projection must build on the first, not on today's disks.
    after_first = plan.jobs[0].disks_after["disk1"].used_after
    assert after_first == index.disks[0].used + plan.jobs[0].job.move_bytes
    assert plan.jobs[1].disks_after["disk1"].used_before == index.disks[0].used
    assert plan.final["disk1"].used_after == plan.jobs[1].disks_after["disk1"].used_after

    # Deleting redundant copies is the only way the array as a whole shrinks.
    net = sum(p.delta for p in plan.final.values())
    assert net == -plan.total_reclaim_bytes


def test_reserve_pressure_blocks_a_job_before_it_runs(index: ScanIndex, path_for) -> None:
    huge_reserve = max(d.free for d in index.disks) * 2
    planner = Planner(reserve_bytes=huge_reserve)
    job = planner.add(SPLIT_SHOW, "disk1", detect_conflicts(index, SPLIT_SHOW, "disk1"))
    planner.resolve_all(job.id, ConflictMode.KEEP_LARGER)

    plan = planner.plan(index, path_for)
    assert plan.jobs[0].overflow
    assert "reserve" in (plan.jobs[0].blocked_reason or "")
    assert not plan.ready


def test_overlapping_jobs_are_flagged(index: ScanIndex, path_for) -> None:
    planner = Planner(reserve_bytes=0)
    parent = planner.add("tv shows", "disk1", detect_conflicts(index, "tv shows", "disk1"))
    planner.resolve_all(parent.id, ConflictMode.KEEP_LARGER)
    child = planner.add(SPLIT_SHOW, "disk2", detect_conflicts(index, SPLIT_SHOW, "disk2"))
    planner.resolve_all(child.id, ConflictMode.KEEP_LARGER)

    plan = planner.plan(index, path_for)
    assert "overlaps" in (plan.jobs[1].blocked_reason or "")
    assert not plan.ready


def test_reordering_and_removal_keep_positions_contiguous(index: ScanIndex) -> None:
    planner = Planner()
    a = planner.add("movies", "disk1", [])
    b = planner.add("music", "disk2", [])
    c = planner.add("tv shows", "disk3", [])

    planner.reorder([c.id, a.id, b.id])
    assert [j.id for j in planner.jobs] == [c.id, a.id, b.id]
    assert [j.position for j in planner.jobs] == [0, 1, 2]

    assert planner.remove(a.id)
    assert [j.position for j in planner.jobs] == [0, 1]
    assert not planner.remove("missing")


def test_resolutions_survive_a_rescan(index: ScanIndex, path_for, backend) -> None:
    from app.scan import run_scan

    planner = Planner(reserve_bytes=0)
    job = planner.add(SPLIT_SHOW, "disk1", detect_conflicts(index, SPLIT_SHOW, "disk1"))
    planner.resolve_all(job.id, ConflictMode.KEEP_LARGER)

    fresh = run_scan(backend, index.root)
    result = compute_job(fresh, job, path_for)
    assert not result.unresolved
    assert all(c.mode is ConflictMode.KEEP_LARGER for c in result.conflicts)


def test_bulk_resolve_only_touches_undecided_rows(index: ScanIndex) -> None:
    planner = Planner()
    job = planner.add(SPLIT_SHOW, "disk1", detect_conflicts(index, SPLIT_SHOW, "disk1"))
    first = job.conflicts[0]
    planner.resolve(job.id, first.relpath, ConflictMode.SKIP)

    planner.resolve_all(job.id, ConflictMode.KEEP_LARGER, only_unresolved=True)
    assert job.conflicts[0].mode is ConflictMode.SKIP
    assert all(c.mode is ConflictMode.KEEP_LARGER for c in job.conflicts[1:])


def test_keep_disk_bulk_skips_files_without_a_copy_there(index: ScanIndex) -> None:
    planner = Planner()
    job = planner.add(DUPED_SHOW, "disk1", detect_conflicts(index, DUPED_SHOW, "disk1"))
    planner.resolve_all(job.id, ConflictMode.KEEP_DISK, keep_disk="disk2")

    for conflict in job.conflicts:
        if "disk2" in conflict.copies:
            assert conflict.mode is ConflictMode.KEEP_DISK
        else:
            assert conflict.mode is None, "applied keep_disk to a file with no copy there"


def test_replanning_keeps_the_reason_a_job_failed(index: ScanIndex, path_for) -> None:
    """The failure note is the most useful thing on screen; don't wipe it."""
    planner = Planner(reserve_bytes=0)
    job = planner.add(SPLIT_SHOW, "disk1", detect_conflicts(index, SPLIT_SHOW, "disk1"))
    job.status = JobStatus.FAILED
    job.error = "remote command failed: TimeoutError"

    planner.plan(index, path_for)

    assert job.status is JobStatus.FAILED
    assert job.error == "remote command failed: TimeoutError"


def test_replanning_clears_a_stale_note_on_a_recovered_job(index: ScanIndex, path_for) -> None:
    planner = Planner(reserve_bytes=0)
    job = planner.add(CONSOLIDATED, "disk1", detect_conflicts(index, CONSOLIDATED, "disk1"))
    job.error = "something old"

    planner.plan(index, path_for)

    assert job.error is None


def test_drop_finished_removes_completed_and_cancelled_jobs_only(index: ScanIndex) -> None:
    planner = Planner()
    done = planner.add("movies", "disk1", [])
    done.status = JobStatus.DONE
    cancelled = planner.add("music", "disk2", [])
    cancelled.status = JobStatus.CANCELLED
    failed = planner.add("tv shows", "disk3", [])
    failed.status = JobStatus.FAILED
    ready = planner.add(SPLIT_SHOW, "disk1", [])

    assert planner.drop_finished() == 2
    assert [j.id for j in planner.jobs] == [failed.id, ready.id]
    assert planner.drop_finished() == 0
