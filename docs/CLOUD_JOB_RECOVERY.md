# Bounded cloud-job recovery

Deploy the matching workflow and worker revision together. Local tests are not
evidence that a cloud job completed. Do not reset retry counters, overwrite a
newer library, or create a replacement queue entry merely to clear a UI error.
The existing workflows also run on matching pushes to main. Merging this patch
can therefore start scheduled-style research automatically; obtain explicit
approval for that deployment effect before merging, not just for a manual retry.

## Profit First

The Continuous Trading Research workflow accepts an optional `job_id`. When
provided it runs `cloud_profit_first_worker.py` instead of the broad research
loop. New desktop publications pass their existing remote queue ID.

For an already-published desktop job, an authorized operator can dispatch this
workflow on the deployed branch with its exact remote `job_id`. The worker:

- requires exactly one desktop `autonomous_validation` item with selected strategies;
- claims only queued or eligible retry work within its existing attempt budget;
- leaves active/terminal jobs and unrelated stale jobs untouched;
- calls the existing strict validator without seeding work or constructing a Gemini router;
- does not change strategy eligibility or execute brokerage orders.

Blank IDs are rejected by the exact worker. Omitting the workflow input selects
the existing broad scheduled-worker behavior, so do not omit it for recovery.

## Strategy Lab

The first missing-file restore avoids one redundant download; subsequent
`load_latest()` calls reconcile against current cloud state. Cloud holdout
exposure writes are narrow compare-and-swap mutations that reapply the reuse
guard on every refreshed snapshot. Conflict protection remains enabled.

The exact Strategy Lab dispatch path does not recover unrelated stale jobs.
Newer permitted attempts can replace an older failure checkpoint, but completed
results always win. Failed checkpoints retain optimizer state for signature-
checked reuse. If a completed checkpoint already exists for the exact run and
ticker, the worker finalizes its queue record without calling market providers
or repeating optimization. Cloud completion requires a verified saved result;
a terminal durable failure now produces a failing workflow exit code.

An exhausted failed job is deliberately not reopened automatically. Inspect
its exact checkpoint and durable attempts first, and obtain approval for any
additional provider computation or explicit retry-budget change. If no result
or optimizer checkpoint survived, a fresh approved attempt may need to repeat
computation. A green workflow alone never proves a result was saved.

## Verification

Run `python3 -m pytest -q`. Focused regressions are in
`test_safe_cloud_job_recovery.py`. After authorized deployment/recovery, verify
the original queue ID, checkpoint identity, nonempty saved result, and desktop
attachment. Confirm no duplicate queue item, untouched unrelated running work,
and unchanged validation gates. Keep credentials and private research out of
public artifacts and pull-request descriptions.
