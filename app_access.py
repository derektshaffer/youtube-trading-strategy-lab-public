"""Shared access gate for every Streamlit entrypoint.

The app stores brokerage, AI, and GitHub credentials on the server, so access
must fail closed unless the owner configured a separate application password.
"""

from __future__ import annotations

import hmac
import os
from typing import Any


ACCESS_PASSWORD_SETTING = "APP_ACCESS_PASSWORD"
ACCESS_SESSION_KEY = "_trading_app_access_granted"


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


def require_app_access(streamlit: Any) -> None:
    """Stop the Streamlit run until the owner authenticates this session."""
    expected = configured_access_password(streamlit)
    if not expected:
        streamlit.error(
            "This trading app is locked because APP_ACCESS_PASSWORD is not configured. "
            "Add a strong, unique value in Streamlit Secrets before using the app."
        )
        streamlit.stop()

    if bool(streamlit.session_state.get(ACCESS_SESSION_KEY)):
        with streamlit.sidebar:
            if streamlit.button("Lock app", key="lock_trading_app_session", width="stretch"):
                streamlit.session_state.pop(ACCESS_SESSION_KEY, None)
                streamlit.rerun()
        return

    streamlit.markdown("## Private trading app")
    streamlit.caption("Enter the separate app-access password configured by the owner.")
    with streamlit.form("trading_app_access_form"):
        candidate = streamlit.text_input("App password", type="password")
        submitted = streamlit.form_submit_button("Unlock", type="primary", width="stretch")
    if submitted and access_password_matches(candidate, expected):
        streamlit.session_state[ACCESS_SESSION_KEY] = True
        streamlit.rerun()
    if submitted:
        streamlit.error("Incorrect app password.")
    streamlit.stop()
