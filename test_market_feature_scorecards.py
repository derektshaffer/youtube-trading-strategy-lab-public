from datetime import datetime, timezone

from market_feature_scorecards import run_detector_scorecards


def _bar(day, minute, o, h, l, c, v=100):
    return {
        "t": f"2026-08-{day:02d}T13:{minute:02d}:00Z",
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "v": v,
    }


def _breakout_session(day):
    return [
        _bar(day, 0, 10.0, 10.1, 9.9, 10.0, 100),
        _bar(day, 1, 10.0, 10.2, 9.8, 10.0, 100),
        _bar(day, 2, 10.0, 10.5, 10.0, 10.4, 120),
        _bar(day, 3, 10.3, 10.3, 9.9, 10.0, 90),
        _bar(day, 4, 10.1, 10.7, 10.2, 10.6, 220),
        _bar(day, 5, 10.6, 10.8, 10.5, 10.7, 240),
        _bar(day, 6, 10.7, 11.0, 10.7, 10.9, 200),
    ]


class FakeMarket:
    def __init__(self, rows_by_symbol):
        self.rows_by_symbol = rows_by_symbol
        self.calls = []

    def bars(self, symbols, *, start, end, timeframe, max_pages):
        self.calls.append(
            {
                "symbols": list(symbols),
                "start": start,
                "end": end,
                "timeframe": timeframe,
                "max_pages": max_pages,
            }
        )
        return {symbol: list(self.rows_by_symbol.get(symbol) or []) for symbol in symbols}


def test_scorecard_reuses_one_batched_history_request():
    market = FakeMarket(
        {
            "AAA": _breakout_session(28),
            "BBB": _breakout_session(29),
        }
    )
    report = run_detector_scorecards(
        market,
        ["AAA", "BBB"],
        start=datetime(2026, 8, 28, tzinfo=timezone.utc),
        end=datetime(2026, 8, 30, tzinfo=timezone.utc),
        detectors=["breakout_holding"],
        horizons=(1,),
        swing_radius=1,
    )
    assert len(market.calls) == 1
    assert market.calls[0]["symbols"] == ["AAA", "BBB"]
    assert report["symbols_with_data"] == 2
    assert report["summary"]["breakout_holding"]["event_count"] == 2
    assert report["summary"]["breakout_holding"]["symbols_with_events"] == 2
    assert report["summary"]["breakout_holding"]["unique_market_days"] == 2
    assert report["summary"]["breakout_holding"]["max_symbol_event_share_pct"] == 50.0
    assert report["summary"]["breakout_holding"]["sample_quality"] == "SPARSE"


def test_scorecard_keeps_symbol_level_event_counts():
    market = FakeMarket(
        {
            "AAA": _breakout_session(28),
            "BBB": [],
        }
    )
    report = run_detector_scorecards(
        market,
        ["AAA", "BBB"],
        start=datetime(2026, 8, 28, tzinfo=timezone.utc),
        end=datetime(2026, 8, 30, tzinfo=timezone.utc),
        detectors=["breakout_holding"],
        horizons=(1,),
        swing_radius=1,
    )
    by_symbol = {item["symbol"]: item for item in report["by_symbol"]}
    assert by_symbol["AAA"]["event_count"] == 1
    assert by_symbol["BBB"]["event_count"] == 0
    assert report["symbols_requested"] == 2
    assert report["symbols_with_data"] == 1


def test_empty_symbol_list_avoids_market_request():
    market = FakeMarket({})
    report = run_detector_scorecards(
        market,
        [],
        start=datetime(2026, 8, 28, tzinfo=timezone.utc),
        end=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )
    assert market.calls == []
    assert report["symbols_requested"] == 0
    assert report["summary"] == {}
