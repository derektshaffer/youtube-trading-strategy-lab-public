from __future__ import annotations

from experiment_registry import (
    EXPERIMENT_STAGE_ORDER,
    begin_experiments,
    candidate_run_key,
    merge_report_into_experiment_registry,
    select_unseen_experiment_candidates,
    summarize_experiment,
)


def candidate():
    return {
        "id": "strategy-1",
        "name": "RVOL continuation",
        "category": "momentum",
        "direction": "long",
        "research_hypothesis_id": "hyp-1",
        "research_run_id": "research-1",
        "source_type": "autonomous_web_research",
        "source_id": "hyp-1",
        "source_title": "Grounded research",
        "machine_rules": {"min_relative_volume": 2.5, "above_vwap": True},
    }


def completed_report():
    return {
        "generated_at": "2026-09-02T20:00:00Z",
        "validation_method_version": 7,
        "timeframe": "5Min",
        "eligible_strategies": 3,
        "strategies_with_opportunities": 2,
        "deep_strategies_attempted": 1,
        "sampling_boundary": {
            "discovery_cutoff": "2026-03-06T05:00:00+00:00",
            "validation_start": "2026-03-06T05:00:00+00:00",
            "validation_end": "2026-09-02T04:00:00+00:00",
        },
        "results": [
            {
                "strategy_id": "strategy-1",
                "strategy_name": "RVOL continuation",
                "anchor_symbol": "AAA",
                "candidate_symbols": ["AAA", "BBB", "CCC"],
                "priority_score": 88,
                "validation_status": "validated",
                "global_score": 84,
                "gate_reasons": [],
                "validation_data_quality": {"complete": True},
                "holdout_reuse_audit": {"pristine": True},
                "validation_evidence_reuse_audits": {
                    "AAA": {"pristine": True},
                    "BBB": {"pristine": True},
                    "CCC": {"pristine": True},
                },
                "research_windows": {
                    "AAA": {"start_date": "2026-03-06", "end_date": "2026-09-01"}
                },
                "strength": {
                    "score": 82,
                    "label": "STRONG",
                    "independently_positive": True,
                },
                "generalization": {
                    "summary": {
                        "score": 78,
                        "active_symbols": 3,
                        "profitable_symbol_pct": 66.7,
                    }
                },
                "optimization_report": {
                    "unique_configurations_tested": 144,
                    "training_sessions": ["2026-03-06"],
                    "validation_sessions": ["2026-07-01"],
                    "holdout_sessions": ["2026-08-31", "2026-09-01"],
                    "backtest_settings": {"starting_cash": 10000},
                    "optimization_settings": {"selection_mode": "validated"},
                    "winner": {
                        "status": "VALIDATED",
                        "optimized_rules": {"min_relative_volume": 2.75},
                        "optimized_backtest_settings": {"starting_cash": 10000},
                        "training_metrics": {"net_pnl": 100},
                        "validation_metrics": {"net_pnl": 60},
                        "holdout_metrics": {"net_pnl": 40},
                        "stress_metrics": {"net_pnl": 20},
                        "execution_sensitivity": {"status": "PASS"},
                    },
                },
                "walk_forward": {
                    "summary": {
                        "fold_count": 3,
                        "active_fold_count": 3,
                        "profitable_fold_count": 2,
                        "broad_profitable_neighborhood_fold_count": 1,
                        "incomplete_neighborhood_fold_count": 0,
                    },
                    "comparison": {
                        "enabled": True,
                        "verdict": "ADAPTIVE BETTER",
                        "adaptive_added_value": True,
                    },
                    "adaptive_learning": {"enabled": True, "experience_count": 3},
                    "static_baseline": {"summary": {"score": 60}},
                    "folds": [
                        {
                            "fold": 1,
                            "history_start": "2026-03-06",
                            "history_end": "2026-06-30",
                            "external_test_start": "2026-07-02",
                            "external_test_end": "2026-07-03",
                            "profitable_neighborhood": {
                                "broad_profitable": True,
                                "attempted_neighbor_count": 4,
                                "profitable_neighbor_count": 3,
                                "failed_neighbor_count": 0,
                                "profitable_neighbor_pct": 75.0,
                            },
                        }
                    ],
                },
            }
        ],
        "failed_finalists": [],
    }


def test_completed_experiment_records_full_pipeline_and_candidate_truth_boundary():
    library = {
        "strategies": [candidate()],
        "research_hypotheses": [
            {
                "id": "hyp-1",
                "statement": "High RVOL may predict continuation.",
                "supporting_source_ids": ["src-1"],
                "contradicting_source_ids": ["src-2"],
                "source_quality_score": 81,
            }
        ],
    }
    merged = merge_report_into_experiment_registry(library, completed_report())
    record = merged["experiment_registry"][0]

    assert [item["name"] for item in record["stages"]] == list(EXPERIMENT_STAGE_ORDER)
    assert record["source_research"]["truth_status"] == "candidate_hypothesis_not_established_truth"
    assert record["promotion_status"] == "paper_shadow_eligible"
    assert record["robustness_metrics"]["adaptive_vs_static"]["verdict"] == "ADAPTIVE BETTER"
    assert record["robustness_metrics"]["profitable_neighborhood"]["broad_profitable_fold_count"] == 1
    assert record["robustness_metrics"]["overfitting_multiplicity"]["configurations_tested"] == 144
    assert record["data_ranges"]["holdout_sessions"] == ["2026-08-31", "2026-09-01"]
    assert record["parameters"]["optimized_rules"]["min_relative_volume"] == 2.75
    assert record["stages"][-1]["evidence"]["affects_live_execution"] is False


def test_missing_neighborhood_blocks_paper_shadow_without_rewriting_historical_result():
    report = completed_report()
    report["results"][0]["walk_forward"]["summary"]["broad_profitable_neighborhood_fold_count"] = 0
    report["results"][0]["walk_forward"]["folds"][0]["profitable_neighborhood"]["broad_profitable"] = False

    merged = merge_report_into_experiment_registry(
        {"strategies": [candidate()]}, report
    )
    record = merged["experiment_registry"][0]
    neighborhood = next(
        item for item in record["stages"] if item["name"] == "profitable_neighborhood"
    )

    assert record["results"]["validation_status"] == "validated"
    assert neighborhood["status"] == "failed"
    assert record["promotion_status"] == "research_only"
    assert record["stages"][-1]["status"] == "blocked"


def test_exact_same_cutoff_protocol_and_rules_are_deduplicated_but_changed_rules_run():
    first = candidate()
    run_key = candidate_run_key(
        first,
        validation_end="2026-09-02T04:00:00Z",
        method_version=7,
        timeframe="5Min",
    )
    library = {"experiment_registry": [{"id": "exp-existing", "candidate_run_key": run_key}]}
    changed = {**candidate(), "machine_rules": {"min_relative_volume": 3.0}}

    selected, duplicates = select_unseen_experiment_candidates(
        library,
        [first, changed],
        validation_end="2026-09-02T04:00:00Z",
        method_version=7,
        timeframe="5Min",
    )

    assert [item["machine_rules"]["min_relative_volume"] for item in selected] == [3.0]
    assert duplicates[0]["existing_experiment_id"] == "exp-existing"


def test_running_experiment_is_durable_and_same_job_can_retry_it():
    library = begin_experiments(
        {"strategies": [candidate()]},
        [candidate()],
        validation_end="2026-09-02T04:00:00Z",
        method_version=7,
        timeframe="5Min",
        job_id="job-1",
    )
    record = library["experiment_registry"][0]

    assert record["status"] == "running"
    assert record["current_stage"] == "development_backtest"
    assert record["source_research"]["truth_status"] == "candidate_hypothesis_not_established_truth"
    assert record["stages"][2]["status"] == "pending"

    selected, duplicates = select_unseen_experiment_candidates(
        library,
        [candidate()],
        validation_end="2026-09-02T04:00:00Z",
        method_version=7,
        timeframe="5Min",
        job_id="job-1",
    )
    assert len(selected) == 1
    assert duplicates == []


def test_failed_experiment_terminalizes_downstream_stages_with_reason():
    report = {
        "generated_at": "2026-09-02T20:00:00Z",
        "validation_method_version": 7,
        "timeframe": "5Min",
        "sampling_boundary": {"validation_end": "2026-09-02T04:00:00Z"},
        "results": [],
        "failed_finalists": [
            {
                "strategy_id": "strategy-1",
                "strategy_name": "RVOL continuation",
                "failure_stage": "walk_forward",
                "error": "Provider could not return enough folds.",
            }
        ],
    }
    merged = merge_report_into_experiment_registry(
        {"strategies": [candidate()]}, report
    )
    record = merged["experiment_registry"][0]
    summary = summarize_experiment(record)

    assert record["status"] == "failed"
    assert record["current_stage"] == "adaptive_walk_forward"
    assert summary["reason"] == "Provider could not return enough folds."
    assert record["stages"][-1]["status"] == "blocked"


def test_registry_upsert_is_idempotent_and_retains_prior_lineage():
    library = {"strategies": [candidate()]}
    first = merge_report_into_experiment_registry(library, completed_report())
    second = merge_report_into_experiment_registry(first, completed_report())

    assert len(second["experiment_registry"]) == 1
    assert second["experiment_registry"][0]["lineage"]["parent_experiment_ids"] == []
