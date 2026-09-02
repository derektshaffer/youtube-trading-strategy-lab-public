"""Focused tests for Trading Intelligence validation scoring."""

import unittest
from unittest.mock import patch

import pandas as pd

from trading_validation_core import (
    adaptive_vs_static_compare,
    validation_strength,
    walk_forward_validate,
)
from youtube_strategy_engine import BacktestSettings, OptimizationSettings


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

    def test_walk_forward_score_is_blended_not_treated_as_probability(self):
        base = self._report(True)
        result = validation_strength(
            base,
            {"summary": {"score": 50.0, "profitable_fold_pct": 66.7}},
        )
        self.assertIsNotNone(result["walk_forward_score"])
        self.assertLessEqual(result["score"], 100.0)
        self.assertIn("not a probability", result["note"].lower())


class AdaptiveStaticComparisonTests(unittest.TestCase):
    def _frame(self, count: int = 8):
        return pd.DataFrame(
            [
                {
                    "session": f"s{index}",
                    "timestamp": pd.Timestamp(f"2026-01-{index + 1:02d}T15:00:00Z"),
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.5,
                    "close": 10.5,
                    "volume": 1000,
                }
                for index in range(count)
            ]
        )

    def _winner(self, threshold: float = 1.0):
        return {
            "winner": {
                "source_strategy_id": "s1",
                "strategy_name": "Test",
                "optimized_rules": {"min_relative_volume": threshold},
                "optimized_backtest_settings": BacktestSettings().__dict__,
                "holdout_metrics": {},
            }
        }

    def test_adaptive_comparison_uses_same_unseen_folds_and_can_show_material_advantage(self):
        adaptive_report = {
            "folds": [
                {
                    "fold": 1,
                    "history_session_count": 4,
                    "history_end": "s3",
                    "external_test_sessions": ["s4", "s5"],
                    "selected_strategy_id": "s1",
                    "optimized_rules": {"min_relative_volume": 1.0},
                },
                {
                    "fold": 2,
                    "history_session_count": 6,
                    "history_end": "s5",
                    "external_test_sessions": ["s6", "s7"],
                    "selected_strategy_id": "s1",
                    "optimized_rules": {"min_relative_volume": 2.0},
                },
            ]
        }

        def fake_backtest(rows, strategy, symbol, settings):
            adaptive_update = strategy["machine_rules"].get("min_relative_volume") == 2.0
            return {
                "metrics": {
                    "trade_count": 2,
                    "net_pnl": 30.0 if adaptive_update else 10.0,
                    "return_pct": 2.0 if adaptive_update else 0.5,
                    "win_rate_pct": 75.0 if adaptive_update else 50.0,
                    "max_drawdown_pct": 1.0,
                }
            }

        with patch("trading_validation_core.bars_to_frame", return_value=self._frame(9)), patch(
            "trading_validation_core.optimize_stock_strategies", return_value=self._winner()
        ), patch("trading_validation_core.run_backtest", side_effect=fake_backtest):
            result = adaptive_vs_static_compare(
                [],
                [{"id": "s1", "name": "Test", "machine_rules": {"min_relative_volume": 1.0}}],
                "ABC",
                BacktestSettings(),
                OptimizationSettings(),
                adaptive_report=adaptive_report,
            )

        self.assertTrue(result["evidence_valid"])
        self.assertEqual(result["decision"], "adaptive_materially_outperforms")
        self.assertEqual(result["recommended_mode"], "adaptive")
        self.assertEqual(result["folds"][0]["external_test_sessions"], ["s4", "s5"])
        self.assertEqual(result["folds"][1]["external_test_sessions"], ["s6", "s7"])
        self.assertEqual(result["adaptation_cost"]["adaptation_count"], 1)

    def test_walk_forward_never_uses_future_sessions_for_earlier_adaptive_selection(self):
        histories = []

        def fake_optimize(rows, strategies, symbol, settings, optimizer, finalize_holdout=True):
            histories.append([str(item["timestamp"])[:10] for item in rows])
            return self._winner()

        def fake_backtest(rows, strategy, symbol, settings):
            return {
                "metrics": {
                    "trade_count": 1,
                    "net_pnl": 1.0,
                    "return_pct": 0.1,
                    "win_rate_pct": 100.0,
                    "max_drawdown_pct": 0.0,
                    "profit_factor": None,
                },
                "trades": [{"pnl": 1.0}],
            }

        with patch("trading_validation_core.bars_to_frame", return_value=self._frame(9)), patch(
            "trading_validation_core.optimize_stock_strategies", side_effect=fake_optimize
        ), patch("trading_validation_core.run_backtest", side_effect=fake_backtest):
            result = walk_forward_validate(
                [],
                [{"id": "s1", "name": "Test", "machine_rules": {"min_relative_volume": 1.0}}],
                "ABC",
                BacktestSettings(),
                OptimizationSettings(),
                minimum_history_sessions=4,
                test_sessions_per_fold=2,
                max_folds=2,
            )

        self.assertEqual(len(histories), 2)
        self.assertEqual(len(histories[0]), 5)
        self.assertEqual(len(histories[1]), 7)
        self.assertLess(histories[0][-1], "2026-01-06")
        self.assertLess(histories[1][-1], "2026-01-08")
        self.assertEqual(result["folds"][0]["external_test_sessions"], ["s5", "s6"])


if __name__ == "__main__":
    unittest.main()
