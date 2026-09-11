# Lossless cloud capacity and controlled recovery verification

This continues local commit `3273f14` in `/Users/Derek_1/Documents/cloud-backup-conflict-fix`. Production data, production source main, the original Trading Lab checkout, Scanner/Analyzer, and V5 repositories are outside the write scope. A dedicated source branch is used only to execute the controlled Actions test. No expensive research or orders are part of the test, and run #104 is not rerun or reconstructed.

**Completed:** lossless capacity implementation and authenticated cross-run upload/recovery/CAS smoke. **Not performed:** production migration, production source deployment, installed-app replacement, or restart. Tested source is `311ccd26a635f5b1317bd8fe9cbe343adeb3970f` on `codex/cloud-backup-capacity-smoke`; its runtime code matches `69b3464`, with only the smoke plan changed to select the retained fixture. A final documentation/trigger-only commit records these results. Public production main remains `d5eb766bf64f73997ef808391e9d62d8996f031c`.

## Storage contract

`cloud_library_codec.py` encodes the exact existing JSON bytes as a versioned binary envelope: `TILJSON1`, decoded byte count, SHA-256 of the original JSON, and one gzip stream. It verifies the count/hash/complete stream on decode. Truncation, appended streams, corruption, unknown versions, excessive decoded size, or a wire blob above GitHub's limit fail closed. Nothing is pruned, deduplicated, summarized, re-ranked, or revalidated by compression.

Readers accept legacy JSON and the new format. Writes remain plain JSON unless `GITHUB_BACKUP_STORAGE_FORMAT=gzip-v1` is explicitly set; with that setting, libraries of at least 1 MiB are compressed. This permits compatible readers to be installed before any storage migration. Local app files and exports stay JSON. The cloud CAS SHA identifies the encoded Git blob; it is never replaced with the decoded JSON hash.

The current bounds are 512 MiB decoded JSON and 100 MiB per encoded Git blob. This provides headroom, not unlimited storage. The current backup's measured compression and production-sized memory results must be included below before judging readiness. The GitHub limit is documented in [About large files on GitHub](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).

Older JSON-only readers reject the binary header. They cannot interpret a manifest as an empty library and silently write it back. They still need upgrading before compression is enabled. The independent installed desktop package is not updated by changing cloud source.

## Reader/writer coverage

| Surface | Compatibility path |
| --- | --- |
| Continuous Research, distributed Finder, cloud Strategy Lab, exact Profit First, Streamlit apps, existing recovery CLI and checkpoint/progress stores | Shared `GitHubCloudBackup.read_library/save_library`; plain local restore remains readable. |
| Desktop cloud bridge | `hybrid_runtime/github_library.py` verifies encoded Git SHA before decode; writes encoded bytes through its existing non-force CAS transport. |
| Research-library audit and targeted Profit First audit of Git clones | Shared `read_library_file` decodes the clone without rewriting it. |
| Strategy integrity audit | Uses the shared private/hash-verified reader rather than a separate raw JSON decoder. |
| Old installed apps, old workflow revisions, manual raw JSON scripts | Must upgrade before compressed writes are enabled; old readers fail closed. |

Compressed cloud bytes and ordinary local JSON have different Git blob SHAs. The old cold-session shortcut may therefore do a full content read rather than a metadata-only match; it does not claim the two byte streams are identical. Normal cached-session behavior is unchanged.

## Recovery hardening needed for an isolated smoke

Recovery artifact names now include a SHA-256 identity for repository, branch and library path. A private fixture artifact cannot block unrelated production work. Legacy artifacts remain checked conservatively. Unknown recovery formats fail closed. A worker restart in the same Actions run no longer skips unresolved artifacts simply because their run ID matches.

The authenticated smoke exposed a real artifact-retention defect in `3273f14`: the pinned `@actions/artifact` 6.2.1 package exports ESM imports only, but the wrapper used CommonJS `require`. This raised `ERR_PACKAGE_PATH_NOT_EXPORTED` before upload. The wrapper now uses asynchronous `import`, and the Node suite loads the actual installed SDK. Allowlisted stage/category diagnostics identify failures without printing private payloads, credentials, or signed URLs. The separate smoke's download check also now preserves the SDK's required `sha256:` digest prefix; its first recovery attempt rejected an intact download before any synchronization.

A real local Git receive race also reproduced a missing concurrency classification: `cannot lock ref 'refs/heads/main': is at <new SHA> but expected <old SHA>` followed by `failed to update ref`. Git can emit this when two pushes pass their initial checks before either updates the branch. The worker and desktop transport now recognize different, valid object IDs for the exact requested ref. Generic lock failures, equal/malformed IDs, another branch, and policy/size rejections remain errors. This preserves non-force writes and lets the reconciled worker use its existing bounded reread/merge/CAS retry. Tests cover both transports and wrong-ref/invalid-ID cases.

`--preflight` runs only the acknowledgement gate. The production wrapper retains its fixed `python cloud_research_worker.py` command. A separate fixed fixture command is allowed only on `refs/heads/codex/cloud-backup-capacity-smoke`. There is no general command override.

## Controlled Actions procedure

The new workflow runs only on `codex/cloud-backup-capacity-smoke`, with read-only source/Actions permissions and existing private-backup credentials. After verification its push trigger is restricted to changes of `.github/cloud-backup-smoke-plan.json`, so publishing documentation or later source changes cannot accidentally replay a completed fixture. It has no brokerage/provider keys, production scheduling, production ref update, or research dispatch. A unique `codex/recovery-capacity-fixture-<source-run-id>` branch in the existing private backup repository starts from a new root commit containing only synthetic fixture data. All fixture data writes are bound to that exact branch and `fixture/library.json`.

1. Run Python/Node regression tests. Read the production library through an adapter that rejects all writes; record only aggregate sizes/counts and hashes after an exact compression round trip.
2. Create a private fixture whose decoded size exceeds 100 MiB, with enough encoded bytes to exercise large Git transport. Verify worker and desktop reads agree exactly.
3. Execute the actual recovery wrapper, upload an encrypted synthetic completed-result envelope, then terminate the process with code 75 immediately after upload acknowledgement and before canonical synchronization. Verify the fixture remained unchanged. This is the workflow's one expected failed step; the workflow must validate its proof and complete successfully.
4. Update the checked-in smoke plan to `recover` with that source run ID. A second Actions run on a fresh hosted runner must block the actual wrapper before computation, download/decrypt the first run's artifact, invoke the recovery CLI, and verify one completed result plus unchanged historical fixture content.
5. Re-import the same immutable bundle into another fresh local store and verify no canonical SHA change. Verify private receipts unblock preflight.
6. Race two real private Git writers from the same base. Require at least one actual CAS conflict, bounded reconciliation, exactly four expected fixture results, and unchanged prior fixture content. Verify all artifact receipts afterward.

Only aggregate evidence JSON and encrypted recovery artifacts are uploaded. No plaintext production or fixture library is uploaded to public Actions artifacts. The isolated fixture branch/artifacts are retained as test evidence; there is no destructive cleanup of production history.

## Results

Verified on September 11, 2026:

- Local targeted suite: **174 passed**. Node: **13 passed**, including the real installed artifact SDK import. Final Actions runs repeat 139 storage Python tests and 13 Node tests on Python 3.12 / Node 24.
- The broader affected engine/recovery/audit comparison produced **139 passed, 20 failed** both at original `3273f14` and final code `69b3464`. The exact 20 failing test IDs match. These are existing preliminary-execution/holdout-related failures; no integrity gate was relaxed.
- Successful final upload/interruption run: [34628386535](https://github.com/derektshaffer/youtube-trading-strategy-lab-public/actions/runs/34628386535), source `69b3464d41573dc6760cd872646b91858f9cdd8d`. The intentional exit 75 happened after encrypted artifact acknowledgement and before canonical synchronization. The private fixture's blob remained unchanged. An earlier retain run `34626986583` also passed.
- Successful separate-run recovery/CAS test: [34628661131](https://github.com/derektshaffer/youtube-trading-strategy-lab-public/actions/runs/34628661131), source `311ccd26a635f5b1317bd8fe9cbe343adeb3970f`. The actual worker wrapper blocked on the unresolved first-run artifact before computation. Authenticated download verified the artifact hash; decryption and the real recovery CLI restored exactly one completed result. Reimport into another fresh local directory left the canonical SHA unchanged. Private receipts then unblocked preflight.
- The real two-writer GitHub race produced **one server ref-update CAS conflict**, which the new exact classifier recognized. The existing bounded reconciliation succeeded, retaining **all four expected fixture results** and byte-identical historical content. Final preflight passed for every retained artifact in the fixture scope.
- Recovery blob: `46a5562e25f26f74baf75a8fa6032af70db71b2d`; final reconciled race blob: `77d14f2a76f2b683425551076fa27adc5f5be3d8`. Two concurrent 114.76 MB fixture stores peaked at **1,837,932 KiB RSS (about 1.75 GiB)** on the hosted runner. This is storage/recovery verification, not a claim about research performance or validation quality.

### Read-only production capacity measurement

| Measurement | Value |
| --- | ---: |
| Original JSON bytes | 104,857,432 |
| Encoded bytes | 5,439,003 |
| Reduction | 94.813% |
| Encoded headroom below the 104,857,600-byte Git blob limit | 99,418,597 bytes |
| Original headroom | 168 bytes |
| Peak process RSS for final read/compress/decode/compare | 680,112 KiB (about 664 MiB) |

The source blob was `c771a334d57292dafe721beb298fe85404be66b9`. Before and after SHA-256 were exactly `ff372b14c264f32f1a7e6240c0578b28966fd805482dc350364c824d192162a7`. All original bytes and collection counts were retained, including 50,000 stock/strategy configuration ledger entries, 450 queue records, 300 worker runs, 221 hypotheses, 180 strategies, and 12 ML runs. This measured a copy in memory; the production file was **not migrated**.

### Durable fixture evidence

The final private fixture branch is `codex/recovery-capacity-fixture-34628386535`, path `fixture/library.json`, with 114,760,469 decoded bytes and 1,732,751 encoded bytes. Both real worker and desktop adapters read it identically. Baseline blob: `d128d9e424c428161be6e36e8564784be5db52f4`; baseline canonical JSON SHA-256: `6d3fa43cf12f0d662060fe1a40bf326115c1c9a2e7d8d1b03123ea5579653731`.

Retained encrypted artifact ID: `10274738465`; completed delta SHA-256: `6daa82a91b94cd467870ebd4f7a8351fd9125c677afa0f772d98803b17cca817`; artifact archive SHA-256: `006778047c1f7d52bb9429ffb430b2474f82dcbe1f553348706ce300e73b4d9e`. The artifact is configured to expire October 11, 2026. Its original encryption token is required for decryption.

Initial diagnostic runs `34625713437` and `34626674930` failed before artifact upload because of the SDK import defect. Recovery attempt `34627181803` downloaded the earlier intact encrypted artifact but stopped on the smoke's checksum-prefix mismatch before decryption/sync. Run `34627727434` then passed actual recovery, idempotent reimport and acknowledgement preflight, but failed during its race; its raw child failure was withheld, so the precise GitHub rejection from that attempt cannot be recovered from its logs. The exact server ref-CAS error described above was reproduced independently using two real local pushes and a synchronized receive hook, then observed and successfully handled in final GitHub run `34628661131`. A fresh fixture was used for the final full test. Earlier fixture branches and encrypted pending artifacts remain retained as evidence and have distinct destination scopes.

## Files changed since `3273f14`

- `cloud_library_codec.py`: versioned exact-byte compression, bounded verified decoding, legacy compatibility.
- `cloud_git_errors.py`, `hybrid_runtime/github_git_upload.py`, `test_cloud_backup_reconciliation.py`, `test_hybrid_github_git_upload.py`: exact server ref-CAS mismatch recognition for worker/desktop, with wrong-ref/invalid-ID/policy failures kept distinct.
- `youtube_strategy_engine.py`, `hybrid_runtime/github_library.py`: shared worker/desktop readers and writers, encoded Git SHA preservation, plain JSON local restore.
- `research_library_audit.py`, `profit_first_targeted_audit.py`, `strategy_integrity_audit_job.py`: compatible read-only audit paths.
- `reconciled_strategy_store.py`: secret-safe artifact failure diagnostics.
- `.github/recovery-artifact/index.cjs`, `test.cjs`: real SDK import, destination-scoped artifact identities, current-run preflight, diagnostics and Node regressions.
- `test_cloud_library_codec.py`: corruption/bounds, byte preservation, worker/desktop/cold restore, compressed CAS, audit and fixture scope regressions.
- `cloud_backup_capacity_smoke.py`, `.github/recovery-artifact/smoke.cjs`, `.github/recovery-smoke/action.yml`, `.github/recovery-smoke/index.cjs`, `.github/cloud-backup-smoke-plan.json`, `.github/workflows/cloud-backup-capacity-smoke.yml`: isolated authenticated two-run storage/recovery test.
- This report and the continuation note in `docs/CLOUD_BACKUP_CONFLICT_FIX.md`: results, rollout limits, historical incident context.

## Production rollout remains separate

After the controlled smoke succeeds, review and publish compatible source to all active readers/writers, including installed desktop packages and pinned recovery/audit workflows. Keep compression off while doing so. Then, with explicit production migration/deployment authorization, take a current SHA-pinned backup and enable `GITHUB_BACKUP_STORAGE_FORMAT=gzip-v1` consistently for writers. Perform a hash-checked migration and cold restore, and verify result identities/counts, queue ownership, and recovery receipts. Do not test deployment by starting expensive research.

The production workflows do not yet map a repository variable with that name into their process environment. Setting a GitHub variable alone will not enable compression. A later rollout must explicitly map it in every applicable writer job (including Continuous Research, distributed Finder jobs and Cloud Strategy Lab), and configure/restart desktop and Streamlit writer processes with the same setting. Updated writers left in the default `json` mode can attempt to expand the backup again; all active writers must be accounted for before migration. Old pinned source revisions and independent desktop packages require their own update. No such environment change or restart was performed here.

Current compression solves the immediate capacity constraint without deleting history. It does not recover run #104's missing new result, eliminate external/legacy writers, or establish research validity. The original encryption token remains required to decrypt retained artifacts.
