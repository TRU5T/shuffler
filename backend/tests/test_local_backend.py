"""Local backend primitives: copying, deleting and pruning."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from app.backends.base import StorageError
from app.backends.local import LocalBackend

GB = 1024**3


@pytest.fixture
def disks(tmp_path: Path) -> Path:
    mnt = tmp_path / "mnt"
    for name in ("disk1", "disk2"):
        (mnt / name).mkdir(parents=True)
    return mnt


@pytest.fixture
def local(disks: Path) -> LocalBackend:
    return LocalBackend(mount_root=str(disks))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_copy_reproduces_content_size_and_metadata(local: LocalBackend, disks: Path) -> None:
    src = disks / "disk1/movie.mkv"
    src.write_bytes(os.urandom(3 * 1024 * 1024 + 17))
    os.chmod(src, 0o640)
    os.utime(src, (1_600_000_000, 1_600_000_000))
    dst = disks / "disk2/movie.mkv"

    written = local.copy_file(str(src), str(dst), src.stat().st_size)

    assert written == src.stat().st_size
    assert digest(dst) == digest(src)
    assert int(dst.stat().st_mtime) == 1_600_000_000
    assert dst.stat().st_mode & 0o777 == 0o640


def test_copy_reports_monotonic_progress_up_to_the_total(local: LocalBackend, disks: Path) -> None:
    src = disks / "disk1/a.bin"
    src.write_bytes(os.urandom(9 * 1024 * 1024))
    size = src.stat().st_size

    seen: list[int] = []
    local.copy_file(str(src), str(disks / "disk2/a.bin"), size, lambda done, _t: seen.append(done))

    assert seen and seen == sorted(seen)
    assert seen[-1] == size
    assert all(0 <= done <= size for done in seen)


def test_a_sparse_source_stays_sparse_and_keeps_its_apparent_size(
    local: LocalBackend, disks: Path
) -> None:
    src = disks / "disk1/sparse.mkv"
    apparent = 8 * GB
    with open(src, "wb") as handle:
        handle.write(b"header")
        handle.truncate(apparent)
        handle.seek(apparent - 5)
        handle.write(b"tail!")

    dst = disks / "disk2/sparse.mkv"
    written = local.copy_file(str(src), str(dst), apparent)

    assert written == apparent
    assert dst.stat().st_size == apparent
    # The copy must not have expanded the hole into 8 GB of real blocks.
    assert dst.stat().st_blocks * 512 < 16 * 1024 * 1024
    with open(dst, "rb") as handle:
        assert handle.read(6) == b"header"
        handle.seek(apparent - 5)
        assert handle.read() == b"tail!"


def test_an_entirely_sparse_file_copies_to_the_right_length(
    local: LocalBackend, disks: Path
) -> None:
    src = disks / "disk1/hole.bin"
    with open(src, "wb") as handle:
        handle.truncate(4 * GB)
    dst = disks / "disk2/hole.bin"

    assert local.copy_file(str(src), str(dst), 4 * GB) == 4 * GB
    assert dst.stat().st_size == 4 * GB
    assert dst.stat().st_blocks * 512 < 1024 * 1024


def test_an_empty_file_copies_cleanly(local: LocalBackend, disks: Path) -> None:
    src = disks / "disk1/empty.nfo"
    src.touch()
    dst = disks / "disk2/empty.nfo"
    assert local.copy_file(str(src), str(dst), 0) == 0
    assert dst.is_file() and dst.stat().st_size == 0


def test_copy_creates_missing_destination_directories(local: LocalBackend, disks: Path) -> None:
    src = disks / "disk1/x.mkv"
    src.write_bytes(b"data")
    dst = disks / "disk2/tv shows/Show/Season 01/x.mkv"

    local.copy_file(str(src), str(dst), 4)
    assert dst.is_file()


def test_no_partial_file_survives_a_successful_copy(local: LocalBackend, disks: Path) -> None:
    src = disks / "disk1/x.mkv"
    src.write_bytes(os.urandom(1024))
    local.copy_file(str(src), str(disks / "disk2/x.mkv"), 1024)

    assert list((disks / "disk2").glob("*.shuffler-partial")) == []


def test_copying_a_missing_source_raises(local: LocalBackend, disks: Path) -> None:
    with pytest.raises(StorageError, match="copy failed"):
        local.copy_file(str(disks / "disk1/gone.mkv"), str(disks / "disk2/gone.mkv"), 10)


def test_deleting_an_absent_file_is_not_an_error(local: LocalBackend, disks: Path) -> None:
    local.delete_file(str(disks / "disk1/never-existed.mkv"))


def test_prune_removes_nested_empties_but_keeps_anything_occupied(
    local: LocalBackend, disks: Path
) -> None:
    root = disks / "disk1/data"
    (root / "empty/deeper/deepest").mkdir(parents=True)
    (root / "occupied/season").mkdir(parents=True)
    (root / "occupied/season/keep.mkv").write_bytes(b"x")

    removed = local.prune_empty_dirs(str(root))

    assert not (root / "empty").exists()
    assert (root / "occupied/season/keep.mkv").is_file()
    assert any("deepest" in path for path in removed)
    assert root.is_dir(), "pruned the root even though it still has content"


def test_prune_removes_the_root_itself_when_it_empties(local: LocalBackend, disks: Path) -> None:
    root = disks / "disk1/data/gone"
    (root / "a/b").mkdir(parents=True)
    local.prune_empty_dirs(str(root))
    assert not root.exists()


def test_remove_dir_refuses_a_directory_that_still_has_contents(
    local: LocalBackend, disks: Path
) -> None:
    occupied = disks / "disk1/data"
    occupied.mkdir(parents=True)
    (occupied / "file.mkv").write_bytes(b"x")

    assert local.remove_dir(str(occupied)) is False
    assert occupied.is_dir()


def test_prune_empty_parents_climbs_only_to_the_boundary(local: LocalBackend, disks: Path) -> None:
    deep = disks / "disk1/data/media/tv shows/Cowboy Bebop"
    deep.mkdir(parents=True)
    boundary = disks / "disk1/data/media"

    removed = local.prune_empty_parents(str(deep), str(boundary))

    assert not deep.exists()
    assert not (disks / "disk1/data/media/tv shows").exists()
    assert boundary.is_dir()
    assert len(removed) == 2


def test_walk_yields_relative_paths_and_ignores_symlinks(local: LocalBackend, disks: Path) -> None:
    media = disks / "disk1/data/media"
    (media / "tv shows/Show").mkdir(parents=True)
    (media / "tv shows/Show/ep.mkv").write_bytes(b"abc")
    (media / "top.nfo").write_bytes(b"z")
    os.symlink(media / "top.nfo", media / "link.nfo")

    found = dict(local.walk("disk1", "data/media"))

    assert set(found) == {"tv shows/Show/ep.mkv", "top.nfo"}
    assert found["tv shows/Show/ep.mkv"].size == 3


def test_walking_a_path_that_is_absent_on_this_disk_yields_nothing(local: LocalBackend) -> None:
    assert list(local.walk("disk2", "data/media")) == []


def test_simulated_capacity_derives_usage_from_apparent_sizes(disks: Path) -> None:
    with open(disks / "disk1/big.mkv", "wb") as handle:
        handle.truncate(5 * GB)

    backend = LocalBackend(mount_root=str(disks), simulated_capacity=60 * GB)
    by_name = {d.name: d for d in backend.list_disks()}

    assert by_name["disk1"].total == 60 * GB
    assert by_name["disk1"].used == 5 * GB
    assert by_name["disk1"].free == 55 * GB
    assert by_name["disk2"].used == 0


def test_disk_discovery_ignores_non_data_directories(disks: Path) -> None:
    (disks / "user").mkdir()
    (disks / "cache").mkdir()
    (disks / "disks").mkdir()
    (disks / "disk10").mkdir()

    backend = LocalBackend(mount_root=str(disks))
    assert [d.name for d in backend.list_disks()] == ["disk1", "disk2", "disk10"]


def test_check_fails_loudly_when_there_are_no_disks(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(StorageError, match="no data disks"):
        LocalBackend(mount_root=str(empty)).check()

    with pytest.raises(StorageError, match="does not exist"):
        LocalBackend(mount_root=str(tmp_path / "nope")).check()
