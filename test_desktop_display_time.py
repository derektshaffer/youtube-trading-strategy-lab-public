import io
from urllib.error import HTTPError
import pytest
from zoneinfo import ZoneInfo
from desktop.trading_intelligence.display_time import format_timestamp


@pytest.mark.parametrize("value, expected", [
    ("2026-09-04T11:40:53.938236Z", "Sep 4, 2026 4:40 AM PDT"),
    ("2026-09-04T01:23:43.133581+00:00", "Sep 3, 2026 6:23 PM PDT"),
    ("2026-09-04T04:40:53-07:00", "Sep 4, 2026 4:40 AM PDT"),
    ("2026-09-04 11:40", "2026-09-04 11:40 (timezone unknown)"),
    ("2026-09-04", "2026-09-04"), (None, "—"), ("Never", "Never"),
])
def test_readable_display(value, expected):
    assert format_timestamp(value, display_timezone=ZoneInfo("America/Los_Angeles")) == expected


def test_http_error_displays_detail_not_json():
    import pytest
    pytest.importorskip("PySide6")
    from desktop.trading_intelligence.window import clean_error
    error = HTTPError("http://localhost", 409, "Conflict", {}, io.BytesIO(
        b'{"detail":"Cloud sync is busy. Your request is saved; retry shortly."}'))
    assert clean_error(error) == "Cloud sync is busy. Your request is saved; retry shortly."
