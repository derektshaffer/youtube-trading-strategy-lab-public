import unittest
from datetime import date, datetime
from unittest.mock import patch

import trading_market_discovery as discovery


class FakeMarket:
    historical_feed = "iex"
    live_feed = "iex"

    def __init__(self):
        self.snapshot_calls = 0
        self.bar_calls = []
        self.news_calls = 0

    def snapshots(self, symbols):
        self.snapshot_calls += 1
        return {symbol: {"symbol": symbol} for symbol in symbols}

    def bars(self, symbols, *, start, end, timeframe, max_pages, feed=None):
        self.bar_calls.append((tuple(symbols), timeframe, feed))
        if timeframe == "1Min":
            return {
                symbol: [
                    {"t": "2026-08-29T13:30:00Z", "o": 10.0, "h": 10.2, "l": 9.9, "c": 10.1, "v": 100},
                    {"t": "2026-08-29T13:31:00Z", "o": 10.1, "h": 10.3, "l": 10.0, "c": 10.2, "v": 120},
                ]
                for symbol in symbols
            }
        return {symbol: [] for symbol in symbols}

    def news(self, symbols, *, hours):
        self.news_calls += 1
        return {symbol: [] for symbol in symbols}


class MarketDiscoveryTests(unittest.TestCase):
    def test_strategy_live_timeframe_prefers_finder_winner_interval(self):
        self.assertEqual(
            discovery._strategy_live_timeframe({"_finder_timeframe": "15Min"}),
            "15Min",
        )
        self.assertEqual(
            discovery._strategy_live_timeframe(
                {"last_validation": {"timeframe": "5Min"}}
            ),
            "5Min",
        )

    def test_strategy_extended_hours_matches_saved_backtest_setting(self):
        self.assertFalse(
            discovery._strategy_allows_extended_hours(
                {"optimized_backtest_settings": {"allow_extended_hours": False}}
            )
        )
        self.assertTrue(
            discovery._strategy_allows_extended_hours(
                {"optimized_backtest_settings": {"allow_extended_hours": True}}
            )
        )

    def test_analyzer_market_features_use_finder_timeframe(self):
        market = FakeMarket()
        strategy = {
            "id": "five-minute",
            "name": "Five Minute",
            "direction": "long",
            "_finder_timeframe": "5Min",
            "optimized_backtest_settings": {"allow_extended_hours": True},
            "machine_rules": {"min_price": 1.0},
        }
        with (
            patch.object(discovery, "effective_strategy_for_live", side_effect=lambda item: item),
            patch.object(discovery, "average_completed_daily_volume", return_value=1_000_000),
            patch.object(
                discovery,
                "snapshot_metrics",
                return_value={
                    "symbol": "AAA",
                    "price": 10.0,
                    "relative_volume": 1.5,
                    "day_change_pct": 2.0,
                    "spread_pct": 0.1,
                },
            ),
            patch.object(
                discovery,
                "match_strategy",
                return_value={"status": "WATCH", "score": 70.0, "unknown": 0, "checks": []},
            ),
        ):
            result = discovery.analyze_stock_strategies(market, "AAA", [strategy])

        self.assertEqual(result["timeframe"], "5Min")
        self.assertEqual(result["market_features"]["features"]["bar_count"], 1)
        self.assertEqual(result["comparisons"][0]["timeframe"], "5Min")

    def test_completed_daily_history_window_is_stable_within_same_market_day(self):
        morning = datetime(2026, 8, 31, 9, 45, tzinfo=discovery.ET)
        afternoon = datetime(2026, 8, 31, 15, 45, tzinfo=discovery.ET)
        morning_window = discovery._completed_daily_history_window(now=morning)
        afternoon_window = discovery._completed_daily_history_window(now=afternoon)
        self.assertEqual(morning_window, afternoon_window)
        self.assertEqual(
            morning_window[1].astimezone(discovery.ET),
            datetime(2026, 8, 31, 0, 0, tzinfo=discovery.ET),
        )

    def test_completed_daily_reference_supplies_prior_session_rules_without_today_leakage(self):
        rows = [
            {"t": "2026-08-25T04:00:00Z", "c": 10.0, "h": 10.5, "v": 100.0},
            {"t": "2026-08-26T04:00:00Z", "c": 10.5, "h": 11.0, "v": 120.0},
            {"t": "2026-08-27T04:00:00Z", "c": 11.0, "h": 11.5, "v": 80.0},
            {"t": "2026-08-28T04:00:00Z", "c": 12.0, "h": 12.5, "v": 300.0},
            # Current-day partial daily bar must never enter prior-session references.
            {"t": "2026-08-30T04:00:00Z", "c": 20.0, "h": 25.0, "v": 9999.0},
        ]
        reference = discovery._completed_daily_reference(
            rows,
            today_et=date(2026, 8, 30),
        )
        self.assertEqual(reference["previous_day_high"], 12.5)
        self.assertAlmostEqual(
            reference["previous_day_change_pct"],
            (12.0 / 11.0 - 1.0) * 100.0,
        )
        self.assertAlmostEqual(reference["previous_day_volume_ratio"], 3.0)

    def test_previous_day_high_breakout_uses_latest_intraday_cross(self):
        rows = [
            {"t": "2026-08-31T14:00:00Z", "c": 12.40},
            {"t": "2026-08-31T14:01:00Z", "c": 12.60},
        ]
        self.assertTrue(
            discovery._latest_previous_day_high_breakout(rows, 12.50)
        )
        self.assertFalse(
            discovery._latest_previous_day_high_breakout(rows, 13.00)
        )

    def test_strategy_chart_checks_overrides_missing_prior_day_fields_from_daily_history(self):
        strategy = {"machine_rules": {"previous_day_high_breakout": True}}
        rows = [
            {"t": "2026-08-31T14:00:00Z", "c": 12.40},
            {"t": "2026-08-31T14:01:00Z", "c": 12.60},
        ]
        reference = {
            "previous_day_high": 12.50,
            "previous_day_change_pct": 4.0,
            "previous_day_volume_ratio": 2.2,
        }
        with patch.object(
            discovery,
            "chart_trigger_checks",
            return_value={
                "previous_day_high_breakout": None,
                "previous_day_change_pct": None,
                "previous_day_volume_ratio": None,
            },
        ):
            checks = discovery._strategy_chart_checks(rows, strategy, reference)

        self.assertTrue(checks["previous_day_high_breakout"])
        self.assertEqual(checks["previous_day_change_pct"], 4.0)
        self.assertEqual(checks["previous_day_volume_ratio"], 2.2)

    def test_chart_data_gate_covers_live_chart_dependent_rules(self):
        for rule_name, rule_value in (
            ("previous_day_high_breakout", True),
            ("min_previous_day_volume_ratio", 1.5),
            ("min_previous_day_change_pct", 5.0),
            ("avwap_anchor_mode", "session_open"),
            ("require_price_above_avwap", True),
            ("require_fast_ema_pullback", True),
            ("require_price_above_trend_ema", True),
            ("require_pullback_breakout", True),
        ):
            strategy = {"machine_rules": {rule_name: rule_value}}
            self.assertTrue(discovery._needs_chart_data(strategy), rule_name)

        self.assertFalse(
            discovery._needs_chart_data({"machine_rules": {"min_price": 1.0}})
        )

    def test_multi_strategy_scan_reuses_one_market_data_pass_and_builds_features_once_per_stock(self):
        market = FakeMarket()
        strategies = [
            {
                "id": "validated",
                "name": "Validated Setup",
                "direction": "long",
                "validation_status": "validated",
                "last_validation": {"robustness_score": 82.0},
                "machine_rules": {"min_price": 1.0},
            },
            {
                "id": "research",
                "name": "Research Setup",
                "direction": "long",
                "validation_status": "unvalidated",
                "machine_rules": {"min_price": 1.0},
            },
        ]

        def fake_snapshot_metrics(symbol, snapshot, *, average_daily_volume):
            return {
                "symbol": symbol,
                "price": 10.0,
                "relative_volume": 2.0,
                "day_change_pct": 5.0,
                "spread_pct": 0.2,
            }

        def fake_match(metrics, strategy):
            self.assertEqual(metrics.get("market_features"), feature_payload["features"])
            if strategy["id"] == "validated":
                return {"status": "MATCH", "score": 91.0, "unknown": 0, "checks": []}
            return {"status": "WATCH", "score": 84.0, "unknown": 0, "checks": []}

        feature_payload = {
            "features": {"price_above_vwap": True},
            "evidence": {"vwap": {"hold_bars": 2}},
            "missing_data": [],
            "provider": "native",
        }

        with (
            patch.object(discovery, "effective_strategy_for_live", side_effect=lambda item: item),
            patch.object(discovery, "average_completed_daily_volume", return_value=1_000_000),
            patch.object(discovery, "snapshot_metrics", side_effect=fake_snapshot_metrics),
            patch.object(discovery, "_needs_chart_data", return_value=False),
            patch.object(discovery, "build_market_features", return_value=feature_payload) as feature_builder,
            patch.object(discovery, "match_strategy", side_effect=fake_match) as matcher,
        ):
            results = discovery.scan_market_strategies(
                market,
                ["AAA", "BBB"],
                strategies,
            )

        self.assertEqual(market.snapshot_calls, 1)
        self.assertEqual(len(market.bar_calls), 2)
        self.assertEqual([call[1] for call in market.bar_calls], ["1Day", "1Min"])
        self.assertEqual(market.news_calls, 0)
        self.assertEqual(feature_builder.call_count, 2)
        self.assertEqual(matcher.call_count, 4)
        self.assertEqual([item["symbol"] for item in results], ["AAA", "BBB"])
        for item in results:
            self.assertEqual(item["best_strategy_id"], "validated")
            self.assertEqual(item["best_strategy_name"], "Validated Setup")
            self.assertEqual(len(item["strategy_matches"]), 2)
            self.assertEqual(item["market_features"], feature_payload)





    def test_momentum_universe_interleaves_gainers_and_active_without_duplicates(self):
        merged = discovery.merge_momentum_candidate_universe(
            ["AAA", "BBB", "CCC"],
            ["AAA", "DDD", "EEE", "FFF"],
            limit=6,
        )
        self.assertEqual(merged, ["AAA", "BBB", "DDD", "CCC", "EEE", "FFF"])
        self.assertEqual(len(merged), len(set(merged)))

    def test_large_market_scan_is_chunked_without_dropping_symbols(self):
        market = FakeMarket()
        symbols = [f"S{i:02d}" for i in range(45)]
        strategies = [
            {
                "id": "one",
                "name": "One",
                "direction": "long",
                "validation_status": "unvalidated",
                "machine_rules": {"min_price": 1.0},
            },
            {
                "id": "two",
                "name": "Two",
                "direction": "long",
                "validation_status": "unvalidated",
                "machine_rules": {"min_price": 1.0},
            },
        ]

        def fake_snapshot_metrics(symbol, snapshot, *, average_daily_volume):
            number = int(symbol[1:])
            return {
                "symbol": symbol,
                "price": 10.0,
                "relative_volume": 1.0 + number / 100.0,
                "day_change_pct": float(number),
                "spread_pct": 0.1,
            }

        with (
            patch.object(discovery, "effective_strategy_for_live", side_effect=lambda item: item),
            patch.object(discovery, "average_completed_daily_volume", return_value=1_000_000),
            patch.object(discovery, "snapshot_metrics", side_effect=fake_snapshot_metrics),
            patch.object(discovery, "_needs_chart_data", return_value=False),
            patch.object(
                discovery,
                "build_market_features",
                return_value={"features": {}, "evidence": {}, "missing_data": [], "provider": "native"},
            ) as feature_builder,
            patch.object(
                discovery,
                "match_strategy",
                return_value={"status": "WATCH", "score": 80.0, "unknown": 0, "checks": []},
            ) as matcher,
        ):
            results = discovery.scan_market_strategies(
                market,
                symbols,
                strategies,
                batch_size=20,
            )

        self.assertEqual(len(results), 45)
        self.assertEqual({item["symbol"] for item in results}, set(symbols))
        self.assertEqual(market.snapshot_calls, 3)
        self.assertEqual(len(market.bar_calls), 6)
        self.assertEqual(
            [len(call[0]) for call in market.bar_calls],
            [20, 20, 20, 20, 5, 5],
        )
        self.assertEqual(feature_builder.call_count, 45)
        self.assertEqual(matcher.call_count, 90)
        self.assertEqual(results[0]["symbol"], "S44")

    def test_market_scan_rejects_more_than_live_safety_limit_instead_of_silent_truncation(self):
        market = FakeMarket()
        symbols = [f"X{i:03d}" for i in range(discovery.MAX_LIVE_SCAN_SYMBOLS + 1)]
        strategy = {
            "id": "one",
            "name": "One",
            "direction": "long",
            "machine_rules": {"min_price": 1.0},
        }

        with self.assertRaises(discovery.AppError):
            discovery.scan_market_strategies(market, symbols, [strategy])

        self.assertEqual(market.snapshot_calls, 0)
        self.assertEqual(market.bar_calls, [])

    def test_batch_progress_reports_batch_number(self):
        market = FakeMarket()
        symbols = [f"P{i:02d}" for i in range(25)]
        strategy = {
            "id": "one",
            "name": "One",
            "direction": "long",
            "machine_rules": {"min_price": 1.0},
        }
        messages = []

        with (
            patch.object(discovery, "effective_strategy_for_live", side_effect=lambda item: item),
            patch.object(discovery, "average_completed_daily_volume", return_value=1_000_000),
            patch.object(
                discovery,
                "snapshot_metrics",
                return_value={
                    "price": 10.0,
                    "relative_volume": 1.0,
                    "day_change_pct": 1.0,
                    "spread_pct": 0.1,
                },
            ),
            patch.object(discovery, "_needs_chart_data", return_value=False),
            patch.object(
                discovery,
                "build_market_features",
                return_value={"features": {}, "evidence": {}, "missing_data": [], "provider": "native"},
            ),
            patch.object(
                discovery,
                "match_strategy",
                return_value={"status": "WATCH", "score": 70.0, "unknown": 0, "checks": []},
            ),
        ):
            discovery.scan_market_strategies(
                market,
                symbols,
                [strategy],
                batch_size=20,
                progress=messages.append,
            )

        self.assertTrue(any(message.startswith("Batch 1/2") for message in messages))
        self.assertTrue(any(message.startswith("Batch 2/2") for message in messages))

    def test_analyzer_returns_market_features_even_when_strategy_needs_no_chart_rule(self):
        market = FakeMarket()
        strategies = [
            {
                "id": "basic",
                "name": "Basic",
                "direction": "long",
                "validation_status": "unvalidated",
                "machine_rules": {"min_price": 1.0},
            }
        ]

        with (
            patch.object(discovery, "effective_strategy_for_live", side_effect=lambda item: item),
            patch.object(discovery, "average_completed_daily_volume", return_value=1_000_000),
            patch.object(
                discovery,
                "snapshot_metrics",
                return_value={
                    "symbol": "AAA",
                    "price": 10.0,
                    "relative_volume": 1.5,
                    "day_change_pct": 2.0,
                    "spread_pct": 0.1,
                },
            ),
            patch.object(discovery, "_needs_chart_data", return_value=False),
            patch.object(
                discovery,
                "match_strategy",
                return_value={"status": "WATCH", "score": 70.0, "unknown": 0, "checks": []},
            ),
        ):
            result = discovery.analyze_stock_strategies(market, "AAA", strategies)

        self.assertIn("market_features", result)
        self.assertEqual(result["market_features"]["features"]["bar_count"], 2)
        self.assertEqual(result["news_items"], [])
        self.assertEqual(market.news_calls, 1)
        self.assertEqual([call[1] for call in market.bar_calls], ["1Day", "1Min"])


if __name__ == "__main__":
    unittest.main()
