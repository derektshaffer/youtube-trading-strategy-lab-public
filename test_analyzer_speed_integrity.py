"""Regression guards for Analyzer speed work that must not change live reasoning."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

import youtube_strategy_engine as engine


ROOT = Path(__file__).parent
APP_PATH = ROOT / "trading_intelligence_app.py"
LIVE_RUNNER_PATH = ROOT / "live_strategy_runner_page.py"


def _between(source: str, start: str, end: str) -> str:
    start_at = source.index(start)
    end_at = source.index(end, start_at)
    return source[start_at:end_at]


class AnalyzerSpeedIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = APP_PATH.read_text(encoding="utf-8")
        cls.analyzer_source = _between(
            cls.app_source,
            'elif module == "Stock Analyzer":',
            'elif module == "Live / Paper":',
        )
        cls.live_runner_source = LIVE_RUNNER_PATH.read_text(encoding="utf-8")

    def test_stock_analyzer_has_no_periodic_background_refresh(self):
        self.assertNotIn("@st.fragment", self.analyzer_source)
        self.assertNotIn("run_every=", self.analyzer_source)
        self.assertNotIn("live_strategy_runner_page", self.analyzer_source)

    def test_stock_analyzer_market_work_stays_click_gated(self):
        click_gate = self.analyzer_source.index("if analyze_stock:")
        market_client = self.analyzer_source.index("analyzer_market = market_client()")
        analysis_call = self.analyzer_source.index("analysis = analyze_stock_strategies(")
        self.assertGreater(market_client, click_gate)
        self.assertGreater(analysis_call, market_client)
        self.assertEqual(
            self.analyzer_source.count("analysis = analyze_stock_strategies("),
            1,
        )

    def test_stock_analyzer_hides_stale_current_setup_results(self):
        self.assertIn('analysis["_analyzed_at"] = analyzer_as_of.isoformat()', self.analyzer_source)
        self.assertIn("ANALYZER_RESULT_MAX_AGE_SECONDS", self.app_source)
        self.assertIn("stock_result_is_fresh", self.analyzer_source)
        self.assertIn("saved current-setup check is stale", self.analyzer_source)
        self.assertIn("if stock_result_matches and stock_result_is_fresh:", self.analyzer_source)

    def test_stock_analyzer_defers_research_only_live_learning(self):
        self.assertIn("queue_live_learning_cycle(", self.analyzer_source)
        self.assertNotIn("persist_live_learning_cycle(", self.analyzer_source)

    def test_live_learning_queue_does_not_fetch_market_history_or_main_library(self):
        queue_start = self.app_source.index("def queue_live_learning_cycle(")
        queue_end = self.app_source.index("def persist_live_learning_cycle(", queue_start)
        queue_source = self.app_source[queue_start:queue_end]
        self.assertIn("build_live_learning_outbox_store()", queue_source)
        self.assertNotIn("market.bars(", queue_source)
        self.assertNotIn("intelligence_store()", queue_source)

    def test_live_runner_remains_explicit_refresh_only(self):
        self.assertIn('"Refresh live signal"', self.live_runner_source)
        self.assertIn("if refresh:", self.live_runner_source)
        self.assertNotIn("@st.fragment", self.live_runner_source)
        self.assertNotIn("run_every=", self.live_runner_source)

    def test_current_session_bar_requests_are_never_reused(self):
        """A future history cache must never make current-session reasoning stale."""
        market = engine.AlpacaMarketData("key", "secret", live_feed="iex", historical_feed="sip")
        end = engine.utc_now()
        start = end - timedelta(hours=2)
        response = {"bars": {"TEST": []}, "next_page_token": None}
        with patch.object(market, "_get", return_value=response) as mocked:
            market.bars(
                ["TEST"],
                start=start,
                end=end,
                timeframe="1Min",
                feed=market.live_feed,
                max_pages=2,
            )
            market.bars(
                ["TEST"],
                start=start,
                end=end,
                timeframe="1Min",
                feed=market.live_feed,
                max_pages=2,
            )
        self.assertEqual(mocked.call_count, 2)

    def test_completed_deep_history_is_shared_across_market_clients(self):
        """The cache must survive new Alpaca client objects created by Streamlit reruns."""
        engine._ALPACA_BAR_HISTORY_CACHE.clear()
        first_market = engine.AlpacaMarketData("key", "secret", live_feed="iex", historical_feed="sip")
        second_market = engine.AlpacaMarketData("key", "secret", live_feed="iex", historical_feed="sip")
        cutoff = first_market._history_cache_cutoff_utc()
        start = cutoff - timedelta(days=5)
        end = cutoff - timedelta(minutes=1)
        response = {
            "bars": {"TEST": [{"t": "2026-08-28T14:30:00Z", "c": 10.0}]},
            "next_page_token": None,
        }
        with patch.object(engine.AlpacaMarketData, "_get", return_value=response) as mocked:
            first_market.bars(
                ["TEST"],
                start=start,
                end=end,
                timeframe="1Min",
                feed=first_market.historical_feed,
                max_pages=2,
            )
            second_market.bars(
                ["TEST"],
                start=start,
                end=end,
                timeframe="1Min",
                feed=second_market.historical_feed,
                max_pages=2,
            )
        self.assertEqual(mocked.call_count, 1)

    def test_oversized_history_response_is_not_cached(self):
        """Deep research responses cannot turn the latency cache into a memory store."""
        engine._ALPACA_BAR_HISTORY_CACHE.clear()
        market = engine.AlpacaMarketData("key", "secret")
        key = ("oversized",)
        payload = {
            "TEST": [
                {"t": "2026-08-28T14:30:00Z", "c": 10.0},
                {"t": "2026-08-28T14:31:00Z", "c": 10.1},
            ]
        }
        with patch.object(engine, "ALPACA_BAR_HISTORY_CACHE_MAX_ROWS_PER_ENTRY", 1):
            market._history_cache_put(key, payload)
        self.assertIsNone(market._history_cache_get(key))

    def test_completed_deep_history_is_reused(self):
        """Identical requests ending before today should share one Alpaca fetch."""
        engine._ALPACA_BAR_HISTORY_CACHE.clear()
        market = engine.AlpacaMarketData("key", "secret", live_feed="iex", historical_feed="sip")
        cutoff = market._history_cache_cutoff_utc()
        start = cutoff - timedelta(days=5)
        end = cutoff - timedelta(minutes=1)
        response = {
            "bars": {"TEST": [{"t": "2026-08-28T14:30:00Z", "c": 10.0}]},
            "next_page_token": None,
        }
        with patch.object(market, "_get", return_value=response) as mocked:
            first = market.bars(
                ["TEST"],
                start=start,
                end=end,
                timeframe="1Min",
                feed=market.historical_feed,
                max_pages=2,
            )
            second = market.bars(
                ["TEST"],
                start=start,
                end=end,
                timeframe="1Min",
                feed=market.historical_feed,
                max_pages=2,
            )
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(first, second)
        self.assertIsNot(first["TEST"][0], second["TEST"][0])

    def test_spanning_request_reuses_only_deep_prefix(self):
        """Long Analyzer requests may cache old history, but today's suffix stays fresh."""
        engine._ALPACA_BAR_HISTORY_CACHE.clear()
        market = engine.AlpacaMarketData("key", "secret", live_feed="iex", historical_feed="sip")
        cutoff = market._history_cache_cutoff_utc()
        start = cutoff - timedelta(days=5)
        end = engine.utc_now()
        responses = [
            {"bars": {"TEST": [{"t": "2026-08-28T14:30:00Z", "c": 10.0}]}, "next_page_token": None},
            {"bars": {"TEST": [{"t": "2026-08-31T14:30:00Z", "c": 11.0}]}, "next_page_token": None},
            {"bars": {"TEST": [{"t": "2026-08-31T14:30:00Z", "c": 12.0}]}, "next_page_token": None},
        ]
        with patch.object(market, "_get", side_effect=responses) as mocked:
            first = market.bars(
                ["TEST"],
                start=start,
                end=end,
                timeframe="1Min",
                feed=market.historical_feed,
                max_pages=2,
            )
            second = market.bars(
                ["TEST"],
                start=start,
                end=end,
                timeframe="1Min",
                feed=market.historical_feed,
                max_pages=2,
            )
        self.assertEqual(mocked.call_count, 3)
        self.assertEqual(first["TEST"][-1]["c"], 11.0)
        self.assertEqual(second["TEST"][-1]["c"], 12.0)


if __name__ == "__main__":
    unittest.main()
