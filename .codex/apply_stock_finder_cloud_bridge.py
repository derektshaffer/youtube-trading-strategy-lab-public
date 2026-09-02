from __future__ import annotations

import ast
from pathlib import Path


PATH = Path("hybrid_runtime/cloud_bridge.py")
source = PATH.read_text(encoding="utf-8")


def once(old: str, new: str, label: str) -> None:
    global source
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    source = source.replace(old, new, 1)


once(
    'SUPPORTED_CLOUD_JOB_TYPES = frozenset({"strategy.profit_first_validation"})',
    'SUPPORTED_CLOUD_JOB_TYPES = frozenset({"strategy.profit_first_validation", "strategy.stock_finder"})',
    "supported job types",
)
once(
    '    def dispatch_workflow(self, inputs: Mapping[str, Any] | None = None) -> bool: ...',
    '''    def dispatch_workflow(\n        self,\n        inputs: Mapping[str, Any] | None = None,\n        *,\n        workflow_file: str | None = None,\n    ) -> bool: ...''',
    "protocol dispatch",
)
once(
'''def _remote_progress(item: Mapping[str, Any]) -> float:\n    raw = item.get("progress")\n''',
'''def _remote_progress(item: Mapping[str, Any]) -> float:\n    payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}\n    raw = payload.get("distributed_progress")\n    if raw is None:\n        raw = item.get("progress")\n''',
    "distributed progress",
)
once(
'''def _remote_stage(item: Mapping[str, Any]) -> str:\n    for name in ("stage", "progress_stage", "current_stage"):\n''',
'''def _remote_stage(item: Mapping[str, Any]) -> str:\n    payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}\n    distributed = str(payload.get("distributed_stage") or "").strip().lower()\n    if distributed:\n        return distributed\n    for name in ("stage", "progress_stage", "current_stage"):\n''',
    "distributed stage",
)
once(
'''def _remote_status(item: Mapping[str, Any]) -> str:\n    return str(item.get("status") or "queued").strip().lower().replace("-", "_")\n\n\ndef _strategy_ids''',
'''def _remote_status(item: Mapping[str, Any]) -> str:\n    return str(item.get("status") or "queued").strip().lower().replace("-", "_")\n\n\ndef _remote_message(item: Mapping[str, Any]) -> str:\n    payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}\n    distributed = " ".join(str(payload.get("distributed_message") or "").split())[:500]\n    if distributed:\n        return distributed\n    return ""\n\n\ndef _strategy_ids''',
    "remote message",
)
once(
'''    "stress": JobStatus.VALIDATING,\n    "saving": JobStatus.SAVING,\n}''',
'''    "stress": JobStatus.VALIDATING,\n    "distributed_optimization": JobStatus.OPTIMIZING,\n    "final_holdout": JobStatus.VALIDATING,\n    "final_validation": JobStatus.VALIDATING,\n    "parameter_stability": JobStatus.VALIDATING,\n    "historical_spread_audit": JobStatus.VALIDATING,\n    "saving_completed_report": JobStatus.SAVING,\n    "finalization_retry": JobStatus.VALIDATING,\n    "saving": JobStatus.SAVING,\n}''',
    "finder stage mapping",
)
once(
'''    ) -> tuple[dict[str, Any] | None, bool, dict[str, Any] | None]:\n        from profit_first_queue import profit_first_validation_batch\n''',
'''    ) -> tuple[dict[str, Any] | None, bool, dict[str, Any] | None]:\n        if job.job_type == "strategy.stock_finder":\n            from .stock_finder_bridge import prepare_stock_finder_publication\n\n            return prepare_stock_finder_publication(library, job)\n\n        from profit_first_queue import profit_first_validation_batch\n''',
    "finder publication dispatch",
)
once(
'''        return self.link_store.upsert(\n            local_job_id=job.id,\n''',
'''        metadata: dict[str, Any]\n        if job.job_type == "strategy.stock_finder":\n            from .stock_finder_bridge import finder_link_metadata\n\n            metadata = finder_link_metadata(item, job)\n        else:\n            metadata = {\n                "strategy_ids": _strategy_ids(item, job),\n                "job_type": job.job_type,\n            }\n        return self.link_store.upsert(\n            local_job_id=job.id,\n''',
    "link metadata setup",
)
once(
'''            metadata={\n                "strategy_ids": _strategy_ids(item, job),\n                "job_type": job.job_type,\n            },\n''',
'''            metadata=metadata,\n''',
    "link metadata use",
)
once(
'''            error_value = item.get("error") or item.get("last_error") or "Cloud validation failed"\n            if isinstance(error_value, Mapping):\n                error = dict(error_value)\n                error.setdefault("type", "CloudValidationError")\n                error.setdefault("message", "Cloud validation failed")\n            else:\n                error = {\n                    "type": "CloudValidationError",\n                    "message": redact_text(error_value),\n                }\n''',
'''            default_message = (\n                "Cloud Stock Finder failed"\n                if job.job_type == "strategy.stock_finder"\n                else "Cloud validation failed"\n            )\n            default_type = (\n                "CloudStockFinderError"\n                if job.job_type == "strategy.stock_finder"\n                else "CloudValidationError"\n            )\n            error_value = item.get("error") or item.get("last_error") or default_message\n            if isinstance(error_value, Mapping):\n                error = dict(error_value)\n                error.setdefault("type", default_type)\n                error.setdefault("message", default_message)\n            else:\n                error = {\n                    "type": default_type,\n                    "message": redact_text(error_value),\n                }\n''',
    "generic cloud failure",
)
once(
'''                message="Cloud validation failed",\n            )\n            return\n\n        if status in {"complete", "completed", "success", "succeeded", "done"}:\n            current = self._ensure_claimed(job.id, "Cloud worker accepted the job")\n            strategy_ids = _strategy_ids(item, job)\n''',
'''                message=default_message,\n            )\n            return\n\n        if status in {"complete", "completed", "success", "succeeded", "done"}:\n            current = self._ensure_claimed(job.id, "Cloud worker accepted the job")\n            if job.job_type == "strategy.stock_finder":\n                from .stock_finder_bridge import finder_report_for_remote\n\n                report = finder_report_for_remote(library, item, job)\n                if not report:\n                    self.service.store.transition_job(\n                        job.id,\n                        JobStatus.FAILED,\n                        stage="failed",\n                        progress=max(current.progress, progress),\n                        error={\n                            "type": "CloudStockFinderResultMissing",\n                            "message": (\n                                "The cloud Finder job completed but its exact durable report "\n                                "could not be reconciled from the research library."\n                            ),\n                        },\n                        worker_id=self.worker_id,\n                        message="Cloud Stock Finder result was missing",\n                    )\n                    return\n                payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}\n                self.service.complete(\n                    job.id,\n                    {\n                        "outcome": "stock_finder_complete",\n                        "remote_job_id": str(item.get("id") or ""),\n                        "remote_dedupe_key": str(item.get("dedupe_key") or ""),\n                        "result_ref": str(item.get("result_ref") or ""),\n                        "symbol": str(payload.get("symbol") or job.payload.get("symbol") or "").upper(),\n                        "profile": str(payload.get("profile") or job.payload.get("profile") or ""),\n                        "finder_report": report,\n                        "research_library_revision": revision,\n                    },\n                    worker_id=self.worker_id,\n                )\n                return\n            strategy_ids = _strategy_ids(item, job)\n''',
    "finder completion",
)
once(
'''        message = (\n            "Waiting in the cloud research queue"\n            if desired == JobStatus.CLAIMED\n            else f"Cloud research: {stage.replace('_', ' ')}"\n        )\n''',
'''        message = _remote_message(item)\n        if not message:\n            message = (\n                "Waiting in the cloud research queue"\n                if desired == JobStatus.CLAIMED\n                else f"Cloud research: {stage.replace('_', ' ')}"\n            )\n''',
    "distributed message",
)
once(
'''            if link:\n                item = _find_remote_item(\n                    library,\n                    remote_job_id=str(link.get("remote_job_id") or ""),\n                    dedupe_key=str(link.get("remote_dedupe_key") or ""),\n                )\n''',
'''            if link:\n                if job.job_type == "strategy.stock_finder":\n                    from .stock_finder_bridge import find_finder_remote_item\n\n                    item = find_finder_remote_item(\n                        library,\n                        local_job_id=job.id,\n                        remote_job_id=str(link.get("remote_job_id") or ""),\n                        dedupe_key=str(link.get("remote_dedupe_key") or ""),\n                    )\n                else:\n                    item = _find_remote_item(\n                        library,\n                        remote_job_id=str(link.get("remote_job_id") or ""),\n                        dedupe_key=str(link.get("remote_dedupe_key") or ""),\n                    )\n''',
    "finder reconnect lookup",
)
once(
'''                    message="Queue Trading Intelligence desktop cloud validation",\n''',
'''                    message="Queue Trading Intelligence desktop cloud research",\n''',
    "generic queue commit message",
)
once(
'''        dispatch_error = ""\n        if published:\n            try:\n                client.dispatch_workflow(\n                    {\n                        "origin": "trading_intelligence_desktop",\n                    }\n                )\n            except GitHubLibraryError as exc:\n                # Queue publication is already durable. A scheduled worker can\n                # still claim it even when this token lacks Actions permission.\n                dispatch_error = redact_text(exc)\n\n        for job, item, _plan in published + attached:\n            self._link(\n                job,\n                item,\n                settings,\n                revision=revision,\n                dispatch_error=dispatch_error,\n            )\n''',
'''        dispatch_errors: dict[str, str] = {}\n        for job, item, _plan in published:\n            try:\n                if job.job_type == "strategy.stock_finder":\n                    from .stock_finder_bridge import DISTRIBUTED_STOCK_FINDER_WORKFLOW\n\n                    client.dispatch_workflow(\n                        {"job_id": str(item.get("id") or "")},\n                        workflow_file=DISTRIBUTED_STOCK_FINDER_WORKFLOW,\n                    )\n                else:\n                    client.dispatch_workflow(\n                        {"origin": "trading_intelligence_desktop"}\n                    )\n            except GitHubLibraryError as exc:\n                # Queue publication is already durable. Scheduled workers can still\n                # claim it even when this token lacks Actions permission.\n                dispatch_errors[job.id] = redact_text(exc)\n\n        for job, item, _plan in published + attached:\n            self._link(\n                job,\n                item,\n                settings,\n                revision=revision,\n                dispatch_error=dispatch_errors.get(job.id, ""),\n            )\n''',
    "job-specific dispatch",
)
once(
'''        for job, plan in terminal_plans:\n            queue_status = str(plan.get("queue_status") or "invalid-plan")\n            if queue_status == "no-eligible-candidates":\n''',
'''        for job, plan in terminal_plans:\n            queue_status = str(plan.get("queue_status") or "invalid-plan")\n            if job.job_type == "strategy.stock_finder":\n                current = self._ensure_claimed(job.id, "Cloud Finder request evaluated")\n                self.service.store.transition_job(\n                    job.id,\n                    JobStatus.FAILED,\n                    stage="failed",\n                    progress=current.progress,\n                    error={\n                        "type": "CloudStockFinderPlanError",\n                        "message": str(\n                            plan.get("bridge_error")\n                            or "Stock Finder cloud plan is not publishable."\n                        ),\n                    },\n                    worker_id=self.worker_id,\n                    message="Cloud Stock Finder plan failed",\n                )\n                continue\n            if queue_status == "no-eligible-candidates":\n''',
    "finder terminal plan",
)

ast.parse(source, filename=str(PATH))
PATH.write_text(source, encoding="utf-8")
print("stock finder cloud bridge patch applied")
