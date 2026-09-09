"""Dedicated remote worker for reconnect-safe Strategy Lab runs.

The worker claims the existing durable research queue, resolves strategies from
the authoritative private library, re-applies the same fidelity gate as the web
app, then calls the existing UI-independent Strategy Lab executor. Detailed
progress stays in the small Strategy Lab checkpoint file; the large main library
is touched only for queue claim/final state and existing holdout-exposure saves.
"""

from __future__ import annotations

from copy import deepcopy
import argparse
import json
import hashlib
from pathlib import Path
import multiprocessing
import os
import signal
import socket
import time
from typing import Any

from distributed_stock_finder import build_cloud_backup, build_market, mutate_remote_library
from hybrid_runtime.strategy_lab_bridge import (
    REMOTE_STRATEGY_LAB_TYPE,
    STRATEGY_LAB_CHECKPOINT_PATH,
    strategy_lab_result_summary,
    strategy_lab_checkpoint_record,
)
from strategy_lab_jobs import execute_strategy_lab_job_once
from strategy_lab_persistence import restore_strategy_lab_result
from strategy_lab_persistence import load_latest_strategy_lab_checkpoint, save_strategy_lab_checkpoint
from hybrid_runtime.diagnostic_budget import (
    diagnostic_budget, remaining_diagnostic_seconds, stamp_diagnostic_deadline,
)
from stock_strategy_finder import apply_holdout_reuse_guard, record_holdout_exposure
from trading_intelligence_core import effective_strategy_for_research, strategy_integrity_report
from trading_research_orchestrator import (
    claim_next_research_job,
    claim_research_job_by_id,
    fail_research_job,
    finish_research_job,
)
from youtube_strategy_engine import AppError, StrategyStore


WORKER_ID_PREFIX = "strategy-lab-cloud"


def env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


class CloudStrategyLabStore(StrategyStore):
    def commit_holdout_exposure(self, wrapper: dict[str, Any], *, generated_at: str) -> dict[str, Any]:
        committed: dict[str, Any] = {}

        def mutation(library: dict[str, Any]) -> dict[str, Any]:
            guarded = apply_holdout_reuse_guard(library, deepcopy(wrapper))
            updated = record_holdout_exposure(
                library, guarded, source="manual_strategy_lab", generated_at=generated_at,
            )
            committed["wrapper"] = guarded
            return updated

        mutate_remote_library(mutation)
        return committed["wrapper"]


def build_main_store() -> StrategyStore:
    return CloudStrategyLabStore(
        directory=".cloud_strategy_lab_main",
        cloud_backup=build_cloud_backup(),
    )


def build_checkpoint_store() -> StrategyStore:
    return StrategyStore(
        directory=".cloud_strategy_lab_checkpoint",
        cloud_backup=build_cloud_backup(path=STRATEGY_LAB_CHECKPOINT_PATH),
    )


def _worker_provenance(payload: dict[str, Any]) -> dict[str, Any]:
    request = {k: v for k, v in payload.items()
               if k not in {"hybrid_cloud_bridge", "diagnostic_attempt_started_at", "diagnostic_deadline_at"}}
    return {
        "source_revision": env("GITHUB_SHA"),
        "workflow_run_id": env("GITHUB_RUN_ID"),
        "workflow_attempt": env("GITHUB_RUN_ATTEMPT"),
        "request_sha256": hashlib.sha256(json.dumps(request, sort_keys=True,
            separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
        "source_sha256": {name: hashlib.sha256((Path(__file__).resolve().parent / name).read_bytes()).hexdigest()
            for name in ("cloud_strategy_lab_worker.py", "youtube_strategy_engine.py",
                         "strategy_lab_execution.py", "hybrid_runtime/strategy_lab_bridge.py",
                         "hybrid_runtime/diagnostic_budget.py", "systematic_trader/preliminary_scope.py")},
    }


def _claim(preferred_job_id: str = "") -> dict[str, Any] | None:
    holder: dict[str, Any] = {}
    worker_id = f"{WORKER_ID_PREFIX}:{socket.gethostname()}:{os.getpid()}"

    def mutation(data: dict[str, Any]) -> dict[str, Any] | None:
        data = deepcopy(data)
        changed = False
        holder["expired"] = []
        for item in data.get("research_queue", []):
            if item.get("type") != REMOTE_STRATEGY_LAB_TYPE or (preferred_job_id and item.get("id") != preferred_job_id):
                continue
            payload = item.get("payload") or {}
            bounds = diagnostic_budget(payload)
            if not bounds:
                continue
            if item.get("max_attempts") != 1:
                item["max_attempts"] = 1
                changed = True
            expired = bool(payload.get("diagnostic_deadline_at")) and remaining_diagnostic_seconds(payload) <= 0
            exhausted = int(item.get("attempts") or 0) >= 1
            if ((item.get("status") == "running" and expired)
                    or (item.get("status") in {"queued", "retry"} and exhausted)):
                kind = "execution_timeout" if expired else "execution_interrupted"
                item.update(status="failed", stage=kind, failure_kind=kind,
                            failure_step="diagnostic_execution", next_attempt_at=None,
                            worker_id=None, status_message="Diagnostic execution stopped; automatic retry is disabled.")
                holder["expired"].append(deepcopy(item))
                changed = True
        if preferred_job_id:
            matches = [row for row in data.get("research_queue", []) if row.get("id") == preferred_job_id]
            if len(matches) != 1:
                raise AppError("Exact Strategy Lab job is missing or duplicated.")
            row = matches[0]
            if row.get("status") not in {"queued", "retry"}:
                holder["job"] = None
                return data if changed else None
            if int(row.get("attempts") or 0) >= int(row.get("max_attempts") or 3):
                raise AppError("Exact Strategy Lab job has exhausted its retry budget.")
            _check_dispatch_budget(row.get("payload") or {})
            _, claimed = claim_research_job_by_id(
                {"research_queue": [deepcopy(row)]},
                worker_id,
                preferred_job_id,
                allowed_types={REMOTE_STRATEGY_LAB_TYPE},
            )
            updated = deepcopy(data)
            if claimed is not None:
                updated["research_queue"] = [claimed if item.get("id") == preferred_job_id else item
                                             for item in data["research_queue"]]
        else:
            updated, claimed = claim_next_research_job(
                data,
                worker_id,
                allowed_types={REMOTE_STRATEGY_LAB_TYPE},
            )
        if claimed is not None:
            # Persist the actual pickup revision and immutable request before
            # admission, so a legitimate early failure still has durable provenance.
            claimed["worker_provenance"] = _worker_provenance(claimed.get("payload") or {})
            stamp_diagnostic_deadline(claimed.setdefault("payload", {}))
            updated["research_queue"] = [claimed if item.get("id") == claimed.get("id") else item
                                         for item in updated["research_queue"]]
        holder["job"] = deepcopy(claimed) if isinstance(claimed, dict) else None
        return updated if claimed is not None or changed else None

    mutate_remote_library(mutation)
    for expired in holder.get("expired", []):
        _diagnostic_terminal(expired, str(expired["failure_kind"]))
    return holder.get("job")


def _resolve_candidates(
    library: dict[str, Any],
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    all_strategies = [
        dict(item)
        for item in library.get("strategies") or []
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    from hybrid_runtime.strategy_lab_bridge import strategy_revision
    pins = payload.get("strategy_revisions") or {}
    ids = [str(s.get("id") or "") for s in all_strategies]
    requested = set(payload.get("strategy_ids") or pins)
    if any(ids.count(i) > 1 for i in requested):
        raise AppError("Strategy revision identity is ambiguous: duplicate library IDs; no strategy conclusion permitted.")
    if pins:
        if set(pins) != requested:
            raise AppError("Strategy revision set does not match the requested strategies.")
        by_id = {str(s.get("id")): s for s in all_strategies}
        if any(i not in by_id or strategy_revision(by_id[i]) != h for i,h in pins.items()):
            raise AppError("Selected strategy revision changed or is missing; no strategy conclusion permitted. Refresh the selection for a new experiment.")
        all_strategies = [s for s in all_strategies if str(s.get("id")) in pins]
    faithful: list[dict[str, Any]] = []
    blocked_by_id: dict[str, str] = {}
    for strategy in all_strategies:
        report = strategy_integrity_report(strategy)
        status = str(report.get("status") or "").strip().lower()
        strategy_id = str(strategy.get("id") or "").strip()
        if status == "faithful":
            faithful.append(strategy)
        else:
            blocked_by_id[strategy_id] = status or "not_faithful"

    if bool(payload.get("compared_all")):
        selected = faithful
    else:
        requested = [
            str(value or "").strip()
            for value in payload.get("strategy_ids") or []
            if str(value or "").strip()
        ]
        by_id = {str(item.get("id") or ""): item for item in faithful}
        selected = [by_id[value] for value in requested if value in by_id]
        missing = [value for value in requested if value not in by_id]
        if missing:
            detail = ", ".join(
                f"{value} ({blocked_by_id.get(value, 'missing')})"
                for value in missing[:8]
            )
            raise AppError(
                "Strategy Lab refused to run because the selected strategy is no longer "
                f"fully modeled by the current backtester: {detail}"
            )

    if not selected:
        raise AppError(
            "No strategy is currently faithful enough for Strategy Lab cloud testing."
        )
    if not pins:
        raise AppError("Exact selected strategy revisions are missing; no strategy conclusion permitted. Refresh the selection for a new experiment.")
    return [effective_strategy_for_research(item) for item in selected]


def _job_spec(remote_job: dict[str, Any], library: dict[str, Any]) -> dict[str, Any]:
    payload = dict(remote_job.get("payload") or {})
    candidates = _resolve_candidates(library, payload)
    return {
        "version": 1,
        "run_id": str(payload.get("run_id") or remote_job.get("id") or ""),
        "started_at": str(payload.get("started_at") or remote_job.get("created_at") or ""),
        "research_end": str(payload.get("research_end") or payload.get("started_at") or ""),
        "ticker": str(payload.get("ticker") or "").upper(),
        "timeframe": str(payload.get("timeframe") or "5Min"),
        "history_days": int(payload.get("history_days") or 30),
        "search_depth": int(payload.get("search_depth") or 36),
        "starting_cash": float(payload.get("starting_cash") or 2000.0),
        "risk_per_trade": float(payload.get("risk_per_trade") or 10.0),
        "max_position": float(payload.get("max_position") or 100.0),
        "max_drawdown": float(payload.get("max_drawdown") or 15.0),
        "training_fraction": float(payload.get("training_fraction") or 0.60),
        "validation_fraction": float(payload.get("validation_fraction") or 0.20),
        "minimum_training_trades": int(payload.get("minimum_training_trades") or 5),
        "minimum_validation_trades": int(payload.get("minimum_validation_trades") or 2),
        "run_walk_forward": bool(payload.get("run_walk_forward")),
        "wf_history_sessions": int(payload.get("wf_history_sessions") or 8),
        "wf_test_sessions": int(payload.get("wf_test_sessions") or 2),
        "wf_folds": int(payload.get("wf_folds") or 3),
        "compared_all": bool(payload.get("compared_all")),
        "candidates": candidates,
        "strategy_revisions": dict(payload.get("strategy_revisions") or {}),
        "queue_job_id": str(remote_job.get("id") or ""),
        "queue_attempt": int(remote_job.get("attempts") or 0),
        **diagnostic_budget(payload),
        **{key: payload[key] for key in ("diagnostic_attempt_started_at", "diagnostic_deadline_at") if key in payload},
    }


def _complete_queue(
    job_id: str,
    *,
    run_id: str,
    result_summary: dict[str, Any],
) -> None:
    result_ref = f"strategy-lab-checkpoint:{run_id}"

    def mutation(data: dict[str, Any]) -> dict[str, Any]:
        matches = [item for item in data.get("research_queue", []) if item.get("id") == job_id]
        if len(matches) != 1:
            raise AppError("Strategy Lab queue identity changed before completion.")
        current = matches[0]
        if (current.get("type") != REMOTE_STRATEGY_LAB_TYPE
                or (current.get("payload") or {}).get("run_id") != run_id
                or current.get("status") != "running"):
            raise AppError("Strategy Lab completion refused: the original run is no longer active.")
        updated = finish_research_job(data, job_id, result_ref=result_ref)
        queue: list[dict[str, Any]] = []
        for raw in updated.get("research_queue") or []:
            item = dict(raw)
            if str(item.get("id") or "") == job_id:
                item["stage"] = "complete"
                item["progress"] = 1.0
                item["result"] = deepcopy(result_summary)
                item["status_message"] = "Strategy Lab cloud run completed and its durable checkpoint is saved."
            queue.append(item)
        updated["research_queue"] = queue
        return updated

    mutate_remote_library(mutation)


def _fail_queue(job_id: str, message: str, *, failure_kind: str = "",
                execution_error: dict[str, Any] | None = None,
                finalizer_provenance: dict[str, Any] | None = None) -> str:
    holder = {"status": "failed"}

    def mutation(data: dict[str, Any]) -> dict[str, Any]:
        data = deepcopy(data)
        for item in data.get("research_queue", []):
            if item.get("id") == job_id and diagnostic_budget(item.get("payload") or {}):
                item["max_attempts"] = 1
        updated = fail_research_job(
            data,
            job_id,
            message,
            retry_delay_minutes=20,
            failure_step="strategy_lab_execution",
        )
        for item in updated.get("research_queue") or []:
            if str(item.get("id") or "") == job_id:
                if item.get("status") == "failed" and diagnostic_budget(item.get("payload") or {}):
                    kind = failure_kind or item.get("failure_kind") or "execution_error"
                    item.update(failure_kind=kind, failure_step="diagnostic_execution", stage=kind,
                                next_attempt_at=None)
                    if execution_error:
                        item["execution_error"] = deepcopy(execution_error)
                        item["terminal_reason"] = kind
                        item["progress"] = max(float(item.get("progress") or 0),
                                               float(execution_error.get("last_progress") or 0))
                if finalizer_provenance:
                    item["finalizer_provenance"] = deepcopy(finalizer_provenance)
                holder["status"] = str(item.get("status") or "failed")
                break
        return updated

    mutate_remote_library(mutation)
    return holder["status"]


def _saved_cloud_result(checkpoint_store: StrategyStore, run_id: str, ticker: str,
                        remote_job: dict[str, Any] | None = None) -> dict[str, Any]:
    remote = checkpoint_store.cloud_backup.read_library()
    saved = strategy_lab_checkpoint_record((remote or {}).get("library") or {}, run_id=run_id)
    if saved.get("status") != "complete":
        return {}
    if str(saved.get("ticker") or "").upper() != ticker:
        raise AppError("Completed cloud checkpoint ticker does not match the requested run.")
    result = saved.get("result")
    if not result and saved.get("result_archive"):
        result = restore_strategy_lab_result(saved)
    if not isinstance(result, dict) or not result:
        raise AppError("Completed cloud checkpoint has no verifiable result.")
    if remote_job is not None:
        from hybrid_runtime.strategy_lab_bridge import overlay_strategy_lab_checkpoint
        if not (remote_job.get("payload") or {}).get("strategy_revisions"):
            raise AppError("Completed cloud recovery lacks pinned strategy revisions; no strategy conclusion permitted.")
        checked = overlay_strategy_lab_checkpoint(remote_job, saved)
        if checked.get("status") != "complete":
            raise AppError("Completed cloud recovery identity mismatch; no strategy conclusion permitted.")
    return result


def _run_claimed_job(remote_job: dict[str, Any]) -> dict[str, Any]:
    job_id = str(remote_job.get("id") or "")
    payload = dict(remote_job.get("payload") or {})
    run_id = str(payload.get("run_id") or job_id)
    ticker = str(payload.get("ticker") or "").upper()
    main_store = build_main_store()
    checkpoint_store = build_checkpoint_store()
    try:
        # Finish a queue-only recovery from exact durable evidence. A prior
        # successful upload followed by queue-save failure must not recompute.
        saved_result = _saved_cloud_result(checkpoint_store, run_id, ticker, remote_job)
        if saved_result:
            summary = strategy_lab_result_summary(saved_result, run_id=run_id)
            _complete_queue(job_id, run_id=run_id, result_summary=summary)
            return {"status": "complete", "job_id": job_id, "run_id": run_id,
                    "ticker": ticker, "result": summary, "recovered_from_checkpoint": True}
        library = main_store.load_latest()
        job = _job_spec(remote_job, library)
        outcome = execute_strategy_lab_job_once(
            run_id=run_id,
            job=job,
            checkpoint_store=checkpoint_store,
            market=build_market(),
            main_store=main_store,
        )
        if str(outcome.get("status") or "") != "complete":
            message = str(outcome.get("message") or "Strategy Lab cloud execution failed")
            queue_status = _fail_queue(job_id, message)
            return {
                "status": queue_status,
                "job_id": job_id,
                "run_id": run_id,
                "ticker": ticker,
                "message": message,
            }
        result = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
        # A runner's local disk is ephemeral. Never mark the queue complete
        # merely because computation finished while checkpoint upload failed.
        saved_result = _saved_cloud_result(checkpoint_store, run_id, ticker)
        expected = json.loads(json.dumps(result, default=str, allow_nan=False))
        if not result or saved_result != expected:
            raise AppError("Strategy Lab result is not verified in the private cloud checkpoint; queue completion refused.")
        summary = strategy_lab_result_summary(result, run_id=run_id)
        _complete_queue(job_id, run_id=run_id, result_summary=summary)
        return {
            "status": "complete",
            "job_id": job_id,
            "run_id": run_id,
            "ticker": ticker,
            "result": summary,
            "warning": str(outcome.get("warning") or ""),
        }
    except BaseException as exc:
        message = (str(exc).strip() or type(exc).__name__) + "; execution/admission incomplete; no strategy validation verdict was produced."
        try:
            queue_status = _fail_queue(job_id, message)
        except BaseException:
            raise
        return {
            "status": queue_status,
            "job_id": job_id,
            "run_id": run_id,
            "ticker": ticker,
            "message": message,
        }


def _check_dispatch_budget(payload: dict[str, Any]) -> None:
    """Inputs may tighten dispatch identity, never replace the persisted budget."""
    if env("DIAGNOSTIC_MODE") == "true":
        expected = diagnostic_budget({
            "diagnostic_mode": True,
            "diagnostic_max_attempts": int(env("DIAGNOSTIC_MAX_ATTEMPTS", "1") or 1),
            "diagnostic_timeout_minutes": int(env("DIAGNOSTIC_TIMEOUT_MINUTES", "20") or 20),
        })
        if diagnostic_budget(payload) != expected:
            raise AppError("Workflow diagnostic inputs do not match the persisted job budget.")


def _diagnostic_terminal(remote_job: dict[str, Any], kind: str) -> dict[str, Any]:
    """Finalize infrastructure failure without generating a strategy verdict."""
    payload = dict(remote_job.get("payload") or {})
    if not diagnostic_budget(payload):
        raise AppError("Diagnostic finalization requires a diagnostic job.")
    job_id = str(remote_job["id"])
    run_id = str(payload.get("run_id") or job_id)
    ticker = str(payload.get("ticker") or "").upper()
    message = ("VALIDATION EXECUTION TIMED OUT" if kind == "execution_timeout"
               else "VALIDATION EXECUTION INTERRUPTED") + "; no strategy validation verdict was produced."
    warning = ""
    execution_error = {
        "category": "infrastructure", "kind": kind,
        "terminal_reason": kind, "message": message,
        **diagnostic_budget(payload),
        **{key: payload[key] for key in ("diagnostic_attempt_started_at", "diagnostic_deadline_at") if key in payload},
    }
    try:
        store = build_checkpoint_store()
        checkpoint = load_latest_strategy_lab_checkpoint(store, run_id=run_id)
        if checkpoint.get("status") == "complete":
            saved = _saved_cloud_result(store, run_id, ticker)
            if saved:
                _complete_queue(job_id, run_id=run_id,
                                result_summary=strategy_lab_result_summary(saved, run_id=run_id))
                return {"status": "complete", "job_id": job_id, "recovered_from_checkpoint": True}
        previous_error = checkpoint.get("execution_error") or {}
        last_stage = str(previous_error.get("last_execution_stage") or checkpoint.get("stage") or "unknown")
        if last_stage in {"execution_timeout", "execution_interrupted", "failed", "aborted"}:
            last_stage = "unknown"  # Never invent execution history lost by old code.
        execution_error.update(
            last_execution_stage=last_stage,
            last_progress=float(checkpoint.get("progress") or 0),
            last_checkpoint_saved_at=str(previous_error.get("last_checkpoint_saved_at")
                                         or checkpoint.get("saved_at") or ""),
        )
        save_strategy_lab_checkpoint(
            store, run_id=run_id, ticker=ticker, status="failed", message=message,
            stage=last_stage, progress=float(checkpoint.get("progress") or 0),
            job=dict(checkpoint.get("job") or payload), attempt=1,
            execution_error=execution_error,
        )
    except Exception as exc:
        # Still terminalize the queue if checkpoint I/O is unavailable. Workflow
        # finalization can repair the checkpoint later without another attempt.
        warning = f"Diagnostic checkpoint finalization unavailable ({type(exc).__name__})."
    status = _fail_queue(job_id, message, failure_kind=kind, execution_error=execution_error)
    return {"status": status, "job_id": job_id, "run_id": run_id,
            "failure_kind": kind if status == "failed" else "", "message": message,
            "warning": warning}


def _diagnostic_child(remote_job: dict[str, Any], connection: Any) -> None:
    os.setsid()
    try:
        outcome = _run_claimed_job(remote_job)
        # Keep the supervision channel small. Results remain in the existing
        # verified cloud checkpoint, not in a second result store.
        connection.send({key: outcome[key] for key in (
            "status", "job_id", "run_id", "ticker", "message", "warning",
            "recovered_from_checkpoint") if key in outcome})
    finally:
        connection.close()


def _stop_diagnostic_process(process: Any) -> None:
    if process.is_alive():
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.terminate()
        process.join(2)
    if process.is_alive():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            process.kill()
        process.join(2)
    if process.is_alive():
        raise AppError("Diagnostic worker could not be stopped; workflow cancellation is required.")


def _run_diagnostic(remote_job: dict[str, Any]) -> dict[str, Any]:
    payload = remote_job["payload"]
    _check_dispatch_budget(payload)
    if int(remote_job.get("attempts") or 0) != 1:
        return _diagnostic_terminal(remote_job, "execution_interrupted")
    remaining = remaining_diagnostic_seconds(payload)
    if remaining <= 0:
        return _diagnostic_terminal(remote_job, "execution_timeout")
    context = multiprocessing.get_context("fork")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_diagnostic_child, args=(remote_job, send))
    deadline = time.monotonic() + remaining
    outcome = None
    kind = "execution_interrupted"
    try:
        process.start()
        send.close()
        while time.monotonic() < deadline:
            if receive.poll(min(0.1, max(0, deadline - time.monotonic()))):
                try:
                    outcome = receive.recv()
                except EOFError:
                    pass
                break
            if not process.is_alive():
                break
        if outcome is None and time.monotonic() >= deadline:
            kind = "execution_timeout"
    finally:
        if process.pid is not None:
            _stop_diagnostic_process(process)
        receive.close()
        send.close()
    return outcome if outcome is not None else _diagnostic_terminal(remote_job, kind)


def finalize_diagnostic(job_id: str) -> dict[str, Any]:
    if not job_id:
        raise AppError("Diagnostic finalization requires an exact job ID.")
    holder: dict[str, Any] = {}
    def inspect(data: dict[str, Any]) -> None:
        rows = [row for row in data.get("research_queue", []) if row.get("id") == job_id]
        if len(rows) != 1 or rows[0].get("type") != REMOTE_STRATEGY_LAB_TYPE:
            raise AppError("Diagnostic finalization requires one existing Strategy Lab job.")
        holder["job"] = deepcopy(rows[0])
    mutate_remote_library(inspect)
    job = holder["job"]
    payload = job.get("payload") or {}
    if not diagnostic_budget(payload):
        raise AppError("Refusing to finalize a non-diagnostic job.")
    if job.get("status") in {"complete", "cancelled"} or int(job.get("attempts") or 0) == 0:
        return {"status": job.get("status"), "job_id": job_id}
    if job.get("status") == "failed" and job.get("failure_kind") not in {"execution_timeout", "execution_interrupted"}:
        # An already-persisted admission/execution failure is not an interrupted
        # process. Preserve its actual error and repair legacy interruption metadata
        # without claiming or executing the job again.
        message = str(job.get("last_error") or "").strip()
        if not message:
            raise AppError("Terminal diagnostic failure lacks its original error; refusing to invent one.")
        kind = str(job.get("failure_kind") or "execution_error")
        error = {"category": "execution", "kind": kind, "terminal_reason": kind,
                 "message": message, **diagnostic_budget(payload)}
        store = build_checkpoint_store()
        checkpoint = load_latest_strategy_lab_checkpoint(store, run_id=str(payload.get("run_id") or job_id))
        if checkpoint.get("status") == "complete":
            raise AppError("Terminal failure conflicts with a completed checkpoint; refusing to overwrite it.")
        save_strategy_lab_checkpoint(
            store, run_id=str(payload.get("run_id") or job_id), ticker=str(payload.get("ticker") or ""),
            status="failed", message=message, stage=str(job.get("failure_step") or "strategy_lab_execution"),
            progress=float(job.get("progress") or 0), job=payload,
            attempt=int(job.get("attempts") or 1), execution_error=error,
        )
        status = _fail_queue(job_id, message, failure_kind=kind, execution_error=error,
                             finalizer_provenance=_worker_provenance(payload))
        return {"status": status, "job_id": job_id, "message": message,
                "preserved_terminal_failure": True, "new_execution_attempts": 0}
    kind = job.get("failure_kind") or (
        "execution_timeout" if remaining_diagnostic_seconds(payload) <= 0 else "execution_interrupted")
    return _diagnostic_terminal(job, str(kind))


def run_once(preferred_job_id: str = "") -> dict[str, Any]:
    remote_job = _claim(str(preferred_job_id or "").strip())
    if remote_job is None:
        return {"status": "idle", "message": "No queued Strategy Lab cloud job is ready."}
    if diagnostic_budget(remote_job.get("payload") or {}):
        return _run_diagnostic(remote_job)
    return _run_claimed_job(remote_job)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", default="")
    parser.add_argument("--finalize-diagnostic", action="store_true")
    args = parser.parse_args(argv)
    result = finalize_diagnostic(args.job_id) if args.finalize_diagnostic else run_once(args.job_id)
    print(result, flush=True)
    # A durable retry is a successfully handled worker cycle; the scheduled
    # workflow will pick it up again after next_attempt_at.
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
