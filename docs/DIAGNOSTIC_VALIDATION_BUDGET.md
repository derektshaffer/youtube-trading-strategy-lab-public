# Opt-in Strategy Lab execution budget

This is an execution constraint on the existing validation contract, not a new
validation mode. No optimization, walk-forward, holdout, robustness, fidelity,
anti-leakage, evidence, or promotion gates are changed.

The explicit supported fields are `diagnostic_mode: true`,
`diagnostic_max_attempts: 1`, and `diagnostic_timeout_minutes: 20`.
Timeouts from 5 through 30 integer minutes are supported; other attempt counts
and malformed bounds are rejected. Without the flag, normal jobs retain three
attempts and the existing 330-minute workflow budget.

Twenty minutes is a diagnostic ceiling, not a promise of completion. Retained
history did not provide a reliable measured Quick runtime. Quick still executes
main optimization, up to three walk-forward optimizations, and robustness checks;
this warrants tens of minutes rather than a seconds-long artificial replay.

## Enforcement chain

- Backend JSON/SQLite retains the request. Bridge normalization validates bounds.
- Queue publication sets `max_attempts=1`; dispatch carries all three fields.
- Exact and scheduled claims re-enforce one attempt even if queue `max_attempts`
  was changed. Claim persists the attempt start and absolute deadline.
- Worker strategy resolution still uses only canonical IDs and the fidelity gate.
- The existing executor runs in a supervised child process only for diagnostics.
  The parent uses the remaining persisted deadline and a monotonic clock, then
  terminates the child process group (kill escalation after two seconds).
- The workflow consumes diagnostic inputs, checks them against the exact queue
  payload, and derives a 22-minute execution-step backstop for a 20-minute request.
  The diagnostic whole-job cap is 45 minutes, allowing dependency setup/cleanup;
  it is NOT a 45-minute validation allowance. Normal whole-job cap remains 330.
- Workflow interruption invokes exact-job finalization without claiming again.
  Scheduled claims also reap expired diagnostic jobs without retrying them.
- Timeout saves `status=failed`, `failure_kind=execution_timeout`, and checkpoint
  `execution_error.category=infrastructure`. No strategy verdict is synthesized.
  Progress and optimizer state are retained. Terminal checkpoint overlay cannot
  show a timed-out diagnostic as running.

## Operational limits

Remote persistence requires GitHub availability. Runner loss or an outage can
delay reconciliation until workflow finalization or the next scheduled worker
(currently every 30 minutes) can write again. No system can guarantee a durable
write while its storage provider is unreachable. The one-attempt budget remains
in the durable queue, so reconciliation does not authorize another computation.

Local tests and an offline dry run do not activate these bounds in an older
deployed workflow/worker. The matching bridge and cloud changes must be activated
through the separately authorized normal release process before submission.
This repair does not publish, deploy, submit validation, alter credentials, or trade.
