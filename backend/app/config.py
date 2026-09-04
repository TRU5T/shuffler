"""Application configuration.

Environment variables provide the defaults; the SSH connection details can be
overridden at runtime from the UI and are persisted in SQLite (see `db.py`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

BackendKind = Literal["local", "ssh"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHUFFLER_", env_file=".env", extra="ignore")

    storage_backend: BackendKind = "ssh"

    # When true, the executor logs every operation but never touches the filesystem.
    dry_run: bool = True

    # Root that disks live under, and the pattern that identifies a data disk.
    # `/mnt/user` and `/mnt/disks` are deliberately never matched.
    mount_root: str = "/mnt"
    disk_pattern: str = r"^disk\d+$"

    ssh_host: str = ""
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_password: str = ""
    ssh_key_path: str = ""
    ssh_timeout: int = 20

    # Bytes to keep free on a target disk; a queued job that would breach this
    # is flagged as an overflow risk.
    reserve_bytes: int = 10 * 1024**3

    # Testing/demo aid: when non-zero the local backend reports this as each
    # disk's capacity and derives usage from apparent file sizes, so a sparse
    # fixture tree behaves like a real array. Must stay 0 on real hardware.
    simulated_disk_capacity: int = 0

    db_path: str = str(REPO_ROOT / "data" / "shuffler.db")
    host: str = "0.0.0.0"
    port: int = 8756

    # Extra origins allowed through CORS on top of the Vite dev server.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def static_dir(self) -> Path:
        return REPO_ROOT / "frontend" / "dist"


settings = Settings()

os.makedirs(Path(settings.db_path).parent, exist_ok=True)
