"""Identity-guarded reads of terminal validation evidence. No execution or writes."""
from copy import deepcopy
import json

from .contracts import JobStatus
from .security import redact_text
from .strategy_lab_result_projection import PROJECTION_VERSION, project_strategy_lab_result

TERMINAL = {"complete", "failed"}
ERROR_FIELDS = ("type", "kind", "category", "message", "terminal_reason",
                "last_execution_stage", "last_progress", "last_checkpoint_saved_at",
                "diagnostic_deadline_at", "diagnostic_max_attempts", "diagnostic_timeout_minutes")


def _request_identity(payload):
    ticker = str(payload.get("ticker") or "").upper()
    ids = payload.get("strategy_ids")
    run_id = str(payload.get("run_id") or "")
    if not ticker or not run_id or not isinstance(ids, list) or not ids or not all(isinstance(x, str) and x for x in ids):
        raise ValueError("Saved validation has no explicit ticker, strategy set or run identity.")
    return ticker, ids, run_id


def _response(*, job_id, cloud_job_id, payload, status, stage, progress, updated_at,
              result=None, error=None):
    ticker, ids, run_id = _request_identity(payload)
    if status not in TERMINAL:
        raise ValueError("Only existing terminal validations can be opened here.")
    response = dict(job_id=job_id, cloud_job_id=cloud_job_id, run_id=run_id,
                    ticker=ticker, strategy_ids=deepcopy(ids), status=status,
                    stage=stage, progress=progress, updated_at=updated_at,
                    result=None, error={}, research_only=True)
    if status == "failed":
        source = error if isinstance(error, dict) else {"message": str(error or "Execution failed.")}
        response["error"] = {k: redact_text(source[k]) if isinstance(source[k], str) else source[k]
                             for k in ERROR_FIELDS if k in source and
                             (source[k] is None or isinstance(source[k], (str, int, float, bool)))}
        response["outcome"] = "execution_failure"
        return response
    raw = result if isinstance(result, dict) else {}
    if not raw:
        raise ValueError("This exact validation has no saved completed evidence.")
    for key, expected in (("run_id", run_id), ("job_id", job_id), ("remote_job_id", cloud_job_id)):
        if raw.get(key) and raw[key] != expected:
            raise ValueError("Saved validation result identity mismatch: " + key)
    if "projection_version" in raw:
        if raw["projection_version"] != PROJECTION_VERSION:
            raise ValueError("Unsupported saved evidence projection; no evidence was substituted.")
        # These records were already projected by PR #115. Do not score them,
        # rebuild a winner, or merge evidence from another candidate.
        evidence = deepcopy(raw)
    else:
        evidence = project_strategy_lab_result(raw, run_id=run_id, saved_at=raw.get("saved_at", ""))
    if evidence.get("ticker") != ticker or evidence.get("winner_strategy_id") not in ids:
        raise ValueError("Saved validation ticker or strategy identity does not match its request.")
    encoded = json.dumps(evidence, allow_nan=False)
    if len(encoded.encode()) > 128_000:
        raise ValueError("Saved evidence is not a bounded canonical projection.")
    def check(value):
        if isinstance(value, dict):
            if set(value) & {"optimizer_state", "configuration_history", "result_archive", "report", "comparison", "trades", "optimized_rules", "optimized_backtest_settings"}:
                raise ValueError("Internal execution payload is not a compact evidence projection.")
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)
    check(evidence)
    response.update(outcome="validation_completed", result=evidence)
    return response


def _local(worker, job, cloud_job_id=""):
    if job.job_type != "strategy.strategy_lab":
        raise ValueError("This read-only view accepts validation jobs only.")
    return _response(job_id=job.id, cloud_job_id=cloud_job_id, payload=job.payload,
                     status=job.status.value, stage=job.stage, progress=job.progress,
                     updated_at=job.updated_at, result=job.result, error=job.error)


def read_saved_validation(worker, request):
    """Read exact local fingerprint or exact cloud binding/request identity."""
    from .search_monitor import binding, identity
    key = str(request.get("key") or "")
    prefix, _, selected_id = key.partition(":")
    if not selected_id or request.get("id") != selected_id:
        raise ValueError("Select an exact saved validation identity.")
    if prefix == "local":
        job = worker.service.get(selected_id)
        if request.get("identity") != job.request_fingerprint:
            raise ValueError("The selected validation request changed.")
        link = worker.link_store.get(job.id) or {}
        return _local(worker, job, str(link.get("remote_job_id") or ""))
    if prefix != "cloud":
        raise ValueError("Select an exact saved validation.")
    settings = worker.settings_loader(worker.data_dir)
    if settings is None or request.get("binding") != binding(settings):
        raise ValueError("Cloud connection changed; refresh the saved-run list.")
    token = worker.token_loader(settings)
    library = worker.client_factory(settings.github, token).read().data
    matches = [x for x in library.get("research_queue") or []
               if isinstance(x, dict) and x.get("id") == selected_id]
    if len(matches) != 1 or matches[0].get("type") != "strategy_lab" or identity(matches[0]) != request.get("identity"):
        raise ValueError("The exact cloud validation request changed or disappeared.")
    item = matches[0]
    if item.get("status") not in TERMINAL:
        raise ValueError("This validation is not terminal. No retry was requested.")
    # Reuse a linked, already-projected durable result when present. All binding,
    # request and terminal identities must agree; never use a latest-symbol fallback.
    linked = []
    for job in worker.service.list(limit=1000):
        link = worker.link_store.get(job.id) or {}
        if link.get("remote_job_id") == selected_id and all(link.get(k) == v for k, v in binding(settings).items()):
            linked.append(job)
    if len(linked) > 1:
        raise ValueError("Cloud validation has ambiguous local attachments.")
    if linked:
        job = linked[0]
        if _request_identity(job.payload) != _request_identity(item.get("payload") or {}) or job.status.value != item["status"]:
            raise ValueError("Local and cloud validation identities or terminal states disagree.")
        return _local(worker, job, selected_id)
    from .strategy_lab_bridge import strategy_lab_checkpoint_config
    from strategy_lab_persistence import restore_strategy_lab_result
    payload = item.get("payload") or {}
    ticker, _, run_id = _request_identity(payload)
    checkpoints = worker.client_factory(strategy_lab_checkpoint_config(settings.github), token).read().data
    records = [x for x in checkpoints.get("validation_runs") or []
               if isinstance(x, dict) and x.get("id") == run_id and x.get("record_type") == "strategy_lab_checkpoint"]
    if len(records) != 1 or records[0].get("ticker") != ticker or records[0].get("status") != item["status"]:
        raise ValueError("Exact terminal checkpoint unavailable or inconsistent.")
    cp = records[0]
    raw = restore_strategy_lab_result(cp) if item["status"] == "complete" else None
    result = project_strategy_lab_result(raw, run_id=run_id, saved_at=cp.get("saved_at", "")) if raw else None
    return _response(job_id=selected_id, cloud_job_id=selected_id, payload=payload,
                     status=item["status"], stage=cp.get("stage") or item["status"],
                     progress=cp.get("progress", 0), updated_at=item.get("updated_at", ""),
                     result=result, error=cp.get("error") or {"message": item.get("last_error") or cp.get("message") or "Execution failed."})
