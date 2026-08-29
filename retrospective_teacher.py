"""Retrospective teacher to causal learner primitives.

Hindsight is allowed only to create labels. Every predictive feature attached to a
teacher example is calculated from bars that existed at or before the decision
bar. known_at records when the retrospective label would become observable.

This separation lets the Lab learn from what eventually proved important without
letting future information leak into historical predictions or backtests.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from statistics import median
from typing import Any

import pandas as pd

from anchored_vwap_engine import apply_multi_anchor_avwap_teacher_features
from causal_volume_profile import apply_causal_volume_profile_features
from indicator_cross_validation import cross_validate_indicators
from youtube_strategy_engine import add_indicators, bars_to_frame, safe_float


RETROSPECTIVE_TEACHER_VERSION = 2


def _iso(value: Any) -> str:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.isoformat().replace("+00:00", "Z")


def _causal_feature_snapshot(frame: pd.DataFrame, position: int) -> dict[str, Any]:
    """Build features from data available no later than position."""
    history = frame.iloc[: position + 1].copy()
    row = history.iloc[-1]
    close = float(row["close"])
    open_price = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    volume = float(row["volume"])
    session = str(row.get("session") or "")
    session_history = history[history["session"].astype(str) == session].copy()

    def prior_close(offset: int) -> float | None:
        index = len(history) - 1 - offset
        if index < 0:
            return None
        return safe_float(history.iloc[index].get("close"))

    features: dict[str, Any] = {
        "price": close,
        "bar_return_pct": ((close / open_price) - 1.0) * 100.0 if open_price > 0 else None,
        "bar_range_pct": ((high - low) / close) * 100.0 if close > 0 else None,
        "close_location": ((close - low) / (high - low)) if high > low else 0.5,
        "session_minute": safe_float(row.get("session_minute")),
    }

    for bars in (1, 3, 5, 10):
        previous = prior_close(bars)
        features[f"return_{bars}_bar_pct"] = (
            ((close / previous) - 1.0) * 100.0
            if previous is not None and previous > 0
            else None
        )

    prior_volumes = pd.to_numeric(
        history.iloc[max(0, len(history) - 21) : -1]["volume"],
        errors="coerce",
    ).dropna()
    features["trailing_volume_ratio"] = (
        volume / float(prior_volumes.mean())
        if len(prior_volumes) >= 3 and float(prior_volumes.mean()) > 0
        else None
    )

    short_prior = pd.to_numeric(
        history.iloc[max(0, len(history) - 4) : -1]["volume"],
        errors="coerce",
    ).dropna()
    long_prior = pd.to_numeric(
        history.iloc[max(0, len(history) - 11) : -1]["volume"],
        errors="coerce",
    ).dropna()
    features["volume_acceleration_ratio"] = (
        float(short_prior.mean()) / float(long_prior.mean())
        if len(short_prior) >= 2
        and len(long_prior) >= 5
        and float(long_prior.mean()) > 0
        else None
    )

    typical = (
        session_history["high"].astype(float)
        + session_history["low"].astype(float)
        + session_history["close"].astype(float)
    ) / 3.0
    session_volume = session_history["volume"].astype(float).clip(lower=0)
    cumulative_volume = float(session_volume.sum())
    vwap = (
        float((typical * session_volume).sum()) / cumulative_volume
        if cumulative_volume > 0
        else None
    )
    features["vwap_distance_pct"] = (
        ((close / vwap) - 1.0) * 100.0 if vwap is not None and vwap > 0 else None
    )

    trailing = history.iloc[max(0, len(history) - 21) : -1]
    if not trailing.empty:
        prior_high = safe_float(trailing["high"].max())
        prior_low = safe_float(trailing["low"].min())
        features["distance_from_20bar_high_pct"] = (
            ((close / prior_high) - 1.0) * 100.0
            if prior_high is not None and prior_high > 0
            else None
        )
        features["distance_from_20bar_low_pct"] = (
            ((close / prior_low) - 1.0) * 100.0
            if prior_low is not None and prior_low > 0
            else None
        )

    for name in (
        "avwap_distance_pct",
        "relative_volume",
        "volume_surge",
        "day_change_pct",
        "spread_pct",
        "atr_14",
        "fast_ema_distance_pct",
        "vp_distance_to_poc_pct",
        "vp_value_area_location",
        "vp_poc_volume_share",
        "vp_value_area_width_pct",
        "vp_profile_entropy",
        "volume_climax_ratio",
        "range_expansion_ratio",
        "upper_wick_fraction",
        "lower_wick_fraction",
        "upper_exhaustion_pressure",
        "lower_exhaustion_pressure",
        "multi_avwap_active_count",
        "multi_avwap_spread_pct",
        "multi_avwap_price_distance_pct",
    ):
        if name in row.index:
            features[name] = safe_float(row.get(name))

    return features


def _teacher_example(
    frame: pd.DataFrame,
    *,
    event_pos: int,
    known_pos: int,
    outcome_end_pos: int,
    label: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = frame.iloc[event_pos]
    known = frame.iloc[min(known_pos, len(frame) - 1)]
    outcome = frame.iloc[min(outcome_end_pos, len(frame) - 1)]
    event_time = _iso(event["timestamp"])
    known_at = _iso(known["timestamp"])
    outcome_end = _iso(outcome["timestamp"])
    return {
        "label": label,
        "event_time": event_time,
        "decision_time": event_time,
        "feature_cutoff": event_time,
        "known_at": known_at,
        "outcome_window_end": outcome_end,
        "teacher_used_future_for_label": known_pos > event_pos or outcome_end_pos > event_pos,
        "features": _causal_feature_snapshot(frame, event_pos),
        "metadata": dict(metadata or {}),
    }


def label_confirmed_swings(
    frame: pd.DataFrame,
    *,
    left_bars: int = 3,
    right_bars: int = 3,
    minimum_move_pct: float = 1.0,
) -> list[dict[str, Any]]:
    """Retrospectively label significant swing highs/lows."""
    left_bars = max(1, int(left_bars))
    right_bars = max(1, int(right_bars))
    minimum_move_pct = max(0.0, float(minimum_move_pct))
    examples: list[dict[str, Any]] = []

    for _, session in frame.groupby("session", sort=False):
        session = session.reset_index()
        if len(session) < left_bars + right_bars + 1:
            continue
        for local_pos in range(left_bars, len(session) - right_bars):
            global_pos = int(session.iloc[local_pos]["index"])
            window = session.iloc[
                local_pos - left_bars : local_pos + right_bars + 1
            ]
            low = float(session.iloc[local_pos]["low"])
            high = float(session.iloc[local_pos]["high"])
            future = session.iloc[local_pos + 1 : local_pos + right_bars + 1]
            if future.empty:
                continue

            if low <= float(window["low"].min()):
                future_high = float(future["high"].max())
                move_pct = ((future_high / low) - 1.0) * 100.0 if low > 0 else 0.0
                if move_pct >= minimum_move_pct:
                    known_global = int(session.iloc[local_pos + right_bars]["index"])
                    examples.append(
                        _teacher_example(
                            frame,
                            event_pos=global_pos,
                            known_pos=known_global,
                            outcome_end_pos=known_global,
                            label="significant_swing_low",
                            metadata={
                                "confirmation_bars": right_bars,
                                "confirmed_rebound_pct": round(move_pct, 4),
                            },
                        )
                    )

            if high >= float(window["high"].max()):
                future_low = float(future["low"].min())
                move_pct = ((high / future_low) - 1.0) * 100.0 if future_low > 0 else 0.0
                if move_pct >= minimum_move_pct:
                    known_global = int(session.iloc[local_pos + right_bars]["index"])
                    examples.append(
                        _teacher_example(
                            frame,
                            event_pos=global_pos,
                            known_pos=known_global,
                            outcome_end_pos=known_global,
                            label="significant_swing_high",
                            metadata={
                                "confirmation_bars": right_bars,
                                "confirmed_reversal_pct": round(move_pct, 4),
                            },
                        )
                    )
    return examples


def label_breakout_outcomes(
    frame: pd.DataFrame,
    *,
    lookback_bars: int = 20,
    outcome_bars: int = 12,
    success_move_pct: float = 2.0,
) -> list[dict[str, Any]]:
    """Label whether a causally detectable breakout later followed through."""
    lookback_bars = max(3, int(lookback_bars))
    outcome_bars = max(2, int(outcome_bars))
    success_move_pct = max(0.1, float(success_move_pct))
    examples: list[dict[str, Any]] = []

    for _, session in frame.groupby("session", sort=False):
        positions = list(session.index)
        if len(positions) < lookback_bars + outcome_bars + 1:
            continue
        for local_pos in range(lookback_bars, len(positions) - outcome_bars):
            event_pos = positions[local_pos]
            row = frame.loc[event_pos]
            prior_positions = positions[local_pos - lookback_bars : local_pos]
            prior_high = float(frame.loc[prior_positions, "high"].max())
            close = float(row["close"])
            previous_close = (
                float(frame.loc[positions[local_pos - 1], "close"])
                if local_pos > 0
                else close
            )
            if not (previous_close <= prior_high and close > prior_high):
                continue

            future_positions = positions[
                local_pos + 1 : local_pos + outcome_bars + 1
            ]
            forward_high = float(frame.loc[future_positions, "high"].max())
            final_close = float(frame.loc[future_positions[-1], "close"])
            best_move_pct = ((forward_high / close) - 1.0) * 100.0
            final_return_pct = ((final_close / close) - 1.0) * 100.0
            success = best_move_pct >= success_move_pct and final_return_pct > 0
            label = "breakout_followthrough" if success else "failed_breakout"
            examples.append(
                _teacher_example(
                    frame,
                    event_pos=event_pos,
                    known_pos=future_positions[-1],
                    outcome_end_pos=future_positions[-1],
                    label=label,
                    metadata={
                        "prior_high": round(prior_high, 6),
                        "best_forward_move_pct": round(best_move_pct, 4),
                        "final_forward_return_pct": round(final_return_pct, 4),
                        "outcome_bars": outcome_bars,
                    },
                )
            )
    return examples


def label_volume_exhaustion_outcomes(
    frame: pd.DataFrame,
    *,
    outcome_bars: int = 8,
    pressure_threshold: float = 65.0,
    reversal_move_pct: float = 1.5,
) -> list[dict[str, Any]]:
    """Use future bars only to label whether causal exhaustion pressure reversed."""
    outcome_bars = max(2, int(outcome_bars))
    pressure_threshold = min(100.0, max(1.0, float(pressure_threshold)))
    reversal_move_pct = max(0.1, float(reversal_move_pct))
    examples: list[dict[str, Any]] = []

    for _, session in frame.groupby("session", sort=False):
        positions = list(session.index)
        for local_pos in range(1, len(positions) - outcome_bars):
            event_pos = positions[local_pos]
            previous_pos = positions[local_pos - 1]
            upper = safe_float(frame.at[event_pos, "upper_exhaustion_pressure"])
            lower = safe_float(frame.at[event_pos, "lower_exhaustion_pressure"])
            previous_upper = safe_float(frame.at[previous_pos, "upper_exhaustion_pressure"], 0.0) or 0.0
            previous_lower = safe_float(frame.at[previous_pos, "lower_exhaustion_pressure"], 0.0) or 0.0
            direction = ""
            pressure = None
            if (
                upper is not None
                and upper >= pressure_threshold
                and previous_upper < pressure_threshold
                and (lower is None or upper >= lower)
            ):
                direction = "upper"
                pressure = upper
            elif (
                lower is not None
                and lower >= pressure_threshold
                and previous_lower < pressure_threshold
            ):
                direction = "lower"
                pressure = lower
            if not direction:
                continue

            future_positions = positions[
                local_pos + 1 : local_pos + outcome_bars + 1
            ]
            close = float(frame.at[event_pos, "close"])
            if close <= 0 or not future_positions:
                continue
            if direction == "upper":
                future_low = float(frame.loc[future_positions, "low"].min())
                reversal_pct = ((close / future_low) - 1.0) * 100.0 if future_low > 0 else 0.0
            else:
                future_high = float(frame.loc[future_positions, "high"].max())
                reversal_pct = ((future_high / close) - 1.0) * 100.0

            reversed_enough = reversal_pct >= reversal_move_pct
            label = (
                f"{direction}_exhaustion_reversal"
                if reversed_enough
                else f"{direction}_exhaustion_no_reversal"
            )
            examples.append(
                _teacher_example(
                    frame,
                    event_pos=event_pos,
                    known_pos=future_positions[-1],
                    outcome_end_pos=future_positions[-1],
                    label=label,
                    metadata={
                        "pressure": round(float(pressure or 0.0), 4),
                        "best_reversal_pct": round(reversal_pct, 4),
                        "outcome_bars": outcome_bars,
                    },
                )
            )
    return examples


def label_multi_avwap_pinch_outcomes(
    frame: pd.DataFrame,
    *,
    outcome_bars: int = 12,
    expansion_move_pct: float = 1.5,
) -> list[dict[str, Any]]:
    """Label what happened after a causally known multi-AVWAP compression."""
    outcome_bars = max(2, int(outcome_bars))
    expansion_move_pct = max(0.1, float(expansion_move_pct))
    examples: list[dict[str, Any]] = []

    for _, session in frame.groupby("session", sort=False):
        positions = list(session.index)
        for local_pos in range(1, len(positions) - outcome_bars):
            event_pos = positions[local_pos]
            previous_pos = positions[local_pos - 1]
            pinch = bool(frame.at[event_pos, "multi_avwap_pinch"])
            prior_pinch = bool(frame.at[previous_pos, "multi_avwap_pinch"])
            if not pinch or prior_pinch:
                continue
            future_positions = positions[
                local_pos + 1 : local_pos + outcome_bars + 1
            ]
            close = float(frame.at[event_pos, "close"])
            if close <= 0 or not future_positions:
                continue
            future_high = float(frame.loc[future_positions, "high"].max())
            future_low = float(frame.loc[future_positions, "low"].min())
            upside = ((future_high / close) - 1.0) * 100.0
            downside = ((close / future_low) - 1.0) * 100.0 if future_low > 0 else 0.0
            if max(upside, downside) < expansion_move_pct:
                label = "avwap_pinch_no_expansion"
            elif upside >= downside:
                label = "avwap_pinch_upside_expansion"
            else:
                label = "avwap_pinch_downside_expansion"
            examples.append(
                _teacher_example(
                    frame,
                    event_pos=event_pos,
                    known_pos=future_positions[-1],
                    outcome_end_pos=future_positions[-1],
                    label=label,
                    metadata={
                        "upside_move_pct": round(upside, 4),
                        "downside_move_pct": round(downside, 4),
                        "active_avwaps": int(
                            safe_float(frame.at[event_pos, "multi_avwap_active_count"], 0)
                            or 0
                        ),
                        "spread_pct": safe_float(
                            frame.at[event_pos, "multi_avwap_spread_pct"]
                        ),
                        "outcome_bars": outcome_bars,
                    },
                )
            )
    return examples


def validate_no_lookahead(examples: list[dict[str, Any]]) -> None:
    """Raise if any teacher example leaks future information into its features."""
    for index, example in enumerate(examples):
        feature_cutoff = pd.Timestamp(example.get("feature_cutoff"))
        decision_time = pd.Timestamp(example.get("decision_time"))
        known_at = pd.Timestamp(example.get("known_at"))
        outcome_end = pd.Timestamp(example.get("outcome_window_end"))
        if feature_cutoff > decision_time:
            raise ValueError(f"Teacher example {index} has a feature cutoff after decision time.")
        if known_at < decision_time:
            raise ValueError(f"Teacher example {index} is marked known before the event occurs.")
        if outcome_end < decision_time:
            raise ValueError(f"Teacher example {index} has an outcome ending before the event.")


def _feature_medians(examples: list[dict[str, Any]]) -> dict[str, float]:
    values: dict[str, list[float]] = {}
    for example in examples:
        for name, raw in (example.get("features") or {}).items():
            number = safe_float(raw)
            if number is not None:
                values.setdefault(name, []).append(float(number))
    return {
        name: round(float(median(series)), 6)
        for name, series in values.items()
        if series
    }


def build_retrospective_teacher_run(
    rows: list[dict[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    swing_confirmation_bars: int = 3,
    swing_minimum_move_pct: float = 1.0,
    breakout_lookback_bars: int = 20,
    breakout_outcome_bars: int = 12,
    breakout_success_move_pct: float = 2.0,
    include_extended_hours: bool = False,
) -> dict[str, Any]:
    frame = bars_to_frame(rows, include_extended_hours=include_extended_hours)
    if frame.empty:
        raise ValueError("No usable bars were available for retrospective learning.")

    # Build every learner feature causally before future-derived labels are assigned.
    frame = add_indicators(
        frame,
        {
            "machine_rules": {
                "fast_ema_period": 9,
                "slow_ema_period": 20,
                "breakout_lookback_bars": breakout_lookback_bars,
            }
        },
    )
    frame = apply_causal_volume_profile_features(
        frame,
        lookback_bars=60,
        bins=24,
        value_area_pct=0.70,
    )
    frame = apply_multi_anchor_avwap_teacher_features(
        frame,
        confirm_bars=max(1, swing_confirmation_bars),
        pinch_threshold_pct=0.35,
    )

    swing_examples = label_confirmed_swings(
        frame,
        left_bars=swing_confirmation_bars,
        right_bars=swing_confirmation_bars,
        minimum_move_pct=swing_minimum_move_pct,
    )
    breakout_examples = label_breakout_outcomes(
        frame,
        lookback_bars=breakout_lookback_bars,
        outcome_bars=breakout_outcome_bars,
        success_move_pct=breakout_success_move_pct,
    )
    exhaustion_examples = label_volume_exhaustion_outcomes(
        frame,
        outcome_bars=max(4, min(20, breakout_outcome_bars)),
        pressure_threshold=65.0,
        reversal_move_pct=max(0.5, breakout_success_move_pct * 0.75),
    )
    avwap_pinch_examples = label_multi_avwap_pinch_outcomes(
        frame,
        outcome_bars=max(4, min(30, breakout_outcome_bars)),
        expansion_move_pct=max(0.5, breakout_success_move_pct * 0.75),
    )
    examples = sorted(
        [
            *swing_examples,
            *breakout_examples,
            *exhaustion_examples,
            *avwap_pinch_examples,
        ],
        key=lambda item: (str(item.get("event_time") or ""), str(item.get("label") or "")),
    )
    validate_no_lookahead(examples)

    counts = Counter(str(item.get("label") or "unknown") for item in examples)
    precursor_medians = {
        label: _feature_medians(
            [item for item in examples if str(item.get("label") or "") == label]
        )
        for label in sorted(counts)
    }

    return {
        "version": f"retrospective-teacher-v{RETROSPECTIVE_TEACHER_VERSION}",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "symbol": str(symbol or "").strip().upper(),
        "timeframe": str(timeframe or ""),
        "bar_count": int(len(frame)),
        "start": _iso(frame.iloc[0]["timestamp"]),
        "end": _iso(frame.iloc[-1]["timestamp"]),
        "label_counts": dict(counts),
        "precursor_feature_medians": precursor_medians,
        "feature_layers": {
            "price_volume_vwap": "causal",
            "volume_profile": "60-bar causal HLC3-volume approximation",
            "volume_exhaustion": "causal pressure features; future used only for reversal labels",
            "multi_anchor_avwap": "causal confirmed anchors; future used only for expansion labels",
        },
        "indicator_cross_validation": cross_validate_indicators(
            rows,
            ema_period=9,
            atr_window=14,
            include_extended_hours=include_extended_hours,
        ),
        "causality_policy": {
            "future_data_allowed_for": "retrospective labels and outcome measurement only",
            "future_data_forbidden_for": "predictive features, entries, exits, scores, and backtests",
            "feature_cutoff": "event/decision timestamp",
            "activation_rule": (
                "A hindsight-identified anchor may be used by a trading rule only from its "
                "known_at confirmation timestamp forward."
            ),
        },
        "examples": examples[-500:],
    }


def merge_retrospective_teacher_run(
    library: dict[str, Any],
    run: dict[str, Any],
    *,
    maximum_runs: int = 60,
) -> dict[str, Any]:
    result = dict(library or {})
    existing = [
        dict(item)
        for item in result.get("retrospective_learning_runs") or []
        if isinstance(item, dict)
    ]
    dedupe = (
        str(run.get("symbol") or ""),
        str(run.get("timeframe") or ""),
        str(run.get("start") or ""),
        str(run.get("end") or ""),
    )
    filtered = [
        item
        for item in existing
        if (
            str(item.get("symbol") or ""),
            str(item.get("timeframe") or ""),
            str(item.get("start") or ""),
            str(item.get("end") or ""),
        )
        != dedupe
    ]
    result["retrospective_learning_runs"] = [dict(run), *filtered][
        : max(1, int(maximum_runs))
    ]
    return result
