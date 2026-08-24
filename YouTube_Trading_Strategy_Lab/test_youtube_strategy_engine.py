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

    def number_input(self, label, **kwargs):
        return kwargs.get("value", 0)

    def slider(self, label, **kwargs):
        return kwargs.get("value", 0)

    def checkbox(self, label, value=False, **kwargs):
        return value

    def selectbox(self, label, options, index=0, **kwargs):
        return list(options)[index]


class FakeStreamlit(types.ModuleType):
    def __init__(self, session_state=None):
        super().__init__("streamlit")
        self.session_state = session_state or {}
        self.secrets = {
            "ALPACA_API_KEY": "fake-alpaca-key",
            "ALPACA_SECRET_KEY": "fake-alpaca-secret",
            "GEMINI_API_KEY": "fake-gemini-key",
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
        if name == "tabs":
            return lambda labels: [FakePanel() for _ in labels]
        if name == "columns":
            return lambda count, **kwargs: [FakePanel() for _ in range(count if isinstance(count, int) else len(count))]
        if name in {"form", "expander", "spinner"}:
            return lambda *args, **kwargs: FakePanel()
        if name == "selectbox":
            return lambda label, options, index=0, **kwargs: list(options)[index]
        if name == "checkbox":
            return lambda label, value=False, **kwargs: value
        if name in {"number_input", "slider"}:
            return lambda label, **kwargs: kwargs.get("value", 0)
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
                }
            )
            app_path = Path(__file__).with_name("youtube_strategy_app.py")
            with patch.dict(sys.modules, {"streamlit": fake_streamlit}), patch.dict("os.environ", {"YOUTUBE_STRATEGY_DATA_DIR": directory}):
                runpy.run_path(str(app_path), run_name="__main__")
            self.assertTrue(any(name == "dataframe" for name, _ in fake_streamlit.rendered))
            self.assertTrue(any(name == "line_chart" for name, _ in fake_streamlit.rendered))


if __name__ == "__main__":
    unittest.main()
