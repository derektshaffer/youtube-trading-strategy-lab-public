import unittest

from trading_progress_ui import AutonomousResearchProgressEstimator, format_elapsed


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

    def test_elapsed_format(self):
        self.assertEqual(format_elapsed(9), "9s")
        self.assertEqual(format_elapsed(75), "1m 15s")
        self.assertEqual(format_elapsed(3670), "1h 01m")


if __name__ == "__main__":
    unittest.main()
