# Regression test contract audit

The frozen A/B/C matrix was completed at 2026-09-14T02:16:30.050233+00:00 before any test repair. All three Ubuntu/Python 3.12.14 states have the same 2,047 passes, 37 failures and 3 skips with identical dependency freezes. Neither #123 nor #124 introduces a failure.

Application source is unchanged. The current native boundary requires an owned exact scope. Canonical Job 2 execution-gate report (2026-09-13), section 6, explicitly preserves the previous preliminary/V5 guard when no retrospective context exists. The unpublished retrospective implementation is not added to this PR. No private token, real certificate, capability or guard bypass is introduced.

The numerical assertions retired below were unreachable through the now-disabled legacy route. Refusal tests do not prove those numerical behaviors. Six orchestration tests retain their assertions with a generated empty-result dependency; these are neither numerical validation nor certification. Existing approved PreliminaryResearch positive/losing/no-trade, bounded-scope and restart/replay tests remain unchanged.

## test_avwap_integration.py::AvwapIntegrationTests::test_session_open_avwap_is_available_to_backtest

**OLD EXPECTATION:**

```python
self.assertTrue(frame["avwap"].notna().any())
self.assertTrue(frame["avwap_anchor_active"].fillna(False).any())
self.assertIn("metrics", report)
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_avwap_integration.py::AvwapIntegrationTests::test_session_open_avwap_features_do_not_authorize_backtest`.

## test_optimizer_feature_reuse.py::test_prepared_record_payload_is_backtest_equivalent

**OLD EXPECTATION:**

```python
assert reused["metrics"] == baseline["metrics"]
assert reused["trades"] == baseline["trades"]
assert reused["equity_curve"] == baseline["equity_curve"]
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_optimizer_feature_reuse.py::test_prepared_record_payload_does_not_authorize_backtest`.

## test_stock_strategy_finder.py::OptimizerLedgerTests::test_optimizer_returns_unique_exact_configuration_ledger

**OLD EXPECTATION:**

```python
self.assertGreater(len(history), 0)
self.assertEqual(report.get("unique_configurations_tested"), len(history))
self.assertEqual(len(signatures), len(set(signatures)))
self.assertTrue(all(item.get("rules") for item in history))
self.assertTrue(all(item.get("settings") for item in history))
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Preserve all original orchestration, configuration-ledger, checkpoint/resume or result-projection assertions, using the explicitly generated empty numerical-result stub. The stub sets numerical_execution/certified/production_eligible/orders_enabled false; it grants no engine authority.

Replacement: `test_stock_strategy_finder.py::OptimizerLedgerTests::test_optimizer_stub_returns_unique_exact_configuration_ledger`.

## test_stock_strategy_finder.py::OptimizerResumeTests::test_resume_skips_families_already_completed_in_checkpoint

**OLD EXPECTATION:**

```python
self.assertEqual(resumed.get("resumed_strategy_count"), 1)
self.assertEqual(
            {item["source_strategy_id"] for item in resumed["rankings"]},
            {"resume-a", "resume-b"},
        )
self.assertGreaterEqual(
            int(resumed.get("unique_configurations_tested") or 0),
            int(durable_state.get("configuration_count") or 0),
        )
self.assertRaises(StopAfterFirstFamily)
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Preserve all original orchestration, configuration-ledger, checkpoint/resume or result-projection assertions, using the explicitly generated empty numerical-result stub. The stub sets numerical_execution/certified/production_eligible/orders_enabled false; it grants no engine authority.

Replacement: `test_stock_strategy_finder.py::OptimizerResumeTests::test_resume_stub_skips_families_already_completed_in_checkpoint`.

## test_stock_strategy_finder.py::FinderEvidenceTierTests::test_regime_diagnostics_are_descriptive_and_use_frozen_winner

**OLD EXPECTATION:**

```python
self.assertEqual(diagnostics["status"], "complete")
self.assertEqual(diagnostics["timeframe"], "1Min")
self.assertGreaterEqual(len(diagnostics["windows"]), 1)
self.assertIn("descriptive", diagnostics["note"])
self.assertTrue(
            all("metrics" in item for item in diagnostics["windows"])
        )
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Preserve all original orchestration, configuration-ledger, checkpoint/resume or result-projection assertions, using the explicitly generated empty numerical-result stub. The stub sets numerical_execution/certified/production_eligible/orders_enabled false; it grants no engine authority.

Replacement: `test_stock_strategy_finder.py::FinderEvidenceTierTests::test_regime_stub_diagnostics_are_descriptive_and_use_frozen_winner`.

## test_strategy_lab_execution.py::StrategyLabExecutionTests::test_real_optimizer_completes_quick_and_very_deep_profiles

**OLD EXPECTATION:**

```python
self.assertGreater(
            reports[160]["variants_tested"],
            reports[12]["variants_tested"],
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
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Preserve all original orchestration, configuration-ledger, checkpoint/resume or result-projection assertions, using the explicitly generated empty numerical-result stub. The stub sets numerical_execution/certified/production_eligible/orders_enabled false; it grants no engine authority.

Replacement: `test_strategy_lab_execution.py::StrategyLabExecutionTests::test_optimizer_orchestration_with_stub_completes_requested_profiles`.

## test_trading_universe_research.py::UniverseResearchTests::test_report_preserves_symbol_count_and_frozen_rules

**OLD EXPECTATION:**

```python
self.assertEqual(report["symbols_tested"], 2)
self.assertTrue(report["using_validated_rules"])
self.assertIn("score", report["summary"])
self.assertEqual(len(report["results"]), 2)
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Preserve all original orchestration, configuration-ledger, checkpoint/resume or result-projection assertions, using the explicitly generated empty numerical-result stub. The stub sets numerical_execution/certified/production_eligible/orders_enabled false; it grants no engine authority.

Replacement: `test_trading_universe_research.py::UniverseResearchTests::test_stub_report_preserves_symbol_count_and_frozen_rules`.

## test_youtube_strategy_engine.py::ParallelOptimizerTests::test_distributed_family_and_timeframe_merge_matches_single_process

**OLD EXPECTATION:**

```python
self.assertEqual(distributed["timeframe"], expected["timeframe"])
self.assertEqual(
            distributed["winner"]["source_strategy_id"],
            expected["winner"]["source_strategy_id"],
        )
self.assertEqual(
            distributed["unique_configurations_tested"],
            expected["unique_configurations_tested"],
        )
self.assertEqual(
            distributed["winner"]["holdout_metrics"],
            expected["winner"]["holdout_metrics"],
        )
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Preserve all original orchestration, configuration-ledger, checkpoint/resume or result-projection assertions, using the explicitly generated empty numerical-result stub. The stub sets numerical_execution/certified/production_eligible/orders_enabled false; it grants no engine authority.

Replacement: `test_youtube_strategy_engine.py::ParallelOptimizerTests::test_distributed_stub_ledger_matches_single_process_aggregation`.

## test_youtube_strategy_engine.py::ParallelOptimizerTests::test_parallel_family_optimizer_matches_sequential_ranking

**OLD EXPECTATION:**

```python
self.assertEqual(
            [item["source_strategy_id"] for item in parallel["rankings"]],
            [item["source_strategy_id"] for item in sequential["rankings"]],
        )
self.assertEqual(
            parallel["winner"]["source_strategy_id"],
            sequential["winner"]["source_strategy_id"],
        )
self.assertEqual(parallel["strategies_tested"], sequential["strategies_tested"])
self.assertEqual(parallel["unique_configurations_tested"], sequential["unique_configurations_tested"])
self.assertEqual(parallel["parallelized_by"], "strategy_family")
self.assertGreaterEqual(parallel["parallel_workers"], 2)
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::ParallelOptimizerTests::test_unscoped_sequential_parallel_and_timeframe_optimizers_are_rejected`.

## test_youtube_strategy_engine.py::BacktestTests::test_adverse_opening_gap_uses_gap_price

**OLD EXPECTATION:**

```python
self.assertEqual(result["trades"][0]["exit_price"], 90.0)
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::BacktestTests::test_opening_gap_rows_do_not_authorize_legacy_execution`.

## test_youtube_strategy_engine.py::BacktestTests::test_costs_reduce_return

**OLD EXPECTATION:**

```python
self.assertGreater(free["metrics"]["net_pnl"], expensive["metrics"]["net_pnl"])
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::BacktestTests::test_cost_settings_do_not_authorize_legacy_execution`.

## test_youtube_strategy_engine.py::BacktestTests::test_entry_uses_next_bar_open

**OLD EXPECTATION:**

```python
self.assertEqual(result["trades"][0]["entry_price"], 103.0)
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::BacktestTests::test_next_bar_entry_rows_do_not_authorize_legacy_execution`.

## test_youtube_strategy_engine.py::BacktestTests::test_extended_hours_can_trade_at_reduced_size

**OLD EXPECTATION:**

```python
self.assertEqual(extended["metrics"]["trade_count"], 1)
self.assertEqual(extended["trades"][0]["entry_session_type"], "extended")
self.assertEqual(regular_only["metrics"]["trade_count"], 0)
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::BacktestTests::test_extended_hours_settings_do_not_authorize_legacy_execution`.

## test_youtube_strategy_engine.py::BacktestTests::test_holdout_is_chronological

**OLD EXPECTATION:**

```python
self.assertEqual(result["holdout_start"], "2026-08-20")
self.assertGreater(result["out_of_sample"]["trade_count"], 0)
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::BacktestTests::test_partitioned_rows_do_not_authorize_legacy_holdout_execution`.

## test_youtube_strategy_engine.py::BacktestTests::test_layered_entries_can_overlap_without_multiplying_total_allocation

**OLD EXPECTATION:**

```python
self.assertEqual(result["metrics"]["trade_count"], 3)
self.assertEqual([trade["trade_id"] for trade in result["trades"]], [1, 2, 3])
self.assertLessEqual(
            sum(trade["entry_price"] * trade["quantity"] for trade in result["trades"]),
            settings.starting_cash * settings.max_position_pct / 100.0 + 1,
        )
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::BacktestTests::test_layered_entry_settings_do_not_authorize_legacy_execution`.

## test_youtube_strategy_engine.py::BacktestTests::test_price_band_can_unlock_momentum_continuation_above_max

**OLD EXPECTATION:**

```python
self.assertEqual(unlocked["metrics"]["trade_count"], 1)
self.assertEqual(locked["metrics"]["trade_count"], 0)
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::BacktestTests::test_price_extension_settings_do_not_authorize_legacy_execution`.

## test_youtube_strategy_engine.py::BacktestTests::test_pullback_strategy_requires_pullback_then_breakout

**OLD EXPECTATION:**

```python
self.assertEqual(result["metrics"]["trade_count"], 1)
self.assertEqual((entry_time.hour, entry_time.minute), (9, 34))
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::BacktestTests::test_pullback_rules_do_not_authorize_legacy_execution`.

## test_youtube_strategy_engine.py::BacktestTests::test_same_bar_stop_and_target_uses_conservative_stop

**OLD EXPECTATION:**

```python
self.assertEqual(result["trades"][0]["reason"], "Stop loss")
self.assertLess(result["trades"][0]["pnl"], 0)
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::BacktestTests::test_ambiguous_exit_rows_do_not_authorize_legacy_execution`.

## test_youtube_strategy_engine.py::BacktestTests::test_short_strategy_is_rejected

**OLD EXPECTATION:**

```python
self.assertRaisesRegex(engine.AppError, "Short-only")
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::BacktestTests::test_execution_scope_is_required_before_short_strategy_validation`.

## test_youtube_strategy_engine.py::BacktestTests::test_strategy_end_time_can_be_ignored

**OLD EXPECTATION:**

```python
self.assertGreater(ignored["metrics"]["trade_count"], 0)
self.assertEqual(respected["metrics"]["trade_count"], 0)
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::BacktestTests::test_session_end_settings_do_not_authorize_legacy_execution`.

## test_youtube_strategy_engine.py::StreamlitSmokeTests::test_complete_dashboard_renders_saved_strategy_backtest_scan_and_positions

**OLD EXPECTATION:**

```python
self.assertTrue(any(name == "markdown" for name, _ in fake_streamlit.rendered))
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Preserve dashboard rendering assertions, but construct a generated inert saved-result fixture instead of running a strategy during UI setup.

Replacement: `test_youtube_strategy_engine.py::StreamlitSmokeTests::test_complete_dashboard_renders_saved_strategy_backtest_scan_and_positions`.

## test_youtube_strategy_engine.py::DynamicExitBacktestTests::test_breakeven_rule_moves_stop_after_r_trigger

**OLD EXPECTATION:**

```python
self.assertEqual(trade["reason"], "Stop loss")
self.assertAlmostEqual(trade["pnl"], 0.0, places=6)
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::DynamicExitBacktestTests::test_breakeven_rules_do_not_authorize_legacy_execution`.

## test_youtube_strategy_engine.py::DynamicExitBacktestTests::test_close_based_vwap_exit_fills_at_next_bar_open

**OLD EXPECTATION:**

```python
self.assertEqual(result["metrics"]["trade_count"], 1)
self.assertEqual(trade["reason"], "VWAP loss")
self.assertEqual(trade["entry_price"], 10.0)
self.assertEqual(trade["exit_price"], 9.0)
self.assertEqual(
            trade["exit_time"],
            rows[2]["t"],
        )
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::DynamicExitBacktestTests::test_caller_prepared_vwap_cannot_authorize_legacy_execution`.

## test_youtube_strategy_engine.py::DynamicExitBacktestTests::test_multi_stage_scale_out_executes_and_moves_remainder_to_breakeven

**OLD EXPECTATION:**

```python
self.assertEqual(result["metrics"]["trade_count"], 1)
self.assertEqual(trade["reason"], "Stop loss")
self.assertEqual(len(trade["partial_exits"]), 2)
self.assertIn("stage 1", trade["partial_exits"][0]["reason"].lower())
self.assertIn("stage 2", trade["partial_exits"][1]["reason"].lower())
self.assertGreater(trade["scaled_out_quantity"], 0)
self.assertGreater(trade["pnl"], 0)
self.assertGreater(trade["max_favorable_excursion_pct"], 0)
self.assertGreaterEqual(trade["max_adverse_excursion_pct"], 0)
self.assertEqual(trade["management_event_count"], 2)
self.assertIn("Stop loss", attribution["final_exit_reasons"])
self.assertEqual(len(attribution["partial_exit_reasons"]), 2)
self.assertGreater(attribution["partial_exit_net_pnl"], 0)
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::DynamicExitBacktestTests::test_scale_out_rules_do_not_authorize_legacy_execution`.

## test_youtube_strategy_engine.py::DynamicExitBacktestTests::test_time_limit_exit_fills_at_bar_open_when_limit_has_elapsed

**OLD EXPECTATION:**

```python
self.assertEqual(result["metrics"]["trade_count"], 1)
self.assertEqual(trade["reason"], "Time limit")
self.assertEqual(trade["exit_price"], 8.0)
self.assertEqual(trade["exit_time"], rows[2]["t"])
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::DynamicExitBacktestTests::test_time_limit_rules_do_not_authorize_legacy_execution`.

## test_youtube_strategy_engine.py::DynamicExitBacktestTests::test_trailing_stop_updates_causally_and_can_exit_next_bar

**OLD EXPECTATION:**

```python
self.assertEqual(result["metrics"]["trade_count"], 1)
self.assertEqual(trade["reason"], "Stop loss")
self.assertIsNone(trade["target_price"])
self.assertGreater(trade["pnl"], 0)
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/preliminary_scope.py::require_preliminary`, called first by `youtube_strategy_engine.py::run_backtest`: owned exact one-use scope is mandatory; prepared indicators/records/sessions, settings, validated labels and a worker process supply no authority. Job 2 gate report section 6 preserves this fallback unchanged.

**WHY THE OLD EXPECTATION IS INVALID:** The old test invokes the legacy native API without owned scope, so its expected numerical result is unreachable under the approved boundary.

**NEW EXPECTATION:** Require the exact legacy-execution refusal for the original fabricated input/settings variants. Direct native cases also assert that the initial result constructor is never reached; the optimizer case checks sequential, process-parallel and timeframe routes.

Replacement: `test_youtube_strategy_engine.py::DynamicExitBacktestTests::test_trailing_stop_rules_do_not_authorize_legacy_execution`.

## test_readonly_validation_native.py::test_normal_validation_defaults_unchanged

**OLD EXPECTATION:**

```python
assert (p["training_fraction"],p["validation_fraction"])==(.6,.2)
assert (p["minimum_training_trades"],p["minimum_validation_trades"])==(5,2)
assert p["run_walk_forward"] and (p["wf_folds"],p["wf_history_sessions"],p["wf_test_sessions"])==(3,8,2)
assert p["search_depth"]==36 and p["history_days"]==30
```

**CURRENT AUTHORITATIVE CONTRACT:** `desktop/trading_intelligence/strategy_lab_page.py::_emit_run` requires a complete exact strategy-revision map before emitting the request.

**WHY THE OLD EXPECTATION IS INVALID:** The old options fixture omits revision; silent non-emission is the correct fail-closed outcome, not a UI regression.

**NEW EXPECTATION:** Give the positive fixture an explicit generated revision, retain every default-setting assertion, and assert the emitted revision map. Add a separate missing-revision/no-emission test.

Replacement: `test_readonly_validation_native.py::test_normal_validation_defaults_unchanged`.

## test_saved_search_results.py::test_certified_saved_validation_is_read_only_and_fail_closed[complete]

**OLD EXPECTATION:**

```python
assert button.isEnabled() and not p.cancel.isEnabled()
assert p.selected()['id'] == s.job.id and s.job.payload['run_id'] in p.detail.text()
assert not window.saved_validation.busy
assert calls == [('POST', '/v1/saved-validations/result', {**s.request, 'binding': None})]
assert (vars(s.job), s.library, s.link) == before
assert window.monitor_fixture.cloud.write_count == 0
assert window.monitor_fixture.cloud.dispatches == []
assert (method, path) == ('POST', '/v1/saved-validations/result')
assert getattr(controller, 'dialog', None) is None
assert 'Saved validation unavailable:' in window.top_status.text()
assert controller.dialog.isVisible()
assert s.job.id in controller.page.identity.text() and SID in controller.page.identity.text()
assert [b.text() for b in controller.dialog.findChildren(QPushButton)] == ['Close']
assert not hasattr(controller.page, 'run_requested')
assert not hasattr(controller.page, 'backtest_requested')
assert 'failed (not running)' in controller.page.verdict.text()
assert 'No strategy-validation verdict' in controller.page.verdict.text()
assert not hasattr(controller.page, 'strength')
assert 'FAILED (execution completed)' in controller.page.verdict.text()
assert '13/100' in controller.page.strength.text()
forbidden.assert_not_called()
```

**CURRENT AUTHORITATIVE CONTRACT:** The saved-validation backend/UI separates completed execution, calibration failure and a permissible strategy conclusion. Existing `test_readonly_validation_native.py::test_saved_native_evidence_and_no_execution_controls` asserts CALIBRATION FAILED and No strategy conclusion permitted.

**WHY THE OLD EXPECTATION IS INVALID:** The old FAILED (execution completed) label conflates execution completion and a strategy verdict when the calibration gate has not passed.

**NEW EXPECTATION:** Assert the explicit calibration-failure/no-strategy-conclusion text. Preserve strength display as recorded evidence, read-only controls, exact read endpoint, immutable records, zero writes and zero dispatches.

Replacement: `test_saved_search_results.py::test_certified_saved_validation_is_read_only_and_fail_closed[complete]`.

## test_youtube_strategy_engine.py::FinalHoldoutIntegrityTests::test_negative_holdout_revokes_validated_status_without_reselection

**OLD EXPECTATION:**

```python
self.assertEqual(winner["pre_holdout_status"], "VALIDATED")
self.assertEqual(winner["status"], "HOLDOUT FAILED")
self.assertEqual(winner["holdout_metrics"]["net_pnl"], -12.0)
self.assertFalse(
            winner["holdout_execution_sensitivity"]["passes_validation_gate"]
        )
```

**CURRENT AUTHORITATIVE CONTRACT:** `youtube_strategy_engine.py::behavior_ab_comparison` returns legacy_settings, optimized_settings, legacy_metrics and optimized_metrics; `finalize_stock_optimization` binds those to the configuration history. Missing lineage must not be accepted.

**WHY THE OLD EXPECTATION IS INVALID:** The empty mocked comparison dictionary violates the actual producer contract; the real finalizer correctly requires the missing fields.

**NEW EXPECTATION:** Return all four explicit generated fields from the comparison stub. Preserve the original negative-holdout, status-revocation and failed-sensitivity assertions without any real holdout or numerical execution.

Replacement: `test_youtube_strategy_engine.py::FinalHoldoutIntegrityTests::test_negative_holdout_revokes_validated_status_without_reselection`.

## tests/systematic_trader/test_collection_campaign.py::test_runtime_single_cycle_and_stop

**OLD EXPECTATION:**

```python
assert s.records()[-1]['kind']=='stopped' and s.seal('day',2100)['classification']=='Complete'
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/service.py::assert_canonical_source` and the first call in `collection_runtime.serve` / CLI main reject foreign source roots before service effects. Locks, stop requests, cooldown, power and calendar invariants remain downstream requirements.

**WHY THE OLD EXPECTATION IS INVALID:** The old test assumes its isolated Linux/worktree path is the configured local canonical source, so the correct source refusal prevents it reaching the intended downstream invariant.

**NEW EXPECTATION:** Supply an explicit, non-autouse unit fixture trust root while keeping the real guard active. Retain original downstream assertions and temporary generated storage; use forbidden/mocked callbacks. The subprocess lock test receives the fixture root and a forbidden callback. The CLI alias test binds its default directory and cwd to one temporary fixture root.

Replacement: `tests/systematic_trader/test_collection_campaign.py::test_runtime_single_cycle_and_stop`.

## tests/systematic_trader/test_collection_campaign.py::test_owner_lock_refuses_second_process

**OLD EXPECTATION:**

```python
assert result.returncode!=0 and 'collector_already_running' in result.stderr
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/service.py::assert_canonical_source` and the first call in `collection_runtime.serve` / CLI main reject foreign source roots before service effects. Locks, stop requests, cooldown, power and calendar invariants remain downstream requirements.

**WHY THE OLD EXPECTATION IS INVALID:** The old test assumes its isolated Linux/worktree path is the configured local canonical source, so the correct source refusal prevents it reaching the intended downstream invariant.

**NEW EXPECTATION:** Supply an explicit, non-autouse unit fixture trust root while keeping the real guard active. Retain original downstream assertions and temporary generated storage; use forbidden/mocked callbacks. The subprocess lock test receives the fixture root and a forbidden callback. The CLI alias test binds its default directory and cwd to one temporary fixture root.

Replacement: `tests/systematic_trader/test_collection_campaign.py::test_owner_lock_refuses_second_process`.

## tests/systematic_trader/test_collection_campaign.py::test_stop_request_prevents_source_access

**OLD EXPECTATION:**

```python
assert s.records()[-1]['kind']=='stopped'
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/service.py::assert_canonical_source` and the first call in `collection_runtime.serve` / CLI main reject foreign source roots before service effects. Locks, stop requests, cooldown, power and calendar invariants remain downstream requirements.

**WHY THE OLD EXPECTATION IS INVALID:** The old test assumes its isolated Linux/worktree path is the configured local canonical source, so the correct source refusal prevents it reaching the intended downstream invariant.

**NEW EXPECTATION:** Supply an explicit, non-autouse unit fixture trust root while keeping the real guard active. Retain original downstream assertions and temporary generated storage; use forbidden/mocked callbacks. The subprocess lock test receives the fixture root and a forbidden callback. The CLI alias test binds its default directory and cwd to one temporary fixture root.

Replacement: `tests/systematic_trader/test_collection_campaign.py::test_stop_request_prevents_source_access`.

## tests/systematic_trader/test_collection_campaign.py::test_persisted_reference_cooldown_survives_restart

**OLD EXPECTATION:**

```python
assert len(called)==1
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/service.py::assert_canonical_source` and the first call in `collection_runtime.serve` / CLI main reject foreign source roots before service effects. Locks, stop requests, cooldown, power and calendar invariants remain downstream requirements.

**WHY THE OLD EXPECTATION IS INVALID:** The old test assumes its isolated Linux/worktree path is the configured local canonical source, so the correct source refusal prevents it reaching the intended downstream invariant.

**NEW EXPECTATION:** Supply an explicit, non-autouse unit fixture trust root while keeping the real guard active. Retain original downstream assertions and temporary generated storage; use forbidden/mocked callbacks. The subprocess lock test receives the fixture root and a forbidden callback. The CLI alias test binds its default directory and cwd to one temporary fixture root.

Replacement: `tests/systematic_trader/test_collection_campaign.py::test_persisted_reference_cooldown_survives_restart`.

## tests/systematic_trader/test_collector_readiness.py::test_power_failure_stops_service_before_reference_access

**OLD EXPECTATION:**

```python
assert status(tmp_path, OPEN)['state'] == 'Stopped'
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/service.py::assert_canonical_source` and the first call in `collection_runtime.serve` / CLI main reject foreign source roots before service effects. Locks, stop requests, cooldown, power and calendar invariants remain downstream requirements.

**WHY THE OLD EXPECTATION IS INVALID:** The old test assumes its isolated Linux/worktree path is the configured local canonical source, so the correct source refusal prevents it reaching the intended downstream invariant.

**NEW EXPECTATION:** Supply an explicit, non-autouse unit fixture trust root while keeping the real guard active. Retain original downstream assertions and temporary generated storage; use forbidden/mocked callbacks. The subprocess lock test receives the fixture root and a forbidden callback. The CLI alias test binds its default directory and cwd to one temporary fixture root.

Replacement: `tests/systematic_trader/test_collector_readiness.py::test_power_failure_stops_service_before_reference_access`.

## tests/systematic_trader/test_historical.py::test_cli_rejects_relative_alias_of_default_live_data_directory

**OLD EXPECTATION:**

```python
assert main(['--data-dir','.systematic-trader/capture','import-history','--file','unread-file.json'])==2
assert 'explicit_data_directory' in capsys.readouterr().err
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/service.py::assert_canonical_source` and the first call in `collection_runtime.serve` / CLI main reject foreign source roots before service effects. Locks, stop requests, cooldown, power and calendar invariants remain downstream requirements.

**WHY THE OLD EXPECTATION IS INVALID:** The old test assumes its isolated Linux/worktree path is the configured local canonical source, so the correct source refusal prevents it reaching the intended downstream invariant.

**NEW EXPECTATION:** Supply an explicit, non-autouse unit fixture trust root while keeping the real guard active. Retain original downstream assertions and temporary generated storage; use forbidden/mocked callbacks. The subprocess lock test receives the fixture root and a forbidden callback. The CLI alias test binds its default directory and cwd to one temporary fixture root.

Replacement: `tests/systematic_trader/test_historical.py::test_cli_rejects_relative_alias_of_default_live_data_directory`.

## tests/systematic_trader/test_live_target.py::test_market_closed_and_next_session_not_fake_sealed

**OLD EXPECTATION:**

```python
assert set(s.registrations())=={'2026-09-08|target-v1'}
assert not any(x['kind']=='seal' for x in s.records())
assert status(tmp_path,now)['phase']=='Awaiting session'
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/service.py::assert_canonical_source` and the first call in `collection_runtime.serve` / CLI main reject foreign source roots before service effects. Locks, stop requests, cooldown, power and calendar invariants remain downstream requirements.

**WHY THE OLD EXPECTATION IS INVALID:** The old test assumes its isolated Linux/worktree path is the configured local canonical source, so the correct source refusal prevents it reaching the intended downstream invariant.

**NEW EXPECTATION:** Supply an explicit, non-autouse unit fixture trust root while keeping the real guard active. Retain original downstream assertions and temporary generated storage; use forbidden/mocked callbacks. The subprocess lock test receives the fixture root and a forbidden callback. The CLI alias test binds its default directory and cwd to one temporary fixture root.

Replacement: `tests/systematic_trader/test_live_target.py::test_market_closed_and_next_session_not_fake_sealed`.

## tests/systematic_trader/test_live_target.py::test_scheduler_premarket_rth_postclose_and_seal

**OLD EXPECTATION:**

```python
assert calls==['alpaca_iex']
assert [r['body']['phase'] for r in s.records() if r['kind']=='session_phase']==['Premarket','RTH','Post-close','Seal due']
assert len(seals)==1 and not seals[0]['certified']
assert status(tmp_path,values[-1])['seal']=='Sealed'
```

**CURRENT AUTHORITATIVE CONTRACT:** `systematic_trader/service.py::assert_canonical_source` and the first call in `collection_runtime.serve` / CLI main reject foreign source roots before service effects. Locks, stop requests, cooldown, power and calendar invariants remain downstream requirements.

**WHY THE OLD EXPECTATION IS INVALID:** The old test assumes its isolated Linux/worktree path is the configured local canonical source, so the correct source refusal prevents it reaching the intended downstream invariant.

**NEW EXPECTATION:** Supply an explicit, non-autouse unit fixture trust root while keeping the real guard active. Retain original downstream assertions and temporary generated storage; use forbidden/mocked callbacks. The subprocess lock test receives the fixture root and a forbidden callback. The CLI alias test binds its default directory and cwd to one temporary fixture root.

Replacement: `tests/systematic_trader/test_live_target.py::test_scheduler_premarket_rth_postclose_and_seal`.

## Added boundary tests and fixture

- `test_missing_strategy_revision_prevents_run_emission`: absent revision emits no request.
- `test_foreign_source_is_rejected_before_store_or_cycle`: actual source guard prevents storage and provider callbacks; output directory never appears.
- `test_fixture_source_guard_remains_active_after_root_changes`: a valid fixture trust root does not disable later foreign-source rejection.
- `fixture_canonical_source` is explicitly requested only by the affected collector tests. It restores the constant after each test; production paths are unchanged.

## CI runtime budget

The existing Tests workflow allows 15 minutes for installation, tests and shutdown. Frozen Linux suites alone took 902.521, 912.982 and 927.081 seconds; all three job durations exceeded 16 minutes. The regression PR raises only that job limit to 30 minutes. It does not drop tests, alter requirements, add xfails/skips, or change admission criteria. PR #124 itself is unchanged.

## Remaining scope boundaries

This is a regression-contract repair, not numerical backtester requalification. Real Job 2, private historical/holdout data, certificate issuance, strategy execution and SVRN worker pickup remain outside this PR. The three baseline skips remain the macOS-specific plugin check and two unavailable private V4 artifact cases.
