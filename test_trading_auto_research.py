"""Tests for autonomous historical opportunity research."""

import unittest

from trading_auto_research import (
    _global_validation_gate,
    deterministic_catalog_sample,
    deterministic_symbol_sample,
    infer_symbol_lifecycle,
    merge_autonomous_research_into_library,
    rank_historical_opportunities,
    score_historical_opportunities,
    select_event_research_window,
)


def daily_bars(days=30, start=10.0, event_every=5):
    rows = []
    price = start
    for index in range(days):
        previous = price
        if index and index % event_every == 0:
            price = previous * 1.08
            volume = 500_000
        else:
            price = previous * 1.002
            volume = 100_000
        rows.append(
            {
                "t": f"2026-07-{index + 1:02d}T20:00:00Z",
                "c": round(price, 4),
                "v": volume,
            }
        )
    return rows


class AutonomousResearchTests(unittest.TestCase):
    def test_broad_sample_keeps_priority_and_is_deterministic(self):
        symbols = [f"S{index:04d}" for index in range(1000)]
        first = deterministic_symbol_sample(
            symbols,
            maximum=50,
            priority=["HOT1", "HOT2"],
        )
        second = deterministic_symbol_sample(
            list(reversed(symbols)),
            maximum=50,
            priority=["HOT1", "HOT2"],
        )
        self.assertEqual(first, second)
        self.assertEqual(first[:2], ["HOT1", "HOT2"])
        self.assertEqual(len(first), 50)

    def test_catalog_sample_reserves_space_for_inactive_symbols(self):
        catalog = [
            {"symbol": f"A{index:03d}", "status": "active"}
            for index in range(100)
        ] + [
            {"symbol": f"D{index:03d}", "status": "inactive"}
            for index in range(60)
        ]
        sampled, stats = deterministic_catalog_sample(
            catalog,
            maximum=50,
            priority=["A000", "A001"],
            inactive_share=0.30,
        )
        self.assertEqual(sampled[:2], ["A000", "A001"])
        self.assertEqual(len(sampled), 50)
        self.assertEqual(stats["inactive_sampled"], 15)
        self.assertEqual(stats["active_sampled"], 35)

    def test_lifecycle_is_inferred_from_actual_bar_dates(self):
        rows = [
            {"t": "2024-01-03T21:00:00Z", "c": 10, "v": 1000},
            {"t": "2024-01-04T21:00:00Z", "c": 11, "v": 1200},
            {"t": "2024-02-01T21:00:00Z", "c": 9, "v": 900},
        ]
        lifecycle = infer_symbol_lifecycle(rows)
        self.assertEqual(lifecycle["first_observed_date"], "2024-01-03")
        self.assertEqual(lifecycle["last_observed_date"], "2024-02-01")
        self.assertEqual(lifecycle["observed_sessions"], 3)

    def test_event_window_selects_dense_historical_cluster(self):
        opportunities = [
            {"date": "2023-01-10", "relative_volume": 2.0, "day_change_pct": 5},
            {"date": "2023-01-20", "relative_volume": 3.0, "day_change_pct": 8},
            {"date": "2023-02-05", "relative_volume": 4.0, "day_change_pct": 10},
            {"date": "2025-06-01", "relative_volume": 9.0, "day_change_pct": 20},
        ]
        window = select_event_research_window(
            opportunities,
            window_days=120,
            buffer_days=30,
        )
        self.assertIsNotNone(window)
        self.assertEqual(window["event_count"], 3)
        self.assertIn("2023-01-20", window["event_dates"])
        self.assertNotIn("2025-06-01", window["event_dates"])

    def test_opportunity_score_uses_historical_conditions_not_trade_outcomes(self):
        strategy = {
            "direction": "long",
            "machine_rules": {
                "max_price": 30.0,
                "min_day_change_pct": 5.0,
                "min_relative_volume": 2.0,
            },
        }
        result = score_historical_opportunities(daily_bars(), strategy)
        self.assertGreater(result["event_count"], 0)
        self.assertEqual(result["candidate_selection_mode"], "strategy_daily_rules")
        self.assertNotIn("pnl", result)
        self.assertNotIn("win_rate", result)

    def test_ranking_prefers_more_strategy_relevant_events(self):
        strategy = {
            "direction": "long",
            "machine_rules": {
                "min_day_change_pct": 5.0,
                "min_relative_volume": 2.0,
            },
        }
        ranked = rank_historical_opportunities(
            {
                "FREQUENT": daily_bars(event_every=4),
                "RARE": daily_bars(event_every=10),
            },
            strategy,
            limit=2,
        )
        self.assertEqual(ranked[0]["symbol"], "FREQUENT")
        self.assertGreater(ranked[0]["event_count"], ranked[1]["event_count"])

    def test_current_screener_fallback_can_never_receive_validated_status(self):
        status, reasons = _global_validation_gate(
            anchor_report={"winner": {"status": "VALIDATED"}},
            strength={"independently_positive": True, "score": 90},
            generalization={
                "summary": {
                    "score": 90,
                    "active_symbols": 5,
                    "profitable_symbol_pct": 80,
                    "total_trades": 50,
                }
            },
            walk_forward={
                "summary": {
                    "profitable_fold_pct": 100,
                    "external_trade_count": 10,
                }
            },
            broad_universe=False,
        )
        self.assertEqual(status, "research_only")
        self.assertTrue(any("selection bias" in reason.lower() for reason in reasons))

    def test_library_merge_freezes_only_full_gate_winner(self):
        library = {
            "strategies": [{"id": "s1", "name": "Test", "validation_status": "unvalidated"}],
            "validation_runs": [],
            "research_runs": [],
        }
        report = {
            "generated_at": "2026-08-27T05:00:00Z",
            "timeframe": "5Min",
            "intraday_lookback_days": 60,
            "universe": {"source": "active_equities_sample"},
            "results": [
                {
                    "strategy_id": "s1",
                    "strategy_name": "Test",
                    "anchor_symbol": "AAA",
                    "candidate_symbols": ["AAA", "BBB", "CCC"],
                    "global_score": 82,
                    "validation_status": "validated",
                    "gate_reasons": [],
                    "strength": {"score": 80, "label": "STRONG"},
                    "generalization": {"summary": {"score": 85, "label": "BROAD"}},
                    "walk_forward": {"summary": {"profitable_fold_pct": 100}},
                    "optimization_report": {
                        "winner": {
                            "status": "VALIDATED",
                            "optimized_rules": {"min_relative_volume": 2.5},
                            "optimized_backtest_settings": {"starting_cash": 10000},
                            "training_metrics": {},
                            "validation_metrics": {},
                            "holdout_metrics": {},
                            "stress_metrics": {},
                        }
                    },
                }
            ],
        }
        merged = merge_autonomous_research_into_library(library, report)
        strategy = merged["strategies"][0]
        self.assertEqual(strategy["validation_status"], "validated")
        self.assertEqual(strategy["validated_rules"]["min_relative_volume"], 2.5)
        self.assertTrue(merged["validation_runs"][0]["autonomous"])
        self.assertEqual(merged["research_runs"][0]["kind"], "autonomous_research")


if __name__ == "__main__":
    unittest.main()
