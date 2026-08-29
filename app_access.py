"""Shared compatibility hook for Streamlit entrypoints.

The Trading Lab no longer requires a separate in-app password. The hook remains
so every page can keep the same startup structure without maintaining duplicate
access logic.
"""

from __future__ import annotations

from typing import Any


def require_app_access(streamlit: Any) -> None:
    """Allow the app to render immediately without an app-level password gate."""
    return None
