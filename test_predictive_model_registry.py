from predictive_model_registry import (
    build_model_registry,
    ready_shadow_models,
)


def model(model_id, *, target="label__target_before_stop_15bar", created="2026-08-29T12:00:00Z"):
    return {
        "id": model_id,
        "shadow_scoring_enabled": True,
        "target": target,
        "session_mode": "regular",
        "profit_target_pct": 1.0,
        "stop_loss_pct": 0.75,
        "created_at": created,
    }


def live(
    model_id,
    *,
    status="HEALTHY",
    decisions=80,
    stocks=6,
    sessions=8,
    skill=0.08,
    ece=0.06,
    brier=0.20,
):
    return {
        "model_id": model_id,
        "status": status,
        "evaluated_decisions": decisions,
        "symbol_count": stocks,
        "session_count": sessions,
        "brier_skill_vs_naive": skill,
        "expected_calibration_error": ece,
        "brier_score": brier,
    }


def test_ready_shadow_models_are_newest_unique_and_bounded():
    runs = [
        {"completed_at": "2026-08-29T13:00:00Z", "probability_model": model("m2")},
        {"completed_at": "2026-08-29T12:00:00Z", "probability_model": model("m1")},
        {"completed_at": "2026-08-29T11:00:00Z", "probability_model": model("m2")},
        {
            "completed_at": "2026-08-29T10:00:00Z",
            "probability_model": {**model("blocked"), "shadow_scoring_enabled": False},
        },
    ]
    chosen = ready_shadow_models(runs, maximum=2)
    assert [item["id"] for item in chosen] == ["m2", "m1"]


def test_initial_champion_is_provisional_newest_ready_model():
    models = [model("new"), model("old")]
    registry = build_model_registry(models, {"models": []})
    assert registry["champion_model_id"] == "new"
    assert registry["status"] == "CHAMPION_PROVISIONAL"
    assert registry["research_only"] is True
    assert registry["affects_live_ranking"] is False


def test_challenger_cannot_replace_incumbent_without_breadth():
    models = [model("new"), model("old")]
    monitor = {
        "models": [
            live("old", skill=0.03, ece=0.08, brier=0.24),
            live("new", decisions=20, skill=0.20, ece=0.02, brier=0.10),
        ]
    }
    registry = build_model_registry(
        models,
        monitor,
        previous={"champion_model_id": "old"},
    )
    assert registry["champion_model_id"] == "old"
    assert registry["promoted_from_model_id"] is None


def test_healthy_materially_better_challenger_is_promoted():
    models = [model("new"), model("old")]
    monitor = {
        "models": [
            live("old", skill=0.04, ece=0.08, brier=0.24),
            live("new", skill=0.09, ece=0.07, brier=0.19),
        ]
    }
    registry = build_model_registry(
        models,
        monitor,
        previous={"champion_model_id": "old"},
    )
    assert registry["champion_model_id"] == "new"
    assert registry["promoted_from_model_id"] == "old"
    assert registry["status"] == "CHAMPION_CONFIRMED"


def test_drift_alert_incumbent_can_be_replaced_by_healthy_positive_skill_challenger():
    models = [model("new"), model("old")]
    monitor = {
        "models": [
            live("old", status="DRIFT_ALERT", decisions=120, skill=-0.10, ece=0.20, brier=0.40),
            live("new", status="HEALTHY", decisions=80, skill=0.03, ece=0.08, brier=0.23),
        ]
    }
    registry = build_model_registry(
        models,
        monitor,
        previous={"champion_model_id": "old"},
    )
    assert registry["champion_model_id"] == "new"
    assert "drift-alert" in registry["decision_reason"]


def test_incompatible_targets_are_not_compared_for_promotion():
    models = [
        model("new", target="label__positive_return_15bar"),
        model("old", target="label__target_before_stop_15bar"),
    ]
    monitor = {
        "models": [
            live("old", skill=0.01, ece=0.10, brier=0.25),
            live("new", skill=0.30, ece=0.01, brier=0.08),
        ]
    }
    registry = build_model_registry(
        models,
        monitor,
        previous={"champion_model_id": "old"},
    )
    assert registry["champion_model_id"] == "old"
    assert "new" in registry["incompatible_ready_model_ids"]



def test_ready_shadow_models_collects_multiple_model_families_from_one_run():
    logistic = {**model("logistic"), "model_type": "portable_numeric_logistic_regression"}
    boosted = {**model("boosted"), "model_type": "portable_gradient_boosted_trees"}
    runs = [
        {
            "completed_at": "2026-08-29T14:00:00Z",
            "probability_model": logistic,
            "probability_models": [logistic, boosted],
        }
    ]
    chosen = ready_shadow_models(runs, maximum=6)
    assert {item["id"] for item in chosen} == {"logistic", "boosted"}
