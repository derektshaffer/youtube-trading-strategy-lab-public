import pandas as pd

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


def test_causal_feature_columns_match_snapshot_volume_and_vwap_state():
    rows = []
    price = 10.0
    for i in range(10):
        rows.append(_bar(i, price, price + 0.1, price - 0.1, price + 0.02, 100))
        price += 0.02
    for i in range(10, 15):
        rows.append(_bar(i, price, price + 0.1, price - 0.1, price + 0.02, 300))
        price += 0.02

    enriched = add_causal_market_feature_columns(
        _feature_frame(rows),
        volume_window=5,
        prior_volume_window=10,
    )
    snapshot = build_market_features(rows, volume_window=5, prior_volume_window=10)

    assert float(enriched.iloc[-1]["volume_acceleration_ratio"]) == 3.0
    assert bool(enriched.iloc[-1]["volume_accelerating"]) is True
    assert int(enriched.iloc[-1]["vwap_hold_bars"]) == snapshot["features"]["vwap_hold_bars"]
    assert bool(enriched.iloc[-1]["vwap_reclaim_recent"]) == snapshot["features"]["vwap_reclaim_recent"]


def test_historical_feature_rows_do_not_change_when_future_bars_are_added():
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

    fields = [
        "atr_pct",
        "vwap_hold_bars",
        "vwap_reclaim_recent",
        "vwap_rejection_recent",
        "volume_acceleration_ratio",
        "last_swing_high_structure",
        "last_swing_low_structure",
        "uptrend_structure",
        "breakout_above_last_swing_high",
        "failed_breakout_last_swing_high",
    ]
    prefix_row = prefix.iloc[-1]
    same_time_row = full.iloc[prefix_length - 1]
    for field in fields:
        left = prefix_row[field]
        right = same_time_row[field]
        if pd.isna(left) and pd.isna(right):
            continue
        assert left == right, field
