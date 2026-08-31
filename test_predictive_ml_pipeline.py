from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from predictive_ml_pipeline import (
    archetype_transfer_walk_forward_logistic_baseline,
    build_cross_stock_training_dataset,
    leave_one_symbol_out_walk_forward_logistic_baseline,
    load_training_dataset,
    save_training_dataset,
    similarity_weighted_leave_one_symbol_out_walk_forward_logistic_baseline,
    ticker_specific_walk_forward_logistic_baseline,
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
    def __init__(self, rows_by_symbol, split_actions=None):
        self.rows_by_symbol = rows_by_symbol
        self._split_actions = list(split_actions or [])
        self.calls = []

    def bars(self, symbols, **kwargs):
        self.calls.append({"symbols": list(symbols), **kwargs})
        return {symbol: list(self.rows_by_symbol.get(symbol) or []) for symbol in symbols}

    def split_actions(self, symbols, **kwargs):
        allowed = set(symbols)
        return [
            dict(item)
            for item in self._split_actions
            if str(item.get("symbol") or "").upper() in allowed
        ]


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
    assert market.calls[0]["adjustment"] == "raw"
    assert report["market_data_integrity_contract"] == "split_safe_raw_v1"
    assert report["symbols_requested"] == 2
    assert report["symbols_with_data"] == 2
    assert report["bars_analyzed"] == 20
    assert report["row_count"] == 16
    assert all(row["symbol"] in {"AAA", "BBB"} for row in report["records"])
    assert all(name.startswith("feature__") for name in report["feature_columns"])
    assert all(name.startswith("label__") for name in report["label_columns"])


def test_cross_stock_dataset_restarts_price_context_at_split_boundary():
    rows = {
        "AAA": [
            _bar(18, i, 10.0 + i * 0.01) for i in range(5)
        ] + [
            _bar(20, i, 1.0 + i * 0.01) for i in range(6)
        ],
        "BBB": [_bar(20, i, 20.0 + i * 0.02) for i in range(6)],
    }
    market = FakeMarket(
        rows,
        split_actions=[
            {
                "symbol": "AAA",
                "ex_date": "2026-08-20",
                "action_type": "forward_split",
            }
        ],
    )
    report = build_cross_stock_training_dataset(
        market,
        ["AAA", "BBB"],
        start="2026-08-18",
        end="2026-08-21",
        horizons=(1,),
        swing_radius=1,
    )

    integrity = report["market_data_integrity_by_symbol"]["AAA"]
    assert integrity["split_detected"] is True
    assert integrity["latest_split_date"] == "2026-08-20"
    assert integrity["discarded_pre_split_rows"] == 5
    aaa = next(item for item in report["by_symbol"] if item["symbol"] == "AAA")
    assert aaa["raw_bars"] == 6


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
    assert [item[:3] for item in left_first] == [item[:3] for item in right_first]
    assert [item[3] for item in left_first] == pytest.approx([item[3] for item in right_first])


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


def test_cross_stock_dataset_limits_to_recent_actual_trading_sessions():
    market = FakeMarket(
        {
            "AAA": (
                [_bar(24, i, 10.0 + i * 0.01) for i in range(6)]
                + [_bar(25, i, 10.2 + i * 0.01) for i in range(6)]
                + [_bar(28, i, 10.4 + i * 0.01) for i in range(6)]
            )
        }
    )
    report = build_cross_stock_training_dataset(
        market,
        ["AAA"],
        start="2026-08-20",
        end="2026-08-29",
        horizons=(1,),
        swing_radius=1,
        session_limit=2,
    )
    assert report["market_sessions_requested"] == 2
    assert report["market_sessions_observed"] == 2
    assert report["market_session_dates"] == ["2026-08-25", "2026-08-28"]
    assert report["by_symbol"][0]["market_sessions"] == ["2026-08-25", "2026-08-28"]
    assert {row["session"] for row in report["records"]} == {"2026-08-25", "2026-08-28"}

def test_walk_forward_can_predict_trade_quality_target():
    dataset = deepcopy(_synthetic_dataset())
    dataset["profit_target_pct"] = 1.0
    dataset["stop_loss_pct"] = 0.75
    dataset["barrier_same_bar_policy"] = "stop_first_conservative"
    dataset["label_columns"] = list(dataset["label_columns"]) + [
        "label__target_before_stop_1bar"
    ]
    for row in dataset["records"]:
        row["label__target_before_stop_1bar"] = row["label__positive_return_1bar"]

    report = walk_forward_logistic_baseline(
        dataset,
        target_horizon=1,
        target_mode="target_before_stop",
        min_train_sessions=4,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=50,
    )
    assert report["status"] == "EVALUATED"
    assert report["target_mode"] == "target_before_stop"
    assert report["target"] == "label__target_before_stop_1bar"
    assert "+1%" in report["target_description"]
    assert report["roc_auc"] is not None and report["roc_auc"] > 0.95


def test_walk_forward_rejects_unknown_target_mode():
    with pytest.raises(ValueError):
        walk_forward_logistic_baseline(
            _synthetic_dataset(),
            target_horizon=1,
            target_mode="not-a-target",
        )

def test_cross_stock_dataset_separates_market_hours_regimes():
    rows = [
        {"t": "2026-08-24T12:00:00Z", "o": 10.0, "h": 10.1, "l": 9.9, "c": 10.0, "v": 100},
        {"t": "2026-08-24T12:01:00Z", "o": 10.0, "h": 10.1, "l": 9.9, "c": 10.0, "v": 100},
        {"t": "2026-08-24T13:30:00Z", "o": 10.0, "h": 10.1, "l": 9.9, "c": 10.0, "v": 100},
        {"t": "2026-08-24T13:31:00Z", "o": 10.0, "h": 10.1, "l": 9.9, "c": 10.0, "v": 100},
        {"t": "2026-08-24T13:32:00Z", "o": 10.0, "h": 10.1, "l": 9.9, "c": 10.0, "v": 100},
        {"t": "2026-08-24T20:00:00Z", "o": 10.0, "h": 10.1, "l": 9.9, "c": 10.0, "v": 100},
        {"t": "2026-08-24T20:01:00Z", "o": 10.0, "h": 10.1, "l": 9.9, "c": 10.0, "v": 100},
        {"t": "2026-08-24T20:02:00Z", "o": 10.0, "h": 10.1, "l": 9.9, "c": 10.0, "v": 100},
    ]
    market = FakeMarket({"AAA": rows})

    regular = build_cross_stock_training_dataset(
        market,
        ["AAA"],
        start="2026-08-24",
        end="2026-08-25",
        horizons=(1,),
        swing_radius=1,
        session_limit=1,
        session_mode="regular",
    )
    premarket = build_cross_stock_training_dataset(
        market,
        ["AAA"],
        start="2026-08-24",
        end="2026-08-25",
        horizons=(1,),
        swing_radius=1,
        session_limit=1,
        session_mode="premarket",
    )
    afterhours = build_cross_stock_training_dataset(
        market,
        ["AAA"],
        start="2026-08-24",
        end="2026-08-25",
        horizons=(1,),
        swing_radius=1,
        session_limit=1,
        session_mode="afterhours",
    )

    assert regular["session_window_et"] == "09:30-16:00"
    assert premarket["session_window_et"] == "04:00-09:30"
    assert afterhours["session_window_et"] == "16:00-20:00"
    assert regular["bars_analyzed"] == 3
    assert premarket["bars_analyzed"] == 2
    assert afterhours["bars_analyzed"] == 3
    assert regular["row_count"] == 2
    assert premarket["row_count"] == 1
    assert afterhours["row_count"] == 2


def test_leave_one_symbol_out_walk_forward_never_trains_on_held_out_symbol():
    dataset = _synthetic_dataset(session_count=12, rows_per_session=30)
    report = leave_one_symbol_out_walk_forward_logistic_baseline(
        dataset,
        target_horizon=1,
        target_mode="positive_return",
        min_train_sessions=4,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=30,
        min_test_rows=5,
    )

    assert report["status"] == "EVALUATED"
    assert report["validation_type"] == "leave_one_symbol_out_walk_forward"
    assert report["split_policy"]["held_out_symbol_never_in_training"] is True
    assert report["roc_auc"] is not None and report["roc_auc"] > 0.95
    assert {item["symbol"] for item in report["by_symbol"]} == {"AAA", "BBB"}
    for item in report["by_symbol"]:
        assert item["status"] == "EVALUATED"
        assert item["roc_auc"] is not None and item["roc_auc"] > 0.95
        for fold in item["folds"]:
            assert item["symbol"] == fold["held_out_symbol"]
            assert item["symbol"] not in fold["train_symbols"]


def test_leave_one_symbol_out_requires_multiple_symbols():
    dataset = _synthetic_dataset(session_count=8)
    dataset["records"] = [
        row for row in dataset["records"] if row["symbol"] == "AAA"
    ]
    report = leave_one_symbol_out_walk_forward_logistic_baseline(
        dataset,
        target_horizon=1,
        target_mode="positive_return",
        min_train_sessions=4,
        test_sessions_per_fold=1,
        embargo_sessions=1,
        min_train_rows=10,
        min_test_rows=2,
    )
    assert report["status"] == "INSUFFICIENT_DATA"
    assert "At least two symbols" in report["reason"]


def _synthetic_similarity_dataset(session_count: int = 12, rows_per_symbol: int = 20) -> dict:
    records = []
    symbols = [
        ("AAA", 2.0, 10.0, 5_000_000.0, 20_000.0, True),
        ("AAB", 2.2, 11.0, 6_000_000.0, 24_000.0, True),
        ("BBB", 20.0, 3.0, 80_000_000.0, 400_000.0, False),
        ("BBC", 18.0, 3.5, 70_000_000.0, 350_000.0, False),
    ]
    for session_index in range(session_count):
        session = f"2026-08-{session_index + 1:02d}"
        for symbol, price, range_pct, dollar_volume, bar_dollar_volume, follows_signal in symbols:
            for row_index in range(rows_per_symbol):
                signal = float(row_index % 2)
                target = bool(signal) if follows_signal else not bool(signal)
                records.append(
                    {
                        "symbol": symbol,
                        "session": session,
                        "timestamp": f"{session}T14:{row_index:02d}:00Z",
                        "feature__signal": signal,
                        "feature__context_typical_price": price,
                        "feature__context_typical_range_pct": range_pct,
                        "feature__context_typical_dollar_volume": dollar_volume,
                        "feature__context_typical_bar_dollar_volume": bar_dollar_volume,
                        "feature__context_archetype": "rigid_one" if follows_signal else "rigid_two",
                        "label__target_before_stop_1bar": target,
                    }
                )
    return {
        "feature_columns": [
            "feature__signal",
            "feature__context_typical_price",
            "feature__context_typical_range_pct",
            "feature__context_typical_dollar_volume",
            "feature__context_typical_bar_dollar_volume",
            "feature__context_archetype",
        ],
        "label_columns": ["label__target_before_stop_1bar"],
        "profit_target_pct": 1.0,
        "stop_loss_pct": 0.75,
        "records": records,
    }


def test_similarity_weighting_beats_pooled_baseline_when_nearby_stocks_share_behavior():
    report = similarity_weighted_leave_one_symbol_out_walk_forward_logistic_baseline(
        _synthetic_similarity_dataset(),
        target_horizon=1,
        target_mode="target_before_stop",
        similarity_bandwidth=0.5,
        minimum_similarity_weight=0.01,
        min_train_sessions=4,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=100,
        min_test_rows=10,
    )

    assert report["status"] == "EVALUATED"
    assert report["paired_oos_rows"] > 0
    assert report["similarity_roc_auc"] is not None
    assert report["baseline_roc_auc"] is not None
    assert report["similarity_roc_auc"] > 0.95
    assert report["similarity_minus_baseline_auc"] > 0.3
    assert report["split_policy"]["held_out_symbol_never_in_training"] is True
    assert report["split_policy"]["all_eligible_training_rows_retained"] is True
    assert report["split_policy"]["hard_archetype_not_used_for_training_selection"] is True
    assert report["split_policy"]["hard_archetype_removed_from_model_inputs"] is True
    assert "feature__context_archetype" not in report["similarity_columns"]
    for item in report["slices"]:
        assert item["held_out_symbol"] not in {
            peer["symbol"] for peer in item["top_similar_symbols"]
        }


def _synthetic_behavior_similarity_dataset(
    session_count: int = 12,
    rows_per_symbol: int = 20,
) -> dict:
    records = []
    symbols = [
        ("AAA", True, 0.90, 0.10, 0.80, 0.15, 0.70, 1.80),
        ("AAB", True, 0.85, 0.15, 0.75, 0.20, 0.65, 1.70),
        ("BBB", False, 0.20, 0.80, 0.15, 0.75, 0.20, 0.65),
        ("BBC", False, 0.25, 0.75, 0.20, 0.70, 0.25, 0.70),
    ]
    for session_index in range(session_count):
        session = f"2026-08-{session_index + 1:02d}"
        for (
            symbol,
            follows_signal,
            breakout_hold,
            breakout_fail,
            bounce_strength,
            bounce_weakness,
            stair_step,
            volume_acceleration,
        ) in symbols:
            for row_index in range(rows_per_symbol):
                signal = float(row_index % 2)
                target = bool(signal) if follows_signal else not bool(signal)
                records.append(
                    {
                        "symbol": symbol,
                        "session": session,
                        "timestamp": f"{session}T14:{row_index:02d}:00Z",
                        "feature__signal": signal,
                        # Deliberately identical scale/liquidity context: only
                        # the prior-session behavior fingerprint can separate families.
                        "feature__context_typical_price": 5.0,
                        "feature__context_typical_range_pct": 8.0,
                        "feature__context_typical_dollar_volume": 20_000_000.0,
                        "feature__context_typical_bar_dollar_volume": 80_000.0,
                        "feature__context_prior_breakout_hold_rate": breakout_hold,
                        "feature__context_prior_breakout_fail_rate": breakout_fail,
                        "feature__context_prior_bounce_strengthening_rate": bounce_strength,
                        "feature__context_prior_late_bounce_weakening_rate": bounce_weakness,
                        "feature__context_prior_stair_step_up_rate": stair_step,
                        "feature__context_prior_volume_acceleration_median": volume_acceleration,
                        "feature__context_archetype": "same_rigid_bucket",
                        "label__target_before_stop_1bar": target,
                    }
                )
    feature_columns = sorted(
        key for key in records[0] if key.startswith("feature__")
    )
    return {
        "feature_columns": feature_columns,
        "label_columns": ["label__target_before_stop_1bar"],
        "profit_target_pct": 1.0,
        "stop_loss_pct": 0.75,
        "records": records,
    }


def test_behavior_similarity_recovers_local_relationship_when_scale_context_is_identical():
    report = similarity_weighted_leave_one_symbol_out_walk_forward_logistic_baseline(
        _synthetic_behavior_similarity_dataset(),
        target_horizon=1,
        target_mode="target_before_stop",
        similarity_bandwidth=0.5,
        minimum_similarity_weight=0.01,
        min_train_sessions=4,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=100,
        min_test_rows=10,
    )

    assert report["status"] == "EVALUATED"
    assert report["similarity_roc_auc"] is not None
    assert report["baseline_roc_auc"] is not None
    assert report["similarity_roc_auc"] > 0.95
    assert report["similarity_minus_baseline_auc"] > 0.3
    assert report["split_policy"][
        "similarity_profile_includes_prior_session_market_behavior"
    ] is True
    assert "feature__context_prior_breakout_hold_rate" in report["similarity_columns"]
    assert "feature__context_prior_bounce_strengthening_rate" in report["similarity_columns"]


def test_similarity_weighting_requires_continuous_context():
    dataset = _synthetic_similarity_dataset()
    dataset["feature_columns"] = ["feature__signal", "feature__context_archetype"]
    report = similarity_weighted_leave_one_symbol_out_walk_forward_logistic_baseline(
        dataset,
        target_horizon=1,
        min_train_sessions=4,
        test_sessions_per_fold=1,
        embargo_sessions=1,
        min_train_rows=20,
        min_test_rows=5,
    )
    assert report["status"] == "INSUFFICIENT_DATA"
    assert "continuous lagged context" in report["reason"]


def _synthetic_archetype_dataset(session_count: int = 12, rows_per_symbol: int = 20) -> dict:
    records = []
    symbols = [("AAA", "family_one"), ("AAB", "family_one"), ("BBB", "family_two"), ("BBC", "family_two")]
    for session_index in range(session_count):
        session = f"2026-08-{session_index + 1:02d}"
        for symbol, archetype in symbols:
            for row_index in range(rows_per_symbol):
                signal = float(row_index % 2)
                target = bool(signal) if archetype == "family_one" else not bool(signal)
                records.append(
                    {
                        "symbol": symbol,
                        "session": session,
                        "timestamp": f"{session}T14:{row_index:02d}:00Z",
                        "feature__signal": signal,
                        "feature__context_typical_range_pct": 12.0 if archetype == "family_one" else 4.0,
                        "feature__context_typical_dollar_volume": 5_000_000.0 if archetype == "family_one" else 80_000_000.0,
                        "feature__context_archetype": archetype,
                        "label__target_before_stop_1bar": target,
                    }
                )
    return {
        "feature_columns": [
            "feature__signal",
            "feature__context_typical_range_pct",
            "feature__context_typical_dollar_volume",
            "feature__context_archetype",
        ],
        "context_feature_columns": [
            "feature__context_typical_range_pct",
            "feature__context_typical_dollar_volume",
            "feature__context_archetype",
        ],
        "archetype_column": "feature__context_archetype",
        "label_columns": ["label__target_before_stop_1bar"],
        "profit_target_pct": 1.0,
        "stop_loss_pct": 0.75,
        "records": records,
    }


def test_context_features_use_only_current_and_completed_prior_sessions():
    market = FakeMarket(
        {
            "AAA": (
                [_bar(24, i, 2.0 + i * 0.01, 50_000) for i in range(8)]
                + [_bar(25, i, 2.2 + i * 0.02, 60_000) for i in range(8)]
                + [_bar(26, i, 2.4 + i * 0.03, 70_000) for i in range(8)]
            )
        }
    )
    base = build_cross_stock_training_dataset(
        market,
        ["AAA"],
        start="2026-08-24",
        end="2026-08-27",
        horizons=(1,),
        swing_radius=1,
        session_mode="regular",
    )
    day26 = [row for row in base["records"] if row["session"] == "2026-08-26"]
    assert day26
    first = day26[0]
    assert first["feature__context_prior_session_count"] == 2
    assert first["feature__context_archetype"] != "unknown"
    assert first["feature__context_typical_range_pct"] is not None
    assert first["feature__context_typical_dollar_volume"] is not None
    assert first["feature__context_prior_above_vwap_rate"] is not None
    assert "feature__context_prior_breakout_hold_rate" in base["context_feature_columns"]
    assert "feature__context_pattern_personality" in first
    assert base["archetype_column"] == "feature__context_archetype"
    assert base["context_feature_columns"]

    changed_market = FakeMarket(
        {
            "AAA": market.rows_by_symbol["AAA"]
            + [_bar(27, i, 50.0 + i, 9_000_000) for i in range(8)]
        }
    )
    changed = build_cross_stock_training_dataset(
        changed_market,
        ["AAA"],
        start="2026-08-24",
        end="2026-08-28",
        horizons=(1,),
        swing_radius=1,
        session_mode="regular",
    )
    changed_day26 = [row for row in changed["records"] if row["session"] == "2026-08-26"]
    keys = [name for name in base["context_feature_columns"] if name != "feature__context_cumulative_dollar_volume"]
    for left, right in zip(day26, changed_day26):
        for key in keys:
            assert left.get(key) == right.get(key)


def test_archetype_transfer_prefers_matching_family_on_opposite_synthetic_relationships():
    report = archetype_transfer_walk_forward_logistic_baseline(
        _synthetic_archetype_dataset(),
        target_horizon=1,
        target_mode="target_before_stop",
        min_train_sessions=4,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=50,
        min_test_rows=10,
    )
    assert report["status"] == "EVALUATED"
    assert report["paired_oos_rows"] > 0
    assert report["within_roc_auc"] is not None and report["within_roc_auc"] > 0.95
    assert report["across_roc_auc"] is not None and report["across_roc_auc"] < 0.05
    assert report["within_minus_across_auc"] is not None
    assert report["within_minus_across_auc"] > 0.9
    assert report["split_policy"]["held_out_symbol_never_in_training"] is True
    assert report["split_policy"]["same_test_rows_for_within_and_across"] is True
    for item in report["slices"]:
        assert item["held_out_symbol"] not in item["within_train_symbols"]
        assert item["held_out_symbol"] not in item["across_train_symbols"]


def test_archetype_transfer_requires_populated_multiple_archetypes():
    dataset = _synthetic_archetype_dataset()
    for row in dataset["records"]:
        row["feature__context_archetype"] = "one_family"
    report = archetype_transfer_walk_forward_logistic_baseline(
        dataset,
        target_horizon=1,
        min_train_sessions=4,
        test_sessions_per_fold=1,
        embargo_sessions=1,
        min_train_rows=20,
        min_test_rows=5,
    )
    assert report["status"] == "INSUFFICIENT_DATA"
    assert "At least two populated archetypes" in report["reason"]



def test_cross_stock_dataset_emits_granular_per_stock_progress():
    market = FakeMarket(
        {
            "AAA": [_bar(24, i, 10.0 + i * 0.03) for i in range(10)],
            "BBB": [_bar(25, i, 20.0 + i * 0.02) for i in range(10)],
        }
    )
    messages = []
    build_cross_stock_training_dataset(
        market,
        ["AAA", "BBB"],
        start="2026-08-24",
        end="2026-08-26",
        horizons=(1,),
        swing_radius=1,
        progress=messages.append,
    )
    assert any("ML stock 1/2 · calculating causal features for AAA" in item for item in messages)
    assert any("ML stock 1/2 · adding causal context for AAA" in item for item in messages)
    assert any("ML stock 1/2 · finished AAA" in item for item in messages)
    assert any("ML stock 2/2 · calculating causal features for BBB" in item for item in messages)
    assert any("ML stock 2/2 · finished BBB" in item for item in messages)


def test_cross_stock_dataset_observation_stride_reduces_rows():
    market = FakeMarket(
        {
            "AAA": [_bar(24, i, 10.0 + i * 0.03) for i in range(20)],
            "BBB": [_bar(25, i, 20.0 + i * 0.02) for i in range(20)],
        }
    )
    full = build_cross_stock_training_dataset(
        market,
        ["AAA", "BBB"],
        start="2026-08-24",
        end="2026-08-26",
        horizons=(1,),
        swing_radius=1,
        observation_stride_bars=1,
    )
    sampled = build_cross_stock_training_dataset(
        market,
        ["AAA", "BBB"],
        start="2026-08-24",
        end="2026-08-26",
        horizons=(1,),
        swing_radius=1,
        observation_stride_bars=5,
    )
    assert sampled["observation_stride_bars"] == 5
    assert sampled["row_count"] < full["row_count"]
    assert sampled["row_count"] <= (full["row_count"] + 4) // 5 + 2


def _opposite_ticker_relationship_dataset(session_count: int = 12, rows_per_session: int = 20) -> dict:
    records = []
    for symbol in ("AAA", "BBB"):
        for session_index in range(session_count):
            day = session_index + 1
            session = f"2026-08-{day:02d}"
            for row_index in range(rows_per_session):
                signal = float(row_index % 2)
                target = bool(signal) if symbol == "AAA" else not bool(signal)
                records.append(
                    {
                        "symbol": symbol,
                        "session": session,
                        "timestamp": f"{session}T14:{row_index:02d}:00Z",
                        "feature__signal": signal,
                        "label__target_before_stop_1bar": target,
                    }
                )
    return {
        "feature_columns": ["feature__signal"],
        "label_columns": ["label__target_before_stop_1bar"],
        "profit_target_pct": 1.0,
        "stop_loss_pct": 0.75,
        "records": records,
    }


def test_ticker_specific_walk_forward_learns_each_stocks_own_history():
    dataset = _opposite_ticker_relationship_dataset()
    own = ticker_specific_walk_forward_logistic_baseline(
        dataset,
        target_horizon=1,
        target_mode="target_before_stop",
        min_train_sessions=4,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=50,
    )
    held_out = leave_one_symbol_out_walk_forward_logistic_baseline(
        dataset,
        target_horizon=1,
        target_mode="target_before_stop",
        min_train_sessions=4,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=50,
        min_test_rows=10,
    )
    assert own["status"] == "EVALUATED"
    assert own["split_policy"]["training_uses_same_symbol_only"] is True
    assert own["split_policy"]["future_sessions_only"] is True
    assert own["roc_auc"] is not None and own["roc_auc"] > 0.95
    assert own["macro_roc_auc"] is not None and own["macro_roc_auc"] > 0.95
    assert all(item["roc_auc"] > 0.95 for item in own["by_symbol"] if item["status"] == "EVALUATED")
    assert held_out["status"] == "EVALUATED"
    assert held_out["roc_auc"] is not None and held_out["roc_auc"] < 0.05
    assert own["roc_auc"] - held_out["roc_auc"] > 0.9


def test_ticker_specific_walk_forward_refuses_too_little_same_stock_history():
    dataset = _opposite_ticker_relationship_dataset(session_count=4, rows_per_session=10)
    report = ticker_specific_walk_forward_logistic_baseline(
        dataset,
        target_horizon=1,
        target_mode="target_before_stop",
        min_train_sessions=4,
        test_sessions_per_fold=1,
        embargo_sessions=1,
        min_train_rows=20,
    )
    assert report["status"] == "INSUFFICIENT_DATA"



def test_walk_forward_feature_schema_uses_training_slice_only():
    dataset = _synthetic_dataset(session_count=12, rows_per_session=24)
    dataset["feature_columns"].append("feature__late_only")
    for row in dataset["records"]:
        session_day = int(str(row["session"]).rsplit("-", 1)[-1])
        row["feature__late_only"] = (
            float(session_day) if session_day >= 9 else None
        )

    report = walk_forward_logistic_baseline(
        dataset,
        target_horizon=1,
        target_mode="positive_return",
        min_train_sessions=4,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=50,
    )
    assert report["status"] == "EVALUATED"
    folds = report["folds"]
    assert "feature__late_only" not in folds[0]["feature_columns"]
    assert any(
        "feature__late_only" in fold["feature_columns"]
        for fold in folds[1:]
    )


def test_ticker_specific_reports_sample_aware_auc_diagnostics():
    report = ticker_specific_walk_forward_logistic_baseline(
        _opposite_ticker_relationship_dataset(),
        target_horizon=1,
        target_mode="target_before_stop",
        min_train_sessions=4,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=50,
    )
    assert report["status"] == "EVALUATED"
    assert report["micro_roc_auc"] == pytest.approx(report["roc_auc"])
    assert report["weighted_macro_roc_auc"] is not None
    assert report["median_ticker_roc_auc"] is not None
    for item in report["by_symbol"]:
        if item["status"] == "EVALUATED":
            assert item["oos_positive_count"] + item["oos_negative_count"] == item["oos_rows"]



def test_parallel_feature_build_matches_sequential_exactly():
    rows_by_symbol = {
        "AAA": [_bar(24, i, 10.0 + i * 0.03) for i in range(20)],
        "BBB": [_bar(24, i, 20.0 + i * 0.02) for i in range(20)],
        "CCC": [_bar(24, i, 30.0 + i * 0.01) for i in range(20)],
    }
    sequential = build_cross_stock_training_dataset(
        FakeMarket(rows_by_symbol),
        ["AAA", "BBB", "CCC"],
        start="2026-08-24",
        end="2026-08-25",
        horizons=(1, 2),
        swing_radius=1,
        feature_workers=1,
    )
    parallel = build_cross_stock_training_dataset(
        FakeMarket(rows_by_symbol),
        ["AAA", "BBB", "CCC"],
        start="2026-08-24",
        end="2026-08-25",
        horizons=(1, 2),
        swing_radius=1,
        feature_workers=3,
    )

    ignored = {"feature_workers"}
    assert {
        key: value for key, value in parallel.items() if key not in ignored
    } == {
        key: value for key, value in sequential.items() if key not in ignored
    }
    assert sequential["feature_workers"] == 1
    assert parallel["feature_workers"] == 3
