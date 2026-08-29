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
        return {symbol: [] for symbol in symbols}

    def news(self, symbols, *, hours):
        self.news_calls += 1
        return {symbol: [] for symbol in symbols}


class MarketDiscoveryTests(unittest.TestCase):
    def test_multi_strategy_scan_reuses_one_market_data_pass(self):
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
            if strategy["id"] == "validated":
                return {"status": "MATCH", "score": 91.0, "unknown": 0, "checks": []}
            return {"status": "WATCH", "score": 84.0, "unknown": 0, "checks": []}

        with (
            patch.object(discovery, "effective_strategy_for_live", side_effect=lambda item: item),
            patch.object(discovery, "average_completed_daily_volume", return_value=1_000_000),
            patch.object(discovery, "snapshot_metrics", side_effect=fake_snapshot_metrics),
            patch.object(discovery, "_needs_chart_data", return_value=False),
            patch.object(discovery, "match_strategy", side_effect=fake_match) as matcher,
        ):
            results = discovery.scan_market_strategies(
                market,
                ["AAA", "BBB"],
                strategies,
            )

        self.assertEqual(market.snapshot_calls, 1)
        self.assertEqual(len(market.bar_calls), 1)
        self.assertEqual(market.bar_calls[0][1], "1Day")
        self.assertEqual(market.news_calls, 0)
        self.assertEqual(matcher.call_count, 4)
        self.assertEqual([item["symbol"] for item in results], ["AAA", "BBB"])
        for item in results:
            self.assertEqual(item["best_strategy_id"], "validated")
            self.assertEqual(item["best_strategy_name"], "Validated Setup")
            self.assertEqual(len(item["strategy_matches"]), 2)


if __name__ == "__main__":
    unittest.main()
