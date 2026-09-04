"""Connection settings and health checks."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..models import ConnectionSettings, Disk, HealthResult
from ..service import service

router = APIRouter()


class ConnectionPatch(BaseModel):
    storage_backend: str | None = None
    ssh_host: str | None = None
    ssh_port: int | None = None
    ssh_user: str | None = None
    ssh_key_path: str | None = None
    #: Empty string clears the stored password; omit the field to leave it alone.
    ssh_password: str | None = None
    mount_root: str | None = None
    dry_run: bool | None = None
    reserve_bytes: int | None = None


@router.get("/connection", response_model=ConnectionSettings)
def get_connection() -> ConnectionSettings:
    return service.public_connection()


@router.put("/connection", response_model=ConnectionSettings)
def put_connection(patch: ConnectionPatch) -> ConnectionSettings:
    return service.update_connection(patch.model_dump(exclude_unset=True))


@router.get("/health", response_model=HealthResult)
def health() -> HealthResult:
    return service.health()


@router.post("/connection/test", response_model=HealthResult)
def test_connection() -> HealthResult:
    return service.health()


@router.get("/disks", response_model=list[Disk])
def disks() -> list[Disk]:
    return service.refresh_disk_usage()
