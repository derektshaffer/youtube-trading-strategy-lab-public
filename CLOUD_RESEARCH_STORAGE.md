# Cloud research storage: compatibility-gated repair

The intelligence library uses a single Git blob in the private backup repository.
GitHub's 100 MiB limit is per file, not a 100 MiB account or repository quota.
Deleting other files or old Actions artifacts cannot reduce this blob.

`cloud_library_storage.py` accepts legacy JSON and a versioned compressed JSON
envelope. zlib compression preserves the original JSON bytes; base64 permits
normal GitHub JSON transport; a SHA-256 and both byte counts are checked before
any record is exposed. Corrupt, truncated, concatenated, oversized, or unknown
formats fail closed. No collection or row is pruned, deduplicated, or rotated.
Local StrategyStore files remain plain JSON, including cold restores.

## Limits and visibility

- Provider hard cap: 100 MiB per Git blob.
- Application write cap: 95 MiB stored; all cloud writers refuse above it.
- Continuous worker preflight: another 5 MiB stored reserve, before startup work
  and before claiming each job. This is a capacity check, not a reservation or
  prediction of maximum job output.
- Expanded restore cap: 512 MiB. Larger evidence needs a separately reviewed
  external artifact architecture. Compression does not make capacity unlimited.
- Existing UI health probes expose size/headroom failures through their existing
  blocked/error path, even if the repository is reachable and once accepted a write.
- Capacity failures do not run cloud-only retry loops. Completed local output is
  retained. Network retries and compare-and-swap conflict protection remain.
- No automatic cleanup, queue launch/cancellation, history rewrite, or provider
  policy bypass is introduced. Older Git versions are retained, so total repository
  history can still grow. Monitor it independently of the per-file cap.

## Compatibility and activation

**Do not migrate the live file while any older desktop, hosted app, packaged app,
worker, diagnostic script, or raw-JSON consumer still reads it.** Older readers do
not understand the envelope. This patch leaves compression OFF by default.
`TRADING_LAB_COMPRESSED_CLOUD_STORAGE=1` opts an upgraded writer in; an upgraded
reader/writer keeps an already compressed file compressed without that setting.
Do not use this setting as a substitute for an inventory of all consumers.

1. Stage and test this patch on every cloud storage reader/writer. Preserve local
   uncommitted changes; do not replace the canonical checkout wholesale.
2. After the protected recorder window, arrange a maintenance window with no
   cloud writers or stale app clients. Deployment of cloud_research_worker.py to
   main itself triggers the Continuous Trading Research workflow. Do not merge
   as an incidental publication step. Coordinate scheduler/workflow behavior
   explicitly; do not launch the 168 queued records as a migration test.
3. Pin the current private branch revision and download/hash the exact original
   blob. Build the compressed candidate; restore it and compare exact bytes,
   every collection, queue IDs/statuses, and provenance digests.
4. Make a single non-force compare-and-swap update of only the intelligence
   library path, with the same document values and timestamp. Abort on branch
   movement. Keep the old commit and original blob as the rollback source.
5. Read back from the pinned new revision with BOTH readers. Verify every value,
   every queue entry and all other private repository blob SHAs are unchanged.
   Use an isolated local StrategyStore to verify cold restore and subsequent
   persistence; do not use recorder evidence directories.
6. Establish end-to-end cloud persistence in a bounded, explicitly authorized
   maintenance check before allowing the research backlog to run.

Rollback must never overwrite newer research. Pin the latest revision and verify
whether any writes occurred after migration. The original uncompressed file is
already too close to 100 MiB to support further work; rollback restores readability,
not capacity. Any rollback or recovery must retain post-migration evidence.

## Remaining durability limits

This patch does not recover historical results that existed only on an expired
Actions runner. A job may produce more than the reserved 5 MiB, and non-capacity
cloud failures can still leave results only on a runner's local disk. A private,
versioned recovery spool or external artifact store is a separate future hardening
step. Do not claim a locally saved result is remotely durable. No public Actions
artifact is used as a destination for private research evidence.
