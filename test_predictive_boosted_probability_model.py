import predictive_boosted_probability_model as boosted_model
import math

import pandas as pd

from predictive_boosted_probability_model import (
    _fit_state,
    _predict_frame,
    build_boosted_probability_model,
    score_boosted_probability_model,
)


def nonlinear_dataset():
    records = []
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    for session_index in range(16):
        session = f"2026-07-{session_index + 1:02d}"
        for symbol_index, symbol in enumerate(symbols):
            for observation in range(12):
                f1 = -1.0 if (observation + session_index) % 2 == 0 else 1.0
                f2 = -1.0 if (observation + symbol_index) % 3 == 0 else 1.0
                target = (f1 > 0) == (f2 > 0)
                records.append(
                    {
                        "symbol": symbol,
                        "session": session,
                        "timestamp": f"{session}T{14 + observation // 6:02d}:{(observation % 6) * 5:02d}:00Z",
                        "feature__f1": f1,
                        "feature__f2": f2,
                        "feature__f3": float((observation % 5) - 2),
                        "feature__f4": float((session_index % 4) - 1),
                        "feature__f5": float(symbol_index),
                        "feature__f6": float((observation + symbol_index) % 7),
                        "label__target_before_stop_15bar": bool(target),
                    }
                )
    return {
        "records": records,
        "row_count": len(records),
        "symbols_with_data": len(symbols),
        "feature_columns": [
            "feature__f1",
            "feature__f2",
            "feature__f3",
            "feature__f4",
            "feature__f5",
            "feature__f6",
        ],
        "label_columns": ["label__target_before_stop_15bar"],
        "profit_target_pct": 1.0,
        "stop_loss_pct": 0.75,
        "session_mode": "regular",
    }


def test_boosted_model_learns_nonlinear_interaction_and_passes_own_gates():
    model = build_boosted_probability_model(nonlinear_dataset())
    assert model["model_type"] == "portable_gradient_boosted_trees"
    assert model["validation"]["oos_rows"] >= 250
    assert model["validation"]["roc_auc"] > 0.80
    assert model["generalization_gate"]["oos_rows"] >= 100
    assert model["generalization_gate"]["roc_auc"] > 0.70
    assert model["shadow_scoring_enabled"] is True
    assert model["research_only"] is True
    assert model["affects_live_ranking"] is False
    assert model["affects_execution"] is False


def test_serialized_boosted_state_is_deterministic_and_json_safe_shape():
    dataset = nonlinear_dataset()
    frame = pd.DataFrame(dataset["records"])
    features = dataset["feature_columns"]
    state = _fit_state(frame, features, "label__target_before_stop_15bar")
    first = _predict_frame(state, frame.iloc[:25])
    second = _predict_frame(state, frame.iloc[:25])
    assert first == second
    assert len(state["trees"]) == state["tree_count"]
    assert state["tree_count"] >= 12
    assert all(math.isfinite(value) for value in first)
    assert all(0.0 <= value <= 1.0 for value in first)


def test_boosted_live_scorer_uses_feature_coverage_gate():
    model = build_boosted_probability_model(nonlinear_dataset())
    result = {
        "market_features": {
            "features": {
                "f1": 1.0,
                "f2": -1.0,
                "f3": 0.0,
                "f4": 0.0,
                "f5": 2.0,
                "f6": 3.0,
            }
        }
    }
    scored = score_boosted_probability_model(model, result)
    assert scored["status"] == "SCORED"
    assert 0.0 <= scored["probability"] <= 1.0
    assert scored["model_id"] == model["id"]

    missing = {
        "market_features": {
            "features": {
                "f1": 1.0,
            }
        }
    }
    blocked = score_boosted_probability_model(model, missing)
    assert blocked["status"] == "INSUFFICIENT_FEATURE_COVERAGE"



def test_future_only_feature_does_not_enter_earlier_boosted_folds():
    dataset = nonlinear_dataset()
    dataset["feature_columns"].append("feature__late_only")
    for row in dataset["records"]:
        session_day = int(str(row["session"]).rsplit("-", 1)[-1])
        row["feature__late_only"] = (
            float(session_day) if session_day >= 12 else None
        )

    model = build_boosted_probability_model(dataset)
    folds = model["validation"]["folds"]
    assert folds
    assert "feature__late_only" not in folds[0]["feature_columns"]
    assert any(
        "feature__late_only" in fold["feature_columns"]
        for fold in folds[1:]
    )


def test_boosted_platt_calibrator_keeps_validation_holdout_untouched(monkeypatch):
    monkeypatch.setattr(
        boosted_model,
        "_fit_platt_calibrator",
        lambda actual, probability: {"coefficient": 1.0, "intercept": 0.0},
    )
    model = build_boosted_probability_model(nonlinear_dataset())
    calibration = model["calibration"]
    assert calibration["enabled"] is True
    assert calibration["fit_rows"] < model["validation"]["oos_rows"]
    assert (
        calibration["fit_rows"] + calibration["selection_holdout_rows"]
        == model["validation"]["oos_rows"]
    )
