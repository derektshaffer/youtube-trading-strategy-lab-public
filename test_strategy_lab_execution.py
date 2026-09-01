import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import strategy_lab_execution as execution
import youtube_strategy_engine as engine


class FakeMarket:
    historical_feed = "iex"
    live_feed = "iex"

    def bars(self, symbols, **kwargs):
        progress = kwargs.get("progress")
        if progress:
            progress(1)
        return {symbols[0]: [{"t": "2026-08-31T14:00:00Z", "c": 10.0}]}

    def research_reset_actions(self, *_args, **_kwargs):
        return []


class MemoryMainStore:
    def __init__(self):
        self.data = {"validation_runs": []}
        self.saved = False

    def load_latest(self):
        return self.data

    def save(self, data):
        self.data = data
        self.saved = True
        return data


class StrategyLabExecutionTests(unittest.TestCase):
    def test_real_optimizer_completes_quick_and_very_deep_profiles(self):
        rows = []
        start = datetime(2026, 8, 18, 13, 30, tzinfo=timezone.utc)
        for day in range(8):
            for minute in range(12):
                close = 10.0 + day * 0.04 + minute * 0.03
                rows.append(
                    {
                        "t": (start + timedelta(days=day, minutes=minute)).isoformat(),
                        "o": close - 0.02,
                        "h": close + 0.08,
                        "l": close - 0.07,
                        "c": close,
                        "v": 1200 + minute * 80,
                    }
                )
        strategy = {
            "id": "depth-smoke",
            "name": "Depth smoke",
            "direction": "long",
            "machine_rules": engine.normalize_machine_rules(
                {
                    "min_price": 1.0,
                    "min_day_change_pct": -50.0,
                    "min_relative_volume": 0.1,
                    "breakout_lookback_bars": 2,
                    "stop_loss_pct": 5.0,
                    "reward_risk": 1.0,
                }
            ),
        }
        settings = engine.BacktestSettings(
            starting_cash=2000.0,
            risk_per_trade_pct=5.0,
            max_position_pct=100.0,
            spread_bps=0.0,
            slippage_bps=0.0,
        )
        reports = {}
        for depth in (12, 160):
            optimizer = engine.OptimizationSettings(
                max_variants_per_strategy=depth,
                finalists_per_strategy=1,
                minimum_training_trades=1,
                minimum_validation_trades=1,
                optimize_position_sizing=False,
                max_execution_variants_per_finalist=1,
                automatic_slippage=False,
                selection_mode="validated",
            )
            checkpoints = []
            reports[depth] = engine.optimize_stock_strategies(
                rows,
                [strategy],
                "SLS",
                settings,
                optimizer,
                finalize_holdout=True,
                checkpoint=lambda state: checkpoints.append(state),
            )
            self.assertEqual(
                reports[depth]["optimization_settings"]["max_variants_per_strategy"],
                depth,
            )
            self.assertTrue(reports[depth].get("winner"))
            self.assertTrue(reports[depth].get("winning_backtest"))
            self.assertEqual(
                checkpoints[-1]["completed_strategy_ids"],
                ["depth-smoke"],
            )
        self.assertGreater(
            reports[160]["variants_tested"],
            reports[12]["variants_tested"],
        )

    def test_quick_and_very_deep_use_the_requested_optimizer_depth(self):
        for depth in (12, 160):
            with self.subTest(depth=depth):
                observed = {}
                stages = []
                main_store = MemoryMainStore()

                def fake_optimize(
                    rows,
                    candidates,
                    ticker,
                    backtest_settings,
                    optimization_settings,
                    **kwargs,
                ):
                    observed["depth"] = optimization_settings.max_variants_per_strategy
                    observed["resume"] = kwargs.get("resume_state")
                    kwargs["progress"](1, 2, "Testing variants")
                    kwargs["checkpoint"](
                        {"completed_strategy_ids": ["strategy-one"], "rankings": []}
                    )
                    return {
                        "generated_at": "2026-09-01T12:00:00+00:00",
                        "holdout_sessions": ["2026-08-31"],
                        "optimization_settings": {
                            "execution_sensitivity_multipliers": [1.25, 2.0]
                        },
                        "winning_backtest": {"trades": []},
                        "winner": {
                            "source_strategy_id": "strategy-one",
                            "strategy_name": "Strategy One",
                            "optimized_rules": {"above_vwap": True},
                            "optimized_backtest_settings": {"spread_bps": 12.0},
                        },
                        "variants_tested": depth,
                    }

                def add_reuse_audit(_library, wrapper):
                    return {**wrapper, "holdout_reuse_audit": {"pristine": True}}

                job = {
                    "ticker": "SLS",
                    "timeframe": "5Min",
                    "history_days": 30,
                    "search_depth": depth,
                    "starting_cash": 2000.0,
                    "risk_per_trade": 5.0,
                    "max_position": 100.0,
                    "max_drawdown": 15.0,
                    "training_fraction": 0.60,
                    "validation_fraction": 0.20,
                    "minimum_training_trades": 5,
                    "minimum_validation_trades": 2,
                    "run_walk_forward": False,
                    "compared_all": False,
                    "candidates": [
                        {
                            "id": "strategy-one",
                            "name": "Strategy One",
                            "direction": "long",
                            "machine_rules": {"above_vwap": True},
                        }
                    ],
                }
                with (
                    patch.object(
                        execution,
                        "split_safe_raw_research_rows",
                        return_value=([{"t": "2026-08-31T14:00:00Z", "c": 10.0}], {}),
                    ),
                    patch.object(execution, "optimize_stock_strategies", side_effect=fake_optimize),
                    patch.object(
                        execution,
                        "validation_strength",
                        return_value={"score": 50.0, "label": "Exploratory"},
                    ),
                    patch.object(execution, "historical_entry_spread_audit", return_value={}),
                    patch.object(
                        execution,
                        "apply_historical_spread_integrity_guard",
                        side_effect=lambda wrapper, _audit: wrapper,
                    ),
                    patch.object(
                        execution,
                        "apply_holdout_reuse_guard",
                        side_effect=add_reuse_audit,
                    ),
                    patch.object(
                        execution,
                        "record_holdout_exposure",
                        side_effect=lambda library, *_args, **_kwargs: library,
                    ),
                    patch.object(
                        execution,
                        "finder_evidence_verdict",
                        return_value={"code": "research_only"},
                    ),
                    patch.object(execution, "paper_execution_fidelity", return_value={}),
                    patch.object(
                        execution,
                        "apply_paper_fidelity_to_verdict",
                        side_effect=lambda verdict, _fidelity: verdict,
                    ),
                ):
                    result = execution.execute_strategy_lab_run(
                        job,
                        market=FakeMarket(),
                        main_store=main_store,
                        progress=lambda _fraction, stage, _message: stages.append(stage),
                        optimizer_resume_state={"fingerprint": "saved"},
                        optimizer_checkpoint=lambda state: observed.setdefault(
                            "checkpoint", state
                        ),
                    )

                self.assertEqual(observed["depth"], depth)
                self.assertEqual(observed["resume"], {"fingerprint": "saved"})
                self.assertEqual(
                    observed["checkpoint"]["completed_strategy_ids"],
                    ["strategy-one"],
                )
                self.assertEqual(result["report"]["variants_tested"], depth)
                self.assertTrue(main_store.saved)
                self.assertIn("optimization", stages)
                self.assertEqual(stages[-1], "complete")


if __name__ == "__main__":
    unittest.main()
