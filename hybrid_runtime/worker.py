"""Bounded local worker for quick jobs; heavy jobs remain queued for cloud workers."""

from __future__ import annotations

import threading
import time
from typing import Any, Mapping

from .contracts import JobStatus
from .engine_adapter import JobCancelled, JobHandler, default_handlers
from .security import redact_text
from .service import HybridService
from .storage import InvalidJobTransition


class LocalWorker:
    def __init__(
        self,
        service: HybridService,
        *,
        worker_id: str,
        handlers: Mapping[str, JobHandler] | None = None,
        poll_seconds: float = 0.25,
    ) -> None:
        self.service = service
        self.worker_id = str(worker_id or "local-worker").strip()
        self.handlers = dict(default_handlers() if handlers is None else handlers)
        self.poll_seconds = max(0.05, float(poll_seconds))

    def run_once(self) -> bool:
        job = self.service.claim_local(self.worker_id)
        if job is None:
            return False
        handler = self.handlers.get(job.job_type)
        if handler is None:
            self.service.store.transition_job(
                job.id,
                JobStatus.FAILED,
                stage="failed",
                error={"type": "UnsupportedJob", "message": f"No local handler for {job.job_type}"},
                worker_id=self.worker_id,
                message="No local handler is registered for this job",
            )
            return True

        def cancelled() -> bool:
            return self.service.store.cancellation_requested(job.id)

        def progress(fraction: float, stage: str, message: str) -> None:
            if cancelled():
                raise JobCancelled("Job cancellation was requested")
            self.service.store.transition_job(
                job.id,
                JobStatus(str(stage)),
                stage=str(stage),
                progress=fraction,
                worker_id=self.worker_id,
                message=message,
            )

        try:
            result = dict(handler(job.payload, progress, cancelled))
            if cancelled():
                raise JobCancelled("Job cancellation was requested")
            self.service.complete(job.id, result, worker_id=self.worker_id)
        except JobCancelled:
            current = self.service.get(job.id)
            try:
                self.service.store.transition_job(
                    job.id,
                    JobStatus.CANCELLED,
                    stage="cancelled",
                    progress=current.progress,
                    worker_id=self.worker_id,
                    message="Job cancelled",
                )
            except InvalidJobTransition:
                # request_cancel moves active jobs through CANCELLING first.
                self.service.store.transition_job(
                    job.id,
                    JobStatus.CANCELLING,
                    stage="cancelling",
                    progress=current.progress,
                    worker_id=self.worker_id,
                    message="Stopping local work",
                )
                self.service.store.transition_job(
                    job.id,
                    JobStatus.CANCELLED,
                    stage="cancelled",
                    progress=current.progress,
                    worker_id=self.worker_id,
                    message="Job cancelled",
                )
        except Exception as exc:
            current = self.service.get(job.id)
            self.service.store.transition_job(
                job.id,
                JobStatus.FAILED,
                stage="failed",
                progress=current.progress,
                error={
                    "type": type(exc).__name__,
                    "message": redact_text(exc),
                },
                worker_id=self.worker_id,
                message="Local execution failed",
            )
        return True

    def run_forever(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            worked = self.run_once()
            if not worked:
                stop_event.wait(self.poll_seconds)
            else:
                time.sleep(0)
