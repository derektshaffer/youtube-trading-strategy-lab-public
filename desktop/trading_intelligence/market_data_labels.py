"""Timestamp-based labels: receipt time and feed names never imply live data."""
from datetime import datetime, timezone
from .display_time import format_timestamp


def timestamp_label(stamp, *, now=None):
    try:
        parsed = datetime.fromisoformat(str(stamp or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age = ((now or datetime.now(timezone.utc)) - parsed).total_seconds()
        if age < 0:
            return "invalid future timestamp; freshness unknown"
    except (TypeError, ValueError, OverflowError):
        return "timestamp not recorded; data age unknown"
    return f"as of {format_timestamp(stamp, 'unknown', naive_utc=True)}; data age {age:,.0f}s"


def snapshot_label(metrics, *, now=None):
    # A quote timestamp is not evidence of when a fallback close was observed.
    stamp = metrics.get("price_timestamp") if "price_timestamp" in metrics else metrics.get("trade_timestamp")
    feed = str(metrics.get("feed") or "feed not recorded").upper()
    source = str(metrics.get("price_source") or "saved snapshot; price source not recorded")
    return f"Alpaca {feed} | {source} | {timestamp_label(stamp, now=now)} | not a live quote"
