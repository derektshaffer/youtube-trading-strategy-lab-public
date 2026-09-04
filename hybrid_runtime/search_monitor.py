"""Read-only search inventory and exact, pre-start cancellation. Never dispatches."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import threading
import time
from typing import Any

from .contracts import JobStatus, TERMINAL_JOB_STATUSES, canonical_json, utc_now_text
from .security import redact_text
from cloud_search_control import supports_stop, verify_worker_run

SEARCH_TYPES = {"stock_finder": "Stock Finder", "strategy_lab": "Strategy Lab"}
LOCAL_TYPES = {"strategy.stock_finder": "Stock Finder", "strategy.strategy_lab": "Strategy Lab",
               "market.discovery": "Find Stocks"}
QUEUED = {"queued", "pending", "retry", "retry_wait"}
TERMINAL = {"complete", "completed", "success", "succeeded", "done", "failed", "error", "cancelled", "canceled", "dead", "abandoned"}


def binding(settings):
    return {key: getattr(settings.github, key) for key in ("repository", "branch", "path")}


def identity(item):
    # Status/progress may advance; immutable request identity must still match.
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    request = {key: value for key, value in payload.items()
               if not key.startswith("distributed_") and key not in {"research_start", "research_end"}}
    value = {key: item.get(key) for key in ("id", "type", "created_at", "cloud_worker")}
    value["payload"] = request
    return sha256(canonical_json(value).encode()).hexdigest()


def fraction(value):
    try:
        value = float(value)
        return value if 0 <= value <= 1 else 0.0
    except (TypeError, ValueError):
        return 0.0


def remote_snapshot(library, settings, revision, checkpoints=None):
    from .strategy_lab_bridge import strategy_lab_checkpoint_record
    queue = library.get("research_queue", [])
    if not isinstance(queue, list):
        raise ValueError("Cloud research queue is malformed; current searches could not be verified.")
    rows = []
    seen = set()
    for item in queue:
        if not isinstance(item, dict) or item.get("type") not in SEARCH_TYPES or not item.get("id"):
            continue
        if str(item["id"]) in seen:
            raise ValueError("Cloud search identifiers are duplicated; refresh cannot verify exact searches.")
        seen.add(str(item["id"]))
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        status = str(item.get("status") or "unknown").lower()
        checkpoint = strategy_lab_checkpoint_record(checkpoints, str(payload.get("run_id") or "")) if (
            item["type"] == "strategy_lab" and payload.get("run_id")) else {}
        # Queue status stays authoritative: an old running checkpoint must not
        # turn a requeued/failed/cancelled job into a supposedly running search.
        stage = str(item.get("stage") or payload.get("distributed_stage") or status)
        progress = fraction(payload.get("distributed_progress", item.get("progress", 0)))
        checkpoint_time = str(checkpoint.get("saved_at") or "")
        if checkpoint and status not in TERMINAL:
            progress = fraction(checkpoint.get("progress"))
            if status == "running":
                stage = str(checkpoint.get("stage") or stage)
        message = str(item.get("status_message") or payload.get("distributed_message") or item.get("last_error") or "")
        if status == "running" and checkpoint:
            message = str(checkpoint.get("message") or message)
        stoppable = supports_stop(item, settings.github.action_repository)
        can_cancel = (status in QUEUED and not item.get("cancel_requested")) or (
            status in {"running", "cancelling"} and stoppable)
        rows.append({
            "key": "cloud:" + str(item["id"]), "id": str(item["id"]), "binding": binding(settings),
            "identity": identity(item), "kind": SEARCH_TYPES[item["type"]], "target": "Cloud",
            "symbol": str(payload.get("symbol") or payload.get("ticker") or "—"),
            "profile": str(payload.get("profile") or payload.get("search_depth") or ""),
            "timeframe": str(payload.get("timeframe") or ""), "run_id": str(payload.get("run_id") or ""),
            "status": status, "stage": stage, "progress": progress,
            "updated_at": str(item.get("updated_at") or item.get("created_at") or ""),
            "checkpoint_at": checkpoint_time, "message": redact_text(message)[:1000],
            "cloud_worker": deepcopy(item.get("cloud_worker") or {}),
            "active": status not in TERMINAL, "can_cancel": can_cancel,
            "cancel_reason": ("Requests a stop for this exact cloud worker. Saved checkpoints are retained; stopping is not confirmed until the worker exits."
                              if stoppable and status in {"running", "cancelling"} else
                              ("Cancels the pending retry; it does not prove that a previous worker has stopped."
                               if progress > 0 or "requeued" in stage else
                               "Cancels this exact queued cloud request before it starts.") if can_cancel else
                              "This worker cannot be interrupted mid-run from the desktop. No stop has been confirmed."
                              if status == "running" else "This search is finished or cancellation is already pending."),
        })
    return {"rows": rows, "binding": binding(settings), "revision": revision,
            "checked_at": utc_now_text(), "monotonic": time.monotonic(), "warning": ""}


class SearchMonitor:
    def __init__(self, worker, *, cache_seconds=60.0):
        self.worker = worker
        self.cache_seconds = cache_seconds
        self._cache = None
        self._lock = threading.Lock()

    def _read(self, settings):
        worker = self.worker
        token = worker.token_loader(settings)
        client = worker.client_factory(settings.github, token)
        remote = client.read()
        checkpoints = {}
        warning = ""
        if any(item.get("type") == "strategy_lab" and item.get("status") not in TERMINAL
               for item in remote.data.get("research_queue") or [] if isinstance(item, dict)):
            from .strategy_lab_bridge import strategy_lab_checkpoint_config
            try:
                checkpoints = worker.client_factory(strategy_lab_checkpoint_config(settings.github), token).read().data
            except Exception as exc:
                warning = "Checkpoint progress unavailable: " + redact_text(exc)
        result = remote_snapshot(remote.data, settings, remote.revision, checkpoints)
        # Confirm stop read-only. Durable 'cancelling' intent prevents scheduler
        # restarts even when the desktop closes before Actions acknowledges it.
        for row in result["rows"]:
            if row["status"] != "cancelling":
                continue
            try:
                run = verify_worker_run(row["cloud_worker"], client.workflow_run(row["cloud_worker"]["run_id"]))
                if run.get("status") == "completed":
                    cancelled = run.get("conclusion") == "cancelled"
                    row.update(status="cancelled" if cancelled else "worker_stopped", stage="worker_stopped",
                               active=False, can_cancel=False,
                               cancel_reason="Cloud worker exit confirmed. Saved checkpoints and results are retained.",
                               message="Cloud worker stopped; outcome: " + str(run.get("conclusion") or "unknown"))
            except Exception as exc:
                row.update(can_cancel=False, cancel_reason="Worker stop is not confirmed: " + redact_text(exc))
        result["warning"] = warning
        return result

    def snapshot(self, *, force=False):
        with self._lock:
            settings = self.worker.settings_loader(self.worker.data_dir)
            current_binding = binding(settings) if settings else None
            stale = False
            error = ""
            if self._cache and self._cache["binding"] != current_binding:
                self._cache = None
            observed = getattr(self.worker, "search_snapshot_cache", None)
            if observed and observed["binding"] == current_binding and (
                self._cache is None or observed["monotonic"] > self._cache["monotonic"]
            ):
                self._cache = observed
            try:
                if settings is None:
                    raise ValueError("Cloud connection is not configured; only this desktop's saved jobs are shown.")
                if force or self._cache is None or time.monotonic() - self._cache["monotonic"] >= self.cache_seconds:
                    self._cache = self._read(settings)
            except Exception as exc:
                stale, error = True, redact_text(exc)
            cached = self._cache or {"rows": [], "checked_at": "", "warning": ""}
            rows = deepcopy(cached["rows"])
            remote_keys = {row["key"] for row in rows}
            if stale:
                for row in rows:
                    row.update(can_cancel=False, cancel_reason="Cloud status is stale. Refresh successfully before cancelling.")
            # Include active local requests even when cloud access is down.
            active = [status for status in JobStatus if status not in TERMINAL_JOB_STATUSES]
            jobs = {job.id: job for job in self.worker.service.store.list_jobs(statuses=active, limit=1000)}
            jobs.update({job.id: job for job in self.worker.service.list(limit=50)})
            for job in jobs.values():
                if job.job_type not in LOCAL_TYPES:
                    continue
                link = self.worker.link_store.get(job.id) or {}
                same_cloud = all(link.get(key) == (current_binding or {}).get(key) for key in ("repository", "branch", "path"))
                if same_cloud and "cloud:" + str(link.get("remote_job_id") or "") in remote_keys:
                    continue
                unpublished = not link.get("remote_job_id") and job.status in {JobStatus.QUEUED, JobStatus.RETRY_WAIT}
                can_cancel = not job.terminal and (job.execution_target.value == "local" or unpublished)
                rows.append({
                    "key": "local:" + job.id, "id": job.id, "identity": job.request_fingerprint,
                    "kind": LOCAL_TYPES[job.job_type], "target": "This Mac" if job.execution_target.value == "local" else "Desktop cloud request",
                    "symbol": str(job.payload.get("symbol") or job.payload.get("ticker") or "Market"),
                    "profile": str(job.payload.get("profile") or job.payload.get("search_depth") or ""),
                    "status": job.status.value, "stage": job.stage, "progress": job.progress,
                    "updated_at": job.updated_at, "message": redact_text((job.error or {}).get("message") or link.get("dispatch_error") or ""),
                    "active": not job.terminal, "can_cancel": can_cancel,
                    "cancel_reason": "Request cancellation for this saved job." if can_cancel else "Refresh cloud status to manage the remote run; local state alone cannot confirm it stopped.",
                })
            rows.sort(key=lambda row: (row["active"], row["updated_at"]), reverse=True)
            active_rows = [row for row in rows if row["active"]]
            recent = [row for row in rows if not row["active"]][:20]
            return {"rows": active_rows + recent, "active_count": len(active_rows), "stale": stale,
                    "checked_at": cached["checked_at"], "warning": error or cached.get("warning", "")}

    def cancel(self, request: dict[str, Any]):
        worker = self.worker
        if not worker._reconciliation_lock.acquire(blocking=False):
            raise ValueError("Cloud sync is busy. No cancellation was sent; try again shortly.")
        try:
            key = str(request.get("key") or "")
            job_id = key.partition(":")[2]
            if key.startswith("local:"):
                job = worker.service.get(job_id)
                link = worker.link_store.get(job_id) or {}
                if job.job_type not in LOCAL_TYPES or request.get("identity") != job.request_fingerprint or job.terminal:
                    raise ValueError("The selected job changed or is already finished. Refresh first.")
                if job.execution_target.value == "cloud" and (link.get("remote_job_id") or job.status not in {JobStatus.QUEUED, JobStatus.RETRY_WAIT}):
                    raise ValueError("The cloud request may have started; refresh and select the exact remote run.")
                if job.execution_target.value == "cloud" and link:
                    # A failed push response does not prove publication failed.
                    # Do not cancel just the local attachment and leave a live
                    # cloud search orphaned after an ambiguous submission.
                    settings = worker.settings_loader(worker.data_dir)
                    if settings is None or any(link.get(key) != binding(settings)[key] for key in ("repository", "branch", "path")):
                        raise ValueError("Verify the original cloud connection before cancelling this submission.")
                    remote = worker.client_factory(settings.github, worker.token_loader(settings)).read()
                    for item in remote.data.get("research_queue") or []:
                        if not isinstance(item, dict):
                            continue
                        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
                        marker = payload.get("hybrid_cloud_bridge") if isinstance(payload.get("hybrid_cloud_bridge"), dict) else {}
                        if marker.get("local_job_id") == job.id and str(item.get("status") or "") not in TERMINAL:
                            raise ValueError("This submission exists in the cloud. Refresh and cancel its exact cloud row instead.")
                result = worker.service.cancel(job_id)
                return {"status": result.status.value, "message": "Cancellation confirmed." if result.status == JobStatus.CANCELLED else "Cancellation requested; waiting for the local worker to stop."}
            if not key.startswith("cloud:"):
                raise ValueError("Select an exact saved search.")
            settings = worker.settings_loader(worker.data_dir)
            if settings is None or request.get("binding") != binding(settings):
                raise ValueError("Cloud connection changed. Refresh before cancelling.")
            client = worker.client_factory(settings.github, worker.token_loader(settings))
            remote = client.read()
            matches = [item for item in remote.data.get("research_queue") or [] if isinstance(item, dict) and str(item.get("id")) == job_id]
            if len(matches) != 1 or matches[0].get("type") not in SEARCH_TYPES or identity(matches[0]) != request.get("identity"):
                raise ValueError("The exact selected search changed or disappeared. Refresh before cancelling.")
            item = matches[0]
            status = str(item.get("status") or "").lower()
            if status in {"running", "cancelling"}:
                if not supports_stop(item, settings.github.action_repository):
                    raise ValueError("This older worker has no verified stop link. No cancellation was sent.")
                bound = item["cloud_worker"]
                run = verify_worker_run(bound, client.workflow_run(bound["run_id"]))
                if run.get("status") == "completed":
                    if status != "cancelling":
                        raise ValueError("This worker has already exited. Refresh to inspect its result.")
                    if run.get("conclusion") != "cancelled":
                        raise ValueError("The worker exited before cancellation was confirmed. Refresh to inspect its outcome.")
                    now = utc_now_text()
                    item.update(status="cancelled", stage="cancelled", updated_at=now, completed_at=now)
                    client.write(remote.data, expected_revision=remote.revision, message="Confirm exact cloud search cancellation")
                    self._cache = worker.search_snapshot_cache = None
                    return {"status": "cancelled", "message": "Cloud worker cancellation confirmed. Saved checkpoints are retained."}
                if status != "cancelling":
                    item.update(status="cancelling", stage="cancelling", cancel_requested=True,
                                updated_at=utc_now_text(), status_message="User requested cloud worker stop; awaiting confirmation.")
                    # Persist intent first so stale leases cannot restart this work.
                    client.write(remote.data, expected_revision=remote.revision, message="Request exact cloud search cancellation")
                self._cache = worker.search_snapshot_cache = None
                # Recheck after the potentially long CAS upload. Never infer a
                # worker from a name, ticker, timing, or list position.
                run = verify_worker_run(bound, client.workflow_run(bound["run_id"]))
                if run.get("status") != "completed":
                    client.cancel_workflow_run(bound["run_id"])
                return {"status": "cancelling", "message": "Stop requested; waiting for the exact cloud worker to exit. Saved checkpoints are retained."}
            if str(item.get("status") or "").lower() not in QUEUED:
                raise ValueError("This search has started or finished. Its worker cannot be interrupted from this desktop version.")
            now = utc_now_text()
            item.update(status="cancelled", stage="cancelled", cancel_requested=True, updated_at=now,
                        completed_at=now, status_message="Cancelled by the user from the desktop search monitor.")
            revision = client.write(remote.data, expected_revision=remote.revision, message="Cancel exact queued desktop research search")
            # Never auto-retry an uncertain write. Only a successful CAS confirms cancellation.
            worker.search_snapshot_cache = remote_snapshot(remote.data, settings, revision)
            self._cache = worker.search_snapshot_cache
            return {"status": "cancelled", "message": "Queued cloud search cancellation confirmed."}
        finally:
            worker._reconciliation_lock.release()
