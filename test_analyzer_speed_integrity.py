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


if __name__ == "__main__":
    unittest.main()
