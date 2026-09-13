"""Suggest whole-title moves that even out free space across the array.

Only show / movie / artist folders that already live on a single disk are
considered, so a suggestion never invents a conflict decision and never offers
a season or extras folder. Each pick is an ordinary job: add it to the queue,
skip it, or hide the folder. Nothing is run from here.
"""

from __future__ import annotations

from .models import Disk, DiskProjection, Suggestion, SuggestionSet, TreeNode
from .planner import PathBuilder, Planner
from .scan import ScanIndex

#: Spread below this share of mean disk size is "close enough".
SPREAD_RATIO = 0.02
#: Never treat a spread smaller than this as unbalanced, even on tiny disks.
MIN_SPREAD = 512 * 1024**2
#: On disks that are already more than half full, never require more imbalance
#: than this before speaking up. 10% of remaining free space grows as disks
#: fill (650 GB free → 65 GB bar) and will hide a 40 GB gap on a 7 TB disk
#: that is 90% full.
MAX_CLOSE_ENOUGH_WHEN_FULL = 32 * 1024**3
#: Ignore folders smaller than this fraction of the current spread — moving
#: crumbs does not rebalance an array.
MIN_FOLDER_RATIO = 0.05
MIN_FOLDER_BYTES = 256 * 1024**2
DEFAULT_LIMIT = 8
#: Top-level folders under a media root. Their children are the titles we move.
_LIBRARY_FOLDERS = frozenset(
    {
        "tv shows",
        "tv",
        "television",
        "anime series",
        "anime",
        "movies",
        "films",
        "movie",
        "music",
        "audio",
        "books",
        "audiobooks",
    }
)


def _free_map(disks: list[Disk], used: dict[str, int]) -> dict[str, int]:
    return {d.name: d.total - used[d.name] for d in disks}


def _spread(frees: dict[str, int]) -> int:
    if not frees:
        return 0
    values = list(frees.values())
    return max(values) - min(values)


def _mean(frees: dict[str, int]) -> int:
    if not frees:
        return 0
    return sum(frees.values()) // len(frees)


def _threshold(disks: list[Disk], frees: dict[str, int]) -> int:
    """How far apart free space can be before a suggestion is worth making.

    A 70 GB gap on nearly-full 7 TB disks is a real imbalance; the same gap on
    empty disks is noise. Take 10% of typical free space, but never more than
    2% of capacity, so a spacious array does not nag. When the array is already
    more than half full, also cap the bar so a 40 GB gap is not dismissed.
    """
    if not disks:
        return MIN_SPREAD
    mean_total = sum(d.total for d in disks) // len(disks)
    mean_free = sum(frees.values()) // len(frees) if frees else 0
    by_size = int(mean_total * SPREAD_RATIO)
    by_free = int(mean_free * 0.10) if mean_free else by_size
    threshold = max(MIN_SPREAD, min(by_size, by_free) if by_free else by_size)
    if mean_total and mean_free < mean_total // 2:
        threshold = min(threshold, MAX_CLOSE_ENOUGH_WHEN_FULL)
    return threshold


def _projections(
    disks: list[Disk],
    used_before: dict[str, int],
    used_after: dict[str, int],
    reserve: int,
) -> dict[str, DiskProjection]:
    out: dict[str, DiskProjection] = {}
    for disk in disks:
        before = used_before[disk.name]
        after = used_after[disk.name]
        free_after = disk.total - after
        delta = after - before
        out[disk.name] = DiskProjection(
            name=disk.name,
            total=disk.total,
            used_before=before,
            free_before=disk.total - before,
            used_after=after,
            free_after=free_after,
            delta=delta,
            overflow=free_after < 0 or (delta > 0 and free_after < reserve),
        )
    return out


def _headline(disks: list[Disk], frees: dict[str, int], mean_free: int, spread: int) -> str:
    if not disks:
        return "No disks to compare."
    names = {d.name: d for d in disks}
    outlier = max(names, key=lambda n: abs(frees[n] - mean_free))
    delta = frees[outlier] - mean_free
    if delta >= 0:
        return f"{outlier} has {_human(delta)} more free space than average"
    return f"{outlier} is {_human(-delta)} fuller than average"


def _human(n: int) -> str:
    gb = 1024**3
    mb = 1024**2
    if n >= gb:
        value = n / gb
        return f"{value:.0f} GB" if value >= 10 else f"{value:.1f} GB"
    return f"{n / mb:.0f} MB"


def _sole_disk(node: TreeNode) -> tuple[str, int] | None:
    if node.disk_count != 1 or not node.is_dir:
        return None
    disk, usage = next(iter(node.per_disk.items()))
    return disk, usage.bytes


def _title_folders(index: ScanIndex) -> list[TreeNode]:
    """Show / movie / artist folders — never seasons, extras, or albums nested further.

    Under a media root (`tv shows/Mayans MC`) titles sit one level down. If the
    scan is already a library (`tv shows`), the children of the root are titles.
    """
    top = [node for node in index.children("") if node.is_dir]
    libraries = [node for node in top if node.name.lower() in _LIBRARY_FOLDERS]
    if libraries:
        titles: list[TreeNode] = []
        for library in libraries:
            titles.extend(child for child in index.children(library.relpath) if child.is_dir)
        return titles
    return top


def suggest(
    index: ScanIndex,
    planner: Planner,
    path_for: PathBuilder,
    exclude: list[str] | None = None,
    hidden: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> SuggestionSet:
    """Greedy pack of whole-title moves that shrink the free-space spread.

    Uses the queue's projected disk state, so a suggestion sees work you have
    already queued. Later suggestions in the list assume the earlier ones were
    accepted, which is only a preview — adding one job and refetching is how
    the UI stays honest.
    """
    disks = list(index.disks)
    plan = planner.plan(index, path_for)
    used = {d.name: plan.final[d.name].used_after if d.name in plan.final else d.used for d in disks}
    frees = _free_map(disks, used)
    mean_free = _mean(frees)
    spread = _spread(frees)
    threshold = _threshold(disks, frees)
    reserve = planner.reserve_bytes

    if spread < threshold:
        return SuggestionSet(
            balanced=True,
            headline="Disks are close enough — nothing worth moving",
            spread_bytes=spread,
            mean_free_bytes=mean_free,
        )

    blocked = set(exclude or []) | set(hidden or [])
    queued = [job.source_relpath for job in planner.jobs]
    min_size = max(MIN_FOLDER_BYTES, int(spread * MIN_FOLDER_RATIO))
    donors_now = {n for n, free in frees.items() if free < mean_free}

    pool: list[TreeNode] = []
    for node in _title_folders(index):
        if node.relpath in blocked:
            continue
        if planner._overlap(node.relpath, queued) is not None:
            continue
        sole = _sole_disk(node)
        if sole is None or sole[1] < min_size:
            continue
        src, _size = sole
        if src not in donors_now:
            continue
        pool.append(node)
    pool.sort(key=lambda n: -n.total_bytes)
    pool = pool[:200]

    suggestions: list[Suggestion] = []
    remaining = list(pool)

    while remaining and len(suggestions) < limit:
        mean_free = _mean(frees)
        spread = _spread(frees)
        donors = {n for n, free in frees.items() if free < mean_free}
        sinks = {n for n, free in frees.items() if free > mean_free}
        if not donors or not sinks:
            break

        best: tuple[int, int, TreeNode, str, str, int] | None = None
        # tuple is (improvement, -overshoot, node, src, sink, size)
        for node in remaining:
            sole = _sole_disk(node)
            if sole is None:
                continue
            src, size = sole
            if src not in donors:
                continue
            for sink in sinks:
                if sink == src:
                    continue
                if frees[sink] - size < reserve:
                    continue
                new_frees = dict(frees)
                new_frees[src] += size
                new_frees[sink] -= size
                improvement = spread - _spread(new_frees)
                if improvement <= 0:
                    continue
                # Prefer a tighter fit: overshooting the mean on the sink is fine
                # if spread still drops, but a closer shot ranks higher.
                overshoot = abs(new_frees[sink] - (sum(new_frees.values()) // len(new_frees)))
                candidate = (improvement, -overshoot, node, src, sink, size)
                if best is None or candidate[:2] > best[:2]:
                    best = candidate

        if best is None:
            break

        improvement, _, node, src, sink, size = best
        used_before = dict(used)
        used[src] -= size
        used[sink] += size
        frees = _free_map(disks, used)
        remaining = [
            n
            for n in remaining
            if n.relpath != node.relpath
            and planner._overlap(n.relpath, [node.relpath]) is None
        ]
        suggestions.append(
            Suggestion(
                source_relpath=node.relpath,
                source_name=node.name,
                from_disk=src,
                target_disk=sink,
                move_bytes=size,
                move_files=node.total_files,
                disks_after=_projections(disks, used_before, used, reserve),
                spread_improvement=improvement,
            )
        )

    frees_now = _free_map(
        disks,
        {d.name: plan.final[d.name].used_after if d.name in plan.final else d.used for d in disks},
    )
    mean_now = _mean(frees_now)
    spread_now = _spread(frees_now)
    headline = _headline(disks, frees_now, mean_now, spread_now)
    if not suggestions:
        headline = f"{headline}. No whole show would even them out without overshooting."
    return SuggestionSet(
        balanced=False,
        headline=headline,
        spread_bytes=spread_now,
        mean_free_bytes=mean_now,
        suggestions=suggestions,
    )
