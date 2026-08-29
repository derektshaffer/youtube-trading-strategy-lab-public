"""Conservative evidence gate for promoting market detectors toward live scoring."""

from __future__ import annotations

from math import sqrt
from typing import Any

from market_feature_validation import DETECTOR_SPECS


MIN_EVENTS = 50
MIN_SYMBOLS = 5
MIN_MARKET_DAYS = 8
MAX_SYMBOL_EVENT_SHARE_PCT = 45.0
PRIMARY_HORIZON = "15"
MIN_DIRECTIONAL_SAMPLES = 30
MIN_POSITIVE_HORIZONS = 2
MIN_WILSON_LOWER_BOUND = 0.50


def wilson_lower_bound(hits: int, samples: int, *, z: float = 1.96) -> float | None:
    """95% Wilson lower confidence bound for a binary hit rate."""
    samples = int(samples)
    hits = int(hits)
    if samples <= 0 or hits < 0 or hits > samples:
        return None
    phat = hits / samples
    z2 = z * z
    denominator = 1.0 + z2 / samples
    center = phat + z2 / (2.0 * samples)
    margin = z * sqrt((phat * (1.0 - phat) + z2 / (4.0 * samples)) / samples)
    return (center - margin) / denominator


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def evaluate_detector_evidence(
    detector: str,
    scorecard: dict[str, Any],
) -> dict[str, Any]:
    """Return a strict promotion status; never mutates live scoring by itself."""
    spec = DETECTOR_SPECS.get(detector) or {}
    direction = (
        int(spec["direction"])
        if "direction" in spec
        else int(scorecard.get("direction") or 0)
    )
    reasons: list[str] = []

    if direction == 0:
        return {
            "detector": detector,
            "status": "DESCRIPTIVE_ONLY",
            "eligible_for_scoring": False,
            "confidence_lower_bound": None,
            "reasons": [
                "This detector has no predefined bullish/bearish direction, so it remains descriptive evidence."
            ],
        }

    event_count = int(scorecard.get("event_count") or 0)
    symbol_count = int(scorecard.get("symbols_with_events") or 0)
    market_days = int(scorecard.get("unique_market_days") or 0)
    concentration = _number(scorecard.get("max_symbol_event_share_pct")) or 0.0

    if event_count < MIN_EVENTS:
        reasons.append(f"Needs at least {MIN_EVENTS} events; has {event_count}.")
    if symbol_count < MIN_SYMBOLS:
        reasons.append(f"Needs events across at least {MIN_SYMBOLS} stocks; has {symbol_count}.")
    if market_days < MIN_MARKET_DAYS:
        reasons.append(f"Needs events across at least {MIN_MARKET_DAYS} market days; has {market_days}.")
    if concentration > MAX_SYMBOL_EVENT_SHARE_PCT:
        reasons.append(
            f"One stock contributes {concentration:.0f}% of events; maximum allowed is "
            f"{MAX_SYMBOL_EVENT_SHARE_PCT:.0f}%."
        )

    horizons = scorecard.get("horizons") or {}
    primary = horizons.get(PRIMARY_HORIZON) or {}
    directional_samples = int(primary.get("directional_samples") or 0)
    directional_hits = int(primary.get("directional_hits") or 0)
    lower_bound = wilson_lower_bound(directional_hits, directional_samples)

    if directional_samples < MIN_DIRECTIONAL_SAMPLES:
        reasons.append(
            f"Needs at least {MIN_DIRECTIONAL_SAMPLES} directional {PRIMARY_HORIZON}-bar outcomes; "
            f"has {directional_samples}."
        )

    primary_return = _number(primary.get("avg_directional_return_pct"))
    if primary_return is None or primary_return <= 0:
        reasons.append(
            f"Average directional return at {PRIMARY_HORIZON} bars is not positive."
        )

    if lower_bound is None or lower_bound < MIN_WILSON_LOWER_BOUND:
        displayed = f"{lower_bound * 100.0:.1f}%" if lower_bound is not None else "unavailable"
        reasons.append(
            "The lower 95% confidence bound for directional hit rate is "
            f"{displayed}; it must be at least {MIN_WILSON_LOWER_BOUND * 100:.0f}%."
        )

    positive_horizons = 0
    for key in ("5", "15", "30"):
        value = _number((horizons.get(key) or {}).get("avg_directional_return_pct"))
        if value is not None and value > 0:
            positive_horizons += 1
    if positive_horizons < MIN_POSITIVE_HORIZONS:
        reasons.append(
            f"Directional return is positive on only {positive_horizons} of 3 horizons; "
            f"at least {MIN_POSITIVE_HORIZONS} are required."
        )

    directional_mfe = _number(scorecard.get("avg_directional_max_favorable_excursion_pct"))
    directional_mae = _number(scorecard.get("avg_directional_max_adverse_excursion_pct"))
    if (
        directional_mfe is None
        or directional_mae is None
        or directional_mfe <= abs(directional_mae)
    ):
        reasons.append(
            "Average directional favorable excursion does not exceed average adverse excursion."
        )

    if reasons:
        breadth_ok = (
            event_count >= MIN_EVENTS
            and symbol_count >= MIN_SYMBOLS
            and market_days >= MIN_MARKET_DAYS
            and concentration <= MAX_SYMBOL_EVENT_SHARE_PCT
        )
        status = "MIXED_EVIDENCE" if breadth_ok else "INSUFFICIENT_EVIDENCE"
        return {
            "detector": detector,
            "status": status,
            "eligible_for_scoring": False,
            "confidence_lower_bound": lower_bound,
            "positive_horizons": positive_horizons,
            "reasons": reasons,
        }

    return {
        "detector": detector,
        "status": "CANDIDATE_FOR_SCORING",
        "eligible_for_scoring": True,
        "confidence_lower_bound": lower_bound,
        "positive_horizons": positive_horizons,
        "reasons": [
            "Detector passed breadth, concentration, confidence, multi-horizon, and excursion checks."
        ],
    }


def evaluate_scorecard_report(report: dict[str, Any]) -> dict[str, Any]:
    gates = {
        detector: evaluate_detector_evidence(detector, item or {})
        for detector, item in (report.get("summary") or {}).items()
    }
    return {
        "detectors": gates,
        "eligible_detectors": [
            detector
            for detector, gate in gates.items()
            if bool(gate.get("eligible_for_scoring"))
        ],
        "note": (
            "Passing this gate only makes a detector a candidate for controlled scoring experiments. "
            "It does not automatically change live rankings or authorize trading."
        ),
    }
