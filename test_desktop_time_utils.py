from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pytest

from desktop.trading_intelligence.time_utils import format_local_timestamp


def test_format_local_timestamp_converts_utc_to_local_display_and_handles_naive_values() -> None:
    displayed = format_local_timestamp("2026-09-04 18:11")
    utc = datetime.fromisoformat("2026-09-04 18:11").replace(tzinfo=timezone.utc)
    local = utc.astimezone()
    now_local = datetime.now(local.tzinfo)
    expected = f"{local.strftime('%b')} {local.day}, {local.strftime('%I:%M %p')}"
    if local.year != now_local.year:
        expected += f", {local.year}"
    assert displayed == expected


def test_format_local_timestamp_includes_year_when_timestamp_year_differs() -> None:
    previous_year = datetime.now().year - 1
    displayed = format_local_timestamp(f"{previous_year}-08-10 12:00:00")
    assert str(previous_year) in displayed


def test_format_local_timestamp_handles_malformed_input_with_safe_fallback() -> None:
    assert format_local_timestamp("not-a-real-timestamp") == "Unknown"


def test_format_local_timestamp_round_trips_non_ambiguous_json_timestamp() -> None:
    displayed = format_local_timestamp("2026-01-15T06:00:00+00:00")
    utc = datetime.fromisoformat("2026-01-15T06:00:00+00:00")
    local = utc.astimezone()
    expected = f"{local.strftime('%b')} {local.day}, {local.strftime('%I:%M %p')}"
    if local.year != datetime.now(local.tzinfo).year:
        expected += f", {local.year}"
    assert displayed == expected


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="tzset unavailable in this environment")
def test_format_local_timestamp_reflects_summer_winter_offset_shift() -> None:
    original_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "America/Los_Angeles"
        time.tzset()
        winter = format_local_timestamp("2026-01-15 12:00:00")
        summer = format_local_timestamp("2026-07-15 12:00:00")
        assert summer != winter
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()
