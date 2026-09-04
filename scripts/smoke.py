#!/usr/bin/env python3
"""End-to-end smoke check of scan -> plan -> execute against the fixtures.

Run after `make_fixtures.py`:

    PYTHONPATH=backend .venv/bin/python scripts/smoke.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))
sys.path.insert(0, str(HERE))

from make_fixtures import build as build_fixtures  # noqa: E402

from app.backends.local import LocalBackend  # noqa: E402
from app.executor import Executor  # noqa: E402
from app.models import ConflictMode, JobStatus  # noqa: E402
from app.planner import Planner, compute_job  # noqa: E402
from app.scan import run_scan  # noqa: E402

GB = 1024**3
MB = 1024**2


def human(n: int) -> str:
    sign = "-" if n < 0 else ""
    n = abs(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{sign}{n:.1f} {unit}" if unit != "B" else f"{sign}{n} B"
        n /= 1024
    return ""


def main() -> int:
    # Build fixtures directly in the scratch directory. Copying them would
    # expand every hole and fill the disk.
    work = Path(tempfile.mkdtemp(prefix="shuffler-smoke-"))
    build_fixtures(work, reset=True)
    mnt = work / "mnt"
    print()

    # 60 GB per disk, derived from apparent file sizes, so the sparse fixtures
    # behave like four separate disks instead of one shared root filesystem.
    backend = LocalBackend(mount_root=str(mnt), simulated_capacity=60 * GB)
    print("check:", backend.check())
    for d in backend.list_disks():
        print(f"  {d.name}: {human(d.used)} used, {human(d.free)} free of {human(d.total)}")

    index = run_scan(backend, "data/media")
    summary = index.summary()
    print(
        f"\nscan {summary.id}: {summary.total_files} files, {human(summary.total_bytes)}, "
        f"{summary.dup_files} duplicate file(s) wasting {human(summary.dup_wasted_bytes)} "
        f"in {summary.duration_seconds * 1000:.0f} ms"
    )

    print("\ntop level:")
    for node in index.children(""):
        spread = ", ".join(f"{d} {human(u.bytes)}" for d, u in node.per_disk.items())
        print(f"  {node.name:<12} {human(node.total_bytes):>10}  [{spread}]")

    print("\nduplicates:")
    groups, count, wasted = index.duplicates()
    print(f"  {count} group(s), {human(wasted)} reclaimable")
    for g in groups[:6]:
        copies = ", ".join(f"{d} {human(s)}" for d, s in g.copies.items())
        print(f"  [{g.kind:<9}] {g.relpath}  ({copies}) waste {human(g.wasted_bytes)}")

    print("\nfragmented directories:")
    for node in index.fragmented()[:6]:
        print(f"  {node.relpath}  across {node.disk_count} disks, {human(node.total_bytes)}")

    def path_for(disk: str, relpath: str) -> str:
        parts = [p for p in (index.root, relpath) if p]
        return backend.full_path(disk, "/".join(parts))

    planner = Planner(reserve_bytes=0)
    target = "disk1"
    source_dir = "tv shows/Cowboy Bebop"
    from app.planner import detect_conflicts

    job = planner.add(source_dir, target, detect_conflicts(index, source_dir, target))
    plan = planner.plan(index, path_for)
    jp = plan.jobs[0]
    print(f"\njob: consolidate '{source_dir}' -> {target}")
    print(f"  blocked: {jp.blocked_reason}")
    print(f"  conflicts: {len(job.conflicts)} ({job.unresolved_conflicts} unresolved)")

    assert job.unresolved_conflicts == len(job.conflicts) > 0, "expected unresolved conflicts"
    assert job.status is JobStatus.DRAFT, "job should be blocked while conflicts are open"

    planner.resolve_all(job.id, ConflictMode.KEEP_LARGER)
    plan = planner.plan(index, path_for)
    jp = plan.jobs[0]
    print("\nafter resolving every conflict as keep_larger:")
    print(f"  status={job.status.value} blocked={jp.blocked_reason}")
    print(f"  move {job.move_files} file(s) {human(job.move_bytes)}, "
          f"delete {job.delete_files} redundant copy/copies {human(job.reclaim_bytes)}")
    for name, p in jp.disks_after.items():
        print(
            f"  {name}: free {human(p.free_before)} -> {human(p.free_after)} "
            f"(delta {human(p.delta)})"
        )
    assert job.status is JobStatus.READY, f"expected READY, got {job.status}"

    ops = compute_job(index, job, path_for).operations
    print(f"\n{len(ops)} operation(s); first few:")
    for op in ops[:6]:
        print(f"  {op.kind.value:<6} {op.relpath}  {human(op.size)}")

    # Dry run must leave the tree untouched.
    before = sorted(str(p.relative_to(mnt)) for p in mnt.rglob("*") if p.is_file())
    executor = Executor()
    ok, msg = executor.start(
        index, planner, backend, path_for, dry_run=True,
        prune_stop_for=lambda d, _="": backend.full_path(d, index.root),
    )
    print(f"\ndry run: {ok} {msg}")
    assert ok, f"dry run refused to start: {msg}"
    executor._thread.join(60)
    after = sorted(str(p.relative_to(mnt)) for p in mnt.rglob("*") if p.is_file())
    assert before == after, "dry run modified the filesystem"
    print(f"  filesystem unchanged ({len(after)} files), {executor.state.ops_done} ops announced")

    # Live run.
    job.status = JobStatus.READY
    executor2 = Executor()
    ok, msg = executor2.start(
        index, planner, backend, path_for, dry_run=False,
        prune_stop_for=lambda d, _="": backend.full_path(d, index.root),
    )
    print(f"\nlive run: {ok} {msg}")
    assert ok, f"live run refused to start: {msg}"
    executor2._thread.join(300)
    print(f"  status={job.status.value} error={job.error}")
    assert job.status is JobStatus.DONE, f"live run failed: {job.error}"

    leftovers = [
        str(p.relative_to(mnt))
        for disk in ("disk2", "disk3", "disk4")
        for p in (mnt / disk / "data/media/tv shows/Cowboy Bebop").rglob("*")
        if p.is_file()
    ]
    print(f"  Cowboy Bebop files left on other disks: {leftovers}")
    assert not leftovers, "consolidation left files behind"

    for disk in ("disk2", "disk3"):
        stray = mnt / disk / "data/media/tv shows/Cowboy Bebop"
        assert not stray.exists(), f"empty folder not pruned: {stray}"
    print("  empty source folders pruned")

    # `tv shows` still holds Firefly on disk1/disk2/disk4, so it must survive.
    assert (mnt / "disk2/data/media/tv shows").is_dir(), "pruned a folder that still has content"
    print("  folders with remaining content preserved")

    after_index = run_scan(backend, "data/media")
    node = after_index.node(source_dir)
    print(f"\nrescan: '{source_dir}' now on {node.disk_count} disk(s): "
          f"{list(node.per_disk)} {human(node.total_bytes)}")
    assert node.disk_count == 1 and target in node.per_disk

    shutil.rmtree(work)
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
