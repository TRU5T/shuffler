"""Shared fixtures.

Every test runs against a synthetic multi-disk tree of sparse files, so the
apparent sizes are realistic (tens of GB) while the real cost on disk is a
couple of hundred kilobytes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from make_fixtures import GB, build as build_fixtures  # noqa: E402

from app.backends.local import LocalBackend  # noqa: E402
from app.planner import Planner  # noqa: E402
from app.scan import ScanIndex, run_scan  # noqa: E402

MEDIA_ROOT = "data/media"
CAPACITY = 60 * GB


@pytest.fixture
def mount(tmp_path: Path) -> Path:
    build_fixtures(tmp_path, reset=True)
    return tmp_path / "mnt"


@pytest.fixture
def backend(mount: Path) -> LocalBackend:
    return LocalBackend(mount_root=str(mount), simulated_capacity=CAPACITY)


@pytest.fixture
def index(backend: LocalBackend) -> ScanIndex:
    return run_scan(backend, MEDIA_ROOT)


@pytest.fixture
def path_for(backend: LocalBackend, index: ScanIndex):
    def build(disk: str, relpath: str) -> str:
        parts = [p for p in (index.root, relpath) if p]
        return backend.full_path(disk, "/".join(parts))

    return build


@pytest.fixture
def prune_stop_for(backend: LocalBackend, index: ScanIndex):
    def build(disk: str, _relpath: str = "") -> str:
        return backend.full_path(disk, index.root)

    return build


@pytest.fixture
def planner() -> Planner:
    return Planner(reserve_bytes=0)


@pytest.fixture
def client(mount: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A TestClient wired to the fixture disks and a throwaway database.

    The app holds one Service singleton, so it is reconfigured for the test and
    reset afterwards rather than rebuilt.
    """
    from fastapi.testclient import TestClient

    from app import db
    from app.config import settings as app_settings
    from app.main import app
    from app.service import service

    monkeypatch.setattr(app_settings, "db_path", str(tmp_path / "api-test.db"))
    monkeypatch.setattr(app_settings, "simulated_disk_capacity", CAPACITY)
    db.init_db()

    def reset() -> None:
        service.planner.jobs = []
        service.planner.reserve_bytes = 0
        service.index = None
        service._backend = None
        service.executor.history.clear()

    reset()
    service.connection.storage_backend = "local"
    service.connection.mount_root = str(mount)
    service.connection.dry_run = True
    service.connection.reserve_bytes = 0

    with TestClient(app) as test_client:
        yield test_client

    reset()
