"""Small, attempt-bound progress sidecars; never a source of result eligibility."""
from __future__ import annotations

from hashlib import sha256
from pathlib import PurePosixPath
from datetime import datetime
import math
from typing import Any
from strategy_lab_telemetry import checkpoint_operation

PROGRESS_FORMAT = "strategy-lab-progress-v1"


def progress_path(checkpoint_path: str, run_id: str) -> str:
    if not str(run_id).strip():
        raise ValueError("An exact Strategy Lab run is required for progress storage.")
    path = PurePosixPath(checkpoint_path)
    return str(path.parent / (path.stem + "-progress") / (sha256(run_id.encode()).hexdigest() + ".json"))


def _timestamp(value: Any) -> float:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def merge_progress(checkpoint: dict, document: dict) -> dict:
    """Overlay only display fields from the same attempt, never terminal evidence."""
    result = dict(checkpoint)
    if result.get("progress_storage") != PROGRESS_FORMAT or result.get("status") != "running":
        return result
    records = document.get("validation_runs") if isinstance(document, dict) else None
    if not isinstance(records, list):
        return result
    matches = [r for r in records if isinstance(r, dict) and r.get("id") == result.get("id")]
    if len(matches) != 1:
        return result
    row = matches[0]
    if row.get("record_type") != PROGRESS_FORMAT or row.get("status") != "running":
        return result
    if any(row.get(k) != result.get(k) for k in ("id", "ticker", "attempt", "started_at")):
        return result
    if not result.get("started_at") or not result.get("attempt"):
        return result
    try:
        fraction = float(row.get("progress"))
        base_fraction = float(result.get("progress") or 0)
    except (ValueError, TypeError, OverflowError):
        return result
    if not math.isfinite(fraction) or not math.isfinite(base_fraction) or not 0 <= fraction < 1:
        return result
    if _timestamp(row.get("saved_at")) <= _timestamp(result.get("saved_at")):
        return result
    if fraction < base_fraction:
        return result
    for key in ("progress", "stage", "message", "saved_at"):
        result[key] = row.get(key)
    return result


def progress_store(checkpoint_store: Any, run_id: str):
    """Bind a tiny StrategyStore to the same repository/branch and local directory."""
    from youtube_strategy_engine import StrategyStore, GitHubCloudBackup
    # Preserve compatibility with external/in-memory store implementations.
    if not isinstance(checkpoint_store, StrategyStore):
        return None
    stores = getattr(checkpoint_store, "_progress_stores", None)
    if stores is None:
        stores = {}
        checkpoint_store._progress_stores = stores
    if run_id not in stores:
        cloud = checkpoint_store.cloud_backup
        sidecar_cloud = None if cloud is None else GitHubCloudBackup(
            cloud.repository, cloud.token, branch=cloud.branch,
            path=progress_path(cloud.path, run_id),
        )
        stores[run_id] = StrategyStore(
            directory=checkpoint_store.directory / "progress" / sha256(run_id.encode()).hexdigest(),
            cloud_backup=sidecar_cloud,
        )
    metric = getattr(checkpoint_store, "_checkpoint_telemetry", None)
    if metric is not None:
        stores[run_id]._checkpoint_telemetry = metric
        if stores[run_id].cloud_backup is not None:
            stores[run_id].cloud_backup._checkpoint_telemetry = metric
    return stores[run_id]


@checkpoint_operation("heartbeat")
def save_progress(store: Any, *, run_id: str, ticker: str, attempt: int,
                  started_at: str, fraction: float, stage: str, message: str) -> None:
    from youtube_strategy_engine import utc_now
    # A single-run document contains no job spec, optimizer history or result.
    data = store.load_latest()
    data["validation_runs"] = [{
        "id": run_id, "record_type": PROGRESS_FORMAT, "status": "running",
        "ticker": ticker, "attempt": attempt, "started_at": started_at,
        "saved_at": utc_now().isoformat(), "progress": min(.999, max(0.0, fraction)),
        "stage": str(stage)[:100], "message": str(message)[:500],
    }]
    store.save(data)


def read_progress(checkpoint_store: Any, checkpoint: dict, *, reconcile_cloud: bool = True) -> dict:
    if checkpoint.get("progress_storage") != PROGRESS_FORMAT or checkpoint.get("status") != "running":
        return checkpoint
    try:
        store = progress_store(checkpoint_store, str(checkpoint.get("id") or ""))
        if store is None:
            return checkpoint
        if reconcile_cloud and store.cloud_backup is not None:
            remote = store.cloud_backup.read_library()
            data = (remote or {}).get("library") or {}
        else:
            data = store.load() if store.path.exists() else {}
        return merge_progress(checkpoint, data)
    except Exception:
        # Optional display progress cannot change the durable state or result.
        return checkpoint
