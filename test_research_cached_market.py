from __future__ import annotations

from datetime import datetime, timedelta, timezone
import tempfile
import unittest

from research_cached_market import CachedResearchMarket


UTC = timezone.utc


class FakeMarket:
    historical_feed = "sip"
    live_feed = "iex"

    def __init__(self) -> None:
        self.calls = 0
        self.last_kwargs = {}

    def bars(self, symbols, **kwargs):
        self.calls += 1
        self.last_kwargs = dict(kwargs)
        return {
            symbols[0]: [
                {
                    "t": "2026-09-01T14:30:00Z",
                    "o": 10.0,
                    "h": 10.2,
                    "l": 9.9,
                    "c": 10.1,
                    "v": 1000,
                }
            ]
        }

    def quotes(self, *args, **kwargs):
        return {"delegated": True, "args": args, "kwargs": kwargs}


class CachedResearchMarketTests(unittest.TestCase):
    def test_exact_finalized_bar_request_reuses_artifact(self):
        base = FakeMarket()
        start = datetime(2026, 8, 30, 14, 0, tzinfo=UTC)
        end = datetime.now(UTC) - timedelta(days=1)
        with tempfile.TemporaryDirectory() as temporary:
            market = CachedResearchMarket(base, data_dir=temporary)
            first = market.bars(
                ["SDOT"],
                start=start,
                end=end,
                timeframe="1Min",
                adjustment="raw",
                max_pages=30,
            )
            second = market.bars(
                ["SDOT"],
                start=start,
                end=end,
                timeframe="1Min",
                adjustment="raw",
                max_pages=30,
            )
            self.assertEqual(first, second)
            self.assertEqual(base.calls, 1)
            self.assertEqual(len(market.research_cache_events), 2)
            self.assertFalse(market.research_cache_events[0]["cache_hit"])
            self.assertTrue(market.research_cache_events[1]["cache_hit"])

    def test_provider_specific_bar_kwargs_delegate_unchanged(self):
        base = FakeMarket()
        with tempfile.TemporaryDirectory() as temporary:
            market = CachedResearchMarket(base, data_dir=temporary)
            market.bars(
                ["SDOT"],
                start=datetime(2026, 8, 30, tzinfo=UTC),
                end=datetime(2026, 9, 1, tzinfo=UTC),
                timeframe="1Min",
                adjustment="raw",
                max_pages=30,
                feed="sip",
            )
            self.assertEqual(base.calls, 1)
            self.assertEqual(base.last_kwargs["feed"], "sip")
            self.assertEqual(market.research_cache_events, [])

    def test_omitted_adjustment_delegates_to_provider_default(self):
        base = FakeMarket()
        with tempfile.TemporaryDirectory() as temporary:
            market = CachedResearchMarket(base, data_dir=temporary)
            market.bars(
                ["SDOT"],
                start=datetime(2026, 8, 30, tzinfo=UTC),
                end=datetime(2026, 9, 1, tzinfo=UTC),
                timeframe="1Min",
                max_pages=30,
            )
            self.assertEqual(base.calls, 1)
            self.assertNotIn("adjustment", base.last_kwargs)
            self.assertEqual(market.research_cache_events, [])

    def test_adjusted_history_delegates_to_provider(self):
        base = FakeMarket()
        with tempfile.TemporaryDirectory() as temporary:
            market = CachedResearchMarket(base, data_dir=temporary)
            market.bars(
                ["SDOT"],
                start=datetime(2026, 8, 30, tzinfo=UTC),
                end=datetime(2026, 9, 1, tzinfo=UTC),
                timeframe="1Min",
                adjustment="split",
                max_pages=30,
            )
            self.assertEqual(base.calls, 1)
            self.assertEqual(base.last_kwargs["adjustment"], "split")
            self.assertEqual(market.research_cache_events, [])

    def test_batch_request_and_other_methods_delegate(self):
        base = FakeMarket()
        with tempfile.TemporaryDirectory() as temporary:
            market = CachedResearchMarket(base, data_dir=temporary)
            market.bars(
                ["AAPL", "MSFT"],
                start=datetime(2026, 8, 30, tzinfo=UTC),
                end=datetime(2026, 9, 1, tzinfo=UTC),
                timeframe="1Min",
                adjustment="raw",
            )
            self.assertEqual(base.calls, 1)
            self.assertTrue(market.quotes(["AAPL"])["delegated"])


if __name__ == "__main__":
    unittest.main()
