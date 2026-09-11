# Lossless cloud capacity and controlled recovery verification

This continues local commit `3273f14` in `/Users/Derek_1/Documents/cloud-backup-conflict-fix`. Production data, production source main, the original Trading Lab checkout, Scanner/Analyzer, and V5 repositories are outside the write scope. A dedicated source branch is used only to execute the controlled Actions test. No expensive research or orders are part of the test, and run #104 is not rerun or reconstructed.

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

`--preflight` runs only the acknowledgement gate. The production wrapper retains its fixed `python cloud_research_worker.py` command. A separate fixed fixture command is allowed only on `refs/heads/codex/cloud-backup-capacity-smoke`. There is no general command override.

## Controlled Actions procedure

The new workflow runs only on `codex/cloud-backup-capacity-smoke`, with read-only source/Actions permissions and existing private-backup credentials. It has no brokerage/provider keys, production scheduling, production ref update, or research dispatch. A unique `codex/recovery-capacity-fixture-<source-run-id>` branch in the existing private backup repository starts from a new root commit containing only synthetic fixture data. All fixture data writes are bound to that exact branch and `fixture/library.json`.

1. Run Python/Node regression tests. Read the production library through an adapter that rejects all writes; record only aggregate sizes/counts and hashes after an exact compression round trip.
2. Create a private fixture whose decoded size exceeds 100 MiB, with enough encoded bytes to exercise large Git transport. Verify worker and desktop reads agree exactly.
3. Execute the actual recovery wrapper, upload an encrypted synthetic completed-result envelope, then terminate the process with code 75 immediately after upload acknowledgement and before canonical synchronization. Verify the fixture remained unchanged. This is the workflow's one expected failed step; the workflow must validate its proof and complete successfully.
4. Update the checked-in smoke plan to `recover` with that source run ID. A second Actions run on a fresh hosted runner must block the actual wrapper before computation, download/decrypt the first run's artifact, invoke the recovery CLI, and verify one completed result plus unchanged historical fixture content.
5. Re-import the same immutable bundle into another fresh local store and verify no canonical SHA change. Verify private receipts unblock preflight.
6. Race two real private Git writers from the same base. Require at least one actual CAS conflict, bounded reconciliation, exactly four expected fixture results, and unchanged prior fixture content. Verify all artifact receipts afterward.

Only aggregate evidence JSON and encrypted recovery artifacts are uploaded. No plaintext production or fixture library is uploaded to public Actions artifacts. The isolated fixture branch/artifacts are retained as test evidence; there is no destructive cleanup of production history.

## Results

Pending the authenticated Actions runs. Local targeted checks and final run links/counts are recorded when complete. The 20 existing preliminary-execution/holdout-related test failures must be compared with the original revision; no integrity gate is relaxed to make those tests green.

## Production rollout remains separate

After the controlled smoke succeeds, review and publish compatible source to all active readers/writers, including installed desktop packages and pinned recovery/audit workflows. Keep compression off while doing so. Then, with explicit production migration/deployment authorization, take a current SHA-pinned backup and enable `GITHUB_BACKUP_STORAGE_FORMAT=gzip-v1` consistently for writers. Perform a hash-checked migration and cold restore, and verify result identities/counts, queue ownership, and recovery receipts. Do not test deployment by starting expensive research.

Current compression solves the immediate capacity constraint without deleting history. It does not recover run #104's missing new result, eliminate external/legacy writers, or establish research validity. The original encryption token remains required to decrypt retained artifacts.
