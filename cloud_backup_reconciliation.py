"""Conservative three-way reconciliation. No timestamps choose record winners."""
from copy import deepcopy
import json

from youtube_strategy_engine import AppError


class ReconciliationConflict(AppError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def conflict(path, reason):
    # Paths/identities only: never log private result payloads.
    raise ReconciliationConflict(f"Cloud reconciliation conflict at {path}: {reason}")


IMMUTABLE = {
    "research_runs", "validation_runs", "external_research_runs",
    "research_worker_runs", "predictive_ml_runs", "strategy_versions",
}
COLLECTIONS = IMMUTABLE | {
    "strategies", "videos", "paper_positions", "recovery_items", "knowledge_sources",
    "research_queue", "research_hypotheses", "experiment_registry",
}
WORKER_PROJECTION = {"last_worker_at", "last_worker_id", "last_worker_status"}
TERMINAL = {"complete", "completed", "failed", "cancelled", "canceled"}


def index(rows, path):
    if not isinstance(rows, list):
        conflict(path, "expected a record collection")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            conflict(path, "non-object record")
        key = row.get("url") if path == "videos" else row.get("id")
        if not isinstance(key, str) or not key:
            conflict(path, "missing stable record identity")
        if key in result:
            conflict(f"{path}/{key}", "duplicate record identity")
        canonical(row)
        result[key] = row
    return result


def validate_queue_change(base, local, path):
    """Only existing claim transitions may be rebased; competing claims conflict."""
    for key in ("id", "type", "created_at", "payload", "dedupe_key", "max_attempts",
                "retry_of", "retry_of_job_id", "parent_job_id", "root_job_id"):
        if base.get(key) != local.get(key):
            conflict(path, f"immutable job identity/ancestry changed: {key}")
    old, new = base.get("status"), local.get("status")
    a, b = base.get("attempts", 0), local.get("attempts", 0)
    if not isinstance(a, int) or not isinstance(b, int) or a < 0 or b < 0:
        conflict(path, "invalid attempts")
    allowed = {
        "queued": {"running", "cancelled", "canceled"},
        "retry": {"running", "cancelled", "canceled"},
        "running": {"running", "complete", "retry", "failed", "cancelling", "cancelled", "canceled"},
        "cancelling": {"cancelled", "canceled"},
    }
    if old in TERMINAL or new not in allowed.get(old, set()):
        conflict(path, f"unsupported state transition {old} -> {new}")
    claiming = old in {"queued", "retry"} and new == "running"
    if b != a + int(claiming) or b > local.get("max_attempts", b):
        conflict(path, "invalid attempt/retry ancestry")
    if claiming:
        if not local.get("worker_id") or not local.get("started_at"):
            conflict(path, "claim has no owner/start identity")
    else:
        for key in ("started_at", "cloud_worker", "claim_id", "attempt_id", "cloud_persistence_protocol"):
            if base.get(key) != local.get(key):
                conflict(path, f"stale or foreign claim identity: {key}")
        if local.get("worker_id") not in (base.get("worker_id"), None):
            conflict(path, "foreign worker ownership")
    for key in ("updated_at", "completed_at"):
        if base.get(key) and local.get(key) and local[key] < base[key]:
            conflict(path, f"regressing {key}")
    if new == "complete" and (not local.get("result_ref") or not local.get("completed_at")):
        conflict(path, "completion lacks result identity/timestamp")


def merge_records(base, local, remote, path):
    bi, li, ri = (index(rows, path) for rows in (base, local, remote))
    # Retention pruning by a worker must never delete historical cloud records.
    out = deepcopy(ri)
    for key, row in li.items():
        old, latest = bi.get(key), ri.get(key)
        where = f"{path}/{key}"
        if old == row:
            continue  # including a newer terminal state/owner in remote
        if old is None:
            if latest is not None and latest != row:
                conflict(where, "same ID has different content")
            out[key] = deepcopy(row)
        else:
            if path in IMMUTABLE:
                conflict(where, "immutable result was rewritten")
            if latest is None:
                conflict(where, "remote deleted a locally modified record")
            if latest == row:
                continue
            if latest != old:
                conflict(where, "both writers changed this record")
            if path == "research_queue":
                validate_queue_change(old, row, where)
            out[key] = deepcopy(row)
    # Newest local additions first; retain remote order and every remote record.
    order = [k for k in li if k not in ri] + list(ri)
    return [out[k] for k in order]


def reconcile(base, local, remote):
    for value in (base, local, remote):
        if not isinstance(value, dict) or not isinstance(value.get("strategies"), list):
            conflict("library", "invalid library")
        canonical(value)
    merged = deepcopy(remote)
    missing = object()
    for name in sorted(set(base) | set(local) | set(remote)):
        b, l, r = (v.get(name, missing) for v in (base, local, remote))
        if name == "updated_at":
            continue
        if name in COLLECTIONS:
            merged[name] = merge_records(
                [] if b is missing else b, [] if l is missing else l,
                [] if r is missing else r, name,
            )
        elif name == "research_system":
            if not all(v is missing or isinstance(v, dict) for v in (b, l, r)):
                conflict(name, "invalid settings object")
            merged[name] = merge_settings({} if b is missing else b,
                                          {} if l is missing else l,
                                          {} if r is missing else r)
        elif l == b or l == r:
            continue
        elif r == b and l is not missing:
            merged[name] = deepcopy(l)
        else:
            conflict(name, "concurrent or unsupported field change/deletion")
    # last_worker_* is a derived display projection, not an independent job state.
    # Rebuild all three fields from the same immutable receipt, keeping both writers.
    settings = merged.get("research_system", {})
    changed_projection = any(local.get("research_system", {}).get(k) != base.get("research_system", {}).get(k)
                             for k in WORKER_PROJECTION)
    if changed_projection:
        receipts = merged.get("research_worker_runs", [])
        if not receipts or any(not r.get("generated_at") or not r.get("worker_id") or not r.get("status") for r in receipts):
            conflict("research_system/last_worker", "missing receipt for worker projection")
        newest = max(receipts, key=lambda r: (r["generated_at"], r["id"]))
        settings.update(last_worker_at=newest["generated_at"], last_worker_id=newest["worker_id"],
                        last_worker_status=newest["status"])
    validate_result_links(base, local, merged)
    return merged


def merge_settings(base, local, remote):
    # Each setting/projection is atomic; never combine parts of different ML models.
    missing = object()
    out = deepcopy(remote)
    for key in set(base) | set(local):
        if key in WORKER_PROJECTION:
            continue
        b, l, r = (v.get(key, missing) for v in (base, local, remote))
        if l == b or l == r:
            continue
        if r != b or l is missing:
            conflict(f"research_system/{key}", "concurrent setting/projection change")
        out[key] = deepcopy(l)
    return out


def claim_identity(job):
    return {key: job.get(key) for key in
            ("id", "attempts", "started_at", "worker_id", "cloud_worker")}


def validate_result_links(base, local, merged):
    before = index(base.get("research_queue", []), "research_queue")
    records = {row["id"]: row for name in IMMUTABLE
               for row in merged.get(name, []) if isinstance(row, dict) and row.get("id")}
    for job in local.get("research_queue", []):
        if job == before.get(job.get("id")) or job.get("status") != "complete":
            continue
        ref = str(job.get("result_ref") or "")
        # ML has an explicit namespaced reference. Other job types retain their
        # existing validator contracts; their result references are not interchangeable.
        if job.get("type") == "predictive_ml_backfill":
            if not ref.startswith("predictive-ml:"):
                conflict(f"research_queue/{job['id']}", "foreign result namespace")
            result = records.get(ref.removeprefix("predictive-ml:"))
            if not result or result not in merged.get("predictive_ml_runs", []):
                conflict(f"research_queue/{job['id']}", "missing ML result")
            claimed = before.get(job["id"])
            if not claimed or result.get("origin_claim") != claim_identity(claimed):
                conflict(f"research_queue/{job['id']}", "foreign ML attempt association")
            if result.get("origin_job_id") != job["id"]:
                conflict(f"research_queue/{job['id']}", "foreign ML result association")
