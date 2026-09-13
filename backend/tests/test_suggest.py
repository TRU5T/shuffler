"""Balance suggestions: clean folder moves that shrink free-space spread."""

from __future__ import annotations

from app.planner import Planner
from app.scan import ScanIndex
from app.suggest import MIN_SPREAD, suggest


def test_clean_folders_are_the_coarsest_single_disk_directories(index: ScanIndex) -> None:
    folders = {node.relpath: next(iter(node.per_disk)) for node in index.clean_folders()}

    assert folders["movies/Dune (2021)"] == "disk3"
    assert folders["movies/Blade Runner 2049 (2017)"] == "disk1"
    assert folders["music"] == "disk4"
    assert "tv shows/Cowboy Bebop/Season 02" in folders
    # Split parents are not themselves candidates.
    assert "tv shows" not in folders
    assert "tv shows/Cowboy Bebop" not in folders
    # A clean parent suppresses listing every nested directory as its own candidate.
    assert "music/Miles Davis" not in folders


def test_suggestions_move_whole_shows_not_seasons(
    index: ScanIndex, planner: Planner, path_for
) -> None:
    result = suggest(index, planner, path_for)

    assert result.balanced is False
    assert result.spread_bytes > MIN_SPREAD
    assert "disk4" in result.headline

    paths = [s.source_relpath for s in result.suggestions]
    assert all(p.count("/") == 1 for p in paths)
    assert all("/Season" not in p for p in paths)
    # Split shows are skipped; a season of Bebop must not appear.
    assert "tv shows/Cowboy Bebop/Season 02" not in paths
    assert "tv shows/Cowboy Bebop" not in paths
    # The whole show on disk3 is small enough to shrink the spread.
    assert "tv shows/Samurai Champloo" in paths
    # An 18 GB movie onto the empty disk would swap which disk is tight.
    assert "movies/Blade Runner 2049 (2017)" not in paths
    # Music already lives on the emptiest disk.
    assert "music" not in paths
    assert "music/Miles Davis" not in paths

    first = next(s for s in result.suggestions if s.source_relpath.endswith("Samurai Champloo"))
    assert first.from_disk == "disk3"
    assert first.target_disk == "disk4"
    assert first.spread_improvement > 0
    assert first.disks_after["disk3"].delta < 0
    assert first.disks_after["disk4"].delta > 0


def test_a_move_already_in_the_queue_is_not_suggested_again(
    index: ScanIndex, planner: Planner, path_for
) -> None:
    planner.add("tv shows/Samurai Champloo", "disk4", [])
    result = suggest(index, planner, path_for)
    assert all("Samurai Champloo" not in s.source_relpath for s in result.suggestions)


def test_excluded_and_hidden_folders_are_skipped(
    index: ScanIndex, planner: Planner, path_for
) -> None:
    result = suggest(
        index,
        planner,
        path_for,
        exclude=["tv shows/Samurai Champloo"],
    )
    assert all("Samurai Champloo" not in s.source_relpath for s in result.suggestions)


def test_suggestions_see_the_queued_projection(
    index: ScanIndex, planner: Planner, path_for
) -> None:
    """Parking work on a disk first should change the remaining spread."""
    before = suggest(index, planner, path_for)
    planner.add("movies/Sicario (2015)", "disk1", [])
    after = suggest(index, planner, path_for)
    assert after.spread_bytes != before.spread_bytes or after.headline != before.headline


def test_nearly_full_disks_still_flag_a_40gb_gap() -> None:
    """10% of remaining free space must not hide a real gap on full disks."""
    from app.models import Disk
    from app.suggest import MAX_CLOSE_ENOUGH_WHEN_FULL, _threshold

    tb = 1024**4
    disks = [
        Disk(name="disk1", path="/mnt/disk1", total=8 * tb, used=int(7.28 * tb), free=int(0.72 * tb)),
        Disk(name="disk2", path="/mnt/disk2", total=8 * tb, used=int(7.33 * tb), free=int(0.67 * tb)),
        Disk(name="disk3", path="/mnt/disk3", total=8 * tb, used=int(7.29 * tb), free=int(0.71 * tb)),
    ]
    frees = {d.name: d.free for d in disks}
    assert _threshold(disks, frees) <= MAX_CLOSE_ENOUGH_WHEN_FULL
    # ~50 GB between the fullest and emptiest — worth a suggestion.
    assert max(frees.values()) - min(frees.values()) > _threshold(disks, frees)


def test_a_balanced_array_offers_nothing(index: ScanIndex, path_for) -> None:
    planner = Planner(reserve_bytes=0)
    mean_used = sum(d.used for d in index.disks) // len(index.disks)
    for disk in index.disks:
        disk.used = mean_used
        disk.free = disk.total - mean_used
    result = suggest(index, planner, path_for)
    assert result.balanced is True
    assert result.suggestions == []
