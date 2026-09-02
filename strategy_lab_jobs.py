"""Reconnect-safe background ownership for Very Deep Strategy Lab jobs."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from threading import RLock
import time
from typing import Any, Callable

from research_cached_market import CachedResearchMarket
from strategy_lab_execution import execute_strategy_lab_run
from strategy_lab_persistence import (
    load_latest_strategy_lab_checkpoint,
    save_strategy_lab_checkpoint,
)
from youtube_strategy_engine import AppError, utc_now


MAX_AUTOMATIC_ATTEMPTS = 3
PROGRESS_SAVE_SECONDS = 10.0
PROGRESS_SAVE_DELTA = 0.05

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="strategy-lab")
_LOCK = RLock()
_FUTURES: dict[str, Future[dict[str, Any]]] = {}


def strategy_lab_runs_in_background(search_depth: int) -> bool:
    """Keep the known-good short path synchronous; detach only Very Deep."""

    return int(search_depth or 0) >= 160


def _message_for_failure(exc: BaseException) -> str:
    if isinstance(exc, AppError):
        return str(exc)
    detail = str(exc).strip()
    label = type(exc).__name__
    if detail:
        return f"Strategy Lab run failed during background execution: {label}: {detail}"
    return f"Strategy Lab run aborted during background execution: {label}."


def _run_job(
    run_id: str,
    job: dict[str, Any],
    checkpoint_store: Any,
    market: Any,
    main_store: Any,
    execute: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    ticker = str(job.get("ticker") or "").strip().upper()
    started_at = str(job.get("started_at") or utc_now().isoformat())
    checkpoint = load_latest_strategy_lab_checkpoint(checkpoint_store)
    previous_attempts = (
        int(checkpoint.get("attempt") or 0)
        if str(checkpoint.get("id") or "") == run_id
        else 0
    )
    if previous_attempts >= MAX_AUTOMATIC_ATTEMPTS:
        message = (
            "Very Deep stopped unexpectedly on three separate process attempts. "
            "The app will not restart it again automatically; review the server resource "
            "limit or the last recorded stage, then start a new run."
        )
        try:
            save_strategy_lab_checkpoint(
                checkpoint_store,
                run_id=run_id,
                status="failed",
                ticker=ticker,
                message=message,
                progress=float(checkpoint.get("progress") or 0.0),
                stage="aborted",
                attempt=previous_attempts,
                started_at=started_at,
            )
        except AppError:
            pass
        return {"status": "failed", "message": message}

    attempt = previous_attempts + 1
    last_saved_at = 0.0
    last_saved_fraction = -1.0
    last_saved_stage = ""
    # A restarted process must repeat history download/integrity preparation
    # before it can reuse completed optimizer families, so restart the displayed
    # fraction while retaining the optimizer state itself.
    last_fraction = (
        0.01
        if previous_attempts
        else float(checkpoint.get("progress") or 0.01)
    )
    last_stage = str(checkpoint.get("stage") or "preparing")
    persistence_warnings: list[str] = []

    def save_running(
        fraction: float,
        stage: str,
        message: str,
        *,
        optimizer_state: dict[str, Any] | None = None,
        force: bool = False,
    ) -> None:
        nonlocal last_saved_at, last_saved_fraction, last_saved_stage
        nonlocal last_fraction, last_stage
        last_fraction = max(last_fraction, max(0.0, min(0.999, float(fraction))))
        last_stage = str(stage or last_stage)
        now = time.monotonic()
        should_save = (
            force
            or optimizer_state is not None
            or last_stage != last_saved_stage
            or last_fraction - last_saved_fraction >= PROGRESS_SAVE_DELTA
            or now - last_saved_at >= PROGRESS_SAVE_SECONDS
        )
        if not should_save:
            return
        try:
            save_strategy_lab_checkpoint(
                checkpoint_store,
                run_id=run_id,
                status="running",
                ticker=ticker,
                message=message,
                progress=last_fraction,
                stage=last_stage,
                job=job if not last_saved_stage else None,
                optimizer_state=optimizer_state,
                attempt=attempt,
                started_at=started_at,
            )
        except AppError as exc:
            warning = str(exc)
            if warning not in persistence_warnings:
                persistence_warnings.append(warning)
        last_saved_at = now
        last_saved_fraction = last_fraction
        last_saved_stage = last_stage

    save_running(
        max(0.01, last_fraction),
        "preparing",
        (
            f"Preparing {ticker} Very Deep research"
            if attempt == 1
            else f"Resuming {ticker} Very Deep research after process restart · attempt {attempt}"
        ),
        force=True,
    )

    resume_state = (
        deepcopy(checkpoint.get("optimizer_state"))
        if isinstance(checkpoint.get("optimizer_state"), dict)
        else None
    )

    def on_progress(fraction: float, stage: str, message: str) -> None:
        save_running(fraction, stage, message)

    def on_optimizer_checkpoint(state: dict[str, Any]) -> None:
        completed = len(state.get("completed_strategy_ids") or [])
        save_running(
            last_fraction,
            "optimization",
            f"Saved optimizer checkpoint · {completed} strategy families complete",
            optimizer_state=state,
            force=True,
        )

    cached_market = (
        market
        if isinstance(market, CachedResearchMarket)
        else CachedResearchMarket(market, store=main_store)
    )
    try:
        result = execute(
            job,
            market=cached_market,
            main_store=main_store,
            progress=on_progress,
            optimizer_resume_state=resume_state,
            optimizer_checkpoint=on_optimizer_checkpoint,
        )
    except BaseException as exc:
        message = _message_for_failure(exc)
        try:
            save_strategy_lab_checkpoint(
                checkpoint_store,
                run_id=run_id,
                status="failed",
                ticker=ticker,
                message=message,
                progress=last_fraction,
                stage="failed",
                attempt=attempt,
                started_at=started_at,
            )
        except AppError:
            pass
        return {"status": "failed", "message": message}

    if isinstance(result, dict) and cached_market.research_cache_events:
        result = deepcopy(result)
        events = deepcopy(cached_market.research_cache_events)
        result["research_history_cache"] = {
            "requests": events,
            "request_count": len(events),
            "reused_request_count": sum(
                1 for item in events if bool(item.get("cache_hit"))
            ),
            "network_request_count": sum(
                1 for item in events if bool(item.get("network_request"))
            ),
            "exact_window_only": True,
        }

    completion_warning = ""
    try:
        save_strategy_lab_checkpoint(
            checkpoint_store,
            run_id=run_id,
            status="complete",
            ticker=ticker,
            message="Optimization + validation complete.",
            result=result,
            progress=1.0,
            stage="complete",
            attempt=attempt,
            started_at=started_at,
        )
    except AppError as exc:
        # StrategyStore writes the local checkpoint before attempting its cloud
        # copy. Keep the finished result available to the reconnecting process and
        # make the cloud failure explicit instead of relabeling a good run failed.
        completion_warning = (
            "The result completed and is available locally, but permanent checkpoint "
            f"storage reported: {exc}"
        )
    return {
        "status": "complete",
        "result": result,
        "message": "Optimization + validation complete.",
        "warning": completion_warning,
        "progress_warnings": persistence_warnings,
    }


def submit_strategy_lab_job(
    *,
    run_id: str,
    job: dict[str, Any],
    checkpoint_store: Any,
    market: Any,
    main_store: Any,
    execute: Callable[..., dict[str, Any]] = execute_strategy_lab_run,
) -> bool:
    """Start or attach to a process-owned job; return True only for a new launch."""

    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        raise AppError("Strategy Lab background run_id is required.")
    with _LOCK:
        existing = _FUTURES.get(normalized_run_id)
        if existing is not None and not existing.done():
            return False
        # Bound retained finished outcomes while keeping the current one available
        # to a reconnecting page if its final cloud write produced a warning.
        finished_ids = [key for key, value in _FUTURES.items() if value.done()]
        for previous_id in finished_ids[:-4]:
            _FUTURES.pop(previous_id, None)
        _FUTURES[normalized_run_id] = _EXECUTOR.submit(
            _run_job,
            normalized_run_id,
            deepcopy(job),
            checkpoint_store,
            market,
            main_store,
            execute,
        )
        return True


def strategy_lab_job_active(run_id: str) -> bool:
    with _LOCK:
        future = _FUTURES.get(str(run_id or ""))
        return bool(future is not None and not future.done())


def strategy_lab_job_outcome(run_id: str) -> dict[str, Any]:
    with _LOCK:
        future = _FUTURES.get(str(run_id or ""))
    if future is None:
        return {}
    if not future.done():
        return {"status": "running"}
    try:
        return deepcopy(future.result())
    except BaseException as exc:  # defensive: _run_job normally converts failures
        return {"status": "failed", "message": _message_for_failure(exc)}
