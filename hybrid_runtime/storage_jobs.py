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
    def enrich_completed_strategy_lab(self, job_id: str, *, result: Mapping[str, Any],
                                     expected_result: Mapping[str, Any] | None) -> JobRecord:
        """CAS refresh of an exact completed result, without lifecycle/queue mutation."""
        from .strategy_lab_result_projection import PROJECTION_VERSION
        with self._transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise JobNotFound(f"Unknown job: {job_id}")
            current = self._record(row)
            payload, old = current.payload, current.result or {}
            ids = payload.get("strategy_ids") or []
            if (current.status != JobStatus.COMPLETE or current.job_type != "strategy.strategy_lab"
                    or current.execution_target != ExecutionTarget.CLOUD or current.result != expected_result
                    or result.get("projection_version") != PROJECTION_VERSION
                    or result.get("outcome") != "strategy_lab_complete"
                    or result.get("job_id") != job_id
                    or not payload.get("run_id") or result.get("run_id") != payload["run_id"]
                    or result.get("ticker") != str(payload.get("ticker") or "").upper()
                    or not result.get("winner_strategy_id")
                    or (not payload.get("compared_all") and result["winner_strategy_id"] not in ids)
                    or not old.get("remote_job_id") or result.get("remote_job_id") != old["remote_job_id"]
                    or result.get("result_ref") != "strategy-lab-checkpoint:" + payload["run_id"]):
                raise InvalidJobTransition("Only the exact unchanged completed validation may receive projected evidence")
            # Enrichment must not revise the previously saved verdict or numeric evidence.
            for block in ("evidence_verdict", "strength", "training_metrics", "validation_metrics", "holdout_metrics", "stress_metrics"):
                prior, fresh = old.get(block), result.get(block)
                if isinstance(prior, Mapping) and any(k not in (fresh or {}) or fresh[k] != v for k, v in prior.items()):
                    raise InvalidJobTransition("Projection cannot change existing validation evidence")
            connection.execute("UPDATE jobs SET result_json = ? WHERE id = ?", (canonical_json(dict(result)), job_id))
            return self._record(connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

    def enrich_failed_strategy_lab(self, job_id: str, *, error: Mapping[str, Any],
                                   expected_updated_at: str) -> JobRecord:
        """Metadata-only reconciliation. Never reopen a terminal execution."""
        from .diagnostic_budget import diagnostic_budget
        with self._transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise JobNotFound(f"Unknown job: {job_id}")
            current = self._record(row)
            if (current.status != JobStatus.FAILED or current.job_type != "strategy.strategy_lab"
                    or current.execution_target != ExecutionTarget.CLOUD or current.result
                    or not diagnostic_budget(current.payload) or current.updated_at != expected_updated_at
                    or error.get("kind") not in {"execution_timeout", "execution_interrupted"}):
                raise InvalidJobTransition("Only an unchanged failed diagnostic can receive terminal details")
            connection.execute("UPDATE jobs SET error_json = ?, progress = ? WHERE id = ?",
                               (canonical_json(dict(error)),
                                max(current.progress, normalized_progress(error.get("last_progress") or 0)), job_id))
            self._append_event(connection, job_id=job_id, status=current.status, stage=current.stage,
                               progress=max(current.progress, normalized_progress(error.get("last_progress") or 0)),
                               message="Reconciled diagnostic infrastructure failure details", created_at=utc_now_text())
            return self._record(connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

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
        expected_status: JobStatus | None = None,
        expected_stage: str | None = None,
    ) -> JobRecord:
        status = next_status if isinstance(next_status, JobStatus) else JobStatus(str(next_status))
        now = utc_now_text()
        with self._transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
            if row is None:
                raise JobNotFound(f"Unknown job: {job_id}")
            current = self._record(row)
            if ((expected_status is not None and current.status != expected_status)
                    or (expected_stage is not None and current.stage != expected_stage)):
                raise InvalidJobTransition("The saved job changed; refresh before retrying.")
            if current.job_type == 'research.independent_review' and status != JobStatus.CANCELLED:
                raise InvalidJobTransition('Independent review jobs advance only through the frozen-packet review gate')
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
