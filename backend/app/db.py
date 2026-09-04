"""SQLite persistence for settings, scans and the job queue.

Scans are persisted so a restart does not throw away a plan you were mid-way
through building. The in-memory index is rebuilt lazily from `scan_files`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from .config import settings

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scans (
    id                TEXT PRIMARY KEY,
    root              TEXT NOT NULL,
    created_at        REAL NOT NULL,
    disks             TEXT NOT NULL,
    total_files       INTEGER NOT NULL DEFAULT 0,
    distinct_files    INTEGER NOT NULL DEFAULT 0,
    total_bytes       INTEGER NOT NULL DEFAULT 0,
    dup_files         INTEGER NOT NULL DEFAULT 0,
    dup_wasted_bytes  INTEGER NOT NULL DEFAULT 0,
    duration_seconds  REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scan_files (
    scan_id  TEXT NOT NULL,
    relpath  TEXT NOT NULL,
    disk     TEXT NOT NULL,
    size     INTEGER NOT NULL,
    mtime    REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_scan_files_scan ON scan_files (scan_id);

CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT PRIMARY KEY,
    position   INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    payload    TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            yield cur
            conn.commit()
        finally:
            conn.close()


def init_db() -> None:
    with cursor() as cur:
        cur.executescript(SCHEMA)
        # Columns added after the first release; a plain ALTER keeps existing
        # development databases usable without a migration framework.
        existing = {row["name"] for row in cur.execute("PRAGMA table_info(scans)")}
        for column, ddl in (("distinct_files", "INTEGER NOT NULL DEFAULT 0"),):
            if column not in existing:
                cur.execute(f"ALTER TABLE scans ADD COLUMN {column} {ddl}")


# --- settings -------------------------------------------------------------


def get_setting(key: str) -> Any | None:
    with cursor() as cur:
        row = cur.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def set_setting(key: str, value: Any) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


# --- scans ----------------------------------------------------------------


def save_scan(
    scan_id: str,
    root: str,
    created_at: float,
    disks: list[dict],
    totals: dict[str, int],
    duration_seconds: float,
    rows: Iterable[tuple[str, str, str, int, float]],
) -> None:
    with cursor() as cur:
        cur.execute("DELETE FROM scan_files WHERE scan_id = ?", (scan_id,))
        cur.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
        cur.execute(
            "INSERT INTO scans (id, root, created_at, disks, total_files, distinct_files, "
            "total_bytes, dup_files, dup_wasted_bytes, duration_seconds) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                scan_id,
                root,
                created_at,
                json.dumps(disks),
                totals.get("total_files", 0),
                totals.get("distinct_files", 0),
                totals.get("total_bytes", 0),
                totals.get("dup_files", 0),
                totals.get("dup_wasted_bytes", 0),
                duration_seconds,
            ),
        )
        cur.executemany(
            "INSERT INTO scan_files (scan_id, relpath, disk, size, mtime) VALUES (?,?,?,?,?)",
            rows,
        )


def list_scans() -> list[sqlite3.Row]:
    with cursor() as cur:
        return cur.execute("SELECT * FROM scans ORDER BY created_at DESC").fetchall()


def get_scan(scan_id: str) -> sqlite3.Row | None:
    with cursor() as cur:
        return cur.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()


def get_scan_files(scan_id: str) -> list[sqlite3.Row]:
    with cursor() as cur:
        return cur.execute(
            "SELECT relpath, disk, size, mtime FROM scan_files WHERE scan_id = ?", (scan_id,)
        ).fetchall()


def delete_scan(scan_id: str) -> None:
    with cursor() as cur:
        cur.execute("DELETE FROM scan_files WHERE scan_id = ?", (scan_id,))
        cur.execute("DELETE FROM scans WHERE id = ?", (scan_id,))


# --- jobs -----------------------------------------------------------------


def save_job(job_id: str, position: int, created_at: float, payload: dict) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO jobs (id, position, created_at, payload) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET position = excluded.position, payload = excluded.payload",
            (job_id, position, created_at, json.dumps(payload)),
        )


def load_jobs() -> list[dict]:
    with cursor() as cur:
        rows = cur.execute("SELECT payload FROM jobs ORDER BY position, created_at").fetchall()
    return [json.loads(r["payload"]) for r in rows]


def delete_job(job_id: str) -> None:
    with cursor() as cur:
        cur.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def clear_jobs() -> None:
    with cursor() as cur:
        cur.execute("DELETE FROM jobs")
