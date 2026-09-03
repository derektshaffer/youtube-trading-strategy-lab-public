"""Durable, secret-free links between local desktop jobs and cloud queue jobs."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping

from .contracts import canonical_json, normalized_progress, utc_now_text


class CloudLinkStoreError(RuntimeError):
    """Raised when cloud-link metadata cannot be persisted safely."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cloud_job_links (
    local_job_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    remote_job_id TEXT,
    remote_dedupe_key TEXT,
    repository TEXT NOT NULL,
    branch TEXT NOT NULL,
    path TEXT NOT NULL,
    remote_status TEXT NOT NULL DEFAULT '',
    remote_stage TEXT NOT NULL DEFAULT '',
    remote_progress REAL NOT NULL DEFAULT 0,
    last_remote_revision TEXT NOT NULL DEFAULT '',
    dispatch_attempted_at TEXT,
    dispatch_error TEXT NOT NULL DEFAULT '',
    last_sync_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cloud_job_links_remote_lookup
ON cloud_job_links(repository, branch, path, remote_job_id);
CREATE INDEX IF NOT EXISTS cloud_job_links_dedupe_lookup
ON cloud_job_links(repository, branch, path, remote_dedupe_key);
"""


class CloudLinkStore:
    """Keep cloud identifiers beside, but never inside, the main job database.

    The table deliberately contains no bearer token, API key, or credential
    account value. Losing this database only loses the local-to-remote shortcut;
    the remote research queue remains authoritative and can be reattached by its
    dedupe key.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
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

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        try:
            metadata = json.loads(str(result.pop("metadata_json") or "{}"))
        except json.JSONDecodeError:
            metadata = {}
        result["metadata"] = metadata if isinstance(metadata, dict) else {}
        result["remote_progress"] = normalized_progress(
            result.get("remote_progress")
        )
        return result

    def get(self, local_job_id: str) -> dict[str, Any] | None:
        clean = str(local_job_id or "").strip()
        if not clean:
            raise ValueError("local_job_id is required")
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM cloud_job_links WHERE local_job_id = ?",
                (clean,),
            ).fetchone()
        return self._decode(row)

    def upsert(
        self,
        *,
        local_job_id: str,
        provider: str = "github_research_queue",
        remote_job_id: str = "",
        remote_dedupe_key: str = "",
        repository: str,
        branch: str,
        path: str,
        remote_status: str = "",
        remote_stage: str = "",
        remote_progress: float = 0.0,
        last_remote_revision: str = "",
        dispatch_attempted_at: str | None = None,
        dispatch_error: str = "",
        last_sync_at: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_job = str(local_job_id or "").strip()
        clean_repository = str(repository or "").strip()
        clean_branch = str(branch or "main").strip() or "main"
        clean_path = str(path or "").strip().strip("/")
        if not clean_job:
            raise ValueError("local_job_id is required")
        if not clean_repository or "/" not in clean_repository:
            raise ValueError("repository must use owner/name form")
        if not clean_path:
            raise ValueError("path is required")
        now = utc_now_text()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO cloud_job_links(
                    local_job_id, provider, remote_job_id, remote_dedupe_key,
                    repository, branch, path, remote_status, remote_stage,
                    remote_progress, last_remote_revision,
                    dispatch_attempted_at, dispatch_error, last_sync_at,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(local_job_id) DO UPDATE SET
                    provider = excluded.provider,
                    remote_job_id = CASE
                        WHEN excluded.remote_job_id <> '' THEN excluded.remote_job_id
                        ELSE cloud_job_links.remote_job_id
                    END,
                    remote_dedupe_key = CASE
                        WHEN excluded.remote_dedupe_key <> '' THEN excluded.remote_dedupe_key
                        ELSE cloud_job_links.remote_dedupe_key
                    END,
                    repository = excluded.repository,
                    branch = excluded.branch,
                    path = excluded.path,
                    remote_status = excluded.remote_status,
                    remote_stage = excluded.remote_stage,
                    remote_progress = excluded.remote_progress,
                    last_remote_revision = excluded.last_remote_revision,
                    dispatch_attempted_at = COALESCE(
                        excluded.dispatch_attempted_at,
                        cloud_job_links.dispatch_attempted_at
                    ),
                    dispatch_error = excluded.dispatch_error,
                    last_sync_at = COALESCE(
                        excluded.last_sync_at,
                        cloud_job_links.last_sync_at
                    ),
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_job,
                    str(provider or "github_research_queue").strip(),
                    str(remote_job_id or "").strip(),
                    str(remote_dedupe_key or "").strip(),
                    clean_repository,
                    clean_branch,
                    clean_path,
                    str(remote_status or "").strip(),
                    str(remote_stage or "").strip(),
                    normalized_progress(remote_progress),
                    str(last_remote_revision or "").strip(),
                    dispatch_attempted_at,
                    str(dispatch_error or "")[:2_000],
                    last_sync_at,
                    canonical_json(dict(metadata or {})),
                    now,
                    now,
                ),
            )
        result = self.get(clean_job)
        if result is None:  # pragma: no cover - defensive database guard
            raise CloudLinkStoreError("Cloud link could not be reloaded")
        return result

    def record_error(
        self,
        local_job_id: str,
        message: str,
        *,
        repository: str,
        branch: str,
        path: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous = self.get(local_job_id) or {}
        return self.upsert(
            local_job_id=local_job_id,
            repository=repository,
            branch=branch,
            path=path,
            dispatch_error=" ".join(str(message or "").split())[:2_000],
            last_sync_at=utc_now_text(),
            # Keep dispatch evidence through an unrelated connection failure.
            metadata={**(previous.get("metadata") or {}), **dict(metadata or {})},
        )

    def delete(self, local_job_id: str) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM cloud_job_links WHERE local_job_id = ?",
                (str(local_job_id or "").strip(),),
            )
            return bool(cursor.rowcount)
