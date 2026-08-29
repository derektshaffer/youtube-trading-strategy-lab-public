"""Causal historical validation for observational market-feature detectors.

This module is deliberately separate from strategy P/L backtesting. It replays
historical candles one bar at a time, records only detector transitions that
were knowable at that moment, then studies subsequent price behavior.

Future bars are used only for outcome measurement after a detector event has
already been recorded. Detector calculation itself always receives a historical
prefix ending at the event bar.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean, median
from typing import Any
from zoneinfo import ZoneInfo

import math
import pandas as pd

from market_features import build_market_features


DEFAULT_HORIZONS = (5, 15, 30)
MARKET_TZ = ZoneInfo("America/New_York")

DETECTOR_SPECS: dict[str, dict[str, Any]] = {
    "vwap_retest_held": {
        "label": "VWAP reclaim retest held",
        "feature": "vwap_retest_held",
        "equals": True,
        "direction": 1,
    },
    "vwap_retest_failed": {
        "label": "VWAP reclaim retest failed",
        "feature": "vwap_retest_failed",
        "equals": True,
        "direction": -1,
    },
    "breakout_holding": {
        "label": "Breakout holding above confirmed swing high",
        "feature": "breakout_state",
        "equals": "holding",
        "direction": 1,
    },
    "breakout_failed": {
        "label": "Breakout failed back below confirmed swing high",
        "feature": "failed_breakout_last_swing_high",
        "equals": True,
        "direction": -1,
    },
    "bounce_2_complete": {
        "label": "Second completed bounce",
        "feature": "bounce_2_present",
        "equals": True,
        "direction": 0,
    },
    "bounce_3_complete": {
        "label": "Third completed bounce",
        "feature": "bounce_3_present",
        "equals": True,
        "direction": 0,
    },
    "bounce_structural_weakening": {
        "label": "Successive bounces structurally weakening",
        "feature": "bounce_structural_weakening",
        "equals": True,
        "direction": -1,
    },
    "bounce_structural_strengthening": {
        "label": "Successive bounces structurally strengthening",
        "feature": "bounce_structural_strengthening",
        "equals": True,
        "direction": 1,
    },
    "stair_step_up": {
        "label": "Confirmed stair-step up structure",
        "feature": "stair_step_up",
        "equals": True,
        "direction": 1,
    },
    "consolidation_expansion_up": {
        "label": "Consolidation to upside expansion",
        "feature": "consolidation_then_expansion_up",
        "equals": True,
        "direction": 1,
    },
    "strong_pullback": {
        "label": "Strong confirmed pullback structure",
        "feature": "pullback_quality",
        "equals": "strong",
        "direction": 1,
    },
}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _value(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row.get(name)
    return None


def _timestamp(row: dict[str, Any]) -> Any:
    return _value(row, "t", "timestamp", "time")


def _close(row: dict[str, Any]) -> float | None:
    return _number(_value(row, "c", "close", "Close"))


def _high(row: dict[str, Any]) -> float | None:
    return _number(_value(row, "h", "high", "High"))


def _low(row: dict[str, Any]) -> float | None:
    return _number(_value(row, "l", "low", "Low"))


def _ordered_sessions(rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Split timestamped rows by U.S. equity session date; undated rows stay together."""
    valid = [dict(row) for row in rows or [] if isinstance(row, dict)]
    if not valid:
        return []

    parsed: list[tuple[pd.Timestamp | None, int, dict[str, Any]]] = []
    for original_index, row in enumerate(valid):
        stamp = pd.to_datetime(_timestamp(row), utc=True, errors="coerce")
        parsed.append((stamp if not pd.isna(stamp) else None, original_index, row))

    if all(stamp is not None for stamp, _, _ in parsed):
        parsed.sort(key=lambda item: item[0])
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        order: list[str] = []
        for stamp, _, row in parsed:
            assert stamp is not None
            key = stamp.tz_convert(MARKET_TZ).date().isoformat()
            if key not in grouped:
                order.append(key)
            grouped[key].append(row)
        return [(key, grouped[key]) for key in order]

    return [("session-0", [row for _, _, row in parsed])]


def _is_active(features: dict[str, Any], spec: dict[str, Any]) -> bool:
    return features.get(str(spec["feature"])) == spec.get("equals")


def _event_outcomes(
    rows: list[dict[str, Any]],
    index: int,
    *,
    horizons: tuple[int, ...],
    direction: int,
) -> dict[str, Any]:
    entry = _close(rows[index])
    outcomes: dict[str, Any] = {
        "entry_price": entry,
        "forward_returns_pct": {},
        "directional_returns_pct": {},
        "max_favorable_excursion_pct": None,
        "max_adverse_excursion_pct": None,
    }
    if entry is None or entry <= 0:
        return outcomes

    for horizon in horizons:
        target_index = index + horizon
        future_return = None
        if target_index < len(rows):
            target_close = _close(rows[target_index])
            if target_close is not None:
                future_return = ((target_close / entry) - 1.0) * 100.0
        outcomes["forward_returns_pct"][str(horizon)] = future_return
        outcomes["directional_returns_pct"][str(horizon)] = (
            future_return * direction
            if future_return is not None and direction in {-1, 1}
            else None
        )

    max_horizon = max(horizons, default=0)
    future_window = rows[index + 1 : min(len(rows), index + max_horizon + 1)]
    highs = [_high(row) for row in future_window]
    lows = [_low(row) for row in future_window]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    if highs:
        outcomes["max_favorable_excursion_pct"] = ((max(highs) / entry) - 1.0) * 100.0
    if lows:
        outcomes["max_adverse_excursion_pct"] = ((min(lows) / entry) - 1.0) * 100.0
    return outcomes


def _summarize_events(
    events: list[dict[str, Any]],
    *,
    horizons: tuple[int, ...],
) -> dict[str, Any]:
    by_detector: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_detector[str(event.get("detector") or "unknown")].append(event)

    summary: dict[str, Any] = {}
    for detector, detector_events in by_detector.items():
        spec = DETECTOR_SPECS.get(detector) or {}
        item: dict[str, Any] = {
            "label": spec.get("label") or detector,
            "direction": int(spec.get("direction") or 0),
            "event_count": len(detector_events),
            "horizons": {},
        }

        mfes = [
            _number((event.get("outcomes") or {}).get("max_favorable_excursion_pct"))
            for event in detector_events
        ]
        maes = [
            _number((event.get("outcomes") or {}).get("max_adverse_excursion_pct"))
            for event in detector_events
        ]
        mfes = [value for value in mfes if value is not None]
        maes = [value for value in maes if value is not None]
        item["avg_max_favorable_excursion_pct"] = mean(mfes) if mfes else None
        item["avg_max_adverse_excursion_pct"] = mean(maes) if maes else None

        for horizon in horizons:
            key = str(horizon)
            returns = [
                _number(((event.get("outcomes") or {}).get("forward_returns_pct") or {}).get(key))
                for event in detector_events
            ]
            returns = [value for value in returns if value is not None]
            directional = [
                _number(((event.get("outcomes") or {}).get("directional_returns_pct") or {}).get(key))
                for event in detector_events
            ]
            directional = [value for value in directional if value is not None]
            item["horizons"][key] = {
                "samples": len(returns),
                "avg_return_pct": mean(returns) if returns else None,
                "median_return_pct": median(returns) if returns else None,
                "positive_return_pct": (
                    sum(value > 0 for value in returns) / len(returns) * 100.0
                    if returns
                    else None
                ),
                "avg_directional_return_pct": mean(directional) if directional else None,
                "directional_hit_pct": (
                    sum(value > 0 for value in directional) / len(directional) * 100.0
                    if directional
                    else None
                ),
            }
        summary[detector] = item
    return summary



def summarize_detector_events(
    events: list[dict[str, Any]],
    *,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> dict[str, Any]:
    """Public aggregation helper for detector scorecards."""
    clean_horizons = tuple(sorted({max(1, int(value)) for value in horizons}))
    return _summarize_events(events, horizons=clean_horizons)


def build_supervised_feature_rows(
    rows: list[dict[str, Any]],
    *,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    swing_radius: int = 3,
    require_full_horizon: bool = True,
) -> dict[str, Any]:
    """Build leakage-safe supervised-learning rows from historical candles.

    Features are computed from a prefix ending at the observation bar. Labels are
    then measured strictly from later bars in the same session. The result is a
    flat, JSON-serializable table suitable for persistence or DataFrame/model use.
    """
    clean_horizons = tuple(sorted({max(1, int(value)) for value in horizons}))
    max_horizon = max(clean_horizons, default=0)
    sessions = _ordered_sessions(rows)
    records: list[dict[str, Any]] = []
    feature_names: set[str] = set()
    label_names: set[str] = set()

    for session_key, session_rows in sessions:
        for index in range(len(session_rows)):
            if require_full_horizon and index + max_horizon >= len(session_rows):
                continue
            prefix = session_rows[: index + 1]
            snapshot = build_market_features(prefix, swing_radius=swing_radius)
            features = dict(snapshot.get("features") or {})
            outcomes = _event_outcomes(
                session_rows,
                index,
                horizons=clean_horizons,
                direction=0,
            )
            record: dict[str, Any] = {
                "session": session_key,
                "bar_index": index,
                "timestamp": _timestamp(session_rows[index]),
            }
            for name, value in features.items():
                column = f"feature__{name}"
                record[column] = value
                feature_names.add(column)

            forward = outcomes.get("forward_returns_pct") or {}
            for horizon in clean_horizons:
                key = str(horizon)
                value = _number(forward.get(key))
                return_name = f"label__forward_return_{horizon}bar_pct"
                positive_name = f"label__positive_return_{horizon}bar"
                record[return_name] = value
                record[positive_name] = None if value is None else value > 0
                label_names.update((return_name, positive_name))

            mfe_name = f"label__max_favorable_excursion_{max_horizon}bar_pct"
            mae_name = f"label__max_adverse_excursion_{max_horizon}bar_pct"
            record[mfe_name] = outcomes.get("max_favorable_excursion_pct")
            record[mae_name] = outcomes.get("max_adverse_excursion_pct")
            label_names.update((mfe_name, mae_name))
            records.append(record)

    return {
        "causal_replay": True,
        "sessions_analyzed": len(sessions),
        "horizons": list(clean_horizons),
        "require_full_horizon": bool(require_full_horizon),
        "row_count": len(records),
        "feature_columns": sorted(feature_names),
        "label_columns": sorted(label_names),
        "records": records,
        "note": (
            "Every feature column is calculated without future bars. Columns prefixed "
            "label__ are calculated only from bars after the observation timestamp."
        ),
    }


def run_detector_event_study(
    rows: list[dict[str, Any]],
    *,
    detectors: list[str] | None = None,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    swing_radius: int = 3,
) -> dict[str, Any]:
    """Replay historical sessions bar by bar and study causal detector events.

    A detector event is emitted on a rising edge only. Persistent states therefore
    count once until they turn off and later become active again.
    """
    selected = detectors or list(DETECTOR_SPECS)
    unknown = [name for name in selected if name not in DETECTOR_SPECS]
    if unknown:
        raise ValueError("Unknown detector(s): " + ", ".join(sorted(unknown)))

    clean_horizons = tuple(sorted({max(1, int(value)) for value in horizons}))
    sessions = _ordered_sessions(rows)
    events: list[dict[str, Any]] = []
    bars_analyzed = 0

    for session_key, session_rows in sessions:
        active_state = {name: False for name in selected}
        bars_analyzed += len(session_rows)
        for index in range(len(session_rows)):
            prefix = session_rows[: index + 1]
            snapshot = build_market_features(prefix, swing_radius=swing_radius)
            features = snapshot.get("features") or {}
            for detector in selected:
                spec = DETECTOR_SPECS[detector]
                active = _is_active(features, spec)
                if active and not active_state[detector]:
                    outcomes = _event_outcomes(
                        session_rows,
                        index,
                        horizons=clean_horizons,
                        direction=int(spec.get("direction") or 0),
                    )
                    events.append(
                        {
                            "detector": detector,
                            "label": spec.get("label"),
                            "direction": int(spec.get("direction") or 0),
                            "session": session_key,
                            "detection_index": index,
                            "detection_timestamp": _timestamp(session_rows[index]),
                            "feature_value": features.get(str(spec["feature"])),
                            "features": dict(features),
                            "evidence": snapshot.get("evidence") or {},
                            "outcomes": outcomes,
                        }
                    )
                active_state[detector] = active

    return {
        "causal_replay": True,
        "sessions_analyzed": len(sessions),
        "bars_analyzed": bars_analyzed,
        "horizons": list(clean_horizons),
        "detectors": selected,
        "events": events,
        "summary": _summarize_events(events, horizons=clean_horizons),
        "note": (
            "Detector events are calculated from historical prefixes only. Future bars are used only "
            "after detection to measure outcomes; these statistics are descriptive evidence, not a "
            "guarantee of profitability."
        ),
    }
