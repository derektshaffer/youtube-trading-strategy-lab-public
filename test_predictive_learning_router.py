import predictive_learning_router as router


def _paired_predictions(symbol, count, ticker_good=True):
    ticker_rows = []
    similarity_rows = []
    for index in range(count):
        actual = index % 2
        session = f"2026-08-{1 + (index // 20):02d}"
        timestamp = f"{session}T14:{index % 60:02d}:00Z"
        if ticker_good:
            ticker_probability = 0.82 if actual else 0.18
            similarity_probability = 0.66 if actual else 0.34
        else:
            ticker_probability = 0.60 if actual else 0.40
            similarity_probability = 0.84 if actual else 0.16
        baseline_probability = 0.55 if actual else 0.45
        ticker_rows.append(
            {
                "model_symbol": symbol,
                "symbol": symbol,
                "session": session,
                "timestamp": timestamp,
                "actual": bool(actual),
                "probability": ticker_probability,
            }
        )
        similarity_rows.append(
            {
                "held_out_symbol": symbol,
                "session": session,
                "timestamp": timestamp,
                "actual": actual,
                "similarity_probability": similarity_probability,
                "baseline_probability": baseline_probability,
            }
        )
    return ticker_rows, similarity_rows


def test_router_selects_same_ticker_when_it_has_clear_paired_edge():
    ticker_rows, similarity_rows = _paired_predictions("AAA", 60, ticker_good=True)
    report = router.build_stock_learning_router(
        {"predictions": ticker_rows},
        {"predictions": similarity_rows},
    )

    assert report["status"] == "EVALUATED"
    assert report["symbols_compared"] == 1
    assert report["symbols_with_clear_route"] == 1
    row = report["by_symbol"][0]
    assert row["symbol"] == "AAA"
    assert row["recommended_route"] == "same_ticker_history"
    assert row["paired_oos_rows"] == 60
    assert (
        row["routes"]["same_ticker_history"]["brier_score"]
        < row["routes"]["similarity_weighted_transfer"]["brier_score"]
    )
    assert report["affects_live_ranking"] is False
    assert report["affects_execution"] is False


def test_router_selects_similarity_transfer_when_it_has_clear_paired_edge():
    ticker_rows, similarity_rows = _paired_predictions("BBB", 60, ticker_good=False)
    report = router.build_stock_learning_router(
        {"predictions": ticker_rows},
        {"predictions": similarity_rows},
    )

    row = report["by_symbol"][0]
    assert row["recommended_route"] == "similarity_weighted_transfer"
    assert report["route_counts"]["similarity_weighted_transfer"] == 1


def test_router_requires_exact_same_unseen_rows_and_minimum_breadth():
    ticker_rows, similarity_rows = _paired_predictions("AAA", 30, ticker_good=True)
    similarity_rows[-1]["timestamp"] = "mismatched"
    report = router.build_stock_learning_router(
        {"predictions": ticker_rows},
        {"predictions": similarity_rows},
        minimum_paired_rows=30,
    )

    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["by_symbol"][0]["paired_oos_rows"] == 29


def test_router_handles_missing_prediction_inputs_safely():
    report = router.build_stock_learning_router({}, {})
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["research_only"] is True
