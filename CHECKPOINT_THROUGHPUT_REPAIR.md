# Checkpoint throughput and diagnostic terminal details

## Scoped activation candidate

The sections below record the original local repair investigation, before
activation. The activation candidate was separately isolated against live main
`aa5ce18da66106f54c8e6e1fd6bc7ca56c8bbb63` in a temporary clean worktree.
Its focused suite passed 166 tests plus two subtests; its complete local suite
passed 1,134 tests plus 217 subtests. The actual proposed commit must also pass
GitHub CI before merge. No market validation is part of this activation.

The candidate excludes all desktop files, timestamp and workflow-label changes,
unrelated bridge terminology edits, Alpaca-message edits, and an existing
search-monitor presentation test that depends on unrelated working-tree changes.
That excluded test remains untouched in the canonical tree. The checkpoint,
progress, diagnostic timeout, terminal reconciliation and recovery tests remain
included. Baseline timestamp behavior and label definitions remain unchanged.

The original eight dirty-tree desktop failures were NOT eight matching live-main
failures: seven checks passed on baseline and the local-only label assertion
failed for a different reason. They are excluded by source/hunk isolation, not by
changing expectations or accepting a failing scoped CI run.

## Scope and activation boundary

Canonical source: `/Users/Derek_1/Documents/Codex/2026-09-03/referenced-chatgpt-conversation-this-is-an/work/trading-lab-dev`.

Evidence is from local job `cc39e172544844d8b654135e3479298c`, cloud job
`desktop-strategy-lab-ad0ff85d2d342a11d3289375`, run
`spy-diagnostic-20260905T224216Z-705d4990`. The deployed worker revision was
`aa5ce18da66106f54c8e6e1fd6bc7ca56c8bbb63`.

This repair is LOCAL ONLY. No new validation, remote checkpoint/queue write,
deployment, backend restart, credential change, checkout, worktree, or repository
copy was performed. Benchmark directories contain disposable data stores only.
Existing unrelated work and the existing, previously undeployed progress-sidecar
implementation were preserved. No strategy, validation threshold, search-depth,
walk-forward, holdout, robustness, fidelity, attempt-limit or timeout setting was
changed. The workflow remains unchanged.

Before another real diagnostic, activate only this scoped repair and its existing
sidecar dependencies through the normal source publication path, verify the
workflow/worker revision, and reload the canonical backend. Do not deploy unrelated
desktop changes. Activation and a new cloud validation have not been performed by
this task. The remaining live-submission blocker is activation; the full local
tree also has eight independently reproduced out-of-scope desktop test failures.

## Root cause measurements

The original checkpoint blob is `caf4edd4589e1a06ec1b09783ff204583fc7de34`.
Its raw size is 50,296,150 bytes. Component sizes use compact JSON encoding.

| Component | Bytes |
| --- | ---: |
| Current SPY record | 6,903 |
| SPY canonical job specification within that record | 6,193 |
| Previous completed LIDR result | 32,624,716 |
| LIDR configuration history within that result | 31,781,263 |
| Previous SLS optimizer state | 3,193,411 |
| Previous SDOT optimizer states | 5,927,960 and 7,955,185 |

The four non-SPY records were EXACTLY equal in the first and terminal snapshots.
The whole document grew from 50,295,967 to 50,296,150 bytes, only 183 bytes.
It was not accumulating 50 MB of new SPY candles or SPY results. The SPY record
had no completed optimizer family and no final result.

There were 53 checkpoint-file commits during the bounded execution/finalization
interval. Every document exceeded the existing 1,000,000-byte REST-write cutoff.
The deployed hot path synchronously loaded the library, copied/serialized it,
saved an atomic local checkpoint, reread the remote library for conflict checking,
then shallow-cloned the private data repository, committed, and pushed it.
It repeated that path for small progress changes. The old throttle also recorded
its timestamp BEFORE saving, so a save lasting over ten seconds made the next
progress callback immediately eligible for another full save.

The first checkpoint's saved timestamp was `22:44:03.213355Z`; its verified commit
`64f46d0a07042603586548958b24f36f85317c23` was at `22:44:20Z`: 16.786645 seconds
within the synchronous persistence path, before the push finished. For 36
observed saved timestamps, the nearest subsequent checkpoint commit lag had a
16.196528-second median and a 15.446195-18.115399-second range. Their summed lags
were 586.332078 seconds. Those associations are an estimate, not independently
instrumented measurements of every write. Observed running-checkpoint gaps had
a roughly 22.95-second median; this was sampled visibility, not a guaranteed rate.

The old run did not log separate computation, serialization, disk-write, clone,
or push durations. Their exact totals cannot be recovered honestly. Read-only
downloads of the two saved 50 MB snapshots on this machine took 3.77 and 3.87
seconds; these are not measurements of the original worker's network latency.

Confidence is HIGH that repeated unchanged checkpoint history caused substantial
avoidable overhead, and MEDIUM in how much of the original 20-minute wall time
it consumed. It is not proven to be the only reason the diagnostic timed out.

## Resume data versus progress and presentation

- Required durable state is preserved without pruning: exact run/ticker identity,
  canonical job/candidates/configuration, attempt/deadline, existing optimizer
  fingerprint, rankings/finalists, completed families, configuration history,
  integrity metadata, and completed results including fold reports.
- Market history continues to use the existing exact-window cache/reload,
  corporate-action preparation and optimizer fingerprint checks. This repair
  changes neither reuse eligibility nor anti-leakage behavior.
- Lightweight progress contains only identity/attempt/start binding, timestamp,
  fraction, stage and a bounded message. It cannot supply jobs, optimizer state,
  results or promotion/eligibility evidence.
- Historical presentation/configuration data is excluded from frequent heartbeat
  writes, NOT deleted from the durable checkpoint library. Existing archive,
  integrity-check, conflict reconciliation and five-record retention behavior is
  unchanged. The full library remains about 50.3 MB.
- Resume granularity remains the existing completed-strategy-family boundary.
  There was no per-variant or in-progress walk-forward-fold checkpoint contract
  to remove. Incomplete work is recomputed on a permitted normal restart;
  diagnostic jobs still cannot restart or obtain a second attempt.

## Repair and instrumentation

The existing canonical progress sidecars are retained and instrumented. Durable
writes happen at initialization, every optimizer checkpoint callback, and terminal
state. Progress-only callbacks use their own small, attempt-bound document.
The existing ten-second / five-percentage-point / stage-change triggers remain;
the throttle is measured from completion of the previous write. These are
callback-driven updates, not a separate heartbeat thread or a guaranteed
wall-clock heartbeat during a long provider call or computation.

Checkpoint stores use a single compact JSON serialization followed by the same
fsync, automatic backup and atomic byte replacement. Non-checkpoint stores keep
their original writer unless instrumentation is explicitly attached.

Each `strategy_lab_checkpoint_io` log event records operation type, stage, exact
run ID, serialized size, serialization seconds, local-write seconds, remote API
or large Git-persistence seconds, remaining parsing/copying/reconciliation time,
and cumulative totals. Time and process CPU between checkpoint operations are
recorded separately. Non-checkpoint wall time includes provider waits and other
application work; it is deliberately not labelled pure backtesting computation.
No job specification, credentials, strategy rules or exception chain is logged.
A failed telemetry sink cannot change storage outcomes.

## Representative replay benchmark

The saved deployed runner and checkpoint artifact were replayed locally with 12
progress callbacks, one completed-family checkpoint and a controlled terminal
interruption. No market validation was executed. Remote transport was stubbed in
memory, with real JSON parsing, serialization and filesystem persistence. No
network, Git clone, commit or push was executed. The isolated second measurement
ran without the test suite competing for CPU.

| Measurement | Before | After |
| --- | ---: | ---: |
| Representative runtime | 48.635348 s | 7.702348 s |
| Persistence wall time | 48.629785 s | 7.701479 s |
| Serialization path | 19.335469 s | 0.902126 s |
| Local flush/backup/replace | 0.409562 s | 0.126377 s |
| Other persistence, including parsing/copying | 28.884754 s | 6.672975 s |
| Expensive durable writes | 15 | 3 |
| Small heartbeat writes | 0 | 12 |
| Writes that would cross the Git cutoff | 15 | 3 |
| Serialized upload volume | 754,439,723 B | 150,896,693 B |
| Full durable document | 50,296,221 B | 50,296,267 B |
| Largest progress-only document | 50,296,221 B full-library path | 697 B |

Persistence/runtime reduction is approximately 84.2%; serialization-path reduction
is 95.3%; aggregate upload-byte reduction is 80.0%. A progress document is over
99.998% smaller than the old full-library update. The old `json.dump` timing
includes buffered writes; the new serialization timing is `json.dumps` plus UTF-8
encoding. This distinction is also present in the machine-readable benchmark.
Remote latency is absent, not zero-cost production persistence.

The first replay measured 50.208435 to 7.840019 seconds. Both runs verified exact
equality of all retained historical records, canonical job identity and saved
optimizer state. No unfinished work became a completed result.

Expected effect: remove most repeated large transfers/Git operations and their
associated parsing/copying costs during optimization. This credibly justifies
another bounded diagnostic AFTER activation. It does not predict profitability,
guarantee completion in 20 minutes, or justify weakening validation criteria.

## Timeout state semantics

Tests establish that checkpoint and queue errors preserve `execution_timeout`,
an infrastructure message, the original `last_execution_stage=optimization`,
progress, attempt budget, absolute deadline and last checkpoint timestamp.
`terminal_reason` is separate from the last meaningful execution stage.
Repeated finalization retains the original execution context instead of replacing
it with the timeout reason. Previously destroyed history is reported as unknown,
not reconstructed speculatively.

The bridge consumes a narrow allowlist of diagnostic fields from the exact run,
ticker and attempt. It preserves detail whether checkpoint failure arrives before
or after queue failure. Existing generic failed local jobs can receive metadata
through a read-only cloud attachment and an atomic SQLite metadata update; status,
attempt, result, completion time and lifecycle remain terminal. A missing remote
row cannot trigger publication or dispatch. No live historical job was rewritten
by this task; these are local implementation and offline-test results.

## Tests

- Syntax/import checks: passed for all modified/new Python modules.
- Focused checkpoint/progress/persistence/budget/worker/bridge/recovery/validation:
  155 passed, zero failed, plus two passing subtests.
- Full local CI-equivalent suite: 1,206 tests, 1,198 passed, eight failed, plus
  217 passing subtests. Four deprecation warnings included the existing fork
  warning in the accelerated real-process timeout test.
- The eight failures reproduced independently in the three untouched desktop
  test modules: six timestamp-format expectations in `test_desktop_display_time.py`,
  one timestamp expectation in `test_desktop_finder_queue_display.py`, and one
  workflow-label mapping expectation in `test_desktop_workflow_labels.py`.
  The standalone reproduction had nine passes and the same eight failures.
- No repair-focused failure, retry-bound regression, or validation-integrity
  regression was observed. The broader tree is NOT fully green. No unrelated
  desktop code or expectations were changed to conceal these failures.

## Files changed by this repair

| File | Purpose |
| --- | --- |
| `strategy_lab_telemetry.py` | Safe checkpoint timing, sizes and cumulative counters. |
| `youtube_strategy_engine.py` | Opt-in timed storage/remote operations and buffered atomic checkpoint writes. |
| `strategy_lab_persistence.py` | Instrument durable operations; store separate terminal reason. |
| `strategy_lab_progress.py` | Preserve existing sidecars; share per-run telemetry with them. |
| `strategy_lab_jobs.py` | Document the retained progress-versus-durable boundary; existing sidecar logic preserved. |
| `cloud_strategy_lab_worker.py` | Preserve original timeout context and propagate it to the queue. |
| `hybrid_runtime/strategy_lab_bridge.py` | Exact-run diagnostic detail projection, including terminal queue state. |
| `hybrid_runtime/cloud_bridge.py` | Persist detailed errors and read-only enrichment without redispatch. |
| `hybrid_runtime/storage_jobs.py` | Atomic metadata-only enrichment of an already failed diagnostic. |
| `test_strategy_lab_diagnostic_budget.py` | Update timeout-stage expectations for separate terminal reason. |
| `test_strategy_lab_checkpoint_repair.py` | Focused timeout, identity, no-redispatch, resume and telemetry regressions. |
| `benchmark_strategy_lab_checkpoints.py` | No-network replay of original artifact through deployed and repaired runners. |
| `CHECKPOINT_THROUGHPUT_REPAIR.md` | Scope, evidence, measurements, caveats, tests and activation boundary. |

## Evidence files

All raw artifacts remain under
`.desktop-dev/diagnostics/spy-diagnostic-20260905T224216Z-705d4990/`:
`checkpoint-original.json`, `checkpoint-first.json`, `checkpoint-profile.json`,
`checkpoint-commit-profile.json`, `deployed-strategy-lab-jobs.py`,
`checkpoint-benchmark.json`, `checkpoint-benchmark-isolated.json`,
`checkpoint-repair-focused.xml`, `checkpoint-repair-suite.xml`, and
`checkpoint-repair-unrelated-ui.xml`, alongside the original observations and
terminal-state evidence.

Reproduce the offline benchmark from the canonical directory:

```sh
.venv-dev/bin/python benchmark_strategy_lab_checkpoints.py \
  --artifact-dir .desktop-dev/diagnostics/spy-diagnostic-20260905T224216Z-705d4990 \
  --output .desktop-dev/diagnostics/spy-diagnostic-20260905T224216Z-705d4990/checkpoint-benchmark-repeat.json
```

No new cloud validation is authorized or triggered by this command.
