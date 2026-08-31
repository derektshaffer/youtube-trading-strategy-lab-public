import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import live_strategy_runner_page as runner


NOW = datetime(2026, 8, 28, 17, 30, tzinfo=timezone.utc)


def valid_metrics(**updates):
    metrics = {
        "symbol": "TEST",
        "price": 100.0,
        "quote_timestamp": (NOW - timedelta(seconds=8)).isoformat(),
        "trade_timestamp": (NOW - timedelta(seconds=5)).isoformat(),
    }
    metrics.update(updates)
    return metrics


def valid_signal():
    return {
        "status": "MATCH",
        "unknown": 0,
        "suggested_stop": 95.0,
        "suggested_target": 110.0,
    }


def valid_strategy(direction="long"):
    return {
        "id": "strategy-1",
        "approved": True,
        "direction": direction,
        "machine_rules": {
            "stop_loss_pct": 5.0,
            "reward_risk": 2.0,
        },
    }


class FakePaperTrader:
    def __init__(self):
        self.submissions = []

    def account(self):
        return {
            "equity": "10000",
            "last_equity": "10000",
            "trading_blocked": False,
        }

    def clock(self):
        return {"is_open": True}

    def positions(self):
        return []

    def orders(self, **kwargs):
        return []

    def submit_bracket_market_order(self, **kwargs):
        self.submissions.append(kwargs)
        return {"id": "paper-order-1", **kwargs}


class ClockValuePaperTrader(FakePaperTrader):
    def __init__(self, is_open):
        super().__init__()
        self.is_open = is_open

    def clock(self):
        return {"is_open": self.is_open}


class MarketDataFreshnessTests(unittest.TestCase):
    def test_recent_quote_and_trade_are_accepted(self):
        fresh, message, ages = runner.market_data_freshness(valid_metrics(), now=NOW)
        self.assertTrue(fresh)
        self.assertIn("recent", message)
        self.assertEqual(ages, {"quote": 8.0, "trade": 5.0})

    def test_missing_malformed_naive_stale_and_future_timestamps_fail_closed(self):
        cases = {
            "missing quote": valid_metrics(quote_timestamp=None),
            "malformed trade": valid_metrics(trade_timestamp="not-a-time"),
            "timezone missing": valid_metrics(quote_timestamp="2026-08-28T17:29:55"),
            "stale quote": valid_metrics(quote_timestamp=(NOW - timedelta(seconds=91)).isoformat()),
            "stale trade": valid_metrics(trade_timestamp=(NOW - timedelta(hours=1)).isoformat()),
            "future quote": valid_metrics(quote_timestamp=(NOW + timedelta(seconds=16)).isoformat()),
        }
        for label, metrics in cases.items():
            with self.subTest(label=label):
                fresh, _, _ = runner.market_data_freshness(metrics, now=NOW)
                self.assertFalse(fresh)

    def test_stale_data_never_constructs_a_broker_client_or_submits(self):
        stale = valid_metrics(trade_timestamp="2020-01-01T00:00:00+00:00")
        with patch.object(runner, "utc_now", return_value=NOW), patch.object(
            runner, "paper_execution_fidelity", return_value={"status": "ready", "unsupported_management": []}
        ), patch.object(
            runner, "paper_client", side_effect=AssertionError("broker client must not be constructed")
        ):
            result = runner.paper_entry(
                strategy=valid_strategy(),
                metrics=stale,
                signal=valid_signal(),
                risk_per_trade_pct=0.5,
                max_position_pct=20.0,
                max_position_dollars=0.0,
                max_daily_loss=200.0,
                max_entries_per_day=5,
                max_open_positions=3,
                one_entry_per_symbol_day=True,
            )
        self.assertFalse(result["submitted"])
        self.assertIn("could not be verified as recent", result["message"])

    def test_recent_data_can_reach_the_existing_order_safeguards(self):
        trader = FakePaperTrader()
        with patch.object(runner, "utc_now", return_value=NOW), patch.object(
            runner, "paper_execution_fidelity", return_value={"status": "ready", "unsupported_management": []}
        ), patch.object(
            runner, "paper_client", return_value=trader
        ):
            result = runner.paper_entry(
                strategy=valid_strategy(),
                metrics=valid_metrics(),
                signal=valid_signal(),
                risk_per_trade_pct=0.5,
                max_position_pct=20.0,
                max_position_dollars=0.0,
                max_daily_loss=200.0,
                max_entries_per_day=5,
                max_open_positions=3,
                one_entry_per_symbol_day=True,
            )
        self.assertTrue(result["submitted"])
        self.assertEqual(len(trader.submissions), 1)
        self.assertEqual(trader.submissions[0]["symbol"], "TEST")

    def test_simple_strategy_is_blocked_when_session_flattening_is_unmanaged(self):
        with patch.object(
            runner,
            "paper_client",
            side_effect=AssertionError("lifecycle mismatch must block before broker construction"),
        ):
            result = runner.paper_entry(
                strategy=valid_strategy(),
                metrics=valid_metrics(),
                signal=valid_signal(),
                risk_per_trade_pct=0.5,
                max_position_pct=20.0,
                max_position_dollars=0.0,
                max_daily_loss=200.0,
                max_entries_per_day=5,
                max_open_positions=3,
                one_entry_per_symbol_day=True,
            )
        self.assertFalse(result["submitted"])
        self.assertIn("cannot yet reproduce", result["message"])
        self.assertIn("end-of-session", result["message"])

    def test_dynamic_exit_strategy_is_blocked_before_broker_client(self):
        strategy = valid_strategy()
        strategy["machine_rules"] = {
            "stop_loss_pct": 5.0,
            "reward_risk": 2.0,
            "trailing_stop_pct": 3.0,
        }
        with patch.object(
            runner,
            "paper_client",
            side_effect=AssertionError("dynamic-exit mismatch must block before broker construction"),
        ):
            result = runner.paper_entry(
                strategy=strategy,
                metrics=valid_metrics(),
                signal=valid_signal(),
                risk_per_trade_pct=0.5,
                max_position_pct=20.0,
                max_position_dollars=0.0,
                max_daily_loss=200.0,
                max_entries_per_day=5,
                max_open_positions=3,
                one_entry_per_symbol_day=True,
            )
        self.assertFalse(result["submitted"])
        self.assertIn("cannot yet reproduce", result["message"])
        self.assertIn("Trailing-stop", result["message"])

    def test_data_that_expires_during_broker_checks_is_not_submitted(self):
        trader = FakePaperTrader()
        later = NOW + timedelta(seconds=100)
        with patch.object(runner, "utc_now", side_effect=[NOW, NOW, later]), patch.object(
            runner, "paper_execution_fidelity", return_value={"status": "ready", "unsupported_management": []}
        ), patch.object(
            runner, "paper_client", return_value=trader
        ):
            result = runner.paper_entry(
                strategy=valid_strategy(),
                metrics=valid_metrics(),
                signal=valid_signal(),
                risk_per_trade_pct=0.5,
                max_position_pct=20.0,
                max_position_dollars=0.0,
                max_daily_loss=200.0,
                max_entries_per_day=5,
                max_open_positions=3,
                one_entry_per_symbol_day=True,
            )
        self.assertFalse(result["submitted"])
        self.assertIn("expired during safety checks", result["message"])
        self.assertEqual(trader.submissions, [])

    def test_non_boolean_market_open_value_fails_closed(self):
        trader = ClockValuePaperTrader("true")
        with patch.object(runner, "utc_now", return_value=NOW), patch.object(
            runner, "paper_client", return_value=trader
        ):
            result = runner.paper_entry(
                strategy=valid_strategy(),
                metrics=valid_metrics(),
                signal=valid_signal(),
                risk_per_trade_pct=0.5,
                max_position_pct=20.0,
                max_position_dollars=0.0,
                max_daily_loss=200.0,
                max_entries_per_day=5,
                max_open_positions=3,
                one_entry_per_symbol_day=True,
            )
        self.assertFalse(result["submitted"])
        self.assertEqual(trader.submissions, [])

    def test_missing_symbol_fails_before_constructing_broker_client(self):
        with patch.object(runner, "paper_client", side_effect=AssertionError("must not construct broker client")):
            result = runner.paper_entry(
                strategy=valid_strategy(),
                metrics=valid_metrics(symbol=""),
                signal=valid_signal(),
                risk_per_trade_pct=0.5,
                max_position_pct=20.0,
                max_position_dollars=0.0,
                max_daily_loss=200.0,
                max_entries_per_day=5,
                max_open_positions=3,
                one_entry_per_symbol_day=True,
            )
        self.assertFalse(result["submitted"])


class RunnerLibraryTests(unittest.TestCase):
    def test_intelligence_finder_children_are_available_with_legacy_strategies(self):
        legacy = {
            "strategies": [
                {"id": "legacy-family", "name": "Legacy family"},
                {"id": "shared", "name": "Old shared record"},
            ]
        }
        intelligence = {
            "strategies": [
                {
                    "id": "finder-child",
                    "name": "SDOT optimized",
                    "source_type": "stock_specific_finder",
                    "approved": True,
                },
                {"id": "shared", "name": "Current shared record"},
            ]
        }

        merged = runner.merge_runner_libraries(legacy, intelligence)

        self.assertEqual(
            [item["id"] for item in merged["strategies"]],
            ["finder-child", "shared", "legacy-family"],
        )
        self.assertEqual(merged["strategies"][1]["name"], "Current shared record")
        self.assertFalse(merged["strategies"][0]["approved"])

    def test_requested_child_strategy_is_selected_by_stable_id(self):
        parent = {"id": "family-1", "name": "Family"}
        child = {
            "id": "stockfinder-child",
            "name": "Family — SDOT optimized",
            "optimized_for_symbol": "SDOT",
        }
        options = runner.strategy_options([parent, child])
        label = runner.requested_strategy_label(options, child["id"])
        self.assertTrue(label)
        self.assertEqual(options[label]["id"], child["id"])

    def test_unknown_handoff_does_not_change_runner_selection(self):
        options = runner.strategy_options([{"id": "family-1", "name": "Family"}])
        self.assertEqual(runner.requested_strategy_label(options, "missing"), "")


class StrategyDirectionTests(unittest.TestCase):
    def test_both_direction_is_long_capable_for_paper_auto(self):
        self.assertTrue(runner.is_long_strategy(valid_strategy("both")))

    def test_short_only_direction_remains_blocked(self):
        self.assertFalse(runner.is_long_strategy(valid_strategy("short")))


if __name__ == "__main__":
    unittest.main()
