import pytest

from market_detector_gate import (
    evaluate_detector_evidence,
    evaluate_scorecard_report,
    wilson_lower_bound,
)


def _strong_scorecard():
    return {
        "direction": 1,
        "event_count": 100,
        "symbols_with_events": 8,
        "unique_market_days": 10,
        "max_symbol_event_share_pct": 20.0,
        "avg_directional_max_favorable_excursion_pct": 3.2,
        "avg_directional_max_adverse_excursion_pct": -1.4,
        "horizons": {
            "5": {
                "avg_directional_return_pct": 0.35,
                "directional_samples": 100,
                "directional_hits": 62,
            },
            "15": {
                "avg_directional_return_pct": 0.70,
                "directional_samples": 100,
                "directional_hits": 65,
            },
            "30": {
                "avg_directional_return_pct": 0.90,
                "directional_samples": 95,
                "directional_hits": 61,
            },
        },
    }


def test_wilson_lower_bound_is_conservative():
    lower = wilson_lower_bound(65, 100)
    assert lower is not None
    assert 0.55 < lower < 0.57


def test_neutral_detector_remains_descriptive_only():
    gate = evaluate_detector_evidence("bounce_2_complete", _strong_scorecard())
    assert gate["status"] == "DESCRIPTIVE_ONLY"
    assert gate["eligible_for_scoring"] is False


def test_sparse_directional_detector_cannot_be_promoted():
    scorecard = _strong_scorecard()
    scorecard["event_count"] = 12
    scorecard["symbols_with_events"] = 2
    scorecard["unique_market_days"] = 3
    gate = evaluate_detector_evidence("breakout_holding", scorecard)
    assert gate["status"] == "INSUFFICIENT_EVIDENCE"
    assert gate["eligible_for_scoring"] is False
    assert any("at least 50 events" in reason for reason in gate["reasons"])


def test_broad_consistent_detector_can_become_scoring_candidate():
    gate = evaluate_detector_evidence("breakout_holding", _strong_scorecard())
    assert gate["status"] == "CANDIDATE_FOR_SCORING"
    assert gate["eligible_for_scoring"] is True
    assert gate["confidence_lower_bound"] > 0.50


def test_broad_but_unreliable_detector_is_mixed_not_eligible():
    scorecard = _strong_scorecard()
    scorecard["horizons"]["15"]["directional_hits"] = 52
    scorecard["horizons"]["15"]["avg_directional_return_pct"] = -0.05
    gate = evaluate_detector_evidence("breakout_holding", scorecard)
    assert gate["status"] == "MIXED_EVIDENCE"
    assert gate["eligible_for_scoring"] is False


def test_report_lists_only_detectors_that_pass_gate():
    report = {
        "summary": {
            "breakout_holding": _strong_scorecard(),
            "bounce_2_complete": _strong_scorecard(),
        }
    }
    result = evaluate_scorecard_report(report)
    assert result["eligible_detectors"] == ["breakout_holding"]
