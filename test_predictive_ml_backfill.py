from datetime import datetime, timezone
from unittest.mock import patch

import predictive_ml_backfill as backfill


def test_choose_backfill_symbols_prioritizes_recent_live_observations():
    library = {
        "research_system": {
            "live_learning_observations": [
                {"symbol": "NEW2", "observed_at": "2026-08-29T15:10:00Z"},
                {"symbol": "NEW1", "observed_at": "2026-08-29T15:20:00Z"},
                {"symbol": "NEW2", "observed_at": "2026-08-29T15:00:00Z"},
            ]
        },
        "predictive_ml_runs": [
            {
                "completed_at": "2026-08-28T12:00:00Z",
                "symbols": ["OLD1", "OLD2"],
            }
        ],
    }
    symbols = backfill.choose_backfill_symbols(
        library,
        configured_symbols="ANCHOR1 ANCHOR2 ANCHOR3",
        maximum=6,
    )
    assert symbols == ["NEW1", "NEW2", "OLD1", "OLD2", "ANCHOR1", "ANCHOR2"]


def test_merge_backfill_result_persists_visible_status_and_run():
    result = {
        "id": "auto-ml-1",
        "origin": "automatic_cloud_backfill",
        "symbols": ["AAA", "BBB", "CCC"],
        "trading_days": 30,
        "horizon": 15,
        "completed_at": "2026-08-29T22:00:00+00:00",
        "dataset_summary": {
            "row_count": 12345,
            "symbols_with_data": 3,
        },
        "evaluation": {"status": "EVALUATED"},
        "probability_model": {
            "status": "READY_FOR_SHADOW_SCORING",
            "shadow_scoring_enabled": True,
        },
    }
    library = {
        "predictive_ml_runs": [{"id": "older"}],
        "research_system": {},
    }
    merged = backfill.merge_backfill_result_into_library(library, result)
    assert merged["predictive_ml_runs"][0]["id"] == "auto-ml-1"
    assert merged["predictive_ml_runs"][1]["id"] == "older"
    status = merged["research_system"]["predictive_ml_backfill_status"]
    assert status["status"] == "complete"
    assert status["labeled_rows"] == 12345
    assert status["symbols_with_data"] == 3
    assert status["shadow_scoring_enabled"] is True
    assert status["affects_live_ranking"] is False
    assert status["affects_execution"] is False


class FakeMarket:
    historical_feed = "sip"


def test_run_backfill_reuses_causal_dataset_and_validation_stack():
    dataset = {
        "records": [{"symbol": "AAA", "session": "2026-08-01"}],
        "row_count": 5000,
        "symbols_with_data": 5,
        "feature_columns": ["feature__x"],
        "label_columns": ["label__target_before_stop_15bar"],
        "profit_target_pct": 1.0,
        "stop_loss_pct": 0.75,
        "session_mode": "regular",
    }
    evaluation = {
        "status": "EVALUATED",
        "oos_rows": 900,
        "roc_auc": 0.60,
        "brier_skill_vs_naive": 0.04,
        "predictions": [{"probability": 0.6}],
    }
    generalization = {
        "status": "EVALUATED",
        "oos_rows": 400,
        "roc_auc": 0.57,
        "brier_skill_vs_naive": 0.02,
        "predictions": [{"probability": 0.55}],
    }
    model = {
        "id": "logistic-1",
        "model_type": "portable_numeric_logistic_regression",
        "status": "READY_FOR_SHADOW_SCORING",
        "shadow_scoring_enabled": True,
        "research_only": True,
        "affects_live_ranking": False,
    }
    boosted_model = {
        "id": "boosted-1",
        "model_type": "portable_gradient_boosted_trees",
        "status": "READY_FOR_SHADOW_SCORING",
        "shadow_scoring_enabled": True,
        "research_only": True,
        "affects_live_ranking": False,
    }

    with patch.object(
        backfill,
        "build_cross_stock_training_dataset",
        return_value=dataset,
    ) as build_dataset, patch.object(
        backfill,
        "walk_forward_logistic_baseline",
        return_value=evaluation,
    ) as baseline, patch.object(
        backfill,
        "leave_one_symbol_out_walk_forward_logistic_baseline",
        return_value=generalization,
    ) as held_out, patch.object(
        backfill,
        "build_portable_probability_model",
        return_value=model,
    ) as portable, patch.object(
        backfill,
        "build_boosted_probability_model",
        return_value=boosted_model,
    ) as boosted:
        result = backfill.run_predictive_ml_backfill(
            FakeMarket(),
            {},
            payload={
                "symbols": ["AAA", "BBB", "CCC", "DDD", "EEE"],
                "trading_days": 30,
                "horizon": 15,
            },
            now=datetime(2026, 8, 29, 22, 0, tzinfo=timezone.utc),
        )

    assert build_dataset.call_count == 1
    assert baseline.call_count == 1
    assert held_out.call_count == 1
    assert portable.call_count == 1
    assert boosted.call_count == 1
    kwargs = build_dataset.call_args.kwargs
    assert kwargs["timeframe"] == "1Min"
    assert kwargs["session_limit"] == 30
    assert kwargs["observation_stride_bars"] == 5
    assert kwargs["require_full_horizon"] is True
    assert result["origin"] == "automatic_cloud_backfill"
    assert result["dataset_summary"]["row_count"] == 5000
    assert "predictions" not in result["evaluation"]
    assert "predictions" not in result["generalization"]
    assert result["probability_model"]["shadow_scoring_enabled"] is True
    assert [item["id"] for item in result["probability_models"]] == [
        "logistic-1",
        "boosted-1",
    ]
    assert result["boosted_probability_model"]["model_type"] == "portable_gradient_boosted_trees"
    assert result["model_suite_version"] == backfill.MODEL_SUITE_VERSION
    assert result["ticker_specific"]["status"] == "SKIPPED_FOR_SPEED"
    assert result["research_only"] is True
    assert result["affects_live_ranking"] is False


def test_worker_and_ui_are_wired_for_automatic_backfill():
    worker = open("cloud_research_worker.py", encoding="utf-8").read()
    app = open("trading_intelligence_app.py", encoding="utf-8").read()
    workflow = open(
        ".github/workflows/continuous-trading-research.yml",
        encoding="utf-8",
    ).read()

    assert 'job_type == "predictive_ml_backfill"' in worker
    assert "ensure_predictive_ml_backfill_job" in worker
    assert "run_predictive_ml_backfill" in worker
    assert '"predictive_ml_backfill_status"' in app
    assert "Automatic ML backfill" in app
    assert 'PREDICTIVE_ML_BACKFILL_TRADING_DAYS' in workflow
    assert 'predictive_ml_backfill.py' in workflow
    assert 'predictive_boosted_probability_model.py' in workflow
