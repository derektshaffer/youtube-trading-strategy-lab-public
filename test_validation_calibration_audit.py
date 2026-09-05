"""Regression tests for the Trading Lab validation calibration audit."""

import unittest

from validation_calibration_audit import run_calibration_audit


class ValidationCalibrationAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_calibration_audit()
        cls.by_name = {
            item["name"]: item
            for item in cls.report["results"]
        }

    def test_broad_durable_positive_control_passes(self):
        item = self.by_name["broad_durable_positive_control"]
        self.assertEqual(item["observed_status"], "validated")
        self.assertTrue(item["calibration_match"])
        self.assertTrue(item["independently_positive"])
        self.assertGreaterEqual(item["robustness_score"], 70.0)

    def test_no_edge_overfit_sparse_and_fragile_controls_fail_closed(self):
        for name in (
            "random_no_edge_negative_control",
            "training_overfit_negative_control",
            "sparse_lucky_positive_slice_negative_control",
            "execution_fragile_negative_control",
        ):
            with self.subTest(name=name):
                item = self.by_name[name]
                self.assertEqual(item["observed_status"], "research_only")
                self.assertTrue(item["calibration_match"])
                self.assertTrue(item["gate_reasons"])

    def test_scope_controls_expose_current_universal_breadth_gate(self):
        regime = self.by_name["regime_scoped_positive_control"]
        stock = self.by_name["stock_specific_positive_control"]

        self.assertEqual(regime["observed_status"], "research_only")
        self.assertFalse(regime["calibration_match"])
        self.assertTrue(
            any("three different stocks" in reason.lower() for reason in regime["gate_reasons"])
        )
        self.assertTrue(
            any("20 trades" in reason.lower() for reason in regime["gate_reasons"])
        )

        self.assertEqual(stock["observed_status"], "research_only")
        self.assertFalse(stock["calibration_match"])
        self.assertTrue(
            any("cross-stock generalization" in reason.lower() for reason in stock["gate_reasons"])
        )

    def test_summary_separates_gate_integrity_from_scope_design_gap(self):
        self.assertTrue(self.report["hard_controls_pass"])
        self.assertFalse(self.report["scope_controls_pass"])
        self.assertFalse(self.report["overall_calibrated"])
        self.assertTrue(
            any(
                item.get("severity") == "design_gap"
                for item in self.report["findings"]
            )
        )


if __name__ == "__main__":
    unittest.main()
