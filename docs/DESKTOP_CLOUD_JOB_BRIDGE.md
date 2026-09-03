# Desktop cloud-job bridge

Status: implementation milestone

## Purpose

The desktop app and the existing cloud validation worker now share one durable
Profit First job instead of launching parallel copies of the same validation.
The bridge does not contain strategy, optimization, or validation logic. It only
publishes and reconciles work owned by the existing Trading Intelligence
research queue.

## Flow

1. The desktop submits `strategy.profit_first_validation` to its authenticated
   loopback service.
2. The router marks that job as cloud-only and stores it in local SQLite.
3. The cloud bridge loads the configured private GitHub research library.
4. `profit_first_validation_batch` selects the current strict batch and dedupe
   key from the authoritative library.
5. The bridge either attaches to the existing remote item or appends exactly one
   `autonomous_validation` queue item.
6. The existing continuous-research workflow is nudged through
   `workflow_dispatch`. A dispatch-permission failure does not lose the queued
   job; the scheduled worker can still claim it.
7. Remote status, stage, progress, result, validation records, failure, and
   cancellation are reconciled back into the original desktop job.
8. Quitting the app stops polling only. Remote validation continues, and the
   same local job reattaches on the next launch.

## Concurrency and recovery

- Local submission uses the existing job idempotency key.
- Remote publication uses the existing Profit First dedupe key.
- The remote queue ID is deterministic from that dedupe key.
- Library writes use Git blobs/trees/commits and a non-force branch update.
- A concurrent library writer causes an explicit reload/retry instead of a lost
  update.
- A separate SQLite link store contains only non-secret remote identifiers.
- If that link store is lost, the bridge recovers by the remote dedupe key.
- Profit First dispatch sends no inputs, matching the continuous-research
  workflow contract without changing worker scope or validation rules.
- Only an actual dispatch call updates the dispatch-attempt timestamp. Queue
  polling preserves the recorded dispatch outcome, including through a later
  connection failure. Reattaching a queue item never silently redispatches it.

## Explicit reconnection of a recovered Finder run

In **Durable Jobs**, select a failed cloud Stock Finder entry and choose
**Reconnect recovered cloud run**. The desktop stays responsive while the
authenticated sidecar checks the private library. This action never retries
research, writes the remote queue, or dispatches a workflow.

Reconnection requires the saved repository, branch, path, exact remote job ID,
request symbol/profile, and any desktop ownership marker to match. The cloud
job must be queued, retrying, running, or complete; completion also requires its
matching saved report. Missing/ambiguous jobs, changed connections, cancellation,
protected local results, and network failures fail closed.

The local schema-v2 upgrade is additive. A transaction archives the prior
failure state in `cloud_job_recoveries`, retains all job events, and explicitly
reopens only the selected failed cloud Finder record. Ordinary terminal-state
transitions remain forbidden. The same job ID then resumes normal progress and
result reconciliation. Recovered entries retain their audited remote identity
across app restarts or link-store loss; a missing remote record never falls back
to publishing new work. Repeated clicks cannot create a new attempt or queue item.

After a connection timeout, refresh jobs before trying again: local verification
may have finished even if the response did not reach the UI. Failed checks are
shown beside the reconnect button and do not launch research.

## Large-library uploads

Serialized libraries above 1,000,000 UTF-8 bytes use compressed Git transport
instead of posting the entire JSON document to the GitHub Git Blob REST API.
This requires Git (or the macOS command-line tools) on the desktop. Smaller
documents retain the existing REST writer.

The uploader fetches the configured branch into a private temporary bare
repository and requires its tip to match the revision that was read. It hashes
the exact JSON bytes, changes only the configured library entry, and creates
a commit with that expected revision as its sole parent. A non-force push
rejects concurrent updates. Fetch and push each have a 300-second timeout;
a failed push is never automatically replayed. If its response is lost, a
read-only remote-ref check confirms whether the exact commit was accepted.

Temporary credentials are supplied through an askpass environment, never a
token-bearing URL or file. User Git configuration, hooks, filters, tracing, and
unrelated provider credentials are excluded; TLS verification stays enabled.
The temporary repository and helper are removed on success or failure.

An upload failure leaves the original desktop jobs queued, with an actionable
link-store error, and dispatches no workflows. The next bridge pass reloads the
authoritative library and uses the existing deduplication rules. This transport
does not remove history, relax validation, or bypass GitHub file-size policies.

## Security

- GitHub tokens are loaded from an environment override for CI or from macOS
  Keychain for the desktop app.
- Tokens are never written to job payloads, the research library, SQLite,
  diagnostic links, or logs.
- The bridge adds no brokerage order path and does not relax validation gates.
- The current Streamlit application remains independently deployable and usable.

## Current boundary

The bridge supports Profit First strict validation first. Very Deep, autonomous
research, book/video extraction, and ML already route to cloud by policy, but
will be attached to this publisher only after the Profit First path passes its
production reconnect and conflict tests.
