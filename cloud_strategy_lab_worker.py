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
import os
import socket
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


def _claim(preferred_job_id: str = "") -> dict[str, Any] | None:
    holder: dict[str, Any] = {}
    worker_id = f"{WORKER_ID_PREFIX}:{socket.gethostname()}:{os.getpid()}"

    def mutation(data: dict[str, Any]) -> dict[str, Any] | None:
        if preferred_job_id:
            matches = [row for row in data.get("research_queue", []) if row.get("id") == preferred_job_id]
            if len(matches) != 1:
                raise AppError("Exact Strategy Lab job is missing or duplicated.")
            row = matches[0]
            if row.get("status") not in {"queued", "retry"}:
                holder["job"] = None
                return None
            if int(row.get("attempts") or 0) >= int(row.get("max_attempts") or 3):
                raise AppError("Exact Strategy Lab job has exhausted its retry budget.")
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
        holder["job"] = deepcopy(claimed) if isinstance(claimed, dict) else None
        return updated if claimed is not None else None

    mutate_remote_library(mutation)
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


def _fail_queue(job_id: str, message: str) -> str:
    holder = {"status": "failed"}

    def mutation(data: dict[str, Any]) -> dict[str, Any]:
        updated = fail_research_job(
            data,
            job_id,
            message,
            retry_delay_minutes=20,
            failure_step="strategy_lab_execution",
        )
        for item in updated.get("research_queue") or []:
            if str(item.get("id") or "") == job_id:
                holder["status"] = str(item.get("status") or "failed")
                break
        return updated

    mutate_remote_library(mutation)
    return holder["status"]


def _saved_cloud_result(checkpoint_store: StrategyStore, run_id: str, ticker: str) -> dict[str, Any]:
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
    return result


def run_once(preferred_job_id: str = "") -> dict[str, Any]:
    remote_job = _claim(str(preferred_job_id or "").strip())
    if remote_job is None:
        return {"status": "idle", "message": "No queued Strategy Lab cloud job is ready."}

    job_id = str(remote_job.get("id") or "")
    payload = dict(remote_job.get("payload") or {})
    run_id = str(payload.get("run_id") or job_id)
    ticker = str(payload.get("ticker") or "").upper()
    main_store = build_main_store()
    checkpoint_store = build_checkpoint_store()
    try:
        # Finish a queue-only recovery from exact durable evidence. A prior
        # successful upload followed by queue-save failure must not recompute.
        saved_result = _saved_cloud_result(checkpoint_store, run_id, ticker)
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
        message = str(exc).strip() or type(exc).__name__
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", default="")
    args = parser.parse_args(argv)
    result = run_once(args.job_id)
    print(result, flush=True)
    # A durable retry is a successfully handled worker cycle; the scheduled
    # workflow will pick it up again after next_attempt_at.
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
