"""Loopback-service authentication and secret-safe utilities."""

from __future__ import annotations

import hmac
import os
from pathlib import Path
import secrets
import tempfile
from typing import Iterable


class ServiceSecurityError(RuntimeError):
    pass


def generate_service_token() -> str:
    return secrets.token_urlsafe(48)


def token_matches(candidate: str | None, expected: str) -> bool:
    raw = str(candidate or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    return bool(raw and expected) and hmac.compare_digest(raw, expected)


def assert_loopback_host(host: str) -> str:
    clean = str(host or "").strip().lower()
    if clean not in {"127.0.0.1", "localhost", "::1"}:
        raise ServiceSecurityError(
            "The desktop sidecar may only bind to a loopback address."
        )
    return clean


def write_private_token_file(path: str | os.PathLike[str], token: str) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(token))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def redact_text(text: object, secrets_to_redact: Iterable[str] = ()) -> str:
    output = " ".join(str(text or "").split())
    for secret in secrets_to_redact:
        clean = str(secret or "")
        if clean:
            output = output.replace(clean, "<redacted>")
    return output[:2_000]
