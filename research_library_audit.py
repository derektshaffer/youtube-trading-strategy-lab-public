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
    return dict(sorted(Counter(str(v if v is not None else "missing") for v in values).items()))


def status_fields(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    fields: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        for key, value in row.items():
            if key == "status" or key.endswith("_status") or key in {"decision", "type", "model_role"}:
                if isinstance(value, (str, int, float, bool)) or value is None:
                    fields[key].append(value)
    return {key: counter_dict(vals) for key, vals in sorted(fields.items())}


def payload_fp(job: dict[str, Any]) -> str:
    material = json.dumps(
        {"type": job.get("type"), "payload": job.get("payload") or {}},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(material).hexdigest()[:16]


def topic_of(job: dict[str, Any]) -> str:
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    return " ".join(str(payload.get("topic") or "").casefold().split())


def compact_router(router: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for raw in router.get("by_symbol") or []:
        if not isinstance(raw, dict):
            continue
        routes = raw.get("routes") if isinstance(raw.get("routes"), dict) else {}
        compact_routes = {}
        for route_name in ("same_ticker_history", "similarity_weighted_transfer", "broad_cross_stock_transfer"):
            route = routes.get(route_name) if isinstance(routes.get(route_name), dict) else {}
            compact_routes[route_name] = {
                "brier_score": route.get("brier_score"),
                "roc_auc": route.get("roc_auc"),
                "rows": route.get("rows") or route.get("test_rows"),
            }
        rows.append({
            "symbol": raw.get("symbol"),
            "status": raw.get("status"),
            "route_status": raw.get("route_status"),
            "recommended_route": raw.get("recommended_route"),
            "provisional_lowest_brier_route": raw.get("provisional_lowest_brier_route"),
            "paired_oos_rows": raw.get("paired_oos_rows"),
            "reason": raw.get("reason"),
            "routes": compact_routes,
        })
    return {
        "status": router.get("status"),
        "symbols_compared": router.get("symbols_compared"),
        "symbols_with_clear_route": router.get("symbols_with_clear_route"),
        "route_counts": router.get("route_counts"),
        "by_symbol": rows,
    }


def profit_first_validation_summary(
    validation_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a compact profit-first view using the current validation protocol."""

    def number(value: Any, default: float = 0.0) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        return result if result == result else default

    def metrics_positive(metrics: dict[str, Any]) -> bool:
        return (
            int(number(metrics.get("trade_count"))) > 0
            and number(metrics.get("net_pnl")) > 0.0
        )

    status_caps = {
        "LIMITED DATA": 39.0,
        "DRAWDOWN TOO HIGH": 39.0,
        "NO VALIDATED EDGE": 39.0,
        "UNSTABLE": 49.0,
        "COST SENSITIVE": 59.0,
        "HOLDOUT LIMITED": 39.0,
        "HOLDOUT FAILED": 39.0,
        "HOLDOUT COST SENSITIVE": 49.0,
        "HOLDOUT REUSED": 39.0,
    }
    robust_verdict_codes = {
        "ready_for_paper",
        "historically_robust_execution_gap",
    }

    latest_by_strategy: dict[str, dict[str, Any]] = {}
    ordered = sorted(
        validation_runs,
        key=lambda item: str(item.get("generated_at") or ""),
        reverse=True,
    )
    for run in ordered:
        strategy_key = str(
            run.get("strategy_id")
            or run.get("strategy_name")
            or run.get("id")
            or ""
        )
        if not strategy_key or strategy_key in latest_by_strategy:
            continue
        validation = run.get("validation_metrics") if isinstance(run.get("validation_metrics"), dict) else {}
        holdout = run.get("holdout_metrics") if isinstance(run.get("holdout_metrics"), dict) else {}
        stress = run.get("stress_metrics") if isinstance(run.get("stress_metrics"), dict) else {}
        robustness = run.get("robustness") if isinstance(run.get("robustness"), dict) else {}
        walk = run.get("walk_forward_summary") if isinstance(run.get("walk_forward_summary"), dict) else {}
        stability = (
            run.get("parameter_stability")
            if isinstance(run.get("parameter_stability"), dict)
            else {}
        )
        verdict = (
            run.get("evidence_verdict")
            if isinstance(run.get("evidence_verdict"), dict)
            else {}
        )
        paper_fidelity = (
            run.get("paper_execution_fidelity")
            if isinstance(run.get("paper_execution_fidelity"), dict)
            else {}
        )
        spread_audit = (
            run.get("historical_spread_audit")
            if isinstance(run.get("historical_spread_audit"), dict)
            else {}
        )
        holdout_reuse = (
            run.get("holdout_reuse_audit")
            if isinstance(run.get("holdout_reuse_audit"), dict)
            else {}
        )

        validation_positive = metrics_positive(validation)
        holdout_positive = metrics_positive(holdout)
        stress_positive = metrics_positive(stress)
        validation_status = str(run.get("validation_status") or "").lower()
        optimizer_status = str(run.get("optimizer_status") or "").strip().upper()
        stored_robustness_score = number(robustness.get("score"))
        robustness_cap = status_caps.get(optimizer_status, 100.0)
        effective_robustness_score = min(stored_robustness_score, robustness_cap)

        verdict_code = str(verdict.get("code") or "").strip().lower()
        autonomous = bool(run.get("autonomous"))
        validation_method_version = int(number(run.get("validation_method_version")))
        current_protocol = bool(
            (autonomous and validation_method_version >= 4)
            or (not autonomous and bool(verdict_code))
        )
        historical_gate_pass = bool(
            (
                autonomous
                and validation_method_version >= 4
                and validation_status == "validated"
            )
            or verdict_code in robust_verdict_codes
        )
        requires_revalidation = not current_protocol
        strict = bool(
            validation_status == "validated"
            and current_protocol
            and historical_gate_pass
            and validation_positive
            and holdout_positive
            and stress_positive
        )

        if requires_revalidation:
            blocker = (
                "Legacy/incomplete validation record; re-run strict validation under "
                "the current protocol."
            )
        elif verdict.get("reason"):
            blocker = str(verdict.get("reason"))
        elif run.get("gate_reasons"):
            blocker = " ".join(str(item) for item in (run.get("gate_reasons") or [])[:2])
        elif optimizer_status and optimizer_status != "VALIDATED":
            blocker = f"Optimizer status is {optimizer_status.replace('_', ' ').title()}."
        else:
            blocker = "The current strict validation gate was not fully satisfied."

        latest_by_strategy[strategy_key] = {
            "strategy_id": run.get("strategy_id"),
            "strategy_name": run.get("strategy_name"),
            "symbol": run.get("symbol"),
            "generated_at": run.get("generated_at"),
            "validation_status": run.get("validation_status"),
            "strict_profit_edge": strict,
            "optimizer_status": run.get("optimizer_status"),
            "stored_robustness_score": stored_robustness_score,
            "robustness_score": effective_robustness_score,
            "robustness_cap": robustness_cap,
            "robustness_label": robustness.get("label"),
            "validation_metrics": validation,
            "holdout_metrics": holdout,
            "stress_metrics": stress,
            "walk_forward_profitable_pct": walk.get("profitable_fold_pct"),
            "parameter_stability_label": stability.get("label"),
            "parameter_stability_positive_pct": stability.get("positive_pct"),
            "evidence_verdict_code": verdict.get("code"),
            "evidence_verdict_label": verdict.get("label"),
            "evidence_verdict_reason": verdict.get("reason"),
            "paper_execution_status": paper_fidelity.get("status"),
            "historical_spread_status": spread_audit.get("status"),
            "holdout_reuse_status": holdout_reuse.get("status"),
            "autonomous": autonomous,
            "validation_method_version": validation_method_version,
            "current_protocol": current_protocol,
            "requires_revalidation": requires_revalidation,
            "blocker": blocker,
            "positive_evidence_periods": sum(
                (validation_positive, holdout_positive, stress_positive)
            ),
        }

    latest = list(latest_by_strategy.values())
    strict_edges = [item for item in latest if item["strict_profit_edge"]]
    strict_edges.sort(
        key=lambda item: (
            number(item.get("robustness_score")),
            number((item.get("holdout_metrics") or {}).get("net_pnl")),
        ),
        reverse=True,
    )
    near_misses = [item for item in latest if not item["strict_profit_edge"]]
    near_misses.sort(
        key=lambda item: (
            int(item.get("positive_evidence_periods") or 0),
            not bool(item.get("requires_revalidation")),
            number(item.get("robustness_score")),
            number((item.get("holdout_metrics") or {}).get("net_pnl")),
        ),
        reverse=True,
    )
    return {
        "saved_validation_run_count": len(validation_runs),
        "distinct_strategy_count": len(latest),
        "validated_run_count": sum(
            1
            for item in validation_runs
            if str(item.get("validation_status") or "").lower() == "validated"
        ),
        "strict_profit_edge_count": len(strict_edges),
        "strict_profit_edges": strict_edges[:20],
        "closest_research_candidates": near_misses[:20],
        "criteria": (
            "Latest saved run for a strategy must come from the current validation "
            "protocol, be labeled validated, and have positive trade count and net "
            "P/L in validation, untouched holdout, and higher-cost stress periods."
        ),
    }


def summarize(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    now = datetime.now(UTC)
    queue = [x for x in data.get("research_queue") or [] if isinstance(x, dict)]
    workers = [x for x in data.get("research_worker_runs") or [] if isinstance(x, dict)]
    runs = [x for x in data.get("external_research_runs") or [] if isinstance(x, dict)]
    hyps = [x for x in data.get("research_hypotheses") or [] if isinstance(x, dict)]
    ml_runs = [x for x in data.get("predictive_ml_runs") or [] if isinstance(x, dict)]
    validation_runs = [x for x in data.get("validation_runs") or [] if isinstance(x, dict)]
    research_runs = [x for x in data.get("research_runs") or [] if isinstance(x, dict)]
    strategies = [x for x in data.get("strategies") or [] if isinstance(x, dict)]
    web_strats = [x for x in strategies if str(x.get("source_type") or "") == "autonomous_web_research"]

    active = [x for x in queue if str(x.get("status") or "") in ACTIVE]
    terminal = [x for x in queue if str(x.get("status") or "") not in ACTIVE]

    by_dedupe, by_payload, by_topic = defaultdict(list), defaultdict(list), defaultdict(list)
    stale = []
    active_detail = []
    for job in active:
        jid = str(job.get("id") or "")
        dedupe = str(job.get("dedupe_key") or "").strip()
        if dedupe:
            by_dedupe[dedupe].append(jid)
        by_payload[payload_fp(job)].append(jid)
        topic_norm = topic_of(job)
        if topic_norm:
            by_topic[topic_norm].append(jid)

        created = parse_dt(job.get("created_at"))
        updated = parse_dt(job.get("updated_at") or job.get("started_at") or job.get("created_at"))
        created_age = (now - created).total_seconds() / 3600 if created else None
        updated_age = (now - updated).total_seconds() / 3600 if updated else None
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        row = {
            "id": jid,
            "type": job.get("type"),
            "status": job.get("status"),
            "priority": job.get("priority"),
            "attempts": job.get("attempts"),
            "max_attempts": job.get("max_attempts"),
            "created_age_hours": round(created_age, 2) if created_age is not None else None,
            "updated_age_hours": round(updated_age, 2) if updated_age is not None else None,
            "dedupe_key": job.get("dedupe_key"),
            "topic": payload.get("topic"),
            "hypothesis_id": payload.get("hypothesis_id"),
            "research_run_id": payload.get("research_run_id"),
            "cycle_date": payload.get("cycle_date"),
            "last_error": str(job.get("last_error") or "")[:350] or None,
        }
        active_detail.append(row)
        if updated_age is not None and updated_age >= 6:
            stale.append(row)

    active_detail.sort(key=lambda x: (-(int(x.get("priority") or 0)), str(x.get("type") or ""), str(x.get("id") or "")))

    completed_ids = {str(x.get("job_id") or "") for x in workers if str(x.get("status") or "") == "complete" and x.get("job_id")}
    failed_ids = {str(x.get("job_id") or "") for x in workers if str(x.get("status") or "") == "failed" and x.get("job_id")}
    worker_times = [parse_dt(x.get("completed_at") or x.get("started_at") or x.get("created_at")) for x in workers]
    worker_times = [x for x in worker_times if x]

    run_topics = Counter(str(x.get("topic") or "missing") for x in runs)
    repeated_run_topics = dict(sorted((k, v) for k, v in run_topics.items() if v > 1))

    latest_ml = None
    if ml_runs:
        latest = sorted(ml_runs, key=lambda x: str(x.get("completed_at") or ""), reverse=True)[0]
        ds = latest.get("dataset_summary") if isinstance(latest.get("dataset_summary"), dict) else {}
        router = latest.get("stock_learning_router") if isinstance(latest.get("stock_learning_router"), dict) else {}
        latest_ml = {
            "id": latest.get("id"),
            "completed_at": latest.get("completed_at"),
            "model_suite_version": latest.get("model_suite_version"),
            "runtime_seconds": latest.get("runtime_seconds"),
            "symbols": latest.get("symbols"),
            "horizons": latest.get("horizons"),
            "dataset": {
                "row_count": ds.get("row_count"),
                "bars_loaded": ds.get("bars_loaded"),
                "bars_analyzed": ds.get("bars_analyzed"),
                "symbol_count": len(ds.get("by_symbol") or []),
            },
            "router": compact_router(router),
        }

    autonomous_validations = [
        item
        for item in validation_runs
        if bool(item.get("autonomous"))
    ]
    autonomous_validations.sort(
        key=lambda item: str(item.get("generated_at") or ""),
        reverse=True,
    )
    latest_autonomous_validations = []
    for item in autonomous_validations[:12]:
        latest_autonomous_validations.append(
            {
                "id": item.get("id"),
                "strategy_id": item.get("strategy_id"),
                "strategy_name": item.get("strategy_name"),
                "symbol": item.get("symbol"),
                "generated_at": item.get("generated_at"),
                "validation_method_version": item.get("validation_method_version"),
                "validation_status": item.get("validation_status"),
                "optimizer_status": item.get("optimizer_status"),
                "global_score": item.get("global_score"),
                "robustness": item.get("robustness"),
                "generalization_summary": item.get("generalization_summary"),
                "walk_forward_summary": item.get("walk_forward_summary"),
                "training_metrics": item.get("training_metrics"),
                "validation_metrics": item.get("validation_metrics"),
                "holdout_metrics": item.get("holdout_metrics"),
                "stress_metrics": item.get("stress_metrics"),
                "gate_reasons": item.get("gate_reasons") or [],
            }
        )

    autonomous_research_runs = [
        item
        for item in research_runs
        if str(item.get("kind") or "") == "autonomous_research"
    ]
    autonomous_research_runs.sort(
        key=lambda item: str(item.get("generated_at") or ""),
        reverse=True,
    )
    latest_autonomous_research_runs = [
        {
            "id": item.get("id"),
            "generated_at": item.get("generated_at"),
            "validation_method_version": item.get("validation_method_version"),
            "run_status": item.get("run_status"),
            "deep_strategies_attempted": item.get("deep_strategies_attempted"),
            "deep_strategies_tested": item.get("deep_strategies_tested"),
            "deep_strategies_failed": item.get("deep_strategies_failed"),
            "failed_finalists": item.get("failed_finalists") or [],
        }
        for item in autonomous_research_runs[:6]
    ]

    system = data.get("research_system") if isinstance(data.get("research_system"), dict) else {}
    return {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "source_size_bytes": path.stat().st_size,
        "library_version": data.get("version"),
        "research_system": system,
        "stored_collection_counts": {
            "strategies": len(strategies),
            "autonomous_web_research_strategies": len(web_strats),
            "research_queue": len(queue),
            "research_worker_runs": len(workers),
            "external_research_runs": len(runs),
            "research_hypotheses": len(hyps),
            "predictive_ml_runs": len(ml_runs),
        },
        "queue": {
            "status_counts": counter_dict(x.get("status") for x in queue),
            "type_counts_all": counter_dict(x.get("type") for x in queue),
            "active_count": len(active),
            "active_type_counts": counter_dict(x.get("type") for x in active),
            "terminal_count": len(terminal),
            "stale_active_6h_count": len(stale),
            "duplicate_active_dedupe_key_groups": {k: v for k, v in by_dedupe.items() if len(v) > 1},
            "duplicate_active_exact_payload_groups": {k: v for k, v in by_payload.items() if len(v) > 1},
            "duplicate_active_exact_topic_groups": {k: v for k, v in by_topic.items() if len(v) > 1},
            "active_jobs": active_detail,
        },
        "worker_history": {
            "stored_records": len(workers),
            "status_counts": counter_dict(x.get("status") for x in workers),
            "type_counts": counter_dict(x.get("job_type") or x.get("type") for x in workers),
            "unique_completed_job_ids": len(completed_ids),
            "unique_failed_job_ids": len(failed_ids),
            "earliest_record_time": min(worker_times).isoformat().replace("+00:00", "Z") if worker_times else None,
            "latest_record_time": max(worker_times).isoformat().replace("+00:00", "Z") if worker_times else None,
        },
        "external_research": {
            "stored_runs": len(runs),
            "repeated_topic_counts": repeated_run_topics,
            "status_fields": status_fields(runs),
        },
        "hypotheses": {"stored": len(hyps), "status_fields": status_fields(hyps)},
        "autonomous_web_strategies": {
            "stored": len(web_strats),
            "validation_status_counts": counter_dict(x.get("validation_status") for x in web_strats),
            "approved_counts": counter_dict(x.get("approved") for x in web_strats),
        },
        "predictive_ml": {"stored_runs": len(ml_runs), "latest": latest_ml},
        "autonomous_validation": {
            "stored_runs": len(autonomous_validations),
            "latest": latest_autonomous_validations,
            "latest_research_runs": latest_autonomous_research_runs,
        },
        "profit_first": profit_first_validation_summary(validation_runs),
        "strategy_validation_status_counts": counter_dict(
            item.get("validation_status") for item in strategies
        ),
        "interpretation_notes": [
            "Counts are records currently retained in the durable library; if collections are retention-capped they are not guaranteed all-time lifetime totals.",
            "Duplicate checks flag exact active dedupe keys, payloads, or normalized topics; semantically similar wording may still require review.",
            "Stale is an audit flag for active jobs not updated for at least six hours, not an automatic deletion recommendation."
        ],
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("library")
    p.add_argument("output")
    a = p.parse_args()
    report = summarize(Path(a.library))
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report["stored_collection_counts"], indent=2, sort_keys=True))
    print(json.dumps(report["queue"]["status_counts"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
