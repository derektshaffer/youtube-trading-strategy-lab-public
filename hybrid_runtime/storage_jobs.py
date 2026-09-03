"""Submission, lookup, and lifecycle transitions for hybrid jobs."""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from uuid import uuid4

from .contracts import (
    ExecutionTarget,
    JobRecord,
    JobRequest,
    JobStatus,
    TERMINAL_JOB_STATUSES,
    canonical_json,
    normalized_progress,
    transition_allowed,
    utc_now_text,
)
from .storage_base import HybridStoreError, InvalidJobTransition, JobNotFound


class JobStoreMixin:
    def cloud_recovery(self, job_id: str) -> dict[str, Any] | None:
        with self._reader() as connection:
            row = connection.execute(
                "SELECT binding_json FROM cloud_job_recoveries WHERE job_id = ? ORDER BY id DESC LIMIT 1",
                (job_id,),
            ).fetchone()
        return self._decode_json(row["binding_json"]) if row else None

    def reconnect_failed_finder(
        self, job_id: str, *, expected_updated_at: str,
        binding: Mapping[str, Any], worker_id: str,
    ) -> JobRecord:
        """Explicit recovery only; ordinary terminal transitions remain forbidden.

        The bridge must first verify this exact remote binding. Audit and local
        state change share a transaction so a crash cannot erase the failure.
        """
        if not all(binding.get(key) for key in ("remote_job_id", "repository", "branch", "path", "revision")):
            raise InvalidJobTransition("Verified cloud binding is required")
        now = utc_now_text()
        with self._transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise JobNotFound(f"Unknown job: {job_id}")
            current = self._record(row)
            if (
                current.status != JobStatus.FAILED
                or current.execution_target != ExecutionTarget.CLOUD
                or current.job_type != "strategy.stock_finder"
                or current.cancel_requested or current.result
                or current.updated_at != expected_updated_at
            ):
                raise InvalidJobTransition("Only the unchanged failed cloud Finder job can reconnect")
            previous = {
                key: current.as_dict().get(key)
                for key in ("status", "stage", "progress", "error", "result", "attempt", "updated_at", "completed_at")
            }
            connection.execute(
                "INSERT INTO cloud_job_recoveries(job_id, binding_json, previous_state_json, created_at) VALUES (?, ?, ?, ?)",
                (job_id, canonical_json(dict(binding)), canonical_json(previous), now),
            )
            connection.execute(
                """UPDATE jobs SET status = ?, stage = 'cloud_reconnected', progress = 0,
                error_json = NULL, completed_at = NULL, updated_at = ?, heartbeat_at = ?, worker_id = ?
                WHERE id = ?""",
                (JobStatus.CLAIMED.value, now, now, worker_id, job_id),
            )
            self._append_event(
                connection, job_id=job_id, status=JobStatus.CLAIMED,
                stage="cloud_reconnected", progress=0.0,
                message="Reconnected to the verified existing cloud run; previous failure archived; no research dispatched",
                created_at=now,
            )
            return self._record(connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

    def create_or_get_job(
        self,
        request: JobRequest,
        *,
        execution_target: ExecutionTarget,
        route_reason: str,
        dedupe_active: bool = True,
    ) -> tuple[JobRecord, bool]:
        now = utc_now_text()
        fingerprint = request.fingerprint()
        with self._transaction(immediate=True) as connection:
            existing = None
            if request.idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ?",
                    (request.idempotency_key,),
                ).fetchone()
            elif dedupe_active:
                terminal = tuple(status.value for status in TERMINAL_JOB_STATUSES)
                placeholders = ",".join("?" for _ in terminal)
                existing = connection.execute(
                    f"""
                    SELECT * FROM jobs
                    WHERE request_fingerprint = ? AND status NOT IN ({placeholders})
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (fingerprint, *terminal),
                ).fetchone()
            if existing is not None:
                if (
                    request.idempotency_key
                    and str(existing["request_fingerprint"]) != fingerprint
                ):
                    raise HybridStoreError(
                        "Idempotency key is already associated with a different request"
                    )
                return self._record(existing), False

            job_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO jobs(
                    id, request_fingerprint, idempotency_key, job_type,
                    requested_target, execution_target, route_reason,
                    status, stage, progress, priority, payload_json,
                    code_fingerprint, data_fingerprint, engine_version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    fingerprint,
                    request.idempotency_key,
                    request.job_type,
                    request.requested_target.value,
                    execution_target.value,
                    str(route_reason or ""),
                    JobStatus.QUEUED.value,
                    JobStatus.QUEUED.value,
                    0.0,
                    request.priority,
                    canonical_json(dict(request.payload)),
                    request.code_fingerprint,
                    request.data_fingerprint,
                    request.engine_version,
                    now,
                    now,
                ),
            )
            self._append_event(
                connection,
                job_id=job_id,
                status=JobStatus.QUEUED,
                stage=JobStatus.QUEUED.value,
                progress=0.0,
                message="Job queued",
                created_at=now,
            )
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:  # pragma: no cover
                raise JobNotFound(f"Unknown queued job: {job_id}")
            return self._record(row), True

    def get_job(self, job_id: str) -> JobRecord:
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (str(job_id),),
            ).fetchone()
        if row is None:
            raise JobNotFound(f"Unknown job: {job_id}")
        return self._record(row)

    def list_jobs(
        self,
        *,
        limit: int = 100,
        statuses: Sequence[JobStatus | str] | None = None,
    ) -> list[JobRecord]:
        maximum = max(1, min(1_000, int(limit)))
        arguments: list[Any] = []
        where = ""
        if statuses:
            values = [
                status.value if isinstance(status, JobStatus) else JobStatus(str(status)).value
                for status in statuses
            ]
            where = "WHERE status IN (" + ",".join("?" for _ in values) + ")"
            arguments.extend(values)
        arguments.append(maximum)
        with self._reader() as connection:
            rows = connection.execute(
                f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ?",
                arguments,
            ).fetchall()
        return [self._record(row) for row in rows]

    def transition_job(
        self,
        job_id: str,
        next_status: JobStatus | str,
        *,
        stage: str | None = None,
        progress: float | None = None,
        message: str = "",
        result: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
        worker_id: str | None = None,
    ) -> JobRecord:
        status = next_status if isinstance(next_status, JobStatus) else JobStatus(str(next_status))
        now = utc_now_text()
        with self._transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
            if row is None:
                raise JobNotFound(f"Unknown job: {job_id}")
            current = self._record(row)
            if not transition_allowed(current.status, status):
                raise InvalidJobTransition(
                    f"Cannot transition {current.status.value} -> {status.value}"
                )
            next_progress = current.progress if progress is None else normalized_progress(progress)
            if (
                status not in {JobStatus.RETRY_WAIT, JobStatus.QUEUED}
                and next_progress + 1e-12 < current.progress
            ):
                raise InvalidJobTransition("Job progress cannot move backwards")
            if status == JobStatus.COMPLETE:
                next_progress = 1.0
            next_stage = str(stage or status.value)
            completed_at = now if status in TERMINAL_JOB_STATUSES else current.completed_at
            connection.execute(
                """
                UPDATE jobs SET
                    status = ?, stage = ?, progress = ?, result_json = ?, error_json = ?,
                    worker_id = COALESCE(?, worker_id), updated_at = ?,
                    heartbeat_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    next_stage,
                    next_progress,
                    self._encoded(dict(result) if result is not None else None, row["result_json"]),
                    self._encoded(dict(error) if error is not None else None, row["error_json"]),
                    worker_id,
                    now,
                    now if status not in TERMINAL_JOB_STATUSES else row["heartbeat_at"],
                    completed_at,
                    str(job_id),
                ),
            )
            self._append_event(
                connection,
                job_id=str(job_id),
                status=status,
                stage=next_stage,
                progress=next_progress,
                message=message or next_stage.replace("_", " ").title(),
                created_at=now,
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (str(job_id),),
            ).fetchone()
            if updated is None:  # pragma: no cover
                raise JobNotFound(f"Unknown job after update: {job_id}")
            return self._record(updated)
