from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

import pandas as pd

from anchored_vwap_engine import (
    apply_anchored_vwap_indicators,
    apply_multi_anchor_avwap_teacher_features,
)


def frame_from(values: list[tuple[float, float, float, float, int]]) -> pd.DataFrame:
    start = datetime(2026, 8, 20, 13, 30, tzinfo=timezone.utc)
    rows = []
    for index, (open_, high, low, close, volume) in enumerate(values):
        rows.append(
            {
                "timestamp": start + timedelta(minutes=index),
                "session": "2026-08-20",
                "session_minute": index,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "prior_breakout_high": 10.5 if index >= 3 else None,
                "previous_bar_close": values[index - 1][3] if index else None,
                "previous_daily_high": 10.6,
            }
        )
    return pd.DataFrame(rows)


class AnchoredVwapEngineTests(unittest.TestCase):
    def test_session_open_matches_full_session_vwap_math(self):
        frame = frame_from(
            [
                (10.0, 10.2, 9.8, 10.1, 100),
                (10.1, 10.4, 10.0, 10.3, 200),
                (10.3, 10.5, 10.2, 10.4, 150),
            ]
        )
        result = apply_anchored_vwap_indicators(
            frame,
            {"avwap_anchor_mode": "session_open"},
        )
        typical = (frame["high"] + frame["low"] + frame["close"]) / 3.0
        expected = (typical * frame["volume"]).cumsum() / frame["volume"].cumsum()
        self.assertTrue(result["avwap"].round(8).equals(expected.round(8)))

    def test_swing_low_is_not_available_until_right_side_confirmation(self):
        frame = frame_from(
            [
                (10.0, 10.2, 9.9, 10.1, 100),
                (10.1, 10.2, 9.7, 9.8, 100),
                (9.8, 9.9, 9.4, 9.6, 100),
                (9.6, 9.9, 9.6, 9.8, 100),
                (9.8, 10.1, 9.8, 10.0, 100),
            ]
        )
        result = apply_anchored_vwap_indicators(
            frame,
            {
                "avwap_anchor_mode": "swing_low",
                "avwap_pivot_confirm_bars": 2,
            },
        )
        self.assertTrue(pd.isna(result.loc[3, "avwap"]))
        self.assertTrue(pd.notna(result.loc[4, "avwap"]))
        self.assertEqual(result.loc[4, "avwap_anchor_reason"], "confirmed_swing_low")

    def test_higher_low_handoff_waits_for_second_confirmed_higher_pivot(self):
        frame = frame_from(
            [
                (10.0, 10.1, 9.8, 10.0, 100),
                (10.0, 10.1, 9.4, 9.6, 100),
                (9.6, 9.9, 9.6, 9.8, 100),
                (9.8, 10.0, 9.7, 9.9, 100),
                (9.9, 10.0, 9.6, 9.8, 100),
                (9.8, 10.0, 9.7, 9.9, 100),
                (9.9, 10.2, 9.9, 10.1, 100),
            ]
        )
        result = apply_anchored_vwap_indicators(
            frame,
            {
                "avwap_anchor_mode": "higher_low_handoff",
                "avwap_pivot_confirm_bars": 1,
            },
        )
        active = result.index[result["avwap_anchor_active"].fillna(False)].tolist()
        self.assertTrue(active)
        self.assertGreaterEqual(active[0], 5)
        self.assertEqual(
            result.loc[active[0], "avwap_anchor_reason"],
            "confirmed_higher_low_handoff",
        )

    def test_multi_anchor_teacher_features_wait_for_causal_pivot_confirmation(self):
        frame = frame_from(
            [
                (10.0, 10.2, 9.9, 10.1, 100),
                (10.1, 10.4, 9.8, 10.0, 100),
                (10.0, 10.1, 9.4, 9.6, 120),
                (9.6, 9.9, 9.6, 9.8, 120),
                (9.8, 10.4, 9.8, 10.3, 150),
                (10.3, 10.5, 10.0, 10.1, 150),
                (10.1, 10.2, 9.9, 10.0, 150),
                (10.0, 10.1, 9.7, 9.8, 160),
                (9.8, 10.0, 9.8, 9.9, 160),
            ]
        )
        result = apply_multi_anchor_avwap_teacher_features(
            frame,
            modes=("swing_low", "swing_high"),
            confirm_bars=1,
            pinch_threshold_pct=5.0,
        )
        self.assertEqual(int(result.loc[1, "multi_avwap_active_count"]), 0)
        self.assertTrue(
            bool((result["multi_avwap_active_count"] >= 2).any()),
            "Both causal swing AVWAPs should eventually become active.",
        )
        first_two = result.index[result["multi_avwap_active_count"] >= 2][0]
        # With one right-side confirmation bar, the high at index 1 is known at
        # index 2 and the low at index 2 is known at index 3. Both anchors are
        # therefore causally available at index 3; waiting until 5 would add an
        # artificial delay that the live engine does not require.
        self.assertEqual(first_two, 3)
        self.assertTrue(pd.notna(result.loc[first_two, "multi_avwap_spread_pct"]))

    def test_breakout_anchor_activates_only_after_observed_break(self):
        frame = frame_from(
            [
                (10.0, 10.2, 9.9, 10.1, 100),
                (10.1, 10.3, 10.0, 10.2, 100),
                (10.2, 10.4, 10.1, 10.3, 100),
                (10.3, 10.7, 10.3, 10.6, 200),
                (10.6, 10.9, 10.5, 10.8, 200),
            ]
        )
        result = apply_anchored_vwap_indicators(
            frame,
            {"avwap_anchor_mode": "breakout_bar"},
        )
        self.assertTrue(result.loc[:2, "avwap"].isna().all())
        self.assertTrue(pd.notna(result.loc[3, "avwap"]))
        self.assertEqual(result.loc[3, "avwap_anchor_reason"], "breakout_bar")


if __name__ == "__main__":
    unittest.main()
