"""Calibration tests for the isolated scope-aware validation policy prototype."""

import unittest

from validation_calibration_audit import calibration_scenarios
from trading_validation_core import validation_strength
from validation_scope_policy import scope_aware_validation_gate


class ValidationScopePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = {item["name"]: item for item in calibration_scenarios()}

    def evaluate(self, name: str, *, locked: bool = True):
        scenario = self.scenarios[name]
        strength = validation_strength(scenario["anchor_report"], scenario["walk_forward"])
        status, reasons = scope_aware_validation_gate(
            anchor_report=scenario["anchor_report"],
            strength=strength,
            generalization=scenario["generalization"],
            walk_forward=scenario["walk_forward"],
            broad_universe=bool(scenario.get("broad_universe", True)),
            validation_scope=scenario["validation_scope"],
            scope_locked_before_validation=locked,
        )
        return status, reasons, strength

    def test_broad_policy_remains_unchanged(self):
        status, reasons, strength = self.evaluate("broad_durable_positive_control")
        self.assertEqual(status, "validated")
        self.assertEqual(reasons, [])
        self.assertTrue(strength["independently_positive"])

        for name in (
            "random_no_edge_negative_control",
            "training_overfit_negative_control",
            "sparse_lucky_positive_slice_negative_control",
            "execution_fragile_negative_control",
        ):
            with self.subTest(name=name):
                status, reasons, _ = self.evaluate(name)
                self.assertEqual(status, "research_only")
                self.assertTrue(reasons)

    def test_scope_must_be_locked_before_unseen_validation(self):
        for name in (
            "regime_scoped_positive_control",
            "stock_specific_positive_control",
        ):
            with self.subTest(name=name):
                status, reasons, strength = self.evaluate(name, locked=False)
                self.assertTrue(strength["independently_positive"])
                self.assertEqual(status, "research_only")
                self.assertTrue(any("scope-shopping" in reason.lower() for reason in reasons))

    def test_predeclared_regime_scoped_positive_control_can_validate(self):
        status, reasons, strength = self.evaluate("regime_scoped_positive_control", locked=True)
        self.assertTrue(strength["independently_positive"])
        self.assertGreaterEqual(float(strength["score"]), 70.0)
        self.assertEqual(status, "validated")
        self.assertEqual(reasons, [])

    def test_predeclared_stock_specific_positive_control_can_validate(self):
        status, reasons, strength = self.evaluate("stock_specific_positive_control", locked=True)
        self.assertTrue(strength["independently_positive"])
        self.assertGreaterEqual(float(strength["score"]), 75.0)
        self.assertEqual(status, "validated")
        self.assertEqual(reasons, [])

    def test_scoped_policy_does_not_rescue_weak_or_overfit_evidence(self):
        weak = self.scenarios["training_overfit_negative_control"]
        for scope in ("matched_regime_cohort", "stock_specific"):
            with self.subTest(scope=scope):
                strength = validation_strength(weak["anchor_report"], weak["walk_forward"])
                status, reasons = scope_aware_validation_gate(
                    anchor_report=weak["anchor_report"],
                    strength=strength,
                    generalization=weak["generalization"],
                    walk_forward=weak["walk_forward"],
                    broad_universe=True,
                    validation_scope=scope,
                    scope_locked_before_validation=True,
                )
                self.assertEqual(status, "research_only")
                self.assertTrue(reasons)
                self.assertFalse(strength["independently_positive"])


if __name__ == "__main__":
    unittest.main()
