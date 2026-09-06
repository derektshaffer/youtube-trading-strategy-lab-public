# Read-only validation evidence publication

## Dependency classification

A. REQUIRED READ-ONLY SAVED-RESULT DEPENDENCY:
- hybrid_runtime/api.py: authenticated /v1/saved-validations/result.
- hybrid_runtime/saved_validation_reader.py: read_saved_validation, _local,
  _response, _request_identity. Only service get/list, link reads and cloud reads.
- hybrid_runtime/strategy_lab_result_projection.py: existing PR #115 projection,
  unchanged. Reuse versioned compact records directly; legacy raw records and
  archived cloud results use project_strategy_lab_result.
- desktop/trading_intelligence/saved_validation_page.py: evidence-only page.
- desktop/trading_intelligence/saved_validation_controller.py: bounded single read,
  native dialog, Close only; no execution controller.
- parity_window.py: install the read-only action and retain the tested stock/
  strategy handoff and options-loading fixes.
- analysis_page.py / strategy_lab_page.py: original P1 evidence/context repairs.
- hybrid_runtime/search_monitor.py: terminal display stage stays terminal.

B. REQUIRED SHARED UTILITY:
- display_time.py and timestamp caller hunks in analysis/chart/finder/search monitor,
  pages/results/research/system-health. No layout/theme redesign.
- Existing service, link store, cloud JSON reader, checkpoint archive restore,
  security redaction, Qt widgets. No new worker or scheduling architecture.

C. UNPUBLISHED BACKTEST/EXECUTION FUNCTIONALITY -- EXCLUDE:
- saved_backtest_controller.py: start, request submission and backtest polling.
- saved_results_page.py: Run Backtest, editable date/ticker controls, execution
  signal, _run, set_backtest_running and render_backtest.
- hybrid_runtime/saved_strategy_backtest.py and related execution handlers.
- saved_strategy_results.py candidate configuration extraction / can_backtest.
- Backtest route/handler/type-map additions and mixed legacy /v1/searches/result.
  None is needed by the new terminal-validation read path.
- No new execution button, run creation, retry, dispatch, write or broker action.

D. UNRELATED DIRTY WORK -- EXCLUDE:
- Navigation renaming, theme, scrolling/layout experiments, discovery lifecycle
  changes, provider/research changes, local dev-launcher work and other dirty files.
  Their local sources remain untouched, not swept into this publication.

## Identity / side effects

Opening requires an exact local fingerprint, or a fresh cloud binding and request
identity. Local/cloud attachments must agree on ticker, explicit strategy set,
run ID and terminal state. No latest-symbol fallback, secondary strategy, replay,
retry or optimistic status repair. Ambiguous or absent evidence fails closed.

Completed raw evidence uses the existing canonical projection. Already-projected
records are not rescored or merged with other candidates. Responses reject internal
execution payloads and projections above 128 KB. Zero, null, warnings, fidelity,
holdout and robustness evidence survive. No historical strategy rules are returned.

A failed execution returns result=null and its terminal error, not a failed-strategy
verdict. Strategy failure is displayed only from a completed run's saved verdict.

The reader does not call reconciliation, execute/publish/dispatch, submit/cancel/
retry, settings writes or persistence updates. Tests poison those calls, repeat
reads, and compare saved strategy, job, cloud library and order-state fixtures.

## Timestamp / activation

Storage/API timestamps stay UTC-aware. Shared display converts aware instants to
the OS local timezone for their date and includes the zone abbreviation and year.
Date-only values do not shift. Only known UTC fields opt into naive UTC handling;
ambiguous naive values remain unchanged with an explicit unknown-zone label.
Never stays Never. Tests cover PDT/PST, DST transitions and midnight rollover.

The canonical source keeps unrelated local modifications. Activation verifies the
published reader, evidence page, read controller and formatter bytes against merged
main, plus the published P1 method/hook contract, before native retesting. Merely
having local unmerged files is not treated as publication.

Library-wide Unvalidated versus SPY-specific failed validation remains P2 terminology.
No validation/backtest submission or redesign is part of this publication.
