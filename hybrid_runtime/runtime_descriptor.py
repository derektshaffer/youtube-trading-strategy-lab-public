"""Secure handoff between a desktop shell and its localhost Python service."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hmac
import json
import os
from pathlib import Path
import secrets
from typing import Any

from .contracts import utc_now_text


LOOPBACK_HOST = "127.0.0.1"


@dataclass(frozen=True, slots=True)
class RuntimeDescriptor:
    host: str
    port: int
    token: str
    pid: int
    started_at: str
    api_version: str = "v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_loopback_host(host: str) -> str:
    value = str(host or "").strip()
    if value != LOOPBACK_HOST:
        raise ValueError(
            "The hybrid local service may bind only to 127.0.0.1; "
            "network exposure is deliberately disabled."
        )
    return value


def create_runtime_descriptor(
    directory: str | Path,
    *,
    port: int,
    pid: int | None = None,
    token: str | None = None,
) -> tuple[RuntimeDescriptor, Path]:
    """Write a mode-0600 descriptor without printing its bearer token."""

    target_dir = Path(directory).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        target_dir.chmod(0o700)
    except OSError:
        pass
    descriptor = RuntimeDescriptor(
        host=LOOPBACK_HOST,
        port=max(1, min(65_535, int(port))),
        token=str(token or secrets.token_urlsafe(32)),
        pid=int(pid if pid is not None else os.getpid()),
        started_at=utc_now_text(),
    )
    path = target_dir / "local-service.json"
    temporary = target_dir / f".{path.name}.{descriptor.pid}.tmp"
    temporary.write_text(
        json.dumps(descriptor.to_dict(), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return descriptor, path


def load_runtime_descriptor(path: str | Path) -> RuntimeDescriptor:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    descriptor = RuntimeDescriptor(
        host=validate_loopback_host(str(raw.get("host") or "")),
        port=int(raw.get("port") or 0),
        token=str(raw.get("token") or ""),
        pid=int(raw.get("pid") or 0),
        started_at=str(raw.get("started_at") or ""),
        api_version=str(raw.get("api_version") or "v1"),
    )
    if not descriptor.token or descriptor.port <= 0:
        raise ValueError("Hybrid runtime descriptor is incomplete.")
    return descriptor


def token_matches(candidate: str, expected: str) -> bool:
    return bool(candidate and expected) and hmac.compare_digest(
        str(candidate).encode("utf-8"), str(expected).encode("utf-8")
    )


def remove_runtime_descriptor(path: str | Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
