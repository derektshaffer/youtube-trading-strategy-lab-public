"""Tests for autonomous historical opportunity research."""

import unittest
from datetime import datetime, timezone

from youtube_strategy_engine import AppError

from trading_auto_research import (
    invalidate_legacy_autonomous_validations,
    autonomous_validation_boundaries,
    AUTONOMOUS_VALIDATION_METHOD_VERSION,
    _batched_bars,
    autonomous_research_baselines,
    _global_validation_gate,
    _invalid_symbol_from_error,
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


class InvalidHistoricalSymbolTests(unittest.TestCase):
    def test_invalid_symbol_is_parsed_from_alpaca_error(self):
        self.assertEqual(
            _invalid_symbol_from_error("Provider request failed (400): invalid symbol: D012219"),
            "D012219",
        )

    def test_bad_symbol_is_skipped_and_remaining_batch_continues(self):
        class FakeMarket:
            def __init__(self):
                self.calls = []

            def bars(self, symbols, **kwargs):
                self.calls.append(list(symbols))
                if "D012219" in symbols:
                    raise AppError("Provider request failed (400): invalid symbol: D012219")
                return {
                    symbol: [{"t": "2026-01-01T00:00:00Z", "c": 10, "v": 1000}]
                    for symbol in symbols
                }

        market = FakeMarket()
        skipped = []
        messages = []
        rows = _batched_bars(
            market,
            ["AAPL", "D012219", "MSFT"],
            start=None,
            end=None,
            timeframe="1Day",
            batch_size=100,
            skipped_symbols=skipped,
            progress=messages.append,
        )

        self.assertEqual(set(rows), {"AAPL", "MSFT"})
        self.assertEqual(skipped, ["D012219"])
        self.assertTrue(any("D012219" in message for message in messages))
        self.assertEqual(market.calls[0], ["AAPL", "D012219", "MSFT"])
        self.assertEqual(market.calls[1], ["AAPL", "MSFT"])

    def test_non_symbol_provider_error_is_not_silently_swallowed(self):
        class FakeMarket:
            def bars(self, symbols, **kwargs):
                raise AppError("The provider's usage or rate limit was reached.")

        with self.assertRaises(AppError):
            _batched_bars(
                FakeMarket(),
                ["AAPL", "MSFT"],
                start=None,
                end=None,
                timeframe="1Day",
            )


class PriorDayOpportunityTests(unittest.TestCase):
    def test_daily_discovery_uses_previous_session_activity_and_high_breakout(self):
        rows = [
            {"t": "2026-07-01T20:00:00Z", "c": 10.0, "h": 10.2, "v": 100_000},
            {"t": "2026-07-02T20:00:00Z", "c": 10.0, "h": 10.2, "v": 100_000},
            {"t": "2026-07-03T20:00:00Z", "c": 10.0, "h": 10.2, "v": 100_000},
            {"t": "2026-07-06T20:00:00Z", "c": 11.0, "h": 11.2, "v": 400_000},
            {"t": "2026-07-07T20:00:00Z", "c": 11.8, "h": 12.0, "v": 120_000},
            {"t": "2026-07-08T20:00:00Z", "c": 11.7, "h": 11.9, "v": 100_000},
        ]
        strategy = {
            "direction": "long",
            "machine_rules": {
                "previous_day_high_breakout": True,
                "min_previous_day_volume_ratio": 2.0,
                "min_previous_day_change_pct": 5.0,
            },
        }
        result = score_historical_opportunities(rows, strategy)
        self.assertGreaterEqual(result["event_count"], 1)
        self.assertEqual(result["candidate_selection_mode"], "strategy_daily_rules")
        event = next(item for item in result["events"] if item["date"] == "2026-07-07")
        self.assertTrue(event["previous_day_high_broken"])
        self.assertAlmostEqual(event["previous_day_volume_ratio"], 4.0, places=2)
        self.assertAlmostEqual(event["previous_day_change_pct"], 10.0, places=2)


class AutonomousResearchBaselineTests(unittest.TestCase):
    def test_nested_stock_optimized_copies_collapse_to_original_root(self):
        root = {
            "id": "root",
            "name": "Low-Float Momentum & Micro Pullback Strategy",
            "direction": "long",
            "machine_rules": {"min_relative_volume": 1.5},
            "validation_status": "unvalidated",
        }
        sdot = {
            **root,
            "id": "sdot-copy",
            "name": "Low-Float Momentum & Micro Pullback Strategy — SDOT optimized",
            "machine_rules": {"min_relative_volume": 3.0},
            "optimized_for_symbol": "SDOT",
            "parent_strategy_id": "root",
            "validation_status": "validated",
            "validated_rules": {"min_relative_volume": 3.0},
        }
        bbai = {
            **sdot,
            "id": "bbai-copy",
            "name": "Low-Float Momentum & Micro Pullback Strategy — SDOT optimized on BBAI",
            "machine_rules": {"min_relative_volume": 5.0},
            "optimized_for_symbol": "BBAI",
            "parent_strategy_id": "sdot-copy",
        }

        baselines = autonomous_research_baselines([bbai, sdot, root])

        self.assertEqual(len(baselines), 1)
        baseline = baselines[0]
        self.assertEqual(baseline["id"], "root")
        self.assertEqual(baseline["name"], root["name"])
        self.assertEqual(baseline["machine_rules"]["min_relative_volume"], 1.5)
        self.assertNotIn("optimized_for_symbol", baseline)
        self.assertNotIn("parent_strategy_id", baseline)
        self.assertNotIn("validated_rules", baseline)
        self.assertEqual(baseline["validation_status"], "unvalidated")
        self.assertEqual(baseline["optimization_status"], "not_run")

    def test_orphaned_stock_optimized_copy_is_unlocked_defensively(self):
        orphan = {
            "id": "orphan",
            "name": "Strategy — BBAI optimized",
            "direction": "long",
            "machine_rules": {"min_relative_volume": 2.5},
            "optimized_for_symbol": "BBAI",
            "parent_strategy_id": "missing-root",
            "optimized_backtest_settings": {"starting_cash": 10000},
            "validation_status": "validated",
            "validated_rules": {"min_relative_volume": 2.5},
        }

        baselines = autonomous_research_baselines([orphan])

        self.assertEqual(len(baselines), 1)
        baseline = baselines[0]
        self.assertEqual(baseline["id"], "orphan")
        self.assertNotIn("optimized_for_symbol", baseline)
        self.assertNotIn("parent_strategy_id", baseline)
        self.assertNotIn("optimized_backtest_settings", baseline)
        self.assertNotIn("validated_rules", baseline)
        self.assertTrue(baseline["autonomous_research_unlocked"])

    def test_distinct_root_strategies_remain_distinct(self):
        strategies = [
            {"id": "a", "name": "A", "direction": "long", "machine_rules": {}},
            {"id": "b", "name": "B", "direction": "long", "machine_rules": {}},
        ]
        baselines = autonomous_research_baselines(strategies)
        self.assertEqual({item["id"] for item in baselines}, {"a", "b"})


class EmaOpportunityDiscoveryTests(unittest.TestCase):
    def test_ema_pullback_uses_strategy_specific_daily_proxy(self):
        rows = []
        price = 10.0
        for index in range(40):
            price *= 1.01
            rows.append(
                {
                    "t": f"2026-06-{(index % 28) + 1:02d}T20:00:00Z",
                    "c": round(price, 4),
                    "h": round(price * 1.01, 4),
                    "l": round(price * 0.99, 4),
                    "v": 2_000_000,
                }
            )
        strategy = {
            "direction": "long",
            "machine_rules": {
                "fast_ema_period": 3,
                "slow_ema_period": 5,
                "trend_ema_period": 8,
                "require_fast_ema_pullback": True,
                "pullback_touch_tolerance_pct": 20.0,
                "require_price_above_slow_ema": True,
                "require_price_above_trend_ema": True,
            },
        }
        result = score_historical_opportunities(rows, strategy)
        self.assertGreater(result["event_count"], 0)
        self.assertEqual(
            result["candidate_selection_mode"],
            "strategy_ema_pullback_daily_proxy",
        )
        self.assertGreater(result["explicit_opportunity_rule_count"], 0)
        self.assertIsNotNone(result["events"][0]["fast_ema"])

    def test_extreme_rvol_from_dormant_baseline_is_audited_not_ranked_as_event(self):
        rows = []
        for index in range(25):
            rows.append(
                {
                    "t": f"2026-05-{index + 1:02d}T20:00:00Z",
                    "c": 10.0,
                    "h": 10.1,
                    "l": 9.9,
                    "v": 1_000,
                }
            )
        rows.append(
            {
                "t": "2026-06-01T20:00:00Z",
                "c": 12.0,
                "h": 12.5,
                "l": 9.8,
                "v": 1_000_000,
            }
        )
        result = score_historical_opportunities(
            rows,
            {"direction": "long", "machine_rules": {}},
        )
        self.assertEqual(result["event_count"], 0)
        self.assertEqual(result["liquidity_regime_outlier_count"], 1)
        self.assertGreater(result["peak_relative_volume"], 100)
        self.assertLess(result["peak_relative_volume_for_ranking"], 5)
        self.assertTrue(result["outlier_events"][0]["liquidity_regime_outlier"])


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
            "run_status": "complete_with_skips",
            "deep_strategies_attempted": 2,
            "deep_strategies_tested": 1,
            "deep_strategies_failed": 1,
            "failed_finalists": [
                {
                    "strategy_id": "s2",
                    "strategy_name": "Skipped",
                    "finalist_number": 2,
                    "error": "Synthetic research failure",
                }
            ],
            "timing_profile": {
                "version": 1,
                "total_seconds": 420.0,
                "samples": [
                    {"fraction": 0.1, "elapsed_seconds": 30.0},
                    {"fraction": 1.0, "elapsed_seconds": 420.0},
                ],
            },
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
        saved_run = merged["research_runs"][0]
        self.assertEqual(saved_run["kind"], "autonomous_research")
        self.assertEqual(saved_run["run_status"], "complete_with_skips")
        self.assertEqual(saved_run["deep_strategies_attempted"], 2)
        self.assertEqual(saved_run["deep_strategies_tested"], 1)
        self.assertEqual(saved_run["deep_strategies_failed"], 1)
        self.assertEqual(saved_run["failed_finalists"][0]["strategy_name"], "Skipped")
        self.assertEqual(saved_run["timing_profile"]["total_seconds"], 420.0)
        self.assertEqual(saved_run["timing_profile"]["samples"][-1]["fraction"], 1.0)


class ValidationIntegrityRegressionTests(unittest.TestCase):
    def test_missing_walk_forward_fails_closed(self):
        status, reasons = _global_validation_gate(
            anchor_report={"winner": {"status": "VALIDATED"}},
            strength={"independently_positive": True, "score": 90},
            generalization={
                "summary": {
                    "score": 90,
                    "active_symbols": 4,
                    "profitable_symbol_pct": 75,
                    "total_trades": 40,
                }
            },
            walk_forward=None,
            broad_universe=True,
        )
        self.assertEqual(status, "research_only")
        self.assertTrue(any("walk-forward" in reason.lower() for reason in reasons))

    def test_discovery_cutoff_is_strictly_before_untouched_validation_period(self):
        end = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
        boundaries = autonomous_validation_boundaries(end, validation_days=180)
        self.assertEqual(boundaries["validation_end"], end)
        self.assertEqual(
            boundaries["discovery_cutoff"],
            boundaries["validation_start"],
        )
        self.assertLess(boundaries["discovery_cutoff"], boundaries["validation_end"])
        self.assertEqual(
            (boundaries["validation_end"] - boundaries["validation_start"]).days,
            180,
        )

    def test_failed_finalist_receives_terminal_validation_state(self):
        library = {
            "strategies": [
                {
                    "id": "s2",
                    "source_type": "autonomous_web_research",
                    "validation_status": "unvalidated",
                }
            ],
            "validation_runs": [],
            "research_runs": [],
        }
        report = {
            "generated_at": "2026-08-30T20:00:00Z",
            "validation_method_version": AUTONOMOUS_VALIDATION_METHOD_VERSION,
            "universe": {"source": "point_in_time"},
            "failed_finalists": [
                {
                    "strategy_id": "s2",
                    "strategy_name": "Skipped",
                    "error": "No candidate stocks had usable intraday history for deep testing.",
                }
            ],
            "results": [],
        }
        merged = merge_autonomous_research_into_library(library, report)
        strategy = merged["strategies"][0]
        self.assertEqual(strategy["validation_status"], "research_only")
        self.assertEqual(
            strategy["last_autonomous_research"]["validation_status"],
            "insufficient_data",
        )
        self.assertFalse(strategy["last_autonomous_research"]["retryable"])

    def test_legacy_hindsight_validation_is_demoted_for_revalidation(self):
        library = {
            "strategies": [
                {
                    "id": "legacy",
                    "source_type": "book_or_document",
                    "research_hypothesis_id": "h1",
                    "validation_status": "validated",
                    "validated_rules": {"min_relative_volume": 2.0},
                    "validated_backtest_settings": {"starting_cash": 10000},
                    "validated_at": "2026-08-29T00:00:00Z",
                    "last_autonomous_research": {
                        "validation_status": "validated",
                    },
                }
            ],
            "research_hypotheses": [{"id": "h1", "status": "validated"}],
        }
        updated, changed = invalidate_legacy_autonomous_validations(library)
        self.assertEqual(changed, 1)
        strategy = updated["strategies"][0]
        self.assertEqual(strategy["validation_status"], "research_only")
        self.assertEqual(
            strategy["last_autonomous_research"]["validation_status"],
            "stale_methodology",
        )
        self.assertNotIn("validated_rules", strategy)
        self.assertEqual(
            updated["research_hypotheses"][0]["status"],
            "queued_for_validation",
        )

    def test_manual_validation_without_autonomous_record_is_preserved(self):
        library = {
            "strategies": [
                {
                    "id": "manual",
                    "source_type": "book_or_document",
                    "validation_status": "validated",
                    "validated_rules": {"min_relative_volume": 2.0},
                }
            ]
        }
        updated, changed = invalidate_legacy_autonomous_validations(library)
        self.assertEqual(changed, 0)
        self.assertEqual(updated["strategies"][0]["validation_status"], "validated")
        self.assertIn("validated_rules", updated["strategies"][0])


if __name__ == "__main__":
    unittest.main()
