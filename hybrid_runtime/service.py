"""Application service used by HTTP, desktop, and test clients."""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import ExecutionTarget, JobRecord, JobRequest, JobStatus
from .router import RoutingDecision, RoutingPolicy
from .storage import HybridStore


class HybridService:
    def __init__(self, store: HybridStore, policy: RoutingPolicy | None = None) -> None:
        self.store = store
        self.policy = policy or RoutingPolicy()

    def route_preview(self, raw_request: JobRequest | Mapping[str, Any]) -> RoutingDecision:
        request = raw_request if isinstance(raw_request, JobRequest) else JobRequest.from_mapping(raw_request)
        return self.policy.decide(request)

    def submit(self, raw_request: JobRequest | Mapping[str, Any]) -> tuple[JobRecord, bool]:
        request = raw_request if isinstance(raw_request, JobRequest) else JobRequest.from_mapping(raw_request)
        decision = self.policy.decide(request)
        return self.store.create_or_get_job(
            request,
            execution_target=decision.target,
            route_reason=decision.reason,
        )

    def get(self, job_id: str) -> JobRecord:
        return self.store.get_job(job_id)

    def list(self, *, limit: int = 100) -> list[JobRecord]:
        return self.store.list_jobs(limit=limit)

    def cancel(self, job_id: str) -> JobRecord:
        return self.store.request_cancel(job_id)

    def events(self, job_id: str, *, after_id: int = 0) -> list[dict[str, Any]]:
        return self.store.list_events(job_id, after_id=after_id)

    def claim_local(self, worker_id: str) -> JobRecord | None:
        return self.store.claim_next(worker_id, target=ExecutionTarget.LOCAL)

    def complete(self, job_id: str, result: Mapping[str, Any], *, worker_id: str) -> JobRecord:
        return self.store.transition_job(
            job_id,
            JobStatus.COMPLETE,
            stage="complete",
            progress=1.0,
            result=result,
            worker_id=worker_id,
            message="Job complete",
        )
