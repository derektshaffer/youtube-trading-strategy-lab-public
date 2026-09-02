"""Stock Strategy Finder publication and result helpers for the desktop cloud bridge."""

from __future__ import annotations

import re
from typing import Any, Mapping

from finder_report_persistence import finder_summary_to_report
from trading_research_orchestrator import enqueue_research_job


DESKTOP_STOCK_FINDER_JOB_TYPE = "strategy.stock_finder"
REMOTE_STOCK_FINDER_TYPE = "stock_finder"
DISTRIBUTED_STOCK_FINDER_WORKFLOW = "distributed-stock-finder.yml"
ACTIVE_REMOTE_STATUSES = frozenset({"queued", "running", "retry", "pending", "retry_wait"})
_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,11}$")


def normalized_finder_request(payload: Mapping[str, Any]) -> tuple[str, str]:
    symbol = str(payload.get("symbol") or "").strip().upper()
    if not _SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError("Stock Finder needs one valid stock ticker.")
    from stock_strategy_finder import search_profile

    profile = search_profile(str(payload.get("profile") or "Deep")).name
    return symbol, profile


def finder_dedupe_key(symbol: str, profile: str) -> str:
    return f"stock-finder:{str(symbol).upper()}:{str(profile)}"


def _payload(item: Mapping[str, Any]) -> dict[str, Any]:
    value = item.get("payload")
    return dict(value) if isinstance(value, Mapping) else {}


def find_finder_remote_item(
    library: Mapping[str, Any],
    *,
    local_job_id: str,
    remote_job_id: str = "",
    dedupe_key: str = "",
) -> dict[str, Any] | None:
    queue = [item for item in library.get("research_queue") or [] if isinstance(item, Mapping)]
    clean_remote = str(remote_job_id or "").strip()
    clean_local = str(local_job_id or "").strip()
    clean_dedupe = str(dedupe_key or "").strip()

    # Exact identifiers first. These remain unambiguous even when later Finder runs
    # reuse the public stock/profile dedupe key.
    for raw in queue:
        if str(raw.get("type") or "") != REMOTE_STOCK_FINDER_TYPE:
            continue
        item = dict(raw)
        if clean_remote and str(item.get("id") or "").strip() == clean_remote:
            return item
        bridge = _payload(item).get("hybrid_cloud_bridge")
        bridge = dict(bridge) if isinstance(bridge, Mapping) else {}
        if clean_local and str(bridge.get("local_job_id") or "").strip() == clean_local:
            return item

    # Dedupe fallback is intentionally restricted to an active remote run. Completed
    # Finder runs can share the same symbol/profile key and must not steal a reconnect.
    if clean_dedupe:
        for raw in queue:
            if str(raw.get("type") or "") != REMOTE_STOCK_FINDER_TYPE:
                continue
            if str(raw.get("dedupe_key") or "").strip() != clean_dedupe:
                continue
            status = str(raw.get("status") or "queued").strip().lower().replace("-", "_")
            if status in ACTIVE_REMOTE_STATUSES:
                return dict(raw)
    return None


def prepare_stock_finder_publication(
    library: dict[str, Any],
    local_job: Any,
) -> tuple[dict[str, Any] | None, bool, dict[str, Any]]:
    symbol, profile = normalized_finder_request(local_job.payload)
    dedupe = finder_dedupe_key(symbol, profile)
    existing = find_finder_remote_item(
        library,
        local_job_id=str(local_job.id),
        dedupe_key=dedupe,
    )
    if existing is not None:
        return existing, False, {
            "queue_status": "active",
            "symbol": symbol,
            "profile": profile,
            "dedupe_key": dedupe,
        }

    remote_payload = {
        "symbol": symbol,
        "profile": profile,
        "requested_from": "Trading Intelligence Desktop",
        "hybrid_cloud_bridge": {
            "version": 1,
            "local_job_id": str(local_job.id),
            "request_fingerprint": str(local_job.request_fingerprint),
        },
    }
    updated, queued = enqueue_research_job(
        library,
        REMOTE_STOCK_FINDER_TYPE,
        remote_payload,
        priority=90 if profile == "Very Deep" else 75,
        dedupe_key=dedupe,
        max_attempts=2,
    )
    if queued is None:
        existing = find_finder_remote_item(
            updated,
            local_job_id=str(local_job.id),
            dedupe_key=dedupe,
        )
        if existing is None:
            return None, False, {
                "queue_status": "invalid-plan",
                "symbol": symbol,
                "profile": profile,
                "dedupe_key": dedupe,
                "bridge_error": "Stock Finder queue deduplication returned no attachable active job.",
            }
        library.clear()
        library.update(updated)
        return existing, False, {
            "queue_status": "active",
            "symbol": symbol,
            "profile": profile,
            "dedupe_key": dedupe,
        }

    library.clear()
    library.update(updated)
    return dict(queued), True, {
        "queue_status": "ready",
        "symbol": symbol,
        "profile": profile,
        "dedupe_key": dedupe,
    }


def finder_distributed_progress(item: Mapping[str, Any]) -> float | None:
    payload = _payload(item)
    value = payload.get("distributed_progress")
    if value is None:
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError, OverflowError):
        return None


def finder_distributed_stage(item: Mapping[str, Any]) -> str:
    return str(_payload(item).get("distributed_stage") or "").strip().lower()


def finder_distributed_message(item: Mapping[str, Any]) -> str:
    return " ".join(str(_payload(item).get("distributed_message") or "").split())[:500]


def finder_link_metadata(item: Mapping[str, Any], local_job: Any) -> dict[str, Any]:
    payload = _payload(item)
    symbol = str(payload.get("symbol") or local_job.payload.get("symbol") or "").strip().upper()
    profile = str(payload.get("profile") or local_job.payload.get("profile") or "").strip()
    return {
        "job_type": str(local_job.job_type),
        "symbol": symbol,
        "profile": profile,
        "distributed_run_id": str(payload.get("distributed_run_id") or ""),
        "distributed_shards_total": int(payload.get("distributed_shards_total") or 0),
        "distributed_shards_completed": list(payload.get("distributed_shards_completed") or []),
        "distributed_message": finder_distributed_message(item),
    }


def _expected_generated_at(item: Mapping[str, Any], symbol: str, profile: str) -> str:
    result_ref = str(item.get("result_ref") or "").strip()
    prefix = f"distributed-finder:{symbol}:{profile}:"
    if result_ref.startswith(prefix):
        return result_ref[len(prefix) :].strip()
    return ""


def finder_report_for_remote(
    library: Mapping[str, Any],
    item: Mapping[str, Any],
    local_job: Any,
) -> dict[str, Any]:
    payload = _payload(item)
    symbol = str(payload.get("symbol") or local_job.payload.get("symbol") or "").strip().upper()
    profile = str(payload.get("profile") or local_job.payload.get("profile") or "").strip()
    expected_generated_at = _expected_generated_at(item, symbol, profile)
    created_at = str(item.get("created_at") or item.get("queued_at") or "").strip()

    candidates: list[dict[str, Any]] = []
    for raw in library.get("stock_strategy_finder_runs") or []:
        if not isinstance(raw, Mapping):
            continue
        summary = dict(raw)
        if str(summary.get("symbol") or "").strip().upper() != symbol:
            continue
        if str(summary.get("profile") or "").strip() != profile:
            continue
        generated_at = str(summary.get("generated_at") or "").strip()
        if expected_generated_at and generated_at != expected_generated_at:
            continue
        if not expected_generated_at and created_at and generated_at and generated_at < created_at:
            continue
        candidates.append(summary)

    if not candidates:
        return {}
    candidates.sort(key=lambda row: str(row.get("generated_at") or ""), reverse=True)
    return finder_summary_to_report(candidates[0])
