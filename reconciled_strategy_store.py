"""Opt-in worker persistence with a durable base, immutable outbox and CAS retries.

A worker directory belongs to one process. Existing desktop stores keep their
fail-closed behavior; this store can reconcile their independent remote writes.
"""
from copy import deepcopy
import gzip
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
import time

from youtube_strategy_engine import AppError, CloudBackupConflict, GitHubCloudBackup, StrategyStore, isoformat_utc, utc_now
from cloud_backup_reconciliation import canonical, reconcile, ReconciliationConflict, COLLECTIONS


class PersistencePending(AppError):
    """Completed local work needs synchronization, never computation retry."""


def atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_bundle(path):
    try:
        bundle = json.loads(gzip.decompress(Path(path).read_bytes()))
        payload = bundle["payload"]
        if bundle["sha256"] != hashlib.sha256(canonical(payload)).hexdigest():
            raise ValueError("checksum mismatch")
        if payload["schema"] != 1:
            raise ValueError("unsupported schema")
        return bundle
    except (OSError, EOFError, ValueError, KeyError, TypeError) as exc:
        raise PersistencePending("Recovery bundle is damaged or unsupported; no cloud write attempted.") from exc


def checked_library(data):
    if not isinstance(data, dict) or not isinstance(data.get("strategies"), list):
        raise ReconciliationConflict("Invalid cloud library structure.")
    for name in COLLECTIONS:
        if name in data and not isinstance(data[name], list):
            raise ReconciliationConflict(f"Invalid cloud collection: {name}")
    if "research_system" in data and not isinstance(data["research_system"], dict):
        raise ReconciliationConflict("Invalid cloud research_system object.")
    canonical(data)
    return StrategyStore.normalize_library(data)


class ReconciledStrategyStore(StrategyStore):
    def __init__(self, *args, retain=None, retry_attempts=4, sleeper=time.sleep, **kwargs):
        super().__init__(*args, **kwargs)
        self.base_path = self.directory / "cloud_base.json.gz"
        self.pending_path = self.directory / "cloud_pending.json.gz"
        self.recovery_directory = self.directory / "cloud_recovery"
        self.retain = retain or retain_actions_artifact
        self.retry_attempts = max(1, min(8, int(retry_attempts)))
        self.sleeper = sleeper

    def destination(self):
        cloud = self.cloud_backup
        return {key: getattr(cloud, key, "") for key in ("repository", "branch", "path")}

    def _base(self):
        try:
            base = json.loads(gzip.decompress(self.base_path.read_bytes()))
            if base["destination"] != self.destination():
                raise ValueError("different destination")
            return base
        except (OSError, EOFError, ValueError, KeyError, TypeError) as exc:
            raise PersistencePending("No verified cloud base for this destination; retain local state and inspect it.") from exc

    def _remember_base(self, library, sha):
        atomic(self.base_path, gzip.compress(canonical({
            "destination": self.destination(), "library": library, "sha": sha,
        }), mtime=0))

    def load_latest(self):
        if self.pending_path.exists():
            # Recovery must finish before new work is claimed or local state refreshed.
            return self.sync_cloud_backup()
        local = self.load()
        if self.cloud_backup is None:
            return local
        remote = self.cloud_backup.read_library()
        library = checked_library(remote["library"]) if remote else self.blank()
        sha = remote["sha"] if remote else ""
        if self.base_path.exists():
            base = self._base()
            if local != base["library"]:
                raise PersistencePending("Unjournaled local changes exist; inspect before refreshing cloud state.")
        elif self._library_has_user_data(local) and local != library:
            raise PersistencePending("Existing local records have no verified common cloud base.")
        self._write_local(library)
        self._remember_base(library, sha)
        self._record_cloud_success(library)
        return deepcopy(library)

    def save(self, data):
        if self.cloud_backup is None:
            return super().save(data)
        if self.pending_path.exists():
            raise PersistencePending("A durable cloud delta is pending; recover it before creating another save.")
        base = self._base()
        if ({k: v for k, v in data.items() if k != "updated_at"}
                == {k: v for k, v in base["library"].items() if k != "updated_at"}):
            # A caller retrying the same completed save must not create another
            # timestamp, artifact, or private acknowledgement receipt.
            return deepcopy(base["library"])
        local = deepcopy(data)
        local["updated_at"] = isoformat_utc(utc_now())
        payload = {"schema": 1, "destination": self.destination(),
                   "base": base["library"], "base_sha": base["sha"], "local": local,
                   "source": {key: os.environ.get(key, "") for key in
                              ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA")}}
        digest = hashlib.sha256(canonical(payload)).hexdigest()
        raw = gzip.compress(canonical({"payload": payload, "sha256": digest}), mtime=0)
        # The bundle is the write-ahead record. No network read/write can precede it.
        try:
            atomic(self.pending_path, raw)
            atomic(self.recovery_directory / f"{digest}.json.gz", raw)
            self._write_local(local)
        except OSError as exc:
            if self.pending_path.exists():
                raise PersistencePending("Completed delta is journaled, but local library installation failed; recover the pending bundle.") from exc
            raise PersistencePending("Local storage could not retain the completed delta; no cloud write or automatic computation retry was attempted.") from exc
        return self.sync_cloud_backup()

    def sync_cloud_backup(self):
        if not self.pending_path.exists():
            return self.load()
        bundle = read_bundle(self.pending_path)
        payload, digest = bundle["payload"], bundle["sha256"]
        if payload["destination"] != self.destination():
            raise PersistencePending("Recovery destination mismatch; no cloud write attempted.")
        try:
            # Confirm off-runner retention before the canonical cloud save.
            self.retain(self.pending_path, digest)
            for attempt in range(self.retry_attempts):
                try:
                    remote = self.cloud_backup.read_library()
                    latest = checked_library(remote["library"]) if remote else self.blank()
                    if remote is None and payload["base_sha"]:
                        raise ReconciliationConflict("Cloud library disappeared; refusing to recreate lost history.")
                    merged = reconcile(payload["base"], payload["local"], latest)
                    if merged != latest:
                        merged["updated_at"] = payload["local"]["updated_at"]
                        # If another writer used this timestamp, keep its collision guard.
                        saved = self.cloud_backup.save_library(
                            merged, previous_updated_at=latest.get("updated_at"),
                            expected_sha=remote["sha"] if remote else "",
                        )
                    else:
                        saved = remote or {"library": merged, "sha": ""}
                    # Verify the CAS response before acknowledging the outbox.
                    if saved["library"] != merged:
                        raise ReconciliationConflict("Cloud save response does not match reconciled library.")
                    if isinstance(self.cloud_backup, GitHubCloudBackup):
                        self.cloud_backup.acknowledge_recovery(digest, saved["sha"])
                    self._write_local(merged)
                    self._remember_base(merged, saved["sha"])
                    self._record_cloud_write_success(merged)
                    atomic(self.recovery_directory / f"{digest}.synced", canonical({"sha": saved["sha"]}))
                    self.pending_path.unlink()
                    return merged
                except CloudBackupConflict:
                    if attempt + 1 == self.retry_attempts:
                        raise
                    self.sleeper(min(8.0, 0.5 * 2 ** attempt) + random.uniform(0, 0.25))
        except (AppError, OSError, ValueError, subprocess.SubprocessError) as exc:
            self._record_cloud_status(last_error=str(exc))
            raise PersistencePending(
                f"Completed/local delta {digest} retained; cloud sync pending: {exc}"
            ) from exc

    def import_recovery(self, path):
        bundle = read_bundle(path)
        if bundle["payload"]["destination"] != self.destination():
            raise PersistencePending("Recovery destination mismatch; no cloud write attempted.")
        if self.pending_path.exists() and read_bundle(self.pending_path) != bundle:
            raise PersistencePending("Another recovery is pending in this directory.")
        atomic(self.pending_path, Path(path).read_bytes())
        return self.sync_cloud_backup()


def retain_actions_artifact(path, digest):
    """Only the Actions wrapper enables upload; never publish private plaintext."""
    if os.environ.get("RETAIN_CLOUD_RECOVERY") != "1":
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise PersistencePending("Actions recovery retention is not configured; cloud save blocked.")
        return
    script = Path(__file__).parent / ".github/recovery-artifact/index.cjs"
    result = subprocess.run(["node", str(script), "--upload", str(path.resolve()), digest],
                            capture_output=True, text=True, timeout=300, check=False)
    if result.returncode:
        # Upload diagnostics may contain signed URLs. Do not echo child stderr.
        raise PersistencePending("Encrypted recovery artifact upload failed; canonical sync was not attempted.")
