# Trading Intelligence hybrid desktop architecture

Status: foundation milestone

## Non-negotiable boundaries

- The existing Streamlit application remains independently deployable until the
  desktop release passes parity and recovery tests.
- Existing trading, risk, and validation engines remain authoritative. The
  desktop migration must not create a second implementation of those rules.
- Quick work can run locally. Very Deep search, autonomous research, predictive
  ML, and strict large-universe validation default to durable cloud workers.
- A fast candidate screen may rank research ideas, but cannot bypass untouched
  holdout, chronological walk-forward, execution stress, or portability gates.
- The Scanner/Analyzer remains a separate product during this phase.

## Components introduced in milestone 1

### `hybrid_runtime`

A standard-library core shared by future desktop and cloud adapters:

- deterministic local/cloud routing with a visible reason,
- SQLite job and event persistence,
- idempotent submission and active-job deduplication,
- transactional claiming and worker heartbeats,
- stale-lease recovery,
- cancellation state,
- cache metadata with fingerprints and expiry,
- loopback-only service security,
- macOS Keychain access,
- real adapter for the existing Profit First candidate planner.

### Optional loopback API

FastAPI and Uvicorn are isolated in `requirements-desktop.txt`; importing the
core does not add dependencies or startup cost to Streamlit. The API requires a
random bearer token written to a mode-0600 file and disables public API docs.
It offers route previews, job submission/status/cancellation, event polling, and
server-sent progress events.

### Framework spikes

PySide6 and Tauri use the same API and job contract. No framework is selected
until both are measured on the user's Apple Silicon Mac.

## Durable job state machine

```
queued -> claimed -> downloading_data -> preparing_features -> searching
       -> optimizing -> validating -> saving -> complete
```

Any active state can fail, enter retry wait, or move through cancellation.
Terminal jobs cannot be silently reopened. Progress cannot move backward.

## Data migration sequence

1. Keep the existing JSON research library authoritative.
2. Build a read-only SQLite index and validate record parity.
3. Move job metadata and cache metadata to SQLite.
4. Store candles/features as fingerprinted partitioned artifacts.
5. Dual-write selected new records with reconciliation checks.
6. Switch primary reads only after migration and restore tests pass.
7. Keep JSON import/export and rollback until at least one stable release.

## Next milestone

- profile current startup/search stages,
- add candle/feature artifact storage and incremental refresh,
- connect the service to existing quick stock analysis,
- introduce a cloud API/worker adapter using the same contracts,
- measure the two desktop spikes on Apple Silicon,
- port the Profit First vertical slice only after the framework decision.
