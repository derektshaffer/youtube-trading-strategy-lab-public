"""Focused tests for Trading Intelligence validation scoring."""

import unittest

from trading_validation_core import validation_strength


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
