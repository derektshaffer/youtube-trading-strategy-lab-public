"""Worker claims, cancellation, lease recovery, and event reads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .contracts import ExecutionTarget, JobRecord, JobStatus, TERMINAL_JOB_STATUSES, utc_now_text
from .storage_base import HybridStoreError, JobNotFound


class WorkerStoreMixin:
    def claim_next(
        self,
        worker_id: str,
        *,
        target: ExecutionTarget = ExecutionTarget.LOCAL,
    ) -> JobRecord | None:
        clean_worker = str(worker_id or "").strip()
        if not clean_worker:
            raise ValueError("worker_id is required")
        now = utc_now_text()
        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = ? AND execution_target = ? AND cancel_requested = 0
                ORDER BY priority DESC, created_at ASC LIMIT 1
                """,
                (JobStatus.QUEUED.value, target.value),
            ).fetchone()
            if row is None:
                return None
            job_id = str(row["id"])
            cursor = connection.execute(
                """
                UPDATE jobs SET
                    status = ?, stage = ?, worker_id = ?, attempt = attempt + 1,
                    claimed_at = COALESCE(claimed_at, ?), heartbeat_at = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    JobStatus.CLAIMED.value,
                    JobStatus.CLAIMED.value,
                    clean_worker,
                    now,
                    now,
                    now,
                    job_id,
                    JobStatus.QUEUED.value,
                ),
            )
            if int(cursor.rowcount or 0) != 1:  # pragma: no cover
                return None
            self._append_event(
                connection,
                job_id=job_id,
                status=JobStatus.CLAIMED,
                stage=JobStatus.CLAIMED.value,
                progress=float(row["progress"]),
                message=f"Claimed by {clean_worker}",
                created_at=now,
            )
            updated = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return self._record(updated) if updated is not None else None

    def heartbeat(self, job_id: str, worker_id: str) -> JobRecord:
        now = utc_now_text()
        with self._transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
            if row is None:
                raise JobNotFound(f"Unknown job: {job_id}")
            if str(row["worker_id"] or "") != str(worker_id or ""):
                raise HybridStoreError("Only the claiming worker may heartbeat this job")
            if JobStatus(str(row["status"])) in TERMINAL_JOB_STATUSES:
                return self._record(row)
            connection.execute(
                "UPDATE jobs SET heartbeat_at = ?, updated_at = ? WHERE id = ?",
                (now, now, str(job_id)),
            )
            updated = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
            return self._record(updated) if updated is not None else self._record(row)

    def request_cancel(self, job_id: str) -> JobRecord:
        current = self.get_job(job_id)
        if current.terminal:
            return current
        if current.status in {JobStatus.QUEUED, JobStatus.RETRY_WAIT}:
            return self.transition_job(
                job_id,
                JobStatus.CANCELLED,
                stage="cancelled_before_start",
                progress=current.progress,
                message="Cancelled before execution",
            )
        with self._transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE jobs SET cancel_requested = 1, updated_at = ? WHERE id = ?",
                (utc_now_text(), str(job_id)),
            )
        return self.transition_job(
            job_id,
            JobStatus.CANCELLING,
            stage="cancelling",
            progress=current.progress,
            message="Cancellation requested",
        )

    def cancellation_requested(self, job_id: str) -> bool:
        return self.get_job(job_id).cancel_requested

    def requeue_stale_jobs(self, *, stale_after_seconds: int = 180) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(1, int(stale_after_seconds)))
        cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
        recoverable = (
            JobStatus.CLAIMED,
            JobStatus.DOWNLOADING_DATA,
            JobStatus.PREPARING_FEATURES,
            JobStatus.SEARCHING,
            JobStatus.OPTIMIZING,
            JobStatus.VALIDATING,
            JobStatus.SAVING,
            JobStatus.CANCELLING,
        )
        values = tuple(status.value for status in recoverable)
        placeholders = ",".join("?" for _ in values)
        now = utc_now_text()
        with self._transaction(immediate=True) as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM jobs
                WHERE status IN ({placeholders})
                  AND COALESCE(heartbeat_at, claimed_at, updated_at) < ?
                """,
                (*values, cutoff_text),
            ).fetchall()
            for row in rows:
                cancelling = bool(row["cancel_requested"]) or str(row["status"]) == JobStatus.CANCELLING.value
                status = JobStatus.CANCELLED if cancelling else JobStatus.QUEUED
                stage = (
                    "cancelled_after_stale_lease"
                    if cancelling
                    else "requeued_after_stale_lease"
                )
                connection.execute(
                    """
                    UPDATE jobs SET status = ?, stage = ?, worker_id = NULL,
                        heartbeat_at = NULL, claimed_at = NULL, updated_at = ?,
                        completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        status.value,
                        stage,
                        now,
                        now if cancelling else None,
                        str(row["id"]),
                    ),
                )
                self._append_event(
                    connection,
                    job_id=str(row["id"]),
                    status=status,
                    stage=stage,
                    progress=float(row["progress"]),
                    message=(
                        "Cancellation completed after the worker lease expired"
                        if cancelling
                        else "Worker heartbeat expired; job safely requeued"
                    ),
                    created_at=now,
                )
            return len(rows)

    def list_events(
        self,
        job_id: str,
        *,
        after_id: int = 0,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        self.get_job(job_id)
        with self._reader() as connection:
            rows = connection.execute(
                """
                SELECT id, job_id, status, stage, progress, message, created_at
                FROM job_events
                WHERE job_id = ? AND id > ?
                ORDER BY id ASC LIMIT ?
                """,
                (str(job_id), max(0, int(after_id)), max(1, min(1_000, int(limit)))),
            ).fetchall()
        return [dict(row) for row in rows]
