"""Shared access gate with a short-lived remembered-browser token.

The password itself stays server-side. After a correct login, the app places a
signed, expiring token in the page URL so a Streamlit session reset does not
force another password entry for the next 12 hours.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any


ACCESS_PASSWORD_SETTING = "APP_ACCESS_PASSWORD"
ACCESS_SESSION_KEY = "_trading_app_access_granted"
ACCESS_TOKEN_PARAM = "_access"
ACCESS_TOKEN_VERSION = "v1"
ACCESS_TOKEN_TTL_SECONDS = 12 * 60 * 60


def configured_access_password(streamlit: Any) -> str:
    """Return the configured access password without exposing it to the UI."""
    try:
        value = streamlit.secrets.get(ACCESS_PASSWORD_SETTING, "")
        if str(value).strip():
            return str(value)
    except (FileNotFoundError, KeyError, RuntimeError, AttributeError, TypeError):
        pass
    return str(os.environ.get(ACCESS_PASSWORD_SETTING, ""))


def access_password_matches(candidate: Any, expected: Any) -> bool:
    """Compare non-empty passwords in constant time."""
    candidate_text = str(candidate or "")
    expected_text = str(expected or "")
    return bool(candidate_text and expected_text) and hmac.compare_digest(
        candidate_text.encode("utf-8"),
        expected_text.encode("utf-8"),
    )


def _token_signature(expected: str, expires_at: int) -> str:
    material = f"{ACCESS_TOKEN_VERSION}:{int(expires_at)}".encode("utf-8")
    key = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.new(key, material, hashlib.sha256).hexdigest()


def issue_access_token(expected: str, *, now: int | None = None) -> str:
    current = int(time.time() if now is None else now)
    expires_at = current + ACCESS_TOKEN_TTL_SECONDS
    return (
        f"{ACCESS_TOKEN_VERSION}.{expires_at}."
        f"{_token_signature(expected, expires_at)}"
    )


def access_token_valid(token: Any, expected: str, *, now: int | None = None) -> bool:
    text = str(token or "").strip()
    if not text or not expected:
        return False
    try:
        version, raw_expiry, signature = text.split(".", 2)
        expires_at = int(raw_expiry)
    except (TypeError, ValueError):
        return False
    current = int(time.time() if now is None else now)
    if version != ACCESS_TOKEN_VERSION or expires_at <= current:
        return False
    # Reject implausibly long-lived tokens even if they somehow carry a valid signature.
    if expires_at > current + ACCESS_TOKEN_TTL_SECONDS + 300:
        return False
    return hmac.compare_digest(
        signature,
        _token_signature(expected, expires_at),
    )


def _query_value(streamlit: Any, name: str) -> str:
    try:
        value = streamlit.query_params.get(name, "")
    except (AttributeError, KeyError, RuntimeError, TypeError):
        return ""
    if isinstance(value, list):
        value = value[-1] if value else ""
    return str(value or "")


def _remember_access(streamlit: Any, expected: str) -> None:
    streamlit.session_state[ACCESS_SESSION_KEY] = True
    try:
        existing = _query_value(streamlit, ACCESS_TOKEN_PARAM)
        if not access_token_valid(existing, expected):
            streamlit.query_params[ACCESS_TOKEN_PARAM] = issue_access_token(expected)
    except (AttributeError, KeyError, RuntimeError, TypeError):
        # Session access still works if a hosting environment does not expose query params.
        pass


def require_app_access(streamlit: Any) -> None:
    """Require the app password at most about twice per day on the same URL/browser."""
    expected = configured_access_password(streamlit)
    if not expected:
        streamlit.error(
            "This trading app is locked because APP_ACCESS_PASSWORD is not configured. "
            "Add a strong, unique value in Streamlit Secrets before using the app."
        )
        streamlit.stop()

    if bool(streamlit.session_state.get(ACCESS_SESSION_KEY)):
        _remember_access(streamlit, expected)
        return

    remembered = _query_value(streamlit, ACCESS_TOKEN_PARAM)
    if access_token_valid(remembered, expected):
        _remember_access(streamlit, expected)
        return

    streamlit.markdown("## Private trading app")
    streamlit.caption(
        "Enter the app password. This browser link will stay unlocked for about 12 hours."
    )
    with streamlit.form("trading_app_access_form"):
        candidate = streamlit.text_input("App password", type="password")
        submitted = streamlit.form_submit_button(
            "Unlock for 12 hours",
            type="primary",
            width="stretch",
        )
    if submitted and access_password_matches(candidate, expected):
        _remember_access(streamlit, expected)
        streamlit.rerun()
    if submitted:
        streamlit.error("Incorrect app password.")
    streamlit.stop()
