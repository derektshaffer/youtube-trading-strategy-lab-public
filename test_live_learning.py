from datetime import datetime, timedelta, timezone

from live_learning import (
    build_shadow_observation,
    earliest_pending_observed_at,
    mature_shadow_observations,
    merge_shadow_observations,
    pending_symbols,
)


def scan_result(symbol="SDOT", price=10.0, trade_timestamp=None):
    metrics = {
        "price": price,
        "relative_volume": 4.2,
        "vwap": 9.8,
    }
    if trade_timestamp is not None:
        metrics["trade_timestamp"] = trade_timestamp
    return {
        "symbol": symbol,
        "metrics": metrics,
        "market_features": {
            "features": {
                "vwap_hold_bars": 3,
                "volume_acceleration_ratio": 2.0,
                "bounce_2_present": True,
            }
        },
        "best_strategy_id": "s1",
        "best_strategy_name": "Momentum",
        "status": "WATCH",
        "score": 82,
    }


def bar(ts, close, high=None, low=None):
    return {
        "t": ts.isoformat().replace("+00:00", "Z"),
        "c": close,
        "h": high if high is not None else close,
        "l": low if low is not None else close,
    }


def test_build_shadow_observation_is_research_only_and_causal_feature_named():
    observed = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)
    item = build_shadow_observation(scan_result(), source="market_discovery", observed_at=observed)
    assert item is not None
    assert item["symbol"] == "SDOT"
    assert item["feature_cutoff"] == "2026-08-29T15:00:00Z"
    assert item["features"]["feature__vwap_hold_bars"] == 3
    assert item["features"]["feature__bounce_2_present"] is True
    assert not any(key.startswith("label__") for key in item["features"])
    assert item["research_only"] is True
    assert item["affects_live_ranking"] is False


def test_market_trade_timestamp_wins_over_late_scan_completion_time():
    completed = datetime(2026, 8, 29, 15, 9, tzinfo=timezone.utc)
    item = build_shadow_observation(
        scan_result(trade_timestamp="2026-08-29T15:02:14Z"),
        source="market_discovery",
        observed_at=completed,
    )
    assert item is not None
    assert item["observed_at"] == "2026-08-29T15:02:14Z"
    assert item["feature_cutoff"] == "2026-08-29T15:02:14Z"


def test_merge_deduplicates_same_symbol_and_five_minute_bucket():
    first = build_shadow_observation(
        scan_result(),
        source="market_discovery",
        observed_at=datetime(2026, 8, 29, 15, 1, tzinfo=timezone.utc),
    )
    second = build_shadow_observation(
        scan_result(),
        source="stock_analyzer",
        observed_at=datetime(2026, 8, 29, 15, 4, tzinfo=timezone.utc),
    )
    merged = merge_shadow_observations([first], [second])
    assert len(merged) == 1
    assert set(merged[0]["sources"]) == {"market_discovery", "stock_analyzer"}


def test_mature_shadow_observation_calculates_raw_outcomes_and_conservative_same_bar_stop():
    observed = datetime(2026, 8, 29, 14, 30, tzinfo=timezone.utc)
    record = build_shadow_observation(scan_result(price=10.0), source="market_discovery", observed_at=observed)
    rows = []
    for minute in range(1, 61):
        stamp = observed + timedelta(minutes=minute)
        close = 10.0 + minute * 0.001
        high = close + 0.01
        low = close - 0.01
        if minute == 3:
            high = 10.12
            low = 9.90
        if minute == 4:
            high = 10.20
            low = 9.90
        rows.append(bar(stamp, close, high=high, low=low))
    rows[0] = bar(observed + timedelta(minutes=1), 10.0, high=10.11, low=9.90)

    matured, summary = mature_shadow_observations(
        [record],
        {"SDOT": rows},
        now=observed + timedelta(minutes=61),
    )
    assert summary["updated"] == 1
    assert matured[0]["outcome_status"] == "COMPLETE"
    outcome = matured[0]["outcomes"]["5"]
    assert outcome["status"] == "EVALUATED"
    assert outcome["target_before_stop"] is False
    assert outcome["barrier_outcome"] == "STOP_FIRST"
    assert outcome["barrier_touch_bar"] == 1
    assert outcome["max_favorable_excursion_pct"] > 0
    assert outcome["max_adverse_excursion_pct"] < 0


def test_horizon_past_regular_close_is_not_mislabeled():
    observed = datetime(2026, 8, 29, 19, 50, tzinfo=timezone.utc)
    record = build_shadow_observation(scan_result(), source="stock_analyzer", observed_at=observed)
    rows = [bar(observed + timedelta(minutes=i), 10 + i * 0.01) for i in range(1, 11)]
    matured, _ = mature_shadow_observations(
        [record],
        {"SDOT": rows},
        now=observed + timedelta(hours=2),
    )
    assert matured[0]["outcomes"]["15"]["status"] == "SESSION_TRUNCATED"
    assert matured[0]["outcomes"]["30"]["status"] == "SESSION_TRUNCATED"
    assert matured[0]["outcomes"]["60"]["status"] == "SESSION_TRUNCATED"


def test_pending_helpers_scope_to_current_symbols():
    a = build_shadow_observation(
        scan_result("SDOT"),
        source="market_discovery",
        observed_at=datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc),
    )
    b = build_shadow_observation(
        scan_result("REAX"),
        source="market_discovery",
        observed_at=datetime(2026, 8, 29, 15, 5, tzinfo=timezone.utc),
    )
    assert pending_symbols([a, b], only_symbols=["SDOT"]) == ["SDOT"]
    assert earliest_pending_observed_at([a, b], only_symbols=["REAX"]) == datetime(
        2026, 8, 29, 15, 5, tzinfo=timezone.utc
    )


def test_shadow_observation_keeps_model_probability_for_later_calibration():
    observed = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)
    result = scan_result()
    result["ml_prediction"] = {
        "status": "SCORED",
        "probability": 0.72,
        "raw_probability": 0.69,
        "model_id": "model-123",
        "target": "label__target_before_stop_15bar",
        "target_description": "Price reaches +1% before -0.75% within 15 bars.",
        "feature_coverage": 0.84,
    }
    item = build_shadow_observation(
        result,
        source="market_discovery",
        observed_at=observed,
    )

    assert item is not None
    assert item["context"]["ml_probability"] == 0.72
    assert item["context"]["ml_raw_probability"] == 0.69
    assert item["context"]["ml_model_id"] == "model-123"
    assert item["context"]["ml_feature_coverage"] == 0.84



def test_shadow_observation_keeps_parallel_challenger_predictions():
    observed = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)
    result = scan_result()
    result["ml_prediction"] = {
        "status": "SCORED",
        "probability": 0.70,
        "raw_probability": 0.68,
        "model_id": "champion",
        "target": "label__target_before_stop_15bar",
        "target_description": "test target",
        "feature_coverage": 0.90,
    }
    result["ml_predictions"] = [
        result["ml_prediction"],
        {
            "status": "SCORED",
            "probability": 0.62,
            "raw_probability": 0.60,
            "model_id": "challenger",
            "target": "label__target_before_stop_15bar",
            "target_description": "test target",
            "feature_coverage": 0.88,
        },
    ]

    item = build_shadow_observation(
        result,
        source="market_discovery",
        observed_at=observed,
    )
    saved = item["context"]["ml_predictions"]
    assert [row["model_id"] for row in saved] == ["champion", "challenger"]
    assert saved[1]["probability"] == 0.62
    assert saved[1]["feature_coverage"] == 0.88



def test_merge_keeps_parallel_predictions_from_duplicate_bucket():
    observed = datetime(2026, 8, 29, 15, 1, tzinfo=timezone.utc)
    first_result = scan_result()
    first_result["ml_prediction"] = {
        "status": "SCORED",
        "probability": 0.70,
        "model_id": "champion",
        "target": "label__target_before_stop_15bar",
    }
    first_result["ml_predictions"] = [first_result["ml_prediction"]]
    second_result = scan_result()
    second_result["ml_prediction"] = {
        "status": "SCORED",
        "probability": 0.62,
        "model_id": "challenger",
        "target": "label__target_before_stop_15bar",
    }
    second_result["ml_predictions"] = [second_result["ml_prediction"]]

    first = build_shadow_observation(
        first_result,
        source="market_discovery",
        observed_at=observed,
    )
    second = build_shadow_observation(
        second_result,
        source="stock_analyzer",
        observed_at=observed + timedelta(minutes=2),
    )
    merged = merge_shadow_observations([first], [second])
    ids = {
        row["model_id"]
        for row in merged[0]["context"]["ml_predictions"]
    }
    assert ids == {"champion", "challenger"}
