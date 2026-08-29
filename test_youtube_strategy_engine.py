from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ast
import json
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
import warnings
from unittest.mock import patch
from zoneinfo import ZoneInfo

import youtube_strategy_engine as engine


ET = ZoneInfo("America/New_York")


def bar(day: int, minute: int, opening: float, high: float, low: float, close: float, volume: int = 1000) -> dict:
    local = datetime(2026, 8, day, 9, 30, tzinfo=ET) + timedelta(minutes=minute)
    return {
        "t": local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "o": opening,
        "h": high,
        "l": low,
        "c": close,
        "v": volume,
    }


def clock_bar(
    day: int,
    hour: int,
    minute: int,
    opening: float,
    high: float,
    low: float,
    close: float,
    volume: int = 1000,
) -> dict:
    local = datetime(2026, 8, day, hour, minute, tzinfo=ET)
    return {
        "t": local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "o": opening,
        "h": high,
        "l": low,
        "c": close,
        "v": volume,
    }


def simple_strategy(**rules) -> dict:
    return {
        "id": "test-strategy",
        "name": "Test strategy",
        "direction": "long",
        "machine_rules": engine.normalize_machine_rules({"min_price": 1, "stop_loss_pct": 5, "reward_risk": 1, **rules}),
        "unresolved_rules": [],
    }


class UrlTests(unittest.TestCase):
    def test_normalizes_watch_and_strips_playlist_tracking(self):
        self.assertEqual(
            engine.normalize_youtube_url("https://www.youtube.com/watch?v=abcDEF12_-3&list=123&si=456"),
            "https://www.youtube.com/watch?v=abcDEF12_-3",
        )

    def test_normalizes_short_url(self):
        self.assertEqual(engine.normalize_youtube_url("https://youtu.be/abcDEF12_-3?t=9"), "https://www.youtube.com/watch?v=abcDEF12_-3")

    def test_normalizes_shorts_url(self):
        self.assertEqual(engine.normalize_youtube_url("https://youtube.com/shorts/abcDEF12_-3"), "https://www.youtube.com/watch?v=abcDEF12_-3")

    def test_rejects_playlist_only(self):
        with self.assertRaisesRegex(engine.AppError, "playlist-only"):
            engine.normalize_youtube_url("https://www.youtube.com/playlist?list=PLabcdef")

    def test_rejects_lookalike_hostname(self):
        with self.assertRaises(engine.AppError):
            engine.normalize_youtube_url("https://youtube.com.evil.example/watch?v=abcDEF12_-3")

    def test_parse_urls_deduplicates_and_preserves_errors(self):
        urls, errors = engine.parse_youtube_urls(
            "https://youtu.be/abcDEF12_-3\nhttps://youtube.com/watch?v=abcDEF12_-3\nhttps://example.com/x"
        )
        self.assertEqual(len(urls), 1)
        self.assertEqual(len(errors), 1)

    def test_timestamp_link(self):
        self.assertEqual(
            engine.timestamped_youtube_url("https://www.youtube.com/watch?v=abcDEF12_-3", "01:02"),
            "https://www.youtube.com/watch?v=abcDEF12_-3&t=62s",
        )

    def test_symbol_validation(self):
        self.assertEqual(engine.parse_symbols("nvda, AAPL; BRK.B invalid! NVDA"), ["NVDA", "AAPL", "BRK.B"])


class PriorDayRuleTests(unittest.TestCase):
    def test_prior_day_rules_are_normalized(self):
        rules = engine.normalize_machine_rules(
            {
                "previous_day_high_breakout": "true",
                "min_previous_day_volume_ratio": "2.5",
                "min_previous_day_change_pct": "4.0",
            }
        )
        self.assertTrue(rules["previous_day_high_breakout"])
        self.assertEqual(rules["min_previous_day_volume_ratio"], 2.5)
        self.assertEqual(rules["min_previous_day_change_pct"], 4.0)

    def test_prior_day_indicators_use_only_completed_sessions(self):
        rows = [
            bar(18, 0, 10.0, 10.2, 9.8, 10.0, 100),
            bar(19, 0, 10.0, 10.2, 9.8, 10.0, 100),
            bar(20, 0, 10.0, 10.2, 9.8, 10.0, 100),
            bar(21, 0, 10.0, 11.5, 9.9, 11.0, 400),
            bar(22, 0, 11.0, 12.0, 10.9, 12.0, 120),
        ]
        frame = engine.add_indicators(
            engine.bars_to_frame(rows),
            simple_strategy(
                previous_day_high_breakout=True,
                min_previous_day_volume_ratio=2.0,
                min_previous_day_change_pct=5.0,
            ),
        )
        current = frame[frame["session"] == "2026-08-22"].iloc[0]
        self.assertAlmostEqual(float(current["previous_daily_high"]), 11.5, places=6)
        self.assertAlmostEqual(float(current["previous_day_volume_ratio"]), 4.0, places=6)
        self.assertAlmostEqual(float(current["previous_day_change_pct"]), 10.0, places=6)

    def test_previous_day_high_breakout_requires_actual_crossing_bar(self):
        rules = engine.normalize_machine_rules(
            {
                "previous_day_high_breakout": True,
                "min_previous_day_volume_ratio": 2.0,
                "min_previous_day_change_pct": 5.0,
            }
        )
        crossing = {
            "close": 12.0,
            "previous_bar_close": 11.0,
            "previous_daily_high": 11.5,
            "previous_day_volume_ratio": 4.0,
            "previous_day_change_pct": 10.0,
            "session_minute": 10,
        }
        self.assertTrue(engine.evaluate_signal(crossing, rules))

        already_above = dict(crossing)
        already_above["previous_bar_close"] = 11.8
        self.assertFalse(engine.evaluate_signal(already_above, rules))

    def test_optimizer_tunes_prior_day_thresholds_but_keeps_structural_breakout(self):
        strategy = simple_strategy(
            previous_day_high_breakout=True,
            min_previous_day_volume_ratio=2.0,
            min_previous_day_change_pct=5.0,
        )
        variants = engine.generate_strategy_variants(strategy, maximum=36)
        self.assertGreater(len(variants), 1)
        self.assertTrue(all(item["previous_day_high_breakout"] is True for item in variants))
        self.assertTrue(
            any(item["min_previous_day_volume_ratio"] != 2.0 for item in variants)
        )
        self.assertTrue(
            any(item["min_previous_day_change_pct"] != 5.0 for item in variants)
        )


class EmaRuleTests(unittest.TestCase):
    def test_ema_rules_normalize_and_validate(self):
        rules = engine.normalize_machine_rules(
            {
                "fast_ema_period": "9",
                "slow_ema_period": "20",
                "trend_ema_period": "200",
                "require_price_above_slow_ema": "true",
                "require_price_above_trend_ema": True,
                "require_fast_ema_pullback": True,
                "pullback_touch_tolerance_pct": "0.75",
                "max_pullback_number": "2",
                "stop_below_fast_ema": True,
                "stop_ema_buffer_pct": "0.25",
            }
        )
        self.assertEqual(rules["fast_ema_period"], 9)
        self.assertEqual(rules["slow_ema_period"], 20)
        self.assertEqual(rules["trend_ema_period"], 200)
        self.assertTrue(rules["require_fast_ema_pullback"])
        self.assertEqual(rules["pullback_touch_tolerance_pct"], 0.75)
        self.assertEqual(rules["max_pullback_number"], 2)
        self.assertTrue(rules["stop_below_fast_ema"])

    def test_ema_signal_requires_alignment_pullback_and_pullback_number(self):
        rules = engine.normalize_machine_rules(
            {
                "fast_ema_period": 9,
                "slow_ema_period": 20,
                "trend_ema_period": 200,
                "require_price_above_slow_ema": True,
                "require_price_above_trend_ema": True,
                "require_fast_ema_rising": True,
                "require_fast_ema_pullback": True,
                "max_pullback_number": 2,
                "require_pullback_breakout": True,
            }
        )
        row = {
            "close": 10.5,
            "fast_ema": 10.2,
            "slow_ema": 10.0,
            "trend_ema": 9.0,
            "fast_ema_rising": True,
            "fast_ema_pullback_recent": True,
            "fast_ema_pullback_number": 2,
            "pullback_breakout": True,
            "session_minute": 15,
        }
        self.assertTrue(engine.evaluate_signal(row, rules))
        too_late = dict(row)
        too_late["fast_ema_pullback_number"] = 3
        self.assertFalse(engine.evaluate_signal(too_late, rules))

    def test_ema_indicators_are_causal_and_detect_pullback_state(self):
        rows = []
        prices = [10.0, 10.2, 10.4, 10.6, 10.8, 10.7, 10.9, 11.0, 11.1, 11.2]
        for minute, price in enumerate(prices):
            rows.append(
                bar(
                    18,
                    minute,
                    price - 0.05,
                    price + 0.08,
                    price - (0.25 if minute == 5 else 0.08),
                    price,
                    10_000,
                )
            )
        strategy = simple_strategy(
            fast_ema_period=3,
            slow_ema_period=5,
            trend_ema_period=8,
            require_fast_ema_pullback=True,
            pullback_touch_tolerance_pct=1.0,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            frame = engine.add_indicators(engine.bars_to_frame(rows), strategy)
        self.assertTrue(frame["fast_ema"].tail(5).notna().all())
        self.assertTrue(frame["slow_ema"].tail(5).notna().all())
        self.assertTrue(frame["trend_ema"].tail(2).notna().all())
        self.assertTrue(bool(frame["fast_ema_pullback_recent"].tail(5).any()))

    def test_optimizer_can_tune_ema_pullback_tolerance(self):
        strategy = simple_strategy(
            fast_ema_period=9,
            require_fast_ema_pullback=True,
            pullback_touch_tolerance_pct=0.75,
        )
        variants = engine.generate_strategy_variants(strategy, maximum=80)
        self.assertTrue(
            any(item["pullback_touch_tolerance_pct"] != 0.75 for item in variants)
        )

    def test_optimizer_prioritizes_ai_assumption_candidate_values(self):
        strategy = simple_strategy(max_vwap_distance_pct=3.0)
        strategy["research_rule_overrides"] = {
            "max_vwap_distance_pct": 3.0,
        }
        strategy["ai_candidate_rule_options"] = {
            "max_vwap_distance_pct": [2.4, 3.0, 3.6],
        }
        variants = engine.generate_strategy_variants(strategy, maximum=36)
        self.assertTrue(
            any(item["max_vwap_distance_pct"] == 2.4 for item in variants)
        )
        self.assertTrue(
            any(item["max_vwap_distance_pct"] == 3.6 for item in variants)
        )


class RuleTests(unittest.TestCase):
    def test_numeric_rules_are_cleaned(self):
        rules = engine.normalize_machine_rules({"min_price": "3.5", "max_price": "1.5", "stop_loss_pct": "-4", "reward_risk": "nan"})
        self.assertEqual(rules["min_price"], 1.5)
        self.assertEqual(rules["max_price"], 3.5)
        self.assertIsNone(rules["stop_loss_pct"])
        self.assertIsNone(rules["reward_risk"])

    def test_clocks_are_validated(self):
        rules = engine.normalize_machine_rules({"session_start": "09:35", "session_end": "25:88"})
        self.assertEqual(rules["session_start"], "09:35")
        self.assertIsNone(rules["session_end"])

    def test_first_session_does_not_have_future_relative_volume(self):
        rows = [bar(18, 0, 10, 11, 9, 10, 100), bar(18, 1, 10, 11, 9, 10, 200), bar(19, 0, 10, 11, 9, 10, 300)]
        frame = engine.add_indicators(engine.bars_to_frame(rows), simple_strategy())
        self.assertTrue(frame.loc[frame["session"] == "2026-08-18", "relative_volume"].isna().all())
        self.assertTrue(frame.loc[frame["session"] == "2026-08-19", "relative_volume"].notna().all())

    def test_vwap_resets_each_session(self):
        rows = [bar(18, 0, 10, 10, 10, 10, 100), bar(19, 0, 20, 20, 20, 20, 100)]
        frame = engine.add_indicators(engine.bars_to_frame(rows), simple_strategy())
        self.assertEqual(frame["vwap"].tolist(), [10.0, 20.0])

    def test_breakout_high_excludes_current_bar(self):
        rows = [bar(18, 0, 9, 10, 8, 9), bar(18, 1, 9, 11, 8, 10), bar(18, 2, 10, 13, 9, 12)]
        frame = engine.add_indicators(engine.bars_to_frame(rows), simple_strategy(breakout_lookback_bars=2))
        self.assertEqual(float(frame.iloc[2]["prior_breakout_high"]), 11.0)
        self.assertTrue(engine.evaluate_signal(frame.iloc[2], engine.normalize_machine_rules({"breakout_lookback_bars": 2})))

    def test_unknown_relative_volume_does_not_pass(self):
        rows = [bar(18, 0, 10, 11, 9, 10)]
        frame = engine.add_indicators(engine.bars_to_frame(rows), simple_strategy(min_relative_volume=2))
        self.assertFalse(engine.evaluate_signal(frame.iloc[0], engine.normalize_machine_rules({"min_relative_volume": 2})))

    def test_filters_out_extended_hours(self):
        before = datetime(2026, 8, 18, 8, 0, tzinfo=ET).astimezone(timezone.utc)
        rows = [{"t": before.isoformat(), "o": 1, "h": 1, "l": 1, "c": 1, "v": 10}, bar(18, 0, 10, 11, 9, 10)]
        self.assertEqual(len(engine.bars_to_frame(rows)), 1)


class ParallelOptimizerTests(unittest.TestCase):
    def test_parallel_family_optimizer_matches_sequential_ranking(self):
        rows = []
        for day in (18, 19, 20, 21, 22, 23):
            for minute in range(10):
                close = 10.0 + (day - 18) * 0.03 + minute * 0.04
                rows.append(
                    bar(
                        day,
                        minute,
                        close - 0.02,
                        close + 0.08,
                        close - 0.07,
                        close,
                        1200 + minute * 80,
                    )
                )

        strategies = [
            {
                **simple_strategy(min_day_change_pct=-50.0, breakout_lookback_bars=2),
                "id": "parallel-a",
                "name": "Parallel A",
            },
            {
                **simple_strategy(min_day_change_pct=-50.0, min_relative_volume=0.1),
                "id": "parallel-b",
                "name": "Parallel B",
            },
        ]
        settings = engine.BacktestSettings(
            starting_cash=10_000,
            risk_per_trade_pct=0.5,
            max_position_pct=20.0,
            spread_bps=0,
            slippage_bps=0,
        )
        optimizer = engine.OptimizationSettings(
            max_variants_per_strategy=2,
            finalists_per_strategy=1,
            minimum_training_trades=1,
            minimum_validation_trades=1,
            optimize_position_sizing=False,
            max_execution_variants_per_finalist=1,
            selection_mode="validated",
        )
        sequential = engine.optimize_stock_strategies(
            rows,
            strategies,
            "TEST",
            settings,
            optimizer,
            finalize_holdout=False,
        )
        parallel = engine.optimize_stock_strategies_parallel(
            rows,
            strategies,
            "TEST",
            settings,
            optimizer,
            max_workers=2,
            finalize_holdout=False,
        )

        self.assertEqual(
            [item["source_strategy_id"] for item in parallel["rankings"]],
            [item["source_strategy_id"] for item in sequential["rankings"]],
        )
        self.assertEqual(
            parallel["winner"]["source_strategy_id"],
            sequential["winner"]["source_strategy_id"],
        )
        self.assertEqual(parallel["strategies_tested"], sequential["strategies_tested"])
        self.assertEqual(parallel["unique_configurations_tested"], sequential["unique_configurations_tested"])
        self.assertEqual(parallel["parallelized_by"], "strategy_family")
        self.assertGreaterEqual(parallel["parallel_workers"], 2)

    def test_distributed_family_and_timeframe_merge_matches_single_process(self):
        rows = []
        for day in (18, 19, 20, 21, 22, 23):
            for minute in range(20):
                close = 9.5 + (day - 18) * 0.04 + minute * 0.025
                rows.append(
                    bar(
                        day,
                        minute,
                        close - 0.02,
                        close + 0.07,
                        close - 0.06,
                        close,
                        1400 + minute * 60,
                    )
                )
        strategies = [
            {
                **simple_strategy(min_day_change_pct=-50.0, breakout_lookback_bars=2),
                "id": "dist-a",
                "name": "Distributed A",
            },
            {
                **simple_strategy(min_day_change_pct=-50.0, min_relative_volume=0.1),
                "id": "dist-b",
                "name": "Distributed B",
            },
        ]
        settings = engine.BacktestSettings(
            starting_cash=10_000,
            risk_per_trade_pct=0.5,
            max_position_pct=20.0,
            spread_bps=0,
            slippage_bps=0,
        )
        optimizer = engine.OptimizationSettings(
            max_variants_per_strategy=2,
            finalists_per_strategy=1,
            minimum_training_trades=1,
            minimum_validation_trades=1,
            optimize_position_sizing=False,
            max_execution_variants_per_finalist=1,
            selection_mode="validated",
        )
        timeframes = ("1Min", "5Min")
        expected = engine.optimize_stock_timeframes(
            rows,
            strategies,
            "TEST",
            settings,
            optimizer,
            timeframes=timeframes,
        )

        reports_by_interval = {}
        for timeframe in timeframes:
            interval_rows = engine.resample_intraday_bars(
                rows,
                timeframe,
                include_extended_hours=True,
            )
            family_reports = [
                engine.optimize_stock_strategies(
                    interval_rows,
                    [strategy],
                    "TEST",
                    settings,
                    optimizer,
                    finalize_holdout=False,
                )
                for strategy in strategies
            ]
            reports_by_interval[timeframe] = engine.combine_strategy_family_reports(
                family_reports,
                parallel_workers=len(family_reports),
            )

        distributed = engine.combine_stock_timeframe_reports(
            rows,
            strategies,
            "TEST",
            reports_by_interval,
            timeframes,
        )
        self.assertEqual(distributed["timeframe"], expected["timeframe"])
        self.assertEqual(
            distributed["winner"]["source_strategy_id"],
            expected["winner"]["source_strategy_id"],
        )
        self.assertEqual(
            distributed["unique_configurations_tested"],
            expected["unique_configurations_tested"],
        )
        self.assertEqual(
            distributed["winner"]["holdout_metrics"],
            expected["winner"]["holdout_metrics"],
        )


class IndicatorCacheEquivalenceTests(unittest.TestCase):
    def test_cached_base_rebuild_matches_full_strategy_indicators(self):
        rows = []
        for day in (18, 19, 20, 21):
            for minute in range(30):
                close = 10.0 + day * 0.02 + minute * 0.015 + (0.08 if minute % 7 == 0 else 0)
                rows.append(
                    bar(
                        day,
                        minute,
                        close - 0.02,
                        close + 0.06,
                        close - 0.07,
                        close,
                        1000 + minute * 50,
                    )
                )
        frame = engine.bars_to_frame(rows)
        strategy = simple_strategy(
            breakout_lookback_bars=7,
            opening_range_minutes=10,
            fast_ema_period=5,
            slow_ema_period=9,
            trend_ema_period=15,
            pullback_touch_tolerance_pct=0.8,
            require_fast_ema_pullback=True,
        )
        direct = engine.add_indicators(frame, strategy)
        base = engine.add_indicators(frame, {"machine_rules": {}})
        cached = engine.apply_strategy_specific_indicators(base, strategy)

        dependent_columns = [
            "prior_breakout_high",
            "opening_range_high",
            "fast_ema",
            "slow_ema",
            "trend_ema",
            "fast_ema_distance_pct",
            "fast_ema_touch_distance_pct",
            "fast_ema_pullback_number",
            "fast_ema_pullback_recent",
            "fast_ema_rising",
        ]
        for column in dependent_columns:
            left = direct[column].astype(object).where(direct[column].notna(), None).tolist()
            right = cached[column].astype(object).where(cached[column].notna(), None).tolist()
            self.assertEqual(left, right, column)


class BacktestTests(unittest.TestCase):
    def setUp(self):
        self.settings = engine.BacktestSettings(
            starting_cash=10_000,
            risk_per_trade_pct=1,
            max_position_pct=100,
            spread_bps=0,
            slippage_bps=0,
        )

    def test_entry_uses_next_bar_open(self):
        rows = [bar(18, 0, 100, 101, 99, 100), bar(18, 1, 103, 104, 102, 103), bar(18, 2, 103, 104, 102, 103)]
        result = engine.run_backtest(rows, simple_strategy(), "TEST", self.settings)
        self.assertEqual(result["trades"][0]["entry_price"], 103.0)

    def test_same_bar_stop_and_target_uses_conservative_stop(self):
        rows = [bar(18, 0, 100, 101, 99, 100), bar(18, 1, 100, 101, 99, 100), bar(18, 2, 100, 106, 94, 100)]
        result = engine.run_backtest(rows, simple_strategy(), "TEST", self.settings)
        self.assertEqual(result["trades"][0]["reason"], "Stop loss")
        self.assertLess(result["trades"][0]["pnl"], 0)

    def test_adverse_opening_gap_uses_gap_price(self):
        rows = [bar(18, 0, 100, 101, 99, 100), bar(18, 1, 100, 101, 99, 100), bar(18, 2, 90, 94, 89, 92)]
        result = engine.run_backtest(rows, simple_strategy(), "TEST", self.settings)
        self.assertEqual(result["trades"][0]["exit_price"], 90.0)

    def test_costs_reduce_return(self):
        rows = [bar(18, 0, 100, 101, 99, 100), bar(18, 1, 100, 101, 99, 100), bar(18, 2, 100, 106, 99, 105)]
        free = engine.run_backtest(rows, simple_strategy(), "TEST", self.settings)
        expensive = engine.run_backtest(rows, simple_strategy(), "TEST", engine.BacktestSettings(spread_bps=30, slippage_bps=20, fee_per_order=1))
        self.assertGreater(free["metrics"]["net_pnl"], expensive["metrics"]["net_pnl"])

    def test_short_strategy_is_rejected(self):
        strategy = simple_strategy()
        strategy["direction"] = "short"
        with self.assertRaisesRegex(engine.AppError, "Short-only"):
            engine.run_backtest([bar(18, 0, 10, 11, 9, 10)], strategy, "TEST")

    def test_holdout_is_chronological(self):
        rows = []
        for day in (18, 19, 20, 21):
            rows.extend([bar(day, 0, 100, 101, 99, 100), bar(day, 1, 100, 101, 99, 100), bar(day, 2, 100, 106, 99, 105)])
        result = engine.run_backtest(rows, simple_strategy(), "TEST", self.settings)
        self.assertEqual(result["holdout_start"], "2026-08-20")
        self.assertGreater(result["out_of_sample"]["trade_count"], 0)

    def test_layered_entries_can_overlap_without_multiplying_total_allocation(self):
        rows = [
            bar(18, 0, 100, 101, 99, 100),
            bar(18, 1, 100, 101, 99, 100),
            bar(18, 2, 100, 101, 99, 100),
            bar(18, 3, 100, 101, 99, 100),
            bar(18, 4, 100, 101, 99, 100),
        ]
        settings = engine.BacktestSettings(
            starting_cash=10_000,
            risk_per_trade_pct=3,
            max_position_pct=90,
            spread_bps=0,
            slippage_bps=0,
            max_concurrent_positions=3,
            allow_extended_hours=False,
        )
        result = engine.run_backtest(rows, simple_strategy(stop_loss_pct=20, reward_risk=10), "TEST", settings)
        self.assertEqual(result["metrics"]["trade_count"], 3)
        self.assertEqual([trade["trade_id"] for trade in result["trades"]], [1, 2, 3])
        self.assertLessEqual(
            sum(trade["entry_price"] * trade["quantity"] for trade in result["trades"]),
            settings.starting_cash * settings.max_position_pct / 100.0 + 1,
        )

    def test_extended_hours_can_trade_at_reduced_size(self):
        rows = [
            clock_bar(18, 17, 0, 10, 10.1, 9.9, 10),
            clock_bar(18, 17, 5, 10, 10.1, 9.9, 10),
            clock_bar(18, 17, 10, 10, 10.1, 9.9, 10),
        ]
        enabled = engine.BacktestSettings(
            starting_cash=10_000,
            risk_per_trade_pct=4,
            max_position_pct=100,
            spread_bps=0,
            slippage_bps=0,
            max_concurrent_positions=1,
            allow_extended_hours=True,
            extended_hours_position_scale=0.25,
        )
        disabled = engine.BacktestSettings(
            starting_cash=10_000,
            risk_per_trade_pct=4,
            max_position_pct=100,
            spread_bps=0,
            slippage_bps=0,
            max_concurrent_positions=1,
            allow_extended_hours=False,
        )
        extended = engine.run_backtest(rows, simple_strategy(), "TEST", enabled)
        regular_only = engine.run_backtest(rows, simple_strategy(), "TEST", disabled)
        self.assertEqual(extended["metrics"]["trade_count"], 1)
        self.assertEqual(extended["trades"][0]["entry_session_type"], "extended")
        self.assertEqual(regular_only["metrics"]["trade_count"], 0)

    def test_strategy_end_time_can_be_ignored(self):
        rows = [
            clock_bar(18, 12, 0, 10, 10.1, 9.9, 10),
            clock_bar(18, 12, 5, 10, 10.1, 9.9, 10),
            clock_bar(18, 12, 10, 10, 10.1, 9.9, 10),
        ]
        strategy = simple_strategy(session_end="11:30")
        ignored = engine.run_backtest(
            rows,
            strategy,
            "TEST",
            engine.BacktestSettings(
                spread_bps=0,
                slippage_bps=0,
                max_concurrent_positions=1,
                ignore_strategy_session_end=True,
            ),
        )
        respected = engine.run_backtest(
            rows,
            strategy,
            "TEST",
            engine.BacktestSettings(
                spread_bps=0,
                slippage_bps=0,
                max_concurrent_positions=1,
                ignore_strategy_session_end=False,
            ),
        )
        self.assertGreater(ignored["metrics"]["trade_count"], 0)
        self.assertEqual(respected["metrics"]["trade_count"], 0)

    def test_price_band_can_unlock_momentum_continuation_above_max(self):
        rows = [
            bar(18, 0, 19.0, 19.2, 18.9, 19.0),
            bar(18, 1, 21.0, 21.2, 20.8, 21.0),
            bar(18, 2, 21.2, 21.4, 21.0, 21.2),
        ]
        strategy = simple_strategy(min_price=1.5, max_price=20, session_start="09:31")
        unlocked = engine.run_backtest(
            rows,
            strategy,
            "TEST",
            engine.BacktestSettings(
                spread_bps=0,
                slippage_bps=0,
                max_concurrent_positions=1,
                allow_price_extension_after_qualification=True,
            ),
        )
        locked = engine.run_backtest(
            rows,
            strategy,
            "TEST",
            engine.BacktestSettings(
                spread_bps=0,
                slippage_bps=0,
                max_concurrent_positions=1,
                allow_price_extension_after_qualification=False,
            ),
        )
        self.assertEqual(unlocked["metrics"]["trade_count"], 1)
        self.assertEqual(locked["metrics"]["trade_count"], 0)

    def test_pullback_strategy_requires_pullback_then_breakout(self):
        strategy = simple_strategy()
        strategy["name"] = "Micro Pullback / Bull Flag Test"
        rows = [
            bar(18, 0, 10.0, 10.2, 9.9, 10.0),
            bar(18, 1, 10.0, 10.1, 9.7, 9.8),
            bar(18, 2, 9.8, 9.9, 9.6, 9.7),
            bar(18, 3, 9.7, 10.6, 9.7, 10.5),
            bar(18, 4, 10.5, 10.7, 10.4, 10.6),
        ]
        result = engine.run_backtest(
            rows,
            strategy,
            "TEST",
            engine.BacktestSettings(
                spread_bps=0,
                slippage_bps=0,
                max_concurrent_positions=1,
                require_pullback_breakout_for_pullback_strategies=True,
            ),
        )
        self.assertEqual(result["metrics"]["trade_count"], 1)
        entry_time = datetime.fromisoformat(result["trades"][0]["entry_time"].replace("Z", "+00:00")).astimezone(ET)
        self.assertEqual((entry_time.hour, entry_time.minute), (9, 34))

    def test_optimizer_execution_variants_cover_entry_behavior_choices(self):
        settings = engine.BacktestSettings(
            starting_cash=10_000,
            risk_per_trade_pct=5,
            max_position_pct=80,
            spread_bps=0,
            slippage_bps=0,
            max_concurrent_positions=4,
            allow_extended_hours=True,
            extended_hours_position_scale=0.25,
            ignore_strategy_session_end=True,
            allow_price_extension_after_qualification=True,
            require_pullback_breakout_for_pullback_strategies=True,
        )
        variants = engine.generate_execution_variants(settings, maximum=8)
        self.assertTrue({1, 2, 3, 4}.issubset({item.max_concurrent_positions for item in variants}))
        self.assertEqual({False, True}, {item.allow_extended_hours for item in variants})
        self.assertTrue({0.15, 0.25, 0.50}.issubset({
            round(item.extended_hours_position_scale, 2)
            for item in variants
            if item.allow_extended_hours
        }))
        self.assertEqual({False, True}, {item.ignore_strategy_session_end for item in variants})
        self.assertEqual({False, True}, {item.allow_price_extension_after_qualification for item in variants})
        self.assertEqual(
            {False, True},
            {item.require_pullback_breakout_for_pullback_strategies for item in variants},
        )

    def test_legacy_behavior_settings_restore_original_entry_engine(self):
        settings = engine.BacktestSettings(
            max_concurrent_positions=4,
            allow_extended_hours=True,
            extended_hours_position_scale=0.5,
            ignore_strategy_session_end=True,
            allow_price_extension_after_qualification=True,
            require_pullback_breakout_for_pullback_strategies=True,
        )
        legacy = engine.legacy_behavior_settings(settings)
        self.assertEqual(legacy.max_concurrent_positions, 1)
        self.assertFalse(legacy.allow_extended_hours)
        self.assertFalse(legacy.ignore_strategy_session_end)
        self.assertFalse(legacy.allow_price_extension_after_qualification)
        self.assertFalse(legacy.require_pullback_breakout_for_pullback_strategies)

    def test_settings_reject_invalid_risk(self):
        with self.assertRaises(engine.AppError):
            engine.BacktestSettings(risk_per_trade_pct=0).validate()

    def test_limitations_disclose_catalyst_and_spread(self):
        warnings = engine.backtest_limitations(simple_strategy(catalyst_required=True, max_spread_pct=0.5))
        self.assertTrue(any("news" in warning.lower() for warning in warnings))
        self.assertTrue(any("spread" in warning.lower() for warning in warnings))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = engine.StrategyStore(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_video_analysis_keeps_existing_approval(self):
        url = "https://www.youtube.com/watch?v=abcDEF12_-3"
        strategy = simple_strategy()
        analysis = {"url": url, "video_title": "Test video", "strategies": [strategy]}
        self.store.add_video_analysis(analysis)
        self.store.update_strategy(strategy["id"], {"approved": True})
        self.store.add_video_analysis(analysis)
        self.assertTrue(self.store.load()["strategies"][0]["approved"])
        self.assertEqual(len(self.store.load()["videos"]), 1)

    def test_paper_position_can_be_edited_closed_and_deleted(self):
        saved = self.store.add_position({"symbol": "NVDA", "entry_price": 100, "quantity": 4})
        position_id = saved["paper_positions"][0]["id"]
        self.store.update_position(position_id, {"quantity": 5, "entry_price": 99})
        closed = self.store.update_position(position_id, {"status": "closed", "exit_price": 103})
        self.assertEqual(closed["paper_positions"][0]["realized_pnl"], 20)
        self.store.delete_position(position_id)
        self.assertEqual(self.store.load()["paper_positions"], [])

    def test_invalid_position_is_rejected(self):
        with self.assertRaises(engine.AppError):
            self.store.add_position({"symbol": "NVDA", "entry_price": 0, "quantity": 5})

    def test_import_merges_instead_of_erasing_positions(self):
        self.store.add_position({"symbol": "AAPL", "entry_price": 10, "quantity": 2})
        result = self.store.import_data(json.dumps({"strategies": [simple_strategy()], "videos": [], "paper_positions": []}))
        self.assertEqual(len(result["paper_positions"]), 1)
        self.assertEqual(len(result["strategies"]), 1)

    def test_valid_json_without_a_strategies_list_returns_an_app_error(self):
        for payload in ("{}", "[]", '{"strategies": null}'):
            with self.subTest(payload=payload), self.assertRaisesRegex(
                engine.AppError, "must contain a strategies list"
            ):
                self.store.import_data(payload)


class DurableStorageTests(unittest.TestCase):
    def test_load_latest_restores_cloud_after_local_directory_disappears(self):
        class FakeCloud:
            repository = "owner/private-backups"
            path = "trading-intelligence-lab/intelligence_library.json"

            def __init__(self):
                self.library = None

            def read_library(self):
                if self.library is None:
                    return None
                return {"library": json.loads(json.dumps(self.library)), "sha": "a" * 40}

            def save_library(self, data, *, previous_updated_at=None):
                self.library = json.loads(json.dumps(data))
                return {"library": self.library, "sha": "b" * 40}

        cloud = FakeCloud()
        with tempfile.TemporaryDirectory() as first_directory:
            first = engine.StrategyStore(first_directory, cloud_backup=cloud)
            first.save(
                {
                    "strategies": [],
                    "knowledge_sources": [
                        {"id": "book1", "title": "Saved Book", "analysis_stage": "complete"}
                    ],
                }
            )
            self.assertEqual(first.load_latest()["knowledge_sources"][0]["title"], "Saved Book")

        with tempfile.TemporaryDirectory() as restarted_directory:
            restarted = engine.StrategyStore(restarted_directory, cloud_backup=cloud)
            restored = restarted.load_latest()
            self.assertEqual(restored["knowledge_sources"][0]["title"], "Saved Book")
            self.assertTrue(restarted.path.exists())

    def test_persistence_status_reports_cloud_durability(self):
        class FakeCloud:
            repository = "owner/private-backups"
            path = "trading-intelligence-lab/intelligence_library.json"

            def read_library(self):
                return None

        with tempfile.TemporaryDirectory() as directory:
            store = engine.StrategyStore(directory, cloud_backup=FakeCloud())
            status = store.persistence_status()
            self.assertTrue(status["durable"])
            self.assertEqual(status["repository"], "owner/private-backups")


class CloudReconciliationTests(unittest.TestCase):
    def test_newer_local_copy_is_kept_and_cloud_revision_is_remembered(self):
        class FakeCloud:
            repository = "owner/private-backups"
            path = "trading-intelligence-lab/intelligence_library.json"

            def read_library(self):
                return {
                    "library": {
                        "version": 2,
                        "strategies": [],
                        "knowledge_sources": [{"id": "remote", "title": "Remote"}],
                        "updated_at": "2026-08-27T17:00:00Z",
                    },
                    "sha": "a" * 40,
                }

        with tempfile.TemporaryDirectory() as directory:
            store = engine.StrategyStore(directory, cloud_backup=FakeCloud())
            store._write_local(
                {
                    "version": 2,
                    "strategies": [],
                    "knowledge_sources": [{"id": "local", "title": "Local"}],
                    "updated_at": "2026-08-27T17:10:00Z",
                },
                make_backup=False,
            )
            store._record_cloud_status(
                synced_updated_at="2026-08-27T17:00:00Z",
                last_synced_at="2026-08-27T17:00:00Z",
            )
            loaded = store.load_latest()
            self.assertEqual(loaded["knowledge_sources"][0]["title"], "Local")
            self.assertEqual(
                store.cloud_status()["synced_updated_at"],
                "2026-08-27T17:00:00Z",
            )

    def test_local_only_book_data_can_migrate_into_empty_initialized_cloud(self):
        class FakeCloud:
            repository = "owner/private-backups"
            path = "trading-intelligence-lab/intelligence_library.json"

            def read_library(self):
                return {
                    "library": {
                        "version": 2,
                        "strategies": [],
                        "knowledge_sources": [],
                        "research_runs": [],
                        "validation_runs": [],
                        "updated_at": "2026-08-27T17:15:00Z",
                    },
                    "sha": "a" * 40,
                }

        with tempfile.TemporaryDirectory() as directory:
            store = engine.StrategyStore(directory, cloud_backup=FakeCloud())
            store._write_local(
                {
                    "version": 2,
                    "strategies": [],
                    "knowledge_sources": [{"id": "book1", "title": "Recovered local book"}],
                    "updated_at": "2026-08-27T17:20:00Z",
                },
                make_backup=False,
            )
            loaded = store.load_latest()
            self.assertEqual(
                loaded["knowledge_sources"][0]["title"],
                "Recovered local book",
            )
            self.assertEqual(
                store.cloud_status()["synced_updated_at"],
                "2026-08-27T17:15:00Z",
            )


    def test_both_sides_changed_since_shared_version_raises_conflict(self):
        class FakeCloud:
            repository = "owner/private-backups"
            path = "trading-intelligence-lab/intelligence_library.json"

            def read_library(self):
                return {
                    "library": {
                        "version": 2,
                        "strategies": [],
                        "knowledge_sources": [{"id": "remote-new"}],
                        "updated_at": "2026-08-27T17:20:00Z",
                    },
                    "sha": "a" * 40,
                }

        with tempfile.TemporaryDirectory() as directory:
            store = engine.StrategyStore(directory, cloud_backup=FakeCloud())
            store._write_local(
                {
                    "version": 2,
                    "strategies": [],
                    "knowledge_sources": [{"id": "local-new"}],
                    "updated_at": "2026-08-27T17:10:00Z",
                },
                make_backup=False,
            )
            store._record_cloud_status(
                synced_updated_at="2026-08-27T17:00:00Z",
                last_synced_at="2026-08-27T17:00:00Z",
            )
            with self.assertRaises(engine.AppError):
                store.load_latest()


    def test_same_timestamp_different_data_is_not_silently_overwritten(self):
        class FakeCloud:
            repository = "owner/private-backups"
            path = "trading-intelligence-lab/intelligence_library.json"

            def read_library(self):
                return {
                    "library": {
                        "version": 2,
                        "strategies": [],
                        "knowledge_sources": [{"id": "remote"}],
                        "updated_at": "2026-08-27T17:00:00Z",
                    },
                    "sha": "a" * 40,
                }

        with tempfile.TemporaryDirectory() as directory:
            store = engine.StrategyStore(directory, cloud_backup=FakeCloud())
            store._write_local(
                {
                    "version": 2,
                    "strategies": [],
                    "knowledge_sources": [{"id": "local"}],
                    "updated_at": "2026-08-27T17:00:00Z",
                },
                make_backup=False,
            )
            with self.assertRaises(engine.AppError):
                store.load_latest()

    def test_successful_read_does_not_hide_previous_cloud_write_error(self):
        class FakeCloud:
            repository = "owner/private-backups"
            path = "trading-intelligence-lab/intelligence_library.json"

            def read_library(self):
                return {
                    "library": {
                        "version": 2,
                        "strategies": [],
                        "updated_at": "2026-08-27T17:00:00Z",
                    },
                    "sha": "a" * 40,
                }

        with tempfile.TemporaryDirectory() as directory:
            store = engine.StrategyStore(directory, cloud_backup=FakeCloud())
            store._record_cloud_status(last_error="Saved locally, but permanent cloud backup failed")
            status = store.persistence_status(verify=True)
            self.assertTrue(status["verified"])
            self.assertFalse(status["healthy"])
            self.assertIn("permanent cloud backup failed", status["last_error"])


class CloudWriteVerificationTests(unittest.TestCase):
    def test_verify_cloud_write_access_forces_real_write_attempt(self):
        class FakeCloud:
            repository = "owner/private-backups"
            path = "trading-intelligence-lab/intelligence_library.json"

            def __init__(self):
                self.library = {
                    "version": 2,
                    "strategies": [],
                    "updated_at": "2026-08-27T17:00:00Z",
                }
                self.force_write_seen = False

            def read_library(self):
                return {
                    "library": json.loads(json.dumps(self.library)),
                    "sha": "a" * 40,
                }

            def save_library(self, data, *, previous_updated_at=None, force_write=False):
                self.force_write_seen = force_write
                self.library = json.loads(json.dumps(data))
                return {"library": self.library, "sha": "b" * 40}

        with tempfile.TemporaryDirectory() as directory:
            cloud = FakeCloud()
            store = engine.StrategyStore(directory, cloud_backup=cloud)
            store._write_local(cloud.library, make_backup=False)
            store._record_cloud_success(cloud.library)

            store.verify_cloud_write_access()

            self.assertTrue(cloud.force_write_seen)
            status = store.persistence_status(verify=True)
            self.assertTrue(status["write_verified"])
            self.assertTrue(status["healthy"])

    def test_write_verification_uses_remote_snapshot_not_stale_local_copy(self):
        class FakeCloud:
            repository = "owner/private-backups"
            path = "trading-intelligence-lab/intelligence_library.json"

            def __init__(self):
                self.library = {
                    "version": 2,
                    "strategies": [],
                    "knowledge_sources": [],
                    "updated_at": "2026-08-27T17:15:00Z",
                }
                self.saved = None
                self.previous = None
                self.force_write_seen = False

            def read_library(self):
                return {
                    "library": json.loads(json.dumps(self.library)),
                    "sha": "a" * 40,
                }

            def save_library(self, data, *, previous_updated_at=None, force_write=False):
                self.saved = json.loads(json.dumps(data))
                self.previous = previous_updated_at
                self.force_write_seen = force_write
                return {"library": self.saved, "sha": "b" * 40}

        with tempfile.TemporaryDirectory() as directory:
            cloud = FakeCloud()
            store = engine.StrategyStore(directory, cloud_backup=cloud)
            store._write_local(
                {
                    "version": 2,
                    "strategies": [],
                    "knowledge_sources": [{"id": "stale-local", "title": "Unsynced local"}],
                    "updated_at": "2026-08-27T17:30:00Z",
                },
                make_backup=False,
            )

            result = store.verify_cloud_write_access()

            self.assertTrue(cloud.force_write_seen)
            self.assertEqual(cloud.previous, "2026-08-27T17:15:00Z")
            expected_remote = engine.StrategyStore.normalize_library(cloud.library)
            self.assertEqual(cloud.saved, expected_remote)
            self.assertEqual(result, expected_remote)
            status = store.persistence_status(verify=True)
            self.assertTrue(status["write_verified"])
            self.assertTrue(status["healthy"])


    def test_successful_store_save_marks_write_verified_and_healthy(self):
        class FakeCloud:
            repository = "owner/private-backups"
            path = "trading-intelligence-lab/intelligence_library.json"

            def __init__(self):
                self.library = {
                    "version": 2,
                    "strategies": [],
                    "updated_at": "2026-08-27T17:00:00Z",
                }

            def read_library(self):
                return {
                    "library": json.loads(json.dumps(self.library)),
                    "sha": "a" * 40,
                }

            def save_library(self, data, *, previous_updated_at=None):
                self.library = json.loads(json.dumps(data))
                return {"library": self.library, "sha": "b" * 40}

        with tempfile.TemporaryDirectory() as directory:
            cloud = FakeCloud()
            store = engine.StrategyStore(directory, cloud_backup=cloud)
            store._write_local(cloud.library, make_backup=False)
            store._record_cloud_success(cloud.library)
            before = store.persistence_status(verify=True)
            self.assertFalse(before["write_verified"])
            self.assertFalse(before["healthy"])

            store.save(store.load_latest())
            after = store.persistence_status(verify=True)
            self.assertTrue(after["write_verified"])
            self.assertTrue(after["healthy"])
            self.assertIsNotNone(after["last_write_at"])


class CloudDestinationBindingTests(unittest.TestCase):
    def test_old_write_verification_is_ignored_after_backup_destination_changes(self):
        class FakeCloud:
            repository = "owner/new-private-backups"
            path = "trading-intelligence-lab/intelligence_library.json"

            def read_library(self):
                return {
                    "library": {
                        "version": 2,
                        "strategies": [],
                        "updated_at": "2026-08-27T17:00:00Z",
                    },
                    "sha": "a" * 40,
                }

        with tempfile.TemporaryDirectory() as directory:
            store = engine.StrategyStore(directory, cloud_backup=FakeCloud())
            store.cloud_status_path.write_text(
                json.dumps(
                    {
                        "repository": "owner/old-private-backups",
                        "path": "old/path.json",
                        "last_write_at": "2026-08-27T16:00:00Z",
                        "last_synced_at": "2026-08-27T16:00:00Z",
                        "synced_updated_at": "2026-08-27T16:00:00Z",
                        "last_error": None,
                    }
                ),
                encoding="utf-8",
            )
            status = store.persistence_status(verify=True)
            self.assertFalse(status["write_verified"])
            self.assertFalse(status["healthy"])


class CloudBackupFirstWriteTests(unittest.TestCase):
    def test_large_library_git_push_round_trip_against_local_bare_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            remote = root / "remote.git"
            source.mkdir()

            def git(*arguments, cwd=None):
                return engine.subprocess.run(
                    ["git", *arguments],
                    cwd=str(cwd) if cwd else None,
                    capture_output=True,
                    check=True,
                    text=True,
                ).stdout

            git("init", "-b", "main", cwd=source)
            git("config", "user.name", "Test", cwd=source)
            git("config", "user.email", "test@example.com", cwd=source)
            path = "trading-intelligence-lab/intelligence_library.json"
            target = source / path
            target.parent.mkdir(parents=True)
            old_data = {"strategies": [], "updated_at": "2026-08-28T23:40:00Z"}
            target.write_text(json.dumps(old_data, indent=2), encoding="utf-8")
            git("add", "--", path, cwd=source)
            git("commit", "-m", "Initial backup", cwd=source)
            current_sha = git("hash-object", "--", path, cwd=source).strip()
            git("clone", "--bare", str(source), str(remote))

            cloud = engine.GitHubCloudBackup(
                "owner/private-backups",
                "token",
                branch="main",
                path=path,
            )
            new_data = {
                "strategies": [],
                "updated_at": "2026-08-29T00:30:00Z",
                "large": "x" * (engine.GITHUB_CONTENTS_API_SAFE_BYTES + 1),
            }
            current = {"library": old_data, "sha": current_sha}
            with patch.object(cloud, "read_library", return_value=current), patch.object(
                cloud,
                "_git_clone_url",
                return_value=remote.as_uri(),
            ):
                saved = cloud.save_library(
                    new_data,
                    previous_updated_at=old_data["updated_at"],
                )

            expected = json.dumps(new_data, indent=2, default=str, allow_nan=False)
            self.assertEqual(git("show", f"main:{path}", cwd=remote), expected)
            self.assertEqual(saved["sha"], git("rev-parse", f"main:{path}", cwd=remote).strip())

    def test_large_library_uses_shallow_non_forced_git_push(self):
        cloud = engine.GitHubCloudBackup(
            "owner/private-backups",
            "token",
            branch="main",
            path="trading-intelligence-lab/intelligence_library.json",
        )
        current_sha = "a" * 40
        blob_sha = "d" * 40
        data = {
            "strategies": [],
            "updated_at": "2026-08-28T23:50:00Z",
            "large": "x" * (engine.GITHUB_CONTENTS_API_SAFE_BYTES + 1),
        }
        current = {
            "library": {"strategies": [], "updated_at": "2026-08-28T23:40:00Z"},
            "sha": current_sha,
        }

        observed = {"commands": [], "saved": b""}

        def git_run(command, **kwargs):
            observed["commands"].append(command)
            if command[1] == "clone":
                checkout = Path(command[-1])
                target = checkout / cloud.path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("old", encoding="utf-8")
                return engine.subprocess.CompletedProcess(command, 0, "", "")
            if command[1:3] == ["hash-object", "--"]:
                prior_hashes = sum(1 for item in observed["commands"] if item[1:3] == ["hash-object", "--"])
                value = current_sha if prior_hashes == 1 else blob_sha
                return engine.subprocess.CompletedProcess(command, 0, value + "\n", "")
            if command[1] == "add":
                observed["saved"] = (Path(kwargs["cwd"]) / cloud.path).read_bytes()
            return engine.subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(cloud, "read_library", return_value=current), patch.object(
            engine.subprocess,
            "run",
            side_effect=git_run,
        ):
            saved = cloud.save_library(
                data,
                previous_updated_at="2026-08-28T23:40:00Z",
            )

        self.assertEqual(saved["sha"], blob_sha)
        self.assertEqual(observed["saved"], json.dumps(data, indent=2, default=str, allow_nan=False).encode("utf-8"))
        push = next(command for command in observed["commands"] if command[1] == "push")
        self.assertEqual(push, ["git", "push", "origin", "HEAD:refs/heads/main"])

    def test_large_library_stops_if_blob_changed_before_commit(self):
        cloud = engine.GitHubCloudBackup(
            "owner/private-backups",
            "token",
            branch="main",
            path="trading-intelligence-lab/intelligence_library.json",
        )
        current = {
            "library": {"strategies": [], "updated_at": "2026-08-28T23:40:00Z"},
            "sha": "a" * 40,
        }
        data = {
            "strategies": [],
            "updated_at": "2026-08-28T23:50:00Z",
            "large": "x" * (engine.GITHUB_CONTENTS_API_SAFE_BYTES + 1),
        }
        def git_run(command, **kwargs):
            if command[1] == "clone":
                checkout = Path(command[-1])
                target = checkout / cloud.path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("changed", encoding="utf-8")
                return engine.subprocess.CompletedProcess(command, 0, "", "")
            if command[1:3] == ["hash-object", "--"]:
                return engine.subprocess.CompletedProcess(command, 0, "9" * 40 + "\n", "")
            return engine.subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(cloud, "read_library", return_value=current), patch.object(
            engine.subprocess,
            "run",
            side_effect=git_run,
        ):
            with self.assertRaisesRegex(engine.AppError, "changed while"):
                cloud.save_library(
                    data,
                    previous_updated_at="2026-08-28T23:40:00Z",
                )

    def test_force_write_does_not_short_circuit_identical_cloud_content(self):
        cloud = engine.GitHubCloudBackup(
            "owner/private-backups",
            "token",
            branch="main",
            path="trading-intelligence-lab/intelligence_library.json",
        )
        data = {"strategies": [], "updated_at": "2026-08-27T17:00:00Z"}
        current = {"library": dict(data), "sha": "a" * 40}
        with patch.object(cloud, "read_library", return_value=current), patch.object(
            cloud,
            "_request",
            return_value={"content": {"sha": "b" * 40}},
        ) as request:
            cloud.save_library(
                data,
                previous_updated_at="2026-08-27T17:00:00Z",
                force_write=True,
            )

        self.assertEqual(request.call_args.kwargs["method"], "PUT")

    def test_first_write_to_initialized_blank_cloud_library_is_allowed(self):
        cloud = engine.GitHubCloudBackup(
            "owner/private-backups",
            "token",
            branch="main",
            path="trading-intelligence-lab/intelligence_library.json",
        )
        blank_remote = {
            "library": {"strategies": [], "updated_at": None},
            "sha": "a" * 40,
        }
        new_data = {"strategies": [], "updated_at": "2026-08-27T17:20:00Z"}
        with patch.object(cloud, "read_library", return_value=blank_remote), patch.object(
            cloud,
            "_request",
            return_value={"content": {"sha": "b" * 40}},
        ) as request:
            saved = cloud.save_library(new_data, previous_updated_at=None)

        self.assertEqual(saved["library"], new_data)
        self.assertEqual(request.call_args.kwargs["method"], "PUT")

    def test_missing_sync_token_cannot_overwrite_dated_cloud_library(self):
        cloud = engine.GitHubCloudBackup(
            "owner/private-backups",
            "token",
            branch="main",
            path="trading-intelligence-lab/intelligence_library.json",
        )
        remote = {
            "library": {"strategies": [], "updated_at": "2026-08-27T17:00:00Z"},
            "sha": "a" * 40,
        }
        with patch.object(cloud, "read_library", return_value=remote):
            with self.assertRaises(engine.AppError):
                cloud.save_library(
                    {"strategies": [], "updated_at": "2026-08-27T17:20:00Z"},
                    previous_updated_at=None,
                )


class ResearchEquitySymbolTests(unittest.TestCase):
    def test_identifier_like_inactive_asset_is_not_a_research_ticker(self):
        self.assertFalse(engine.is_research_equity_symbol("D012219"))
        self.assertTrue(engine.is_research_equity_symbol("AAPL"))
        self.assertTrue(engine.is_research_equity_symbol("BRK.B"))


class ProviderTests(unittest.TestCase):
    def test_interaction_parser_uses_current_steps_schema(self):
        response = {"steps": [{"type": "thought"}, {"type": "model_output", "content": [{"type": "text", "text": '{"ok":true}'}]}]}
        self.assertEqual(engine._extract_interaction_text(response), '{"ok":true}')

    def test_gemini_receives_real_video_input_not_plain_text_link(self):
        analysis = {
            "video_title": "Trading lesson",
            "creator": "Teacher",
            "summary": "A VWAP reclaim.",
            "visual_observations": ["The chart crosses VWAP."],
            "general_risk_warnings": [],
            "strategies": [
                {
                    "name": "VWAP reclaim",
                    "category": "VWAP",
                    "direction": "long",
                    "summary": "Reclaim VWAP.",
                    "entry_conditions": ["Reclaim VWAP"],
                    "exit_conditions": [],
                    "risk_rules": [],
                    "avoid_conditions": [],
                    "unresolved_rules": [],
                    "confidence": 85,
                    "machine_rules": {"vwap_reclaim": True},
                    "evidence": [],
                }
            ],
        }
        response = {"id": "int_123", "steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps(analysis)}]}]}
        with patch.object(engine, "_json_request", return_value=response) as mocked:
            parsed = engine.GeminiVideoAnalyzer("secret").analyze("https://youtu.be/abcDEF12_-3")
        payload = mocked.call_args.kwargs["payload"]
        self.assertEqual(payload["input"][0]["type"], "video")
        self.assertEqual(payload["input"][0]["uri"], "https://www.youtube.com/watch?v=abcDEF12_-3")
        self.assertFalse(payload["store"])
        self.assertTrue(parsed["strategies"][0]["machine_rules"]["vwap_reclaim"])

    def test_gemini_503_high_demand_is_treated_as_transient(self):
        error = engine.AppError(
            "Provider request failed (503): This model is currently experiencing high demand. Please try again later."
        )
        self.assertTrue(engine.provider_temporarily_unavailable(error))
        self.assertFalse(engine.provider_quota_reached(error))

    def test_gemini_overload_retries_then_switches_to_backup_model(self):
        analyzer = engine.GeminiVideoAnalyzer(
            "secret",
            model="gemini-3.7-flash",
            fallback_model="gemini-3.6-flash",
        )
        overload = engine.AppError(
            "Provider request failed (503): This model is currently experiencing high demand. Please try again later."
        )
        with (
            patch.object(
                analyzer,
                "_analyze_whole_video",
                side_effect=[overload, overload, overload, {"video_title": "Recovered"}],
            ) as mocked,
            patch.object(engine, "sleep") as mocked_sleep,
        ):
            result = analyzer._analyze_whole_video_with_transient_retries(
                "https://www.youtube.com/watch?v=abcDEF12_-3",
                "prompt",
                None,
            )
        self.assertEqual(result["video_title"], "Recovered")
        self.assertEqual(mocked.call_count, 4)
        self.assertEqual(mocked_sleep.call_count, 2)
        self.assertTrue(analyzer.model_fallback_used)
        self.assertEqual(analyzer.model, "gemini-3.6-flash")

    def test_gemini_overload_can_advance_through_supported_backup_models(self):
        analyzer = engine.GeminiVideoAnalyzer(
            "secret",
            model="gemini-3.7-flash",
            fallback_model="gemini-3.6-flash",
        )
        overload = engine.AppError(
            "Provider request failed (503): This model is currently experiencing high demand. Please try again later."
        )
        self.assertTrue(analyzer._activate_model_fallback(overload))
        self.assertEqual(analyzer.model, "gemini-3.6-flash")
        self.assertTrue(analyzer._activate_model_fallback(overload))
        self.assertEqual(analyzer.model, "gemini-3.5-flash")
        self.assertFalse(analyzer._activate_model_fallback(overload))
        self.assertNotIn("gemini-2.5-flash", analyzer.fallback_models)

    def test_retired_gemini_model_is_detected_and_skipped_immediately(self):
        analyzer = engine.GeminiVideoAnalyzer(
            "secret",
            model="gemini-3.6-flash",
            fallback_model="gemini-3.5-flash",
        )
        retired = engine.AppError(
            "Provider request failed (404): This model models/gemini-3.6-flash is no longer available "
            "to new users. Please update your code to use models/gemini-3.5-flash."
        )
        self.assertTrue(engine.provider_model_unavailable(retired))
        self.assertTrue(analyzer._activate_model_fallback(retired))
        self.assertEqual(analyzer.model, "gemini-3.5-flash")

    def test_whole_video_retired_model_switches_without_sleeping(self):
        analyzer = engine.GeminiVideoAnalyzer(
            "secret",
            model="gemini-3.6-flash",
            fallback_model="gemini-3.5-flash",
        )
        retired = engine.AppError(
            "Provider request failed (404): This model models/gemini-3.6-flash is no longer available "
            "to new users. Please update your code to use models/gemini-3.5-flash."
        )
        with (
            patch.object(
                analyzer,
                "_analyze_whole_video",
                side_effect=[retired, {"video_title": "Recovered"}],
            ) as mocked,
            patch.object(engine, "sleep") as mocked_sleep,
        ):
            result = analyzer._analyze_whole_video_with_transient_retries(
                "https://www.youtube.com/watch?v=abcDEF12_-3",
                "prompt",
                None,
            )
        self.assertEqual(result["video_title"], "Recovered")
        self.assertEqual(mocked.call_count, 2)
        mocked_sleep.assert_not_called()
        self.assertEqual(analyzer.model, "gemini-3.5-flash")

    def test_alpaca_pagination_is_followed(self):
        market = engine.AlpacaMarketData("key", "secret")
        responses = [
            {"bars": {"NVDA": [bar(18, 0, 10, 11, 9, 10)]}, "next_page_token": "page-two"},
            {"bars": {"AAPL": [bar(18, 0, 20, 21, 19, 20)]}, "next_page_token": None},
        ]
        with patch.object(market, "_get", side_effect=responses):
            result = market.bars(["NVDA", "AAPL"], start=engine.utc_now() - timedelta(days=5), end=engine.utc_now())
        self.assertEqual(len(result["NVDA"]), 1)
        self.assertEqual(len(result["AAPL"]), 1)

    def test_snapshot_handles_zero_vwap_without_claiming_above(self):
        metrics = engine.snapshot_metrics("NVDA", {"latestTrade": {"p": 100}, "dailyBar": {"v": 1000}, "prevDailyBar": {"c": 95}})
        self.assertFalse(metrics["above_vwap"])
        self.assertIsNone(metrics["vwap"])

    def test_unknown_chart_trigger_is_never_reported_as_match(self):
        metrics = {"symbol": "NVDA", "price": 100, "above_vwap": True, "vwap": 98}
        result = engine.match_strategy(metrics, simple_strategy(above_vwap=True, breakout_lookback_bars=5))
        self.assertEqual(result["status"], "VERIFY")
        self.assertGreater(result["unknown"], 0)

    def test_matching_all_measurable_rules_reports_match(self):
        metrics = {"symbol": "NVDA", "price": 100, "above_vwap": True, "vwap": 98}
        result = engine.match_strategy(metrics, simple_strategy(above_vwap=True))
        self.assertEqual(result["status"], "MATCH")

    def test_confirmed_chart_trigger_can_be_matched_automatically(self):
        rows = [
            bar(18, 0, 9, 10, 8, 9),
            bar(18, 1, 9, 11, 8, 10),
            bar(18, 2, 10, 13, 9, 12),
        ]
        strategy = simple_strategy(breakout_lookback_bars=2)
        observed = engine.chart_trigger_checks(rows, strategy)
        self.assertTrue(observed["breakout_lookback_bars"])
        result = engine.match_strategy({"price": 12, "chart_checks": observed}, strategy)
        self.assertEqual(result["status"], "MATCH")

    def test_failed_chart_trigger_is_never_treated_as_verified(self):
        metrics = {"price": 100, "chart_checks": {"breakout_lookback_bars": False}}
        result = engine.match_strategy(metrics, simple_strategy(breakout_lookback_bars=3))
        self.assertNotEqual(result["status"], "MATCH")
        self.assertGreater(result["failed"], 0)

    def test_app_source_parses(self):
        source = Path(__file__).with_name("youtube_strategy_app.py").read_text(encoding="utf-8")
        ast.parse(source)


class FakePanel:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def markdown(self, *args, **kwargs):
        return None

    def button(self, *args, **kwargs):
        return False

    def form_submit_button(self, *args, **kwargs):
        return False

    def text_input(self, label, value="", **kwargs):
        return value

    def number_input(self, label, *args, **kwargs):
        if "value" in kwargs:
            return kwargs["value"]
        return args[2] if len(args) >= 3 else 0

    def slider(self, label, *args, **kwargs):
        if "value" in kwargs:
            return kwargs["value"]
        return args[2] if len(args) >= 3 else 0

    def checkbox(self, label, value=False, **kwargs):
        return value

    def selectbox(self, label, options, index=0, **kwargs):
        return list(options)[index]

    def radio(self, label, options, index=0, **kwargs):
        return list(options)[index]

    def metric(self, *args, **kwargs):
        return None

    def caption(self, *args, **kwargs):
        return None


class FakeStreamlit(types.ModuleType):
    def __init__(self, session_state=None):
        super().__init__("streamlit")
        self.session_state = session_state or {}
        self.secrets = {
            "ALPACA_API_KEY": "fake-alpaca-key",
            "ALPACA_SECRET_KEY": "fake-alpaca-secret",
            "GEMINI_API_KEY": "fake-gemini-key",
            "APP_ACCESS_PASSWORD": "test-access-password",
        }
        self.sidebar = FakePanel()
        self.rendered = []

    def __getattr__(self, name):
        if name in {"set_page_config", "markdown", "success", "warning", "caption", "divider", "code", "info", "error", "dataframe", "write", "line_chart"}:
            return lambda *args, **kwargs: self.rendered.append((name, args))
        if name in {"button", "download_button", "form_submit_button"}:
            return lambda *args, **kwargs: False
        if name == "file_uploader":
            return lambda *args, **kwargs: None
        if name == "empty":
            return lambda *args, **kwargs: FakePanel()
        if name == "tabs":
            return lambda labels: [FakePanel() for _ in labels]
        if name == "columns":
            return lambda count, **kwargs: [FakePanel() for _ in range(count if isinstance(count, int) else len(count))]
        if name in {"form", "expander", "spinner"}:
            return lambda *args, **kwargs: FakePanel()
        if name in {"selectbox", "radio"}:
            return lambda label, options, index=0, **kwargs: list(options)[index]
        if name == "metric":
            return lambda *args, **kwargs: self.rendered.append((name, args))
        if name == "checkbox":
            return lambda label, value=False, **kwargs: value
        if name in {"number_input", "slider"}:
            return lambda label, *args, **kwargs: (
                kwargs["value"] if "value" in kwargs else (args[2] if len(args) >= 3 else 0)
            )
        if name in {"text_input", "text_area"}:
            return lambda label, value="", **kwargs: value
        if name == "rerun":
            return lambda *args, **kwargs: None
        if name == "stop":
            return lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("The app unexpectedly stopped."))
        raise AttributeError(name)


class StreamlitSmokeTests(unittest.TestCase):
    def test_complete_dashboard_renders_saved_strategy_backtest_scan_and_positions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = engine.StrategyStore(directory)
            strategy = engine.demo_strategy()
            strategy["approved"] = True
            strategy["source_url"] = "https://www.youtube.com/watch?v=abcDEF12_-3"
            strategy["evidence"] = [{"timestamp": "01:15", "description": "VWAP example", "visual_evidence": "Chart above VWAP", "spoken_evidence": "Wait for confirmation"}]
            store.save(
                {
                    "videos": [{"url": strategy["source_url"], "video_title": "Example", "creator": "Teacher", "visual_observations": ["Visible chart"]}],
                    "strategies": [strategy],
                    "paper_positions": [{"id": "paper1", "symbol": "NVDA", "quantity": 2, "entry_price": 100, "status": "open"}],
                }
            )
            result = engine.run_backtest(
                [bar(18, 0, 100, 101, 99, 100), bar(18, 1, 100, 101, 99, 100), bar(18, 2, 100, 106, 99, 105)],
                simple_strategy(),
                "NVDA",
            )
            snapshot = engine.snapshot_metrics(
                "NVDA",
                {"latestTrade": {"p": 100}, "latestQuote": {"bp": 99.9, "ap": 100.1}, "dailyBar": {"v": 10000, "vw": 98, "h": 102}, "prevDailyBar": {"c": 95}},
            )
            match = engine.match_strategy(snapshot, simple_strategy(above_vwap=True))
            fake_streamlit = FakeStreamlit(
                {
                    "backtest_results": [result],
                    "live_scan": [{"metrics": snapshot, "matches": [match], "best": match}],
                    "paper_live_prices": {"NVDA": 103},
                    "_trading_app_access_granted": True,
                }
            )
            app_path = Path(__file__).with_name("youtube_strategy_app.py")
            with patch.dict(sys.modules, {"streamlit": fake_streamlit}), patch.dict("os.environ", {"YOUTUBE_STRATEGY_DATA_DIR": directory}):
                runpy.run_path(str(app_path), run_name="__main__")
            self.assertTrue(any(name == "markdown" for name, _ in fake_streamlit.rendered))


class DynamicExitBacktestTests(unittest.TestCase):
    def test_dynamic_exit_rules_normalize(self):
        rules = engine.normalize_machine_rules(
            {
                "trailing_stop_pct": "5",
                "move_stop_to_breakeven_at_r": "1.5",
                "exit_below_vwap": "true",
                "exit_below_fast_ema": True,
            }
        )
        self.assertEqual(rules["trailing_stop_pct"], 5.0)
        self.assertEqual(rules["move_stop_to_breakeven_at_r"], 1.5)
        self.assertTrue(rules["exit_below_vwap"])
        self.assertTrue(rules["exit_below_fast_ema"])

    def test_trailing_stop_updates_causally_and_can_exit_next_bar(self):
        rows = [
            bar(18, 0, 10.0, 10.1, 9.9, 10.0),
            bar(18, 1, 10.0, 11.0, 9.9, 10.9),
            bar(18, 2, 10.8, 10.9, 10.4, 10.5),
        ]
        strategy = simple_strategy(
            stop_loss_pct=20,
            reward_risk=None,
            trailing_stop_pct=5,
        )
        result = engine.run_backtest(
            rows,
            strategy,
            "TEST",
            engine.BacktestSettings(
                spread_bps=0,
                slippage_bps=0,
                max_concurrent_positions=1,
                allow_extended_hours=False,
            ),
        )
        self.assertEqual(result["metrics"]["trade_count"], 1)
        trade = result["trades"][0]
        self.assertEqual(trade["reason"], "Stop loss")
        self.assertIsNone(trade["target_price"])
        self.assertGreater(trade["pnl"], 0)

    def test_breakeven_rule_moves_stop_after_r_trigger(self):
        rows = [
            bar(18, 0, 10.0, 10.1, 9.9, 10.0),
            bar(18, 1, 10.0, 11.2, 9.9, 11.0),
            bar(18, 2, 10.5, 10.6, 9.8, 10.0),
        ]
        strategy = simple_strategy(
            stop_loss_pct=10,
            reward_risk=None,
            move_stop_to_breakeven_at_r=1.0,
        )
        result = engine.run_backtest(
            rows,
            strategy,
            "TEST",
            engine.BacktestSettings(
                spread_bps=0,
                slippage_bps=0,
                max_concurrent_positions=1,
                allow_extended_hours=False,
            ),
        )
        trade = result["trades"][0]
        self.assertEqual(trade["reason"], "Stop loss")
        self.assertAlmostEqual(trade["pnl"], 0.0, places=6)

    def test_optimizer_does_not_inject_fixed_target_into_dynamic_exit_source(self):
        strategy = simple_strategy(
            reward_risk=None,
            trailing_stop_pct=4.0,
        )
        variants = engine.generate_strategy_variants(strategy, maximum=40)
        self.assertGreater(len(variants), 1)
        self.assertTrue(all(item.get("reward_risk") is None for item in variants))
        self.assertTrue(any(item.get("trailing_stop_pct") != 4.0 for item in variants))

    def test_scale_out_stages_normalize_sort_and_preserve_remainder(self):
        rules = engine.normalize_machine_rules(
            {
                "scale_out_stages": [
                    {"fraction_pct": 25, "at_r": 2},
                    {"fraction_pct": 25, "at_r": 1},
                    {"fraction_pct": 60, "at_r": 3},
                    {"fraction_pct": 10, "at_r": -1},
                ],
                "move_stop_to_breakeven_after_scale_out": True,
                "trail_below_vwap": True,
            }
        )
        self.assertEqual(
            rules["scale_out_stages"],
            [
                {"fraction_pct": 25.0, "at_r": 1.0},
                {"fraction_pct": 25.0, "at_r": 2.0},
            ],
        )
        self.assertTrue(rules["move_stop_to_breakeven_after_scale_out"])
        self.assertTrue(rules["trail_below_vwap"])

    def test_multi_stage_scale_out_executes_and_moves_remainder_to_breakeven(self):
        rows = [
            bar(18, 0, 10.0, 10.1, 9.9, 10.0),
            bar(18, 1, 10.0, 12.2, 9.5, 12.0),
            bar(18, 2, 10.2, 10.3, 9.8, 10.0),
        ]
        strategy = simple_strategy(
            stop_loss_pct=10,
            reward_risk=None,
            scale_out_stages=[
                {"fraction_pct": 25, "at_r": 1},
                {"fraction_pct": 25, "at_r": 2},
            ],
            move_stop_to_breakeven_after_scale_out=True,
        )
        result = engine.run_backtest(
            rows,
            strategy,
            "TEST",
            engine.BacktestSettings(
                spread_bps=0,
                slippage_bps=0,
                max_concurrent_positions=1,
                allow_extended_hours=False,
            ),
        )
        self.assertEqual(result["metrics"]["trade_count"], 1)
        trade = result["trades"][0]
        self.assertEqual(trade["reason"], "Stop loss")
        self.assertEqual(len(trade["partial_exits"]), 2)
        self.assertIn("stage 1", trade["partial_exits"][0]["reason"].lower())
        self.assertIn("stage 2", trade["partial_exits"][1]["reason"].lower())
        self.assertGreater(trade["scaled_out_quantity"], 0)
        self.assertGreater(trade["pnl"], 0)
        self.assertGreater(trade["max_favorable_excursion_pct"], 0)
        self.assertGreaterEqual(trade["max_adverse_excursion_pct"], 0)
        self.assertEqual(trade["management_event_count"], 2)
        attribution = result["exit_attribution"]
        self.assertIn("Stop loss", attribution["final_exit_reasons"])
        self.assertEqual(len(attribution["partial_exit_reasons"]), 2)
        self.assertGreater(attribution["partial_exit_net_pnl"], 0)

    def test_legacy_single_scale_out_remains_supported(self):
        rules = engine.normalize_machine_rules(
            {"scale_out_fraction_pct": 50, "scale_out_at_r": 1}
        )
        self.assertEqual(
            engine.configured_scale_out_stages(rules),
            [{"fraction_pct": 50.0, "at_r": 1.0}],
        )

    def test_optimizer_preserves_no_fixed_target_for_staged_exit_policy(self):
        strategy = simple_strategy(
            reward_risk=None,
            scale_out_stages=[
                {"fraction_pct": 25, "at_r": 1},
                {"fraction_pct": 25, "at_r": 2},
            ],
            trail_below_vwap=True,
        )
        variants = engine.generate_strategy_variants(strategy, maximum=40)
        self.assertGreater(len(variants), 1)
        self.assertTrue(all(item.get("reward_risk") is None for item in variants))
        self.assertTrue(all(item.get("scale_out_stages") for item in variants))



if __name__ == "__main__":
    unittest.main()
