# Continuous Research persistence: local fix and deployment assessment

## Result and scope

This change is checkpointed locally on `codex/cloud-backup-conflict-fix`, in the isolated clone `/Users/Derek_1/Documents/cloud-backup-conflict-fix`, based on public main `d5eb766bf64f73997ef808391e9d62d8996f031c`. That is also run #104's source revision. No production backup was written, no code was pushed/deployed, no workflow was dispatched, and no research or live orders were run. The existing `/Users/Derek_1/Documents/New project` checkout and the separate V5 qualification repositories were not edited.

**SAFE TO DEPLOY as an unqualified production repair: NO.** The concurrency/recovery regression tests pass, with no new broader-suite failures. However, the current private backup is only **168 bytes below GitHub's 100 MiB single-file limit**. The patch deliberately fails closed above that limit; it does not compact, prune, or change historical research. An authenticated Actions artifact/preflight/recovery smoke remains unperformed. Existing broader-suite failures also remain.

The new store is explicitly selected by the normal Continuous Research `main()` only. Other applications retain their existing persistence behavior. They can concurrently publish independent updates that the new worker reconciles. The exact Profit First entry point still receives the original store; it does not accidentally acquire an artifact dependency. Shared low-level GitHub conflict classification and blob integrity checks are strengthened for all callers.

## What actually happened in run #104

Source: [Continuous Research #104](https://github.com/derektshaffer/youtube-trading-strategy-lab-public/actions/runs/34317234604), job `102355898602`, plus a read-only inspection of the private backup's reachable history and saved library.

| UTC on September 9, 2026 | Evidence |
| --- | --- |
| 06:01:11 | Workflow created at revision `d5eb766…`. Hosted Ubuntu/Azure runner. |
| 06:02:02 | Startup prints `auto-ml-78ee79b1bcee5334`, suite 7, six compared symbols, two provisional broad-transfer leaders, runtime 5,730.157 seconds. **Its completed_at is September 6, 23:03:21**, so this is a previously persisted result, not #104's new computation. |
| 06:03:16 | Worker announces ML job `rq-5ee1be6e460c60e2e2d17b`. |
| 06:03:36 | Last reachable backup commit before completion failure: `94eb0c431611e6a4de8c1f90d2b907fa977fc2b6`. The library is 104,466,396 bytes, blob `97bf68d9c28f1c4d7dbd971772cfe9314cacf555`. Its queue has this job running, attempt 2, worker `runnervmejwal:2275`. |
| 06:03:41 | New ML backfill starts: 24 stocks, 45 trading days. |
| 07:20:03 | Dataset complete: 61,155 labeled rows, 4,581.7 seconds. |
| 08:13:29 | Final aligned-row route comparison starts. |
| 08:16:18 | Trace reaches the post-computation `persist_store()` call and fails inside `_save_large_library`'s Git subprocess. Both final persistence and subsequent error-handler persistence fail. |
| 08:16:21–25 | Exit 1, hosted runner cleanup, workflow complete. |

Reachable backup commits in the entire 06:00–08:17 interval are only `d0c454d` (06:02:22), `c3fce0e` (06:02:52), `5e52756` (06:03:11), and `94eb0c4` (06:03:36). Their timing is consistent with this worker's startup/claim/status saves; generic commit authors do not conclusively identify a process. **There is no committed competing write in the history inspected during the new ML computation.** Exact competing-write time, payload, and writer therefore cannot be established.

The old classifier treated *any* Git stderr containing `rejected` as a concurrency conflict. That includes GitHub oversized-file and repository-policy rejections. With a starting file approximately 391 KB below the limit and a new ML result being appended, **size rejection is strongly supported, but not proven**: the old error discarded the original Git stderr and the unsynced result's byte count. Do not describe #104 as a confirmed concurrent-write incident.

The new computation ran from approximately 06:03:41 through completion shortly before 08:16:18—roughly 132 minutes, not the historic 95-minute runtime printed at startup.

## Writer audit and workflow overlap

These are source-confirmed entry points, not a claim that every installed instance or external service has been inventoried.

| Writer / path | Shared destination and coordination |
| --- | --- |
| `cloud_research_worker.py`: scheduled, push-triggered, manual broad worker; queue claims, web/specialist research, validations, Finder, ML, worker receipts, live-learning ingestion | Main library selected by backup repository/branch/path settings. Now uses the reconciled store. |
| `distributed_stock_finder.py`: dispatch/prepare, queue progress, shard completion bookkeeping, aggregate/finalization and recovery | `mutate_remote_library()` rereads and applies narrow mutations with its own bounded retries. Main library plus separate shard/checkpoint files. |
| `cloud_profit_first_worker.py`: exact/manual/recovery jobs | Narrow claim/failure mutations and the shared execution functions; original store retained. |
| `cloud_strategy_lab_worker.py`: scheduled poller, direct dispatch, diagnostic/finalize-only recovery | Main-library queue/result/checkpoint mutations through `mutate_remote_library`; separate concurrency group. |
| Local/hosted `trading_intelligence_app.py` and `strategy_lab_execution.py` | Main library via `StrategyStore.save`, including queue submission, saved results and Strategy Lab exposure/checkpoints. Multiple tabs/processes are possible. |
| Desktop `hybrid_runtime/cloud_bridge.py` / `GitHubJSONLibrary.write` | Queue publication, cancellation/recovery reconciliation and desktop cloud bridge writes; uses commit-based non-force CAS. Not serialized by Actions groups. |
| `strategy_lab_persistence.py` | Checkpoint save, checkpoint conflict recovery and lossless compaction save through supplied stores; destination may be main or dedicated checkpoint storage. |
| Manual sync / storage write verification / authenticated validation smoke | `StrategyStore.sync_cloud_backup`, `verify_cloud_write_access`, `youtube_strategy_app_core.py` manual controls, and `authenticated_validation_pipeline_smoke.py`. Configured destination matters. |
| Other configurable app writers: YouTube core, `machine_learning_lab_core.py`, `live_strategy_runner_page.py`, `explosive_stock_storage.py` / prescreen worker | Usually distinct default paths. They can target the same library if configured that way. Even a different file on the same branch can reject a simultaneous large-library non-force Git push. |
| Sidecars: `strategy_lab_progress.py`, distributed shard stores, live-learning outbox, system-health smoke | Different default files, potentially the same private branch. Branch-level races remain possible. |
| This patch's recovery acknowledgements | Immutable, small files under `<library-path>.recovery-receipts/<delta-sha256>.json`, only after a reconciled canonical save. They can advance the branch but never replace the main library. |

`strategy_integrity_cloud_audit.py`, `strategy_integrity_audit_job.py`, the research-library audit workflow and targeted revalidation audit's cloned backup input are readers/report producers, not established main-backup writers. The `.github/scripts` source-fix workflows change application source, not the private research backup. Direct external Git/API edits remain possible but no such writer was evidenced in this incident.

Continuous Research and Distributed Finder **already** share `trading-intelligence-library-writer` with `cancel-in-progress: false`. That is retained; the misleading comment claiming complete lost-update prevention is corrected. Cloud Strategy Lab intentionally has `strategy-lab-cloud-worker` and may overlap. Its overlapping run `34319937231` (06:37:48–06:38:41) logged **idle/no queued job**. Finder run `34314979177` ended at 05:28:46. Neighboring Continuous runs #103 and #105 did not overlap #104. Overlapping execution is not established as the cause.

## Algorithm before and after

Previously: write local JSON, compare the remembered timestamp with remote, then perform Contents API SHA save or a checked shallow-clone/non-force push. A collision was retried by replaying the same whole snapshot and old expectation. There was no durable three-way base/delta recovery bundle. The generic execution error handler also wrote a failed ML summary and failure receipt after persistence failure. The existing `fail_research_job` guard did already protect a locally terminal queue state from being converted to retry; remote stale recovery could nevertheless later reclaim a still-running cloud record.

Now:

1. Establish a destination-bound, compressed local base containing the exact loaded blob SHA and library. Refuse unjournaled local divergence or a missing common ancestor.
2. On save, atomically write/fsync a checksummed compressed envelope containing base, local delta snapshot, base SHA, destination and source workflow identity. Keep an immutable digest-named copy and a pending write-ahead file. Install the local library atomically. No network operation precedes the pending envelope.
3. In Actions, encrypt the envelope with authenticated AES-256-GCM and upload an immutable artifact. Confirm acknowledgement before canonical synchronization. No private plaintext, backup credentials or signed URLs are published in artifacts/logs. Encryption uses scrypt and the existing backup token; recovery requires that same token value.
4. Fetch latest cloud content. Validate structure and verify downloaded bytes against the Git blob hash. Reconcile **the original immutable base/local pair** with each new remote snapshot.
5. Write against **the newly read SHA** and existing timestamp expectation. Retain non-force Git push and cloned-file SHA comparison; do not git merge/rebase.
6. Verify the save response matches the reconciled document. Publish an immutable private synchronization receipt for the delta. Atomically update local base/library and acknowledge the pending envelope. If the cloud accepted a write but its response or receipt was lost, recovery detects the already-applied records and converges without duplicates.
7. Before future broad workers start, inventory earlier recovery artifacts using read-only Actions permission and check their private synchronization receipts. Missing/mismatched receipts or unavailable/incomplete inventory block computation. Inventory is bounded to 100 pages of 100 artifacts. Each new Continuous claim is tagged `cloud_persistence_protocol=1`; stale recovery preserves that claim and attempts instead of automatically rerunning it, even after artifact expiry/deletion. Explicit recovery/review is required.

Only typed, verified concurrency conflicts retry: at most four attempts (configurably clamped 1–8), exponential delay starting at 0.5 seconds, capped at eight seconds, plus 0–0.25 seconds jitter. Each attempt rereads/reconciles. Semantic conflicts, malformed state, size/policy/auth errors stop with a retained pending envelope. The worker catches `PersistencePending` separately and exits without fabricating failed computation metadata or another result/receipt. Network/storage/retention outages do not launch a computation retry.

## Semantic rules

- Immutable collections: research, validation, external research, worker, predictive ML and strategy-version records. New unique IDs append; identical IDs/content converge. An existing immutable result may not be rewritten, even when only the local side changed it. Different payloads under one ID fail with the collection/ID path.
- Remote collection order/history is retained; local new IDs are prepended. Local history truncation/deletion is not propagated during reconciliation. No remote record is silently dropped. Duplicate or missing stable identities and malformed collections fail. Videos use URL identity; other known collections use `id`.
- Other known record collections are merged atomically per record. A one-sided edit can apply against the unchanged base record. Both-sided changes or remote deletion of a locally edited record fail. No field-level splicing of models, strategies or checkpoints.
- Queue records are atomic. Unchanged local running state never overwrites newer terminal state or owner. A locally changed record requires the remote record to remain equal to its base, or to already equal the complete local record. Completion/claim transitions preserve job type, payload, creation identity, dedupe/max-attempt settings and retry ancestry. Claims advance exactly one attempt; completions retain started-at, workflow/claim identity and attempt count. Foreign ownership, regressing timestamps, terminal regression and unsupported transitions fail closed.
- New ML results bind `origin_job_id` and `origin_claim` (attempt, worker, start and cloud-run identity). Completion must reference that exact ML record/namespace/claim. Older results are unchanged.
- `research_system` entries are atomic settings/projections. Independent settings merge; competing ML summaries/model projections conflict. The three `last_worker_*` display fields are explicitly derived together from the newest immutable worker receipt, with ID as a deterministic tie-breaker; both writers' receipts survive.
- Unknown top-level fields use a conservative atomic three-way rule. Unsupported changes/deletions fail rather than guessing.

## Recovery evidence and limits

Run #104 has **zero workflow artifacts**. Its workflow only cached Python dependencies; it did not cache/upload local strategy data, write-ahead journals or completed result bundles. Its temporary Git commit and `.youtube_strategy_data` lived on an ephemeral hosted runner. No accessible runner workspace survives.

At current inspected backup commit `ebbce51ba7f231f754f9f153eac726f9c1adcf32`, the library is 104,857,432 bytes. It still has 12 ML runs; the newest is the same unchanged September 6 result. The #104 job is failed, attempt 3, no completed-at/result reference, with a later stale-worker exhaustion error. **No actual copy of #104's new completed result was found; recovery without recomputation is not established and was not attempted.** The older suite-7 result is already durable and requires no recovery. Logs/summaries are not sufficient to reconstruct the missing full result.

Future envelopes support local-process restart and separate-machine artifact recovery. Artifacts are requested for 30 days, subject to repository limits. Retain the original encryption key through recovery; rotating/deleting that key can make encrypted artifacts unreadable. Expired/deleted artifacts cannot be restored by this patch; protocol-tagged claims still refuse automatic replay. A failure before the first successful local journal write, disk loss, artifact-service outage or killed runner before upload can still prevent off-runner retention. The code does not claim otherwise. Preflight intentionally blocks on any unresolved earlier envelope, including an interrupted status/claim save, until inspected/recovered.

The worker directory is process-owned. Separate workers need separate directories; this is not an interprocess database transaction layer for two processes sharing one local directory. Genuine mutable-record conflicts require review. Old installed writers/reclaimers do not gain the new protocol until updated. An external force push or authorized history deletion is outside the CAS guarantee.

## Verification

All tests use mocks/fixtures/temp directories. The Git race uses a temporary local bare repository. No production persistence was tested by writing it.

- Focused persistence/worker/exact-job/queue suite: **118 passed** at the final focused run.
- New Python regression module: **54 tests** covering unrelated writers, duplicates, differing payloads, queue/ownership/ancestry, ML associations, repeated CAS races/exhaustion, process/disk crashes, restart, uncertain acknowledgements, corruption, remote disappearance, idempotence, immutable receipts, size/rejection classification and stale protocol claims.
- Node encryption/preflight suite: **7 passed**; authenticated encryption, tamper/wrong-key rejection, unresolved/foreign receipt and inventory failure gates.
- Broader 18-module run: **420 passed, 20 failed, 1 skipped**. Baseline comparison of the same 17 pre-existing modules at `d5eb766…`: **366 passed, 20 failed, 1 skipped**. The failed node-ID sets match exactly; failures are legacy preliminary-execution/holdout expectations and the dependent Streamlit smoke, not new persistence regressions. No failing tests were excluded or integrity gates relaxed. The existing skip remains.
- One existing download fixture now computes its real blob SHA; one workflow assertion now checks the conditional wrapper and its fixed Python command. Neither change modifies research outcomes.
- Python compilation and `git diff --check` pass. Locked `@actions/artifact` 6.2.1 dependencies report zero npm audit vulnerabilities. Authenticated Actions upload/preflight/recovery, production-size peak memory and deployed runtime remain unverified.

## Changed files

- `cloud_backup_reconciliation.py`: explicit semantic merge and claim/result validation.
- `reconciled_strategy_store.py`: durable base/outbox, bounded CAS reconciliation, idempotent recovery.
- `recover_cloud_research.py`: inspect-by-default CLI; explicit `--sync` only publishes the saved envelope.
- `youtube_strategy_engine.py`: typed conflicts, SHA expectation/integrity, size and Git rejection diagnostics, immutable synchronization receipts.
- `cloud_research_worker.py`: opt-in reconciled store, ML claim binding, preserve post-computation snapshot, persistence-specific failure handling, protocol-tagged claims.
- `trading_research_orchestrator.py`: preserve tagged stale claims until recovery/review.
- `.github/workflows/continuous-trading-research.yml`: recovery wrapper installation/use, read-only artifact inventory permission, existing non-canceling concurrency retained.
- `.github/recovery-artifact/{action.yml,index.cjs,package.json,package-lock.json,.gitignore,test.cjs}`: locked artifact client, encryption/upload, startup preflight and tests.
- `test_cloud_backup_reconciliation.py`, `test_youtube_strategy_engine.py`, `test_safe_cloud_job_recovery.py`: new regressions and updated SHA/workflow fixtures.
- This report.

## Exact next steps

1. Review this local commit. Resolve the private library's capacity problem through a separately reviewed **lossless** storage change, verifying hashes/round trips and compatibility of every reader/writer. Do not delete history to make space and do not rerun #104.
2. Before production deployment, run the wrapper and recovery CLI in a controlled Actions workflow against a temporary private fixture backup: upload encrypted envelope, interrupt before sync, verify the next run blocks, recover without computation, verify the private receipt unblocks it, then exercise a real concurrent CAS race. Production backup stays untouched during this smoke. Investigate/reconcile the pre-existing broader-suite failures separately.
3. Only after those checks and deployment authorization, publish/deploy the reviewed commit to the workflow source and update any worker using the stale-claim helper. Retain `cancel-in-progress: false` and the recovery wrapper. Do not dispatch expensive research as the deployment test.
4. For a **real future retained artifact**, download the `.tilrec` file. In an environment already configured for the exact backup destination and original encryption token:

```sh
node .github/recovery-artifact/index.cjs --decrypt /absolute/path/result.tilrec /absolute/path/result.json.gz
python recover_cloud_research.py /absolute/path/result.json.gz --directory /absolute/path/isolated-recovery
# Only when authorized to publish this concrete saved result:
python recover_cloud_research.py /absolute/path/result.json.gz --directory /absolute/path/isolated-recovery --sync
```

The first Python command only inspects metadata. The last uses the original base/local delta, fetches current remote, applies the same semantic/CAS checks and records acknowledgement; it imports no research execution function. Destination mismatch or genuine record conflict stops the operation. This procedure cannot recover #104 without an actual retained bundle/result.
