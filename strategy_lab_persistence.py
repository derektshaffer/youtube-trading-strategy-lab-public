"""Durable checkpoint helpers for interactive Strategy Lab runs."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from youtube_strategy_engine import AppError, StrategyStore, utc_now


STRATEGY_LAB_RECORD_TYPE = "strategy_lab_checkpoint"
MAX_STRATEGY_LAB_CHECKPOINTS = 5
STRATEGY_LAB_CLOUD_CONFLICT_MARKER = (
    "Both the local Trading Lab library and the private GitHub library changed "
    "since their last shared version."
)


def _saved_time(record: dict[str, Any]) -> float:
    value = str(record.get("saved_at") or record.get("updated_at") or "").strip()
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (OverflowError, TypeError, ValueError):
        return 0.0


def _checkpoint_rank(record: dict[str, Any]) -> tuple[int, float, float, int]:
    """Prefer terminal evidence and never regress a resumable run's progress."""

    status = str(record.get("status") or "").strip().lower()
    has_result = isinstance(record.get("result"), dict) and bool(record.get("result"))
    if status == "complete" and has_result:
        lifecycle_rank = 3
    elif status == "failed":
        lifecycle_rank = 2
    elif status == "running":
        lifecycle_rank = 1
    else:
        lifecycle_rank = 0
    try:
        progress = max(0.0, min(1.0, float(record.get("progress") or 0.0)))
    except (OverflowError, TypeError, ValueError):
        progress = 0.0
    resumable_payload = int(bool(record.get("job"))) + int(
        bool(record.get("optimizer_state"))
    )
    return lifecycle_rank, progress, resumable_payload, _saved_time(record)


def merge_strategy_lab_checkpoint_libraries(
    local: dict[str, Any],
    remote: dict[str, Any],
) -> dict[str, Any]:
    """Merge bounded checkpoint histories without discarding either writer.

    A completed result always wins for the same run. Failed state wins over a
    stale running copy, and two running copies keep the furthest saved progress.
    Distinct run IDs are retained newest-first within the existing history cap.
    """

    merged = deepcopy(remote if isinstance(remote, dict) else {})
    records: dict[str, dict[str, Any]] = {}
    anonymous_index = 0
    for source in (remote, local):
        source_records = source.get("validation_runs") if isinstance(source, dict) else []
        for item in source_records or []:
            if not isinstance(item, dict):
                continue
            record = deepcopy(item)
            record_id = str(record.get("id") or "").strip()
            record_type = str(record.get("record_type") or "").strip()
            if record_id:
                key = f"{record_type}:{record_id}"
            else:
                try:
                    key = "anonymous:" + json.dumps(record, sort_keys=True, default=str)
                except (TypeError, ValueError):
                    anonymous_index += 1
                    key = f"anonymous:{anonymous_index}"
            previous = records.get(key)
            if previous is None or _checkpoint_rank(record) > _checkpoint_rank(previous):
                records[key] = record

    ordered = sorted(
        records.values(),
        key=lambda item: (_saved_time(item), _checkpoint_rank(item)),
        reverse=True,
    )
    merged["validation_runs"] = ordered[:MAX_STRATEGY_LAB_CHECKPOINTS]
    return merged


def recover_strategy_lab_checkpoint_conflict(
    store: StrategyStore,
    exc: AppError,
) -> dict[str, Any]:
    """Safely reconcile the small checkpoint store after two writers diverge.

    ``restore_cloud_backup`` first creates the store's automatic local backup
    and refreshes its compare-and-swap token. Saving the merged value then fails
    closed if another cloud writer moves the file again during recovery.
    """

    if STRATEGY_LAB_CLOUD_CONFLICT_MARKER not in str(exc):
        raise exc
    local = store.load()
    remote = store.restore_cloud_backup()
    merged = merge_strategy_lab_checkpoint_libraries(local, remote)
    if merged.get("validation_runs") != remote.get("validation_runs"):
        merged = store.save(merged)
    setattr(store, "checkpoint_conflict_recovered", True)
    return merged


def _load_checkpoint_library(store: StrategyStore) -> dict[str, Any]:
    try:
        return store.load_latest()
    except AppError as exc:
        return recover_strategy_lab_checkpoint_conflict(store, exc)


def save_strategy_lab_checkpoint(
    store: StrategyStore,
    *,
    run_id: str,
    status: str,
    ticker: str,
    message: str = "",
    result: dict[str, Any] | None = None,
    progress: float | None = None,
    stage: str = "",
    job: dict[str, Any] | None = None,
    optimizer_state: dict[str, Any] | None = None,
    attempt: int | None = None,
    started_at: str = "",
) -> dict[str, Any]:
    """Persist one Strategy Lab status/result without touching the large main library.

    Running records retain their job specification and completed optimizer-family
    state so a process restart can resume instead of returning to a blank page or
    repeating already completed strategy families.
    """

    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {"running", "complete", "failed"}:
        raise AppError("Strategy Lab checkpoint status must be running, complete, or failed.")
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        raise AppError("Strategy Lab checkpoint run_id is required.")

    data = _load_checkpoint_library(store)
    saved_at = utc_now().isoformat()
    existing = next(
        (
            item
            for item in data.get("validation_runs") or []
            if isinstance(item, dict)
            and str(item.get("id") or "") == normalized_run_id
            and str(item.get("record_type") or "") == STRATEGY_LAB_RECORD_TYPE
        ),
        {},
    )
    record: dict[str, Any] = {
        **deepcopy(existing),
        "id": normalized_run_id,
        "record_type": STRATEGY_LAB_RECORD_TYPE,
        "status": normalized_status,
        "ticker": str(ticker or "").strip().upper(),
        "message": str(message or "").strip(),
        "saved_at": saved_at,
    }
    if progress is not None:
        record["progress"] = max(0.0, min(1.0, float(progress)))
    if stage:
        record["stage"] = str(stage).strip()
    if job is not None:
        record["job"] = deepcopy(job)
    if optimizer_state is not None:
        record["optimizer_state"] = deepcopy(optimizer_state)
    if attempt is not None:
        record["attempt"] = max(0, int(attempt))
    if started_at:
        record["started_at"] = str(started_at).strip()
    if normalized_status == "complete":
        if not isinstance(result, dict) or not result:
            raise AppError("A completed Strategy Lab checkpoint requires a result.")
        record["result"] = deepcopy(result)
        record["progress"] = 1.0
        record["stage"] = "complete"
        record.pop("job", None)
        record.pop("optimizer_state", None)
    elif normalized_status == "failed":
        record.pop("result", None)
        record.pop("job", None)
        record.pop("optimizer_state", None)

    if existing and _checkpoint_rank(existing) > _checkpoint_rank(record):
        return deepcopy(existing)

    previous = [
        item
        for item in data.get("validation_runs") or []
        if isinstance(item, dict)
        and str(item.get("id") or "") != normalized_run_id
        and str(item.get("record_type") or "") == STRATEGY_LAB_RECORD_TYPE
    ]
    data["validation_runs"] = [record, *previous][:MAX_STRATEGY_LAB_CHECKPOINTS]
    store.save(data)
    return record


def load_latest_strategy_lab_checkpoint(
    store: StrategyStore,
    *,
    reconcile_cloud: bool = True,
) -> dict[str, Any]:
    """Return the newest durable Strategy Lab checkpoint, if one exists."""

    data = _load_checkpoint_library(store) if reconcile_cloud else store.load()
    for item in data.get("validation_runs") or []:
        if (
            isinstance(item, dict)
            and str(item.get("record_type") or "") == STRATEGY_LAB_RECORD_TYPE
        ):
            return deepcopy(item)
    return {}
