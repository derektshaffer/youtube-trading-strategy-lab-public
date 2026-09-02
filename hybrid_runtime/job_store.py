"""Transactional job queue used by the desktop client and local/cloud workers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Any, Mapping
from uuid import uuid4

from .contracts import (
    ACTIVE_JOB_STATES,
    TERMINAL_JOB_STATES,
    ExecutionTarget,
    JobKind,
    JobState,
    ReproducibilityMetadata,
    RouteDecision,
    canonical_json,
    stable_fingerprint,
    utc_now_text,
)
from .database import HybridDatabase


_ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.CLAIMED: frozenset(
        {
            JobState.DOWNLOADING_DATA,
            JobState.PREPARING_FEATURES,
            JobState.SEARCHING,
            JobState.OPTIMIZING,
            JobState.VALIDATING,
            JobState.SAVING,
        }
    ),
    JobState.DOWNLOADING_DATA: frozenset(
        {
            JobState.PREPARING_FEATURES,
            JobState.SEARCHING,
            JobState.OPTIMIZING,
            JobState.VALIDATING,
            JobState.SAVING,
        }
    ),
    JobState.PREPARING_FEATURES: frozenset(
        {
            JobState.SEARCHING,
            JobState.OPTIMIZING,
            JobState.VALIDATING,
            JobState.SAVING,
        }
    ),
    JobState.SEARCHING: frozenset(
        {JobState.OPTIMIZING, JobState.VALIDATING, JobState.SAVING}
    ),
    JobState.OPTIMIZING: frozenset({JobState.VALIDATING, JobState.SAVING}),
    JobState.VALIDATING: frozenset({JobState.SAVING}),
    JobState.SAVING: frozenset(),
}


class JobStateError(RuntimeError):
    """Raised when a worker attempts an unsafe job transition."""


def _as_utc(value: datetime | str | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        current = value
    else:
        current = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _time_text(value: datetime | str | None = None) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_or(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _route_dict(route: RouteDecision | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(route, RouteDecision):
        return route.to_dict()
    result = dict(route or {})
    result["requested"] = ExecutionTarget(
        str(result.get("requested") or ExecutionTarget.AUTO.value)
    ).value
    result["resolved"] = ExecutionTarget(str(result.get("resolved"))).value
    return result


class JobStore:
    """Durable queue with deduplication, leases, checkpoints, and cancellation."""

    def __init__(self, database: HybridDatabase):
        self.database = database

    @staticmethod
    def _record(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        output = dict(row)
        output["payload"] = _json_or(output.pop("payload_json", None), {})
        output["route"] = _json_or(output.pop("route_json", None), {})
        output["checkpoint"] = _json_or(output.pop("checkpoint_json", None), None)
        output["cancel_requested"] = bool(output.get("cancel_requested"))
        return output

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        job_id: str,
        state: JobState,
        stage: str,
        progress: float,
        message: str,
        created_at: str,
    ) -> None:
        connection.execute(
            "INSERT INTO job_events(job_id, state, stage, progress, message, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, state.value, stage, progress, message, created_at),
        )

    def get(self, job_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (str(job_id),)
            ).fetchone()
        return self._record(row)

    def list_jobs(
        self,
        *,
        states: list[JobState | str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = []
        where = ""
        if states:
            values = [JobState(str(value)).value for value in states]
            where = " WHERE status IN (" + ",".join("?" for _ in values) + ")"
            parameters.extend(values)
        parameters.append(max(1, min(500, int(limit))))
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs"
                + where
                + " ORDER BY created_at DESC LIMIT ?",
                parameters,
            ).fetchall()
        return [self._record(row) for row in rows]

    def events(self, job_id: str, *, after_id: int = 0) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM job_events WHERE job_id = ? AND id > ? ORDER BY id ASC",
                (str(job_id), max(0, int(after_id))),
            ).fetchall()
        return [dict(row) for row in rows]

    def submit(
        self,
        kind: JobKind | str,
        payload: Mapping[str, Any],
        route: RouteDecision | Mapping[str, Any],
        *,
        priority: int = 0,
        dedupe_key: str | None = None,
        max_attempts: int = 3,
        now: datetime | str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        resolved_kind = kind if isinstance(kind, JobKind) else JobKind(str(kind))
        route_data = _route_dict(route)
        payload_data = dict(payload or {})
        payload_json = canonical_json(payload_data)
        route_json = canonical_json(route_data)
        fingerprint = str(dedupe_key or "").strip() or stable_fingerprint(
            {
                "kind": resolved_kind.value,
                "payload": payload_data,
                "resolved_target": route_data["resolved"],
            }
        )
        timestamp = _time_text(now)
        active_values = [state.value for state in ACTIVE_JOB_STATES]
        placeholders = ",".join("?" for _ in active_values)

        with self.database.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM jobs WHERE dedupe_key = ? "
                f"AND status IN ({placeholders}) ORDER BY created_at DESC LIMIT 1",
                [fingerprint, *active_values],
            ).fetchone()
            if existing is not None:
                return self._record(existing), False

            job_id = "hybrid-job-" + uuid4().hex
            connection.execute(
                """
                INSERT INTO jobs(
                    id, kind, requested_target, resolved_target, status,
                    payload_json, route_json, dedupe_key, priority, progress,
                    stage, message, max_attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, 'queued', ?, ?, ?, ?)
                """,
                (
                    job_id,
                    resolved_kind.value,
                    route_data["requested"],
                    route_data["resolved"],
                    JobState.QUEUED.value,
                    payload_json,
                    route_json,
                    fingerprint,
                    int(priority),
                    "Waiting for an available worker.",
                    max(1, min(20, int(max_attempts))),
                    timestamp,
                    timestamp,
                ),
            )
            self._event(
                connection,
                job_id,
                JobState.QUEUED,
                "queued",
                0.0,
                "Waiting for an available worker.",
                timestamp,
            )
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._record(row), True

    def _reclaim_expired_locked(
        self,
        connection: sqlite3.Connection,
        *,
        now_text: str,
    ) -> int:
        states = [
            JobState.CLAIMED,
            JobState.DOWNLOADING_DATA,
            JobState.PREPARING_FEATURES,
            JobState.SEARCHING,
            JobState.OPTIMIZING,
            JobState.VALIDATING,
            JobState.SAVING,
        ]
        placeholders = ",".join("?" for _ in states)
        rows = connection.execute(
            "SELECT * FROM jobs "
            f"WHERE status IN ({placeholders}) "
            "AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?",
            [*(state.value for state in states), now_text],
        ).fetchall()
        reclaimed = 0
        for row in rows:
            record = dict(row)
            job_id = str(record["id"])
            if bool(record.get("cancel_requested")):
                next_state = JobState.CANCELLED
                message = "Cancelled after the previous worker lease expired."
                completed_at = now_text
            elif int(record.get("attempt") or 0) >= int(record.get("max_attempts") or 1):
                next_state = JobState.FAILED
                message = "Worker lease expired and the maximum retry count was reached."
                completed_at = now_text
            else:
                next_state = JobState.QUEUED
                message = "The previous worker stopped responding; the job was safely requeued."
                completed_at = None
            connection.execute(
                """
                UPDATE jobs SET status = ?, stage = ?, message = ?, worker_id = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL, next_attempt_at = NULL,
                    updated_at = ?, completed_at = ?, error = ?
                WHERE id = ?
                """,
                (
                    next_state.value,
                    next_state.value,
                    message,
                    now_text,
                    completed_at,
                    message if next_state == JobState.FAILED else None,
                    job_id,
                ),
            )
            self._event(
                connection,
                job_id,
                next_state,
                next_state.value,
                float(record.get("progress") or 0.0),
                message,
                now_text,
            )
            reclaimed += 1
        return reclaimed

    def reclaim_expired(self, *, now: datetime | str | None = None) -> int:
        timestamp = _time_text(now)
        with self.database.transaction(immediate=True) as connection:
            return self._reclaim_expired_locked(connection, now_text=timestamp)

    def claim(
        self,
        worker_id: str,
        *,
        target: ExecutionTarget | str | None = None,
        lease_seconds: int = 120,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        worker = str(worker_id or "").strip()
        if not worker:
            raise ValueError("A non-empty worker_id is required to claim a job.")
        current = _as_utc(now)
        timestamp = _time_text(current)
        lease = _time_text(current + timedelta(seconds=max(15, int(lease_seconds))))
        target_value = ExecutionTarget(str(target)).value if target is not None else None

        with self.database.transaction(immediate=True) as connection:
            self._reclaim_expired_locked(connection, now_text=timestamp)
            parameters: list[Any] = [
                JobState.QUEUED.value,
                JobState.RETRY_WAIT.value,
                timestamp,
            ]
            target_clause = ""
            if target_value:
                target_clause = " AND resolved_target = ?"
                parameters.append(target_value)
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status IN (?, ?)
                  AND cancel_requested = 0
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                """
                + target_clause
                + " ORDER BY priority DESC, created_at ASC LIMIT 1",
                parameters,
            ).fetchone()
            if row is None:
                return {}
            job_id = str(row["id"])
            message = f"Claimed by {worker}."
            connection.execute(
                """
                UPDATE jobs SET status = ?, stage = ?, message = ?, attempt = attempt + 1,
                    worker_id = ?, heartbeat_at = ?, lease_expires_at = ?,
                    next_attempt_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    JobState.CLAIMED.value,
                    JobState.CLAIMED.value,
                    message,
                    worker,
                    timestamp,
                    lease,
                    timestamp,
                    job_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            self._event(
                connection,
                job_id,
                JobState.CLAIMED,
                JobState.CLAIMED.value,
                float(updated["progress"] or 0.0),
                message,
                timestamp,
            )
        return self._record(updated)

    @staticmethod
    def _assert_worker(row: sqlite3.Row, worker_id: str | None) -> None:
        assigned = str(row["worker_id"] or "").strip()
        supplied = str(worker_id or "").strip()
        if assigned and assigned != supplied:
            raise JobStateError(
                f"Job is leased to {assigned}; worker {supplied or '<blank>'} cannot update it."
            )

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 120,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        current = _as_utc(now)
        timestamp = _time_text(current)
        lease = _time_text(current + timedelta(seconds=max(15, int(lease_seconds))))
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (str(job_id),)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown hybrid job: {job_id}")
            self._assert_worker(row, worker_id)
            state = JobState(str(row["status"]))
            if state in TERMINAL_JOB_STATES or state in {JobState.QUEUED, JobState.RETRY_WAIT}:
                raise JobStateError(f"Cannot heartbeat a {state.value} job.")
            connection.execute(
                "UPDATE jobs SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ? "
                "WHERE id = ?",
                (timestamp, lease, timestamp, str(job_id)),
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (str(job_id),)
            ).fetchone()
        return self._record(updated)

    def transition(
        self,
        job_id: str,
        state: JobState | str,
        *,
        worker_id: str,
        stage: str | None = None,
        progress: float | None = None,
        message: str = "",
        lease_seconds: int = 120,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        next_state = state if isinstance(state, JobState) else JobState(str(state))
        if next_state in TERMINAL_JOB_STATES or next_state in {
            JobState.QUEUED,
            JobState.RETRY_WAIT,
        }:
            raise JobStateError(
                "Use complete(), fail(), or request_cancel() for terminal/retry transitions."
            )
        current = _as_utc(now)
        timestamp = _time_text(current)
        lease = _time_text(current + timedelta(seconds=max(15, int(lease_seconds))))

        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (str(job_id),)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown hybrid job: {job_id}")
            self._assert_worker(row, worker_id)
            current_state = JobState(str(row["status"]))
            if bool(row["cancel_requested"]):
                raise JobStateError("Job cancellation was requested; the worker must stop safely.")
            if next_state != current_state and next_state not in _ALLOWED_TRANSITIONS.get(
                current_state, frozenset()
            ):
                raise JobStateError(
                    f"Unsafe hybrid job transition: {current_state.value} -> {next_state.value}."
                )
            next_progress = max(
                float(row["progress"] or 0.0),
                min(0.999, max(0.0, float(progress if progress is not None else row["progress"] or 0.0))),
            )
            next_stage = str(stage or next_state.value)
            next_message = str(message or row["message"] or "")
            connection.execute(
                """
                UPDATE jobs SET status = ?, stage = ?, progress = ?, message = ?,
                    heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    next_state.value,
                    next_stage,
                    next_progress,
                    next_message,
                    timestamp,
                    lease,
                    timestamp,
                    str(job_id),
                ),
            )
            self._event(
                connection,
                str(job_id),
                next_state,
                next_stage,
                next_progress,
                next_message,
                timestamp,
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (str(job_id),)
            ).fetchone()
        return self._record(updated)

    def save_checkpoint(
        self,
        job_id: str,
        worker_id: str,
        checkpoint: Mapping[str, Any],
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        timestamp = _time_text(now)
        encoded = canonical_json(dict(checkpoint or {}))
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (str(job_id),)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown hybrid job: {job_id}")
            self._assert_worker(row, worker_id)
            state = JobState(str(row["status"]))
            if state in TERMINAL_JOB_STATES:
                raise JobStateError(f"Cannot checkpoint a {state.value} job.")
            connection.execute(
                "UPDATE jobs SET checkpoint_json = ?, updated_at = ? WHERE id = ?",
                (encoded, timestamp, str(job_id)),
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (str(job_id),)
            ).fetchone()
        return self._record(updated)

    def request_cancel(
        self,
        job_id: str,
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        timestamp = _time_text(now)
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (str(job_id),)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown hybrid job: {job_id}")
            state = JobState(str(row["status"]))
            if state in TERMINAL_JOB_STATES:
                return self._record(row)
            immediate = state in {JobState.QUEUED, JobState.RETRY_WAIT}
            next_state = JobState.CANCELLED if immediate else state
            message = (
                "Cancelled before a worker started."
                if immediate
                else "Cancellation requested; the worker will stop at the next safe checkpoint."
            )
            connection.execute(
                """
                UPDATE jobs SET status = ?, stage = ?, message = ?, cancel_requested = 1,
                    updated_at = ?, completed_at = ?, worker_id = CASE WHEN ? THEN NULL ELSE worker_id END,
                    lease_expires_at = CASE WHEN ? THEN NULL ELSE lease_expires_at END
                WHERE id = ?
                """,
                (
                    next_state.value,
                    next_state.value if immediate else str(row["stage"]),
                    message,
                    timestamp,
                    timestamp if immediate else None,
                    int(immediate),
                    int(immediate),
                    str(job_id),
                ),
            )
            self._event(
                connection,
                str(job_id),
                next_state,
                next_state.value if immediate else str(row["stage"]),
                float(row["progress"] or 0.0),
                message,
                timestamp,
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (str(job_id),)
            ).fetchone()
        return self._record(updated)

    def fail(
        self,
        job_id: str,
        worker_id: str,
        error: BaseException | str,
        *,
        retryable: bool = True,
        retry_delay_seconds: int = 30,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        current = _as_utc(now)
        timestamp = _time_text(current)
        safe_error = " ".join(str(error or "Unknown worker failure").split())[:2_000]
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (str(job_id),)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown hybrid job: {job_id}")
            self._assert_worker(row, worker_id)
            state = JobState(str(row["status"]))
            if state in TERMINAL_JOB_STATES:
                return self._record(row)
            can_retry = (
                bool(retryable)
                and not bool(row["cancel_requested"])
                and int(row["attempt"] or 0) < int(row["max_attempts"] or 1)
            )
            if bool(row["cancel_requested"]):
                next_state = JobState.CANCELLED
                message = "Cancelled at a safe worker checkpoint."
                next_attempt = None
                completed_at = timestamp
            elif can_retry:
                next_state = JobState.RETRY_WAIT
                delay = max(1, min(86_400, int(retry_delay_seconds)))
                next_attempt = _time_text(current + timedelta(seconds=delay))
                message = f"Worker failed safely; retry scheduled after {delay} seconds."
                completed_at = None
            else:
                next_state = JobState.FAILED
                next_attempt = None
                message = "Worker failed and no further automatic retry is allowed."
                completed_at = timestamp
            connection.execute(
                """
                UPDATE jobs SET status = ?, stage = ?, message = ?, error = ?,
                    worker_id = NULL, heartbeat_at = NULL, lease_expires_at = NULL,
                    next_attempt_at = ?, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    next_state.value,
                    next_state.value,
                    message,
                    safe_error,
                    next_attempt,
                    timestamp,
                    completed_at,
                    str(job_id),
                ),
            )
            self._event(
                connection,
                str(job_id),
                next_state,
                next_state.value,
                float(row["progress"] or 0.0),
                message,
                timestamp,
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (str(job_id),)
            ).fetchone()
        return self._record(updated)

    def complete(
        self,
        job_id: str,
        worker_id: str,
        result: Mapping[str, Any],
        reproducibility: ReproducibilityMetadata | Mapping[str, Any],
        *,
        now: datetime | str | None = None,
    ) -> dict[str, Any]:
        metadata = (
            reproducibility
            if isinstance(reproducibility, ReproducibilityMetadata)
            else ReproducibilityMetadata(**dict(reproducibility or {}))
        )
        metadata_data = metadata.to_dict()
        result_json = canonical_json(dict(result or {}))
        metadata_json = canonical_json(metadata_data)
        timestamp = _time_text(now)
        result_id = "hybrid-result-" + uuid4().hex

        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (str(job_id),)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown hybrid job: {job_id}")
            self._assert_worker(row, worker_id)
            state = JobState(str(row["status"]))
            if state == JobState.COMPLETE:
                return self._record(row)
            if state in {JobState.FAILED, JobState.CANCELLED, JobState.QUEUED, JobState.RETRY_WAIT}:
                raise JobStateError(f"Cannot complete a {state.value} job.")
            if bool(row["cancel_requested"]):
                raise JobStateError("Cannot publish a result after cancellation was requested.")
            connection.execute(
                "INSERT INTO results(id, job_id, result_json, reproducibility_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (result_id, str(job_id), result_json, metadata_json, timestamp),
            )
            message = "Job completed and its reproducibility evidence was saved."
            connection.execute(
                """
                UPDATE jobs SET status = ?, stage = ?, progress = 1.0, message = ?,
                    worker_id = NULL, heartbeat_at = NULL, lease_expires_at = NULL,
                    next_attempt_at = NULL, error = NULL, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    JobState.COMPLETE.value,
                    JobState.COMPLETE.value,
                    message,
                    timestamp,
                    timestamp,
                    str(job_id),
                ),
            )
            self._event(
                connection,
                str(job_id),
                JobState.COMPLETE,
                JobState.COMPLETE.value,
                1.0,
                message,
                timestamp,
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (str(job_id),)
            ).fetchone()
        return self._record(updated)

    def result(self, job_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM results WHERE job_id = ?", (str(job_id),)
            ).fetchone()
        if row is None:
            return {}
        output = dict(row)
        output["result"] = _json_or(output.pop("result_json", None), {})
        output["reproducibility"] = _json_or(
            output.pop("reproducibility_json", None), {}
        )
        return output
