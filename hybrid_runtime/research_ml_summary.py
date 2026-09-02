"""Bounded, read-only Research + ML status summaries for the desktop UI."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping


ACTIVE_QUEUE_STATUSES = frozenset({"queued", "running", "retry", "retry_wait"})


def _text(value: Any, maximum: int = 300) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _when(item: Mapping[str, Any]) -> str:
    for key in (
        "completed_at",
        "updated_at",
        "generated_at",
        "created_at",
        "started_at",
        "saved_at",
    ):
        value = _text(item.get(key), 80)
        if value:
            return value
    return ""


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _progress(item: Mapping[str, Any]) -> float:
    raw = item.get("progress")
    if raw is None:
        raw = item.get("progress_fraction")
    if raw is None and item.get("progress_pct") is not None:
        raw = _number(item.get("progress_pct")) / 100.0
    return max(0.0, min(1.0, _number(raw)))


def _sorted(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    rows.sort(key=lambda item: _text(item.get("when"), 80), reverse=True)
    return rows[:limit]


def _research_runs(library: Mapping[str, Any], limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for collection_name in (
        "research_worker_runs",
        "external_research_runs",
        "autonomous_research_runs",
    ):
        collection = library.get(collection_name)
        if not isinstance(collection, list):
            continue
        for raw in collection:
            if not isinstance(raw, Mapping):
                continue
            identifier = _text(raw.get("id") or raw.get("run_id"), 140)
            fingerprint = identifier or "|".join(
                [
                    collection_name,
                    _when(raw),
                    _text(raw.get("topic") or raw.get("title"), 120),
                ]
            )
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            hypotheses = raw.get("hypotheses") if isinstance(raw.get("hypotheses"), list) else []
            sources = raw.get("sources") if isinstance(raw.get("sources"), list) else []
            rows.append(
                {
                    "id": identifier,
                    "kind": _text(raw.get("type") or raw.get("job_type") or collection_name, 80),
                    "status": _text(raw.get("status") or raw.get("decision") or "complete", 80),
                    "topic": _text(raw.get("topic") or raw.get("title") or raw.get("summary"), 220),
                    "model": _text(raw.get("model") or raw.get("model_name"), 120),
                    "hypothesis_count": int(raw.get("hypothesis_count") or len(hypotheses) or 0),
                    "source_count": int(raw.get("source_count") or len(sources) or 0),
                    "when": _when(raw),
                }
            )
    return _sorted(rows, limit)


def _hypotheses(library: Mapping[str, Any], limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    collection = library.get("research_hypotheses")
    if not isinstance(collection, list):
        collection = []
    for raw in collection:
        if not isinstance(raw, Mapping):
            continue
        rows.append(
            {
                "id": _text(raw.get("id") or raw.get("concept_fingerprint"), 140),
                "name": _text(raw.get("name") or raw.get("statement"), 220),
                "category": _text(raw.get("category"), 100),
                "direction": _text(raw.get("direction"), 40),
                "status": _text(
                    raw.get("status")
                    or raw.get("specialist_decision")
                    or raw.get("decision")
                    or "research",
                    80,
                ),
                "confidence": _number(raw.get("confidence"), -1.0),
                "when": _when(raw),
            }
        )
    return _sorted(rows, limit)


def _knowledge_sources(library: Mapping[str, Any], limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    collection = library.get("knowledge_sources")
    if not isinstance(collection, list):
        collection = []
    for raw in collection:
        if not isinstance(raw, Mapping):
            continue
        rows.append(
            {
                "id": _text(raw.get("id"), 140),
                "title": _text(raw.get("title") or raw.get("name") or raw.get("url"), 220),
                "source_type": _text(raw.get("source_type") or raw.get("type"), 80),
                "status": _text(raw.get("status") or raw.get("ingestion_status") or "saved", 80),
                "url": _text(raw.get("url"), 260),
                "when": _when(raw),
            }
        )
    return _sorted(rows, limit)


def _predictive_runs(library: Mapping[str, Any], limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    collection = library.get("predictive_ml_runs")
    if not isinstance(collection, list):
        collection = []
    rows: list[dict[str, Any]] = []
    for raw in collection:
        if not isinstance(raw, Mapping):
            continue
        models = [item for item in raw.get("probability_models") or [] if isinstance(item, Mapping)]
        if isinstance(raw.get("probability_model"), Mapping) and not models:
            models = [raw.get("probability_model")]
        dataset = raw.get("dataset_summary") if isinstance(raw.get("dataset_summary"), Mapping) else {}
        rows.append(
            {
                "id": _text(raw.get("id") or raw.get("run_id"), 140),
                "status": _text(raw.get("status") or raw.get("validation_status") or "complete", 80),
                "model_count": len(models),
                "symbol_count": int(
                    dataset.get("symbol_count")
                    or raw.get("symbol_count")
                    or len(raw.get("symbols") or [])
                    or 0
                ),
                "row_count": int(dataset.get("row_count") or dataset.get("rows") or raw.get("row_count") or 0),
                "integrity_contract": _text(dataset.get("market_data_integrity_contract"), 100),
                "method": _text(raw.get("method") or raw.get("model_suite_version") or raw.get("version"), 100),
                "when": _when(raw),
            }
        )

    ready_rows: list[dict[str, Any]] = []
    try:
        from predictive_model_registry import ready_shadow_models

        ready = ready_shadow_models([dict(item) for item in collection if isinstance(item, Mapping)], maximum=12)
    except Exception:
        ready = []
    for model in ready:
        if not isinstance(model, Mapping):
            continue
        ready_rows.append(
            {
                "id": _text(model.get("id"), 140),
                "target": _text(model.get("target"), 100),
                "session_mode": _text(model.get("session_mode"), 80),
                "model_type": _text(model.get("model_type") or model.get("algorithm") or model.get("name"), 100),
                "shadow_scoring_enabled": bool(model.get("shadow_scoring_enabled")),
                "created_at": _text(model.get("created_at"), 80),
            }
        )
    return _sorted(rows, limit), ready_rows[:12]


def build_research_ml_summary(
    library: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    maximum = max(5, min(100, int(limit)))
    queue_rows: list[dict[str, Any]] = []
    queue = library.get("research_queue") if isinstance(library.get("research_queue"), list) else []
    type_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    for raw in queue:
        if not isinstance(raw, Mapping):
            continue
        kind = _text(raw.get("type") or raw.get("job_type") or "unknown", 80)
        status = _text(raw.get("status") or "queued", 60).lower()
        type_counts[kind] += 1
        status_counts[status] += 1
        payload = raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {}
        message = _text(
            raw.get("status_message")
            or payload.get("distributed_message")
            or raw.get("last_error"),
            240,
        )
        queue_rows.append(
            {
                "id": _text(raw.get("id"), 140),
                "type": kind,
                "status": status,
                "stage": _text(raw.get("stage") or payload.get("distributed_stage") or status, 100),
                "progress": _progress(raw) or max(0.0, min(1.0, _number(payload.get("distributed_progress")))),
                "message": message,
                "when": _when(raw),
            }
        )

    research_runs = _research_runs(library, maximum)
    hypotheses = _hypotheses(library, maximum)
    sources = _knowledge_sources(library, maximum)
    ml_runs, ready_models = _predictive_runs(library, maximum)
    research_system = library.get("research_system") if isinstance(library.get("research_system"), Mapping) else {}
    active_count = sum(
        1
        for row in queue_rows
        if str(row.get("status") or "") in ACTIVE_QUEUE_STATUSES
    )

    return {
        "queue": _sorted(queue_rows, maximum),
        "research_runs": research_runs,
        "hypotheses": hypotheses,
        "sources": sources,
        "predictive_ml_runs": ml_runs,
        "ready_shadow_models": ready_models,
        "counts": {
            "active_cloud_jobs": active_count,
            "queue_total": len(queue_rows),
            "research_runs": sum(
                len(library.get(name) or [])
                for name in ("research_worker_runs", "external_research_runs", "autonomous_research_runs")
                if isinstance(library.get(name), list)
            ),
            "hypotheses": len(library.get("research_hypotheses") or []) if isinstance(library.get("research_hypotheses"), list) else 0,
            "sources": len(library.get("knowledge_sources") or []) if isinstance(library.get("knowledge_sources"), list) else 0,
            "predictive_ml_runs": len(library.get("predictive_ml_runs") or []) if isinstance(library.get("predictive_ml_runs"), list) else 0,
            "ready_shadow_models": len(ready_models),
        },
        "queue_type_counts": dict(type_counts),
        "queue_status_counts": dict(status_counts),
        "research_system": {
            key: research_system.get(key)
            for key in (
                "status",
                "last_cycle_at",
                "last_worker_at",
                "last_error",
                "enabled",
            )
            if research_system.get(key) is not None
        },
        "library": dict(metadata or {}),
        "bounded": True,
        "limit_per_section": maximum,
        "research_only": True,
        "affects_live_ranking": False,
        "affects_execution": False,
    }
