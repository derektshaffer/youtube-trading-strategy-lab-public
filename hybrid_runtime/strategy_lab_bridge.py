"""Cloud-bridge contract for reconnect-safe Strategy Lab execution."""

from __future__ import annotations

from .diagnostic_budget import diagnostic_budget

from copy import deepcopy
from hashlib import sha256
import re
from typing import Any, Mapping

from .contracts import JobRecord, utc_now_text, canonical_json
from .github_library import GitHubLibraryConfig


REMOTE_STRATEGY_LAB_TYPE = "strategy_lab"
CLOUD_STRATEGY_LAB_WORKFLOW = "cloud-strategy-lab.yml"
STRATEGY_LAB_CHECKPOINT_PATH = "trading-intelligence-lab/strategy_lab_latest.json"
STRATEGY_LAB_RECORD_TYPE = "strategy_lab_checkpoint"
_ALLOWED_TIMEFRAMES = frozenset({"1Min", "5Min", "15Min"})
_ALLOWED_DEPTHS = frozenset({12, 36, 96, 160})
_SYMBOL = re.compile(r"^[A-Z][A-Z.\-]{0,9}$")


def strategy_revision(strategy: Mapping[str, Any]) -> str:
    """Pin the selected library record, including source/admission semantics."""
    return sha256(canonical_json(dict(strategy)).encode("utf-8")).hexdigest()


def strategy_lab_checkpoint_config(base: GitHubLibraryConfig) -> GitHubLibraryConfig:
    return GitHubLibraryConfig(
        repository=base.repository,
        path=STRATEGY_LAB_CHECKPOINT_PATH,
        branch=base.branch,
        action_repository=base.action_repository,
        workflow_file=CLOUD_STRATEGY_LAB_WORKFLOW,
        workflow_ref=base.workflow_ref,
    )


def strategy_lab_dedupe_key(run_id: str) -> str:
    clean = str(run_id or "").strip()
    if not clean:
        raise ValueError("Strategy Lab run_id is required")
    return "strategy-lab:" + clean


def _remote_identifier(dedupe_key: str) -> str:
    digest = sha256(str(dedupe_key).encode("utf-8")).hexdigest()[:24]
    return f"desktop-strategy-lab-{digest}"


def _clean_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    return max(minimum, min(maximum, number))


def _clean_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    return max(minimum, min(maximum, number))


def _strategy_ids(payload: Mapping[str, Any]) -> list[str]:
    raw = payload.get("strategy_ids") or []
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",")]
    if not isinstance(raw, (list, tuple)):
        raw = []
    return list(
        dict.fromkeys(
            str(value or "").strip()
            for value in raw
            if str(value or "").strip()
        )
    )[:250]


def normalized_strategy_lab_payload(job: JobRecord) -> dict[str, Any]:
    source = job.payload
    run_id = str(source.get("run_id") or f"strategy-lab-{job.id}").strip()
    ticker = str(source.get("ticker") or "").strip().upper()
    if not _SYMBOL.fullmatch(ticker):
        raise ValueError("Strategy Lab requires a valid U.S. equity ticker")
    timeframe = str(source.get("timeframe") or "5Min").strip()
    if timeframe not in _ALLOWED_TIMEFRAMES:
        raise ValueError("Strategy Lab timeframe must be 1Min, 5Min, or 15Min")
    try:
        search_depth = int(source.get("search_depth") or 36)
    except (TypeError, ValueError, OverflowError):
        search_depth = 36
    if search_depth not in _ALLOWED_DEPTHS:
        raise ValueError("Strategy Lab depth must be 12, 36, 96, or 160")
    compare_all = bool(source.get("compared_all") or source.get("compare_all"))
    strategy_ids = _strategy_ids(source)
    if not strategy_ids and not compare_all:
        raise ValueError("Strategy Lab requires a strategy id unless Compare All is enabled")
    training_fraction = _clean_float(source.get("training_fraction"), 0.60, 0.40, 0.75)
    validation_fraction = _clean_float(source.get("validation_fraction"), 0.20, 0.10, 0.35)
    if training_fraction + validation_fraction > 0.90:
        raise ValueError("Strategy Lab must leave at least 10% of sessions untouched for holdout")
    started_at = str(source.get("started_at") or utc_now_text()).strip()
    return {
        "version": 1,
        "run_id": run_id,
        "started_at": started_at,
        "research_end": str(source.get("research_end") or started_at).strip(),
        "ticker": ticker,
        "timeframe": timeframe,
        "history_days": _clean_int(source.get("history_days"), 30, 7, 180),
        "search_depth": search_depth,
        "starting_cash": _clean_float(source.get("starting_cash"), 2000.0, 1000.0, 1_000_000.0),
        "risk_per_trade": _clean_float(source.get("risk_per_trade"), 10.0, 0.1, 10.0),
        "max_position": _clean_float(source.get("max_position"), 100.0, 1.0, 100.0),
        "max_drawdown": _clean_float(source.get("max_drawdown"), 15.0, 1.0, 20.0),
        "training_fraction": training_fraction,
        "validation_fraction": validation_fraction,
        "minimum_training_trades": _clean_int(source.get("minimum_training_trades"), 5, 1, 50),
        "minimum_validation_trades": _clean_int(source.get("minimum_validation_trades"), 2, 1, 25),
        "run_walk_forward": bool(source.get("run_walk_forward")),
        "wf_history_sessions": _clean_int(source.get("wf_history_sessions"), 8, 5, 60),
        "wf_test_sessions": _clean_int(source.get("wf_test_sessions"), 2, 1, 10),
        "wf_folds": _clean_int(source.get("wf_folds"), 3, 2, 6),
        "compared_all": compare_all,
        "strategy_ids": strategy_ids,
        "strategy_revisions": dict(source.get("strategy_revisions") or {}),
        "checkpoint_path": STRATEGY_LAB_CHECKPOINT_PATH,
        "continue_after_app_exit": True,
        **diagnostic_budget(source),
    }


def find_strategy_lab_remote_item(
    library: Mapping[str, Any],
    *,
    local_job_id: str = "",
    remote_job_id: str = "",
    dedupe_key: str = "",
) -> dict[str, Any] | None:
    local = str(local_job_id or "").strip()
    remote = str(remote_job_id or "").strip()
    dedupe = str(dedupe_key or "").strip()
    for raw in library.get("research_queue") or []:
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("type") or "") != REMOTE_STRATEGY_LAB_TYPE:
            continue
        if remote and str(raw.get("id") or "") == remote:
            return dict(raw)
        if dedupe and str(raw.get("dedupe_key") or "") == dedupe:
            return dict(raw)
        payload = raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {}
        marker = payload.get("hybrid_cloud_bridge") if isinstance(payload.get("hybrid_cloud_bridge"), Mapping) else {}
        if local and str(marker.get("local_job_id") or "") == local:
            return dict(raw)
    return None


def prepare_strategy_lab_publication(
    library: dict[str, Any],
    job: JobRecord,
) -> tuple[dict[str, Any] | None, bool, dict[str, Any] | None]:
    payload = normalized_strategy_lab_payload(job)
    dedupe = strategy_lab_dedupe_key(payload["run_id"])
    existing = find_strategy_lab_remote_item(
        library,
        local_job_id=job.id,
        dedupe_key=dedupe,
    )
    if existing is not None:
        return existing, False, {"queue_status": "active", "run_id": payload["run_id"]}

    now = utc_now_text()
    remote_id = _remote_identifier(dedupe)
    payload["hybrid_cloud_bridge"] = {
        "version": 1,
        "local_job_id": job.id,
        "request_fingerprint": job.request_fingerprint,
    }
    item = {
        "id": remote_id,
        "request_id": remote_id,
        "type": REMOTE_STRATEGY_LAB_TYPE,
        "job_type": REMOTE_STRATEGY_LAB_TYPE,
        "status": "queued",
        "stage": "queued",
        "progress": 0.0,
        "priority": max(85, min(100, int(job.priority))),
        "attempt": 0,
        "attempts": 0,
        "max_attempts": diagnostic_budget(payload).get("diagnostic_max_attempts", 3),
        "dedupe_key": dedupe,
        "origin": "trading_intelligence_desktop",
        "source": "trading_intelligence_desktop",
        "created_at": now,
        "updated_at": now,
        "queued_at": now,
        "payload": payload,
        "result_ref": None,
    }
    queue = [dict(row) for row in library.get("research_queue") or [] if isinstance(row, Mapping)]
    queue.append(item)
    library["research_queue"] = queue
    return item, True, {"queue_status": "ready", "run_id": payload["run_id"]}


def strategy_lab_checkpoint_record(
    checkpoint_library: Mapping[str, Any] | None,
    run_id: str,
) -> dict[str, Any]:
    if not isinstance(checkpoint_library, Mapping):
        return {}
    target = str(run_id or "").strip()
    for raw in checkpoint_library.get("validation_runs") or []:
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("record_type") or "") != STRATEGY_LAB_RECORD_TYPE:
            continue
        if target and str(raw.get("id") or "") != target:
            continue
        return dict(raw)
    return {}


def strategy_lab_execution_error(item: Mapping[str, Any], checkpoint: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Allowlisted diagnostic details, bound to the exact queue run and ticker."""
    payload = item.get("payload") or {}
    if not diagnostic_budget(payload):
        return {}
    cp = checkpoint if isinstance(checkpoint, Mapping) else {}
    if cp and (cp.get("id") != payload.get("run_id")
               or str(cp.get("ticker") or "").upper() != str(payload.get("ticker") or "").upper()
               or (item.get("attempts") and cp.get("attempt") != item.get("attempts"))):
        cp = {}
    source = cp.get("execution_error") if cp.get("status") == "failed" else None
    source = source if isinstance(source, Mapping) else item.get("execution_error") or {}
    kind = str(source.get("kind") or item.get("failure_kind") or "")
    if kind not in {"execution_timeout", "execution_interrupted"}:
        return {}
    stage = str(source.get("last_execution_stage") or cp.get("stage") or "unknown")
    if stage in {"execution_timeout", "execution_interrupted", "failed", "aborted"}:
        stage = "unknown"
    details = {
        "type": "CloudStrategyLabExecutionError", "category": "infrastructure",
        "kind": kind, "terminal_reason": kind,
        "message": ("VALIDATION EXECUTION TIMED OUT" if kind == "execution_timeout"
                    else "VALIDATION EXECUTION INTERRUPTED") + "; no strategy validation verdict was produced.",
        "last_execution_stage": stage,
        "last_progress": _clean_float(source.get("last_progress", cp.get("progress", item.get("progress"))), 0.0, 0.0, 1.0),
        "last_checkpoint_saved_at": str(source.get("last_checkpoint_saved_at") or cp.get("saved_at") or ""),
        **diagnostic_budget(payload),
    }
    for key in ("diagnostic_attempt_started_at", "diagnostic_deadline_at"):
        value = payload.get(key) or source.get(key) or cp.get(key)
        if value:
            details[key] = str(value)
    return details


def overlay_strategy_lab_checkpoint(
    item: Mapping[str, Any],
    checkpoint: Mapping[str, Any] | None,
) -> dict[str, Any]:
    result = deepcopy(dict(item))
    current_status = str(result.get("status") or "").strip().lower()
    payload = dict(result.get("payload") or {})
    if isinstance(checkpoint, Mapping) and checkpoint:
        mismatch = (
            not payload.get("run_id")
            or checkpoint.get("id") != payload.get("run_id")
            or (payload.get("ticker") and str(checkpoint.get("ticker") or "").upper()
                != str(payload["ticker"]).upper())
            or (result.get("attempts") and checkpoint.get("attempt") != result["attempts"])
        )
        if mismatch:
            payload.update(strategy_lab_checkpoint_status="identity_mismatch",
                           distributed_message="Checkpoint identity mismatch; no strategy validation verdict was produced.")
            result["payload"] = payload
            result.pop("result", None)
            result.pop("result_ref", None)
            if current_status == "complete":
                result.update(status="failed", error={"type": "CloudStrategyLabIdentityMismatch",
                    "message": payload["distributed_message"]})
            return result
    error = strategy_lab_execution_error(result, checkpoint)
    if error and current_status not in {"complete", "cancelled", "canceled"}:
        result.update(status="failed", error=error, failure_kind=error["kind"],
                      terminal_reason=error["kind"], stage=error["last_execution_stage"],
                      progress=max(_clean_float(result.get("progress"), 0, 0, 1), error["last_progress"]))
        payload = dict(result.get("payload") or {})
        payload.update(distributed_progress=result["progress"], distributed_stage=result["stage"],
                       distributed_message=error["message"], strategy_lab_checkpoint_status="failed",
                       strategy_lab_checkpoint_saved_at=error["last_checkpoint_saved_at"])
        result["payload"] = payload
        return result
    if not isinstance(checkpoint, Mapping) or not checkpoint:
        return result
    current_status = str(result.get("status") or "").strip().lower()
    if current_status in {"cancelled", "canceled", "failed", "error"}:
        return result
    checkpoint_status = str(checkpoint.get("status") or "").strip().lower()
    payload = dict(result.get("payload") or {})
    try:
        progress = max(0.0, min(1.0, float(checkpoint.get("progress") or 0.0)))
    except (TypeError, ValueError, OverflowError):
        progress = 0.0
    stage = str(checkpoint.get("stage") or result.get("stage") or "running").strip().lower()
    message = " ".join(str(checkpoint.get("message") or "").split())[:500]
    payload["distributed_progress"] = progress
    payload["distributed_stage"] = stage
    payload["distributed_message"] = message
    payload["strategy_lab_checkpoint_saved_at"] = str(checkpoint.get("saved_at") or "")
    result["payload"] = payload
    result["progress"] = progress
    result["stage"] = stage
    if checkpoint_status == "complete":
        summary = strategy_lab_result_from_checkpoint(checkpoint)
        if not summary:
            result.update(status="failed", stage="failed", error={
                "type": "CloudStrategyLabResultMissing",
                "message": "Completed checkpoint has no durable result; no strategy validation verdict was produced."})
            result.pop("result", None)
            return result
        requested_ids = payload.get("strategy_ids") or []
        pins = payload.get("strategy_revisions") or {}
        identity = summary.get("identity") or {}
        target = identity.get("validation_target") or {}
        pinned_mismatch = bool(pins) and (
            identity.get("strategy_revisions") != pins
            or summary.get("winner_strategy_id") not in pins
            or identity.get("run_id") != payload.get("run_id")
            or target.get("strategy_id") != summary.get("winner_strategy_id")
            or target.get("configuration_hash") != summary.get("winner_configuration_hash")
            or not identity.get("execution_completed")
            or not identity.get("input_sha256")
        )
        if ((payload.get("ticker") and summary.get("ticker") != str(payload["ticker"]).upper())
                or pinned_mismatch
                or (requested_ids and not payload.get("compared_all")
                    and summary.get("winner_strategy_id") not in requested_ids)):
            result.update(status="failed", stage="failed", error={
                "type": "CloudStrategyLabIdentityMismatch",
                "message": "Checkpoint result does not match the requested strategy revision or validated winner; no strategy validation verdict was produced."})
            result.pop("result", None)
            result.pop("result_ref", None)
            return result
        if summary:
            result["status"] = "complete"
            result["progress"] = 1.0
            result["stage"] = "complete"
            result["result"] = summary
            result["result_ref"] = (
                f"strategy-lab-checkpoint:{str(checkpoint.get('id') or '')}"
            )
    elif checkpoint_status == "failed":
        if diagnostic_budget(payload) and (checkpoint.get("execution_error") or {}).get("category") == "infrastructure":
            result["status"] = "failed"
            result["failure_kind"] = checkpoint["execution_error"].get("kind", "execution_error")
            payload["strategy_lab_checkpoint_status"] = "failed"
            return result
        # A checkpoint describes one execution attempt, not the durable queue's
        # final decision. The main queue may already have moved this job to
        # `retry`, so never terminalize the desktop from checkpoint failure alone.
        payload["strategy_lab_checkpoint_status"] = "failed"
        if current_status in {"queued", "pending", "retry", "retry_wait"}:
            result["stage"] = "cloud_queued"
            payload["distributed_stage"] = "cloud_queued"
            payload["distributed_message"] = (
                message or "Strategy Lab attempt ended; waiting for durable cloud retry."
            )
        result["payload"] = payload
    elif checkpoint_status == "running" and current_status in {"queued", "pending", "retry"}:
        result["status"] = "running"
    return result


def strategy_lab_link_metadata(
    item: Mapping[str, Any],
    job: JobRecord,
) -> dict[str, Any]:
    payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
    return {
        "job_type": job.job_type,
        "run_id": str(payload.get("run_id") or job.payload.get("run_id") or ""),
        "ticker": str(payload.get("ticker") or job.payload.get("ticker") or "").upper(),
        "timeframe": str(payload.get("timeframe") or job.payload.get("timeframe") or ""),
        "search_depth": int(payload.get("search_depth") or job.payload.get("search_depth") or 0),
        "checkpoint_path": str(payload.get("checkpoint_path") or STRATEGY_LAB_CHECKPOINT_PATH),
        "distributed_message": str(payload.get("distributed_message") or ""),
    }


def _metric_block(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    allowed = (
        "net_pnl",
        "return_pct",
        "trade_count",
        "win_rate_pct",
        "profit_factor",
        "max_drawdown_pct",
        "expectancy",
    )
    return {key: value.get(key) for key in allowed if value.get(key) is not None}


def strategy_lab_result_summary(
    result: Mapping[str, Any] | None,
    *,
    run_id: str = "",
    saved_at: str = "",
) -> dict[str, Any]:
    """Single canonical compact mapping shared by worker, bridge and results."""
    from .strategy_lab_result_projection import project_strategy_lab_result
    return project_strategy_lab_result(result, run_id=run_id, saved_at=saved_at)


def strategy_lab_result_from_checkpoint(
    checkpoint: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(checkpoint, Mapping):
        return {}
    if str(checkpoint.get("status") or "").strip().lower() != "complete":
        return {}
    from strategy_lab_persistence import restore_strategy_lab_result
    raw = restore_strategy_lab_result(dict(checkpoint))
    if not raw:
        return {}
    return strategy_lab_result_summary(
        raw,
        run_id=str(checkpoint.get("id") or ""),
        saved_at=str(checkpoint.get("saved_at") or ""),
    )


def read_strategy_lab_progress(library, base_config, token, client_factory):
    """Read optional lightweight progress while preserving the full checkpoint."""
    from dataclasses import replace
    from strategy_lab_progress import PROGRESS_FORMAT, progress_path, merge_progress
    result = dict(library)
    records = []
    for record in library.get("validation_runs") or []:
        if not isinstance(record, dict):
            records.append(record)
            continue
        merged = record
        if record.get("progress_storage") == PROGRESS_FORMAT and record.get("status") == "running":
            try:
                config = replace(strategy_lab_checkpoint_config(base_config),
                    path=progress_path(STRATEGY_LAB_CHECKPOINT_PATH, str(record["id"])))
                document = client_factory(config, token).read().data
                merged = merge_progress(record, document)
            except Exception:
                # Progress is optional; its absence cannot become completion.
                pass
        records.append(merged)
    result["validation_runs"] = records
    return result
