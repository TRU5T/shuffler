#!/usr/bin/env python3
"""Build a synthetic multi-disk tree for testing.

Files are created sparse, so a 40 GB fake media library costs a few kilobytes
on disk. That lets the whole scan/plan/execute path be exercised end-to-end
without touching a real array.

    python scripts/make_fixtures.py --root /tmp/shuffler-fixtures --reset

The result looks like a small Unraid array:

    <root>/mnt/disk1/data/media/tv shows/...
    <root>/mnt/disk2/data/media/tv shows/...

`disk2` and `disk3` deliberately share some episode paths at different sizes so
the duplicate and conflict flows have something to chew on.
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

GB = 1024**3
MB = 1024**2

# (disk, relative path below the media root, size in bytes)
LAYOUT: list[tuple[str, str, int]] = [
    # A show cleanly split across two disks: the canonical consolidation case.
    ("disk1", "tv shows/Cowboy Bebop/Season 01/S01E01.mkv", 3800 * MB),
    ("disk1", "tv shows/Cowboy Bebop/Season 01/S01E02.mkv", 3700 * MB),
    ("disk2", "tv shows/Cowboy Bebop/Season 01/S01E03.mkv", 3600 * MB),
    ("disk2", "tv shows/Cowboy Bebop/Season 01/S01E04.mkv", 3900 * MB),
    ("disk3", "tv shows/Cowboy Bebop/Season 02/S02E01.mkv", 3500 * MB),
    ("disk3", "tv shows/Cowboy Bebop/Season 02/S02E02.mkv", 3400 * MB),
    # Same path on two disks with different sizes: a "variant" duplicate.
    ("disk2", "tv shows/Cowboy Bebop/Season 01/S01E05.mkv", 3800 * MB),
    ("disk3", "tv shows/Cowboy Bebop/Season 01/S01E05.mkv", 888 * MB),
    ("disk2", "tv shows/Cowboy Bebop/Season 01/S01E06.mkv", 4100 * MB),
    ("disk3", "tv shows/Cowboy Bebop/Season 01/S01E06.mkv", 950 * MB),
    # Byte-identical copies on two disks: an "identical" duplicate.
    ("disk1", "tv shows/Firefly/Season 01/S01E01.mkv", 2200 * MB),
    ("disk4", "tv shows/Firefly/Season 01/S01E01.mkv", 2200 * MB),
    ("disk1", "tv shows/Firefly/Season 01/S01E02.mkv", 2100 * MB),
    ("disk4", "tv shows/Firefly/Season 01/S01E03.mkv", 2300 * MB),
    # A three-way variant, to prove keep_disk works with more than two copies.
    ("disk1", "tv shows/Firefly/Season 01/S01E04.mkv", 2400 * MB),
    ("disk2", "tv shows/Firefly/Season 01/S01E04.mkv", 1200 * MB),
    ("disk4", "tv shows/Firefly/Season 01/S01E04.mkv", 700 * MB),
    # Movies spread thin across every disk.
    ("disk1", "movies/Blade Runner 2049 (2017)/Blade Runner 2049.mkv", 18 * GB),
    ("disk2", "movies/Arrival (2016)/Arrival.mkv", 12 * GB),
    ("disk3", "movies/Dune (2021)/Dune.mkv", 22 * GB),
    ("disk4", "movies/Sicario (2015)/Sicario.mkv", 9 * GB),
    ("disk2", "movies/Arrival (2016)/poster.jpg", 240 * 1024),
    ("disk3", "movies/Arrival (2016)/poster.jpg", 240 * 1024),
    # A show that lives wholly on one disk, sized so it can rebalance without
    # overshooting the empty disk — the balance-suggestion happy path.
    ("disk3", "tv shows/Samurai Champloo/Season 01/S01E01.mkv", 3500 * MB),
    ("disk3", "tv shows/Samurai Champloo/Season 01/S01E02.mkv", 3400 * MB),
    # A folder already fully consolidated, which should report nothing to do.
    ("disk4", "music/Miles Davis/Kind of Blue/01 So What.flac", 220 * MB),
    ("disk4", "music/Miles Davis/Kind of Blue/02 Freddie Freeloader.flac", 240 * MB),
]

DISKS = ["disk1", "disk2", "disk3", "disk4"]
MEDIA_ROOT = "data/media"


def write_sparse(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        if size > 0:
            # A real first and last byte means the file is never zero-length,
            # while the middle stays a hole.
            handle.write(b"\x00")
            handle.truncate(size)
            handle.seek(size - 1)
            handle.write(b"\xff")


def build(root: Path, reset: bool) -> None:
    mnt = root / "mnt"
    if reset and mnt.exists():
        shutil.rmtree(mnt)
    for disk in DISKS:
        (mnt / disk / MEDIA_ROOT).mkdir(parents=True, exist_ok=True)

    total = 0
    for disk, relpath, size in LAYOUT:
        write_sparse(mnt / disk / MEDIA_ROOT / relpath, size)
        total += size

    print(f"fixtures at {mnt}")
    print(f"  {len(DISKS)} disks, {len(LAYOUT)} files, {total / GB:.1f} GB apparent")
    apparent = sum(
        os.stat(p).st_size for p in mnt.rglob("*") if p.is_file()
    )
    actual = sum(
        os.stat(p).st_blocks * 512 for p in mnt.rglob("*") if p.is_file()
    )
    print(f"  apparent {apparent / GB:.1f} GB, actually on disk {actual / MB:.2f} MB")
    print(f"\nPoint Shuffler at: {mnt}  (mount root)   root: {MEDIA_ROOT}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/tmp/shuffler-fixtures")
    parser.add_argument("--reset", action="store_true", help="delete an existing tree first")
    args = parser.parse_args()
    build(Path(args.root), args.reset)


if __name__ == "__main__":
    main()
