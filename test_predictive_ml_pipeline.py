from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from predictive_ml_pipeline import (
    build_cross_stock_training_dataset,
    load_training_dataset,
    save_training_dataset,
    walk_forward_logistic_baseline,
)


def _bar(day: int, minute: int, close: float, volume: float = 1000.0) -> dict:
    stamp = datetime(2026, 8, day, 13, 30, tzinfo=timezone.utc) + timedelta(minutes=minute)
    return {
        "t": stamp.isoformat().replace("+00:00", "Z"),
        "o": close - 0.02,
        "h": close + 0.05,
        "l": close - 0.05,
        "c": close,
        "v": volume,
    }


class FakeMarket:
    def __init__(self, rows_by_symbol):
        self.rows_by_symbol = rows_by_symbol
        self.calls = []

    def bars(self, symbols, **kwargs):
        self.calls.append({"symbols": list(symbols), **kwargs})
        return {symbol: list(self.rows_by_symbol.get(symbol) or []) for symbol in symbols}


def test_cross_stock_dataset_reuses_one_batched_history_request():
    market = FakeMarket(
        {
            "AAA": [_bar(24, i, 10.0 + i * 0.03) for i in range(10)],
            "BBB": [_bar(25, i, 20.0 + i * 0.02) for i in range(10)],
        }
    )
    report = build_cross_stock_training_dataset(
        market,
        ["AAA", "BBB"],
        start="2026-08-24",
        end="2026-08-26",
        horizons=(1, 2),
        swing_radius=1,
        max_pages=12,
    )
    assert len(market.calls) == 1
    assert market.calls[0]["symbols"] == ["AAA", "BBB"]
    assert report["symbols_requested"] == 2
    assert report["symbols_with_data"] == 2
    assert report["bars_analyzed"] == 20
    assert report["row_count"] == 16
    assert all(row["symbol"] in {"AAA", "BBB"} for row in report["records"])
    assert all(name.startswith("feature__") for name in report["feature_columns"])
    assert all(name.startswith("label__") for name in report["label_columns"])


def test_training_dataset_round_trip_is_atomic_and_lossless(tmp_path: Path):
    dataset = {
        "causal_replay": True,
        "feature_columns": ["feature__atr_pct"],
        "label_columns": ["label__positive_return_1bar"],
        "records": [
            {
                "symbol": "AAA",
                "session": "2026-08-24",
                "timestamp": "2026-08-24T13:30:00Z",
                "feature__atr_pct": 2.5,
                "label__positive_return_1bar": True,
            }
        ],
    }
    paths = save_training_dataset(dataset, tmp_path / "training_rows")
    data_path = Path(paths["data_path"])
    metadata_path = Path(paths["metadata_path"])
    assert data_path.exists()
    assert metadata_path.exists()
    loaded = load_training_dataset(data_path)
    assert loaded["row_count"] == 1
    assert loaded["records"][0]["feature__atr_pct"] == 2.5
    assert loaded["records"][0]["label__positive_return_1bar"] is True
    assert loaded["feature_columns"] == ["feature__atr_pct"]


def _synthetic_dataset(session_count: int = 12, rows_per_session: int = 24) -> dict:
    records = []
    for session_index in range(session_count):
        day = session_index + 1
        session = f"2026-08-{day:02d}"
        for row_index in range(rows_per_session):
            signal = 1 if row_index % 2 == 0 else 0
            # Stable cross-session relationship with a categorical companion feature.
            target = bool(signal)
            records.append(
                {
                    "symbol": "AAA" if row_index % 3 else "BBB",
                    "session": session,
                    "timestamp": f"{session}T13:{30 + (row_index % 30):02d}:00Z",
                    "feature__signal": float(signal),
                    "feature__flag": bool(signal),
                    "feature__state": "strong" if signal else "weak",
                    "label__positive_return_1bar": target,
                }
            )
    return {
        "feature_columns": ["feature__signal", "feature__flag", "feature__state"],
        "label_columns": ["label__positive_return_1bar"],
        "records": records,
    }


def test_walk_forward_baseline_is_out_of_sample_and_beats_naive_on_real_signal():
    report = walk_forward_logistic_baseline(
        _synthetic_dataset(),
        target_horizon=1,
        min_train_sessions=4,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=50,
    )
    assert report["status"] == "EVALUATED"
    assert report["fold_count"] >= 3
    assert report["oos_rows"] > 0
    assert report["roc_auc"] is not None and report["roc_auc"] > 0.95
    assert report["brier_skill_vs_naive"] is not None
    assert report["brier_skill_vs_naive"] > 0.5
    assert report["split_policy"]["embargo_sessions"] == 1


def test_later_future_labels_cannot_change_first_walk_forward_predictions():
    base = _synthetic_dataset(session_count=12)
    changed = deepcopy(base)
    for row in changed["records"]:
        if row["session"] >= "2026-08-10":
            row["label__positive_return_1bar"] = not row["label__positive_return_1bar"]

    kwargs = dict(
        target_horizon=1,
        min_train_sessions=4,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=50,
    )
    left = walk_forward_logistic_baseline(base, **kwargs)
    right = walk_forward_logistic_baseline(changed, **kwargs)
    assert left["status"] == right["status"] == "EVALUATED"

    first_test_sessions = set(left["folds"][0]["test_sessions"])
    left_first = [
        (item["symbol"], item["session"], item["timestamp"], item["probability"])
        for item in left["predictions"]
        if item["session"] in first_test_sessions
    ]
    right_first = [
        (item["symbol"], item["session"], item["timestamp"], item["probability"])
        for item in right["predictions"]
        if item["session"] in first_test_sessions
    ]
    assert left_first == pytest.approx(right_first)


def test_walk_forward_refuses_too_little_history():
    report = walk_forward_logistic_baseline(
        _synthetic_dataset(session_count=4),
        target_horizon=1,
        min_train_sessions=4,
        test_sessions_per_fold=1,
        embargo_sessions=1,
        min_train_rows=10,
    )
    assert report["status"] == "INSUFFICIENT_DATA"
