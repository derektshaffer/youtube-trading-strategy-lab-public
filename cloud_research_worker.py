"""Cloud worker entrypoint for persistent Trading Intelligence research.

Designed to run outside Streamlit (for example GitHub Actions or a dedicated
worker service) so research continues while the user's browser/computer is off.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any

# Keep worker invalidation/requeue behavior pinned to the current autonomous
# validation protocol exported by trading_auto_research.
from trading_auto_research import (
    AUTONOMOUS_VALIDATION_METHOD_VERSION,
    invalidate_legacy_autonomous_validations,
    merge_autonomous_research_into_library,
    run_autonomous_research,
)
from predictive_ml_backfill import (
    merge_backfill_result_into_library,
    run_predictive_ml_backfill,
)
from profit_first_queue import (
    profit_first_validation_batch,
)
from stock_strategy_finder import (
    apply_historical_spread_integrity_guard,
    latest_finder_checkpoint,
    merge_finder_checkpoint_into_library,
    merge_finder_report_into_library,
    run_stock_strategy_finder,
    search_profile,
    selected_strategies_for_profile,
    stock_finder_strategy_families,
)
from trading_catalyst_core import (
    enrich_bars_with_point_in_time_catalysts,
    historical_news,
)
from live_learning import (
    DEFAULT_MAX_OBSERVATIONS,
    earliest_pending_observed_at,
    mature_shadow_observations,
    merge_shadow_observations,
    pending_symbols,
)
from predictive_model_monitor import build_shadow_model_monitor
from predictive_model_registry import build_model_registry, ready_shadow_models
from trading_intelligence_core import research_readiness
from trading_research_orchestrator import (
    DEFAULT_GEMINI_BULK_FALLBACK_MODEL,
    DEFAULT_GEMINI_BULK_RESEARCH_MODEL,
    DEFAULT_GEMINI_SPECIALIST_FALLBACK_MODEL,
    DEFAULT_GEMINI_SPECIALIST_MODEL,
    SUPPORTED_RESEARCH_JOB_TYPES,
    AUTONOMOUS_VALIDATION_PRIORITY,
    GeminiResearchRouter,
    apply_specialist_review,
    claim_next_research_job,
    fail_research_job,
    find_external_research_run,
    find_research_hypothesis,
    finish_research_job,
    merge_grounded_research,
    record_worker_run,
    ensure_predictive_ml_backfill_job,
    enqueue_research_job,
    research_queue_status,
    seed_continuous_research_cycle,
    sync_hypothesis_validation_results,
)
from youtube_strategy_engine import (
    AlpacaMarketData,
    AppError,
    DEFAULT_GITHUB_BACKUP_PATH,
    GitHubCloudBackup,
    StrategyStore,
    historical_entry_spread_audit,
    normalize_machine_rules,
    safe_float,
    split_safe_raw_research_rows,
)


# stock_finder is executable here for direct/dedicated deployments, while the
# continuous worker deliberately leaves it for the distributed Finder workflow.
CONTINUOUS_WORKER_JOB_TYPES = SUPPORTED_RESEARCH_JOB_TYPES - {"stock_finder", "strategy_lab"}
CLOUD_FINDER_INTEGRITY_VERSION = 2
DEFAULT_LIVE_LEARNING_OUTBOX_PATH = "trading-intelligence-lab/live_learning_outbox.json"
LIVE_LEARNING_STORAGE_KEY = "live_learning_observations"
LIVE_LEARNING_STATUS_KEY = "live_learning_status"
LIVE_LEARNING_MAX_MATURATION_SYMBOLS = 25


def env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def persist_store(
    store: StrategyStore,
    data: dict[str, Any],
    *,
    cloud_retries: int = 3,
) -> dict[str, Any]:
    """Persist once locally, then retry cloud-only sync on transient failures.

    A completed computation must not be executed again merely because the first
    cloud write had a temporary network/provider failure.
    """
    try:
        return store.save(data)
    except AppError as exc:
        if "Saved locally, but permanent cloud backup failed:" not in str(exc):
            raise
        last_error: Exception = exc

    for attempt in range(max(1, int(cloud_retries))):
        time.sleep(min(8.0, 2.0 ** attempt))
        try:
            return store.sync_cloud_backup()
        except AppError as exc:
            last_error = exc
    raise AppError(
        f"Research result is saved locally but cloud persistence still failed after retries: {last_error}"
    ) from last_error


def build_store() -> StrategyStore:
    repository = env("GITHUB_BACKUP_REPOSITORY")
    token = env("GITHUB_BACKUP_TOKEN")
    if not repository or not token:
        raise AppError(
            "Cloud research needs GITHUB_BACKUP_REPOSITORY and GITHUB_BACKUP_TOKEN "
            "so work is durable across worker runs."
        )
    cloud = GitHubCloudBackup(
        repository,
        token,
        branch=env("GITHUB_BACKUP_BRANCH"),
        path=env("GITHUB_BACKUP_PATH", DEFAULT_GITHUB_BACKUP_PATH),
    )
    return StrategyStore(cloud_backup=cloud)


def build_live_learning_outbox_store() -> StrategyStore:
    repository = env("GITHUB_BACKUP_REPOSITORY")
    token = env("GITHUB_BACKUP_TOKEN")
    if not repository or not token:
        raise AppError(
            "Live-learning outbox needs GITHUB_BACKUP_REPOSITORY and GITHUB_BACKUP_TOKEN."
        )
    cloud = GitHubCloudBackup(
        repository,
        token,
        branch=env("GITHUB_BACKUP_BRANCH"),
        path=env(
            "TRADING_INTELLIGENCE_LIVE_LEARNING_OUTBOX_PATH",
            DEFAULT_LIVE_LEARNING_OUTBOX_PATH,
        ),
    )
    return StrategyStore(
        directory=".cloud_live_learning_outbox",
        cloud_backup=cloud,
    )


def drain_live_learning_outbox(
    store: StrategyStore,
    outbox_store: StrategyStore,
) -> dict[str, Any]:
    """Merge queued live observations into the main library and mature them off-page."""
    outbox = outbox_store.load_latest()
    outbox_system = (
        dict(outbox.get("research_system") or {})
        if isinstance(outbox.get("research_system"), dict)
        else {}
    )
    incoming = [
        dict(item)
        for item in outbox_system.get(LIVE_LEARNING_STORAGE_KEY) or []
        if isinstance(item, dict)
    ]
    if not incoming:
        return {"queued": 0, "merged": 0, "matured": 0}

    now = datetime.now(timezone.utc)
    data = store.load_latest()
    research_system = (
        dict(data.get("research_system") or {})
        if isinstance(data.get("research_system"), dict)
        else {}
    )
    existing = [
        dict(item)
        for item in research_system.get(LIVE_LEARNING_STORAGE_KEY) or []
        if isinstance(item, dict)
    ]
    combined = merge_shadow_observations(
        existing,
        incoming,
        max_records=DEFAULT_MAX_OBSERVATIONS,
    )

    maturation_summary = {
        "updated": 0,
        "completed": 0,
        "partial": 0,
        "pending": 0,
    }
    scoped_pending = pending_symbols(combined)[:LIVE_LEARNING_MAX_MATURATION_SYMBOLS]
    if scoped_pending:
        earliest = earliest_pending_observed_at(
            combined,
            only_symbols=scoped_pending,
        )
        if earliest is not None:
            history_start = max(
                earliest - timedelta(minutes=2),
                now - timedelta(days=7),
            )
            try:
                market = build_market()
                future_bars = market.bars(
                    scoped_pending,
                    start=history_start,
                    end=now,
                    timeframe="1Min",
                    feed=market.live_feed,
                    adjustment="raw",
                    max_pages=60,
                )
                combined, maturation_summary = mature_shadow_observations(
                    combined,
                    future_bars,
                    now=now,
                    only_symbols=scoped_pending,
                )
            except AppError as exc:
                print(
                    f"Live-learning maturation deferred: {exc}",
                    file=sys.stderr,
                    flush=True,
                )

    models = ready_shadow_models(data.get("predictive_ml_runs") or [])
    model_lookup = {
        str(model.get("id") or ""): model
        for model in models
        if str(model.get("id") or "").strip()
    }
    model_monitor = build_shadow_model_monitor(
        combined,
        model_lookup=model_lookup,
    )
    previous_registry = (
        research_system.get("predictive_model_registry")
        if isinstance(research_system.get("predictive_model_registry"), dict)
        else {}
    )
    model_registry = build_model_registry(
        models,
        model_monitor,
        previous=previous_registry,
    )

    counts = {"complete": 0, "partial": 0, "pending": 0}
    for item in combined:
        status = str(item.get("outcome_status") or "PENDING").strip().lower()
        if status in counts:
            counts[status] += 1
        else:
            counts["pending"] += 1

    research_system[LIVE_LEARNING_STORAGE_KEY] = combined
    research_system["predictive_model_monitor"] = model_monitor
    research_system["predictive_model_registry"] = model_registry
    research_system[LIVE_LEARNING_STATUS_KEY] = {
        "last_logged_at": now.isoformat(),
        "last_source": "cloud_live_learning_outbox",
        "last_logged": len(incoming),
        "last_matured": int(maturation_summary.get("updated") or 0),
        "total": len(combined),
        **counts,
        "horizons_minutes": [5, 15, 30, 60],
        "research_only": True,
        "affects_live_ranking": False,
        "affects_execution": False,
        "champion_model_id": model_registry.get("champion_model_id"),
        "model_registry_status": model_registry.get("status"),
    }
    data["research_system"] = research_system
    persist_store(store, data)

    processed_ids = {
        str(item.get("id") or "").strip()
        for item in incoming
        if str(item.get("id") or "").strip()
    }
    latest_outbox = outbox_store.load_latest()
    latest_system = (
        dict(latest_outbox.get("research_system") or {})
        if isinstance(latest_outbox.get("research_system"), dict)
        else {}
    )
    remaining = [
        dict(item)
        for item in latest_system.get(LIVE_LEARNING_STORAGE_KEY) or []
        if isinstance(item, dict)
        and str(item.get("id") or "").strip() not in processed_ids
    ]
    latest_system[LIVE_LEARNING_STORAGE_KEY] = remaining
    latest_system["live_learning_outbox_status"] = {
        "last_drained_at": now.isoformat(),
        "last_drained": len(processed_ids),
        "remaining": len(remaining),
        "research_only": True,
    }
    latest_outbox["research_system"] = latest_system
    persist_store(outbox_store, latest_outbox)

    return {
        "queued": len(incoming),
        "merged": len(incoming),
        "matured": int(maturation_summary.get("updated") or 0),
        "remaining": len(remaining),
    }


def build_router() -> GeminiResearchRouter:
    return GeminiResearchRouter(
        env("GEMINI_API_KEY"),
        paid_api_key=env("GEMINI_PAID_API_KEY"),
        bulk_model=env("GEMINI_RESEARCH_BULK_MODEL", DEFAULT_GEMINI_BULK_RESEARCH_MODEL),
        bulk_fallback_model=env(
            "GEMINI_RESEARCH_BULK_FALLBACK_MODEL",
            DEFAULT_GEMINI_BULK_FALLBACK_MODEL,
        ),
        specialist_model=env(
            "GEMINI_RESEARCH_SPECIALIST_MODEL",
            DEFAULT_GEMINI_SPECIALIST_MODEL,
        ),
        specialist_fallback_model=env(
            "GEMINI_RESEARCH_SPECIALIST_FALLBACK_MODEL",
            DEFAULT_GEMINI_SPECIALIST_FALLBACK_MODEL,
        ),
    )


def build_market() -> AlpacaMarketData:
    return AlpacaMarketData(
        env("ALPACA_API_KEY"),
        env("ALPACA_SECRET_KEY"),
        env("ALPACA_LIVE_FEED", "iex"),
        env("ALPACA_HISTORICAL_FEED", "sip"),
    )


def _pending_web_strategies(data: dict[str, Any], maximum: int = 3) -> list[dict[str, Any]]:
    candidates = []
    now = datetime.now(timezone.utc)
    for item in data.get("strategies") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("source_type") or "") != "autonomous_web_research":
            continue
        if str(item.get("validation_status") or "") == "validated":
            continue
        if research_readiness(item).get("label") != "ready_for_backtest":
            continue
        last = item.get("last_autonomous_research")
        if isinstance(last, dict):
            if bool(last.get("retryable")) and last.get("retry_after"):
                try:
                    retry_after = datetime.fromisoformat(
                        str(last.get("retry_after") or "").replace("Z", "+00:00")
                    )
                    if retry_after.tzinfo is None:
                        retry_after = retry_after.replace(tzinfo=timezone.utc)
                except ValueError:
                    retry_after = None
                if retry_after is not None and retry_after > now:
                    continue
            last_status = str(last.get("validation_status") or "")
            terminal = {
                "validated",
                "research_only",
                "validation_failed",
                "insufficient_data",
                "untestable",
            }
            if last_status in terminal and not bool(last.get("retryable")):
                # Avoid repeatedly burning compute on the same hypothesis until
                # materially changed rules/evidence produce a new strategy id.
                continue
        candidates.append(dict(item))
    candidates.sort(
        key=lambda item: (
            float(item.get("research_source_quality_score") or 0),
            float(item.get("confidence") or 0),
        ),
        reverse=True,
    )
    return candidates[: max(1, int(maximum))]


def _targeted_validation_strategies(
    data: dict[str, Any],
    strategy_ids: list[str],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Resolve explicitly requested strategies without restricting their source type."""
    requested = list(
        dict.fromkeys(
            str(strategy_id or "").strip()
            for strategy_id in strategy_ids
            if str(strategy_id or "").strip()
        )
    )
    by_id = {
        str(item.get("id") or ""): item
        for item in data.get("strategies") or []
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    candidates: list[dict[str, Any]] = []
    missing: list[str] = []
    blocked: list[str] = []
    for strategy_id in requested:
        item = by_id.get(strategy_id)
        if item is None:
            missing.append(strategy_id)
            continue
        readiness = research_readiness(item)
        if readiness.get("label") != "ready_for_backtest":
            blocked.append(
                f"{strategy_id}: {readiness.get('label') or 'not ready for backtest'}"
            )
            continue
        candidates.append(dict(item))
    return candidates, missing, blocked


def _target_strategy_ids(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("strategy_ids")
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, list):
        return []
    return list(
        dict.fromkeys(
            str(value or "").strip()
            for value in raw
            if str(value or "").strip()
        )
    )


def close_blocked_targeted_validation_jobs(
    data: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Close queued targeted jobs that can no longer pass the fidelity gate."""
    result = dict(data or {})
    queue: list[dict[str, Any]] = []
    closed = 0
    now_text = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for raw in result.get("research_queue") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        target_ids = _target_strategy_ids(payload)
        if (
            str(item.get("type") or "") == "autonomous_validation"
            and str(item.get("status") or "") in {"queued", "retry"}
            and target_ids
        ):
            _, missing, blocked = _targeted_validation_strategies(result, target_ids)
            if missing or blocked:
                reasons = [
                    *(f"missing strategy {strategy_id}" for strategy_id in missing),
                    *(f"blocked strategy {reason}" for reason in blocked),
                ]
                item["status"] = "complete"
                item["updated_at"] = now_text
                item["completed_at"] = now_text
                item["next_attempt_at"] = None
                item["last_error"] = None
                item["failure_step"] = None
                item["worker_id"] = None
                item["result_ref"] = "blocked-by-strategy-fidelity"
                item["status_message"] = (
                    "Targeted validation closed without spending compute because "
                    + "; ".join(reasons)
                )
                closed += 1
        queue.append(item)
    result["research_queue"] = queue
    return result, closed


def _profit_first_queue_status(data: dict[str, Any]) -> dict[str, Any]:
    research_system = (
        data.get("research_system")
        if isinstance(data.get("research_system"), dict)
        else {}
    )
    status = research_system.get("profit_first_validation_queue")
    return dict(status) if isinstance(status, dict) else {}


def _set_profit_first_queue_status(
    data: dict[str, Any],
    ranking: dict[str, Any],
) -> dict[str, Any]:
    """Persist a fresh semantic queue snapshot without timestamp-only churn."""
    result = dict(data or {})
    research_system = (
        dict(result.get("research_system") or {})
        if isinstance(result.get("research_system"), dict)
        else {}
    )
    previous = (
        dict(research_system.get("profit_first_validation_queue") or {})
        if isinstance(research_system.get("profit_first_validation_queue"), dict)
        else {}
    )
    next_status = {
        **ranking,
        "validation_method_version": AUTONOMOUS_VALIDATION_METHOD_VERSION,
    }
    previous_semantic = {
        key: value
        for key, value in previous.items()
        if key != "updated_at"
    }
    if previous_semantic == next_status and previous.get("updated_at"):
        next_status["updated_at"] = previous.get("updated_at")
    else:
        next_status["updated_at"] = datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    research_system["profit_first_validation_queue"] = next_status
    result["research_system"] = research_system
    return result


def refresh_automatic_profit_first_validation_job(
    store: StrategyStore,
    data: dict[str, Any],
    *,
    maximum_candidates: int = 2,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    """Re-rank Profit First and persist only when its queue state actually changes."""
    before = _profit_first_queue_status(data)
    updated, job, status = ensure_automatic_profit_first_validation_job(
        data,
        maximum_candidates=maximum_candidates,
    )
    after = _profit_first_queue_status(updated)
    if job is not None or before != after:
        persist_store(store, updated)
    return updated, job, status


def ensure_automatic_profit_first_validation_job(
    data: dict[str, Any],
    *,
    maximum_candidates: int = 2,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    """Queue the strongest testable unproven candidates for strict validation."""
    result = dict(data or {})
    ranking = profit_first_validation_batch(
        result,
        maximum_candidates=maximum_candidates,
    )
    queue_status = str(ranking.get("queue_status") or "")
    if queue_status == "active":
        result = _set_profit_first_queue_status(result, ranking)
        return result, None, ranking

    if queue_status == "no-eligible-candidates":
        result = _set_profit_first_queue_status(result, ranking)
        return result, None, ranking

    if queue_status == "already-attempted":
        result = _set_profit_first_queue_status(result, ranking)
        return result, None, ranking

    strategy_ids = list(ranking.get("strategy_ids") or [])
    result, job = enqueue_research_job(
        result,
        "autonomous_validation",
        dict(ranking.get("payload") or {}),
        priority=99,
        dedupe_key=str(ranking.get("dedupe_key") or ""),
        max_attempts=2,
    )
    ranking = {
        **ranking,
        "queue_status": "queued" if job else "deduped",
        "queued_job_id": (job or {}).get("id"),
        "queued_strategy_ids": strategy_ids,
    }
    result = _set_profit_first_queue_status(result, ranking)
    return result, job, ranking


def close_empty_autonomous_validation_jobs(
    data: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Close queued/retry validation jobs when nothing testable remains."""
    if _pending_web_strategies(data, maximum=1):
        return data, 0

    result = dict(data or {})
    queue = []
    closed = 0
    now_text = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for raw in result.get("research_queue") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        targeted = bool(_target_strategy_ids(payload))
        if (
            str(item.get("type") or "") == "autonomous_validation"
            and str(item.get("status") or "") in {"queued", "retry"}
            and not targeted
        ):
            item["status"] = "complete"
            item["updated_at"] = now_text
            item["completed_at"] = now_text
            item["next_attempt_at"] = None
            item["last_error"] = None
            item["failure_step"] = None
            item["status_message"] = (
                "Validation queue closed cleanly because no machine-testable "
                "strategies remain."
            )
            item["result_ref"] = "no-pending-validation"
            item["worker_id"] = None
            closed += 1
        queue.append(item)
    result["research_queue"] = queue
    return result, closed


def execute_job(
    store: StrategyStore,
    router: GeminiResearchRouter,
    job: dict[str, Any],
    worker_id: str,
) -> str:
    job_type = str(job.get("type") or "")
    payload = dict(job.get("payload") or {})
    if job_type not in SUPPORTED_RESEARCH_JOB_TYPES:
        raise AppError(f"Unknown cloud research job type: {job_type or 'blank'}")

    if job_type == "stock_finder":
        symbol = str(payload.get("symbol") or "").strip().upper()
        profile_name = str(payload.get("profile") or "Deep").strip()
        if not symbol:
            raise AppError("Cloud Stock Strategy Finder job is missing a symbol.")
        profile = search_profile(profile_name)

        latest = store.load_latest()
        strategies = stock_finder_strategy_families(
            list(latest.get("strategies") or [])
        )
        selected, skipped = selected_strategies_for_profile(
            strategies,
            symbol,
            profile,
        )
        if not selected:
            raise AppError(
                f"No machine-testable long strategy families are available for {symbol}."
            )

        market = build_market()
        previous_checkpoint = latest_finder_checkpoint(
            latest,
            symbol,
            profile.name,
        )
        previous_engine_state = dict((previous_checkpoint or {}).get("engine_state") or {})
        previous_integrity = (
            (previous_checkpoint or {}).get("market_data_integrity")
            if isinstance((previous_checkpoint or {}).get("market_data_integrity"), dict)
            else {}
        )
        resumable = bool(
            int((previous_checkpoint or {}).get("integrity_version") or 0)
            == CLOUD_FINDER_INTEGRITY_VERSION
            and str(previous_integrity.get("mode") or "") in {
                "raw_prices",
                "raw_prices_post_latest_split",
            }
            and previous_engine_state.get("timeframes")
            and str((previous_checkpoint or {}).get("status") or "").lower()
            in {"running", "failed", "interrupted"}
        )
        saved_start = str((previous_checkpoint or {}).get("research_start") or "").strip()
        saved_end = str((previous_checkpoint or {}).get("research_end") or "").strip()
        if resumable and saved_start and saved_end:
            try:
                start = datetime.fromisoformat(saved_start.replace("Z", "+00:00"))
                end = datetime.fromisoformat(saved_end.replace("Z", "+00:00"))
            except ValueError:
                resumable = False
        if not resumable:
            end = datetime.now(timezone.utc)
            if market.historical_feed == "sip" and market.live_feed != "sip":
                end -= timedelta(minutes=16)
            start = end - timedelta(days=profile.history_days)

        now_text = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        checkpoint_record = {
            "id": (
                str((previous_checkpoint or {}).get("id") or "")
                if resumable
                else "cloud-finder-" + str(job.get("id") or "")
            ),
            "symbol": symbol,
            "profile": profile.name,
            "status": "running",
            "started_at": (
                str((previous_checkpoint or {}).get("started_at") or now_text)
                if resumable else now_text
            ),
            "updated_at": now_text,
            "progress": float((previous_checkpoint or {}).get("progress") or 0.0),
            "message": (
                f"Cloud worker resuming {symbol} {profile.name} research"
                if resumable
                else f"Cloud worker starting {symbol} {profile.name} research"
            ),
            "research_start": start.isoformat(),
            "research_end": end.isoformat(),
            "engine_state": previous_engine_state if resumable else {},
            "integrity_version": CLOUD_FINDER_INTEGRITY_VERSION,
            "market_data_integrity": (
                previous_integrity if resumable else {}
            ),
            "last_error": None,
        }
        latest = merge_finder_checkpoint_into_library(latest, checkpoint_record)
        persist_store(store, latest)
        checkpoint_counter = [0]
        checkpoint_last_save = [time.monotonic()]

        def persist_checkpoint(*, force: bool = False) -> None:
            if not force:
                checkpoint_counter[0] += 1
                if checkpoint_counter[0] % 4 != 0 and time.monotonic() - checkpoint_last_save[0] < 90:
                    return
            checkpoint_record["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            current = store.load_latest()
            current = merge_finder_checkpoint_into_library(current, checkpoint_record)
            persist_store(store, current)
            checkpoint_last_save[0] = time.monotonic()

        def history_progress(page: int) -> None:
            if page == 1 or page % 10 == 0:
                print(
                    f"[stock-finder] {symbol} history page {page}",
                    flush=True,
                )

        rows_by_symbol = market.bars(
            [symbol],
            start=start,
            end=end,
            timeframe="1Min",
            adjustment="raw",
            max_pages=300,
            progress=history_progress,
        )
        rows = list(rows_by_symbol.get(symbol) or [])
        if not rows:
            raise AppError(f"No historical bars were returned for {symbol}.")
        split_actions = market.research_reset_actions(
            [symbol],
            start=start,
            end=end,
        )
        rows, split_guard = split_safe_raw_research_rows(
            rows,
            split_actions,
            symbol,
        )
        if not rows:
            raise AppError(f"No split-safe raw-price history remained for {symbol}.")
        checkpoint_record["market_data_integrity"] = split_guard
        persist_checkpoint(force=True)

        needs_catalyst_history = any(
            bool(normalize_machine_rules(item.get("machine_rules")).get("catalyst_required"))
            for item in selected
        )
        if needs_catalyst_history:
            print(
                f"[stock-finder] loading point-in-time catalyst history for {symbol}",
                flush=True,
            )
            articles = historical_news(
                market,
                [symbol],
                start=start - timedelta(hours=24),
                end=end,
                max_pages=100,
            )
            rows, _ = enrich_bars_with_point_in_time_catalysts(
                rows,
                articles,
                lookback_hours=24.0,
            )

        def finder_progress(completed: int, total: int, message: str) -> None:
            portion = completed / max(1, total)
            checkpoint_record["progress"] = max(
                float(checkpoint_record.get("progress") or 0.0),
                min(0.99, portion),
            )
            checkpoint_record["message"] = message
            if completed == total or completed % max(1, total // 100) == 0:
                pct = portion * 100.0
                print(
                    f"[stock-finder] {symbol} {profile.name} {pct:.1f}% · {message}",
                    flush=True,
                )

        def finder_checkpoint(engine_state: dict[str, Any]) -> None:
            checkpoint_record["engine_state"] = engine_state
            persist_checkpoint()

        parallel_workers = max(
            1,
            min(8, int(env("RESEARCH_PARALLEL_WORKERS", "4") or 4)),
        )
        # Resume uses the sequential checkpoint-aware path. A fresh cloud run can
        # spread independent strategy families across CPU cores.
        workers_for_run = 1 if resumable else parallel_workers
        print(
            f"[stock-finder] using {workers_for_run} CPU worker(s) across independent strategy families"
            + (" (checkpoint resume mode)" if resumable else ""),
            flush=True,
        )
        report = run_stock_strategy_finder(
            rows,
            selected,
            symbol,
            profile_name=profile.name,
            progress=finder_progress,
            resume_state=previous_engine_state if resumable else None,
            checkpoint=finder_checkpoint,
            parallel_workers=workers_for_run,
        )
        report["market_data_integrity"] = split_guard

        optimization_for_spread = report.get("optimization") or {}
        winner_for_spread = optimization_for_spread.get("winner") or {}
        winning_backtest = optimization_for_spread.get("winning_backtest") or {}
        optimized_settings = winner_for_spread.get("optimized_backtest_settings") or {}
        optimizer_settings = optimization_for_spread.get("optimization_settings") or {}
        multipliers = [
            safe_float(value)
            for value in (
                optimizer_settings.get("execution_sensitivity_multipliers")
                or (1.25, 1.5, 1.75, 2.0)
            )
        ]
        spread_audit = historical_entry_spread_audit(
            market,
            symbol,
            list(winning_backtest.get("trades") or []),
            list(optimization_for_spread.get("holdout_sessions") or []),
            modeled_spread_bps=(
                safe_float(optimized_settings.get("spread_bps"), 12.0)
                or 12.0
            ),
            maximum_stress_multiplier=max(
                [value for value in multipliers if value is not None] or [2.0]
            ),
        )
        report = apply_historical_spread_integrity_guard(report, spread_audit)

        checkpoint_record["status"] = "complete"
        checkpoint_record["progress"] = 1.0
        checkpoint_record["message"] = f"{symbol} {profile.name} cloud research complete"
        checkpoint_record["completed_at"] = str(
            report.get("generated_at")
            or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
        checkpoint_record["last_error"] = None
        persist_checkpoint(force=True)
        latest = store.load_latest()
        latest = merge_finder_report_into_library(latest, report)
        result_ref = (
            f"finder:{symbol}:{profile.name}:"
            f"{report.get('generated_at') or datetime.now(timezone.utc).isoformat()}"
        )
        latest = finish_research_job(
            latest,
            str(job.get("id") or ""),
            result_ref=result_ref,
        )
        latest = record_worker_run(
            latest,
            worker_id=worker_id,
            job_id=str(job.get("id") or ""),
            job_type=job_type,
            status="complete",
            detail=(
                f"{symbol} {profile.name} Finder completed with "
                f"{int(report.get('unique_configurations_tested') or 0):,} unique configurations."
            ),
        )
        persist_store(store, latest)
        return result_ref

    if job_type == "predictive_ml_backfill":
        latest = store.load_latest()
        research_system = (
            dict(latest.get("research_system") or {})
            if isinstance(latest.get("research_system"), dict)
            else {}
        )
        research_system["predictive_ml_backfill_status"] = {
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "job_id": str(job.get("id") or ""),
            "research_only": True,
            "affects_live_ranking": False,
            "affects_execution": False,
        }
        latest["research_system"] = research_system
        persist_store(store, latest)

        configured_symbols = env("PREDICTIVE_ML_BACKFILL_SYMBOLS")
        worker_payload = dict(payload)
        if configured_symbols:
            worker_payload["symbols"] = configured_symbols
        if env("PREDICTIVE_ML_BACKFILL_TRADING_DAYS"):
            worker_payload["trading_days"] = int(env("PREDICTIVE_ML_BACKFILL_TRADING_DAYS"))
        if env("PREDICTIVE_ML_BACKFILL_MAX_SYMBOLS"):
            worker_payload["max_symbols"] = int(env("PREDICTIVE_ML_BACKFILL_MAX_SYMBOLS"))
        if env("PREDICTIVE_ML_BACKFILL_HORIZON"):
            worker_payload["horizon"] = int(env("PREDICTIVE_ML_BACKFILL_HORIZON"))
        if env("PREDICTIVE_ML_BACKFILL_HORIZONS"):
            worker_payload["horizons"] = env("PREDICTIVE_ML_BACKFILL_HORIZONS")
        if env("PREDICTIVE_ML_SIMILARITY_MAX_SYMBOLS"):
            worker_payload["similarity_max_symbols"] = int(
                env("PREDICTIVE_ML_SIMILARITY_MAX_SYMBOLS")
            )
        if env("PREDICTIVE_ML_TICKER_SPECIFIC_MAX_SYMBOLS"):
            worker_payload["ticker_specific_max_symbols"] = int(
                env("PREDICTIVE_ML_TICKER_SPECIFIC_MAX_SYMBOLS")
            )
        if env("PREDICTIVE_ML_BACKFILL_STRIDE"):
            worker_payload["observation_stride_bars"] = int(
                env("PREDICTIVE_ML_BACKFILL_STRIDE")
            )

        market = build_market()

        def ml_progress(message: str) -> None:
            print(f"[predictive-ml] {message}", flush=True)

        result = run_predictive_ml_backfill(
            market,
            latest,
            payload=worker_payload,
            progress=ml_progress,
        )
        latest = store.load_latest()
        latest = merge_backfill_result_into_library(latest, result)
        result_ref = f"predictive-ml:{result.get('id')}"
        latest = finish_research_job(
            latest,
            str(job.get("id") or ""),
            result_ref=result_ref,
        )
        model = (
            result.get("probability_model")
            if isinstance(result.get("probability_model"), dict)
            else {}
        )
        latest = record_worker_run(
            latest,
            worker_id=worker_id,
            job_id=str(job.get("id") or ""),
            job_type=job_type,
            status="complete",
            detail=(
                f"Automatic ML backfill completed with "
                f"{int((result.get('dataset_summary') or {}).get('row_count') or 0):,} labeled rows across "
                f"{len(result.get('symbols') or [])} stocks and "
                f"{len(result.get('horizons') or [result.get('horizon')])} horizon(s); "
                f"shadow model status {model.get('status') or 'unknown'}; "
                f"ticker-specific validation {str((result.get('ticker_specific') or {}).get('status') or 'unknown')}; "
                f"similarity validation {str((result.get('similarity_validation') or {}).get('status') or 'unknown')}; "
                f"learning router {str((result.get('stock_learning_router') or {}).get('status') or 'unknown')}."
            ),
        )
        persist_store(store, latest)
        return result_ref

    if job_type == "web_research":
        topic = str(payload.get("topic") or "").strip()
        research = router.grounded_research(
            topic,
            existing_context=str(payload.get("existing_context") or ""),
        )
        latest = store.load_latest()
        latest, run_id, hypothesis_ids = merge_grounded_research(
            latest,
            research,
            topic=topic,
            origin_job_id=str(job.get("id") or ""),
        )
        latest = finish_research_job(latest, str(job.get("id") or ""), result_ref=run_id)
        latest = record_worker_run(
            latest,
            worker_id=worker_id,
            job_id=str(job.get("id") or ""),
            job_type=job_type,
            status="complete",
            detail=f"Grounded research saved with {len(hypothesis_ids)} hypotheses.",
        )
        persist_store(store, latest)
        return run_id

    if job_type == "specialist_review":
        hypothesis_id = str(payload.get("hypothesis_id") or "")
        run_id = str(payload.get("research_run_id") or "")
        latest = store.load_latest()
        hypothesis = find_research_hypothesis(latest, hypothesis_id)
        if hypothesis is None:
            raise AppError("The queued specialist hypothesis no longer exists.")
        research_run = find_external_research_run(latest, run_id)
        review = router.specialist_review(hypothesis, research_run=research_run)
        latest, strategy_id = apply_specialist_review(latest, hypothesis_id, review)
        result_ref = strategy_id or hypothesis_id
        latest = finish_research_job(
            latest,
            str(job.get("id") or ""),
            result_ref=result_ref,
        )
        latest = record_worker_run(
            latest,
            worker_id=worker_id,
            job_id=str(job.get("id") or ""),
            job_type=job_type,
            status="complete",
            detail=f"Specialist decision: {review.get('decision')}.",
        )
        persist_store(store, latest)
        return result_ref

    if job_type == "autonomous_validation":
        latest = store.load_latest()
        target_strategy_ids = _target_strategy_ids(payload)
        targeted = bool(target_strategy_ids)
        if targeted:
            candidates, missing_targets, blocked_targets = _targeted_validation_strategies(
                latest,
                target_strategy_ids,
            )
            problems = [
                *(f"missing strategy {strategy_id}" for strategy_id in missing_targets),
                *(f"blocked strategy {reason}" for reason in blocked_targets),
            ]
            if problems:
                raise AppError(
                    "Targeted autonomous validation could not start: " + "; ".join(problems)
                )
        else:
            candidates = _pending_web_strategies(
                latest,
                maximum=int(env("RESEARCH_VALIDATION_BATCH_SIZE", "3") or 3),
            )
        if not candidates:
            latest = finish_research_job(
                latest,
                str(job.get("id") or ""),
                result_ref="no-pending-validation",
            )
            latest = record_worker_run(
                latest,
                worker_id=worker_id,
                job_id=str(job.get("id") or ""),
                job_type=job_type,
                status="complete",
                detail=(
                    "No targeted strategies were eligible for deterministic validation."
                    if targeted
                    else "No new web-research strategies were awaiting deterministic validation."
                ),
            )
            persist_store(store, latest)
            return "no-pending-validation"

        market = build_market()

        def progress(message: str) -> None:
            print(f"[validation] {message}", flush=True)

        report = run_autonomous_research(
            market,
            candidates,
            universe_sample_size=int(env("RESEARCH_VALIDATION_UNIVERSE_SIZE", "250") or 250),
            deep_strategy_limit=(
                len(candidates)
                if targeted
                else min(
                    len(candidates),
                    int(env("RESEARCH_VALIDATION_DEEP_LIMIT", "3") or 3),
                )
            ),
            symbols_per_strategy=int(env("RESEARCH_VALIDATION_SYMBOLS_PER_STRATEGY", "6") or 6),
            parallel_workers=max(
                1,
                min(
                    3,
                    int(env("RESEARCH_VALIDATION_PARALLEL_WORKERS", "2") or 2),
                ),
            ),
            progress=progress,
        )
        latest = store.load_latest()
        latest = merge_autonomous_research_into_library(latest, report)
        latest = sync_hypothesis_validation_results(latest, report)
        result_ref = f"autonomous:{report.get('generated_at')}"
        latest = finish_research_job(
            latest,
            str(job.get("id") or ""),
            result_ref=result_ref,
        )
        continuation_queued = False
        if not targeted:
            remaining = _pending_web_strategies(latest, maximum=1)
            if remaining:
                latest, continuation = enqueue_research_job(
                    latest,
                    "autonomous_validation",
                    {"origin": "validation_batch_continuation"},
                    priority=AUTONOMOUS_VALIDATION_PRIORITY,
                    dedupe_key="autonomous_validation:pending_web_research",
                )
                continuation_queued = continuation is not None
        latest = record_worker_run(
            latest,
            worker_id=worker_id,
            job_id=str(job.get("id") or ""),
            job_type=job_type,
            status="complete",
            detail=(
                (
                    f"Targeted revalidation tested {int(report.get('deep_strategies_tested') or 0)} "
                    f"strategy candidate(s); {int(report.get('deep_strategies_failed') or 0)} skipped."
                )
                if targeted
                else (
                    f"Validated {int(report.get('deep_strategies_tested') or 0)} hypothesis strategy "
                    f"candidate(s); {int(report.get('deep_strategies_failed') or 0)} skipped; "
                    f"continuation_queued={continuation_queued}."
                )
            ),
        )
        persist_store(store, latest)
        return result_ref

    raise AppError(f"Unknown cloud research job type: {job_type}")



def print_predictive_ml_router_summary(library: dict[str, Any]) -> None:
    """Print a compact latest-run learning-router summary for cloud diagnostics."""
    runs = [
        item
        for item in (library.get("predictive_ml_runs") or [])
        if isinstance(item, dict)
    ]
    if not runs:
        print("[predictive-ml-summary] no saved predictive ML runs.", flush=True)
        return

    runs.sort(
        key=lambda item: str(item.get("completed_at") or ""),
        reverse=True,
    )
    run = runs[0]
    router = (
        run.get("stock_learning_router")
        if isinstance(run.get("stock_learning_router"), dict)
        else {}
    )

    route_counts = (
        dict(router.get("route_counts") or {})
        if isinstance(router.get("route_counts"), dict)
        else {}
    )
    print(
        "[predictive-ml-summary] "
        f"run_id={run.get('id') or 'unknown'} "
        f"suite={int(run.get('model_suite_version') or 0)} "
        f"completed_at={run.get('completed_at') or 'unknown'} "
        f"runtime_seconds={run.get('runtime_seconds') or 'na'} "
        f"feature_workers={run.get('feature_workers') or 'na'} "
        f"validation_workers={run.get('validation_workers') or 'na'} "
        f"router_status={router.get('status') or 'missing'} "
        f"symbols_compared={int(router.get('symbols_compared') or 0)} "
        f"clear_routes={int(router.get('symbols_with_clear_route') or 0)} "
        f"route_counts={route_counts}",
        flush=True,
    )

    def metric(routes: dict[str, Any], route: str, field: str) -> str:
        row = routes.get(route) if isinstance(routes.get(route), dict) else {}
        value = row.get(field)
        if value is None:
            return "na"
        try:
            return f"{float(value):.6f}"
        except (TypeError, ValueError, OverflowError):
            return str(value)

    for raw in router.get("by_symbol") or []:
        if not isinstance(raw, dict):
            continue
        routes = raw.get("routes") if isinstance(raw.get("routes"), dict) else {}
        reason = " ".join(str(raw.get("reason") or "").split())
        print(
            "[predictive-ml-route] "
            f"symbol={raw.get('symbol') or 'unknown'} "
            f"status={raw.get('status') or 'unknown'} "
            f"route_status={raw.get('route_status') or 'unknown'} "
            f"recommended={raw.get('recommended_route') or 'none'} "
            f"provisional={raw.get('provisional_lowest_brier_route') or 'none'} "
            f"paired_oos_rows={int(raw.get('paired_oos_rows') or 0)} "
            f"same_brier={metric(routes, 'same_ticker_history', 'brier_score')} "
            f"similar_brier={metric(routes, 'similarity_weighted_transfer', 'brier_score')} "
            f"broad_brier={metric(routes, 'broad_cross_stock_transfer', 'brier_score')} "
            f"same_auc={metric(routes, 'same_ticker_history', 'roc_auc')} "
            f"similar_auc={metric(routes, 'similarity_weighted_transfer', 'roc_auc')} "
            f"broad_auc={metric(routes, 'broad_cross_stock_transfer', 'roc_auc')} "
            f"reason={reason}",
            flush=True,
        )


def main() -> int:
    store = build_store()
    outbox_store = build_live_learning_outbox_store()
    live_learning = drain_live_learning_outbox(store, outbox_store)
    if live_learning.get("queued"):
        print(
            "Drained live-learning outbox: "
            f"{int(live_learning.get('merged') or 0)} merged, "
            f"{int(live_learning.get('matured') or 0)} matured, "
            f"{int(live_learning.get('remaining') or 0)} remaining.",
            flush=True,
        )
    worker_id = env("RESEARCH_WORKER_ID") or (
        f"{socket.gethostname()}:{os.getpid()}"
    )
    jobs_per_run = max(1, min(12, int(env("RESEARCH_JOBS_PER_RUN", "4") or 4)))

    # Seed once per UTC day. Follow-up questions and specialist reviews add more
    # work automatically, so this is a bounded self-feeding queue rather than an
    # uncontrolled infinite API loop.
    data = store.load_latest()
    data, invalidated = invalidate_legacy_autonomous_validations(data)
    if invalidated:
        persist_store(store, data)
        print(
            f"Marked {invalidated} legacy autonomous validation result(s) for "
            f"method-v{AUTONOMOUS_VALIDATION_METHOD_VERSION} revalidation.",
            flush=True,
        )
        data = store.load_latest()

    data, closed_blocked_targeted_jobs = close_blocked_targeted_validation_jobs(data)
    if closed_blocked_targeted_jobs:
        persist_store(store, data)
        print(
            f"Closed {closed_blocked_targeted_jobs} targeted validation job(s) blocked "
            "by strategy fidelity.",
            flush=True,
        )
        data = store.load_latest()

    data, closed_empty_validation_jobs = close_empty_autonomous_validation_jobs(data)
    if closed_empty_validation_jobs:
        persist_store(store, data)
        print(
            f"Closed {closed_empty_validation_jobs} empty autonomous validation queue job(s).",
            flush=True,
        )
        data = store.load_latest()

    print_predictive_ml_router_summary(data)
    data, seeded = seed_continuous_research_cycle(
        data,
        maximum_topics=int(env("RESEARCH_TOPICS_PER_CYCLE", "10") or 10),
    )
    if seeded:
        persist_store(store, data)
        print(f"Seeded {seeded} autonomous research topics.", flush=True)

    target_ids = [
        part.strip()
        for part in env("RESEARCH_TARGET_REVALIDATION_STRATEGY_IDS").split(",")
        if part.strip()
    ]
    if target_ids:
        data = store.load_latest()
        _, missing_targets, blocked_targets = _targeted_validation_strategies(
            data,
            target_ids,
        )
        if missing_targets or blocked_targets:
            print(
                "Targeted profit-first revalidation was not queued: "
                + "; ".join(
                    [
                        *(f"missing strategy {strategy_id}" for strategy_id in missing_targets),
                        *(f"blocked strategy {reason}" for reason in blocked_targets),
                    ]
                ),
                file=sys.stderr,
                flush=True,
            )
        else:
            dedupe_suffix = hashlib.sha256(
                "|".join(target_ids).encode("utf-8")
            ).hexdigest()[:16]
            data, targeted_job = enqueue_research_job(
                data,
                "autonomous_validation",
                {
                    "origin": "profit_first_revalidation",
                    "strategy_ids": target_ids,
                    "validation_method_version": AUTONOMOUS_VALIDATION_METHOD_VERSION,
                },
                priority=120,
                dedupe_key=f"autonomous_validation:targeted:{dedupe_suffix}",
            )
            if targeted_job:
                persist_store(store, data)
                print(
                    "Queued targeted profit-first revalidation job "
                    f"{targeted_job.get('id')} for {', '.join(target_ids)}.",
                    flush=True,
                )

    data = store.load_latest()
    data, profit_first_job, profit_first_status = refresh_automatic_profit_first_validation_job(
        store,
        data,
        maximum_candidates=int(env("PROFIT_FIRST_VALIDATION_BATCH_SIZE", "2") or 2),
    )
    if profit_first_job:
        print(
            "Queued automatic profit-first validation job "
            f"{profit_first_job.get('id')} for "
            + ", ".join(
                str(value)
                for value in profit_first_status.get("queued_strategy_ids") or []
            )
            + ".",
            flush=True,
        )
    elif str(profit_first_status.get("queue_status") or "") in {
        "no-eligible-candidates",
        "already-attempted",
    }:
        print(
            "Automatic profit-first validator: "
            + str(profit_first_status.get("queue_status") or "idle")
            + ".",
            flush=True,
        )

    # ML bootstrap/retraining has its own freshness clock so a deployment can
    # start it immediately even if today's web-research cycle was already seeded.
    data = store.load_latest()
    data, ml_backfill_job = ensure_predictive_ml_backfill_job(
        data,
        freshness_hours=int(env("PREDICTIVE_ML_BACKFILL_FRESHNESS_HOURS", "20") or 20),
    )
    if ml_backfill_job:
        persist_store(store, data)
        print(
            f"Queued high-priority predictive ML backfill job {ml_backfill_job.get('id')}.",
            flush=True,
        )

    router = build_router()
    completed = 0
    for _ in range(jobs_per_run):
        data = store.load_latest()
        # Re-evaluate Profit First before every worker slot. A completed strict
        # validation can therefore advance immediately to the next candidate
        # batch instead of spending the remaining run on lower-priority work.
        data, profit_first_job, profit_first_status = refresh_automatic_profit_first_validation_job(
            store,
            data,
            maximum_candidates=int(env("PROFIT_FIRST_VALIDATION_BATCH_SIZE", "2") or 2),
        )
        if profit_first_job:
            print(
                "Queued next automatic profit-first validation job "
                f"{profit_first_job.get('id')} for "
                + ", ".join(
                    str(value)
                    for value in profit_first_status.get("queued_strategy_ids") or []
                )
                + ".",
                flush=True,
            )
        data, job = claim_next_research_job(
            data,
            worker_id,
            allowed_types=set(CONTINUOUS_WORKER_JOB_TYPES),
        )
        if job is None:
            print("No research jobs are ready.", flush=True)
            break
        persist_store(store, data)
        job_id = str(job.get("id") or "")
        job_type = str(job.get("type") or "")
        print(f"Running {job_type} job {job_id}…", flush=True)
        try:
            result_ref = execute_job(store, router, job, worker_id)
            completed += 1
            print(f"Completed {job_id}: {result_ref}", flush=True)
        except Exception as exc:
            latest = store.load_latest()
            failure_step = "job_execution"
            if job_type == "stock_finder":
                finder_payload = dict(job.get("payload") or {})
                finder_symbol = str(finder_payload.get("symbol") or "").strip().upper()
                finder_profile = str(finder_payload.get("profile") or "Deep").strip()
                checkpoint = latest_finder_checkpoint(
                    latest,
                    finder_symbol,
                    finder_profile,
                )
                if checkpoint:
                    checkpoint = dict(checkpoint)
                    checkpoint["status"] = "failed"
                    checkpoint["updated_at"] = datetime.now(timezone.utc).isoformat().replace(
                        "+00:00", "Z"
                    )
                    checkpoint["last_error"] = str(exc)[:1800]
                    checkpoint["message"] = f"Cloud Finder failed: {exc}"
                    latest = merge_finder_checkpoint_into_library(latest, checkpoint)
                    failure_step = "stock_finder_execution"
            if job_type == "predictive_ml_backfill":
                research_system = (
                    dict(latest.get("research_system") or {})
                    if isinstance(latest.get("research_system"), dict)
                    else {}
                )
                research_system["predictive_ml_backfill_status"] = {
                    "status": "failed",
                    "failed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "job_id": job_id,
                    "last_error": str(exc)[:1200],
                    "research_only": True,
                    "affects_live_ranking": False,
                    "affects_execution": False,
                }
                latest["research_system"] = research_system
                failure_step = "predictive_ml_backfill"
            latest = fail_research_job(
                latest,
                job_id,
                exc,
                failure_step=failure_step,
            )
            latest = record_worker_run(
                latest,
                worker_id=worker_id,
                job_id=job_id,
                job_type=job_type,
                status="failed",
                detail=str(exc),
            )
            persist_store(store, latest)
            print(f"Failed {job_id}: {exc}", file=sys.stderr, flush=True)

    final = store.load_latest()
    status = research_queue_status(final)
    print(
        f"Worker finished: {completed} completed this run; "
        f"{status['active']} active queued/running/retry job(s) remain.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
