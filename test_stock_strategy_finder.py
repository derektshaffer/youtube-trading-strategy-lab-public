from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from zoneinfo import ZoneInfo

import stock_strategy_finder as finder
import youtube_strategy_engine as engine


ET = ZoneInfo("America/New_York")


def bar(day: int, minute: int, close: float) -> dict:
    local = datetime(2026, 8, day, 9, 30, tzinfo=ET) + timedelta(minutes=minute)
    return {
        "t": local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "o": close - 0.02,
        "h": close + 0.05,
        "l": close - 0.05,
        "c": close,
        "v": 20_000 + minute * 100,
    }


def strategy(strategy_id: str, *, category: str = "momentum", **rules) -> dict:
    return {
        "id": strategy_id,
        "name": strategy_id,
        "category": category,
        "direction": "long",
        "backtest_supported": True,
        "machine_rules": engine.normalize_machine_rules(
            {
                "min_price": 1.0,
                "stop_loss_pct": 3.0,
                "reward_risk": 1.5,
                **rules,
            }
        ),
    }


class StockStrategyFinderPolicyTests(unittest.TestCase):
    def test_deep_search_keeps_every_technically_eligible_family(self):
        strategies = [
            strategy("vwap", category="reclaim", above_vwap=True),
            strategy("pullback", category="pullback", require_fast_ema_pullback=True, fast_ema_period=9),
            strategy("breakout", category="breakout", breakout_lookback_bars=20),
            strategy("volume", category="momentum", min_relative_volume=2.0),
        ]
        selected, skipped = finder.selected_strategies_for_profile(
            strategies,
            "SDOT",
            finder.search_profile("Deep"),
        )
        self.assertEqual({item["id"] for item in selected}, {item["id"] for item in strategies})
        self.assertFalse(skipped)

    def test_only_technical_ineligibility_removes_family(self):
        valid = strategy("valid", breakout_lookback_bars=10)
        short_only = strategy("short-only")
        short_only["direction"] = "short"
        locked = strategy("locked", min_relative_volume=2.0)
        locked["optimized_for_symbol"] = "AAPL"
        selected, skipped = finder.selected_strategies_for_profile(
            [valid, short_only, locked],
            "SDOT",
            finder.search_profile("Deep"),
        )
        self.assertEqual([item["id"] for item in selected], ["valid"])
        self.assertEqual(len(skipped), 2)

    def test_deep_search_estimate_is_large_for_many_families(self):
        work = finder.estimate_search_work(finder.search_profile("Deep"), 20)
        self.assertGreater(work["minimum_estimated_simulations"], 10_000)


class OptimizerLedgerTests(unittest.TestCase):
    def test_optimizer_returns_unique_exact_configuration_ledger(self):
        rows = []
        for day in (18, 19, 20, 21, 22, 23):
            for minute in range(8):
                rows.append(bar(day, minute, 10.0 + day * 0.01 + minute * 0.03))

        candidate = strategy(
            "ledger-test",
            min_day_change_pct=-50.0,
            max_hold_minutes=5,
        )
        settings = engine.BacktestSettings(
            starting_cash=10_000,
            risk_per_trade_pct=0.5,
            max_position_pct=20.0,
            allow_extended_hours=False,
        )
        optimizer = engine.OptimizationSettings(
            max_variants_per_strategy=3,
            finalists_per_strategy=1,
            minimum_training_trades=1,
            minimum_validation_trades=1,
            optimize_position_sizing=False,
            max_execution_variants_per_finalist=1,
            selection_mode="validated",
        )
        report = engine.optimize_stock_strategies(
            rows,
            [candidate],
            "TEST",
            settings,
            optimizer,
        )
        history = list(report.get("configuration_history") or [])
        self.assertGreater(len(history), 0)
        self.assertEqual(report.get("unique_configurations_tested"), len(history))
        signatures = [item.get("signature") for item in history]
        self.assertEqual(len(signatures), len(set(signatures)))
        self.assertTrue(all(item.get("rules") for item in history))
        self.assertTrue(all(item.get("settings") for item in history))


class OptimizerResumeTests(unittest.TestCase):
    def test_resume_skips_families_already_completed_in_checkpoint(self):
        rows = []
        for day in (18, 19, 20, 21, 22, 23):
            for minute in range(8):
                rows.append(bar(day, minute, 10.0 + day * 0.01 + minute * 0.03))

        candidates = [
            strategy("resume-a", min_day_change_pct=-50.0, max_hold_minutes=5),
            strategy("resume-b", min_day_change_pct=-50.0, max_hold_minutes=5),
        ]
        settings = engine.BacktestSettings(
            starting_cash=10_000,
            risk_per_trade_pct=0.5,
            max_position_pct=20.0,
            allow_extended_hours=False,
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
        captured = {}

        class StopAfterFirstFamily(RuntimeError):
            pass

        def checkpoint(state):
            captured["state"] = state
            if len(state.get("rankings") or []) == 1:
                raise StopAfterFirstFamily()

        with self.assertRaises(StopAfterFirstFamily):
            engine.optimize_stock_strategies(
                rows,
                candidates,
                "TEST",
                settings,
                optimizer,
                finalize_holdout=False,
                checkpoint=checkpoint,
            )

        raw_state = dict(captured["state"])
        durable_state = {
            **raw_state,
            "configuration_history": [],
            "configuration_count": int(raw_state.get("configuration_count") or 0),
        }
        resumed = engine.optimize_stock_strategies(
            rows,
            candidates,
            "TEST",
            settings,
            optimizer,
            finalize_holdout=False,
            resume_state=durable_state,
        )
        self.assertEqual(resumed.get("resumed_strategy_count"), 1)
        self.assertEqual(
            {item["source_strategy_id"] for item in resumed["rankings"]},
            {"resume-a", "resume-b"},
        )
        self.assertGreaterEqual(
            int(resumed.get("unique_configurations_tested") or 0),
            int(durable_state.get("configuration_count") or 0),
        )


class FinderPersistenceTests(unittest.TestCase):
    def test_merge_saves_loser_ledger_and_stock_specific_child(self):
        source = strategy("source-family", breakout_lookback_bars=20)
        report = {
            "generated_at": "2026-08-28T05:00:00+00:00",
            "symbol": "SDOT",
            "profile": {"name": "Deep"},
            "verdict": {"code": "ready_for_paper", "label": "READY FOR PAPER TESTING"},
            "winner_strategy_name": source["name"],
            "winner_source_strategy_id": source["id"],
            "timeframe": "5Min",
            "unique_configurations_tested": 2,
            "strategies_tested": 1,
            "robustness": {"score": 72, "label": "PROMISING"},
            "parameter_stability": {"positive_pct": 65},
            "walk_forward": {"summary": {"profitable_fold_pct": 75}},
            "optimization": {
                "winner": {
                    "status": "VALIDATED",
                    "optimized_rules": source["machine_rules"],
                    "optimized_backtest_settings": {},
                    "training_metrics": {"net_pnl": 100},
                    "validation_metrics": {"net_pnl": 50},
                    "holdout_metrics": {"net_pnl": 40},
                    "stress_metrics": {"net_pnl": 30},
                }
            },
            "configuration_history": [
                {
                    "signature": "winner-config",
                    "timeframe": "5Min",
                    "strategy_id": source["id"],
                    "strategy_name": source["name"],
                    "phases": ["validation"],
                    "rules": source["machine_rules"],
                    "settings": {"risk_per_trade_pct": 0.5},
                    "metrics": {"net_pnl": 40},
                },
                {
                    "signature": "loser-config",
                    "timeframe": "5Min",
                    "strategy_id": source["id"],
                    "strategy_name": source["name"],
                    "phases": ["coarse_training"],
                    "rules": {**source["machine_rules"], "reward_risk": 5.0},
                    "settings": {"risk_per_trade_pct": 0.5},
                    "metrics": {"net_pnl": -80},
                },
            ],
        }
        checkpoint = {
            "id": "sdot-deep-checkpoint",
            "symbol": "SDOT",
            "profile": "Deep",
            "status": "running",
            "started_at": "2026-08-28T04:00:00+00:00",
            "updated_at": "2026-08-28T04:30:00+00:00",
            "progress": 0.5,
            "engine_state": {
                "timeframes": {
                    "5Min": {
                        "fingerprint": "abc",
                        "rankings": [{"source_strategy_id": source["id"]}],
                        "configuration_count": 1,
                        "configuration_history": [
                            {
                                "signature": "checkpoint-config",
                                "strategy_id": source["id"],
                                "strategy_name": source["name"],
                                "phases": ["coarse_training"],
                                "rules": source["machine_rules"],
                                "settings": {"risk_per_trade_pct": 0.5},
                                "metrics": {"net_pnl": -10},
                            }
                        ],
                    }
                }
            },
        }
        checkpointed = finder.merge_finder_checkpoint_into_library(
            {"strategies": [source]},
            checkpoint,
        )
        durable_checkpoint = finder.latest_finder_checkpoint(checkpointed, "SDOT", "Deep")
        self.assertIsNotNone(durable_checkpoint)
        durable_state = durable_checkpoint["engine_state"]["timeframes"]["5Min"]
        self.assertEqual(durable_state["configuration_history"], [])
        self.assertEqual(durable_state["configuration_count"], 1)
        self.assertIn(
            "checkpoint-config",
            {item["signature"] for item in checkpointed["stock_strategy_configuration_ledger"]},
        )

        merged = finder.merge_finder_report_into_library(
            checkpointed,
            report,
        )
        ledger = merged.get("stock_strategy_configuration_ledger") or []
        self.assertEqual({item["signature"] for item in ledger}, {"winner-config", "loser-config"})
        child = next(
            item for item in merged["strategies"]
            if str(item.get("source_type") or "") == "stock_specific_finder"
        )
        self.assertEqual(child["optimized_for_symbol"], "SDOT")
        self.assertEqual(child["paper_validation_status"], "ready")
        self.assertEqual(child["validation_status"], "validated")

        restored = finder.latest_completed_finder_report(merged, "SDOT", "Deep")
        self.assertTrue(restored.get("restored_from_library"))
        self.assertEqual(restored["winner_strategy_name"], source["name"])
        self.assertEqual(restored["timeframe"], "5Min")
        self.assertEqual(restored["optimization"]["winner"]["holdout_metrics"]["net_pnl"], 40)

        completed_checkpoint = finder.latest_finder_checkpoint(merged, "SDOT", "Deep")
        self.assertEqual(completed_checkpoint["status"], "complete")
        self.assertEqual(completed_checkpoint["engine_state"], {})


if __name__ == "__main__":
    unittest.main()
