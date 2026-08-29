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

from youtube_strategy_engine import bars_to_frame, safe_float


RETROSPECTIVE_TEACHER_VERSION = 1


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
        "day_change_pct",
        "spread_pct",
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
    examples = sorted(
        [*swing_examples, *breakout_examples],
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
