"""Readable UTC display only; persisted timestamps retain full precision."""
from datetime import datetime, timezone


def format_timestamp(value, fallback="—"):
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        if len(text) == 10:
            return text
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.strftime("%Y-%m-%d %H:%M")
    except (ValueError, OverflowError):
        return text
