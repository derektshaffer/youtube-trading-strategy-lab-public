"""Create a compact, read-only audit of the durable Trading Intelligence Lab research library."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ACTIVE = {"queued", "running", "retry"}
UTC = timezone.utc


def parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def counter_dict(values) -> dict[str, int]:
    return dict(sorted(Counter(str(v or "missing") for v in values).items()))


def status_fields(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    fields: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        for key, value in row.items():
            if key == "status" or key.endswith("_status") or key in {"decision", "type", "model_role"}:
                if isinstance(value, (str, int, float, bool)) or value is None:
                    fields[key].append(value)
    return {key: counter_dict(vals) for key, vals in sorted(fields.items())}


def fingerprint_payload(job: dict[str, Any]) -> str:
    material = json.dumps(
        {"type": job.get("type"), "payload": job.get("payload") or {}},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


def topic_of(job: dict[str, Any]) -> str:
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    return " ".join(str(payload.get("topic") or "").casefold().split())


def summarize(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    now = datetime.now(UTC)

    queue = [x for x in data.get("research_queue") or [] if isinstance(x, dict)]
    workers = [x for x in data.get("research_worker_runs") or [] if isinstance(x, dict)]
    runs = [x for x in data.get("external_research_runs") or [] if isinstance(x, dict)]
    hypotheses = [x for x in data.get("research_hypotheses") or [] if isinstance(x, dict)]
    ml_runs = [x for x in data.get("predictive_ml_runs") or [] if isinstance(x, dict)]
    strategies = [x for x in data.get("strategies") or [] if isinstance(x, dict)]

    active = [x for x in queue if str(x.get("status") or "") in ACTIVE]
    terminal = [x for x in queue if str(x.get("status") or "") not in ACTIVE]

    duplicate_dedupe = defaultdict(list)
    duplicate_payload = defaultdict(list)
    duplicate_topic = defaultdict(list)
    for job in active:
        dedupe = str(job.get("dedupe_key") or "").strip()
        if dedupe:
            duplicate_dedupe[dedupe].append(str(job.get("id") or ""))
        duplicate_payload[fingerprint_payload(job)].append(str(job.get("id") or ""))
        topic = topic_of(job)
        if topic:
            duplicate_topic[topic].append(str(job.get("id") or ""))

    stale = []
    for job in active:
        stamp = parse_dt(job.get("updated_at") or job.get("started_at") or job.get("created_at"))
        age_h = (now - stamp).total_seconds() / 3600 if stamp else None
        if age_h is not None and age_h >= 6:
            stale.append({
                "id": job.get("id"),
                "type": job.get("type"),
                "status": job.get("status"),
                "age_hours": round(age_h, 2),
                "attempts": job.get("attempts"),
                "max_attempts": job.get("max_attempts"),
                "dedupe_key": job.get("dedupe_key"),
                "topic": (job.get("payload") or {}).get("topic") if isinstance(job.get("payload"), dict) else None,
            })

    active_detail = []
    for job in active:
        stamp = parse_dt(job.get("created_at"))
        age_h = (now - stamp).total_seconds() / 3600 if stamp else None
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        active_detail.append({
            "id": job.get("id"),
            "type": job.get("type"),
            "status": job.get("status"),
            "priority": job.get("priority"),
            "attempts": job.get("attempts"),
            "max_attempts": job.get("max_attempts"),
            "age_hours": round(age_h, 2) if age_h is not None else None,
            "dedupe_key": job.get("dedupe_key"),
            "topic": payload.get("topic"),
            "hypothesis_id": payload.get("hypothesis_id"),
            "research_run_id": payload.get("research_run_id"),
            "cycle_date": payload.get("cycle_date"),
            "last_error": str(job.get("last_error") or "")[:500] or None,
        })
    active_detail.sort(key=lambda x: (-(int(x.get("priority") or 0)), str(x.get("type") or ""), str(x.get("id") or "")))

    web_strategies = [x for x in strategies if str(x.get("source_type") or "") == "autonomous_web_research"]

    worker_times = [parse_dt(x.get("completed_at") or x.get("created_at") or x.get("started_at")) for x in workers]
    worker_times = [x for x in worker_times if x]
    complete_worker_ids = {
        str(x.get("job_id") or "") for x in workers
        if str(x.get("status") or "") == "complete" and str(x.get("job_id") or "")
    }
    failed_worker_ids = {
        str(x.get("job_id") or "") for x in workers
        if str(x.get("status") or "") == "failed" and str(x.get("job_id") or "")
    }

    latest_ml = None
    if ml_runs:
        ordered = sorted(ml_runs, key=lambda x: str(x.get("completed_at") or ""), reverse=True)
        latest = ordered[0]
        router = latest.get("stock_learning_router") if isinstance(latest.get("stock_learning_router"), dict) else {}
        latest_ml = {
            "id": latest.get("id"),
            "completed_at": latest.get("completed_at"),
            "model_suite_version": latest.get("model_suite_version"),
            "runtime_seconds": latest.get("runtime_seconds"),
            "symbols": latest.get("symbols"),
            "horizons": latest.get("horizons"),
            "dataset_summary": latest.get("dataset_summary"),
            "router": {
                "status": router.get("status"),
                "symbols_compared": router.get("symbols_compared"),
                "symbols_with_clear_route": router.get("symbols_with_clear_route"),
                "route_counts": router.get("route_counts"),
                "by_symbol": router.get("by_symbol"),
            },
        }

    system = data.get("research_system") if isinstance(data.get("research_system"), dict) else {}

    return {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "source_file": str(path),
        "source_size_bytes": path.stat().st_size,
        "library_version": data.get("version"),
        "research_system": system,
        "stored_collection_counts": {
            "strategies": len(strategies),
            "autonomous_web_research_strategies": len(web_strategies),
            "research_queue": len(queue),
            "research_worker_runs": len(workers),
            "external_research_runs": len(runs),
            "research_hypotheses": len(hypotheses),
            "predictive_ml_runs": len(ml_runs),
        },
        "queue": {
            "status_counts": counter_dict(x.get("status") for x in queue),
            "type_counts_all": counter_dict(x.get("type") for x in queue),
            "active_count": len(active),
            "active_type_counts": counter_dict(x.get("type") for x in active),
            "terminal_count": len(terminal),
            "active_jobs": active_detail,
            "stale_active_6h_count": len(stale),
            "stale_active_jobs": sorted(stale, key=lambda x: -(x.get("age_hours") or 0)),
            "duplicate_active_dedupe_keys": {k: v for k, v in duplicate_dedupe.items() if len(v) > 1},
            "duplicate_active_exact_payloads": {k: v for k, v in duplicate_payload.items() if len(v) > 1},
            "duplicate_active_exact_topics": {k: v for k, v in duplicate_topic.items() if len(v) > 1},
        },
        "worker_history": {
            "stored_records": len(workers),
            "status_counts": counter_dict(x.get("status") for x in workers),
            "type_counts": counter_dict(x.get("job_type") or x.get("type") for x in workers),
            "unique_completed_job_ids": len(complete_worker_ids),
            "unique_failed_job_ids": len(failed_worker_ids),
            "earliest_record_time": min(worker_times).isoformat().replace("+00:00", "Z") if worker_times else None,
            "latest_record_time": max(worker_times).isoformat().replace("+00:00", "Z") if worker_times else None,
        },
        "external_research": {
            "stored_runs": len(runs),
            "status_fields": status_fields(runs),
            "topic_counts": counter_dict(x.get("topic") for x in runs),
        },
        "hypotheses": {
            "stored": len(hypotheses),
            "status_fields": status_fields(hypotheses),
        },
        "autonomous_web_strategies": {
            "stored": len(web_strategies),
            "validation_status_counts": counter_dict(x.get("validation_status") for x in web_strategies),
            "approved_counts": counter_dict(x.get("approved") for x in web_strategies),
            "category_counts": counter_dict(x.get("category") for x in web_strategies),
        },
        "predictive_ml": {
            "stored_runs": len(ml_runs),
            "latest": latest_ml,
        },
        "interpretation_notes": [
            "Counts are counts of records currently retained in the durable library, not automatically guaranteed all-time lifetime counts if a collection has retention caps.",
            "Exact duplicate checks only flag identical active dedupe keys, payloads, or normalized web-research topics; semantically similar wording may still need human/model review.",
            "Stale means an active queued/running/retry job whose latest useful timestamp is at least six hours old; it is an audit flag, not an automatic deletion recommendation.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("library")
    parser.add_argument("output")
    args = parser.parse_args()
    report = summarize(Path(args.library))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report["stored_collection_counts"], indent=2, sort_keys=True))
    print(json.dumps(report["queue"]["status_counts"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
