"""Verified local cache for candles, features, and recent analysis artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .contracts import canonical_json, stable_fingerprint, utc_now_text
from .database import HybridDatabase


def _utc(value: datetime | str | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        current = value
    else:
        current = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


class ArtifactCache:
    """Compressed file payloads with a small SQLite index and integrity checks."""

    def __init__(self, database: HybridDatabase, directory: str | Path):
        self.database = database
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def put_json(
        self,
        cache_key: str,
        kind: str,
        payload: Any,
        *,
        metadata: Mapping[str, Any] | None = None,
        coverage_start: str | None = None,
        coverage_end: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        key = str(cache_key or "").strip()
        artifact_kind = str(kind or "").strip()
        if not key or not artifact_kind:
            raise ValueError("cache_key and kind are required.")
        encoded = canonical_json(payload).encode("utf-8")
        fingerprint = stable_fingerprint(payload)
        filename = f"{fingerprint[:32]}.json.gz"
        path = self.directory / filename
        temporary = self.directory / f".{filename}.{os.getpid()}.tmp"
        temporary.write_bytes(gzip.compress(encoded, compresslevel=5))
        os.replace(temporary, path)
        timestamp = utc_now_text()
        metadata_json = canonical_json(dict(metadata or {}))
        previous_path = ""
        with self.database.transaction(immediate=True) as connection:
            previous = connection.execute(
                "SELECT file_path FROM artifact_cache WHERE cache_key = ?", (key,)
            ).fetchone()
            previous_path = str(previous["file_path"] or "") if previous else ""
            connection.execute(
                """
                INSERT INTO artifact_cache(
                    cache_key, kind, file_path, content_fingerprint, metadata_json,
                    coverage_start, coverage_end, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    kind = excluded.kind,
                    file_path = excluded.file_path,
                    content_fingerprint = excluded.content_fingerprint,
                    metadata_json = excluded.metadata_json,
                    coverage_start = excluded.coverage_start,
                    coverage_end = excluded.coverage_end,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    artifact_kind,
                    str(path),
                    fingerprint,
                    metadata_json,
                    coverage_start,
                    coverage_end,
                    expires_at,
                    timestamp,
                    timestamp,
                ),
            )
        if previous_path and previous_path != str(path):
            try:
                Path(previous_path).unlink()
            except (FileNotFoundError, OSError):
                pass
        return {
            "cache_key": key,
            "kind": artifact_kind,
            "content_fingerprint": fingerprint,
            "file_path": str(path),
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
            "expires_at": expires_at,
            "updated_at": timestamp,
        }

    def get_json(
        self,
        cache_key: str,
        *,
        now: datetime | str | None = None,
        expected_fingerprint: str | None = None,
    ) -> dict[str, Any] | None:
        key = str(cache_key or "").strip()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifact_cache WHERE cache_key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        expires_at = str(row["expires_at"] or "").strip()
        if expires_at and _utc(expires_at) <= _utc(now):
            return None
        path = Path(str(row["file_path"] or ""))
        try:
            payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        except (OSError, EOFError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            return None
        actual = stable_fingerprint(payload)
        stored = str(row["content_fingerprint"] or "")
        if actual != stored:
            return None
        if expected_fingerprint and actual != str(expected_fingerprint):
            return None
        return {
            "cache_key": key,
            "kind": str(row["kind"]),
            "payload": payload,
            "metadata": json.loads(str(row["metadata_json"] or "{}")),
            "content_fingerprint": actual,
            "coverage_start": row["coverage_start"],
            "coverage_end": row["coverage_end"],
            "expires_at": row["expires_at"],
            "updated_at": row["updated_at"],
        }

    def invalidate(self, cache_key: str) -> bool:
        key = str(cache_key or "").strip()
        file_path = ""
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT file_path FROM artifact_cache WHERE cache_key = ?", (key,)
            ).fetchone()
            if row is None:
                return False
            file_path = str(row["file_path"] or "")
            connection.execute("DELETE FROM artifact_cache WHERE cache_key = ?", (key,))
        if file_path:
            try:
                Path(file_path).unlink()
            except (FileNotFoundError, OSError):
                pass
        return True
