"""DST and field provenance remain explicit in the scoped read-only view."""
from datetime import datetime, timezone
import time
from zoneinfo import ZoneInfo
import pytest
from desktop.trading_intelligence.display_time import format_timestamp

LA=ZoneInfo("America/Los_Angeles")


@pytest.mark.parametrize("stamp,expected",[
 ("2026-09-04T19:55:00Z","Sep 4, 2026 12:55 PM PDT"),
 ("2026-01-04T19:55:00Z","Jan 4, 2026 11:55 AM PST"),
 ("2026-09-06T01:37:29Z","Sep 5, 2026 6:37 PM PDT"),
 ("2026-09-05T23:03:56Z","Sep 5, 2026 4:03 PM PDT"),
 ("2026-03-08T09:59:00Z","Mar 8, 2026 1:59 AM PST"),
 ("2026-03-08T10:00:00Z","Mar 8, 2026 3:00 AM PDT"),
 ("2026-11-01T08:30:00Z","Nov 1, 2026 1:30 AM PDT"),
 ("2026-11-01T09:30:00Z","Nov 1, 2026 1:30 AM PST"),
])
def test_local_zone_and_dst(stamp,expected,monkeypatch):
    assert format_timestamp(stamp,display_timezone=LA)==expected
    if hasattr(time,"tzset"):
        with monkeypatch.context() as env:
            env.setenv("TZ","America/Los_Angeles");time.tzset()
            try:assert format_timestamp(stamp)==expected
            finally:pass
        time.tzset()


def test_date_naive_and_never_contract():
    assert format_timestamp("2026-09-04",naive_utc=True,display_timezone=LA)=="2026-09-04"
    assert format_timestamp("Never")=="Never"
    assert format_timestamp("2026-09-04 11:40",display_timezone=LA)=="2026-09-04 11:40 (timezone unknown)"
    assert format_timestamp("2026-09-04 11:40",naive_utc=True,display_timezone=LA)=="Sep 4, 2026 4:40 AM PDT"
    assert format_timestamp(datetime(2026,1,4,19,55,tzinfo=timezone.utc),display_timezone=LA,clock_only=True)=="11:55 PST"


def test_native_analysis_and_saved_times(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from desktop.trading_intelligence.analysis_page import AnalysisPage
    from desktop.trading_intelligence.saved_validation_page import SavedValidationPage
    app=QApplication.instance() or QApplication([])
    analysis=AnalysisPage();analysis.symbol.setText("SPY")
    analysis.render_analysis({"symbol":"SPY","summary":{"as_of":"2026-09-04T19:55:00Z"}})
    assert format_timestamp("2026-09-04T19:55:00Z") in analysis.detail.text()
    context={"symbol":"SPY","metrics":{"trade_timestamp":"2026-09-04T19:59:59Z"}}
    analysis.set_discovery_context(context)
    assert format_timestamp("2026-09-04T19:59:59Z") in analysis.signal_summary.text()
    for status,stamp in (("complete","2026-09-06T01:37:29Z"),("failed","2026-09-05T23:04:34Z")):
        page=SavedValidationPage({"job_id":"saved","run_id":"run","ticker":"SPY","strategy_ids":["exact"],
                                  "status":status,"updated_at":stamp,"result":{},"error":{}})
        assert format_timestamp(stamp) in page.timestamp.text()
