"""Stable contracts shared by the desktop UI, local service, and cloud workers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from math import isfinite
from typing import Any, Mapping


class ExecutionTarget(str, Enum):
    AUTO = "auto"
    LOCAL = "local"
    CLOUD = "cloud"


class JobStatus(str, Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    DOWNLOADING_DATA = "downloading_data"
    PREPARING_FEATURES = "preparing_features"
    SEARCHING = "searching"
    OPTIMIZING = "optimizing"
    VALIDATING = "validating"
    SAVING = "saving"
    RETRY_WAIT = "retry_wait"
    CANCELLING = "cancelling"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_JOB_STATUSES = {
    JobStatus.COMPLETE,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
}

_ACTIVE_STAGE_ORDER = {
    JobStatus.CLAIMED: 0,
    JobStatus.DOWNLOADING_DATA: 1,
    JobStatus.PREPARING_FEATURES: 2,
    JobStatus.SEARCHING: 3,
    JobStatus.OPTIMIZING: 4,
    JobStatus.VALIDATING: 5,
    JobStatus.SAVING: 6,
}


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    """Return deterministic JSON suitable for fingerprints and durable records."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


def _clean_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def _clean_target(value: ExecutionTarget | str) -> ExecutionTarget:
    if isinstance(value, ExecutionTarget):
        return value
    return ExecutionTarget(str(value or ExecutionTarget.AUTO.value).strip().lower())


@dataclass(frozen=True, slots=True)
class JobRequest:
    job_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    requested_target: ExecutionTarget | str = ExecutionTarget.AUTO
    priority: int = 0
    idempotency_key: str | None = None
    code_fingerprint: str = ""
    data_fingerprint: str = ""
    engine_version: str = ""

    def __post_init__(self) -> None:
        clean_type = str(self.job_type or "").strip()
        if not clean_type:
            raise ValueError("job_type is required")
        if len(clean_type) > 120:
            raise ValueError("job_type is too long")
        payload = _clean_mapping(self.payload)
        # Fail early rather than persisting NaN/Infinity or unserializable payloads.
        canonical_json(payload)
        target = _clean_target(self.requested_target)
        priority = max(-1000, min(1000, int(self.priority)))
        idem = str(self.idempotency_key or "").strip() or None
        if idem and len(idem) > 240:
            raise ValueError("idempotency_key is too long")
        object.__setattr__(self, "job_type", clean_type)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "requested_target", target)
        object.__setattr__(self, "priority", priority)
        object.__setattr__(self, "idempotency_key", idem)
        object.__setattr__(self, "code_fingerprint", str(self.code_fingerprint or "").strip())
        object.__setattr__(self, "data_fingerprint", str(self.data_fingerprint or "").strip())
        object.__setattr__(self, "engine_version", str(self.engine_version or "").strip())

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "JobRequest":
        return cls(
            job_type=str(raw.get("job_type") or ""),
            payload=_clean_mapping(raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {}),
            requested_target=raw.get("requested_target") or ExecutionTarget.AUTO,
            priority=int(raw.get("priority") or 0),
            idempotency_key=raw.get("idempotency_key"),
            code_fingerprint=str(raw.get("code_fingerprint") or ""),
            data_fingerprint=str(raw.get("data_fingerprint") or ""),
            engine_version=str(raw.get("engine_version") or ""),
        )

    def fingerprint(self) -> str:
        material = {
            "job_type": self.job_type,
            "payload": dict(self.payload),
            "requested_target": self.requested_target.value,
            "code_fingerprint": self.code_fingerprint,
            "data_fingerprint": self.data_fingerprint,
            "engine_version": self.engine_version,
        }
        return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["requested_target"] = self.requested_target.value
        result["payload"] = dict(self.payload)
        result["request_fingerprint"] = self.fingerprint()
        return result


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: str
    request_fingerprint: str
    job_type: str
    requested_target: ExecutionTarget
    execution_target: ExecutionTarget
    route_reason: str
    status: JobStatus
    stage: str
    progress: float
    priority: int
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    idempotency_key: str | None
    code_fingerprint: str
    data_fingerprint: str
    engine_version: str
    worker_id: str | None
    attempt: int
    cancel_requested: bool
    created_at: str
    updated_at: str
    claimed_at: str | None
    heartbeat_at: str | None
    completed_at: str | None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_JOB_STATUSES

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["requested_target"] = self.requested_target.value
        result["execution_target"] = self.execution_target.value
        result["status"] = self.status.value
        result["terminal"] = self.terminal
        return result


def normalized_progress(value: float | int | None) -> float:
    try:
        number = float(value if value is not None else 0.0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("progress must be numeric") from exc
    if not isfinite(number):
        raise ValueError("progress must be finite")
    return max(0.0, min(1.0, number))


def transition_allowed(previous: JobStatus, next_status: JobStatus) -> bool:
    """Validate the durable state machine without allowing silent stage rewind."""

    if previous == next_status:
        return True
    if previous in TERMINAL_JOB_STATUSES:
        return False
    if previous == JobStatus.QUEUED:
        return next_status in {JobStatus.CLAIMED, JobStatus.CANCELLED}
    if previous == JobStatus.RETRY_WAIT:
        return next_status in {JobStatus.QUEUED, JobStatus.CANCELLED}
    if previous == JobStatus.CANCELLING:
        return next_status in {JobStatus.CANCELLED, JobStatus.FAILED}
    if next_status in {JobStatus.FAILED, JobStatus.CANCELLING, JobStatus.RETRY_WAIT}:
        return previous not in TERMINAL_JOB_STATUSES
    if next_status == JobStatus.CANCELLED:
        return previous in {JobStatus.QUEUED, JobStatus.RETRY_WAIT, JobStatus.CANCELLING}
    if next_status == JobStatus.COMPLETE:
        return previous in _ACTIVE_STAGE_ORDER
    if previous in _ACTIVE_STAGE_ORDER and next_status in _ACTIVE_STAGE_ORDER:
        return _ACTIVE_STAGE_ORDER[next_status] >= _ACTIVE_STAGE_ORDER[previous]
    return False
