"""Durable experiment lineage for Trading Intelligence Lab research.

The registry is intentionally a metadata/evidence ledger.  It never grants a
trading approval and it never interprets research text as proof.  Historical
engines remain authoritative for every pass/fail decision.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping

from youtube_strategy_engine import normalize_machine_rules, safe_float


EXPERIMENT_REGISTRY_VERSION = 1
MAX_EXPERIMENT_HISTORY = 1000
EXPERIMENT_STAGE_ORDER = (
    "research_hypothesis",
    "candidate_generation",
    "development_backtest",
    "robustness_testing",
    "adaptive_walk_forward",
    "profitable_neighborhood",
    "overfitting_multiplicity",
    "independent_validation",
    "paper_shadow_eligibility",
)
ACTIVE_EXPERIMENT_STATUSES = frozenset({"queued", "running"})


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _clean_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def ensure_experiment_registry(library: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(library or {})
    if not isinstance(data.get("experiment_registry"), list):
        data["experiment_registry"] = []
    return data


def strategy_experiment_fingerprint(strategy: Mapping[str, Any]) -> str:
    """Return a stable identity for a materially identical candidate."""
    rules = {
        key: value
        for key, value in normalize_machine_rules(strategy.get("machine_rules")).items()
        if value is not None
    }
    material = {
        "strategy_id": str(strategy.get("id") or ""),
        "hypothesis_id": str(strategy.get("research_hypothesis_id") or ""),
        "root_strategy_id": str(
            strategy.get("autonomous_research_root_id")
            or strategy.get("parent_strategy_id")
            or strategy.get("id")
            or ""
        ),
        "direction": str(strategy.get("direction") or "").strip().casefold(),
        "rules": rules,
        "candidate_rule_options": _clean_mapping(
            strategy.get("candidate_rule_options")
            or strategy.get("ai_candidate_rule_options")
        ),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def candidate_run_key(
    strategy: Mapping[str, Any],
    *,
    validation_end: Any,
    method_version: int,
    timeframe: str,
) -> str:
    """Identify a same-candidate/same-protocol/same-data-cutoff attempt."""
    cutoff = str(validation_end or "").strip()
    if "T" in cutoff:
        cutoff = cutoff.split("T", 1)[0]
    material = {
        "candidate": strategy_experiment_fingerprint(strategy),
        "validation_end": cutoff,
        "method_version": int(method_version or 0),
        "timeframe": str(timeframe or ""),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:28]


def select_unseen_experiment_candidates(
    library: Mapping[str, Any],
    strategies: Iterable[Mapping[str, Any]],
    *,
    validation_end: Any,
    method_version: int,
    timeframe: str,
    job_id: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Skip exact same-day protocol repeats while allowing changed/new evidence."""
    existing_by_key = {
        str(item.get("candidate_run_key") or ""): item
        for item in library.get("experiment_registry") or []
        if isinstance(item, Mapping) and str(item.get("candidate_run_key") or "")
    }
    selected: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen_in_batch: set[str] = set()
    for raw in strategies:
        strategy = dict(raw)
        run_key = candidate_run_key(
            strategy,
            validation_end=validation_end,
            method_version=method_version,
            timeframe=timeframe,
        )
        previous = existing_by_key.get(run_key)
        same_retry = bool(
            previous is not None
            and str(previous.get("job_id") or "")
            and str(previous.get("job_id") or "") == str(job_id or "")
            and str(previous.get("status") or "") in ACTIVE_EXPERIMENT_STATUSES
        )
        if (previous is not None and not same_retry) or run_key in seen_in_batch:
            duplicates.append(
                {
                    "strategy_id": strategy.get("id"),
                    "strategy_name": strategy.get("name"),
                    "candidate_run_key": run_key,
                    "existing_experiment_id": (
                        previous.get("id") if isinstance(previous, Mapping) else None
                    ),
                    "reason": (
                        "An experiment with identical candidate rules, validation protocol, "
                        "timeframe, and data cutoff is already recorded."
                    ),
                }
            )
            continue
        seen_in_batch.add(run_key)
        selected.append(strategy)
    return selected, duplicates


def begin_experiments(
    library: dict[str, Any],
    strategies: Iterable[Mapping[str, Any]],
    *,
    validation_end: Any,
    method_version: int,
    timeframe: str,
    job_id: str = "",
    status: str = "running",
) -> dict[str, Any]:
    """Persist candidate/running state before expensive validation begins."""
    data = ensure_experiment_registry(library)
    now = _utc_iso()
    existing = [
        dict(item)
        for item in data.get("experiment_registry") or []
        if isinstance(item, Mapping)
    ]
    by_key = {
        str(item.get("candidate_run_key") or ""): item
        for item in existing
        if str(item.get("candidate_run_key") or "")
    }
    touched: list[dict[str, Any]] = []
    touched_keys: set[str] = set()
    for raw in strategies:
        strategy = dict(raw)
        run_key = candidate_run_key(
            strategy,
            validation_end=validation_end,
            method_version=method_version,
            timeframe=timeframe,
        )
        touched_keys.add(run_key)
        previous = by_key.get(run_key) or {}
        created_at = str(previous.get("created_at") or now)
        identifier = str(previous.get("id") or "exp-" + run_key)
        source = _source_research(data, strategy)
        stages = [
            _stage(
                "research_hypothesis",
                "passed",
                reason="Research is recorded as an unproven candidate hypothesis, not truth.",
                evidence=source,
                completed_at=created_at,
            ),
            _stage(
                "candidate_generation",
                "passed",
                reason="A measurable candidate was frozen for deterministic testing.",
                evidence={
                    "candidate_fingerprint": strategy_experiment_fingerprint(strategy),
                    "validation_end": str(validation_end or ""),
                },
                completed_at=now,
            ),
        ]
        stages.extend(
            _stage(
                name,
                "pending",
                reason="Waiting for the preceding experiment stage.",
            )
            for name in EXPERIMENT_STAGE_ORDER[2:]
        )
        touched.append(
            {
                "id": identifier,
                "schema_version": EXPERIMENT_REGISTRY_VERSION,
                "experiment_signature": None,
                "candidate_run_key": run_key,
                "job_id": str(job_id or previous.get("job_id") or ""),
                "status": status if status in ACTIVE_EXPERIMENT_STATUSES else "running",
                "current_stage": "development_backtest",
                "promotion_status": "research_only",
                "strategy_id": strategy.get("id"),
                "strategy_name": strategy.get("name"),
                "strategy_family": {
                    "id": strategy.get("autonomous_research_root_id")
                    or strategy.get("parent_strategy_id")
                    or strategy.get("id"),
                    "name": strategy.get("name"),
                    "category": strategy.get("category"),
                    "direction": strategy.get("direction"),
                },
                "source_research": source,
                "measurable_features": {
                    "machine_rules": normalize_machine_rules(
                        strategy.get("machine_rules")
                    ),
                    "feature_names": sorted(
                        key
                        for key, value in normalize_machine_rules(
                            strategy.get("machine_rules")
                        ).items()
                        if value is not None
                    ),
                    "unresolved_rules": list(strategy.get("unresolved_rules") or []),
                },
                "parameters": {
                    "input_rules": normalize_machine_rules(
                        strategy.get("machine_rules")
                    )
                },
                "data_ranges": {"validation_end": str(validation_end or "")},
                "results": {},
                "robustness_metrics": {},
                "failure_reasons": [],
                "lineage": _clean_mapping(previous.get("lineage")) or {
                    "parent_strategy_id": strategy.get("parent_strategy_id"),
                    "root_strategy_id": strategy.get("autonomous_research_root_id"),
                    "hypothesis_id": strategy.get("research_hypothesis_id"),
                    "parent_experiment_ids": [],
                },
                "stages": stages,
                "created_at": created_at,
                "started_at": str(previous.get("started_at") or now),
                "completed_at": None,
                "updated_at": now,
            }
        )
    data["experiment_registry"] = (
        touched
        + [
            item
            for item in existing
            if str(item.get("candidate_run_key") or "") not in touched_keys
        ]
    )[:MAX_EXPERIMENT_HISTORY]
    return data


def experiment_stage_for_progress(message: str) -> str | None:
    """Map established engine progress text onto the durable stage vocabulary."""
    text = str(message or "").casefold()
    if "testing frozen" in text or "cross-stock" in text:
        return "independent_validation"
    if "walk-forward" in text or "walk forward" in text:
        return "adaptive_walk_forward"
    if "optimizing" in text or "optimization" in text:
        return "development_backtest"
    if any(
        token in text
        for token in (
            "screening",
            "candidate",
            "stock universe",
            "validation history",
            "catalyst window",
        )
    ):
        return "candidate_generation"
    return None


def update_running_experiments(
    library: dict[str, Any],
    *,
    job_id: str,
    stage: str,
    message: str,
) -> tuple[dict[str, Any], bool]:
    """Advance matching running records monotonically for reconnect-safe status."""
    data = ensure_experiment_registry(library)
    if stage not in EXPERIMENT_STAGE_ORDER:
        return data, False
    target_index = EXPERIMENT_STAGE_ORDER.index(stage)
    now = _utc_iso()
    changed = False
    records = []
    for raw in data.get("experiment_registry") or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        if (
            str(item.get("job_id") or "") != str(job_id or "")
            or str(item.get("status") or "") not in ACTIVE_EXPERIMENT_STATUSES
        ):
            records.append(item)
            continue
        current = str(item.get("current_stage") or "research_hypothesis")
        current_index = (
            EXPERIMENT_STAGE_ORDER.index(current)
            if current in EXPERIMENT_STAGE_ORDER
            else 0
        )
        if target_index < current_index:
            records.append(item)
            continue
        item["current_stage"] = stage
        item["status_message"] = str(message or "")[:500]
        item["updated_at"] = now
        stages = []
        for raw_stage in item.get("stages") or []:
            stage_item = dict(raw_stage) if isinstance(raw_stage, Mapping) else {}
            if str(stage_item.get("name") or "") == stage:
                stage_item["status"] = "running"
                stage_item["reason"] = str(message or "")[:500]
            stages.append(stage_item)
        item["stages"] = stages
        records.append(item)
        changed = True
    data["experiment_registry"] = records
    return data, changed


def _stage(
    name: str,
    status: str,
    *,
    reason: str,
    evidence: Mapping[str, Any] | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "reason": str(reason or ""),
        "evidence": deepcopy(dict(evidence or {})),
        "completed_at": completed_at if status in {"passed", "failed", "blocked"} else None,
    }


def _profitable_neighborhood_evidence(walk: Mapping[str, Any]) -> dict[str, Any]:
    summary = _clean_mapping(walk.get("summary"))
    folds = []
    for raw in walk.get("folds") or []:
        if not isinstance(raw, Mapping):
            continue
        neighborhood = _clean_mapping(raw.get("profitable_neighborhood"))
        folds.append(
            {
                "fold": raw.get("fold"),
                "external_test_start": raw.get("external_test_start"),
                "external_test_end": raw.get("external_test_end"),
                "broad_profitable": bool(neighborhood.get("broad_profitable")),
                "attempted_neighbor_count": int(
                    neighborhood.get("attempted_neighbor_count") or 0
                ),
                "profitable_neighbor_count": int(
                    neighborhood.get("profitable_neighbor_count") or 0
                ),
                "failed_neighbor_count": int(
                    neighborhood.get("failed_neighbor_count") or 0
                ),
                "profitable_neighbor_pct": neighborhood.get(
                    "profitable_neighbor_pct"
                ),
            }
        )
    return {
        "broad_profitable_fold_count": int(
            summary.get("broad_profitable_neighborhood_fold_count") or 0
        ),
        "incomplete_neighborhood_fold_count": int(
            summary.get("incomplete_neighborhood_fold_count") or 0
        ),
        "folds": folds,
    }


def _multiplicity_evidence(
    report: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    optimization = _clean_mapping(result.get("optimization_report"))
    winner = _clean_mapping(optimization.get("winner"))
    holdout_audit = _clean_mapping(result.get("holdout_reuse_audit"))
    reuse_audits = _clean_mapping(result.get("validation_evidence_reuse_audits"))
    configuration_count = int(
        optimization.get("unique_configurations_tested")
        or optimization.get("variants_tested")
        or winner.get("variants_tested")
        or len(optimization.get("rankings") or [])
        or 0
    )
    reused_symbols = sorted(
        str(symbol)
        for symbol, audit in reuse_audits.items()
        if isinstance(audit, Mapping) and not bool(audit.get("pristine", True))
    )
    holdout_sessions = list(optimization.get("holdout_sessions") or [])
    return {
        "strategy_families_eligible": int(report.get("eligible_strategies") or 0),
        "strategy_families_with_opportunities": int(
            report.get("strategies_with_opportunities") or 0
        ),
        "deep_finalists_attempted": int(report.get("deep_strategies_attempted") or 0),
        "configurations_tested": configuration_count,
        "selection_pressure_disclosed": True,
        "formal_multiplicity_correction": False,
        "formal_correction_note": (
            "No p-value multiplicity correction is claimed. Selection pressure is recorded "
            "and controlled with frozen candidates, internal splits, untouched holdout, "
            "walk-forward tests, neighborhood breadth, and cross-stock validation."
        ),
        "untouched_holdout_session_count": len(holdout_sessions),
        "holdout_reuse_pristine": bool(holdout_audit.get("pristine", True)),
        "reused_validation_symbols": reused_symbols,
    }


def _source_research(
    library: Mapping[str, Any], strategy: Mapping[str, Any]
) -> dict[str, Any]:
    hypothesis_id = str(strategy.get("research_hypothesis_id") or "")
    hypothesis = next(
        (
            dict(item)
            for item in library.get("research_hypotheses") or []
            if isinstance(item, Mapping) and str(item.get("id") or "") == hypothesis_id
        ),
        {},
    )
    return {
        "truth_status": "candidate_hypothesis_not_established_truth",
        "source_type": strategy.get("source_type"),
        "source_id": strategy.get("source_id"),
        "source_title": strategy.get("source_title"),
        "research_run_id": strategy.get("research_run_id"),
        "hypothesis_id": hypothesis_id or None,
        "hypothesis_statement": hypothesis.get("statement") or strategy.get("summary"),
        "supporting_source_ids": list(hypothesis.get("supporting_source_ids") or []),
        "contradicting_source_ids": list(hypothesis.get("contradicting_source_ids") or []),
        "source_quality_score": (
            hypothesis.get("source_quality_score")
            if hypothesis
            else strategy.get("research_source_quality_score")
        ),
    }


def build_completed_experiment(
    library: Mapping[str, Any],
    report: Mapping[str, Any],
    result: Mapping[str, Any],
    strategy: Mapping[str, Any],
) -> dict[str, Any]:
    generated_at = str(report.get("generated_at") or _utc_iso())
    optimization = _clean_mapping(result.get("optimization_report"))
    winner = _clean_mapping(optimization.get("winner"))
    strength = _clean_mapping(result.get("strength"))
    generalization = _clean_mapping(result.get("generalization"))
    general_summary = _clean_mapping(generalization.get("summary"))
    walk = _clean_mapping(result.get("walk_forward"))
    walk_summary = _clean_mapping(walk.get("summary"))
    comparison = _clean_mapping(walk.get("comparison"))
    neighborhood = _profitable_neighborhood_evidence(walk)
    multiplicity = _multiplicity_evidence(report, result)
    validation_status = str(result.get("validation_status") or "research_only")
    gate_reasons = [str(item) for item in result.get("gate_reasons") or []]

    development_passed = bool(winner)
    robustness_passed = bool(strength.get("independently_positive")) and (
        safe_float(strength.get("score"), 0.0) or 0.0
    ) >= 70.0
    walk_passed = bool(walk) and int(walk_summary.get("fold_count") or 0) >= 3
    comparison_passed = bool(comparison.get("enabled")) and str(
        comparison.get("verdict") or ""
    ).upper() != "STATIC BETTER"
    neighborhood_passed = (
        int(neighborhood.get("broad_profitable_fold_count") or 0) > 0
        and int(neighborhood.get("incomplete_neighborhood_fold_count") or 0) == 0
    )
    multiplicity_passed = (
        int(multiplicity.get("configurations_tested") or 0) > 0
        and int(multiplicity.get("untouched_holdout_session_count") or 0) > 0
        and bool(multiplicity.get("holdout_reuse_pristine"))
        and not multiplicity.get("reused_validation_symbols")
    )
    independent_passed = (
        validation_status == "validated"
        and (safe_float(general_summary.get("score"), 0.0) or 0.0) >= 65.0
    )
    paper_eligible = all(
        (
            development_passed,
            robustness_passed,
            walk_passed,
            comparison_passed,
            neighborhood_passed,
            multiplicity_passed,
            independent_passed,
        )
    )

    stages = [
        _stage(
            "research_hypothesis",
            "passed",
            reason="Research was recorded only as an unproven candidate hypothesis.",
            evidence=_source_research(library, strategy),
            completed_at=generated_at,
        ),
        _stage(
            "candidate_generation",
            "passed",
            reason="The deterministic rules and fixed candidate symbols were recorded before scoring.",
            evidence={
                "candidate_symbols": list(result.get("candidate_symbols") or []),
                "anchor_symbol": result.get("anchor_symbol"),
                "priority_score": result.get("priority_score"),
                "opportunity_count": sum(
                    int(item.get("event_count") or 0)
                    for item in result.get("opportunities") or []
                    if isinstance(item, Mapping)
                ),
                "sampling_boundary": _clean_mapping(report.get("sampling_boundary")),
            },
            completed_at=generated_at,
        ),
        _stage(
            "development_backtest",
            "passed" if development_passed else "failed",
            reason=(
                "Development optimization produced a frozen winner."
                if development_passed
                else "Development optimization did not produce a winner."
            ),
            evidence={
                "optimizer_status": winner.get("status"),
                "training_metrics": _clean_mapping(winner.get("training_metrics")),
                "validation_metrics": _clean_mapping(winner.get("validation_metrics")),
            },
            completed_at=generated_at,
        ),
        _stage(
            "robustness_testing",
            "passed" if robustness_passed else "failed",
            reason=(
                "Independent validation/holdout and execution stress met the robustness gate."
                if robustness_passed
                else "One or more independent validation, holdout, or execution-stress checks failed."
            ),
            evidence={
                "strength": strength,
                "holdout_metrics": _clean_mapping(winner.get("holdout_metrics")),
                "stress_metrics": _clean_mapping(winner.get("stress_metrics")),
                "execution_sensitivity": _clean_mapping(
                    winner.get("execution_sensitivity")
                ),
            },
            completed_at=generated_at,
        ),
        _stage(
            "adaptive_walk_forward",
            "passed" if walk_passed and comparison_passed else "failed",
            reason=(
                "Adaptive walk-forward completed and did not lose to the static baseline."
                if walk_passed and comparison_passed
                else "Adaptive walk-forward was missing, too thin, or inferior to the static baseline."
            ),
            evidence={
                "summary": walk_summary,
                "adaptive_learning": _clean_mapping(walk.get("adaptive_learning")),
                "static_summary": _clean_mapping(
                    _clean_mapping(walk.get("static_baseline")).get("summary")
                ),
                "comparison": comparison,
                "warnings": list(walk.get("warnings") or []),
            },
            completed_at=generated_at,
        ),
        _stage(
            "profitable_neighborhood",
            "passed" if neighborhood_passed else "failed",
            reason=(
                "At least one unseen fold showed a complete broad profitable neighborhood."
                if neighborhood_passed
                else "No complete broad profitable parameter neighborhood was established on unseen data."
            ),
            evidence=neighborhood,
            completed_at=generated_at,
        ),
        _stage(
            "overfitting_multiplicity",
            "passed" if multiplicity_passed else "failed",
            reason=(
                "Selection pressure was disclosed and protected by a pristine untouched holdout."
                if multiplicity_passed
                else "Multiplicity/overfitting evidence is incomplete or previously exposed validation data was reused."
            ),
            evidence=multiplicity,
            completed_at=generated_at,
        ),
        _stage(
            "independent_validation",
            "passed" if independent_passed else "failed",
            reason=(
                "Frozen rules cleared the independent cross-stock and final validation gates."
                if independent_passed
                else "Frozen rules did not clear every independent cross-stock/final validation gate."
            ),
            evidence={
                "generalization_summary": general_summary,
                "validation_data_quality": _clean_mapping(
                    result.get("validation_data_quality")
                ),
                "holdout_reuse_audit": _clean_mapping(
                    result.get("holdout_reuse_audit")
                ),
                "validation_evidence_reuse_audits": _clean_mapping(
                    result.get("validation_evidence_reuse_audits")
                ),
            },
            completed_at=generated_at,
        ),
        _stage(
            "paper_shadow_eligibility",
            "passed" if paper_eligible else "blocked",
            reason=(
                "Eligible for paper/shadow evaluation only; live deployment remains separately controlled."
                if paper_eligible
                else "Paper/shadow eligibility is blocked until every preceding experiment stage passes."
            ),
            evidence={
                "historical_validation_status": validation_status,
                "affects_live_execution": False,
                "live_deployment_approved": False,
            },
            completed_at=generated_at,
        ),
    ]

    run_key = candidate_run_key(
        strategy,
        validation_end=_clean_mapping(report.get("sampling_boundary")).get(
            "validation_end"
        )
        or report.get("generated_at"),
        method_version=int(report.get("validation_method_version") or 0),
        timeframe=str(report.get("timeframe") or ""),
    )
    signature_material = {
        "candidate_run_key": run_key,
        "candidate_symbols": sorted(str(item) for item in result.get("candidate_symbols") or []),
        "research_windows": _clean_mapping(result.get("research_windows")),
    }
    signature = hashlib.sha256(
        json.dumps(signature_material, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:30]
    return {
        "id": "exp-" + signature,
        "schema_version": EXPERIMENT_REGISTRY_VERSION,
        "experiment_signature": signature,
        "candidate_run_key": run_key,
        "status": "complete",
        "current_stage": "paper_shadow_eligibility",
        "promotion_status": (
            "paper_shadow_eligible" if paper_eligible else "research_only"
        ),
        "strategy_id": strategy.get("id"),
        "strategy_name": strategy.get("name") or result.get("strategy_name"),
        "strategy_family": {
            "id": strategy.get("autonomous_research_root_id")
            or strategy.get("parent_strategy_id")
            or strategy.get("id"),
            "name": strategy.get("name"),
            "category": strategy.get("category"),
            "direction": strategy.get("direction"),
        },
        "source_research": _source_research(library, strategy),
        "measurable_features": {
            "machine_rules": {
                key: value
                for key, value in normalize_machine_rules(
                    strategy.get("machine_rules")
                ).items()
                if value is not None
            },
            "feature_names": sorted(
                key
                for key, value in normalize_machine_rules(
                    strategy.get("machine_rules")
                ).items()
                if value is not None
            ),
            "unresolved_rules": list(strategy.get("unresolved_rules") or []),
        },
        "parameters": {
            "input_rules": normalize_machine_rules(strategy.get("machine_rules")),
            "optimized_rules": _clean_mapping(winner.get("optimized_rules")),
            "backtest_settings": _clean_mapping(optimization.get("backtest_settings")),
            "optimization_settings": _clean_mapping(
                optimization.get("optimization_settings")
            ),
            "optimized_backtest_settings": _clean_mapping(
                winner.get("optimized_backtest_settings")
            ),
        },
        "data_ranges": {
            "sampling_boundary": _clean_mapping(report.get("sampling_boundary")),
            "research_windows": _clean_mapping(result.get("research_windows")),
            "training_sessions": list(optimization.get("training_sessions") or []),
            "validation_sessions": list(optimization.get("validation_sessions") or []),
            "holdout_sessions": list(optimization.get("holdout_sessions") or []),
            "walk_forward_folds": [
                {
                    key: fold.get(key)
                    for key in (
                        "fold",
                        "history_start",
                        "history_end",
                        "embargo_start",
                        "embargo_end",
                        "external_test_start",
                        "external_test_end",
                    )
                }
                for fold in walk.get("folds") or []
                if isinstance(fold, Mapping)
            ],
        },
        "results": {
            "validation_status": validation_status,
            "global_score": result.get("global_score"),
            "training_metrics": _clean_mapping(winner.get("training_metrics")),
            "validation_metrics": _clean_mapping(winner.get("validation_metrics")),
            "holdout_metrics": _clean_mapping(winner.get("holdout_metrics")),
            "stress_metrics": _clean_mapping(winner.get("stress_metrics")),
        },
        "robustness_metrics": {
            "strength": strength,
            "generalization": general_summary,
            "profitable_neighborhood": neighborhood,
            "adaptive_vs_static": comparison,
            "overfitting_multiplicity": multiplicity,
        },
        "failure_reasons": gate_reasons,
        "lineage": {
            "parent_strategy_id": strategy.get("parent_strategy_id"),
            "root_strategy_id": strategy.get("autonomous_research_root_id"),
            "hypothesis_id": strategy.get("research_hypothesis_id"),
            "prior_hypothesis_id": None,
            "parent_experiment_ids": [],
        },
        "stages": stages,
        "created_at": generated_at,
        "started_at": generated_at,
        "completed_at": generated_at,
        "updated_at": generated_at,
    }


def build_failed_experiment(
    library: Mapping[str, Any],
    report: Mapping[str, Any],
    failure: Mapping[str, Any],
    strategy: Mapping[str, Any],
) -> dict[str, Any]:
    generated_at = str(report.get("generated_at") or _utc_iso())
    failed_stage = str(failure.get("failure_stage") or "candidate_generation")
    stage_alias = {
        "candidate_data": "candidate_generation",
        "optimization": "development_backtest",
        "walk_forward": "adaptive_walk_forward",
        "cross_stock_generalization": "independent_validation",
        "final_validation_gate": "independent_validation",
    }.get(failed_stage, failed_stage)
    if stage_alias not in EXPERIMENT_STAGE_ORDER:
        stage_alias = "candidate_generation"
    reason = str(failure.get("error") or "Experiment could not complete.")
    stages = []
    failed_index = EXPERIMENT_STAGE_ORDER.index(stage_alias)
    for index, name in enumerate(EXPERIMENT_STAGE_ORDER):
        if index < failed_index:
            status = "passed"
            stage_reason = "Stage completed before the recorded failure."
        elif index == failed_index:
            status = "failed"
            stage_reason = reason
        else:
            status = "blocked"
            stage_reason = f"Blocked because {stage_alias.replace('_', ' ')} did not complete."
        stages.append(
            _stage(
                name,
                status,
                reason=stage_reason,
                evidence={},
                completed_at=generated_at,
            )
        )
    run_key = candidate_run_key(
        strategy,
        validation_end=_clean_mapping(report.get("sampling_boundary")).get(
            "validation_end"
        )
        or report.get("generated_at"),
        method_version=int(report.get("validation_method_version") or 0),
        timeframe=str(report.get("timeframe") or ""),
    )
    signature = hashlib.sha256(
        f"{run_key}|failed|{stage_alias}".encode("utf-8")
    ).hexdigest()[:30]
    return {
        "id": "exp-" + signature,
        "schema_version": EXPERIMENT_REGISTRY_VERSION,
        "experiment_signature": signature,
        "candidate_run_key": run_key,
        "status": "failed",
        "current_stage": stage_alias,
        "promotion_status": "research_only",
        "strategy_id": strategy.get("id"),
        "strategy_name": strategy.get("name") or failure.get("strategy_name"),
        "strategy_family": {
            "id": strategy.get("autonomous_research_root_id")
            or strategy.get("parent_strategy_id")
            or strategy.get("id"),
            "name": strategy.get("name"),
            "category": strategy.get("category"),
            "direction": strategy.get("direction"),
        },
        "source_research": _source_research(library, strategy),
        "measurable_features": {
            "machine_rules": normalize_machine_rules(strategy.get("machine_rules")),
            "unresolved_rules": list(strategy.get("unresolved_rules") or []),
        },
        "parameters": {"input_rules": normalize_machine_rules(strategy.get("machine_rules"))},
        "data_ranges": {
            "sampling_boundary": _clean_mapping(report.get("sampling_boundary"))
        },
        "results": {"validation_status": "validation_failed"},
        "robustness_metrics": {},
        "failure_reasons": [reason],
        "lineage": {
            "parent_strategy_id": strategy.get("parent_strategy_id"),
            "root_strategy_id": strategy.get("autonomous_research_root_id"),
            "hypothesis_id": strategy.get("research_hypothesis_id"),
            "prior_hypothesis_id": None,
            "parent_experiment_ids": [],
        },
        "stages": stages,
        "created_at": generated_at,
        "started_at": generated_at,
        "completed_at": generated_at,
        "updated_at": generated_at,
    }


def merge_report_into_experiment_registry(
    library: dict[str, Any], report: Mapping[str, Any]
) -> dict[str, Any]:
    """Upsert one durable experiment per attempted finalist."""
    data = ensure_experiment_registry(library)
    strategies = {
        str(item.get("id") or ""): dict(item)
        for item in data.get("strategies") or []
        if isinstance(item, Mapping) and str(item.get("id") or "")
    }
    incoming: list[dict[str, Any]] = []
    for result in report.get("results") or []:
        if not isinstance(result, Mapping):
            continue
        strategy = strategies.get(str(result.get("strategy_id") or ""), {})
        incoming.append(build_completed_experiment(data, report, result, strategy))
    for failure in report.get("failed_finalists") or []:
        if not isinstance(failure, Mapping):
            continue
        strategy = strategies.get(str(failure.get("strategy_id") or ""), {})
        incoming.append(build_failed_experiment(data, report, failure, strategy))

    existing = [
        dict(item)
        for item in data.get("experiment_registry") or []
        if isinstance(item, Mapping)
    ]
    existing_by_candidate: dict[str, list[dict[str, Any]]] = {}
    incoming_ids = {str(item.get("id") or "") for item in incoming}
    for item in existing:
        existing_by_candidate.setdefault(
            str(item.get("strategy_id") or ""), []
        ).append(item)

    for record in incoming:
        active_match = next(
            (
                item
                for item in existing
                if str(item.get("candidate_run_key") or "")
                == str(record.get("candidate_run_key") or "")
                and str(item.get("status") or "") in ACTIVE_EXPERIMENT_STATUSES
            ),
            None,
        )
        if active_match is not None:
            record["id"] = active_match.get("id") or record.get("id")
            record["job_id"] = active_match.get("job_id")
            record["created_at"] = active_match.get("created_at") or record.get(
                "created_at"
            )
            record["started_at"] = active_match.get("started_at") or record.get(
                "started_at"
            )
        lineage = _clean_mapping(record.get("lineage"))
        parents = [
            str(item.get("id") or "")
            for item in existing_by_candidate.get(str(record.get("strategy_id") or ""), [])
            if str(item.get("id") or "") and str(item.get("id") or "") != str(record.get("id") or "")
        ]
        lineage["parent_experiment_ids"] = parents[:8]
        hypothesis_id = str(lineage.get("hypothesis_id") or "")
        if hypothesis_id:
            hypothesis = next(
                (
                    item
                    for item in data.get("research_hypotheses") or []
                    if isinstance(item, Mapping)
                    and str(item.get("id") or "") == hypothesis_id
                ),
                {},
            )
            lineage["prior_hypothesis_id"] = hypothesis.get("prior_hypothesis_id")
            if hypothesis.get("parent_hypothesis_id"):
                lineage["parent_hypothesis_id"] = hypothesis.get(
                    "parent_hypothesis_id"
                )
        record["lineage"] = lineage

    replaced_run_keys = {
        str(item.get("candidate_run_key") or "")
        for item in incoming
        if str(item.get("candidate_run_key") or "")
    }
    incoming_ids = {str(item.get("id") or "") for item in incoming}
    retained = [
        item
        for item in existing
        if str(item.get("id") or "") not in incoming_ids
        and str(item.get("candidate_run_key") or "") not in replaced_run_keys
    ]
    merged = incoming + retained
    merged.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    data["experiment_registry"] = merged[:MAX_EXPERIMENT_HISTORY]
    return data


def summarize_experiment(record: Mapping[str, Any]) -> dict[str, Any]:
    stages = [item for item in record.get("stages") or [] if isinstance(item, Mapping)]
    current_name = str(record.get("current_stage") or "")
    current = next(
        (item for item in stages if str(item.get("name") or "") == current_name),
        stages[-1] if stages else {},
    )
    failed = next(
        (item for item in stages if str(item.get("status") or "") in {"failed", "blocked"}),
        {},
    )
    reasons = [str(item) for item in record.get("failure_reasons") or [] if str(item)]
    reason = str(failed.get("reason") or (reasons[0] if reasons else current.get("reason") or ""))
    return {
        "id": record.get("id"),
        "strategy_name": record.get("strategy_name"),
        "status": record.get("status"),
        "stage": current_name,
        "stage_status": current.get("status"),
        "promotion_status": record.get("promotion_status"),
        "reason": reason,
        "updated_at": record.get("updated_at") or record.get("completed_at"),
        "hypothesis_id": _clean_mapping(record.get("source_research")).get(
            "hypothesis_id"
        ),
    }
