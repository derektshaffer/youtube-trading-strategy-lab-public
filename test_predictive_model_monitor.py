from datetime import datetime, timedelta, timezone

from predictive_model_monitor import build_shadow_model_monitor


def observation(
    *,
    model_id: str,
    symbol: str,
    observed_at: datetime,
    probability: float,
    actual: bool,
    horizon: int = 15,
):
    return {
        "symbol": symbol,
        "session": observed_at.date().isoformat(),
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "context": {
            "ml_model_id": model_id,
            "ml_probability": probability,
            "ml_target": f"label__target_before_stop_{horizon}bar",
            "ml_target_description": "test target",
        },
        "outcomes": {
            str(horizon): {
                "status": "EVALUATED",
                "target_before_stop": actual,
                "forward_return_pct": 1.0 if actual else -0.5,
            }
        },
    }


def test_monitor_deduplicates_repeated_same_stock_decision_bucket():
    start = datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc)
    rows = [
        observation(
            model_id="m1",
            symbol="AAA",
            observed_at=start,
            probability=0.7,
            actual=True,
        ),
        observation(
            model_id="m1",
            symbol="AAA",
            observed_at=start + timedelta(minutes=5),
            probability=0.9,
            actual=True,
        ),
        observation(
            model_id="m1",
            symbol="AAA",
            observed_at=start + timedelta(minutes=35),
            probability=0.6,
            actual=False,
        ),
    ]
    report = build_shadow_model_monitor(rows, bucket_minutes=30)
    model = report["models"][0]
    assert model["raw_shadow_observations"] == 3
    assert model["evaluated_decisions"] == 2
    assert model["status"] == "COLLECTING"


def test_monitor_calculates_brier_skill_and_calibration():
    start = datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc)
    rows = []
    for day in range(6):
        for idx, symbol in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"]):
            for bucket in range(2):
                actual = (idx + bucket + day) % 2 == 0
                probability = 0.80 if actual else 0.20
                rows.append(
                    observation(
                        model_id="m1",
                        symbol=symbol,
                        observed_at=start + timedelta(days=day, minutes=bucket * 35),
                        probability=probability,
                        actual=actual,
                    )
                )
    report = build_shadow_model_monitor(
        rows,
        model_lookup={"m1": {"validation": {"brier_score": 0.08}}},
    )
    model = report["latest_model"]
    assert model["evaluated_decisions"] == 60
    assert model["symbol_count"] == 5
    assert model["session_count"] == 6
    assert model["brier_skill_vs_naive"] > 0
    assert model["expected_calibration_error"] < 0.25
    assert model["status"] == "HEALTHY"
    assert model["affects_live_ranking"] is False
    assert model["affects_execution"] is False


def test_monitor_flags_material_drift_only_after_enough_breadth():
    start = datetime(2026, 8, 1, 14, 0, tzinfo=timezone.utc)
    rows = []
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    for day in range(10):
        for symbol in symbols:
            for bucket in range(2):
                rows.append(
                    observation(
                        model_id="m2",
                        symbol=symbol,
                        observed_at=start + timedelta(days=day, minutes=bucket * 35),
                        probability=0.90,
                        actual=False,
                    )
                )
    report = build_shadow_model_monitor(
        rows,
        model_lookup={"m2": {"validation": {"brier_score": 0.20}}},
    )
    model = report["latest_model"]
    assert model["evaluated_decisions"] == 100
    assert model["status"] == "DRIFT_ALERT"
    assert model["brier_score"] > 0.7
    assert model["expected_calibration_error"] > 0.5


def test_monitor_supports_positive_return_target():
    observed = datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc)
    row = {
        "symbol": "AAA",
        "session": "2026-08-29",
        "observed_at": observed.isoformat(),
        "context": {
            "ml_model_id": "m3",
            "ml_probability": 0.6,
            "ml_target": "label__positive_return_15bar",
        },
        "outcomes": {
            "15": {
                "status": "EVALUATED",
                "forward_return_pct": 0.25,
            }
        },
    }
    report = build_shadow_model_monitor([row])
    assert report["models"][0]["positive_rate"] == 1.0
