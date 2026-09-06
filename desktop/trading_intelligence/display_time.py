"""Human display only: aware instants become labeled, DST-aware local time.

Dates are not instants. Unknown naive values remain unshifted and explicitly
unqualified; opt into naive_utc only for fields with an established UTC contract.
No persisted/API value is changed by this helper.
"""

from datetime import date, datetime, timezone


def format_timestamp(value, fallback="—", *, naive_utc=False,
                     display_timezone=None, clock_only=False):
    text = str(value or "").strip()
    if not text:
        return fallback
    if text.lower() in {"—", "never", "unknown", "not yet"}:
        return text
    try:
        if len(text) == 10:
            return date.fromisoformat(text).isoformat()
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            if not naive_utc:
                return f"{text} (timezone unknown)"
            parsed = parsed.replace(tzinfo=timezone.utc)
        # Omitting a target uses the OS timezone for THIS instant, not today's
        # fixed UTC offset, so winter and summer retain their correct offsets.
        local = parsed.astimezone(display_timezone)
    except (TypeError, ValueError, OverflowError, OSError):
        return fallback
    zone = local.tzname() or "local time"
    if clock_only:
        return f"{local:%H:%M} {zone}"
    time_text = local.strftime("%I:%M %p").lstrip("0")
    return f"{local:%b} {local.day}, {local.year} {time_text} {zone}"
