from predictive_ml_comparison import compare_predictive_ml_runs, compact_comparison_lines


def _run(run_id, suite, route, status, same_brier, same_auc, rows=100):
    return {
        "id": run_id,
        "model_suite_version": suite,
        "stock_learning_router": {
            "by_symbol": [
                {
                    "symbol": "ABC",
                    "recommended_route": route,
                    "route_status": status,
                    "paired_oos_rows": rows,
                    "routes": {
                        "same_ticker_history": {
                            "brier_score": same_brier,
                            "roc_auc": same_auc,
                        },
                        "similarity_weighted_transfer": {
                            "brier_score": 0.20,
                            "roc_auc": 0.60,
                        },
                        "broad_cross_stock_transfer": {
                            "brier_score": 0.19,
                            "roc_auc": 0.61,
                        },
                    },
                    "reason": "example",
                }
            ]
        },
    }


def test_compare_predictive_ml_runs_reports_route_and_metric_changes():
    previous = _run(
        "v5",
        5,
        "same_ticker_history",
        "PROVISIONAL_ROUTE_LEADER",
        0.25,
        0.55,
        rows=100,
    )
    current = _run(
        "v6",
        6,
        "broad_cross_stock_transfer",
        "PROVISIONAL_ROUTE_LEADER",
        0.20,
        0.65,
        rows=125,
    )
    report = compare_predictive_ml_runs(previous, current)
    row = report["by_symbol"][0]

    assert report["previous_suite_version"] == 5
    assert report["current_suite_version"] == 6
    assert report["route_changes"] == ["ABC"]
    assert row["paired_oos_rows_delta"] == 25
    assert row["routes"]["same_ticker_history"]["brier_delta"] == -0.05
    assert row["routes"]["same_ticker_history"]["auc_delta"] == 0.10


def test_compact_comparison_lines_are_log_safe_and_informative():
    previous = _run("v5", 5, None, "NO_CLEAR_ROUTE", 0.25, 0.55)
    current = _run(
        "v6",
        6,
        "similarity_weighted_transfer",
        "PROVISIONAL_ROUTE_LEADER",
        0.23,
        0.58,
    )
    report = compare_predictive_ml_runs(previous, current)
    lines = compact_comparison_lines(report)

    assert lines[0].startswith("[predictive-ml-compare]")
    assert "route_changes=ABC" in lines[0]
    assert any(
        line.startswith("[predictive-ml-compare-symbol] symbol=ABC")
        for line in lines
    )
