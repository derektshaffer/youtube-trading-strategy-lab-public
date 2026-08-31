import unittest

import pandas as pd

import retrospective_teacher as teacher


def bar(i, o, h, l, c, v=1000):
    t = pd.Timestamp("2026-08-20 13:30:00", tz="UTC") + pd.Timedelta(minutes=i)
    return {
        "t": t.isoformat().replace("+00:00", "Z"),
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "v": v,
    }


class RetrospectiveTeacherTests(unittest.TestCase):
    def test_swing_label_keeps_features_causal(self):
        rows = [
            bar(0, 10, 10.2, 9.9, 10.1, 100),
            bar(1, 10.1, 10.2, 9.8, 9.9, 120),
            bar(2, 9.9, 10, 9.4, 9.5, 180),
            bar(3, 9.5, 9.7, 9, 9.1, 300),
            bar(4, 9.1, 9.8, 9.1, 9.7, 350),
            bar(5, 9.7, 10.1, 9.6, 10, 320),
            bar(6, 10, 10.3, 9.9, 10.2, 250),
        ]
        frame = teacher.bars_to_frame(rows)
        examples = teacher.label_confirmed_swings(
            frame,
            left_bars=2,
            right_bars=2,
            minimum_move_pct=2.0,
        )
        low = next(
            item
            for item in examples
            if item["label"] == "significant_swing_low"
        )
        self.assertLess(low["event_time"], low["known_at"])
        self.assertEqual(low["feature_cutoff"], low["decision_time"])
        teacher.validate_no_lookahead([low])

    def test_bad_feature_cutoff_is_rejected(self):
        bad = {
            "feature_cutoff": "2026-08-20T14:01:00Z",
            "decision_time": "2026-08-20T14:00:00Z",
            "known_at": "2026-08-20T14:05:00Z",
            "outcome_window_end": "2026-08-20T14:05:00Z",
        }
        with self.assertRaises(ValueError):
            teacher.validate_no_lookahead([bad])

    def test_run_persists_without_duplicates(self):
        rows = [
            bar(
                i,
                10 + i * 0.01,
                10.1 + i * 0.01,
                9.9 + i * 0.01,
                10.02 + i * 0.01,
                100 + i,
            )
            for i in range(50)
        ]
        run = teacher.build_retrospective_teacher_run(
            rows,
            symbol="SDOT",
            timeframe="1Min",
            swing_confirmation_bars=2,
            swing_minimum_move_pct=0.1,
            breakout_lookback_bars=10,
            breakout_outcome_bars=4,
            breakout_success_move_pct=0.2,
        )
        self.assertEqual(run["symbol"], "SDOT")
        self.assertEqual(run["version"], "retrospective-teacher-v3")
        self.assertIn("volume_profile", run["feature_layers"])
        self.assertIn("multi_anchor_avwap", run["feature_layers"])
        self.assertTrue(run["indicator_cross_validation"]["passed"])
        library = teacher.merge_retrospective_teacher_run({}, run)
        library = teacher.merge_retrospective_teacher_run(library, run)
        self.assertEqual(len(library["retrospective_learning_runs"]), 1)


if __name__ == "__main__":
    unittest.main()
