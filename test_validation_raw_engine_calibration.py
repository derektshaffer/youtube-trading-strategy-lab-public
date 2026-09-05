"""End-to-end raw-bar controls for validation calibration."""

import unittest

from validation_raw_engine_calibration import run_raw_engine_calibration


class RawEngineCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_raw_engine_calibration()

    def test_planted_raw_edge_survives_execution_and_validation_strength(self):
        self.assertTrue(self.report["favorable_control_pass"], self.report)
        metrics = self.report["favorable_metrics"]
        self.assertGreaterEqual(int(metrics.get("trade_count") or 0), 30)
        self.assertGreater(float(metrics.get("net_pnl") or 0.0), 0.0)
        self.assertTrue(self.report["validation_strength"]["independently_positive"])
        self.assertGreaterEqual(float(self.report["validation_strength"]["score"]), 70.0)

    def test_same_long_rules_reject_adverse_raw_path(self):
        self.assertTrue(self.report["adverse_control_rejected"], self.report)

    def test_planted_edge_is_positive_in_every_temporal_block(self):
        blocks = self.report["temporal_metrics"]
        self.assertEqual(len(blocks), 3)
        for metrics in blocks:
            self.assertGreaterEqual(int(metrics.get("trade_count") or 0), 15)
            self.assertGreater(float(metrics.get("net_pnl") or 0.0), 0.0)

    def test_planted_edge_survives_realistic_cost_stress_fixture(self):
        self.assertGreater(float(self.report["stress_metrics"].get("net_pnl") or 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
