import pytest

from market_feature_validation import run_detector_event_study


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


def test_breakout_holding_event_is_not_emitted_before_second_hold_bar():
    rows = _breakout_session(29)
    report = run_detector_event_study(
        rows,
        detectors=["breakout_holding"],
        horizons=(1,),
        swing_radius=1,
    )
    events = report["events"]
    assert len(events) == 1
    event = events[0]
    assert event["detection_index"] == 5
    assert event["feature_value"] == "holding"
    assert report["causal_replay"] is True


def test_event_outcome_is_measured_after_detection_not_used_to_trigger_it():
    rows = _breakout_session(29)
    report = run_detector_event_study(
        rows,
        detectors=["breakout_holding"],
        horizons=(1,),
        swing_radius=1,
    )
    event = report["events"][0]
    outcome = event["outcomes"]
    expected = ((10.9 / 10.7) - 1.0) * 100.0
    assert outcome["entry_price"] == 10.7
    assert outcome["forward_returns_pct"]["1"] == pytest.approx(expected)
    assert outcome["directional_returns_pct"]["1"] == pytest.approx(expected)


def test_sessions_are_replayed_independently():
    rows = _breakout_session(28) + _breakout_session(29)
    report = run_detector_event_study(
        rows,
        detectors=["breakout_holding"],
        horizons=(1,),
        swing_radius=1,
    )
    assert report["sessions_analyzed"] == 2
    assert len(report["events"]) == 2
    assert {event["session"] for event in report["events"]} == {"2026-08-28", "2026-08-29"}
    assert report["summary"]["breakout_holding"]["event_count"] == 2


def test_bearish_detector_inverts_directional_return():
    rows = [
        _bar(29, 0, 10.0, 10.1, 9.9, 10.0, 100),
        _bar(29, 1, 10.0, 10.2, 9.8, 10.0, 100),
        _bar(29, 2, 10.0, 10.5, 10.0, 10.4, 120),
        _bar(29, 3, 10.3, 10.3, 9.9, 10.0, 90),
        _bar(29, 4, 10.1, 10.7, 10.2, 10.6, 220),
        _bar(29, 5, 10.6, 10.8, 10.2, 10.3, 180),
        _bar(29, 6, 10.3, 10.4, 9.9, 10.0, 170),
    ]
    report = run_detector_event_study(
        rows,
        detectors=["breakout_failed"],
        horizons=(1,),
        swing_radius=1,
    )
    event = report["events"][0]
    raw = event["outcomes"]["forward_returns_pct"]["1"]
    directional = event["outcomes"]["directional_returns_pct"]["1"]
    assert raw < 0
    assert directional == pytest.approx(-raw)


def test_unknown_detector_is_rejected():
    with pytest.raises(ValueError):
        run_detector_event_study(_breakout_session(29), detectors=["not_a_detector"])
