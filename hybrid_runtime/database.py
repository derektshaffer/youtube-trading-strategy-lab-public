"""SQLite persistence for reconnect-safe local jobs, results, and cache metadata."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator


SCHEMA_VERSION = 1


class HybridDatabase:
    """Small connection-per-operation SQLite wrapper.

    WAL mode permits the desktop interface to read progress while a local worker
    updates a job. Connections are never shared across threads.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        began = False
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            began = True
            yield connection
            connection.execute("COMMIT")
            began = False
        except BaseException:
            if began:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        # sqlite3.executescript manages its own transaction boundary, so schema
        # creation deliberately does not run through transaction().
        connection = self.connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    requested_target TEXT NOT NULL,
                    resolved_target TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    route_json TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    progress REAL NOT NULL DEFAULT 0.0,
                    stage TEXT NOT NULL DEFAULT 'queued',
                    message TEXT NOT NULL DEFAULT '',
                    attempt INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    worker_id TEXT,
                    lease_expires_at TEXT,
                    heartbeat_at TEXT,
                    next_attempt_at TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    checkpoint_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS jobs_status_priority_idx
                    ON jobs(status, priority DESC, created_at ASC);
                CREATE INDEX IF NOT EXISTS jobs_dedupe_idx
                    ON jobs(dedupe_key, status);
                CREATE INDEX IF NOT EXISTS jobs_lease_idx
                    ON jobs(lease_expires_at)
                    WHERE lease_expires_at IS NOT NULL;

                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    state TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress REAL NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS job_events_job_idx
                    ON job_events(job_id, id ASC);

                CREATE TABLE IF NOT EXISTS results (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
                    result_json TEXT NOT NULL,
                    reproducibility_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS artifact_cache (
                    cache_key TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    coverage_start TEXT,
                    coverage_end TEXT,
                    expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS artifact_cache_kind_idx
                    ON artifact_cache(kind, updated_at DESC);
                """
            )
            row = connection.execute(
                "SELECT MAX(version) AS version FROM schema_migrations"
            ).fetchone()
            version = int(row["version"] or 0) if row else 0
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Hybrid database schema {version} is newer than supported {SCHEMA_VERSION}."
                )
            if version < SCHEMA_VERSION:
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) "
                    "VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                    (SCHEMA_VERSION,),
                )
        finally:
            connection.close()

    def schema_version(self) -> int:
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT MAX(version) AS version FROM schema_migrations"
            ).fetchone()
        finally:
            connection.close()
        return int(row["version"] or 0) if row else 0
