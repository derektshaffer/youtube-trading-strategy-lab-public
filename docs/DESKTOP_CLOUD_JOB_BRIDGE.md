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
