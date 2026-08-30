"""Print a compact, non-secret summary of the latest predictive ML backfill."""

from __future__ import annotations

import json

from cloud_research_worker import build_store


def pick_metrics(report):
    if not isinstance(report, dict):
        return {}
    keys = (
        "status",
        "oos_rows",
        "fold_count",
        "roc_auc",
        "brier_score",
        "naive_brier_score",
        "brier_skill_vs_naive",
        "accuracy",
        "positive_rate",
        "held_out_symbols",
        "symbol_count",
        "session_count",
        "feature_count",
        "similarity_minus_baseline_auc",
        "similarity_minus_baseline_brier",
        "baseline_roc_auc",
        "similarity_roc_auc",
        "baseline_brier_score",
        "similarity_brier_score",
        "baseline_brier_skill_vs_naive",
        "similarity_brier_skill_vs_naive",
    )
    return {key: report.get(key) for key in keys if key in report}


def model_summary(model):
    if not isinstance(model, dict):
        return {}
    validation = model.get("validation") if isinstance(model.get("validation"), dict) else {}
    generalization = (
        model.get("generalization_gate")
        if isinstance(model.get("generalization_gate"), dict)
        else {}
    )
    return {
        "id": model.get("id"),
        "family": model.get("model_family"),
        "model_type": model.get("model_type"),
        "status": model.get("status"),
        "shadow_scoring_enabled": bool(model.get("shadow_scoring_enabled")),
        "target_horizon": model.get("target_horizon"),
        "trained_rows": model.get("trained_rows"),
        "trained_sessions": model.get("trained_sessions"),
        "feature_count": model.get("feature_count"),
        "validation": pick_metrics(validation),
        "generalization_gate": pick_metrics(generalization),
        "calibration": model.get("calibration"),
        "gate_reasons": model.get("gate_reasons"),
    }


def main():
    data = build_store().load_latest()
    runs = [item for item in data.get("predictive_ml_runs") or [] if isinstance(item, dict)]
    run = runs[0] if runs else {}
    system = data.get("research_system") if isinstance(data.get("research_system"), dict) else {}
    backfill_status = (
        system.get("predictive_ml_backfill_status")
        if isinstance(system.get("predictive_ml_backfill_status"), dict)
        else {}
    )
    horizons = run.get("horizon_evaluations") if isinstance(run.get("horizon_evaluations"), dict) else {}
    similarity = run.get("similarity_validation") if isinstance(run.get("similarity_validation"), dict) else {}
    h2h = run.get("historical_head_to_head") if isinstance(run.get("historical_head_to_head"), dict) else {}
    models = [model_summary(item) for item in run.get("probability_models") or [] if isinstance(item, dict)]

    summary = {
        "run_id": run.get("id"),
        "completed_at": run.get("completed_at"),
        "symbols": run.get("symbols"),
        "trading_days": run.get("trading_days"),
        "horizons": run.get("horizons"),
        "dataset_summary": run.get("dataset_summary"),
        "backfill_status": backfill_status,
        "horizon_evaluations": {str(k): pick_metrics(v) for k, v in horizons.items()},
        "primary_generalization": pick_metrics(run.get("generalization") or {}),
        "models": models,
        "similarity_validation": {
            **pick_metrics(similarity),
            "automatic_subset_symbols": similarity.get("automatic_subset_symbols"),
            "by_symbol": [
                {
                    "symbol": item.get("symbol"),
                    **pick_metrics(item),
                }
                for item in similarity.get("by_symbol") or []
                if isinstance(item, dict)
            ],
        },
        "historical_head_to_head": h2h,
    }
    print("PREDICTIVE_V3_SUMMARY_START")
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    print("PREDICTIVE_V3_SUMMARY_END")


if __name__ == "__main__":
    main()
