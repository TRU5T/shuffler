"""Scanning, aggregation and duplicate classification."""

from __future__ import annotations

import pytest

from app.scan import ScanIndex, normalise_root


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("/mnt/disk1/data/media", "data/media"),
        ("/mnt/disk12/data/media/", "data/media"),
        ("data/media", "data/media"),
        ("/data/media", "data/media"),
        ("//data///media//", "data/media"),
        ("mnt/disk3/data", "data"),
        ("", ""),
        ("/", ""),
        ("/mnt/disk1", ""),
        (r"\mnt\disk1\data\media", "data/media"),
    ],
)
def test_normalise_root_addresses_the_same_tree_on_every_disk(given: str, expected: str) -> None:
    assert normalise_root(given) == expected


def test_scan_finds_every_disk_and_keys_by_relative_path(index: ScanIndex) -> None:
    assert [d.name for d in index.disks] == ["disk1", "disk2", "disk3", "disk4"]
    assert index.root == "data/media"

    # The same episode on disk2 and disk3 collapses to one entry with two copies.
    shared = index.files["tv shows/Cowboy Bebop/Season 01/S01E05.mkv"]
    assert set(shared) == {"disk2", "disk3"}
    assert shared["disk2"].size > shared["disk3"].size


def test_directory_totals_are_the_sum_of_their_disks(index: ScanIndex) -> None:
    node = index.node("tv shows/Cowboy Bebop")
    assert node is not None
    assert node.total_bytes == sum(usage.bytes for usage in node.per_disk.values())
    assert node.total_files == sum(usage.files for usage in node.per_disk.values())
    assert node.disk_count == len(node.per_disk)


def test_distinct_files_counts_a_duplicate_once(index: ScanIndex) -> None:
    node = index.node("tv shows/Cowboy Bebop")
    assert node is not None
    # Two episodes exist on two disks each, so there are two more files on disk
    # than there are distinct episodes.
    assert node.total_files == node.distinct_files + 2
    assert node.dup_files == 2


def test_root_totals_match_the_whole_index(index: ScanIndex) -> None:
    summary = index.summary()
    expected_copies = sum(len(copies) for copies in index.files.values())
    expected_bytes = sum(e.size for copies in index.files.values() for e in copies.values())
    assert summary.total_files == expected_copies
    assert summary.distinct_files == len(index.files)
    assert summary.total_bytes == expected_bytes


def test_duplicates_are_classified_by_size_agreement(index: ScanIndex) -> None:
    groups, count, wasted = index.duplicates()
    by_path = {g.relpath: g for g in groups}

    identical = by_path["tv shows/Firefly/Season 01/S01E01.mkv"]
    assert identical.kind == "identical"
    assert len(identical.copies) == 2
    assert identical.wasted_bytes == min(identical.copies.values())

    variant = by_path["tv shows/Cowboy Bebop/Season 01/S01E05.mkv"]
    assert variant.kind == "variant"

    assert count == len(groups)
    assert wasted == sum(g.wasted_bytes for g in groups)
    # Sorted by what you would get back, largest first.
    assert [g.wasted_bytes for g in groups] == sorted(
        (g.wasted_bytes for g in groups), reverse=True
    )


def test_wasted_bytes_assumes_only_the_largest_copy_survives(index: ScanIndex) -> None:
    groups, _, _ = index.duplicates()
    three_way = next(g for g in groups if g.relpath.endswith("Firefly/Season 01/S01E04.mkv"))
    assert len(three_way.copies) == 3
    assert three_way.wasted_bytes == sum(three_way.copies.values()) - max(three_way.copies.values())


def test_duplicate_filters_narrow_by_kind_and_subtree(index: ScanIndex) -> None:
    identical, _, _ = index.duplicates(kind="identical")
    assert identical and all(g.kind == "identical" for g in identical)

    scoped, _, _ = index.duplicates(under="movies")
    assert scoped and all(g.relpath.startswith("movies/") for g in scoped)

    none, count, wasted = index.duplicates(under="music")
    assert none == [] and count == 0 and wasted == 0


def test_fragmented_reports_the_shallowest_split_folder_only(index: ScanIndex) -> None:
    fragmented = index.fragmented()
    paths = [node.relpath for node in fragmented]

    assert "movies" in paths
    assert "tv shows" in paths
    # `tv shows` is already reported, so its children must not be listed too.
    assert not any(path.startswith("tv shows/") for path in paths)


def test_files_under_includes_the_whole_subtree(index: ScanIndex) -> None:
    under = dict(index.files_under("tv shows/Cowboy Bebop"))
    assert under
    assert all(path.startswith("tv shows/Cowboy Bebop/") for path in under)
    assert dict(index.files_under("")) == index.files


def test_children_put_directories_first_then_largest(index: ScanIndex) -> None:
    children = index.children("")
    assert [c.name for c in children][:2] == ["movies", "tv shows"]
    assert all(child.is_dir for child in children)


def test_unknown_paths_return_nothing(index: ScanIndex) -> None:
    assert index.node("does/not/exist") is None
    assert index.children("does/not/exist") == []
