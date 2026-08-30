import math

import pandas as pd
import pytest

from ml_session_features import add_session_aware_ml_features, add_session_outcome_labels


def _row(session, minute, price, volume=1000.0):
    ts = pd.Timestamp(f"{session} 13:{30 + minute:02d}:00", tz="UTC")
    return {
        "session": session,
        "timestamp": ts,
        "open": price,
        "high": price + 0.05,
        "low": price - 0.05,
        "close": price,
        "volume": volume,
    }


def test_session_aware_features_do_not_span_overnight_gap():
    rows = []
    for minute in range(25):
        rows.append(_row("2026-08-18", minute, 100.0 + minute * 0.1, 1000 + minute))
    for minute in range(25):
        rows.append(_row("2026-08-19", minute, 150.0 + minute * 0.1, 1200 + minute))

    result = add_session_aware_ml_features(pd.DataFrame(rows))
    day2 = result[result["session"] == "2026-08-19"].reset_index(drop=True)

    assert pd.isna(day2.loc[0, "return_1"])
    assert pd.isna(day2.loc[0, "return_3"])
    assert pd.isna(day2.loc[0, "return_12"])
    assert pd.isna(day2.loc[0, "atr_14_pct"])
    assert pd.isna(day2.loc[0, "rolling_volatility_20"])
    assert pd.isna(day2.loc[0, "volume_z20"])
    assert day2.loc[0, "overnight_gap_pct"] == pytest.approx(
        (150.0 / (100.0 + 24 * 0.1) - 1.0) * 100.0
    )


def test_predictive_outcome_excludes_same_bar_target_stop_ambiguity():
    frame = pd.DataFrame(
        [
            {
                "session": "2026-08-19",
                "open": 10.0,
                "high": 10.02,
                "low": 9.98,
                "close": 10.0,
            },
            {
                "session": "2026-08-19",
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.05,
            },
        ]
    )
    result = add_session_outcome_labels(
        frame,
        stop_pct=0.75,
        reward_risk=1.0 / 0.75,
        horizon_bars=1,
    )
    assert math.isnan(float(result.loc[0, "profitable_outcome"]))
    assert math.isnan(float(result.loc[0, "outcome_return_pct"]))


def test_predictive_outcome_can_still_use_conservative_same_bar_policy():
    frame = pd.DataFrame(
        [
            {
                "session": "2026-08-19",
                "open": 10.0,
                "high": 10.02,
                "low": 9.98,
                "close": 10.0,
            },
            {
                "session": "2026-08-19",
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.05,
            },
        ]
    )
    result = add_session_outcome_labels(
        frame,
        stop_pct=0.75,
        reward_risk=1.0 / 0.75,
        horizon_bars=1,
        same_bar_policy="stop_first_conservative",
    )
    assert result.loc[0, "profitable_outcome"] == 0.0


def test_predictive_outcome_requires_full_same_session_horizon():
    frame = pd.DataFrame(
        [
            {"session": "2026-08-18", "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0},
            {"session": "2026-08-18", "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0},
            {"session": "2026-08-19", "open": 12.0, "high": 12.1, "low": 11.9, "close": 12.0},
        ]
    )
    result = add_session_outcome_labels(
        frame,
        stop_pct=0.75,
        reward_risk=2.0,
        horizon_bars=2,
        require_full_horizon=True,
    )
    assert math.isnan(float(result.loc[0, "profitable_outcome"]))
