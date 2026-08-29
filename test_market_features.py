import pandas as pd
import pytest

from market_features import add_causal_market_feature_columns, build_market_features


def _bar(i, o, h, l, c, v=100):
    return {"t": f"2026-08-29T13:{i:02d}:00Z", "o": o, "h": h, "l": l, "c": c, "v": v}


def test_empty_rows_report_missing_data():
    snapshot = build_market_features([])
    assert snapshot["features"] == {}
    assert "ohlc_bars" in snapshot["missing_data"]


def test_volume_acceleration_is_separate_from_cumulative_volume():
    rows = []
    price = 10.0
    for i in range(10):
        rows.append(_bar(i, price, price + 0.1, price - 0.1, price + 0.02, 100))
        price += 0.02
    for i in range(10, 15):
        rows.append(_bar(i, price, price + 0.1, price - 0.1, price + 0.02, 300))
        price += 0.02

    snapshot = build_market_features(rows, volume_window=5, prior_volume_window=10)
    assert snapshot["features"]["volume_acceleration_ratio"] == 3.0
    assert snapshot["features"]["volume_accelerating"] is True


def test_confirmed_swings_do_not_repaint_once_confirmed():
    rows = [
        _bar(0, 10.0, 10.2, 9.8, 10.0),
        _bar(1, 10.0, 10.5, 9.9, 10.4),
        _bar(2, 10.4, 11.0, 10.2, 10.8),
        _bar(3, 10.8, 10.9, 10.1, 10.2),
        _bar(4, 10.2, 10.4, 9.9, 10.0),
        _bar(5, 10.0, 10.3, 9.7, 10.1),
        _bar(6, 10.1, 10.6, 10.0, 10.5),
        _bar(7, 10.5, 11.2, 10.4, 11.0),
        _bar(8, 11.0, 11.1, 10.5, 10.7),
        _bar(9, 10.7, 10.8, 10.2, 10.3),
    ]

    prefix = build_market_features(rows[:6], swing_radius=2)
    extended = build_market_features(rows, swing_radius=2)
    first_confirmed = prefix["evidence"]["market_structure"]["last_two_swing_highs"]
    all_confirmed = extended["evidence"]["market_structure"]["last_two_swing_highs"]

    assert first_confirmed
    assert any(item["index"] == first_confirmed[-1]["index"] for item in all_confirmed)


def test_vwap_reclaim_requires_a_hold_not_one_crossing_bar():
    rows = [
        _bar(0, 10.0, 10.1, 9.9, 10.0, 100),
        _bar(1, 9.9, 10.0, 9.7, 9.8, 100),
        _bar(2, 9.8, 10.4, 9.8, 10.3, 100),
    ]
    one_bar = build_market_features(rows)
    assert one_bar["features"]["vwap_reclaim_recent"] is False

    rows.append(_bar(3, 10.3, 10.5, 10.2, 10.4, 100))
    held = build_market_features(rows)
    assert held["features"]["vwap_reclaim_recent"] is True
    assert held["features"]["vwap_hold_bars"] >= 2


def test_feature_snapshot_exposes_evidence_and_missing_data_contract():
    rows = [_bar(0, 10.0, 10.1, 9.9, 10.0, 100)]
    snapshot = build_market_features(rows)
    assert set(snapshot) == {"features", "evidence", "missing_data", "provider"}
    assert snapshot["provider"] == "native"
    assert "vwap" in snapshot["evidence"]
    assert "bar_history" in snapshot["missing_data"]


def test_out_of_order_rows_are_normalized_before_sequence_features():
    rows = [
        _bar(2, 10.2, 10.4, 10.1, 10.3, 300),
        _bar(0, 10.0, 10.1, 9.9, 10.0, 100),
        _bar(1, 10.0, 10.3, 9.9, 10.2, 100),
    ]
    snapshot = build_market_features(rows)
    assert snapshot["features"]["last_price"] == 10.3


def test_three_confirmed_bounces_can_flag_deterioration():
    rows = [
        _bar(0, 10.0, 10.2, 9.9, 10.0, 100),
        _bar(1, 9.8, 10.0, 9.4, 9.6, 120),
        _bar(2, 9.6, 10.5, 9.8, 10.4, 220),
        _bar(3, 10.1, 10.1, 9.6, 9.8, 110),
        _bar(4, 9.8, 10.4, 9.9, 10.3, 180),
        _bar(5, 10.0, 10.1, 9.7, 9.9, 105),
        _bar(6, 9.9, 10.3, 9.9, 10.2, 150),
        _bar(7, 10.2, 10.2, 10.0, 10.1, 100),
    ]
    snapshot = build_market_features(rows, swing_radius=1)
    features = snapshot["features"]
    assert features["completed_bounce_count"] == 3
    assert features["bounce_2_present"] is True
    assert features["bounce_3_present"] is True
    assert features["bounce_deteriorating"] is True
    assert len(snapshot["evidence"]["bounce_sequence"]["recent_bounces"]) == 3


def test_bounce_is_not_counted_before_following_high_is_confirmed():
    rows = [
        _bar(0, 10.0, 10.1, 9.9, 10.0),
        _bar(1, 9.9, 10.0, 9.5, 9.6),
        _bar(2, 9.6, 10.4, 9.8, 10.3),
    ]
    before_confirmation = build_market_features(rows, swing_radius=1)
    assert before_confirmation["features"]["completed_bounce_count"] == 0
    rows.append(_bar(3, 10.2, 10.2, 9.9, 10.0))
    after_confirmation = build_market_features(rows, swing_radius=1)
    assert after_confirmation["features"]["completed_bounce_count"] == 1


def test_stair_step_requires_three_higher_highs_and_higher_lows():
    rows = [
        _bar(0, 10.0, 10.1, 9.9, 10.0),
        _bar(1, 9.9, 10.0, 9.5, 9.7),
        _bar(2, 9.7, 10.4, 9.8, 10.3),
        _bar(3, 10.1, 10.2, 9.7, 9.9),
        _bar(4, 9.9, 10.7, 10.0, 10.6),
        _bar(5, 10.4, 10.5, 9.9, 10.1),
        _bar(6, 10.1, 11.0, 10.2, 10.9),
        _bar(7, 10.8, 10.8, 10.4, 10.5),
    ]
    snapshot = build_market_features(rows, swing_radius=1)
    assert snapshot["features"]["stair_step_up"] is True
    assert snapshot["features"]["stair_step_down"] is False


def test_tight_base_breakout_is_labeled_consolidation_expansion():
    rows = []
    for i in range(8):
        rows.append(_bar(i, 10.0, 10.05, 9.95, 10.0 + (0.01 if i % 2 else 0.0), 100))
    rows.extend(
        [
            _bar(8, 10.02, 10.25, 10.0, 10.20, 220),
            _bar(9, 10.20, 10.35, 10.15, 10.30, 250),
        ]
    )
    snapshot = build_market_features(rows, swing_radius=1)
    assert snapshot["features"]["consolidation_then_expansion_up"] is True
    assert snapshot["features"]["expansion_volume_ratio"] > 2.0


def _feature_frame(rows):
    frame = pd.DataFrame(
        [
            {
                "timestamp": row["t"],
                "open": row["o"],
                "high": row["h"],
                "low": row["l"],
                "close": row["c"],
                "volume": row["v"],
                "session": "2026-08-29",
            }
            for row in rows
        ]
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame


def test_historical_feature_rows_match_live_prefix_features():
    rows = [
        _bar(0, 10.0, 10.1, 9.9, 10.0, 100),
        _bar(1, 9.9, 10.0, 9.5, 9.6, 120),
        _bar(2, 9.6, 10.5, 9.8, 10.4, 220),
        _bar(3, 10.1, 10.1, 9.6, 9.8, 110),
        _bar(4, 9.8, 10.4, 9.9, 10.3, 180),
        _bar(5, 10.0, 10.1, 9.7, 9.9, 105),
        _bar(6, 9.9, 10.3, 9.9, 10.2, 150),
        _bar(7, 10.2, 10.2, 10.0, 10.1, 100),
        _bar(8, 10.1, 10.15, 10.0, 10.08, 100),
        _bar(9, 10.08, 10.16, 10.02, 10.10, 100),
        _bar(10, 10.10, 10.17, 10.03, 10.11, 100),
        _bar(11, 10.11, 10.18, 10.04, 10.12, 100),
        _bar(12, 10.12, 10.19, 10.05, 10.13, 100),
        _bar(13, 10.13, 10.20, 10.06, 10.14, 100),
        _bar(14, 10.14, 10.21, 10.07, 10.15, 100),
        _bar(15, 10.15, 10.22, 10.08, 10.16, 100),
        _bar(16, 10.16, 10.60, 10.14, 10.55, 260),
        _bar(17, 10.55, 10.80, 10.45, 10.72, 280),
    ]
    enriched = add_causal_market_feature_columns(
        _feature_frame(rows),
        swing_radius=1,
        volume_window=5,
        prior_volume_window=10,
    )
    fields = (
        "vwap_hold_bars",
        "vwap_reclaim_recent",
        "vwap_rejection_recent",
        "volume_acceleration_ratio",
        "volume_accelerating",
        "last_swing_high_structure",
        "last_swing_low_structure",
        "confirmed_swing_high_count",
        "confirmed_swing_low_count",
        "uptrend_structure",
        "completed_bounce_count",
        "bounce_2_present",
        "bounce_3_present",
        "bounce_deteriorating",
        "stair_step_up",
        "consolidation_then_expansion_up",
        "base_range_atr_ratio",
        "expansion_volume_ratio",
        "breakout_above_last_swing_high",
        "failed_breakout_last_swing_high",
    )
    for i in range(len(rows)):
        snapshot = build_market_features(
            rows[: i + 1],
            swing_radius=1,
            volume_window=5,
            prior_volume_window=10,
        )["features"]
        historical = enriched.iloc[i]
        for field in fields:
            expected = snapshot.get(field)
            actual = historical.get(field)
            if expected is None or pd.isna(expected):
                assert actual is None or pd.isna(actual), (i, field, expected, actual)
            elif isinstance(expected, float):
                assert float(actual) == pytest.approx(expected, rel=1e-9, abs=1e-9), (i, field)
            else:
                assert actual == expected, (i, field, expected, actual)


def test_adding_future_bars_does_not_change_past_feature_rows():
    rows = [
        _bar(0, 10.0, 10.2, 9.8, 10.0, 100),
        _bar(1, 10.0, 10.5, 9.9, 10.4, 100),
        _bar(2, 10.4, 11.0, 10.2, 10.8, 100),
        _bar(3, 10.8, 10.9, 10.1, 10.2, 100),
        _bar(4, 10.2, 10.4, 9.9, 10.0, 100),
        _bar(5, 10.0, 10.3, 9.7, 10.1, 100),
        _bar(6, 10.1, 10.6, 10.0, 10.5, 120),
        _bar(7, 10.5, 11.2, 10.4, 11.0, 140),
        _bar(8, 11.0, 11.1, 10.5, 10.7, 160),
        _bar(9, 10.7, 10.8, 10.2, 10.3, 180),
        _bar(10, 10.3, 11.4, 10.2, 11.3, 300),
        _bar(11, 11.3, 11.6, 11.1, 11.5, 320),
    ]
    prefix_length = 10
    prefix = add_causal_market_feature_columns(_feature_frame(rows[:prefix_length]), swing_radius=2)
    full = add_causal_market_feature_columns(_feature_frame(rows), swing_radius=2)
    for field in (
        "atr_pct",
        "vwap_hold_bars",
        "volume_acceleration_ratio",
        "last_swing_high_structure",
        "last_swing_low_structure",
        "completed_bounce_count",
        "stair_step_up",
        "consolidation_then_expansion_up",
        "breakout_above_last_swing_high",
        "failed_breakout_last_swing_high",
    ):
        left = prefix.iloc[-1][field]
        right = full.iloc[prefix_length - 1][field]
        if left is None or pd.isna(left):
            assert right is None or pd.isna(right), field
        else:
            assert left == right, field
