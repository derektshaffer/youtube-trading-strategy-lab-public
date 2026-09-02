"""Shared SQLite plumbing for the hybrid runtime stores."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from .contracts import (
    ExecutionTarget,
    JobRecord,
    JobStatus,
    canonical_json,
    utc_now_text,
)


SCHEMA_VERSION = 1


class HybridStoreError(RuntimeError):
    pass


class JobNotFound(HybridStoreError):
    pass


class InvalidJobTransition(HybridStoreError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    request_fingerprint TEXT NOT NULL,
    idempotency_key TEXT,
    job_type TEXT NOT NULL,
    requested_target TEXT NOT NULL,
    execution_target TEXT NOT NULL,
    route_reason TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    result_json TEXT,
    error_json TEXT,
    code_fingerprint TEXT NOT NULL DEFAULT '',
    data_fingerprint TEXT NOT NULL DEFAULT '',
    engine_version TEXT NOT NULL DEFAULT '',
    worker_id TEXT,
    attempt INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    claimed_at TEXT,
    heartbeat_at TEXT,
    completed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency_key_unique
ON jobs(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS jobs_claim_queue
ON jobs(execution_target, status, priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS jobs_fingerprint_status
ON jobs(request_fingerprint, status, created_at DESC);
CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    progress REAL NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS job_events_lookup ON job_events(job_id, id);
CREATE TABLE IF NOT EXISTS cache_entries (
    cache_key TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    byte_size INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS cache_entries_namespace
ON cache_entries(namespace, updated_at DESC);
"""


class StorageBase:
    path: Path

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def _transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _reader(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._transaction(immediate=True) as connection:
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_now_text()),
            )

    @staticmethod
    def _decode_json(value: str | None) -> dict[str, Any] | None:
        if value is None:
            return None
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else {"value": decoded}

    @classmethod
    def _record(cls, row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=str(row["id"]),
            request_fingerprint=str(row["request_fingerprint"]),
            job_type=str(row["job_type"]),
            requested_target=ExecutionTarget(str(row["requested_target"])),
            execution_target=ExecutionTarget(str(row["execution_target"])),
            route_reason=str(row["route_reason"]),
            status=JobStatus(str(row["status"])),
            stage=str(row["stage"]),
            progress=float(row["progress"]),
            priority=int(row["priority"]),
            payload=cls._decode_json(row["payload_json"]) or {},
            result=cls._decode_json(row["result_json"]),
            error=cls._decode_json(row["error_json"]),
            idempotency_key=row["idempotency_key"],
            code_fingerprint=str(row["code_fingerprint"] or ""),
            data_fingerprint=str(row["data_fingerprint"] or ""),
            engine_version=str(row["engine_version"] or ""),
            worker_id=row["worker_id"],
            attempt=int(row["attempt"]),
            cancel_requested=bool(row["cancel_requested"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            claimed_at=row["claimed_at"],
            heartbeat_at=row["heartbeat_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        status: JobStatus,
        stage: str,
        progress: float,
        message: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO job_events(job_id, status, stage, progress, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, status.value, stage, progress, str(message or ""), created_at),
        )

    @staticmethod
    def _encoded(value: dict[str, Any] | None, fallback: str | None) -> str | None:
        return canonical_json(value) if value is not None else fallback
