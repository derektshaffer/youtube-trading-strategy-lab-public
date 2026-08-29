import pytest

from market_features import build_market_features

from market_feature_validation import _ordered_sessions, build_supervised_feature_rows, run_detector_event_study


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


def test_detector_event_retains_full_point_in_time_feature_snapshot():
    report = run_detector_event_study(
        _breakout_session(29),
        detectors=["breakout_holding"],
        horizons=(1,),
        swing_radius=1,
    )
    event = report["events"][0]
    assert event["features"]["breakout_state"] == "holding"
    assert "vwap_hold_bars" in event["features"]


def test_supervised_rows_separate_feature_and_future_label_columns():
    rows = _breakout_session(29)
    report = build_supervised_feature_rows(
        rows,
        horizons=(1, 2),
        swing_radius=1,
        require_full_horizon=True,
    )
    assert report["causal_replay"] is True
    assert report["feature_calculation"] == "single_pass_causal_session_frame"
    assert report["row_count"] == 5
    assert all(name.startswith("feature__") for name in report["feature_columns"])
    assert all(name.startswith("label__") for name in report["label_columns"])
    first = report["records"][0]
    expected_1 = ((rows[1]["c"] / rows[0]["c"]) - 1.0) * 100.0
    expected_2 = ((rows[2]["c"] / rows[0]["c"]) - 1.0) * 100.0
    assert first["label__forward_return_1bar_pct"] == pytest.approx(expected_1)
    assert first["label__forward_return_2bar_pct"] == pytest.approx(expected_2)
    assert "feature__vwap_hold_bars" in first
    assert "label__max_favorable_excursion_2bar_pct" in first
    assert "label__max_adverse_excursion_2bar_pct" in first


def test_supervised_feature_values_are_unchanged_when_later_future_is_appended():
    base = _breakout_session(29)
    extended = base + [_bar(29, 7, 10.9, 11.2, 10.8, 11.1, 230)]
    left = build_supervised_feature_rows(
        base, horizons=(1,), swing_radius=1, require_full_horizon=True
    )["records"]
    right = build_supervised_feature_rows(
        extended, horizons=(1,), swing_radius=1, require_full_horizon=True
    )["records"]
    for index in range(len(left)):
        left_features = {k: v for k, v in left[index].items() if k.startswith("feature__")}
        right_features = {k: v for k, v in right[index].items() if k.startswith("feature__")}
        assert left_features == right_features


def test_session_grouping_uses_new_york_date_not_utc_midnight():
    rows = [
        {"t": "2026-08-30T00:00:00Z", "o": 10.0, "h": 10.1, "l": 9.9, "c": 10.0, "v": 100},
        {"t": "2026-08-30T00:30:00Z", "o": 10.0, "h": 10.1, "l": 9.9, "c": 10.0, "v": 100},
    ]
    sessions = _ordered_sessions(rows)
    assert len(sessions) == 1
    assert sessions[0][0] == "2026-08-29"


def test_single_pass_supervised_features_still_match_live_prefix_snapshot():
    rows = _breakout_session(29)
    report = build_supervised_feature_rows(
        rows,
        horizons=(1,),
        swing_radius=1,
        require_full_horizon=True,
    )
    for record in report["records"]:
        index = int(record["bar_index"])
        expected = build_market_features(rows[: index + 1], swing_radius=1)["features"]
        for name, value in expected.items():
            actual = record.get(f"feature__{name}")
            if value is None:
                assert actual is None
            elif isinstance(value, float):
                assert actual == pytest.approx(value)
            else:
                assert actual == value

def test_supervised_trade_quality_label_detects_target_before_stop():
    rows = [
        _bar(29, 0, 10.0, 10.02, 9.98, 10.0, 100),
        _bar(29, 1, 10.0, 10.15, 9.96, 10.12, 150),
    ]
    report = build_supervised_feature_rows(
        rows,
        horizons=(1,),
        swing_radius=1,
        require_full_horizon=True,
        profit_target_pct=1.0,
        stop_loss_pct=0.75,
    )
    first = report["records"][0]
    assert first["label__target_before_stop_1bar"] is True
    assert first["label__barrier_outcome_1bar"] == "target"
    assert first["label__max_favorable_excursion_1bar_pct"] >= 1.0
    assert report["barrier_same_bar_policy"] == "stop_first_conservative"


def test_supervised_trade_quality_label_counts_stop_as_failure():
    rows = [
        _bar(29, 0, 10.0, 10.02, 9.98, 10.0, 100),
        _bar(29, 1, 10.0, 10.05, 9.90, 9.94, 150),
    ]
    report = build_supervised_feature_rows(
        rows,
        horizons=(1,),
        swing_radius=1,
        require_full_horizon=True,
        profit_target_pct=1.0,
        stop_loss_pct=0.75,
    )
    first = report["records"][0]
    assert first["label__target_before_stop_1bar"] is False
    assert first["label__barrier_outcome_1bar"] == "stop"


def test_same_bar_target_and_stop_is_scored_conservatively_as_stop():
    rows = [
        _bar(29, 0, 10.0, 10.02, 9.98, 10.0, 100),
        _bar(29, 1, 10.0, 10.20, 9.80, 10.05, 200),
    ]
    report = build_supervised_feature_rows(
        rows,
        horizons=(1,),
        swing_radius=1,
        require_full_horizon=True,
        profit_target_pct=1.0,
        stop_loss_pct=0.75,
    )
    first = report["records"][0]
    assert first["label__target_before_stop_1bar"] is False
    assert first["label__barrier_outcome_1bar"] == "stop"



def test_supervised_rows_can_sample_observations_without_changing_causal_features():
    rows = _breakout_session(29)
    full = build_supervised_feature_rows(
        rows,
        horizons=(1,),
        swing_radius=1,
        require_full_horizon=True,
        observation_stride_bars=1,
    )
    sampled = build_supervised_feature_rows(
        rows,
        horizons=(1,),
        swing_radius=1,
        require_full_horizon=True,
        observation_stride_bars=2,
    )
    assert sampled["observation_stride_bars"] == 2
    assert sampled["row_count"] < full["row_count"]
    full_by_index = {int(row["bar_index"]): row for row in full["records"]}
    for row in sampled["records"]:
        index = int(row["bar_index"])
        assert index % 2 == 0
        for key, value in row.items():
            if key.startswith("feature__"):
                assert value == full_by_index[index][key]
