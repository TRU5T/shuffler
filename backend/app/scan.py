"""Cross-disk scan index.

A scan walks the same relative subpath (e.g. `data/media`) on every data disk
and keys everything it finds by that relative path. Two copies of
`tv shows/Cowboy Bebop/s01e01.mkv` on disk2 and disk3 therefore collapse to one
index entry with two disk copies, which is exactly the duplicate signal we want.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

from .backends.base import StorageBackend
from .models import (
    Disk,
    DiskUsage,
    DuplicateGroup,
    DuplicateKind,
    FileEntry,
    ScanSummary,
    TreeNode,
)


@dataclass
class _Node:
    name: str
    relpath: str
    is_dir: bool
    #: disk name -> [bytes, file_count]
    per_disk: dict[str, list[int]] = field(default_factory=dict)
    total_bytes: int = 0
    total_files: int = 0
    distinct_files: int = 0
    dup_files: int = 0
    dup_wasted: int = 0
    children: dict[str, "_Node"] = field(default_factory=dict)


def classify(sizes: list[int]) -> DuplicateKind:
    return "identical" if len(set(sizes)) == 1 else "variant"


def normalise_root(root: str) -> str:
    """Turn any user-supplied path into a subpath relative to a disk mount.

    Accepts `/mnt/disk1/data/media`, `/data/media` or `data/media` and always
    yields `data/media`, so the same tree can be addressed on every disk.
    """
    cleaned = (root or "").strip().replace("\\", "/")
    while "//" in cleaned:
        cleaned = cleaned.replace("//", "/")
    cleaned = cleaned.strip("/")
    parts = cleaned.split("/") if cleaned else []
    # Drop a leading `mnt/<disk>` so pasting a real path from the array works.
    if len(parts) >= 2 and parts[0] == "mnt":
        parts = parts[2:]
    return "/".join(parts)


class ScanIndex:
    """An immutable, queryable view of one scan."""

    def __init__(
        self,
        scan_id: str,
        root: str,
        disks: list[Disk],
        files: dict[str, dict[str, FileEntry]],
        created_at: float | None = None,
        duration_seconds: float = 0.0,
    ) -> None:
        self.id = scan_id
        self.root = root
        self.disks = disks
        self.files = files
        self.created_at = created_at if created_at is not None else time.time()
        self.duration_seconds = duration_seconds
        self._root_node = _Node(name=root or "/", relpath="", is_dir=True)
        self._build()

    # --- construction -----------------------------------------------------

    def _build(self) -> None:
        for relpath, copies in self.files.items():
            self._insert(relpath, copies)

    def _insert(self, relpath: str, copies: dict[str, FileEntry]) -> None:
        sizes = [entry.size for entry in copies.values()]
        total = sum(sizes)
        is_dup = len(copies) > 1
        wasted = total - max(sizes) if is_dup else 0

        parts = relpath.split("/")
        node = self._root_node
        self._accumulate(node, copies, total, is_dup, wasted)
        for depth, part in enumerate(parts):
            is_leaf = depth == len(parts) - 1
            child = node.children.get(part)
            if child is None:
                child = _Node(
                    name=part,
                    relpath="/".join(parts[: depth + 1]),
                    is_dir=not is_leaf,
                )
                node.children[part] = child
            node = child
            self._accumulate(node, copies, total, is_dup, wasted)

    @staticmethod
    def _accumulate(
        node: _Node,
        copies: dict[str, FileEntry],
        total: int,
        is_dup: bool,
        wasted: int,
    ) -> None:
        for disk, entry in copies.items():
            bucket = node.per_disk.setdefault(disk, [0, 0])
            bucket[0] += entry.size
            bucket[1] += 1
        node.total_bytes += total
        # Copies, not distinct paths, so bytes and file counts describe the same
        # thing: what is actually sitting on the disks.
        node.total_files += len(copies)
        node.distinct_files += 1
        if is_dup:
            node.dup_files += 1
            node.dup_wasted += wasted

    # --- queries ----------------------------------------------------------

    def _find(self, relpath: str) -> _Node | None:
        node = self._root_node
        if not relpath:
            return node
        for part in relpath.split("/"):
            node = node.children.get(part)  # type: ignore[assignment]
            if node is None:
                return None
        return node

    def _to_model(self, node: _Node) -> TreeNode:
        return TreeNode(
            name=node.name,
            relpath=node.relpath,
            is_dir=node.is_dir,
            total_bytes=node.total_bytes,
            total_files=node.total_files,
            distinct_files=node.distinct_files,
            per_disk={
                disk: DiskUsage(bytes=v[0], files=v[1]) for disk, v in sorted(node.per_disk.items())
            },
            disk_count=len(node.per_disk),
            dup_files=node.dup_files,
            dup_wasted_bytes=node.dup_wasted,
            has_children=bool(node.children),
        )

    def node(self, relpath: str) -> TreeNode | None:
        node = self._find(relpath)
        return self._to_model(node) if node else None

    def children(self, relpath: str = "") -> list[TreeNode]:
        node = self._find(relpath)
        if node is None:
            return []
        models = [self._to_model(child) for child in node.children.values()]
        # Directories first, then largest first: the things worth consolidating
        # float to the top.
        models.sort(key=lambda n: (not n.is_dir, -n.total_bytes, n.name.lower()))
        return models

    def files_under(self, relpath: str) -> Iterator[tuple[str, dict[str, FileEntry]]]:
        """Every indexed file at or beneath `relpath`."""
        if not relpath:
            yield from self.files.items()
            return
        prefix = relpath + "/"
        for path, copies in self.files.items():
            if path == relpath or path.startswith(prefix):
                yield path, copies

    def duplicates(
        self,
        under: str = "",
        kind: DuplicateKind | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[DuplicateGroup], int, int]:
        """Duplicate groups sorted by reclaimable bytes, plus total count/bytes."""
        groups: list[DuplicateGroup] = []
        for path, copies in self.files_under(under):
            if len(copies) < 2:
                continue
            sizes = {disk: entry.size for disk, entry in copies.items()}
            group_kind = classify(list(sizes.values()))
            if kind and group_kind != kind:
                continue
            groups.append(
                DuplicateGroup(
                    relpath=path,
                    kind=group_kind,
                    copies=dict(sorted(sizes.items())),
                    wasted_bytes=sum(sizes.values()) - max(sizes.values()),
                )
            )
        groups.sort(key=lambda g: (-g.wasted_bytes, g.relpath))
        total_wasted = sum(g.wasted_bytes for g in groups)
        total_count = len(groups)
        window = groups[offset : offset + limit] if limit is not None else groups[offset:]
        return window, total_count, total_wasted

    def fragmented(self, under: str = "", min_disks: int = 2, limit: int = 100) -> list[TreeNode]:
        """Directories whose contents are split across disks.

        Only the shallowest fragmented directory in any branch is reported: if
        `tv shows` is split, listing every season below it as well is noise.
        """
        start = self._find(under)
        if start is None:
            return []
        found: list[_Node] = []
        stack = [start]
        while stack:
            node = stack.pop()
            if node is not start and node.is_dir and len(node.per_disk) >= min_disks:
                found.append(node)
                continue
            stack.extend(child for child in node.children.values() if child.is_dir)
        found.sort(key=lambda n: (-n.total_bytes, n.relpath))
        return [self._to_model(n) for n in found[:limit]]

    def clean_folders(self) -> list[TreeNode]:
        """Directories that live on exactly one disk, at the coarsest grain.

        If `movies/Dune (2021)` is wholly on disk3, we report that folder and
        not every file inside it. Split parents are descended into so a show
        that is scattered can still yield a clean season.
        """
        found: list[_Node] = []

        def walk(node: _Node, at_root: bool) -> None:
            if not node.is_dir:
                return
            if at_root:
                for child in node.children.values():
                    walk(child, False)
                return
            if len(node.per_disk) == 1:
                found.append(node)
                return
            for child in node.children.values():
                walk(child, False)

        walk(self._root_node, True)
        found.sort(key=lambda n: (-n.total_bytes, n.relpath))
        return [self._to_model(n) for n in found]

    def summary(self) -> ScanSummary:
        root = self._root_node
        return ScanSummary(
            id=self.id,
            root=self.root,
            created_at=self.created_at,
            disks=self.disks,
            total_files=root.total_files,
            distinct_files=root.distinct_files,
            total_bytes=root.total_bytes,
            dup_files=root.dup_files,
            dup_wasted_bytes=root.dup_wasted,
            duration_seconds=self.duration_seconds,
        )

    def db_rows(self) -> Iterator[tuple[str, str, str, int, float]]:
        for relpath, copies in self.files.items():
            for disk, entry in copies.items():
                yield (self.id, relpath, disk, entry.size, entry.mtime)


def run_scan(backend: StorageBackend, root: str, disks: list[Disk] | None = None) -> ScanIndex:
    """Walk `root` on every data disk and build an index."""
    subpath = normalise_root(root)
    started = time.monotonic()
    disk_list = disks if disks is not None else backend.list_disks()
    files: dict[str, dict[str, FileEntry]] = {}
    for disk in disk_list:
        for relpath, entry in backend.walk(disk.name, subpath):
            files.setdefault(relpath, {})[disk.name] = entry
    return ScanIndex(
        scan_id=uuid.uuid4().hex[:12],
        root=subpath,
        disks=disk_list,
        files=files,
        duration_seconds=time.monotonic() - started,
    )
