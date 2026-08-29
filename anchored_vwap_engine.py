"""Causal Anchored VWAP primitives for deterministic historical research.

The functions here deliberately avoid retrospective anchors. Swing pivots become
usable only after the configured right-side confirmation bars have closed. When a
pivot becomes confirmed, AVWAP may be calculated from that already-observed pivot
through the current bar because every included bar is known at decision time.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


SUPPORTED_AVWAP_ANCHOR_MODES = {
    "session_open",
    "session_minute",
    "breakout_bar",
    "previous_day_high_break",
    "swing_low",
    "swing_high",
    "higher_low_handoff",
    "lower_high_handoff",
}


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if pd.notna(number) else default


def _confirmed_pivot(
    lows: list[float],
    highs: list[float],
    current_pos: int,
    confirm_bars: int,
    *,
    kind: str,
) -> tuple[int, float] | None:
    candidate = current_pos - confirm_bars
    if candidate < confirm_bars:
        return None
    left = candidate - confirm_bars
    right = candidate + confirm_bars
    if right > current_pos:
        return None

    if kind == "low":
        value = lows[candidate]
        window = lows[left : right + 1]
        if not window or value != min(window):
            return None
    else:
        value = highs[candidate]
        window = highs[left : right + 1]
        if not window or value != max(window):
            return None
    return candidate, value


def _session_avwap(
    group: pd.DataFrame,
    *,
    mode: str,
    confirm_bars: int,
    anchor_session_minute: int,
) -> tuple[pd.Series, pd.Series]:
    positions = list(range(len(group)))
    typical = ((group["high"] + group["low"] + group["close"]) / 3.0).astype(float).tolist()
    volumes = group["volume"].fillna(0).astype(float).tolist()
    lows = group["low"].astype(float).tolist()
    highs = group["high"].astype(float).tolist()
    closes = group["close"].astype(float).tolist()

    cum_volume: list[float] = []
    cum_pv: list[float] = []
    running_volume = 0.0
    running_pv = 0.0
    for price, volume in zip(typical, volumes):
        running_volume += max(0.0, volume)
        running_pv += price * max(0.0, volume)
        cum_volume.append(running_volume)
        cum_pv.append(running_pv)

    anchor_pos: int | None = None
    anchor_label = ""
    previous_pivot_value: float | None = None
    values: list[float | None] = []
    labels: list[str | None] = []

    for pos in positions:
        if mode == "session_open" and anchor_pos is None:
            anchor_pos = 0
            anchor_label = "session_open"
        elif mode == "session_minute" and anchor_pos is None:
            minute_value = _number(group.iloc[pos].get("session_minute"), -1)
            minute = int(minute_value if minute_value is not None else -1)
            if minute >= anchor_session_minute:
                anchor_pos = pos
                anchor_label = f"session_minute_{anchor_session_minute}"
        elif mode == "breakout_bar" and anchor_pos is None:
            prior_high = _number(group.iloc[pos].get("prior_breakout_high"))
            if prior_high is not None and closes[pos] > prior_high:
                anchor_pos = pos
                anchor_label = "breakout_bar"
        elif mode == "previous_day_high_break" and anchor_pos is None:
            prior_day_high = _number(group.iloc[pos].get("previous_daily_high"))
            previous_close = _number(group.iloc[pos].get("previous_bar_close"))
            if (
                prior_day_high is not None
                and previous_close is not None
                and previous_close <= prior_day_high
                and closes[pos] > prior_day_high
            ):
                anchor_pos = pos
                anchor_label = "previous_day_high_break"
        elif mode in {"swing_low", "higher_low_handoff"}:
            pivot = _confirmed_pivot(
                lows,
                highs,
                pos,
                confirm_bars,
                kind="low",
            )
            if pivot is not None:
                pivot_pos, pivot_value = pivot
                if mode == "swing_low":
                    anchor_pos = pivot_pos
                    anchor_label = "confirmed_swing_low"
                elif previous_pivot_value is not None and pivot_value > previous_pivot_value:
                    anchor_pos = pivot_pos
                    anchor_label = "confirmed_higher_low_handoff"
                previous_pivot_value = pivot_value
        elif mode in {"swing_high", "lower_high_handoff"}:
            pivot = _confirmed_pivot(
                lows,
                highs,
                pos,
                confirm_bars,
                kind="high",
            )
            if pivot is not None:
                pivot_pos, pivot_value = pivot
                if mode == "swing_high":
                    anchor_pos = pivot_pos
                    anchor_label = "confirmed_swing_high"
                elif previous_pivot_value is not None and pivot_value < previous_pivot_value:
                    anchor_pos = pivot_pos
                    anchor_label = "confirmed_lower_high_handoff"
                previous_pivot_value = pivot_value

        if anchor_pos is None or anchor_pos > pos:
            values.append(None)
            labels.append(None)
            continue

        prior_volume = cum_volume[anchor_pos - 1] if anchor_pos > 0 else 0.0
        prior_pv = cum_pv[anchor_pos - 1] if anchor_pos > 0 else 0.0
        anchored_volume = cum_volume[pos] - prior_volume
        anchored_pv = cum_pv[pos] - prior_pv
        values.append(anchored_pv / anchored_volume if anchored_volume > 0 else None)
        labels.append(anchor_label or mode)

    return (
        pd.Series(values, index=group.index, dtype="float64"),
        pd.Series(labels, index=group.index, dtype="object"),
    )


def apply_anchored_vwap_indicators(
    frame: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    """Add causal AVWAP columns required by the deterministic strategy engine."""
    data = frame.copy()
    for column in (
        "avwap",
        "previous_avwap",
        "avwap_distance_pct",
        "avwap_rising",
        "avwap_pullback_recent",
        "avwap_anchor_active",
        "avwap_anchor_reason",
    ):
        if column not in data.columns:
            data[column] = None

    mode = str(rules.get("avwap_anchor_mode") or "").strip().casefold()
    if not mode or mode not in SUPPORTED_AVWAP_ANCHOR_MODES or data.empty:
        return data

    confirm_bars = int(_number(rules.get("avwap_pivot_confirm_bars"), 2) or 2)
    confirm_bars = min(20, max(1, confirm_bars))
    anchor_minute_value = _number(rules.get("avwap_anchor_session_minute"), 0)
    anchor_session_minute = int(anchor_minute_value if anchor_minute_value is not None else 0)
    anchor_session_minute = min(390, max(0, anchor_session_minute))

    avwap = pd.Series(index=data.index, dtype="float64")
    reasons = pd.Series(index=data.index, dtype="object")
    for _, group in data.groupby("session", sort=False):
        values, labels = _session_avwap(
            group,
            mode=mode,
            confirm_bars=confirm_bars,
            anchor_session_minute=anchor_session_minute,
        )
        avwap.loc[group.index] = values
        reasons.loc[group.index] = labels

    data["avwap"] = avwap
    data["avwap_anchor_reason"] = reasons
    data["avwap_anchor_active"] = data["avwap"].notna()
    data["previous_avwap"] = data.groupby("session", sort=False)["avwap"].shift(1)
    data["avwap_distance_pct"] = (
        data["close"].div(data["avwap"]) - 1.0
    ) * 100.0
    data["avwap_rising"] = data["avwap"] > data["previous_avwap"]

    tolerance = _number(rules.get("avwap_pullback_tolerance_pct"), 0.5) or 0.5
    tolerance = min(20.0, max(0.01, tolerance))
    lower_band = data["avwap"] * (1.0 - tolerance / 100.0)
    upper_band = data["avwap"] * (1.0 + tolerance / 100.0)
    touches = (
        data["avwap"].notna()
        & (data["low"] <= upper_band)
        & (data["high"] >= lower_band)
    )
    recent_touch = touches.groupby(data["session"], sort=False).transform(
        lambda series: series.rolling(3, min_periods=1).max()
    )
    data["avwap_pullback_recent"] = (
        recent_touch.fillna(False).astype(bool)
        & data["avwap"].notna()
        & (data["close"] >= data["avwap"])
    )
    return data


TEACHER_MULTI_AVWAP_MODES: tuple[str, ...] = (
    "swing_low",
    "swing_high",
    "higher_low_handoff",
    "lower_high_handoff",
    "breakout_bar",
    "previous_day_high_break",
)


def apply_multi_anchor_avwap_teacher_features(
    frame: pd.DataFrame,
    *,
    modes: tuple[str, ...] = TEACHER_MULTI_AVWAP_MODES,
    confirm_bars: int = 2,
    pinch_threshold_pct: float = 0.35,
) -> pd.DataFrame:
    """Attach multiple causal AVWAPs for retrospective-teacher research.

    Each component AVWAP obeys the same causal activation rules as the live
    deterministic engine. A swing anchor is not visible until its right-side
    confirmation bars have closed.
    """
    data = frame.copy().sort_values("timestamp").reset_index(drop=True)
    clean_modes = tuple(
        mode
        for mode in modes
        if str(mode or "") in SUPPORTED_AVWAP_ANCHOR_MODES
    )
    confirm_bars = min(20, max(1, int(confirm_bars)))
    pinch_threshold_pct = min(10.0, max(0.01, float(pinch_threshold_pct)))

    avwap_columns: list[str] = []
    for mode in clean_modes:
        rules = {
            "avwap_anchor_mode": mode,
            "avwap_pivot_confirm_bars": confirm_bars,
            "avwap_pullback_tolerance_pct": 0.5,
        }
        enriched = apply_anchored_vwap_indicators(data, rules)
        column = f"teacher_avwap_{mode}"
        data[column] = pd.to_numeric(enriched["avwap"], errors="coerce")
        avwap_columns.append(column)

    if not avwap_columns:
        data["multi_avwap_active_count"] = 0
        data["multi_avwap_spread_pct"] = float("nan")
        data["multi_avwap_center"] = float("nan")
        data["multi_avwap_price_distance_pct"] = float("nan")
        data["multi_avwap_pinch"] = False
        return data

    values = data[avwap_columns]
    active_count = values.notna().sum(axis=1)
    minimum = values.min(axis=1, skipna=True)
    maximum = values.max(axis=1, skipna=True)
    center = values.mean(axis=1, skipna=True)
    spread_pct = ((maximum - minimum).div(center.replace(0, float("nan")))) * 100.0

    data["multi_avwap_active_count"] = active_count.astype(int)
    data["multi_avwap_spread_pct"] = spread_pct.where(active_count >= 2)
    data["multi_avwap_center"] = center.where(active_count >= 2)
    data["multi_avwap_price_distance_pct"] = (
        (data["close"].div(data["multi_avwap_center"]) - 1.0) * 100.0
    ).where(active_count >= 2)
    data["multi_avwap_pinch"] = (
        (active_count >= 2)
        & data["multi_avwap_spread_pct"].notna()
        & (data["multi_avwap_spread_pct"] <= pinch_threshold_pct)
    )
    return data
