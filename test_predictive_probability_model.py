from datetime import datetime, timedelta, timezone

from predictive_probability_model import (
    build_portable_probability_model,
    score_scan_result_probability,
)


def synthetic_dataset():
    records = []
    start = datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc)
    symbols = ["AAA", "BBB", "CCC", "DDD"]
    feature_columns = [
        "feature__momentum",
        "feature__volume_acceleration",
        "feature__above_vwap",
        "feature__pullback_depth",
        "feature__breakout_extension",
        "feature__range_expansion",
        "feature__context_typical_price",
    ]
    for session_index in range(18):
        session = f"2026-07-{session_index + 1:02d}"
        for row_index in range(24):
            symbol = symbols[row_index % len(symbols)]
            momentum = ((row_index % 12) - 5.5) / 5.5
            volume = ((row_index * 3 + session_index) % 11) / 5.0
            above_vwap = 1.0 if row_index % 3 != 0 else 0.0
            pullback = ((row_index + session_index) % 9) / 9.0
            breakout = ((row_index * 2 + session_index) % 13) / 10.0
            expansion = ((row_index * 5 + session_index) % 17) / 10.0
            signal = (
                1.8 * momentum
                + 0.55 * volume
                + 0.8 * above_vwap
                - 0.7 * pullback
                + 0.45 * breakout
                + 0.25 * expansion
            )
            # Small deterministic disturbance prevents a perfectly separable toy set.
            disturbance = -0.7 if (row_index + session_index) % 11 == 0 else 0.0
            target = signal + disturbance > 0.7
            records.append(
                {
                    "symbol": symbol,
                    "session": session,
                    "timestamp": (
                        start
                        + timedelta(days=session_index, minutes=row_index * 5)
                    ).isoformat(),
                    "feature__momentum": momentum,
                    "feature__volume_acceleration": volume,
                    "feature__above_vwap": above_vwap,
                    "feature__pullback_depth": pullback,
                    "feature__breakout_extension": breakout,
                    "feature__range_expansion": expansion,
                    "feature__context_typical_price": 10.0 + session_index,
                    "label__target_before_stop_15bar": target,
                }
            )
    return {
        "records": records,
        "feature_columns": feature_columns,
        "profit_target_pct": 1.0,
        "stop_loss_pct": 0.75,
        "session_mode": "regular",
    }


def passing_generalization():
    return {
        "status": "EVALUATED",
        "oos_rows": 220,
        "roc_auc": 0.64,
        "brier_skill_vs_naive": 0.08,
    }


def test_builds_ready_portable_shadow_model_without_context_features():
    model = build_portable_probability_model(
        synthetic_dataset(),
        target_horizon=15,
        target_mode="target_before_stop",
        generalization=passing_generalization(),
        min_train_sessions=6,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=80,
        minimum_oos_rows=100,
        minimum_auc=0.52,
    )

    assert model["status"] == "READY_FOR_SHADOW_SCORING"
    assert model["shadow_scoring_enabled"] is True
    assert model["research_only"] is True
    assert model["affects_live_ranking"] is False
    assert model["affects_execution"] is False
    assert model["validation"]["oos_rows"] >= 100
    assert model["validation"]["roc_auc"] > 0.52
    assert model["validation"]["brier_skill_vs_naive"] > 0
    assert model["feature_count"] >= 5
    assert not any(
        column.startswith("feature__context_")
        for column in model["feature_columns"]
    )


def test_scores_fresh_result_as_research_only_probability():
    model = build_portable_probability_model(
        synthetic_dataset(),
        target_horizon=15,
        target_mode="target_before_stop",
        generalization=passing_generalization(),
        min_train_sessions=6,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=80,
        minimum_oos_rows=100,
        minimum_auc=0.52,
    )
    result = {
        "symbol": "ZZZ",
        "market_features": {
            "features": {
                "momentum": 0.95,
                "volume_acceleration": 1.8,
                "above_vwap": True,
                "pullback_depth": 0.1,
                "breakout_extension": 1.0,
                "range_expansion": 1.3,
            }
        },
    }

    score = score_scan_result_probability(model, result)
    assert score["status"] == "SCORED"
    assert 0.0 <= score["probability"] <= 1.0
    assert score["probability"] > 0.5
    assert score["feature_coverage"] == 1.0
    assert score["research_only"] is True
    assert score["affects_live_ranking"] is False
    assert score["affects_execution"] is False


def test_feature_coverage_gate_blocks_misleading_probability():
    model = build_portable_probability_model(
        synthetic_dataset(),
        target_horizon=15,
        target_mode="target_before_stop",
        generalization=passing_generalization(),
        min_train_sessions=6,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=80,
        minimum_oos_rows=100,
        minimum_auc=0.52,
    )
    result = {
        "symbol": "ZZZ",
        "market_features": {"features": {"momentum": 0.9}},
    }

    score = score_scan_result_probability(model, result)
    assert score["status"] == "INSUFFICIENT_FEATURE_COVERAGE"
    assert score["feature_coverage"] < score["required_feature_coverage"]


def test_generalization_gate_keeps_weak_candidate_research_only():
    model = build_portable_probability_model(
        synthetic_dataset(),
        target_horizon=15,
        target_mode="target_before_stop",
        generalization={
            "status": "EVALUATED",
            "oos_rows": 220,
            "roc_auc": 0.49,
            "brier_skill_vs_naive": -0.03,
        },
        min_train_sessions=6,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=80,
        minimum_oos_rows=100,
        minimum_auc=0.52,
    )

    assert model["status"] == "RESEARCH_ONLY"
    assert model["shadow_scoring_enabled"] is False
    assert any("Held-out-stock" in reason for reason in model["gate_reasons"])
