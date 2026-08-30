import pandas as pd
import pytest

from market_features import MARKET_FEATURE_COLUMNS, add_causal_market_feature_columns, build_market_features


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



def test_breakout_quality_distinguishes_hold_from_single_break():
    rows = [
        _bar(0, 10.0, 10.1, 9.9, 10.0, 100),
        _bar(1, 10.0, 10.2, 9.8, 10.0, 100),
        _bar(2, 10.0, 10.5, 10.0, 10.4, 120),
        _bar(3, 10.3, 10.3, 9.9, 10.0, 90),
        _bar(4, 10.1, 10.7, 10.2, 10.6, 220),
        _bar(5, 10.6, 10.8, 10.5, 10.7, 240),
    ]
    snapshot = build_market_features(rows, swing_radius=1)
    features = snapshot["features"]
    assert features["breakout_state"] == "holding"
    assert features["breakout_above_last_swing_high"] is True
    assert features["failed_breakout_last_swing_high"] is False
    assert features["breakout_hold_bars"] == 2
    assert features["breakout_volume_ratio"] > 1.5


def test_breakout_quality_flags_latest_failure_after_break():
    rows = [
        _bar(0, 10.0, 10.1, 9.9, 10.0, 100),
        _bar(1, 10.0, 10.2, 9.8, 10.0, 100),
        _bar(2, 10.0, 10.5, 10.0, 10.4, 120),
        _bar(3, 10.3, 10.3, 9.9, 10.0, 90),
        _bar(4, 10.1, 10.7, 10.2, 10.6, 220),
        _bar(5, 10.6, 10.8, 10.2, 10.3, 180),
    ]
    snapshot = build_market_features(rows, swing_radius=1)
    assert snapshot["features"]["breakout_state"] == "failed"
    assert snapshot["features"]["failed_breakout_last_swing_high"] is True
    assert snapshot["features"]["breakout_hold_bars"] == 0


def test_vwap_retest_requires_reclaim_then_touch_and_hold():
    rows = [
        _bar(0, 10.0, 10.1, 9.9, 10.0, 100),
        _bar(1, 10.0, 10.0, 9.7, 9.8, 100),
        _bar(2, 9.8, 10.4, 9.8, 10.3, 120),
        _bar(3, 10.3, 10.35, 10.0, 10.2, 110),
        _bar(4, 10.2, 10.35, 10.15, 10.25, 130),
    ]
    snapshot = build_market_features(rows, swing_radius=1)
    features = snapshot["features"]
    assert features["vwap_retest_recent"] is True
    assert features["vwap_retest_held"] is True
    assert features["vwap_retest_failed"] is False
    assert snapshot["evidence"]["vwap_retest"]["retest_index"] is not None


def test_pullback_quality_uses_depth_higher_low_and_volume_contraction():
    rows = [
        _bar(0, 9.2, 9.3, 9.1, 9.2, 200),
        _bar(1, 9.2, 9.2, 9.0, 9.1, 180),
        _bar(2, 9.1, 10.0, 9.2, 9.9, 300),
        _bar(3, 9.9, 11.0, 9.8, 10.8, 350),
        _bar(4, 10.8, 10.7, 10.3, 10.4, 140),
        _bar(5, 10.4, 10.5, 10.2, 10.3, 120),
        _bar(6, 10.3, 10.8, 10.25, 10.7, 150),
    ]
    snapshot = build_market_features(rows, swing_radius=1)
    features = snapshot["features"]
    assert features["pullback_higher_low"] is True
    assert 35.0 <= features["pullback_depth_pct_of_impulse"] <= 45.0
    assert features["pullback_volume_ratio"] < 1.0
    assert features["pullback_quality"] == "strong"


def test_bounce_context_uses_structure_not_only_recovery_percentage():
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
    assert features["bounce_sequence_higher_lows"] is True
    assert features["bounce_sequence_higher_highs"] is False
    assert features["bounce_structural_weakening"] is True
    assert snapshot["evidence"]["bounce_context"]["weakness_signals"] >= 2


def _historical_feature_frame(rows):
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


def test_historical_feature_frame_matches_live_prefix_semantics():
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
    historical = add_causal_market_feature_columns(
        _historical_feature_frame(rows),
        swing_radius=1,
        volume_window=5,
        prior_volume_window=10,
    )
    for index in range(len(rows)):
        expected = build_market_features(
            rows[: index + 1],
            swing_radius=1,
            volume_window=5,
            prior_volume_window=10,
        )["features"]
        actual = historical.iloc[index]
        for field in MARKET_FEATURE_COLUMNS:
            left = expected.get(field)
            right = actual.get(field)
            if left is None or pd.isna(left):
                assert right is None or pd.isna(right), (index, field, left, right)
            elif isinstance(left, float):
                assert float(right) == pytest.approx(left, rel=1e-9, abs=1e-9), (index, field)
            else:
                assert right == left, (index, field, left, right)


def test_future_bars_do_not_repaint_historical_feature_rows():
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
    cutoff = 10
    prefix = add_causal_market_feature_columns(_historical_feature_frame(rows[:cutoff]), swing_radius=2)
    full = add_causal_market_feature_columns(_historical_feature_frame(rows), swing_radius=2)
    for field in MARKET_FEATURE_COLUMNS:
        left = prefix.iloc[-1].get(field)
        right = full.iloc[cutoff - 1].get(field)
        if left is None or pd.isna(left):
            assert right is None or pd.isna(right), field
        elif isinstance(left, float):
            assert float(right) == pytest.approx(float(left), rel=1e-9, abs=1e-9), field
        else:
            assert right == left, field
