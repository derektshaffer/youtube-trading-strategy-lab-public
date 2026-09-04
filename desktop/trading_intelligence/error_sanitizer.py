"""Error string sanitization for desktop UI surfaces."""

from __future__ import annotations

import html
import json
import logging
import re
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse


_LOG = logging.getLogger(__name__)
_ERROR_TEXT_LIMIT = 1_000
_AUTH_STATUS_RE = re.compile(r"\b(?:401|unauthorized|forbidden|authentication failed|authentication error|auth failed|invalid credentials)\b", re.I)

_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
_HTML_TAG_RE = re.compile(r"(?s)<[^>]*>")
_SECRET_RE = re.compile(
    r"(?i)\b(token|api[_-]?key|secret[_-]?key|access[_-]?token|authorization)\s*[:=]\s*[\w\-._~+/=%]+"
)
_BEARER_RE = re.compile(r"(?i)bearer\s+[\w\-._~+/=%]+")


def _redact_sensitive_text(raw: str) -> str:
    text = _SECRET_RE.sub(r"\1=[redacted]", raw)
    return _BEARER_RE.sub("bearer [redacted]", text)


def _to_plain_text(raw: str) -> str:
    """Return compact, human-readable text from HTML/JSON payloads."""

    sanitized = _SCRIPT_STYLE_RE.sub(" ", raw)
    sanitized = _HTML_TAG_RE.sub(" ", sanitized)
    sanitized = html.unescape(sanitized)
    sanitized = _redact_sensitive_text(str(sanitized))
    return " ".join(str(sanitized).split())


def sanitize_display_text(value: Any) -> str:
    """Compact sanitized text for non-error UI fields."""

    return _to_plain_text(str(value or ""))


def _read_http_error_body(exc: HTTPError) -> str:
    try:
        body = exc.read()
    except (OSError, ValueError):
        return ""
    if not body:
        return ""
    if isinstance(body, bytes):
        try:
            return body.decode("utf-8", errors="replace")
        except (UnicodeDecodeError, LookupError):
            return ""
    return str(body)


def _extract_json_message(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        for item in payload:
            message = _extract_json_message(item)
            if message:
                return message
        return ""
    if not isinstance(payload, dict):
        return ""

    for key in ("detail", "message", "description", "error", "reason"):
        value = payload.get(key)
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    for value in payload.values():
        message = _extract_json_message(value)
        if message:
            return message
    return ""


def _extract_http_error_message(body: str) -> str:
    text = body.strip()
    if not text:
        return ""
    if text.startswith("{") or text.startswith("["):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return _to_plain_text(text)
        message = _extract_json_message(decoded)
        if message:
            return _to_plain_text(message)
        return _to_plain_text(text)
    return _to_plain_text(text)


def _provider_hint(exc: HTTPError) -> str | None:
    url = str(exc.url or "").lower()
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    providers = (
        ("api.github.com", "GitHub"),
        ("github.com", "GitHub"),
        ("raw.githubusercontent.com", "GitHub"),
        ("tradier", "Tradier"),
        ("alpaca", "Alpaca"),
    )
    for token, name in providers:
        if token in host:
            return name
    return None


def _provider_hint_from_text(value: Any) -> str | None:
    lowered = str(value or "").lower()
    providers = (
        ("api.github.com", "GitHub"),
        ("github.com", "GitHub"),
        ("raw.githubusercontent.com", "GitHub"),
        ("tradier", "Tradier"),
        ("alpaca", "Alpaca"),
    )
    for token, name in providers:
        if token in lowered:
            return name
    return None


def _looks_like_missing_credentials(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in (
            "missing",
            "not provided",
            "no api key",
            "no token",
            "empty",
            "requires authentication",
            "authentication missing",
        )
    )


def _looks_like_rejected_credentials(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in (
            "invalid",
            "rejected",
            "revok",
            "expired",
            "forbidden",
            "bad credentials",
            "incorrect",
            "unauthorized",
        )
    )


def _is_local_service_url(url: str) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host in {"127.0.0.1", "::1", "localhost"}


def _looks_like_http_auth_failure(message: str) -> bool:
    return bool(_AUTH_STATUS_RE.search(message))


def _sanitized_http_target(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if not parsed.scheme and not parsed.netloc:
        return str(url or "")
    return parsed._replace(query="", fragment="").geturl()


def _auth_failure_message(exc: HTTPError, message: str) -> str:
    provider = _provider_hint(exc)
    if provider:
        prefix = f"{provider} authentication failed."
        if _looks_like_missing_credentials(message):
            detail = f"{prefix} Credentials are missing or not configured."
        elif _looks_like_rejected_credentials(message):
            detail = f"{prefix} Credentials were rejected."
        else:
            detail = f"{prefix} Access is currently denied."
        return f"{detail} Check the connection credentials in Data Connections."

    if _is_local_service_url(str(exc.url or "")):
        return "Local service authentication failed. Restart the desktop app and try again."
    if _looks_like_missing_credentials(message):
        return "Authentication failed. Connection credentials may be missing. Check Data Connections."
    if _looks_like_rejected_credentials(message):
        return "Authentication failed. Credentials were rejected. Check Data Connections."
    return "Authentication failed. Check the connection credentials in Data Connections."


def _auth_failure_message_from_text(message: str) -> str:
    provider = _provider_hint_from_text(message)
    if provider:
        prefix = f"{provider} authentication failed."
        if _looks_like_missing_credentials(message):
            detail = f"{prefix} Credentials are missing or not configured."
        elif _looks_like_rejected_credentials(message):
            detail = f"{prefix} Credentials were rejected."
        else:
            detail = f"{prefix} Access is currently denied."
        return f"{detail} Check the connection credentials in Data Connections."
    if _looks_like_missing_credentials(message):
        return "Authentication failed. Connection credentials may be missing. Check Data Connections."
    if _looks_like_rejected_credentials(message):
        return "Authentication failed. Credentials were rejected. Check Data Connections."
    return "Authentication failed. Check the connection credentials in Data Connections."


def _http_error_message(exc: HTTPError, body: str) -> str:
    message = _extract_http_error_message(body)
    if not message:
        endpoint = _sanitized_http_target(str(exc.url or ""))
        message = f"HTTP {exc.code} response from {endpoint}" if endpoint else f"HTTP {exc.code}"
    text = " ".join(str(message).split())
    if text:
        if exc.code in {401, 403}:
            return _auth_failure_message(exc, text)
        return text
    return f"Request failed with HTTP {exc.code}."


def clean_error(exc: BaseException) -> str:
    """Convert exception details to a user-safe UI message."""

    if isinstance(exc, HTTPError):
        body = _read_http_error_body(exc)
        message = _http_error_message(exc, body)
        _LOG.warning(
            "HTTP error from %s (provider=%s, status=%s): %s",
            _sanitized_http_target(str(exc.url or "")),
            _provider_hint(exc) or "unknown",
            exc.code,
            message,
        )
        return message[:_ERROR_TEXT_LIMIT]
    message = _to_plain_text(str(exc))
    if _looks_like_http_auth_failure(message):
        message = _auth_failure_message_from_text(message)
    _LOG.warning("Request failed: %s", message)
    return message[:_ERROR_TEXT_LIMIT]
