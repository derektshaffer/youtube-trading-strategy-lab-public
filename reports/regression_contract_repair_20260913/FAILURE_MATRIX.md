# Frozen Linux failure matrix

Completed before repairs: 2026-09-14T02:16:30.050233+00:00. Workflow run 34796762163.

| Test | Canonical | #124 | #123 + #124 | Attribution / contract |
| --- | --- | --- | --- | --- |
| test_avwap_integration.py::AvwapIntegrationTests::test_session_open_avwap_is_available_to_backtest | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_optimizer_feature_reuse.py::test_prepared_record_payload_is_backtest_equivalent | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_stock_strategy_finder.py::OptimizerLedgerTests::test_optimizer_returns_unique_exact_configuration_ledger | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_stock_strategy_finder.py::OptimizerResumeTests::test_resume_skips_families_already_completed_in_checkpoint | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_stock_strategy_finder.py::FinderEvidenceTierTests::test_regime_diagnostics_are_descriptive_and_use_frozen_winner | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_strategy_lab_execution.py::StrategyLabExecutionTests::test_real_optimizer_completes_quick_and_very_deep_profiles | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_trading_universe_research.py::UniverseResearchTests::test_report_preserves_symbol_count_and_frozen_rules | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::ParallelOptimizerTests::test_distributed_family_and_timeframe_merge_matches_single_process | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::ParallelOptimizerTests::test_parallel_family_optimizer_matches_sequential_ranking | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::BacktestTests::test_adverse_opening_gap_uses_gap_price | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::BacktestTests::test_costs_reduce_return | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::BacktestTests::test_entry_uses_next_bar_open | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::BacktestTests::test_extended_hours_can_trade_at_reduced_size | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::BacktestTests::test_holdout_is_chronological | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::BacktestTests::test_layered_entries_can_overlap_without_multiplying_total_allocation | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::BacktestTests::test_price_band_can_unlock_momentum_continuation_above_max | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::BacktestTests::test_pullback_strategy_requires_pullback_then_breakout | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::BacktestTests::test_same_bar_stop_and_target_uses_conservative_stop | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::BacktestTests::test_short_strategy_is_rejected | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::BacktestTests::test_strategy_end_time_can_be_ignored | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::StreamlitSmokeTests::test_complete_dashboard_renders_saved_strategy_backtest_scan_and_positions | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::DynamicExitBacktestTests::test_breakeven_rule_moves_stop_after_r_trigger | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::DynamicExitBacktestTests::test_close_based_vwap_exit_fills_at_next_bar_open | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::DynamicExitBacktestTests::test_multi_stage_scale_out_executes_and_moves_remainder_to_breakeven | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::DynamicExitBacktestTests::test_time_limit_exit_fills_at_bar_open_when_limit_has_elapsed | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::DynamicExitBacktestTests::test_trailing_stop_updates_causally_and_can_exit_next_bar | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_readonly_validation_native.py::test_normal_validation_defaults_unchanged | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_saved_search_results.py::test_certified_saved_validation_is_read_only_and_fail_closed[complete] | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| test_youtube_strategy_engine.py::FinalHoldoutIntegrityTests::test_negative_holdout_revokes_validated_status_without_reselection | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| tests/systematic_trader/test_collection_campaign.py::test_runtime_single_cycle_and_stop | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| tests/systematic_trader/test_collection_campaign.py::test_owner_lock_refuses_second_process | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| tests/systematic_trader/test_collection_campaign.py::test_stop_request_prevents_source_access | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| tests/systematic_trader/test_collection_campaign.py::test_persisted_reference_cooldown_survives_restart | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| tests/systematic_trader/test_collector_readiness.py::test_power_failure_stops_service_before_reference_access | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| tests/systematic_trader/test_historical.py::test_cli_rejects_relative_alias_of_default_live_data_directory | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| tests/systematic_trader/test_live_target.py::test_market_closed_and_next_session_not_fake_sealed | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
| tests/systematic_trader/test_live_target.py::test_scheduler_premarket_rth_postclose_and_seal | FAIL | FAIL | FAIL | PRE_EXISTING_CANONICAL_FAILURE / STALE_TEST_CONTRACT |
