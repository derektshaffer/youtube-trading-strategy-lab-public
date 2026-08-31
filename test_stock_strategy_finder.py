from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from finder_report_persistence import newest_matching_finder_report
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
    def test_library_family_selection_excludes_prior_stock_specific_children(self):
        source = strategy("source-family")
        child = {
            **source,
            "id": "stock-child",
            "source_type": "stock_specific_finder",
            "parent_strategy_id": source["id"],
            "optimized_for_symbol": "SDOT",
        }
        canonical = {
            **strategy("canonical-family"),
            "source_type": "canonical_family",
        }

        self.assertEqual(
            [item["id"] for item in finder.stock_finder_strategy_families([source, child])],
            [source["id"]],
        )
        self.assertEqual(
            [
                item["id"]
                for item in finder.stock_finder_strategy_families(
                    [source, child, canonical]
                )
            ],
            [canonical["id"]],
        )

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

    def test_finder_excludes_strategy_when_defining_exit_is_not_modeled(self):
        incomplete = strategy("scaleout", breakout_lookback_bars=20)
        incomplete["exit_conditions"] = [
            "Take partial profit on the first push and scale out into strength."
        ]
        selected, skipped = finder.selected_strategies_for_profile(
            [incomplete],
            "SDOT",
            finder.search_profile("Deep"),
        )
        self.assertEqual(selected, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("fidelity audit failed", skipped[0])
        self.assertIn("Scale-out", skipped[0])

    def test_current_regime_search_uses_recent_history_without_family_cap(self):
        profile = finder.search_profile("Current Regime")
        self.assertEqual(profile.history_days, 35)
        self.assertEqual(profile.timeframes, ("1Min", "5Min", "15Min"))
        self.assertIsNone(profile.quick_family_limit)
        candidates = [
            strategy("recent-a", breakout_lookback_bars=10),
            strategy("recent-b", min_relative_volume=2.0),
        ]
        selected, skipped = finder.selected_strategies_for_profile(
            candidates,
            "SDOT",
            profile,
        )
        self.assertEqual({item["id"] for item in selected}, {"recent-a", "recent-b"})
        self.assertFalse(skipped)

    def test_spread_rule_fails_closed_until_historical_quotes_are_enforced(self):
        spread_strategy = strategy(
            "spread-gated",
            breakout_lookback_bars=10,
            max_spread_pct=0.5,
        )
        selected, skipped = finder.selected_strategies_for_profile(
            [spread_strategy],
            "TEST",
            finder.search_profile("Deep"),
        )
        self.assertEqual(selected, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("historical bid/ask quotes", skipped[0])
        self.assertIn("not accepted as a substitute", skipped[0])

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


class FinderEvidenceTierTests(unittest.TestCase):
    @staticmethod
    def optimization_with_pnls(training, validation, holdout, stress):
        return {
            "winner": {
                "training_metrics": {"trade_count": 10, "net_pnl": training},
                "validation_metrics": {"trade_count": 5, "net_pnl": validation},
                "holdout_metrics": {"trade_count": 5, "net_pnl": holdout},
                "stress_metrics": {"trade_count": 5, "net_pnl": stress},
            }
        }

    def test_profitable_candidate_is_visible_without_being_called_validated(self):
        verdict = finder.finder_evidence_verdict(
            {"score": 42, "independently_positive": False},
            {"positive_pct": 25},
            {"summary": {"profitable_fold_pct": 25}},
            self.optimization_with_pnls(120, -20, 35, -10),
        )
        self.assertEqual(verdict["code"], "historical_candidate")
        self.assertEqual(verdict["research_tier"], "historical_candidate")
        self.assertFalse(verdict["paper_ready"])

    def test_promising_candidate_remains_below_validated_tier(self):
        verdict = finder.finder_evidence_verdict(
            {"score": 56, "independently_positive": False},
            {"positive_pct": 45},
            {"summary": {"profitable_fold_pct": 50}},
            self.optimization_with_pnls(100, 20, 15, -5),
        )
        self.assertEqual(verdict["code"], "promising")
        self.assertFalse(verdict["paper_ready"])

    def test_ready_for_paper_keeps_strict_existing_gate(self):
        verdict = finder.finder_evidence_verdict(
            {"score": 70, "independently_positive": True},
            {"positive_pct": 60},
            {"summary": {"profitable_fold_pct": 50}},
            self.optimization_with_pnls(100, 20, 15, 10),
        )
        self.assertEqual(verdict["code"], "ready_for_paper")
        self.assertEqual(verdict["research_tier"], "validated")
        self.assertTrue(verdict["paper_ready"])

    def test_paper_execution_gap_downgrades_ready_verdict_consistently(self):
        verdict = finder.apply_paper_fidelity_to_verdict(
            {
                "code": "ready_for_paper",
                "research_tier": "validated",
                "paper_ready": True,
            },
            {"status": "gap"},
        )
        self.assertEqual(verdict["code"], "historically_robust_execution_gap")
        self.assertFalse(verdict["paper_ready"])

    def test_validated_status_separates_historical_evidence_from_paper_fidelity(self):
        ready = {
            "code": "ready_for_paper",
            "research_tier": "validated",
            "paper_ready": True,
        }
        execution_gap = {
            "code": "historically_robust_execution_gap",
            "research_tier": "historically_robust_execution_gap",
            "paper_ready": False,
        }
        self.assertFalse(
            finder.validated_status_ready(
                ready,
                {"status": "ready"},
                None,
            )
        )
        self.assertTrue(
            finder.validated_status_ready(
                ready,
                {"status": "ready"},
                {"summary": {"profitable_fold_pct": 75}},
            )
        )
        self.assertTrue(
            finder.validated_status_ready(
                execution_gap,
                {"status": "blocked"},
                {"summary": {"profitable_fold_pct": 75}},
            )
        )
        self.assertFalse(
            finder.validated_status_ready(
                {"code": "promising", "paper_ready": False},
                {"status": "ready"},
                {"summary": {"profitable_fold_pct": 75}},
            )
        )

    def test_regime_diagnostics_are_descriptive_and_use_frozen_winner(self):
        rows = []
        for day in (18, 19, 20, 21, 22, 23):
            for minute in range(8):
                rows.append(bar(day, minute, 10.0 + day * 0.01 + minute * 0.03))
        candidate = strategy(
            "regime-test",
            min_day_change_pct=-50.0,
            max_hold_minutes=5,
        )
        report = {
            "symbol": "TEST",
            "timeframe": "1Min",
            "backtest_settings": {},
            "winner": {
                "optimized_rules": candidate["machine_rules"],
                "optimized_backtest_settings": {},
            },
        }
        diagnostics = finder.regime_diagnostics(rows, candidate, report)
        self.assertEqual(diagnostics["status"], "complete")
        self.assertEqual(diagnostics["timeframe"], "1Min")
        self.assertGreaterEqual(len(diagnostics["windows"]), 1)
        self.assertIn("descriptive", diagnostics["note"])
        self.assertTrue(
            all("metrics" in item for item in diagnostics["windows"])
        )


class ParameterStabilityIntegrityTests(unittest.TestCase):
    def test_stability_keeps_pre_holdout_history_for_indicator_warmup(self):
        rows = []
        for day in (18, 19, 20, 21):
            for minute in range(4):
                rows.append(bar(day, minute, 10.0 + day * 0.01 + minute * 0.02))
        candidate = strategy("warmup-test", fast_ema_period=9)
        report = {
            "symbol": "TEST",
            "timeframe": "1Min",
            "holdout_sessions": ["2026-08-21"],
            "backtest_settings": {},
            "winner": {
                "optimized_rules": candidate["machine_rules"],
                "optimized_backtest_settings": {},
            },
        }
        fake_full_result = {"trades": [], "metrics": {}}
        holdout_metrics = {
            "trade_count": 2,
            "net_pnl": 10.0,
            "profit_factor": 1.5,
            "max_drawdown_pct": 1.0,
        }

        with patch.object(
            finder,
            "generate_local_strategy_refinements",
            return_value=[],
        ), patch.object(
            finder,
            "run_backtest",
            return_value=fake_full_result,
        ) as mocked_backtest, patch.object(
            finder,
            "_period_metrics",
            return_value=holdout_metrics,
        ) as mocked_period:
            result = finder.parameter_stability_test(
                rows,
                candidate,
                report,
                maximum=1,
            )

        backtest_rows = mocked_backtest.call_args.args[0]
        self.assertEqual(len(backtest_rows), len(rows))
        self.assertGreater(
            len(backtest_rows),
            len(finder._rows_for_sessions(rows, ["2026-08-21"])),
        )
        mocked_period.assert_called_once()
        self.assertEqual(mocked_period.call_args.args[1], {"2026-08-21"})
        self.assertEqual(result["positive_pct"], 100.0)
        self.assertIn("full pre-holdout causal warmup", result["note"])


class HistoricalSpreadIntegrityTests(unittest.TestCase):
    def test_under_modeled_real_spreads_revoke_paper_ready_verdict(self):
        report = {
            "verdict": {
                "code": "ready_for_paper",
                "label": "READY FOR PAPER TESTING",
                "paper_ready": True,
            },
            "robustness": {
                "score": 78.0,
                "label": "PROMISING",
                "independently_positive": True,
                "reasons": [],
            },
            "optimization": {
                "winner": {
                    "status": "VALIDATED",
                }
            },
        }
        audit = {
            "status": "UNDERMODELED",
            "p90_observed_spread_bps": 35.0,
            "tested_spread_ceiling_bps": 24.0,
        }

        guarded = finder.apply_historical_spread_integrity_guard(report, audit)

        self.assertEqual(
            guarded["optimization"]["winner"]["status"],
            "HOLDOUT SPREAD UNDERMODELED",
        )
        self.assertFalse(guarded["robustness"]["independently_positive"])
        self.assertLessEqual(guarded["robustness"]["score"], 49.0)
        self.assertEqual(
            guarded["verdict"]["code"],
            "historical_spread_under_modeled",
        )
        self.assertFalse(guarded["verdict"]["paper_ready"])
        self.assertEqual(guarded["historical_spread_audit"], audit)

    def test_covered_real_spreads_do_not_change_verdict(self):
        report = {
            "verdict": {"code": "ready_for_paper", "paper_ready": True},
            "robustness": {"score": 75.0, "independently_positive": True},
            "optimization": {"winner": {"status": "VALIDATED"}},
        }
        audit = {"status": "COVERED"}
        guarded = finder.apply_historical_spread_integrity_guard(report, audit)
        self.assertEqual(guarded["optimization"]["winner"]["status"], "VALIDATED")
        self.assertEqual(guarded["verdict"]["code"], "ready_for_paper")
        self.assertTrue(guarded["robustness"]["independently_positive"])


class HoldoutReuseIntegrityTests(unittest.TestCase):
    def _report(self):
        candidate = strategy("reuse-family", breakout_lookback_bars=10)
        return {
            "generated_at": "2026-08-30T10:00:00+00:00",
            "symbol": "TEST",
            "profile": {"name": "Deep"},
            "timeframe": "5Min",
            "winner_source_strategy_id": candidate["id"],
            "winner_strategy_name": candidate["name"],
            "verdict": {
                "code": "ready_for_paper",
                "label": "READY FOR PAPER TESTING",
                "research_tier": "validated",
                "paper_ready": True,
            },
            "robustness": {
                "score": 78.0,
                "label": "PROMISING",
                "independently_positive": True,
                "reasons": [],
            },
            "paper_execution_fidelity": {"status": "ready"},
            "walk_forward": {"summary": {"profitable_fold_pct": 75.0}},
            "optimization": {
                "holdout_sessions": [
                    "2026-08-25",
                    "2026-08-26",
                    "2026-08-27",
                    "2026-08-28",
                ],
                "winner": {
                    "status": "VALIDATED",
                    "optimized_rules": candidate["machine_rules"],
                    "optimized_backtest_settings": {},
                    "training_metrics": {"net_pnl": 100.0},
                    "validation_metrics": {"net_pnl": 40.0},
                    "holdout_metrics": {"net_pnl": 30.0},
                    "stress_metrics": {"net_pnl": 20.0},
                },
            },
        }

    def test_first_saved_holdout_is_pristine(self):
        report = finder.apply_holdout_reuse_guard({}, self._report())
        self.assertTrue(report["holdout_reuse_audit"]["pristine"])
        self.assertEqual(report["verdict"]["code"], "ready_for_paper")
        self.assertEqual(report["optimization"]["winner"]["status"], "VALIDATED")

    def test_any_prior_holdout_session_breaks_pristine_status(self):
        report = self._report()
        prior = {
            "id": "one-session-exposure",
            "symbol": "TEST",
            "timeframe": "1Min",
            "holdout_sessions": ["2026-08-25"],
        }
        guarded = finder.apply_holdout_reuse_guard(
            {"holdout_exposure_ledger": [prior]},
            report,
        )
        self.assertFalse(guarded["holdout_reuse_audit"]["pristine"])
        self.assertEqual(
            guarded["holdout_reuse_audit"]["prior_exposures"][0]["overlap_sessions"],
            ["2026-08-25"],
        )

    def test_record_holdout_exposure_does_not_require_validation_save(self):
        report = self._report()
        stored = finder.record_holdout_exposure(
            {},
            report,
            source="manual_strategy_lab",
            generated_at="2026-08-30T20:00:00Z",
        )
        ledger = stored["holdout_exposure_ledger"]
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["symbol"], "TEST")
        self.assertEqual(ledger[0]["source"], "manual_strategy_lab")
        self.assertEqual(
            ledger[0]["holdout_sessions"],
            ["2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"],
        )

    def test_materially_reused_holdout_cannot_remain_validated(self):
        report = self._report()
        prior = {
            "id": "prior-run",
            "generated_at": "2026-08-29T10:00:00+00:00",
            "symbol": "TEST",
            "profile": "Current Regime",
            "timeframe": "1Min",
            "holdout_sessions": [
                "2026-08-25",
                "2026-08-26",
                "2026-08-27",
            ],
        }
        guarded = finder.apply_holdout_reuse_guard(
            {"stock_strategy_finder_runs": [prior]},
            report,
        )
        self.assertFalse(guarded["holdout_reuse_audit"]["pristine"])
        self.assertEqual(
            guarded["holdout_reuse_audit"]["prior_material_exposure_count"],
            1,
        )
        self.assertEqual(guarded["verdict"]["code"], "holdout_reused")
        self.assertFalse(guarded["verdict"]["paper_ready"])
        self.assertFalse(guarded["robustness"]["independently_positive"])
        self.assertEqual(
            guarded["optimization"]["winner"]["status"],
            "HOLDOUT REUSED",
        )


class FinderPersistenceTests(unittest.TestCase):
    def test_merge_saves_loser_ledger_and_stock_specific_child(self):
        source = strategy("source-family", breakout_lookback_bars=20)
        source["approved"] = True
        source["validation_status"] = "validated"
        source["validated_rules"] = {
            **source["machine_rules"],
            "reward_risk": 9.0,
        }
        report = {
            "generated_at": "2026-08-28T05:00:00+00:00",
            "strategy_fidelity_engine_version": 1,
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
            "regime_diagnostics": {
                "status": "complete",
                "timeframe": "5Min",
                "windows": [
                    {
                        "label": "Recent regime",
                        "session_count": 20,
                        "start_session": "2026-08-01",
                        "end_session": "2026-08-28",
                        "metrics": {"trade_count": 8, "net_pnl": 22},
                    }
                ],
            },
            "paper_execution_fidelity": {
                "status": "ready",
                "label": "PAPER EXECUTION COMPATIBLE",
            },
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
                    "execution_sensitivity": {
                        "score": 82.0,
                        "label": "ROBUST",
                        "passes_validation_gate": True,
                    },
                    "holdout_execution_sensitivity": {
                        "score": 76.0,
                        "label": "ROBUST",
                        "passes_validation_gate": True,
                    },
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
        self.assertEqual(
            {item["signature"] for item in ledger},
            {"checkpoint-config", "winner-config", "loser-config"},
        )
        child = next(
            item for item in merged["strategies"]
            if str(item.get("source_type") or "") == "stock_specific_finder"
        )
        self.assertEqual(child["optimized_for_symbol"], "SDOT")
        self.assertEqual(child["paper_validation_status"], "ready")
        self.assertEqual(child["validation_status"], "validated")
        self.assertFalse(child["approved"])
        self.assertEqual(child["validated_rules"], source["machine_rules"])
        self.assertNotEqual(child["validated_rules"], source["validated_rules"])
        self.assertEqual(
            child["validated_backtest_settings"],
            report["optimization"]["winner"]["optimized_backtest_settings"],
        )

        restored = finder.latest_completed_finder_report(merged, "SDOT", "Deep")
        self.assertTrue(restored.get("restored_from_library"))
        self.assertEqual(restored["winner_strategy_name"], source["name"])
        self.assertEqual(
            restored["stock_specific_strategy_id"],
            child["id"],
        )
        self.assertEqual(restored["paper_validation_status"], "ready")
        self.assertEqual(restored["timeframe"], "5Min")
        self.assertEqual(restored["strategy_fidelity_engine_version"], 1)
        self.assertEqual(restored["paper_execution_fidelity"]["status"], "ready")
        self.assertEqual(
            restored["regime_diagnostics"]["windows"][0]["label"],
            "Recent regime",
        )
        self.assertEqual(restored["optimization"]["winner"]["holdout_metrics"]["net_pnl"], 40)
        self.assertEqual(
            restored["optimization"]["winner"]["execution_sensitivity"]["score"],
            82.0,
        )
        self.assertEqual(
            restored["optimization"]["winner"]["holdout_execution_sensitivity"]["score"],
            76.0,
        )

        completed_checkpoint = finder.latest_finder_checkpoint(merged, "SDOT", "Deep")
        self.assertEqual(completed_checkpoint["status"], "complete")
        self.assertEqual(completed_checkpoint["engine_state"], {})

        failed_report = deepcopy(report)
        failed_report["generated_at"] = "2026-08-28T06:00:00+00:00"
        failed_report["verdict"] = {
            "code": "historical_candidate",
            "label": "HISTORICALLY PROFITABLE CANDIDATE — NOT VALIDATED",
        }
        failed = finder.merge_finder_report_into_library(merged, failed_report)
        updated_child = next(
            item
            for item in failed["strategies"]
            if str(item.get("id") or "") == child["id"]
        )
        self.assertEqual(updated_child["validation_status"], "research_only")
        self.assertEqual(updated_child["paper_validation_status"], "not_ready")
        self.assertFalse(updated_child["approved"])
        self.assertNotIn("validated_rules", updated_child)
        self.assertNotIn("validated_backtest_settings", updated_child)
        self.assertNotIn("validated_at", updated_child)

    def test_newer_durable_result_replaces_matching_stale_session_result(self):
        session_report = {
            "symbol": "SDOT",
            "profile": {"name": "Deep"},
            "generated_at": "2026-08-28T05:00:00+00:00",
            "winner_strategy_name": "Old local winner",
        }
        saved_report = {
            "symbol": "SDOT",
            "profile": {"name": "Deep"},
            "generated_at": "2026-08-28T06:00:00+00:00",
            "winner_strategy_name": "New cloud winner",
        }
        chosen = newest_matching_finder_report(
            session_report,
            saved_report,
            "SDOT",
            "Deep",
        )
        self.assertEqual(chosen["winner_strategy_name"], "New cloud winner")

    def test_durable_result_wins_an_exact_timestamp_tie(self):
        session_report = {
            "symbol": "SDOT",
            "profile": {"name": "Current Regime"},
            "generated_at": "2026-08-28T05:00:00Z",
            "restored_from_library": False,
        }
        saved_report = {
            **session_report,
            "restored_from_library": True,
        }
        chosen = newest_matching_finder_report(
            session_report,
            saved_report,
            "SDOT",
            "Current Regime",
        )
        self.assertTrue(chosen["restored_from_library"])


if __name__ == "__main__":
    unittest.main()
