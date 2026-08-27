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
