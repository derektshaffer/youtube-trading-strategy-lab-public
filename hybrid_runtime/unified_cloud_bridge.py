"""Extend the durable desktop cloud bridge with distributed Stock Finder jobs."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Any, Mapping

from finder_report_persistence import latest_completed_finder_report

from .cloud_bridge import (
    DEFAULT_LIBRARY_PATH,
    CloudBridgeWorker,
    _find_remote_item,
    _queue,
    _remote_identifier,
    _remote_status,
)
from .contracts import ExecutionTarget, JobRecord, JobStatus, TERMINAL_JOB_STATUSES, utc_now_text
from .github_library import GitHubLibraryConflict, GitHubLibraryError
from .keychain import KeychainError
from .security import redact_text


SUPPORTED_DESKTOP_CLOUD_JOB_TYPES = frozenset(
    {
        "strategy.profit_first_validation",
        "strategy.stock_finder",
    }
)
DISTRIBUTED_FINDER_WORKFLOW = "distributed-stock-finder.yml"
FINDER_PROFILES = frozenset({"Current Regime", "Deep", "Very Deep"})


class UnifiedCloudBridgeWorker(CloudBridgeWorker):
    """Keep Profit First unchanged while adding the existing distributed Finder."""

    def _jobs(self) -> list[JobRecord]:
        return [
            job
            for job in self.service.list(limit=1_000)
            if job.execution_target == ExecutionTarget.CLOUD
            and job.status not in TERMINAL_JOB_STATUSES
            and job.job_type in SUPPORTED_DESKTOP_CLOUD_JOB_TYPES
        ]

    @staticmethod
    def _active_stock_finder(
        library: Mapping[str, Any],
        *,
        symbol: str,
        profile: str,
    ) -> dict[str, Any] | None:
        for item in library.get("research_queue") or []:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("type") or item.get("job_type") or "") != "stock_finder":
                continue
            if _remote_status(item) not in {"queued", "pending", "running", "retry", "retry_wait"}:
                continue
            payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
            if (
                str(payload.get("symbol") or "").strip().upper() == symbol
                and str(payload.get("profile") or "").strip() == profile
            ):
                return dict(item)
        return None

    def _stock_finder_publication_for(
        self,
        job: JobRecord,
        library: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, bool, dict[str, Any] | None]:
        symbol = str(job.payload.get("symbol") or "").strip().upper()
        profile = str(job.payload.get("profile") or "Deep").strip()
        if not symbol:
            return None, False, {
                "queue_status": "invalid-plan",
                "bridge_error": "Stock Finder cloud jobs require a symbol.",
            }
        if profile not in FINDER_PROFILES:
            return None, False, {
                "queue_status": "invalid-plan",
                "bridge_error": (
                    "Cloud Stock Finder accepts Current Regime, Deep, or Very Deep. "
                    "Quick Finder runs locally."
                ),
            }

        active = self._active_stock_finder(library, symbol=symbol, profile=profile)
        if active is not None:
            return active, False, {
                "queue_status": "active",
                "symbol": symbol,
                "profile": profile,
            }

        dedupe_key = str(job.payload.get("remote_dedupe_key") or "").strip()
        if not dedupe_key:
            # The local job id is durable across reconnects but changes for a new
            # user-initiated search. This prevents duplicate publication for one
            # local job without blocking a legitimate future re-run forever.
            dedupe_key = "desktop-stock-finder:" + sha256(job.id.encode("utf-8")).hexdigest()[:24]
        remote_id = _remote_identifier(dedupe_key)
        existing = _find_remote_item(
            library,
            remote_job_id=remote_id,
            dedupe_key=dedupe_key,
        )
        if existing is not None and _remote_status(existing) not in {
            "complete", "completed", "success", "succeeded", "done", "failed", "error", "cancelled", "canceled"
        }:
            return existing, False, {
                "queue_status": "active",
                "symbol": symbol,
                "profile": profile,
            }

        now = utc_now_text()
        item = {
            "id": remote_id,
            "request_id": remote_id,
            "type": "stock_finder",
            "job_type": "stock_finder",
            "status": "queued",
            "stage": "queued",
            "progress": 0.0,
            "priority": max(100, int(job.priority)),
            "attempt": 0,
            "attempts": 0,
            "max_attempts": 3,
            "dedupe_key": dedupe_key,
            "origin": "trading_intelligence_desktop",
            "source": "trading_intelligence_desktop",
            "created_at": now,
            "updated_at": now,
            "queued_at": now,
            "payload": {
                "symbol": symbol,
                "profile": profile,
                "origin": "trading_intelligence_desktop",
                "hybrid_cloud_bridge": {
                    "version": 2,
                    "local_job_id": job.id,
                    "request_fingerprint": job.request_fingerprint,
                },
            },
        }
        queue = _queue(library)
        queue.append(item)
        library["research_queue"] = queue
        return item, True, {
            "queue_status": "ready",
            "symbol": symbol,
            "profile": profile,
            "dedupe_key": dedupe_key,
        }

    def _publication_for(
        self,
        job: JobRecord,
        library: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, bool, dict[str, Any] | None]:
        if job.job_type == "strategy.stock_finder":
            return self._stock_finder_publication_for(job, library)
        return super()._publication_for(job, library)

    def _advance_from_remote(
        self,
        job: JobRecord,
        item: Mapping[str, Any],
        library: Mapping[str, Any],
        settings,
        revision: str,
    ) -> None:
        if job.job_type != "strategy.stock_finder":
            super()._advance_from_remote(job, item, library, settings, revision)
            return

        status = _remote_status(item)
        if status not in {"complete", "completed", "success", "succeeded", "done"}:
            # Cancellation, failure, queue and progress semantics are generic and
            # already hardened in the original bridge.
            super()._advance_from_remote(job, item, library, settings, revision)
            return

        current = self._ensure_claimed(job.id, "Cloud Stock Finder accepted the job")
        if current.status in TERMINAL_JOB_STATUSES:
            return
        payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
        symbol = str(payload.get("symbol") or job.payload.get("symbol") or "").strip().upper()
        profile = str(payload.get("profile") or job.payload.get("profile") or "Deep").strip()
        compact = latest_completed_finder_report(dict(library), symbol, profile)
        if not compact:
            self.service.store.transition_job(
                job.id,
                JobStatus.FAILED,
                stage="failed",
                progress=max(current.progress, 0.99),
                error={
                    "type": "CloudFinderResultMissing",
                    "message": (
                        "The distributed Finder queue item completed but its durable Finder report "
                        "was not found in the research library."
                    ),
                },
                worker_id=self.worker_id,
                message="Cloud Finder result could not be reconciled",
            )
            return
        result = dict(compact)
        result.update(
            {
                "outcome": "cloud_stock_finder_complete",
                "remote_job_id": str(item.get("id") or ""),
                "remote_dedupe_key": str(item.get("dedupe_key") or ""),
                "research_library_revision": revision,
                "execution_target": "cloud",
                "research_only": True,
                "affects_execution": False,
            }
        )
        self.service.complete(job.id, result, worker_id=self.worker_id)

    def run_once(self) -> bool:
        """Publish/reconcile both cloud job families and dispatch the right workflow."""

        jobs = self._jobs()
        if not jobs:
            return False
        settings = self.settings_loader(self.data_dir)
        if settings is None:
            self._record_waiting(
                jobs,
                "Configure a private GitHub research library before launching cloud research.",
                None,
            )
            return True
        try:
            token = self.token_loader(settings)
            client = self.client_factory(settings.github, token)
            remote = client.read()
        except (ValueError, KeychainError, GitHubLibraryError) as exc:
            self._record_waiting(jobs, redact_text(exc), settings)
            return True

        library = dict(remote.data)
        published: list[tuple[JobRecord, dict[str, Any], dict[str, Any] | None]] = []
        attached: list[tuple[JobRecord, dict[str, Any], dict[str, Any] | None]] = []
        terminal_plans: list[tuple[JobRecord, dict[str, Any]]] = []
        changed = False

        for job in jobs:
            link = self.link_store.get(job.id)
            item = None
            if link:
                item = _find_remote_item(
                    library,
                    remote_job_id=str(link.get("remote_job_id") or ""),
                    dedupe_key=str(link.get("remote_dedupe_key") or ""),
                )
            plan: dict[str, Any] | None = None
            if item is None:
                try:
                    item, created, plan = self._publication_for(job, library)
                except Exception as exc:
                    self.link_store.record_error(
                        job.id,
                        redact_text(exc),
                        repository=settings.github.repository,
                        branch=settings.github.branch,
                        path=settings.github.path,
                    )
                    continue
                if item is None:
                    terminal_plans.append((job, dict(plan or {})))
                    continue
                changed = changed or created
                (published if created else attached).append((job, item, plan))
            else:
                attached.append((job, item, plan))

            if self._cancel_remote_if_requested(job, item):
                queue = _queue(library)
                for index, candidate in enumerate(queue):
                    if str(candidate.get("id") or "") == str(item.get("id") or ""):
                        queue[index] = item
                        break
                library["research_queue"] = queue
                changed = True

        revision = remote.revision
        if changed:
            try:
                revision = client.write(
                    library,
                    expected_revision=remote.revision,
                    message="Queue Trading Intelligence desktop cloud research",
                )
            except GitHubLibraryConflict as exc:
                self._record_waiting(jobs, str(exc), settings)
                return True
            except GitHubLibraryError as exc:
                self._record_waiting(jobs, str(exc), settings)
                return True

        dispatch_errors: dict[str, str] = {}
        profit_first = [(job, item) for job, item, _plan in published if job.job_type != "strategy.stock_finder"]
        if profit_first:
            try:
                client.dispatch_workflow({"origin": "trading_intelligence_desktop"})
            except GitHubLibraryError as exc:
                message = redact_text(exc)
                for job, _item in profit_first:
                    dispatch_errors[job.id] = message

        finder_jobs = [(job, item) for job, item, _plan in published if job.job_type == "strategy.stock_finder"]
        if finder_jobs:
            finder_config = replace(settings.github, workflow_file=DISTRIBUTED_FINDER_WORKFLOW)
            try:
                finder_client = self.client_factory(finder_config, token)
                for job, item in finder_jobs:
                    try:
                        finder_client.dispatch_workflow({"job_id": str(item.get("id") or "")})
                    except GitHubLibraryError as exc:
                        dispatch_errors[job.id] = redact_text(exc)
            except (ValueError, GitHubLibraryError) as exc:
                message = redact_text(exc)
                for job, _item in finder_jobs:
                    dispatch_errors[job.id] = message

        for job, item, _plan in published + attached:
            self._link(
                job,
                item,
                settings,
                revision=revision,
                dispatch_error=dispatch_errors.get(job.id, ""),
            )
            self._advance_from_remote(job, item, library, settings, revision)

        for job, plan in terminal_plans:
            queue_status = str(plan.get("queue_status") or "invalid-plan")
            if job.job_type == "strategy.profit_first_validation" and queue_status == "no-eligible-candidates":
                self._complete_without_remote_validation(
                    job,
                    {
                        "outcome": "no_eligible_candidates",
                        "message": (
                            "Profit First found no strategy eligible for another strict cloud validation run."
                        ),
                        "plan": plan,
                        "research_library_revision": revision,
                    },
                )
            else:
                self.link_store.record_error(
                    job.id,
                    str(plan.get("bridge_error") or "Cloud research plan is not publishable."),
                    repository=settings.github.repository,
                    branch=settings.github.branch,
                    path=settings.github.path,
                    metadata={"plan": plan},
                )
        return True
