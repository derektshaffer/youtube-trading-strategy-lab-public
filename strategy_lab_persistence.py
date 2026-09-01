"""Durable checkpoint helpers for interactive Strategy Lab runs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from youtube_strategy_engine import AppError, StrategyStore, utc_now


STRATEGY_LAB_RECORD_TYPE = "strategy_lab_checkpoint"
MAX_STRATEGY_LAB_CHECKPOINTS = 5


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

    data = store.load_latest()
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

    data = store.load_latest() if reconcile_cloud else store.load()
    for item in data.get("validation_runs") or []:
        if (
            isinstance(item, dict)
            and str(item.get("record_type") or "") == STRATEGY_LAB_RECORD_TYPE
        ):
            return deepcopy(item)
    return {}
