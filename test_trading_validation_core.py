"""Focused tests for Trading Intelligence validation scoring."""

import unittest

from trading_validation_core import _walk_forward_session_splits, validation_strength


class WalkForwardSplitTests(unittest.TestCase):
    def test_session_embargo_is_never_used_for_training_or_external_test(self):
        sessions = [f"2026-08-{day:02d}" for day in range(1, 13)]
        splits = _walk_forward_session_splits(
            sessions,
            minimum_history_sessions=5,
            test_sessions_per_fold=2,
            embargo_sessions=1,
        )
        self.assertGreaterEqual(len(splits), 2)

        first = splits[0]
        self.assertEqual(first["history_sessions"], sessions[:5])
        self.assertEqual(first["embargo_sessions"], [sessions[5]])
        self.assertEqual(first["external_test_sessions"], sessions[6:8])
        self.assertTrue(
            set(first["history_sessions"]).isdisjoint(first["embargo_sessions"])
        )
        self.assertTrue(
            set(first["embargo_sessions"]).isdisjoint(first["external_test_sessions"])
        )

        second = splits[1]
        self.assertEqual(second["embargo_sessions"], [sessions[7]])
        self.assertEqual(second["external_test_sessions"], sessions[8:10])
        self.assertLess(
            sessions.index(second["history_sessions"][-1]),
            sessions.index(second["embargo_sessions"][0]),
        )
        self.assertLess(
            sessions.index(second["embargo_sessions"][-1]),
            sessions.index(second["external_test_sessions"][0]),
        )


class ValidationStrengthTests(unittest.TestCase):
    def _report(self, positive: bool):
        pnl = 250.0 if positive else -100.0
        metrics = {
            "trade_count": 6,
            "win_rate_pct": 60.0 if positive else 35.0,
            "net_pnl": pnl,
            "return_pct": pnl / 100.0,
            "profit_factor": 1.8 if positive else 0.7,
            "max_drawdown_pct": 4.0 if positive else 18.0,
        }
        return {
            "winner": {
                "status": "VALIDATED" if positive else "NO VALIDATED EDGE",
                "training_metrics": dict(metrics),
                "validation_metrics": dict(metrics),
                "holdout_metrics": dict(metrics),
                "stress_metrics": dict(metrics),
            },
            "optimization_settings": {
                "minimum_validation_trades": 2,
                "maximum_drawdown_pct": 15.0,
            },
        }

    def test_positive_unseen_periods_score_above_negative_periods(self):
        strong = validation_strength(self._report(True))
        weak = validation_strength(self._report(False))
        self.assertGreater(strong["score"], weak["score"])
        self.assertTrue(strong["independently_positive"])
        self.assertFalse(weak["independently_positive"])

    def test_unstable_anchor_cannot_receive_strong_robustness_from_walk_forward(self):
        report = {
            "winner": {
                "status": "UNSTABLE",
                "training_metrics": {
                    "trade_count": 164,
                    "net_pnl": -300.06,
                    "profit_factor": 0.75,
                    "max_drawdown_pct": 3.04,
                },
                "validation_metrics": {
                    "trade_count": 55,
                    "net_pnl": 30.98,
                    "profit_factor": 1.093,
                    "max_drawdown_pct": 1.54,
                },
                "holdout_metrics": {
                    "trade_count": 90,
                    "net_pnl": 102.64,
                    "profit_factor": 1.193,
                    "max_drawdown_pct": 1.45,
                },
                "stress_metrics": {
                    "trade_count": 55,
                    "net_pnl": 10.4,
                    "profit_factor": 1.03,
                    "max_drawdown_pct": 1.65,
                },
            },
            "optimization_settings": {
                "minimum_validation_trades": 2,
                "maximum_drawdown_pct": 15.0,
            },
        }
        result = validation_strength(
            report,
            {"summary": {"score": 99.3, "profitable_fold_pct": 100.0}},
        )
        self.assertGreater(result["raw_score_before_caps"], 80.0)
        self.assertLessEqual(result["score"], 49.0)
        self.assertEqual(result["label"], "WEAK")
        self.assertFalse(result["independently_positive"])
        self.assertEqual(result["optimizer_status"], "UNSTABLE")
        self.assertTrue(any("UNSTABLE" in reason for reason in result["reasons"]))

    def test_cost_sensitive_anchor_cannot_receive_strong_rating(self):
        report = self._report(True)
        report["winner"]["status"] = "COST SENSITIVE"
        report["winner"]["stress_metrics"]["net_pnl"] = -5.0
        result = validation_strength(
            report,
            {"summary": {"score": 95.0, "profitable_fold_pct": 100.0}},
        )
        self.assertLessEqual(result["score"], 49.0)
        self.assertFalse(result["independently_positive"])

    def test_full_curve_prevents_one_stress_point_from_becoming_magic_cutoff(self):
        report = self._report(True)
        report["winner"]["stress_metrics"]["net_pnl"] = -5.0
        report["winner"]["stress_metrics"]["profit_factor"] = 0.8
        report["winner"]["execution_sensitivity"] = {
            "score": 82.0,
            "label": "ROBUST",
            "passes_validation_gate": True,
            "profitable_multiplier_pct": 75.0,
            "median_pnl_retention_pct": 62.0,
        }
        result = validation_strength(report)
        self.assertEqual(result["execution_sensitivity_label"], "ROBUST")
        self.assertTrue(result["independently_positive"])
        self.assertGreater(result["score"], 49.0)

    def test_fragile_full_curve_caps_robustness_even_if_legacy_stress_is_positive(self):
        report = self._report(True)
        report["winner"]["execution_sensitivity"] = {
            "score": 34.0,
            "label": "FRAGILE",
            "passes_validation_gate": False,
            "profitable_multiplier_pct": 50.0,
            "median_pnl_retention_pct": 10.0,
        }
        report["winner"]["status"] = "COST SENSITIVE"
        result = validation_strength(
            report,
            {"summary": {"score": 95.0, "profitable_fold_pct": 100.0}},
        )
        self.assertLessEqual(result["score"], 49.0)
        self.assertFalse(result["independently_positive"])
        self.assertTrue(any("curve" in reason.lower() for reason in result["reasons"]))

    def test_untouched_holdout_cost_curve_overrides_development_curve(self):
        report = self._report(True)
        report["winner"]["execution_sensitivity"] = {
            "score": 90.0,
            "label": "ROBUST",
            "passes_validation_gate": True,
        }
        report["winner"]["holdout_execution_sensitivity"] = {
            "score": 32.0,
            "label": "FRAGILE",
            "passes_validation_gate": False,
        }

        result = validation_strength(report)

        self.assertEqual(result["execution_sensitivity_scope"], "untouched_holdout")
        self.assertEqual(result["execution_sensitivity_label"], "FRAGILE")
        self.assertLessEqual(result["score"], 49.0)
        self.assertFalse(result["independently_positive"])

    def test_small_unseen_samples_cannot_receive_high_robustness(self):
        report = self._report(True)
        report["winner"]["validation_metrics"]["trade_count"] = 4
        report["winner"]["holdout_metrics"]["trade_count"] = 4
        result = validation_strength(
            report,
            {"summary": {"score": 99.0, "profitable_fold_pct": 100.0}},
        )
        self.assertLessEqual(result["score"], 50.0)
        self.assertEqual(result["minimum_unseen_trades_for_high_confidence"], 15)
        self.assertTrue(
            any("at least 15 validation trades" in reason for reason in result["reasons"])
        )

    def test_walk_forward_score_is_blended_not_treated_as_probability(self):
        base = self._report(True)
        result = validation_strength(
            base,
            {"summary": {"score": 50.0, "profitable_fold_pct": 66.7}},
        )
        self.assertIsNotNone(result["walk_forward_score"])
        self.assertLessEqual(result["score"], 100.0)
        self.assertIn("not a probability", result["note"].lower())


if __name__ == "__main__":
    unittest.main()
