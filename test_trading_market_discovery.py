import unittest
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
            # Market features are deliberately observational at this stage and
            # must not be injected into the strategy matcher yet.
            self.assertNotIn("market_features", metrics)
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
        self.assertEqual([call[1] for call in market.bar_calls], ["1Day", "1Min"])


if __name__ == "__main__":
    unittest.main()
