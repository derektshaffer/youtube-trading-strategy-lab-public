import io
from urllib.error import HTTPError
import pytest
from desktop.trading_intelligence.display_time import format_timestamp


@pytest.mark.parametrize("value, expected", [
    ("2026-09-04T11:40:53.938236Z", "2026-09-04 11:40"),
    ("2026-09-04T01:23:43.133581+00:00", "2026-09-04 01:23"),
    ("2026-09-04T04:40:53-07:00", "2026-09-04 11:40"),
    ("2026-09-04 11:40", "2026-09-04 11:40"),
    ("2026-09-04", "2026-09-04"), (None, "—"), ("Never", "Never"),
])
def test_readable_display(value, expected):
    assert format_timestamp(value) == expected


def test_http_error_displays_detail_not_json():
    import pytest
    pytest.importorskip("PySide6")
    from desktop.trading_intelligence.window import clean_error
    error = HTTPError("http://localhost", 409, "Conflict", {}, io.BytesIO(
        b'{"detail":"Cloud sync is busy. Your request is saved; retry shortly."}'))
    assert clean_error(error) == "Cloud sync is busy. Your request is saved; retry shortly."
