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
    def __init__(self, rows_by_symbol, split_actions=None):
        self.rows_by_symbol = rows_by_symbol
        self._split_actions = list(split_actions or [])
        self.calls = []

    def bars(self, symbols, *, start, end, timeframe, adjustment="split", max_pages):
        self.calls.append(
            {
                "symbols": list(symbols),
                "start": start,
                "end": end,
                "timeframe": timeframe,
                "adjustment": adjustment,
                "max_pages": max_pages,
            }
        )
        return {symbol: list(self.rows_by_symbol.get(symbol) or []) for symbol in symbols}

    def research_reset_actions(self, symbols, **kwargs):
        allowed = set(symbols)
        return [
            dict(item)
            for item in self._split_actions
            if str(item.get("symbol") or "").upper() in allowed
        ]


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
    assert market.calls[0]["adjustment"] == "raw"
    assert report["market_data_integrity_contract"] == "split_safe_raw_v1"
    assert report["symbols_with_data"] == 2
    assert report["summary"]["breakout_holding"]["event_count"] == 2
    assert report["summary"]["breakout_holding"]["symbols_with_events"] == 2
    assert report["summary"]["breakout_holding"]["unique_market_days"] == 2
    assert report["summary"]["breakout_holding"]["max_symbol_event_share_pct"] == 50.0
    assert report["summary"]["breakout_holding"]["sample_quality"] == "SPARSE"


def test_scorecard_restarts_at_latest_split_boundary():
    market = FakeMarket(
        {
            "AAA": _breakout_session(18) + _breakout_session(20),
        },
        split_actions=[
            {
                "symbol": "AAA",
                "ex_date": "2026-08-20",
                "action_type": "forward_split",
            }
        ],
    )
    report = run_detector_scorecards(
        market,
        ["AAA"],
        start=datetime(2026, 8, 18, tzinfo=timezone.utc),
        end=datetime(2026, 8, 21, tzinfo=timezone.utc),
        detectors=["breakout_holding"],
        horizons=(1,),
        swing_radius=1,
    )
    integrity = report["market_data_integrity_by_symbol"]["AAA"]
    assert integrity["split_detected"] is True
    assert integrity["latest_split_date"] == "2026-08-20"
    assert integrity["discarded_pre_split_rows"] == len(_breakout_session(18))
    assert report["by_symbol"][0]["market_sessions"] == ["2026-08-20"]


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


def test_session_limit_keeps_most_recent_actual_market_dates_only():
    market = FakeMarket(
        {
            "AAA": (
                _breakout_session(24)
                + _breakout_session(25)
                + _breakout_session(28)
            )
        }
    )
    report = run_detector_scorecards(
        market,
        ["AAA"],
        start=datetime(2026, 8, 20, tzinfo=timezone.utc),
        end=datetime(2026, 8, 29, tzinfo=timezone.utc),
        detectors=["breakout_holding"],
        horizons=(1,),
        swing_radius=1,
        session_limit=2,
    )

    by_symbol = report["by_symbol"][0]
    assert by_symbol["sessions"] == 2
    assert by_symbol["market_sessions"] == ["2026-08-25", "2026-08-28"]
    assert report["market_sessions_requested"] == 2
    assert report["market_sessions_observed"] == 2
    assert report["market_session_dates"] == ["2026-08-25", "2026-08-28"]


def test_session_limit_does_not_count_missing_weekend_dates():
    market = FakeMarket(
        {
            "AAA": _breakout_session(28),
        }
    )
    report = run_detector_scorecards(
        market,
        ["AAA"],
        start=datetime(2026, 8, 28, tzinfo=timezone.utc),
        end=datetime(2026, 8, 31, tzinfo=timezone.utc),
        detectors=["breakout_holding"],
        horizons=(1,),
        swing_radius=1,
        session_limit=3,
    )

    assert report["market_sessions_requested"] == 3
    assert report["market_sessions_observed"] == 1
    assert report["market_session_dates"] == ["2026-08-28"]
