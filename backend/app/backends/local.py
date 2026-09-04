"""Local filesystem backend, used when running in Docker on the Unraid host."""

from __future__ import annotations

import os
import posixpath
import shutil
from collections.abc import Iterator

from ..models import Disk, FileEntry
from .base import CopyProgress, StorageBackend, StorageError, assert_safe_path

COPY_CHUNK = 4 * 1024 * 1024

#: Hole-aware seeking is Linux-only, which is where Unraid lives. Without it the
#: copy falls back to reading every byte, which is correct but slower on sparse
#: sources.
_SPARSE_SEEK = hasattr(os, "SEEK_DATA") and hasattr(os, "SEEK_HOLE")


class LocalBackend(StorageBackend):
    def __init__(
        self,
        mount_root: str = "/mnt",
        disk_pattern: str = r"^disk\d+$",
        simulated_capacity: int = 0,
    ) -> None:
        """`simulated_capacity` makes each disk report a fixed total size with
        usage derived from apparent file sizes rather than from `statvfs`.

        That is what lets a sparse fixture tree behave like a real array of
        separate disks, for testing and for demoing the UI. Leave it at 0 for
        real hardware.
        """
        super().__init__(mount_root=mount_root, disk_pattern=disk_pattern)
        self.simulated_capacity = simulated_capacity

    def check(self) -> str:
        if not os.path.isdir(self.mount_root):
            raise StorageError(f"mount root {self.mount_root!r} does not exist")
        disks = self.list_disks()
        if not disks:
            raise StorageError(
                f"no data disks found under {self.mount_root!r} "
                "(expected directories named disk1, disk2, ...)"
            )
        return f"local filesystem, {len(disks)} disk(s) under {self.mount_root}"

    def list_disks(self) -> list[Disk]:
        disks: list[Disk] = []
        try:
            names = os.listdir(self.mount_root)
        except OSError as exc:
            raise StorageError(f"cannot read {self.mount_root}: {exc}") from exc

        for name in names:
            if not self.is_data_disk(name):
                continue
            path = self.disk_path(name)
            if not os.path.isdir(path):
                continue
            if self.simulated_capacity:
                total = self.simulated_capacity
                used = self._apparent_used(path)
                free = max(total - used, 0)
            else:
                try:
                    st = os.statvfs(path)
                    total = st.f_blocks * st.f_frsize
                    free = st.f_bavail * st.f_frsize
                    used = (st.f_blocks - st.f_bfree) * st.f_frsize
                except OSError:
                    total = free = used = 0
            disks.append(Disk(name=name, path=path, total=total, used=used, free=free))
        return self.sort_disks(disks)

    @staticmethod
    def _apparent_used(path: str) -> int:
        total = 0
        stack = [path]
        while stack:
            try:
                entries = list(os.scandir(stack.pop()))
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
        return total

    def walk(self, disk: str, subpath: str) -> Iterator[tuple[str, FileEntry]]:
        root = self.full_path(disk, subpath)
        if not os.path.isdir(root):
            return
        prefix_len = len(root.rstrip("/")) + 1
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                entries = list(os.scandir(current))
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        st = entry.stat(follow_symlinks=False)
                        yield entry.path[prefix_len:], FileEntry(size=st.st_size, mtime=st.st_mtime)
                except OSError:
                    continue

    def list_dir(self, path: str) -> list[tuple[str, bool]]:
        try:
            return [(e.name, e.is_dir(follow_symlinks=False)) for e in os.scandir(path)]
        except OSError:
            return []

    def exists(self, path: str) -> bool:
        return os.path.lexists(path)

    def size_of(self, path: str) -> int | None:
        try:
            return os.stat(path, follow_symlinks=False).st_size
        except OSError:
            return None

    def makedirs(self, path: str) -> None:
        assert_safe_path(path)
        os.makedirs(path, exist_ok=True)

    def copy_file(self, src: str, dst: str, size: int, progress: CopyProgress | None = None) -> int:
        assert_safe_path(src)
        assert_safe_path(dst)
        os.makedirs(posixpath.dirname(dst), exist_ok=True)
        # Copy to a temporary name so an interrupted run never leaves a partial
        # file that looks complete to the next scan.
        tmp = f"{dst}.shuffler-partial"
        try:
            with open(src, "rb") as fsrc, open(tmp, "wb") as fdst:
                if _SPARSE_SEEK:
                    self._copy_extents(fsrc, fdst, size, progress)
                else:
                    self._copy_sequential(fsrc, fdst, size, progress)
                fdst.flush()
                os.fsync(fdst.fileno())
            shutil.copystat(src, tmp)
            os.replace(tmp, dst)
        except OSError as exc:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            raise StorageError(f"copy failed for {src} -> {dst}: {exc}") from exc
        return os.stat(dst).st_size

    @staticmethod
    def _copy_extents(fsrc, fdst, size: int, progress: CopyProgress | None) -> None:
        """Copy only the allocated regions, leaving holes as holes.

        Asking the filesystem where the data is turns a copy of a mostly-sparse
        file into a near-instant operation instead of reading gigabytes of
        zeros. Ordinary media files have a single extent, so this costs nothing.
        """
        fd = fsrc.fileno()
        position = 0
        while position < size:
            try:
                data_start = os.lseek(fd, position, os.SEEK_DATA)
            except OSError:
                # ENXIO: everything from here to the end is a hole.
                break
            try:
                data_end = os.lseek(fd, data_start, os.SEEK_HOLE)
            except OSError:
                data_end = size
            data_end = min(data_end, size)

            fsrc.seek(data_start)
            fdst.seek(data_start)
            remaining = data_end - data_start
            while remaining > 0:
                chunk = fsrc.read(min(COPY_CHUNK, remaining))
                if not chunk:
                    break
                fdst.write(chunk)
                remaining -= len(chunk)
                if progress:
                    progress(min(fdst.tell(), size), size)
            position = data_end
        # Holes at the tail are not created by seeking past the end.
        fdst.truncate(size)
        if progress:
            progress(size, size)

    @staticmethod
    def _copy_sequential(fsrc, fdst, size: int, progress: CopyProgress | None) -> None:
        copied = 0
        while True:
            chunk = fsrc.read(COPY_CHUNK)
            if not chunk:
                break
            fdst.write(chunk)
            copied += len(chunk)
            if progress:
                progress(copied, size)
        fdst.truncate(copied)

    def delete_file(self, path: str) -> None:
        assert_safe_path(path)
        try:
            os.remove(path)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise StorageError(f"delete failed for {path}: {exc}") from exc

    def prune_empty_dirs(self, root: str) -> list[str]:
        assert_safe_path(root)
        removed: list[str] = []
        if not os.path.isdir(root):
            return removed
        for current, dirnames, filenames in os.walk(root, topdown=False):
            if filenames or dirnames:
                # os.walk caches dirnames from before children were removed, so
                # re-check the directory as it stands now.
                if os.listdir(current):
                    continue
            try:
                os.rmdir(current)
                removed.append(current)
            except OSError:
                continue
        return removed

    def remove_dir(self, path: str) -> bool:
        assert_safe_path(path)
        try:
            os.rmdir(path)
            return True
        except OSError:
            return False
