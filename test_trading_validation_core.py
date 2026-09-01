"""Focused tests for Trading Intelligence validation scoring."""

import unittest
from unittest.mock import patch

from trading_validation_core import (
    _adaptive_walk_forward_strategies,
    _profitable_external_neighborhood,
    _walk_forward_session_splits,
    validation_strength,
    walk_forward_validate,
)
from youtube_strategy_engine import BacktestSettings


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


class AdaptiveWalkForwardTests(unittest.TestCase):
    def test_only_completed_profitable_unseen_rules_are_promoted_as_positive_seeds(self):
        strategies = [
            {
                "id": "s1",
                "name": "VWAP reclaim",
                "direction": "long",
                "machine_rules": {"min_relative_volume": 1.5},
            }
        ]
        experience = [
            {
                "source_strategy_id": "s1",
                "trade_count": 3,
                "net_pnl": 120.0,
                "optimized_rules": {"min_relative_volume": 2.0},
            },
            {
                "source_strategy_id": "s1",
                "trade_count": 2,
                "net_pnl": -40.0,
                "optimized_rules": {"min_relative_volume": 3.0},
            },
        ]

        adapted = _adaptive_walk_forward_strategies(strategies, experience)

        options = adapted[0]["candidate_rule_options"]["min_relative_volume"]
        self.assertIn(2.0, options)
        self.assertNotIn(3.0, options)
        self.assertEqual(
            adapted[0]["_adaptive_walk_forward_completed_fold_count"],
            2,
        )
        self.assertEqual(
            adapted[0]["_adaptive_walk_forward_profitable_fold_count"],
            1,
        )

    def test_each_fold_can_learn_only_from_unseen_folds_that_already_finished(self):
        rows = []
        for day in range(1, 9):
            rows.append(
                {
                    "timestamp": f"2026-08-{day:02d}T14:30:00Z",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 100000,
                }
            )
        strategies = [
            {
                "id": "s1",
                "name": "Adaptive test",
                "direction": "long",
                "machine_rules": {"min_relative_volume": 1.0},
            }
        ]
        optimizer_calls = []
        learned_values = [1.5, 2.0, 2.5]

        def fake_optimize(history_rows, fold_strategies, symbol, settings, optimizer, **kwargs):
            call_index = len(optimizer_calls)
            optimizer_calls.append(fold_strategies)
            return {
                "winner": {
                    "source_strategy_id": "s1",
                    "strategy_name": "Adaptive test",
                    "optimized_rules": {
                        "min_relative_volume": learned_values[call_index]
                    },
                    "optimized_backtest_settings": {},
                    "status": "VALIDATED",
                    "holdout_metrics": {},
                }
            }

        def fake_backtest(*args, **kwargs):
            return {
                "metrics": {
                    "trade_count": 1,
                    "net_pnl": 25.0,
                    "return_pct": 1.0,
                    "profit_factor": 1.5,
                    "max_drawdown_pct": 1.0,
                },
                "trades": [],
            }

        with patch(
            "trading_validation_core.optimize_stock_strategies",
            side_effect=fake_optimize,
        ), patch(
            "trading_validation_core.run_backtest",
            side_effect=fake_backtest,
        ):
            report = walk_forward_validate(
                rows,
                strategies,
                "TEST",
                minimum_history_sessions=5,
                test_sessions_per_fold=1,
                embargo_sessions=0,
                max_folds=3,
                adaptive_learning=True,
            )

        self.assertEqual(len(optimizer_calls), 3)

        first_options = optimizer_calls[0][0].get("candidate_rule_options") or {}
        second_options = optimizer_calls[1][0].get("candidate_rule_options") or {}
        third_options = optimizer_calls[2][0].get("candidate_rule_options") or {}

        self.assertNotIn("min_relative_volume", first_options)
        self.assertIn(1.5, second_options["min_relative_volume"])
        self.assertNotIn(2.0, second_options["min_relative_volume"])
        self.assertIn(1.5, third_options["min_relative_volume"])
        self.assertIn(2.0, third_options["min_relative_volume"])
        self.assertNotIn(2.5, third_options["min_relative_volume"])

        folds = report["folds"]
        self.assertEqual(folds[0]["adaptive_experience_count_before_fold"], 0)
        self.assertEqual(folds[1]["adaptive_experience_count_before_fold"], 1)
        self.assertEqual(folds[2]["adaptive_experience_count_before_fold"], 2)
        self.assertTrue(report["adaptive_learning"]["enabled"])
        self.assertEqual(report["adaptive_learning"]["experience_count"], 3)

    def test_profitable_external_neighborhood_requires_multiple_unseen_neighbors(self):
        winner_metrics = {
            "trade_count": 3,
            "net_pnl": 120.0,
            "return_pct": 6.0,
            "profit_factor": 1.8,
            "max_drawdown_pct": 2.0,
        }
        neighbors = [
            {
                "rules": {"min_relative_volume": 1.4},
                "settings": {},
                "external_metrics": {
                    "trade_count": 2,
                    "net_pnl": 60.0,
                    "return_pct": 3.0,
                },
            },
            {
                "rules": {"min_relative_volume": 1.6},
                "settings": {},
                "external_metrics": {
                    "trade_count": 2,
                    "net_pnl": 45.0,
                    "return_pct": 2.25,
                },
            },
            {
                "rules": {"min_relative_volume": 1.8},
                "settings": {},
                "external_metrics": {
                    "trade_count": 2,
                    "net_pnl": -20.0,
                    "return_pct": -1.0,
                },
            },
        ]

        neighborhood = _profitable_external_neighborhood(
            {"min_relative_volume": 1.5},
            BacktestSettings(),
            winner_metrics,
            neighbors,
        )

        self.assertTrue(neighborhood["broad_profitable"])
        self.assertEqual(neighborhood["tested_neighbor_count"], 3)
        self.assertEqual(neighborhood["profitable_neighbor_count"], 2)
        self.assertEqual(neighborhood["profitable_neighbor_pct"], 66.7)
        self.assertEqual(
            neighborhood["rule_ranges"]["min_relative_volume"],
            {"min": 1.4, "max": 1.6},
        )

    def test_broad_profitable_neighborhood_seeds_multiple_rule_values(self):
        strategies = [
            {
                "id": "s1",
                "name": "VWAP reclaim",
                "direction": "long",
                "machine_rules": {"min_relative_volume": 1.5},
            }
        ]
        experience = [
            {
                "source_strategy_id": "s1",
                "trade_count": 3,
                "net_pnl": 120.0,
                "optimized_rules": {"min_relative_volume": 2.0},
                "profitable_neighborhood": {
                    "broad_profitable": True,
                    "rule_values": {
                        "min_relative_volume": [1.75, 2.0, 2.25],
                    },
                },
            }
        ]

        adapted = _adaptive_walk_forward_strategies(
            strategies,
            experience,
        )
        options = adapted[0]["candidate_rule_options"][
            "min_relative_volume"
        ]

        self.assertIn(1.75, options)
        self.assertIn(2.0, options)
        self.assertIn(2.25, options)
        self.assertEqual(
            adapted[0][
                "_adaptive_walk_forward_neighborhood_fold_count"
            ],
            1,
        )
        self.assertGreaterEqual(
            adapted[0][
                "_adaptive_walk_forward_neighborhood_seeded_rule_values"
            ],
            2,
        )

    def test_static_baseline_uses_same_folds_without_adaptive_seeds(self):
        rows = []
        for day in range(1, 9):
            rows.append(
                {
                    "timestamp": f"2026-08-{day:02d}T14:30:00Z",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 100000,
                }
            )
        strategies = [
            {
                "id": "s1",
                "name": "Adaptive test",
                "direction": "long",
                "machine_rules": {"min_relative_volume": 1.0},
            }
        ]
        optimizer_calls = []

        def fake_optimize(
            history_rows,
            fold_strategies,
            symbol,
            settings,
            optimizer,
            **kwargs,
        ):
            optimizer_calls.append(fold_strategies)
            return {
                "winner": {
                    "source_strategy_id": "s1",
                    "strategy_name": "Adaptive test",
                    "optimized_rules": {
                        "min_relative_volume": 1.5
                    },
                    "optimized_backtest_settings": {},
                    "status": "VALIDATED",
                    "holdout_metrics": {},
                }
            }

        def fake_backtest(*args, **kwargs):
            return {
                "metrics": {
                    "trade_count": 1,
                    "net_pnl": 25.0,
                    "return_pct": 1.0,
                    "profit_factor": 1.5,
                    "max_drawdown_pct": 1.0,
                },
                "trades": [],
            }

        with patch(
            "trading_validation_core.optimize_stock_strategies",
            side_effect=fake_optimize,
        ), patch(
            "trading_validation_core.run_backtest",
            side_effect=fake_backtest,
        ):
            report = walk_forward_validate(
                rows,
                strategies,
                "TEST",
                minimum_history_sessions=5,
                test_sessions_per_fold=1,
                embargo_sessions=0,
                max_folds=3,
                adaptive_learning=True,
                compare_static_baseline=True,
            )

        # First fold is identical and reused. Each later fold runs both the
        # adaptive optimizer and a clean static counterfactual.
        self.assertEqual(len(optimizer_calls), 5)
        self.assertTrue(report["static_baseline"]["enabled"])
        self.assertEqual(len(report["static_baseline"]["folds"]), 3)
        self.assertTrue(
            report["static_baseline"]["folds"][0][
                "reused_adaptive_first_fold"
            ]
        )
        self.assertFalse(
            report["static_baseline"]["folds"][1][
                "reused_adaptive_first_fold"
            ]
        )
        self.assertTrue(report["comparison"]["enabled"])
        self.assertEqual(report["comparison"]["verdict"], "TIE")


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

    def test_sparse_walk_forward_activity_caps_robustness(self):
        report = self._report(True)
        result = validation_strength(
            report,
            {
                "summary": {
                    "score": 96.0,
                    "fold_count": 3,
                    "active_fold_count": 1,
                    "profitable_fold_count": 1,
                    "profitable_fold_pct": 100.0,
                    "external_trade_count": 8,
                }
            },
        )
        self.assertLessEqual(result["score"], 49.0)
        self.assertFalse(result["independently_positive"])
        self.assertEqual(result["walk_forward_temporal_coverage_pct"], 33.3)
        self.assertEqual(result["walk_forward_profitable_scheduled_pct"], 33.3)
        self.assertTrue(
            any("scheduled walk-forward folds" in reason.lower() for reason in result["reasons"])
        )

    def test_broad_walk_forward_activity_can_remain_independently_positive(self):
        report = self._report(True)
        result = validation_strength(
            report,
            {
                "summary": {
                    "score": 82.0,
                    "fold_count": 3,
                    "active_fold_count": 3,
                    "profitable_fold_count": 2,
                    "profitable_fold_pct": 66.7,
                    "external_trade_count": 12,
                }
            },
        )
        self.assertTrue(result["independently_positive"])
        self.assertEqual(result["walk_forward_temporal_coverage_pct"], 100.0)
        self.assertEqual(result["walk_forward_profitable_scheduled_pct"], 66.7)

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
