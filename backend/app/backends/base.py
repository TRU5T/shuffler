"""The storage backend interface every implementation must satisfy."""

from __future__ import annotations

import posixpath
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator

from ..models import Disk, FileEntry

#: Called with (bytes_copied_so_far, total_bytes) during a copy.
CopyProgress = Callable[[int, int], None]

#: Unraid's fuse-based user share. Moving data between disks *through* this path
#: can silently destroy files, so every path the executor touches is checked
#: against it. This is the single most important safety rule in the app.
FORBIDDEN_PREFIXES = ("/mnt/user/", "/mnt/user0/")
FORBIDDEN_EXACT = ("/mnt/user", "/mnt/user0")


class StorageError(RuntimeError):
    """Raised for backend failures that should surface to the user verbatim."""


def assert_safe_path(path: str) -> None:
    """Reject paths that route through an Unraid user share."""
    normalised = posixpath.normpath(path)
    if normalised in FORBIDDEN_EXACT or normalised.startswith(FORBIDDEN_PREFIXES):
        raise StorageError(
            f"refusing to operate on {path!r}: transfers must address disks directly "
            "(/mnt/diskN), never the /mnt/user share"
        )


class StorageBackend(ABC):
    """Read and write access to a set of Unraid data disks."""

    def __init__(self, mount_root: str = "/mnt", disk_pattern: str = r"^disk\d+$") -> None:
        self.mount_root = mount_root.rstrip("/") or "/"
        self._disk_re = re.compile(disk_pattern)

    # --- naming helpers ---------------------------------------------------

    def disk_path(self, disk: str) -> str:
        return posixpath.join(self.mount_root, disk)

    def full_path(self, disk: str, relpath: str = "") -> str:
        base = self.disk_path(disk)
        return posixpath.join(base, relpath) if relpath else base

    def is_data_disk(self, name: str) -> bool:
        return bool(self._disk_re.match(name))

    # --- required operations ---------------------------------------------

    @abstractmethod
    def check(self) -> str:
        """Verify the backend is usable; return a human-readable description."""

    @abstractmethod
    def list_disks(self) -> list[Disk]:
        """Discover data disks and their capacity, sorted by disk number."""

    @abstractmethod
    def walk(self, disk: str, subpath: str) -> Iterator[tuple[str, FileEntry]]:
        """Yield `(relpath_below_subpath, entry)` for every file under the subpath."""

    @abstractmethod
    def list_dir(self, path: str) -> list[tuple[str, bool]]:
        """List a directory as `(name, is_dir)` pairs. Returns [] if absent."""

    @abstractmethod
    def exists(self, path: str) -> bool: ...

    @abstractmethod
    def size_of(self, path: str) -> int | None:
        """File size in bytes, or None when the path is missing."""

    @abstractmethod
    def makedirs(self, path: str) -> None: ...

    @abstractmethod
    def copy_file(self, src: str, dst: str, size: int, progress: CopyProgress | None = None) -> int:
        """Copy preserving mtime and mode; return the bytes written at the destination."""

    @abstractmethod
    def delete_file(self, path: str) -> None: ...

    @abstractmethod
    def prune_empty_dirs(self, root: str) -> list[str]:
        """Remove empty directories beneath (and including) root; return what went."""

    @abstractmethod
    def remove_dir(self, path: str) -> bool:
        """Remove a single directory if it is empty; True when it was removed."""

    def prune_empty_parents(self, path: str, stop_at: str) -> list[str]:
        """Walk up from `path` removing empty directories, never past `stop_at`.

        Consolidating a folder off a disk leaves a trail of empty parents
        (`.../tv shows/Cowboy Bebop`, then `.../tv shows`); this clears them.
        """
        stop = posixpath.normpath(stop_at)
        current = posixpath.normpath(path)
        removed: list[str] = []
        while current != stop and current.startswith(stop + "/"):
            assert_safe_path(current)
            if not self.remove_dir(current):
                break
            removed.append(current)
            current = posixpath.dirname(current)
        return removed

    # --- shared helpers ---------------------------------------------------

    def close(self) -> None:  # pragma: no cover - overridden where needed
        return None

    def sort_disks(self, disks: list[Disk]) -> list[Disk]:
        def key(d: Disk) -> tuple[int, str]:
            digits = re.findall(r"\d+", d.name)
            return (int(digits[0]) if digits else 1 << 30, d.name)

        return sorted(disks, key=key)
