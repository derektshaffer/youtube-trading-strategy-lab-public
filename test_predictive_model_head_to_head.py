from predictive_model_head_to_head import build_historical_model_head_to_head


def model(
    model_id,
    family,
    *,
    auc,
    brier,
    skill,
    oos_rows=1000,
    trained_rows=2000,
    sessions=20,
    fold_rows=(200, 200, 200, 200, 200),
):
    return {
        "id": model_id,
        "model_family": family,
        "model_type": family,
        "shadow_scoring_enabled": True,
        "target": "label__target_before_stop_15bar",
        "target_horizon": 15,
        "session_mode": "regular",
        "profit_target_pct": 1.0,
        "stop_loss_pct": 0.75,
        "trained_rows": trained_rows,
        "trained_sessions": sessions,
        "trained_through_session": "2026-08-28",
        "validation": {
            "oos_rows": oos_rows,
            "fold_count": len(fold_rows),
            "roc_auc": auc,
            "brier_score": brier,
            "brier_skill_vs_naive": skill,
            "folds": [
                {
                    "test_sessions": [f"2026-08-{10 + i:02d}"],
                    "test_rows": rows,
                }
                for i, rows in enumerate(fold_rows)
            ],
        },
        "generalization_gate": {
            "status": "EVALUATED",
            "oos_rows": 300,
            "roc_auc": max(0.51, auc - 0.03),
            "brier_skill_vs_naive": max(0.001, skill - 0.01),
        },
    }


def test_probability_quality_edge_names_provisional_historical_leader():
    logistic = model(
        "logistic",
        "portable_numeric_logistic_regression",
        auc=0.68,
        brier=0.205,
        skill=0.04,
    )
    boosted = model(
        "boosted",
        "gradient_boosting",
        auc=0.69,
        brier=0.198,
        skill=0.07,
    )
    result = build_historical_model_head_to_head([logistic, boosted])
    assert result["status"] == "PROVISIONAL_HISTORICAL_LEADER"
    assert result["leader_model_id"] == "boosted"
    assert result["same_oos_rows"] is True
    assert result["live_confirmation_required"] is True


def test_no_clear_leader_when_metrics_conflict_materially():
    logistic = model(
        "logistic",
        "portable_numeric_logistic_regression",
        auc=0.72,
        brier=0.200,
        skill=0.06,
    )
    boosted = model(
        "boosted",
        "gradient_boosting",
        auc=0.68,
        brier=0.195,
        skill=0.08,
    )
    result = build_historical_model_head_to_head([logistic, boosted])
    assert result["status"] == "NO_CLEAR_HISTORICAL_LEADER"
    assert result["leader_model_id"] is None


def test_auc_breaks_probability_near_tie():
    logistic = model(
        "logistic",
        "portable_numeric_logistic_regression",
        auc=0.66,
        brier=0.2000,
        skill=0.0500,
    )
    boosted = model(
        "boosted",
        "gradient_boosting",
        auc=0.69,
        brier=0.1996,
        skill=0.0508,
    )
    result = build_historical_model_head_to_head([logistic, boosted])
    assert result["leader_model_id"] == "boosted"


def test_models_with_different_oos_folds_are_not_compared():
    logistic = model(
        "logistic",
        "portable_numeric_logistic_regression",
        auc=0.68,
        brier=0.205,
        skill=0.04,
    )
    boosted = model(
        "boosted",
        "gradient_boosting",
        auc=0.72,
        brier=0.180,
        skill=0.12,
        fold_rows=(199, 200, 200, 200, 201),
    )
    result = build_historical_model_head_to_head([logistic, boosted])
    assert result["status"] == "NOT_COMPARABLE"
    assert result["leader_model_id"] is None


def test_single_model_does_not_fake_a_winner():
    result = build_historical_model_head_to_head(
        [
            model(
                "only",
                "portable_numeric_logistic_regression",
                auc=0.68,
                brier=0.205,
                skill=0.04,
            )
        ]
    )
    assert result["status"] == "INSUFFICIENT_MODELS"
    assert result["leader_model_id"] is None



def test_app_uses_historical_leader_before_live_confirmation():
    app = open("trading_intelligence_app.py", encoding="utf-8").read()
    assert "historical_shadow_head_to_head" in app
    assert "PROVISIONAL_HISTORICAL_LEADER" in app
    assert "Historical model head-to-head" in app
    assert "Historical OOS leader is being used provisionally" in app
    assert '"CHAMPION_CONFIRMED"' in app
