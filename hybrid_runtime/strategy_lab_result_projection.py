"""Versioned, bounded projection of persisted validation evidence; never scoring."""
from __future__ import annotations

from collections.abc import Mapping

PROJECTION_VERSION = 1
METRIC_FIELDS = "trade_count net_pnl return_pct win_rate_pct profit_factor max_drawdown_pct expectancy average_trade average_winner average_loser".split()
METRIC_BLOCKS = "training_metrics validation_metrics holdout_metrics stress_metrics full_metrics baseline_training_metrics".split()
WALK_FIELDS = "score label fold_count active_fold_count profitable_fold_count profitable_fold_pct temporal_coverage_pct profitable_scheduled_fold_pct external_trade_count external_net_pnl external_return_pct external_profit_factor max_fold_drawdown_pct median_fold_return_pct average_fold_return_pct selected_strategy_counts embargo_sessions adaptive_learning_enabled adaptive_experience_count adaptive_profitable_experience_count broad_profitable_neighborhood_fold_count incomplete_neighborhood_fold_count status note positive_fold_ratio".split()
STABILITY_FIELDS = "status label tested active positive positive_pct median_net_pnl worst_net_pnl best_net_pnl score note profitable_neighbor_ratio".split()
SENSITIVITY_FIELDS = "score label passes_validation_gate baseline_net_pnl profitable_multiplier_pct median_pnl_retention_pct first_unprofitable_multiplier note".split()
GATES = {
    "paper_execution_fidelity": "status label unsupported_management reason research_backtest_forces_session_flat paper_runner_persistent_manager",
    "historical_spread_audit": "status label symbol holdout_trade_count sampled_entry_count quote_count quote_feeds consolidated_sip_quotes coverage_pct maximum_quote_age_seconds modeled_spread_bps maximum_stress_multiplier tested_spread_ceiling_bps median_observed_spread_bps p90_observed_spread_bps max_observed_spread_bps provider_error post_selection_diagnostic note",
    "holdout_reuse_audit": "status pristine symbol timeframe holdout_sessions holdout_fingerprint prior_material_exposure_count material_overlap_threshold_pct note",
    "market_data_integrity": "mode corporate_action_reset_detected latest_price_reset_date reset_action_types split_detected latest_split_date rows_before rows_after discarded_pre_split_rows note",
}


def _pick(value, fields):
    """Membership, not truthiness: retain explicit null, zero, false and empty."""
    if not isinstance(value, Mapping):
        return {}
    result = {}
    for key in fields:
        if key not in value:
            continue
        item = value[key]
        if item is None or isinstance(item, (bool, int, float)):
            result[key] = item
        elif isinstance(item, str):
            result[key] = item[:4000]
        elif isinstance(item, list):
            # Explanation lists, never raw trials, trades or optimizer states.
            result[key] = [x[:2000] if isinstance(x, str) else x for x in item[:64]
                           if x is None or isinstance(x, (str, bool, int, float))]
        elif isinstance(item, Mapping):
            result[key] = {str(k): v for k, v in list(item.items())[:64]
                           if v is None or isinstance(v, (bool, int, float))}
    return result


def _aliases(source, fields, aliases):
    result = _pick(source, fields)
    if isinstance(source, Mapping):
        for canonical, legacy in aliases.items():
            if canonical not in source and legacy in source:
                result.update(_pick({canonical: source[legacy]}, [canonical]))
    return result


def _walk(source):
    if not isinstance(source, Mapping):
        return {}
    summary = _aliases(source.get("summary"), WALK_FIELDS, {
        "fold_count": "folds", "profitable_fold_count": "profitable_folds",
        "external_net_pnl": "total_pnl",
    })
    result = _pick(source, ("symbol", "warnings", "note"))
    folds = source.get("folds")
    if isinstance(folds, list):
        summary["recorded_fold_count"] = len(folds)
        summary.setdefault("fold_count", len(folds))
        result["folds"] = []
        for fold in folds[:6]:
            compact = _pick(fold, "fold history_start history_end external_test_start external_test_end history_session_count embargo_session_count embargo_start embargo_end test_session_count selected_strategy_id selected_strategy_name optimizer_status adaptive_learning_enabled adaptive_learning_cutoff validation_neighbor_candidate_count validation_neighbor_failure_count".split())
            if isinstance(fold, Mapping):
                for key in ("external_metrics", "internal_holdout_metrics", "static_baseline_external_metrics"):
                    if key in fold:
                        compact[key] = None if fold[key] is None else _pick(fold[key], METRIC_FIELDS)
                if "profitable_neighborhood" in fold:
                    compact["profitable_neighborhood"] = _pick(fold["profitable_neighborhood"], "winner_profitable tested_neighbor_count attempted_neighbor_count failed_neighbor_count complete profitable_neighbor_count profitable_neighbor_pct profitable_configuration_count broad_profitable note".split())
            result["folds"].append(compact)
        if len(folds) > 6:
            result["fold_details_truncated"] = True
    if type(summary.get("fold_count")) is int:
        summary["executed"] = summary["fold_count"] > 0
    if "label" in summary:
        summary["classification"] = summary["label"]
    result["summary"] = summary
    return result


def project_strategy_lab_result(result, *, run_id="", saved_at=""):
    """Current engine field names are authoritative; only named legacy aliases apply."""
    raw = result if isinstance(result, Mapping) else {}
    report = raw.get("report") if isinstance(raw.get("report"), Mapping) else {}
    winner = report.get("winner") if isinstance(report.get("winner"), Mapping) else {}
    projected = {
        "projection_version": PROJECTION_VERSION,
        "outcome": "strategy_lab_complete",
        "run_id": str(run_id or raw.get("run_id") or ""),
        "saved_at": str(saved_at),
        "ticker": str(raw.get("ticker") or "").upper(),
        "timeframe": raw.get("timeframe", ""),
        "history_days": raw.get("history_days", 0),
        "winner_strategy_id": winner.get("source_strategy_id", ""),
        "winner_strategy_name": winner.get("strategy_name", winner.get("source_strategy_name", "")),
        "evidence_verdict": _pick(raw.get("evidence_verdict"), "code label status reason tone research_tier paper_ready".split()),
        "strength": _pick(raw.get("strength"), "score raw_score_before_caps score_cap base_score walk_forward_score walk_forward_fold_count walk_forward_active_fold_count walk_forward_temporal_coverage_pct walk_forward_profitable_scheduled_pct execution_sensitivity_score execution_sensitivity_label execution_sensitivity_scope optimizer_status minimum_unseen_trades_for_high_confidence label independently_positive reasons note status reason".split()),
        "research_only": True, "affects_live_ranking": False, "affects_execution": False,
        "evidence_availability": {key: key in raw for key in ("walk_forward", "parameter_stability", "strength", "evidence_verdict", *GATES)},
    }
    for key in METRIC_BLOCKS:
        projected[key] = None if key in winner and winner[key] is None else _pick(winner.get(key), METRIC_FIELDS)
        projected["evidence_availability"][key] = key in winner
    walk = _walk(raw.get("walk_forward"))
    projected["walk_forward_summary"] = walk.get("summary", {})
    if "walk_forward" in raw:
        projected["walk_forward"] = None if raw["walk_forward"] is None else walk
    stability = _aliases(raw.get("parameter_stability"), STABILITY_FIELDS, {"tested": "tested_neighbor_count"})
    if "label" in stability:
        # Existing desktop tables display scalar details separately from execution status.
        stability["classification"] = stability["label"]
    if type(stability.get("tested")) is int:
        stability["executed"] = stability["tested"] > 0
    projected["parameter_stability"] = None if "parameter_stability" in raw and raw["parameter_stability"] is None else stability
    for key, fields in GATES.items():
        if key in raw:
            projected[key] = None if raw[key] is None else _pick(raw[key], fields.split())
    projected["optimizer_summary"] = _pick(report, "session_count strategies_tested resumed_strategy_count variants_tested rule_variants_tested execution_variants_tested adaptive_refinement_tests unique_configurations_tested training_sessions validation_sessions holdout_sessions warnings".split())
    projected["optimizer_summary"].update(_pick(winner, "status adequate_sample pre_holdout_status limitations execution_sensitivity_tests holdout_execution_sensitivity_tests".split()))
    for key in ("execution_sensitivity", "holdout_execution_sensitivity"):
        if key in winner:
            projected[key] = None if winner[key] is None else _pick(winner[key], SENSITIVITY_FIELDS)
    projected["validation_settings"] = _pick(report.get("optimization_settings"), "minimum_training_trades minimum_validation_trades enforce_historical_minimum_trades minimum_historical_trades training_fraction validation_fraction stress_cost_multiplier execution_sensitivity_multipliers maximum_drawdown_pct selection_mode".split())
    projected["backtest_limitations"] = _pick(report.get("winning_backtest"), ("limitations",)).get("limitations", [])
    projected["source_fidelity_recorded"] = "source_fidelity" in raw
    if "source_fidelity" in raw:
        projected["source_fidelity"] = _pick(raw["source_fidelity"], "status label reason findings warnings".split())
    return projected


def needs_projection(job):
    """Pure predicate. A newer projection must never be downgraded."""
    from .contracts import JobStatus
    version = (job.result or {}).get("projection_version")
    return (job.job_type == "strategy.strategy_lab" and job.status == JobStatus.COMPLETE
            and not (type(version) is int and version >= PROJECTION_VERSION))
