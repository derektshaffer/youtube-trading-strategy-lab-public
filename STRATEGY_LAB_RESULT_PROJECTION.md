# Compact validation result contract (projection_version=1)

The canonical input is the persisted Strategy Lab checkpoint result, including an integrity-checked archived result when present. Projection never runs the optimizer, reads market data, adjusts thresholds, scores evidence, or grants production eligibility.

`hybrid_runtime/strategy_lab_result_projection.py` is the single mapping used through `strategy_lab_result_summary` by cloud completion, checkpoint reconciliation and saved-results adapters. Current engine fields are authoritative. Dictionary membership preserves explicit zero, false, empty and null; `evidence_availability` distinguishes absence. Undefined profit factor is null, not zero.

Walk-forward uses `summary.fold_count`, `active_fold_count`, `profitable_fold_count`, external metrics, coverage and embargo fields. The compact result retains that summary and at most six small fold evidence records, excluding optimized rules and trial payloads. `executed` is derived only from a recorded fold count; absence never becomes a pass. The existing desktop model consumes `walk_forward.summary` while API clients may use `walk_forward_summary`.

Parameter stability uses `tested`, `active`, `positive`, `positive_pct`, `label`, P/L extrema and its persisted explanation. `classification` mirrors the persisted label so the current desktop scalar-details table can distinguish BRITTLE from the test-execution status complete. It is not a new score.

Narrow legacy input aliases: walk-forward folds -> fold_count, profitable_folds -> profitable_fold_count, total_pnl -> external_net_pnl; stability tested_neighbor_count -> tested. Aliases apply only when the canonical key is absent, never for a present zero, false, null or empty value. Legacy ratio fields retain their original names/units rather than guessing a percentage conversion.

Additional projected evidence: all compact period metrics (including full descriptive history), confidence caps/reasons, optimizer warnings/sample status, validation thresholds, development and untouched-holdout cost sensitivity, paper-readiness and execution-fidelity blocks, historical spread coverage, market integrity, holdout-reuse safeguards and backtest limitations. A source-fidelity finding is not invented when the saved result does not contain it. Large optimizer histories, trade arrays, equity curves, raw neighbor rules and checkpoint archives are not exposed. Explanation lists and fold details are bounded.

Completed legacy desktop jobs receive one bounded historical attachment pass per backend lifetime. Only an exact authenticated remote/checkpoint identity can supply the new projection. The SQLite compare-and-swap enrichment changes result_json only; prior verdict/numeric evidence, job status, progress, timestamps, attempts and payload remain unchanged. Missing/evicted history never publishes a job or triggers endless polling. No cloud historical result is rewritten.

Regression fixture: fixtures/strategy_lab_spy_completed.json is a compact extraction of the already completed SPY run spy-diagnostic-2-20260906T012943Z-c1e52265, local job d5e0aeae9c7c4556809fab89370b425f. No simulation was rerun to create it. It preserves three inactive folds, twelve BRITTLE stability checks, zero trades, NO RELIABLE EDGE FOUND and 13/100 WEAK. The fixture omits raw optimizer configurations and carries no credentials.

## Publication boundary

The scoped PR is based on live main ed39bf7. The canonical working tree also has pre-existing, unpublished saved-results endpoint and desktop comparison adapters. They are deliberately excluded. The canonical focused run exercises their real FastAPI endpoint and comparison contract (139 tests passed); the isolated published test exercises the shared serialized JobRecord contract without depending on those unrelated local-only modules or optional desktop dependencies. The live canonical saved-result endpoint is replayed again after activation.
