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
        "horizons": [5, 15, 30, 60],
        "completed_at": "2026-08-29T22:00:00+00:00",
        "dataset_summary": {
            "row_count": 12345,
            "symbols_with_data": 3,
        },
        "evaluation": {"status": "EVALUATED"},
        "probability_model": {
            "id": "logistic-1",
            "status": "READY_FOR_SHADOW_SCORING",
            "shadow_scoring_enabled": True,
        },
        "similarity_validation": {
            "status": "EVALUATED",
            "automatic_subset_symbols": ["AAA", "BBB", "CCC"],
        },
        "ticker_specific": {
            "status": "EVALUATED",
            "automatic_subset_symbols": ["AAA", "CCC"],
        },
        "stock_learning_router": {
            "status": "EVALUATED",
            "symbols_compared": 2,
            "symbols_with_clear_route": 1,
            "route_counts": {
                "same_ticker_history": 1,
                "similarity_weighted_transfer": 0,
                "broad_cross_stock_transfer": 0,
            },
        },
        "historical_head_to_head": {
            "status": "PROVISIONAL_HISTORICAL_LEADER",
            "leader_model_id": "logistic-1",
            "leader_model_family": "Logistic Regression",
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
    assert status["horizons"] == [5, 15, 30, 60]
    assert status["similarity_status"] == "EVALUATED"
    assert status["similarity_symbols"] == ["AAA", "BBB", "CCC"]
    assert status["ticker_specific_status"] == "EVALUATED"
    assert status["ticker_specific_symbols"] == ["AAA", "CCC"]
    assert status["learning_router_status"] == "EVALUATED"
    assert status["learning_router_symbols_compared"] == 2
    assert status["learning_router_clear_routes"] == 1
    assert status["learning_router_route_counts"]["same_ticker_history"] == 1
    assert status["historical_leader_model_id"] == "logistic-1"
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
    ) as boosted, patch.object(
        backfill,
        "ticker_specific_walk_forward_logistic_baseline",
        return_value={
            "status": "EVALUATED",
            "oos_rows": 180,
            "roc_auc": 0.63,
            "brier_skill_vs_naive": 0.05,
            "predictions": [{"probability": 0.58}],
        },
    ) as ticker_specific, patch.object(
        backfill,
        "similarity_weighted_leave_one_symbol_out_walk_forward_logistic_baseline",
        return_value={
            "status": "EVALUATED",
            "paired_oos_rows": 250,
            "similarity_minus_baseline_auc": 0.01,
            "predictions": [{"probability": 0.6}],
        },
    ) as similarity, patch.object(
        backfill,
        "build_stock_learning_router",
        return_value={
            "status": "EVALUATED",
            "symbols_compared": 5,
            "symbols_with_clear_route": 3,
            "route_counts": {
                "same_ticker_history": 2,
                "similarity_weighted_transfer": 1,
                "broad_cross_stock_transfer": 0,
            },
        },
    ) as learning_router, patch.object(
        backfill,
        "build_historical_model_head_to_head",
        return_value={
            "status": "PROVISIONAL_HISTORICAL_LEADER",
            "leader_model_id": "boosted-1",
            "leader_model_family": "Gradient Boosting",
        },
    ) as head_to_head:
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
    assert baseline.call_count == 4
    assert held_out.call_count == 1
    assert portable.call_count == 1
    assert boosted.call_count == 1
    assert ticker_specific.call_count == 1
    assert similarity.call_count == 1
    assert learning_router.call_count == 1
    assert head_to_head.call_count == 1
    kwargs = build_dataset.call_args.kwargs
    assert kwargs["timeframe"] == "1Min"
    assert kwargs["session_limit"] == 30
    assert kwargs["horizons"] == (5, 15, 30, 60)
    assert kwargs["observation_stride_bars"] == 5
    assert kwargs["require_full_horizon"] is True
    assert result["origin"] == "automatic_cloud_backfill"
    assert result["dataset_summary"]["row_count"] == 5000
    assert "predictions" not in result["evaluation"]
    assert "predictions" not in result["generalization"]
    assert sorted(result["horizon_evaluations"]) == ["15", "30", "5", "60"]
    assert result["ticker_specific"]["status"] == "EVALUATED"
    assert "predictions" not in result["ticker_specific"]
    assert result["ticker_specific"]["automatic_subset_symbols"] == [
        "AAA", "BBB", "CCC", "DDD", "EEE"
    ]
    assert result["similarity_validation"]["status"] == "EVALUATED"
    assert "predictions" not in result["similarity_validation"]
    assert result["stock_learning_router"]["status"] == "EVALUATED"
    assert result["stock_learning_router"]["symbols_with_clear_route"] == 3
    assert result["historical_head_to_head"]["leader_model_id"] == "boosted-1"
    assert result["probability_model"]["shadow_scoring_enabled"] is True
    assert [item["id"] for item in result["probability_models"]] == [
        "logistic-1",
        "boosted-1",
    ]
    assert result["boosted_probability_model"]["model_type"] == "portable_gradient_boosted_trees"
    assert result["model_suite_version"] == backfill.MODEL_SUITE_VERSION
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
    assert 'PREDICTIVE_ML_BACKFILL_HORIZONS' in workflow
    assert 'PREDICTIVE_ML_SIMILARITY_MAX_SYMBOLS' in workflow
    assert 'PREDICTIVE_ML_TICKER_SPECIFIC_MAX_SYMBOLS' in workflow
    assert 'predictive_ml_backfill.py' in workflow
    assert 'predictive_boosted_probability_model.py' in workflow
    assert 'predictive_learning_router.py' in workflow



def test_accelerated_defaults_use_more_history_and_multiple_horizons():
    config = backfill.build_backfill_configuration({})
    assert config["trading_days"] == 45
    assert config["horizon"] == 15
    assert config["horizons"] == [5, 15, 30, 60]
    assert len(config["symbols"]) == 24
    assert backfill.MODEL_SUITE_VERSION == 5


def test_explicit_primary_horizon_is_added_without_discarding_other_labels():
    config = backfill.build_backfill_configuration(
        {},
        {
            "symbols": ["AAA", "BBB", "CCC", "DDD", "EEE"],
            "horizon": 20,
            "horizons": "5,15,30,60",
        },
    )
    assert config["horizon"] == 20
    assert config["horizons"] == [5, 15, 20, 30, 60]


def test_similarity_subset_spreads_across_full_universe():
    symbols = [f"S{i:02d}" for i in range(24)]
    chosen = backfill._spread_symbol_subset(symbols, maximum=10)
    assert len(chosen) == 10
    assert chosen[0] == "S00"
    assert chosen[-1] == "S23"
    assert len(set(chosen)) == 10


def test_ticker_specific_subset_prioritizes_sdot_then_spreads_remaining_slots():
    symbols = ["AAA", "BBB", "CCC", "SDOT", "DDD", "EEE", "FFF", "GGG"]
    chosen = backfill._priority_spread_symbol_subset(
        symbols,
        maximum=4,
        priority=("SDOT",),
    )
    assert len(chosen) == 4
    assert chosen[0] == "SDOT"
    assert len(set(chosen)) == 4
    assert all(symbol in symbols for symbol in chosen)


def test_ticker_specific_subset_honors_explicit_priority_order():
    symbols = ["AAA", "BBB", "SDOT", "REAX", "CCC", "DDD"]
    chosen = backfill._priority_spread_symbol_subset(
        symbols,
        maximum=3,
        priority=("REAX", "SDOT"),
    )
    assert chosen[:2] == ["REAX", "SDOT"]
    assert len(chosen) == 3



def test_similarity_subset_can_guarantee_ticker_specific_coverage():
    symbols = [f"S{i:02d}" for i in range(24)]
    ticker_symbols = ["S00", "S07", "S12", "S18", "S23"]
    chosen = backfill._priority_spread_symbol_subset(
        symbols,
        maximum=10,
        priority=ticker_symbols,
    )
    assert chosen[: len(ticker_symbols)] == ticker_symbols
    assert len(chosen) == 10
    assert all(symbol in chosen for symbol in ticker_symbols)
