"""Read-only remote verification for explicit local Finder reconnection."""
from __future__ import annotations

from typing import Any, Mapping

from .contracts import ExecutionTarget, JobStatus
from .stock_finder_bridge import finder_report_for_remote, normalized_finder_request
from .storage import HybridStoreError


def exact_recovery_item(library, binding, settings, job) -> dict[str, Any]:
    for key in ("repository", "branch", "path"):
        if binding.get(key) != getattr(settings.github, key):
            raise HybridStoreError("Cloud connection changed; restore the original repository, branch and path")
    queue = library.get("research_queue", [])
    if not isinstance(queue, list):
        raise HybridStoreError("The cloud research queue is malformed")
    matches = [item for item in queue
               if isinstance(item, Mapping) and item.get("id") == binding.get("remote_job_id")]
    if len(matches) != 1 or not binding.get("remote_job_id"):
        raise HybridStoreError("The exact linked cloud job is missing or duplicated; no research was dispatched")
    item = dict(matches[0])
    payload = item.get("payload") or {}
    if not isinstance(payload, Mapping):
        raise HybridStoreError("The linked cloud request is malformed")
    if item.get("type") != "stock_finder" or normalized_finder_request(payload) != normalized_finder_request(job.payload):
        raise HybridStoreError("The linked cloud job does not match this Finder request")
    marker = payload.get("hybrid_cloud_bridge") or {}
    if not isinstance(marker, Mapping):
        raise HybridStoreError("The cloud ownership marker is malformed")
    if marker.get("local_job_id") and marker["local_job_id"] != job.id:
        raise HybridStoreError("The cloud job belongs to another desktop entry")
    return item


def reconnect_finder(worker, job_id: str):
    job = worker.service.get(job_id)
    if (job.status != JobStatus.FAILED or job.execution_target != ExecutionTarget.CLOUD
            or job.job_type != "strategy.stock_finder" or job.cancel_requested or job.result):
        raise HybridStoreError("Select a failed cloud Stock Finder entry without a completed result")
    # Once recovered, the audited identity is immutable even if a settings
    # change or a lost shortcut database later alters cloud-link metadata.
    binding = worker.service.store.cloud_recovery(job_id) or worker.link_store.get(job_id)
    if not binding or not binding.get("remote_job_id"):
        raise HybridStoreError("No exact saved cloud link exists; automatic matching is not safe")
    settings = worker.settings_loader(worker.data_dir)
    if settings is None:
        raise HybridStoreError("The original cloud connection is not configured")
    # Check identity before sending credentials or reading a different repository.
    for key in ("repository", "branch", "path"):
        if binding.get(key) != getattr(settings.github, key):
            raise HybridStoreError("Cloud connection changed; restore the original repository, branch and path")
    client = worker.client_factory(settings.github, worker.token_loader(settings))
    remote = client.read()
    item = exact_recovery_item(remote.data, binding, settings, job)
    status = str(item.get("status") or "").strip().lower()
    if status not in {"queued", "retry", "running", "complete"} or item.get("cancel_requested"):
        raise HybridStoreError("The linked cloud job has not recovered or was cancelled")
    if status == "complete" and not finder_report_for_remote(remote.data, item, job):
        raise HybridStoreError("Cloud completion has no matching saved report; the failed entry was preserved")
    # Do not reopen if the saved link changed while the network read was pending.
    latest = worker.service.store.cloud_recovery(job_id) or worker.link_store.get(job_id) or {}
    if any(latest.get(key) != binding.get(key) for key in ("remote_job_id", "repository", "branch", "path")):
        raise HybridStoreError("Cloud link changed during verification; refresh and try again")
    audit_binding = {key: binding[key] for key in ("remote_job_id", "repository", "branch", "path")}
    audit_binding["revision"] = remote.revision
    reopened = worker.service.store.reconnect_failed_finder(
        job_id, expected_updated_at=job.updated_at, binding=audit_binding, worker_id=worker.worker_id,
    )
    worker._advance_from_remote(reopened, item, remote.data, settings, remote.revision)
    return worker.service.get(job_id)
