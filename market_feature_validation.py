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

from market_features import MARKET_FEATURE_COLUMNS, add_causal_market_feature_columns, build_market_features


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


def limit_rows_to_recent_market_sessions(
    rows: list[dict[str, Any]],
    session_limit: int | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep only the most recent completed market-date buckets from timestamped rows.

    The session labels use the same America/New_York grouping as detector replay, so
    weekends and market holidays naturally contribute zero sessions. If timestamps
    cannot be parsed, the original rows are returned unchanged because an exact
    trading-session count cannot be established safely.
    """
    if session_limit is None:
        sessions = _ordered_sessions(rows)
        return list(rows or []), [name for name, _ in sessions]
    limit = max(1, int(session_limit))
    sessions = _ordered_sessions(rows)
    if not sessions:
        return [], []
    if len(sessions) == 1 and sessions[0][0] == "session-0":
        return list(rows or []), ["session-0"]
    selected = sessions[-limit:]
    flattened = [row for _, session_rows in selected for row in session_rows]
    return flattened, [name for name, _ in selected]


def _is_active(features: dict[str, Any], spec: dict[str, Any]) -> bool:
    return features.get(str(spec["feature"])) == spec.get("equals")


def _event_outcomes(
    rows: list[dict[str, Any]],
    index: int,
    *,
    horizons: tuple[int, ...],
    direction: int,
    profit_target_pct: float | None = None,
    stop_loss_pct: float | None = None,
) -> dict[str, Any]:
    """Measure future outcomes without allowing future data into the feature row.

    When profit/stop barriers are supplied, a successful trade-quality outcome
    means the upside target was reached before the downside limit within the
    selected horizon. If both barriers are touched inside the same candle, the
    downside barrier wins conservatively because intrabar ordering is unknowable.
    """
    entry = _close(rows[index])
    outcomes: dict[str, Any] = {
        "entry_price": entry,
        "forward_returns_pct": {},
        "directional_returns_pct": {},
        "max_favorable_excursion_pct_by_horizon": {},
        "max_adverse_excursion_pct_by_horizon": {},
        "target_before_stop_by_horizon": {},
        "barrier_outcome_by_horizon": {},
        "max_favorable_excursion_pct": None,
        "max_adverse_excursion_pct": None,
        "directional_max_favorable_excursion_pct": None,
        "directional_max_adverse_excursion_pct": None,
        "profit_target_pct": profit_target_pct,
        "stop_loss_pct": stop_loss_pct,
    }
    if entry is None or entry <= 0:
        return outcomes

    target_pct = _number(profit_target_pct)
    stop_pct = _number(stop_loss_pct)
    use_barriers = (
        target_pct is not None
        and stop_pct is not None
        and target_pct > 0
        and stop_pct > 0
    )

    for horizon in horizons:
        key = str(horizon)
        target_index = index + horizon
        full_window = target_index < len(rows)
        future_return = None
        if full_window:
            target_close = _close(rows[target_index])
            if target_close is not None:
                future_return = ((target_close / entry) - 1.0) * 100.0
        outcomes["forward_returns_pct"][key] = future_return
        outcomes["directional_returns_pct"][key] = (
            future_return * direction
            if future_return is not None and direction in {-1, 1}
            else None
        )

        future_window = rows[index + 1 : min(len(rows), index + horizon + 1)]
        highs = [_high(row) for row in future_window]
        lows = [_low(row) for row in future_window]
        highs = [value for value in highs if value is not None]
        lows = [value for value in lows if value is not None]
        mfe = ((max(highs) / entry) - 1.0) * 100.0 if highs else None
        mae = ((min(lows) / entry) - 1.0) * 100.0 if lows else None
        outcomes["max_favorable_excursion_pct_by_horizon"][key] = mfe
        outcomes["max_adverse_excursion_pct_by_horizon"][key] = mae

        if use_barriers and full_window:
            target_price = entry * (1.0 + float(target_pct) / 100.0)
            stop_price = entry * (1.0 - float(stop_pct) / 100.0)
            barrier_outcome = "none"
            for future_bar in future_window:
                high = _high(future_bar)
                low = _low(future_bar)
                hit_target = high is not None and high >= target_price
                hit_stop = low is not None and low <= stop_price
                if hit_stop:
                    # Includes same-bar target+stop ambiguity: count downside first.
                    barrier_outcome = "stop"
                    break
                if hit_target:
                    barrier_outcome = "target"
                    break
            outcomes["barrier_outcome_by_horizon"][key] = barrier_outcome
            outcomes["target_before_stop_by_horizon"][key] = barrier_outcome == "target"
        else:
            outcomes["barrier_outcome_by_horizon"][key] = None
            outcomes["target_before_stop_by_horizon"][key] = None

    max_horizon = max(horizons, default=0)
    max_key = str(max_horizon)
    outcomes["max_favorable_excursion_pct"] = (
        outcomes["max_favorable_excursion_pct_by_horizon"].get(max_key)
    )
    outcomes["max_adverse_excursion_pct"] = (
        outcomes["max_adverse_excursion_pct_by_horizon"].get(max_key)
    )

    raw_mfe = _number(outcomes.get("max_favorable_excursion_pct"))
    raw_mae = _number(outcomes.get("max_adverse_excursion_pct"))
    if direction == 1:
        outcomes["directional_max_favorable_excursion_pct"] = raw_mfe
        outcomes["directional_max_adverse_excursion_pct"] = raw_mae
    elif direction == -1:
        outcomes["directional_max_favorable_excursion_pct"] = -raw_mae if raw_mae is not None else None
        outcomes["directional_max_adverse_excursion_pct"] = -raw_mfe if raw_mfe is not None else None
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

        directional_mfes = [
            _number((event.get("outcomes") or {}).get("directional_max_favorable_excursion_pct"))
            for event in detector_events
        ]
        directional_maes = [
            _number((event.get("outcomes") or {}).get("directional_max_adverse_excursion_pct"))
            for event in detector_events
        ]
        directional_mfes = [value for value in directional_mfes if value is not None]
        directional_maes = [value for value in directional_maes if value is not None]
        item["avg_directional_max_favorable_excursion_pct"] = (
            mean(directional_mfes) if directional_mfes else None
        )
        item["avg_directional_max_adverse_excursion_pct"] = (
            mean(directional_maes) if directional_maes else None
        )

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
                "directional_samples": len(directional),
                "directional_hits": sum(value > 0 for value in directional),
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


def _plain_scalar(value: Any) -> Any:
    """Convert pandas/numpy scalar values into JSON-friendly Python scalars."""
    if value is None:
        return None
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        missing = False
    if isinstance(missing, bool) and missing:
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def _session_feature_frame(
    session_key: str,
    rows: list[dict[str, Any]],
    *,
    swing_radius: int,
) -> pd.DataFrame:
    """Calculate the causal market-feature vocabulary once for a whole session."""
    frame = pd.DataFrame(
        [
            {
                "timestamp": _timestamp(row),
                "open": _number(_value(row, "o", "open", "Open")),
                "high": _high(row),
                "low": _low(row),
                "close": _close(row),
                "volume": _number(_value(row, "v", "volume", "Volume")) or 0.0,
                "session": session_key,
            }
            for row in rows
        ]
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    return add_causal_market_feature_columns(
        frame,
        session_column="session",
        swing_radius=swing_radius,
    )


def build_supervised_feature_rows(
    rows: list[dict[str, Any]],
    *,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    swing_radius: int = 3,
    require_full_horizon: bool = True,
    profit_target_pct: float = 1.0,
    stop_loss_pct: float = 0.75,
    observation_stride_bars: int = 1,
) -> dict[str, Any]:
    """Build leakage-safe supervised-learning rows from historical candles.

    Each session's causal feature frame is calculated once, left-to-right. Labels
    are measured strictly from later bars in the same session. In addition to raw
    forward-return labels, each horizon receives a conservative trade-quality label
    asking whether price reached the profit target before the stop limit.
    """
    clean_horizons = tuple(sorted({max(1, int(value)) for value in horizons}))
    max_horizon = max(clean_horizons, default=0)
    observation_stride_bars = max(1, int(observation_stride_bars))
    clean_profit_target = _number(profit_target_pct)
    clean_stop_loss = _number(stop_loss_pct)
    if clean_profit_target is None or clean_profit_target <= 0:
        raise ValueError("profit_target_pct must be greater than zero.")
    if clean_stop_loss is None or clean_stop_loss <= 0:
        raise ValueError("stop_loss_pct must be greater than zero.")

    sessions = _ordered_sessions(rows)
    records: list[dict[str, Any]] = []
    feature_names = {f"feature__{name}" for name in MARKET_FEATURE_COLUMNS}
    label_names: set[str] = set()

    for session_key, session_rows in sessions:
        feature_frame = _session_feature_frame(
            session_key,
            session_rows,
            swing_radius=swing_radius,
        )
        if len(feature_frame) != len(session_rows):
            raise ValueError("Causal feature frame did not preserve historical row count.")

        for index in range(0, len(session_rows), observation_stride_bars):
            if require_full_horizon and index + max_horizon >= len(session_rows):
                continue
            outcomes = _event_outcomes(
                session_rows,
                index,
                horizons=clean_horizons,
                direction=0,
                profit_target_pct=float(clean_profit_target),
                stop_loss_pct=float(clean_stop_loss),
            )
            feature_row = feature_frame.iloc[index]
            record: dict[str, Any] = {
                "session": session_key,
                "bar_index": index,
                "timestamp": _timestamp(session_rows[index]),
            }
            for name in MARKET_FEATURE_COLUMNS:
                record[f"feature__{name}"] = _plain_scalar(feature_row.get(name))

            forward = outcomes.get("forward_returns_pct") or {}
            mfes = outcomes.get("max_favorable_excursion_pct_by_horizon") or {}
            maes = outcomes.get("max_adverse_excursion_pct_by_horizon") or {}
            target_before_stop = outcomes.get("target_before_stop_by_horizon") or {}
            barrier_outcomes = outcomes.get("barrier_outcome_by_horizon") or {}

            for horizon in clean_horizons:
                key = str(horizon)
                value = _number(forward.get(key))
                return_name = f"label__forward_return_{horizon}bar_pct"
                positive_name = f"label__positive_return_{horizon}bar"
                mfe_name = f"label__max_favorable_excursion_{horizon}bar_pct"
                mae_name = f"label__max_adverse_excursion_{horizon}bar_pct"
                target_name = f"label__target_before_stop_{horizon}bar"
                outcome_name = f"label__barrier_outcome_{horizon}bar"

                record[return_name] = value
                record[positive_name] = None if value is None else value > 0
                record[mfe_name] = _number(mfes.get(key))
                record[mae_name] = _number(maes.get(key))
                record[target_name] = target_before_stop.get(key)
                record[outcome_name] = barrier_outcomes.get(key)
                label_names.update(
                    (
                        return_name,
                        positive_name,
                        mfe_name,
                        mae_name,
                        target_name,
                        outcome_name,
                    )
                )
            records.append(record)

    return {
        "causal_replay": True,
        "sessions_analyzed": len(sessions),
        "horizons": list(clean_horizons),
        "require_full_horizon": bool(require_full_horizon),
        "profit_target_pct": float(clean_profit_target),
        "stop_loss_pct": float(clean_stop_loss),
        "barrier_same_bar_policy": "stop_first_conservative",
        "observation_stride_bars": observation_stride_bars,
        "row_count": len(records),
        "feature_columns": sorted(feature_names),
        "label_columns": sorted(label_names),
        "records": records,
        "feature_calculation": "single_pass_causal_session_frame",
        "note": (
            "Every feature column is calculated without future bars. Columns prefixed "
            "label__ are calculated only from bars after the observation timestamp. "
            "Trade-quality labels ask whether the upside target was reached before the "
            "downside limit; if both are touched in one candle, the downside limit wins "
            "conservatively because intrabar ordering is unknown. "
            f"Supervised observations are sampled every {observation_stride_bars} bar(s), "
            "while causal features still use every underlying candle."
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
