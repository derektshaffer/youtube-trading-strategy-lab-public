"""Display utilities for desktop job timestamps."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _default_timestamp_fallback(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    return text


def parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)):
        try:
            result = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, TypeError, ValueError):
            return None
    else:
        text = _default_timestamp_fallback(value)
        if text is None:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


def _format_local_with_year(local: datetime, now_local: datetime) -> str:
    base = f"{local.strftime('%b')} {local.day}, {local.strftime('%I:%M %p')}"
    if local.year != now_local.year:
        return f"{base}, {local.year}"
    return base


def format_local_timestamp(value: Any, *, fallback: str = "Unknown") -> str:
    timestamp = parse_timestamp(value)
    if timestamp is None:
        return fallback
    local = timestamp.astimezone()
    now_local = datetime.now(local.tzinfo)
    return _format_local_with_year(local, now_local)


def format_local_timestamp_with_utc_tooltip(value: Any, *, fallback: str = "Unknown") -> tuple[str, str | None]:
    timestamp = parse_timestamp(value)
    if timestamp is None:
        return fallback, None
    local = timestamp.astimezone()
    now_local = datetime.now(local.tzinfo)
    text = _format_local_with_year(local, now_local)
    utc = timestamp.astimezone(timezone.utc)
    utc_display = utc.strftime("%Y-%m-%d %H:%M UTC")
    return text, utc_display

