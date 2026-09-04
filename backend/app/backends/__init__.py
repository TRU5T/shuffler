"""Storage backends.

`LocalBackend` is what ships in the Docker image running on Unraid, where the
data disks are bind-mounted into the container. `SSHBackend` is used for
development from a separate machine. Everything above this layer is written
against the `StorageBackend` interface only.
"""

from __future__ import annotations

from .base import CopyProgress, StorageBackend, StorageError
from .local import LocalBackend
from .ssh import SSHBackend

__all__ = [
    "CopyProgress",
    "LocalBackend",
    "SSHBackend",
    "StorageBackend",
    "StorageError",
    "build_backend",
]


def build_backend(kind: str, **kwargs) -> StorageBackend:
    if kind == "local":
        return LocalBackend(
            mount_root=kwargs.get("mount_root", "/mnt"),
            disk_pattern=kwargs.get("disk_pattern", r"^disk\d+$"),
            simulated_capacity=kwargs.get("simulated_capacity", 0),
        )
    if kind == "ssh":
        return SSHBackend(
            host=kwargs.get("host", ""),
            port=kwargs.get("port", 22),
            user=kwargs.get("user", "root"),
            password=kwargs.get("password", ""),
            key_path=kwargs.get("key_path", ""),
            timeout=kwargs.get("timeout", 20),
            mount_root=kwargs.get("mount_root", "/mnt"),
            disk_pattern=kwargs.get("disk_pattern", r"^disk\d+$"),
        )
    raise StorageError(f"unknown storage backend: {kind!r}")
