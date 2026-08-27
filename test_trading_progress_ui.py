import unittest

from trading_progress_ui import (
    AutonomousResearchEtaEstimator,
    AutonomousResearchProgressEstimator,
    AutonomousResearchTimingRecorder,
    LongTaskMonitor,
    autonomous_timing_profiles,
    format_eta_range,
    save_session_task_profile,
    session_task_profiles,
)


class AutonomousResearchProgressEstimatorTests(unittest.TestCase):
    def test_daily_batches_advance_progress_monotonically(self):
        tracker = AutonomousResearchProgressEstimator()
        values = [
            tracker.update("Loading current movers and most-active stocks for priority coverage…"),
            tracker.update("Loading Alpaca active + inactive U.S. equity master catalog…"),
            tracker.update("Screening 500 active + inactive stocks across about 5 years of daily history…"),
            tracker.update("Historical 1Day batch 1 of 5…"),
            tracker.update("Historical 1Day batch 5 of 5…"),
        ]
        self.assertEqual(values, sorted(values))
        self.assertGreaterEqual(values[-1], 0.35)

    def test_intraday_and_catalyst_windows_advance_estimate(self):
        tracker = AutonomousResearchProgressEstimator()
        tracker.update("Historical 1Day batch 5 of 5…")
        intraday = tracker.update("Point-in-time intraday window 5/10: ABC 2024-01-01 → 2024-05-01…")
        catalysts = tracker.update("Historical catalyst window 10/10: XYZ…")
        self.assertGreater(intraday, 0.40)
        self.assertGreater(catalysts, intraday)

    def test_deep_research_substages_advance_within_each_finalist(self):
        tracker = AutonomousResearchProgressEstimator()
        start = tracker.update("Deep research 1/3: optimizing Strategy A on ABC…")
        walk = tracker.update("Running rolling walk-forward checks for Strategy A…")
        frozen = tracker.update("Testing frozen Strategy A rules across 6 stocks…")
        second = tracker.update("Deep research 2/3: optimizing Strategy B on XYZ…")
        self.assertLess(start, walk)
        self.assertLess(walk, frozen)
        self.assertLess(frozen, second)
        self.assertLess(second, 0.99)

    def test_unknown_messages_never_move_progress_backward(self):
        tracker = AutonomousResearchProgressEstimator()
        tracker.update("Historical 1Day batch 4 of 5…")
        before = tracker.fraction
        after = tracker.update("Skipping unusable historical symbol TEST; continuing…")
        self.assertEqual(after, before)

    def test_timing_recorder_builds_compact_completed_profile(self):
        recorder = AutonomousResearchTimingRecorder()
        recorder.record(0.10, 12.0, "daily")
        recorder.record(0.10, 13.0, "duplicate fraction")
        recorder.record(0.50, 55.0, "intraday")
        profile = recorder.finish(
            120.0,
            deep_strategies_attempted=3,
            universe_sample_size=500,
        )
        self.assertEqual(profile["total_seconds"], 120.0)
        self.assertEqual(profile["deep_strategies_attempted"], 3)
        self.assertEqual(profile["universe_sample_size"], 500)
        self.assertEqual(profile["samples"][-1]["fraction"], 1.0)
        self.assertLessEqual(len(profile["samples"]), 3)

    def test_no_completed_timing_history_returns_no_eta(self):
        estimator = AutonomousResearchEtaEstimator.from_research_runs([])
        self.assertIsNone(estimator.estimate_range(0.35, current_elapsed_seconds=30))

    def test_one_completed_run_produces_wide_eta_range(self):
        runs = [
            {
                "kind": "autonomous_research",
                "timing_profile": {
                    "total_seconds": 600.0,
                    "samples": [
                        {"fraction": 0.10, "elapsed_seconds": 60.0},
                        {"fraction": 0.50, "elapsed_seconds": 240.0},
                        {"fraction": 1.0, "elapsed_seconds": 600.0},
                    ],
                },
            }
        ]
        estimator = AutonomousResearchEtaEstimator.from_research_runs(runs)
        eta = estimator.estimate_range(0.50, current_elapsed_seconds=240.0)
        self.assertIsNotNone(eta)
        low, high = eta
        self.assertLess(low, 360.0)
        self.assertGreater(high, 360.0)
        self.assertGreater(high - low, 100.0)

    def test_multiple_completed_runs_tighten_eta_around_history(self):
        runs = []
        for total, half in ((540, 220), (600, 250), (660, 280), (630, 260)):
            runs.append(
                {
                    "kind": "autonomous_research",
                    "timing_profile": {
                        "total_seconds": float(total),
                        "samples": [
                            {"fraction": 0.10, "elapsed_seconds": 60.0},
                            {"fraction": 0.50, "elapsed_seconds": float(half)},
                            {"fraction": 1.0, "elapsed_seconds": float(total)},
                        ],
                    },
                }
            )
        estimator = AutonomousResearchEtaEstimator.from_research_runs(runs)
        eta = estimator.estimate_range(0.50, current_elapsed_seconds=250.0)
        self.assertIsNotNone(eta)
        low, high = eta
        self.assertGreater(low, 200.0)
        self.assertLess(high, 500.0)
        self.assertLess(high - low, 250.0)

    def test_very_early_progress_waits_before_showing_eta(self):
        runs = [
            {
                "kind": "autonomous_research",
                "timing_profile": {
                    "total_seconds": 600.0,
                    "samples": [
                        {"fraction": 0.10, "elapsed_seconds": 60.0},
                        {"fraction": 1.0, "elapsed_seconds": 600.0},
                    ],
                },
            }
        ]
        estimator = AutonomousResearchEtaEstimator.from_research_runs(runs)
        self.assertIsNone(estimator.estimate_range(0.05, current_elapsed_seconds=10.0))

    def test_eta_range_format_is_coarse_not_fake_precision(self):
        self.assertIsNone(format_eta_range(None))
        self.assertEqual(format_eta_range((20, 45)), "less than 1 min")
        self.assertEqual(format_eta_range((95, 245)), "about 1 min–5 min")

    def test_session_task_profiles_are_isolated_by_action(self):
        state = {}
        profile_a = {
            "total_seconds": 120.0,
            "samples": [
                {"fraction": 0.1, "elapsed_seconds": 10.0},
                {"fraction": 1.0, "elapsed_seconds": 120.0},
            ],
        }
        profile_b = {
            "total_seconds": 30.0,
            "samples": [
                {"fraction": 0.1, "elapsed_seconds": 3.0},
                {"fraction": 1.0, "elapsed_seconds": 30.0},
            ],
        }
        save_session_task_profile(state, "book", profile_a)
        save_session_task_profile(state, "scan", profile_b)
        self.assertEqual(session_task_profiles(state, "book")[0]["total_seconds"], 120.0)
        self.assertEqual(session_task_profiles(state, "scan")[0]["total_seconds"], 30.0)

    def test_generic_long_task_monitor_uses_learned_eta(self):
        profile = {
            "total_seconds": 600.0,
            "samples": [
                {"fraction": 0.1, "elapsed_seconds": 60.0},
                {"fraction": 0.5, "elapsed_seconds": 240.0},
                {"fraction": 1.0, "elapsed_seconds": 600.0},
            ],
        }
        monitor = LongTaskMonitor("generic", [profile])
        text = monitor.text(0.5, "Halfway")
        self.assertIn("Estimated progress: 50%", text)
        self.assertIn("Estimated time remaining:", text)
        self.assertIn("Halfway", text)

    def test_generic_long_task_monitor_learns_after_finish(self):
        state = {}
        monitor = LongTaskMonitor("generic_finish", [])
        monitor.text(0.2, "Starting")
        profile = monitor.finish(state)
        self.assertGreater(profile["total_seconds"], 0)
        saved = session_task_profiles(state, "generic_finish")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["samples"][-1]["fraction"], 1.0)

    def test_only_autonomous_runs_with_valid_profiles_are_used(self):
        runs = [
            {"kind": "manual", "timing_profile": {"total_seconds": 10, "samples": [{}, {}]}},
            {"kind": "autonomous_research"},
            {
                "kind": "autonomous_research",
                "timing_profile": {
                    "total_seconds": 100,
                    "samples": [
                        {"fraction": 0.1, "elapsed_seconds": 10},
                        {"fraction": 1.0, "elapsed_seconds": 100},
                    ],
                },
            },
        ]
        self.assertEqual(len(autonomous_timing_profiles(runs)), 1)


if __name__ == "__main__":
    unittest.main()
