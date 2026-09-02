"""Fingerprint-aware cache metadata stored separately from large artifacts."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .contracts import canonical_json, utc_now_text
from .storage_base import HybridStoreError


class CacheStoreMixin:
    def upsert_cache_entry(
        self,
        *,
        cache_key: str,
        namespace: str,
        fingerprint: str,
        artifact_path: str,
        byte_size: int = 0,
        metadata: Mapping[str, Any] | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        key = str(cache_key or "").strip()
        if not key:
            raise ValueError("cache_key is required")
        now = utc_now_text()
        with self._transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO cache_entries(
                    cache_key, namespace, fingerprint, artifact_path, byte_size,
                    metadata_json, created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    namespace = excluded.namespace,
                    fingerprint = excluded.fingerprint,
                    artifact_path = excluded.artifact_path,
                    byte_size = excluded.byte_size,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at
                """,
                (
                    key,
                    str(namespace or "default"),
                    str(fingerprint or ""),
                    str(artifact_path or ""),
                    max(0, int(byte_size)),
                    canonical_json(dict(metadata or {})),
                    now,
                    now,
                    expires_at,
                ),
            )
        entry = self.get_cache_entry(key, include_expired=True)
        if entry is None:  # pragma: no cover
            raise HybridStoreError("Cache entry could not be reloaded")
        return entry

    def get_cache_entry(
        self,
        cache_key: str,
        *,
        include_expired: bool = False,
    ) -> dict[str, Any] | None:
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM cache_entries WHERE cache_key = ?",
                (str(cache_key),),
            ).fetchone()
        if row is None:
            return None
        entry = dict(row)
        entry["metadata"] = json.loads(str(entry.pop("metadata_json") or "{}"))
        expires = str(entry.get("expires_at") or "")
        if expires and not include_expired and expires <= utc_now_text():
            return None
        return entry

    def delete_expired_cache_entries(self) -> int:
        with self._transaction(immediate=True) as connection:
            cursor = connection.execute(
                "DELETE FROM cache_entries WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (utc_now_text(),),
            )
            return max(0, int(cursor.rowcount or 0))
