"""Reusable, non-look-ahead market feature calculations.

This module sits between raw market bars and strategy/scanner logic. It does
not decide whether to enter a trade. Instead it turns candles into explicit,
testable market facts that can be shared by the scanner, stock analyzer, and
future strategy engines.

All features are calculated only from rows supplied by the caller. No detector
uses bars after the final supplied bar, which makes prefix-by-prefix historical
validation possible without accidental look-ahead.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import math
import pandas as pd


@dataclass
class MarketFeatureSnapshot:
    """Normalized market facts plus evidence and missing-data notes."""

    features: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    missing_data: list[str] = field(default_factory=list)
    provider: str = "native"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Normalize Alpaca-style or conventional OHLCV rows into one dataframe."""
    normalized: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item = {
            "timestamp": row.get("t", row.get("timestamp", row.get("time"))),
            "open": _number(row.get("o", row.get("open", row.get("Open")))),
            "high": _number(row.get("h", row.get("high", row.get("High")))),
            "low": _number(row.get("l", row.get("low", row.get("Low")))),
            "close": _number(row.get("c", row.get("close", row.get("Close")))),
            "volume": _number(row.get("v", row.get("volume", row.get("Volume")))),
        }
        if all(item[name] is not None for name in ("open", "high", "low", "close")):
            normalized.append(item)
    frame = pd.DataFrame(normalized)
    if frame.empty:
        return frame
    frame["volume"] = frame["volume"].fillna(0.0)
    if frame["timestamp"].notna().all():
        parsed = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        if parsed.notna().all():
            frame = frame.assign(_parsed_timestamp=parsed).sort_values(
                "_parsed_timestamp", kind="stable"
            ).drop(columns="_parsed_timestamp")
    return frame.reset_index(drop=True)


def _session_vwap(frame: pd.DataFrame) -> pd.Series:
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    cumulative_volume = frame["volume"].cumsum()
    numerator = (typical * frame["volume"]).cumsum()
    return numerator.div(cumulative_volume.where(cumulative_volume > 0))


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period, min_periods=1).mean()


def _confirmed_swings(frame: pd.DataFrame, radius: int = 3) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return only swings confirmable by bars already present.

    A pivot at i is not emitted until radius bars to its right are available.
    Re-running this on progressively longer prefixes therefore cannot repaint a
    previously confirmed pivot.
    """
    highs: list[dict[str, Any]] = []
    lows: list[dict[str, Any]] = []
    if radius < 1 or len(frame) < radius * 2 + 1:
        return highs, lows
    for i in range(radius, len(frame) - radius):
        left = i - radius
        right = i + radius + 1
        high = float(frame.at[i, "high"])
        low = float(frame.at[i, "low"])
        window_high = float(frame.iloc[left:right]["high"].max())
        window_low = float(frame.iloc[left:right]["low"].min())
        timestamp = frame.at[i, "timestamp"]
        if high >= window_high:
            highs.append({"index": i, "price": high, "timestamp": timestamp, "confirmed_at_index": i + radius})
        if low <= window_low:
            lows.append({"index": i, "price": low, "timestamp": timestamp, "confirmed_at_index": i + radius})
    return highs, lows


def _structure_label(swings: list[dict[str, Any]], *, high: bool) -> str | None:
    if len(swings) < 2:
        return None
    previous = float(swings[-2]["price"])
    current = float(swings[-1]["price"])
    if high:
        return "HH" if current > previous else "LH" if current < previous else "EH"
    return "HL" if current > previous else "LL" if current < previous else "EL"


def _completed_bounces(
    frame: pd.DataFrame,
    swing_highs: list[dict[str, Any]],
    swing_lows: list[dict[str, Any]],
    *,
    max_recent: int = 3,
) -> list[dict[str, Any]]:
    """Build completed low-to-high rebounds from confirmed pivots only.

    This is intentionally a structural bounce counter, not yet a strategy entry
    rule. A bounce is complete only after both the low and the following high
    are confirmed, so historical prefix tests cannot gain information from the
    future.
    """
    bounces: list[dict[str, Any]] = []
    highs = sorted(swing_highs, key=lambda item: int(item["index"]))
    lows = sorted(swing_lows, key=lambda item: int(item["index"]))
    high_cursor = 0
    previous_low_index = -1
    for low in lows:
        low_index = int(low["index"])
        if low_index <= previous_low_index:
            continue
        while high_cursor < len(highs) and int(highs[high_cursor]["index"]) <= low_index:
            high_cursor += 1
        if high_cursor >= len(highs):
            break
        high = highs[high_cursor]
        next_low_index = next(
            (int(candidate["index"]) for candidate in lows if int(candidate["index"]) > low_index),
            None,
        )
        if next_low_index is not None and int(high["index"]) >= next_low_index:
            continue
        low_price = float(low["price"])
        high_price = float(high["price"])
        recovery_pct = ((high_price / low_price) - 1.0) * 100.0 if low_price > 0 else None
        segment = frame.iloc[low_index : int(high["index"]) + 1]
        bounces.append(
            {
                "low_index": low_index,
                "low_price": low_price,
                "high_index": int(high["index"]),
                "high_price": high_price,
                "recovery_pct": recovery_pct,
                "mean_volume": float(segment["volume"].mean()) if not segment.empty else None,
            }
        )
        previous_low_index = low_index
        high_cursor += 1
    return bounces[-max_recent:]


def _bounce_features(bounces: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not bounces:
        return (
            {
                "completed_bounce_count": 0,
                "latest_bounce_number": None,
                "latest_bounce_recovery_pct": None,
                "bounce_deteriorating": None,
                "bounce_strengthening": None,
                "bounce_2_present": False,
                "bounce_3_present": False,
            },
            {"recent_bounces": []},
        )

    recoveries = [
        float(item["recovery_pct"])
        for item in bounces
        if item.get("recovery_pct") is not None
    ]
    deteriorating = None
    strengthening = None
    if len(recoveries) >= 2:
        deteriorating = all(
            current <= previous * 0.85
            for previous, current in zip(recoveries, recoveries[1:])
        )
        strengthening = all(
            current >= previous * 1.15
            for previous, current in zip(recoveries, recoveries[1:])
        )
    numbered = []
    for number, bounce in enumerate(bounces, start=1):
        item = dict(bounce)
        item["number"] = number
        numbered.append(item)
    return (
        {
            "completed_bounce_count": len(numbered),
            "latest_bounce_number": numbered[-1]["number"],
            "latest_bounce_recovery_pct": numbered[-1].get("recovery_pct"),
            "bounce_deteriorating": deteriorating,
            "bounce_strengthening": strengthening,
            "bounce_2_present": len(numbered) >= 2,
            "bounce_3_present": len(numbered) >= 3,
        },
        {"recent_bounces": numbered},
    )


def _stair_step_features(
    swing_highs: list[dict[str, Any]],
    swing_lows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    recent_highs = swing_highs[-3:]
    recent_lows = swing_lows[-3:]
    enough = len(recent_highs) >= 3 and len(recent_lows) >= 3
    if not enough:
        return (
            {"stair_step_up": None, "stair_step_down": None},
            {"recent_highs": recent_highs, "recent_lows": recent_lows},
        )
    high_prices = [float(item["price"]) for item in recent_highs]
    low_prices = [float(item["price"]) for item in recent_lows]
    stair_up = all(b > a for a, b in zip(high_prices, high_prices[1:])) and all(
        b > a for a, b in zip(low_prices, low_prices[1:])
    )
    stair_down = all(b < a for a, b in zip(high_prices, high_prices[1:])) and all(
        b < a for a, b in zip(low_prices, low_prices[1:])
    )
    return (
        {"stair_step_up": stair_up, "stair_step_down": stair_down},
        {"recent_highs": recent_highs, "recent_lows": recent_lows},
    )


def _consolidation_expansion_features(
    frame: pd.DataFrame,
    *,
    base_window: int = 8,
    expansion_bars: int = 2,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Detect a tight recent base followed by an observed expansion.

    The base excludes the current expansion bars. Tightness is normalized by
    ATR so the detector works across different price/volatility regimes.
    """
    required = base_window + expansion_bars
    if len(frame) < required:
        return (
            {
                "consolidation_then_expansion_up": None,
                "consolidation_then_expansion_down": None,
                "base_range_atr_ratio": None,
            },
            {},
        )
    base = frame.iloc[-required:-expansion_bars]
    expansion = frame.iloc[-expansion_bars:]
    base_high = float(base["high"].max())
    base_low = float(base["low"].min())
    base_range = base_high - base_low
    atr = _number(_atr(frame).iloc[-expansion_bars - 1])
    range_atr_ratio = base_range / atr if atr is not None and atr > 0 else None
    tight = bool(range_atr_ratio is not None and range_atr_ratio <= 2.5)
    expansion_up = tight and bool((expansion["close"] > base_high).any())
    expansion_down = tight and bool((expansion["close"] < base_low).any())
    base_volume = float(base["volume"].mean()) if not base.empty else 0.0
    expansion_volume = float(expansion["volume"].mean()) if not expansion.empty else 0.0
    volume_ratio = expansion_volume / base_volume if base_volume > 0 else None
    return (
        {
            "consolidation_then_expansion_up": expansion_up,
            "consolidation_then_expansion_down": expansion_down,
            "base_range_atr_ratio": range_atr_ratio,
            "expansion_volume_ratio": volume_ratio,
        },
        {
            "base_high": base_high,
            "base_low": base_low,
            "base_range": base_range,
            "base_range_atr_ratio": range_atr_ratio,
            "base_mean_volume": base_volume,
            "expansion_mean_volume": expansion_volume,
            "expansion_volume_ratio": volume_ratio,
        },
    )


def _vwap_retest_features(frame: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]]:
    """Describe whether a recent VWAP reclaim survived a causal retest."""
    if len(frame) < 4 or "vwap" not in frame:
        return (
            {
                "vwap_retest_recent": None,
                "vwap_retest_held": None,
                "vwap_retest_failed": None,
                "vwap_retest_distance_atr": None,
            },
            {},
        )

    above = frame["close"] > frame["vwap"]
    reclaim_indices = [
        int(frame.index[i])
        for i in range(1, len(frame))
        if not bool(above.iloc[i - 1]) and bool(above.iloc[i])
    ]
    if not reclaim_indices:
        return (
            {
                "vwap_retest_recent": False,
                "vwap_retest_held": False,
                "vwap_retest_failed": False,
                "vwap_retest_distance_atr": None,
            },
            {"reclaim_index": None},
        )

    reclaim_index = reclaim_indices[-1]
    post = frame.iloc[reclaim_index + 1 :]
    if post.empty:
        return (
            {
                "vwap_retest_recent": False,
                "vwap_retest_held": False,
                "vwap_retest_failed": False,
                "vwap_retest_distance_atr": None,
            },
            {"reclaim_index": reclaim_index},
        )

    atr = _number(frame.at[reclaim_index, "atr"]) or _number(frame["atr"].iloc[-1])
    last_close = float(frame["close"].iloc[-1])
    tolerance = max((atr or 0.0) * 0.20, last_close * 0.0005)
    retest_indices: list[int] = []
    for idx, row in post.iterrows():
        row_vwap = _number(row.get("vwap"))
        if row_vwap is not None and float(row["low"]) <= row_vwap + tolerance:
            retest_indices.append(int(idx))

    if not retest_indices:
        return (
            {
                "vwap_retest_recent": False,
                "vwap_retest_held": False,
                "vwap_retest_failed": False,
                "vwap_retest_distance_atr": None,
            },
            {
                "reclaim_index": reclaim_index,
                "tolerance": tolerance,
                "retest_index": None,
            },
        )

    retest_index = retest_indices[-1]
    retest_row = frame.loc[retest_index]
    retest_vwap = _number(retest_row.get("vwap"))
    distance_atr = None
    if retest_vwap is not None and atr is not None and atr > 0:
        distance_atr = (float(retest_row["low"]) - retest_vwap) / atr

    tail = frame.loc[retest_index:]
    latest_above = bool(float(frame["close"].iloc[-1]) >= float(frame["vwap"].iloc[-1]))
    hold_tail = tail.tail(min(2, len(tail)))
    held = latest_above and bool((hold_tail["close"] >= hold_tail["vwap"]).all())
    failed = not latest_above
    return (
        {
            "vwap_retest_recent": True,
            "vwap_retest_held": held,
            "vwap_retest_failed": failed,
            "vwap_retest_distance_atr": distance_atr,
        },
        {
            "reclaim_index": reclaim_index,
            "retest_index": retest_index,
            "retest_low": float(retest_row["low"]),
            "retest_vwap": retest_vwap,
            "tolerance": tolerance,
            "distance_atr": distance_atr,
            "latest_above_vwap": latest_above,
        },
    )


def _pullback_quality_features(
    frame: pd.DataFrame,
    swing_highs: list[dict[str, Any]],
    swing_lows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Evaluate the latest confirmed high-to-low pullback relative to its prior impulse."""
    if not swing_highs or len(swing_lows) < 2:
        return (
            {
                "pullback_depth_pct_of_impulse": None,
                "pullback_higher_low": None,
                "pullback_volume_ratio": None,
                "pullback_quality": None,
            },
            {},
        )

    latest_low = swing_lows[-1]
    preceding_highs = [item for item in swing_highs if int(item["index"]) < int(latest_low["index"])]
    if not preceding_highs:
        return (
            {
                "pullback_depth_pct_of_impulse": None,
                "pullback_higher_low": None,
                "pullback_volume_ratio": None,
                "pullback_quality": None,
            },
            {},
        )
    prior_high = preceding_highs[-1]
    impulse_lows = [item for item in swing_lows if int(item["index"]) < int(prior_high["index"])]
    if not impulse_lows:
        return (
            {
                "pullback_depth_pct_of_impulse": None,
                "pullback_higher_low": None,
                "pullback_volume_ratio": None,
                "pullback_quality": None,
            },
            {},
        )

    impulse_low = impulse_lows[-1]
    impulse_low_price = float(impulse_low["price"])
    high_price = float(prior_high["price"])
    pullback_low_price = float(latest_low["price"])
    impulse_size = high_price - impulse_low_price
    pullback_size = high_price - pullback_low_price
    depth_ratio = pullback_size / impulse_size if impulse_size > 0 else None

    impulse_segment = frame.iloc[int(impulse_low["index"]) : int(prior_high["index"]) + 1]
    pullback_segment = frame.iloc[int(prior_high["index"]) : int(latest_low["index"]) + 1]
    impulse_volume = float(impulse_segment["volume"].mean()) if not impulse_segment.empty else 0.0
    pullback_volume = float(pullback_segment["volume"].mean()) if not pullback_segment.empty else 0.0
    volume_ratio = pullback_volume / impulse_volume if impulse_volume > 0 else None
    higher_low = pullback_low_price > impulse_low_price

    quality = "weak"
    if depth_ratio is not None:
        if higher_low and 0.15 <= depth_ratio <= 0.55 and (volume_ratio is None or volume_ratio <= 1.0):
            quality = "strong"
        elif higher_low and depth_ratio <= 0.70 and (volume_ratio is None or volume_ratio <= 1.25):
            quality = "acceptable"

    return (
        {
            "pullback_depth_pct_of_impulse": depth_ratio * 100.0 if depth_ratio is not None else None,
            "pullback_higher_low": higher_low,
            "pullback_volume_ratio": volume_ratio,
            "pullback_quality": quality,
        },
        {
            "impulse_low": impulse_low,
            "prior_high": prior_high,
            "pullback_low": latest_low,
            "impulse_size": impulse_size,
            "pullback_size": pullback_size,
            "depth_ratio": depth_ratio,
            "impulse_mean_volume": impulse_volume,
            "pullback_mean_volume": pullback_volume,
            "volume_ratio": volume_ratio,
        },
    )


def _bounce_context_features(bounces: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Add structure/volume context to completed bounce sequences."""
    if len(bounces) < 2:
        return (
            {
                "bounce_sequence_higher_lows": None,
                "bounce_sequence_higher_highs": None,
                "latest_bounce_volume_ratio_vs_prior": None,
                "bounce_structural_weakening": None,
                "bounce_structural_strengthening": None,
            },
            {},
        )

    low_prices = [float(item["low_price"]) for item in bounces]
    high_prices = [float(item["high_price"]) for item in bounces]
    recoveries = [float(item.get("recovery_pct") or 0.0) for item in bounces]
    higher_lows = all(b > a for a, b in zip(low_prices, low_prices[1:]))
    higher_highs = all(b > a for a, b in zip(high_prices, high_prices[1:]))

    prior = bounces[-2]
    latest = bounces[-1]
    prior_volume = _number(prior.get("mean_volume"))
    latest_volume = _number(latest.get("mean_volume"))
    volume_ratio = (
        latest_volume / prior_volume
        if latest_volume is not None and prior_volume is not None and prior_volume > 0
        else None
    )

    weakness_signals = 0
    strength_signals = 0
    if recoveries[-1] <= recoveries[-2] * 0.85:
        weakness_signals += 1
    elif recoveries[-1] >= recoveries[-2] * 1.15:
        strength_signals += 1
    if high_prices[-1] <= high_prices[-2]:
        weakness_signals += 1
    else:
        strength_signals += 1
    if low_prices[-1] <= low_prices[-2]:
        weakness_signals += 1
    else:
        strength_signals += 1
    if volume_ratio is not None:
        if volume_ratio <= 0.75:
            weakness_signals += 1
        elif volume_ratio >= 1.20:
            strength_signals += 1

    return (
        {
            "bounce_sequence_higher_lows": higher_lows,
            "bounce_sequence_higher_highs": higher_highs,
            "latest_bounce_volume_ratio_vs_prior": volume_ratio,
            "bounce_structural_weakening": weakness_signals >= 2,
            "bounce_structural_strengthening": strength_signals >= 3,
        },
        {
            "weakness_signals": weakness_signals,
            "strength_signals": strength_signals,
            "latest_vs_prior_volume_ratio": volume_ratio,
            "low_prices": low_prices,
            "high_prices": high_prices,
            "recoveries": recoveries,
        },
    )


def _breakout_quality_features(
    frame: pd.DataFrame,
    last_swing_high: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Measure breakout hold/failure only after a swing level was confirmed."""
    if not last_swing_high:
        return (
            {
                "breakout_above_last_swing_high": None,
                "failed_breakout_last_swing_high": None,
                "breakout_state": None,
                "breakout_hold_bars": None,
                "breakout_volume_ratio": None,
                "breakout_max_extension_pct": None,
            },
            {},
        )

    level = float(last_swing_high["price"])
    start_index = int(last_swing_high["confirmed_at_index"]) + 1
    after = frame.iloc[start_index:]
    breakout_indices = [int(idx) for idx, value in after["close"].items() if float(value) > level]
    if not breakout_indices:
        return (
            {
                "breakout_above_last_swing_high": False,
                "failed_breakout_last_swing_high": False,
                "breakout_state": "not_broken",
                "breakout_hold_bars": 0,
                "breakout_volume_ratio": None,
                "breakout_max_extension_pct": None,
            },
            {
                "level": level,
                "level_timestamp": last_swing_high.get("timestamp"),
                "first_breakout_index": None,
            },
        )

    first_breakout_index = breakout_indices[0]
    post = frame.loc[first_breakout_index:]
    latest_above = bool(float(frame["close"].iloc[-1]) > level)
    hold_bars = 0
    for value in reversed(post["close"].tolist()):
        if float(value) <= level:
            break
        hold_bars += 1

    state = "failed"
    if latest_above and hold_bars >= 2:
        state = "holding"
    elif latest_above:
        state = "testing"

    before = frame.iloc[max(0, first_breakout_index - 5) : first_breakout_index]
    breakout_window = frame.iloc[first_breakout_index : min(len(frame), first_breakout_index + 2)]
    before_volume = float(before["volume"].mean()) if not before.empty else 0.0
    breakout_volume = float(breakout_window["volume"].mean()) if not breakout_window.empty else 0.0
    volume_ratio = breakout_volume / before_volume if before_volume > 0 else None
    max_extension_pct = ((float(post["high"].max()) / level) - 1.0) * 100.0 if level > 0 else None

    return (
        {
            "breakout_above_last_swing_high": latest_above,
            "failed_breakout_last_swing_high": not latest_above,
            "breakout_state": state,
            "breakout_hold_bars": hold_bars,
            "breakout_volume_ratio": volume_ratio,
            "breakout_max_extension_pct": max_extension_pct,
        },
        {
            "level": level,
            "level_timestamp": last_swing_high.get("timestamp"),
            "first_breakout_index": first_breakout_index,
            "latest_close_above": latest_above,
            "hold_bars": hold_bars,
            "pre_breakout_mean_volume": before_volume,
            "breakout_mean_volume": breakout_volume,
            "volume_ratio": volume_ratio,
            "max_extension_pct": max_extension_pct,
        },
    )



MARKET_FEATURE_COLUMNS: tuple[str, ...] = (
    "bar_count",
    "last_price",
    "session_vwap",
    "price_above_vwap",
    "price_below_vwap",
    "atr",
    "atr_pct",
    "vwap_hold_bars",
    "vwap_reclaim_recent",
    "vwap_rejection_recent",
    "vwap_retest_recent",
    "vwap_retest_held",
    "vwap_retest_failed",
    "vwap_retest_distance_atr",
    "volume_acceleration_ratio",
    "volume_accelerating",
    "volume_contracting",
    "last_swing_high_structure",
    "last_swing_low_structure",
    "confirmed_swing_high_count",
    "confirmed_swing_low_count",
    "uptrend_structure",
    "downtrend_structure",
    "completed_bounce_count",
    "latest_bounce_number",
    "latest_bounce_recovery_pct",
    "bounce_deteriorating",
    "bounce_strengthening",
    "bounce_2_present",
    "bounce_3_present",
    "bounce_sequence_higher_lows",
    "bounce_sequence_higher_highs",
    "latest_bounce_volume_ratio_vs_prior",
    "bounce_structural_weakening",
    "bounce_structural_strengthening",
    "stair_step_up",
    "stair_step_down",
    "consolidation_then_expansion_up",
    "consolidation_then_expansion_down",
    "base_range_atr_ratio",
    "expansion_volume_ratio",
    "pullback_depth_pct_of_impulse",
    "pullback_higher_low",
    "pullback_volume_ratio",
    "pullback_quality",
    "breakout_above_last_swing_high",
    "failed_breakout_last_swing_high",
    "breakout_state",
    "breakout_hold_bars",
    "breakout_volume_ratio",
    "breakout_max_extension_pct",
)


def add_causal_market_feature_columns(
    frame: pd.DataFrame,
    *,
    session_column: str = "session",
    swing_radius: int = 3,
    volume_window: int = 5,
    prior_volume_window: int = 10,
    base_window: int = 8,
    expansion_bars: int = 2,
) -> pd.DataFrame:
    """Attach the live detector vocabulary to every historical bar.

    The implementation advances left-to-right. Confirmed pivots are exposed only
    after their right-side confirmation bars exist, and no feature reads rows
    after the row being populated. This gives backtests and future ML datasets
    the same point-in-time feature semantics as build_market_features.
    """
    if frame.empty:
        return frame.copy()

    data = frame.copy().sort_values("timestamp").reset_index(drop=True)
    if session_column not in data.columns:
        data[session_column] = "session"
    for name in MARKET_FEATURE_COLUMNS:
        if name not in data.columns:
            data[name] = None

    radius = max(1, int(swing_radius))
    recent_n = max(2, int(volume_window))
    prior_n = max(recent_n, int(prior_volume_window))
    base_n = max(2, int(base_window))
    expansion_n = max(1, int(expansion_bars))

    for _, original_group in data.groupby(session_column, sort=False):
        indices = list(original_group.index)
        group = data.loc[indices].copy().reset_index(drop=True)
        if group.empty:
            continue

        group["vwap"] = (
            pd.to_numeric(group["vwap"], errors="coerce")
            if "vwap" in group.columns and group["vwap"].notna().all()
            else _session_vwap(group)
        )
        group["atr"] = _atr(group)
        close = pd.to_numeric(group["close"], errors="coerce")
        volume = pd.to_numeric(group["volume"], errors="coerce").fillna(0.0)

        group["bar_count"] = list(range(1, len(group) + 1))
        group["last_price"] = close
        group["session_vwap"] = group["vwap"]
        group["price_above_vwap"] = close > group["vwap"]
        group["price_below_vwap"] = close < group["vwap"]
        group["atr_pct"] = (
            group["atr"].div(close.where(close > 0)).mul(100.0).round(4)
        )

        above = (close > group["vwap"]).fillna(False)
        previous_above = above.shift(1)
        hold_counts: list[int] = []
        run = 0
        for value in above.tolist():
            run = min(8, run + 1) if bool(value) else 0
            hold_counts.append(run)
        group["vwap_hold_bars"] = hold_counts
        reclaim_event = previous_above.eq(False) & above
        rejection_event = previous_above.eq(True) & ~above
        group["vwap_reclaim_recent"] = (
            reclaim_event.rolling(7, min_periods=1).max().fillna(False).astype(bool)
            & (group["vwap_hold_bars"] >= 2)
        )
        group["vwap_rejection_recent"] = (
            rejection_event.rolling(7, min_periods=1).max().fillna(False).astype(bool)
            & ~above
        )

        recent_mean = volume.rolling(recent_n, min_periods=recent_n).mean()
        prior_mean = volume.shift(recent_n).rolling(prior_n, min_periods=prior_n).mean()
        acceleration = recent_mean.div(prior_mean.where(prior_mean > 0))
        group["volume_acceleration_ratio"] = acceleration
        group["volume_accelerating"] = acceleration.ge(1.5).where(acceleration.notna())
        group["volume_contracting"] = acceleration.le(0.7).where(acceleration.notna())

        base_high = group["high"].shift(expansion_n).rolling(base_n, min_periods=base_n).max()
        base_low = group["low"].shift(expansion_n).rolling(base_n, min_periods=base_n).min()
        base_range = base_high - base_low
        anchor_atr = group["atr"].shift(expansion_n)
        base_ratio = base_range.div(anchor_atr.where(anchor_atr > 0))
        tight = base_ratio.le(2.5) & base_ratio.notna()
        expansion_high_close = close.rolling(expansion_n, min_periods=expansion_n).max()
        expansion_low_close = close.rolling(expansion_n, min_periods=expansion_n).min()
        group["consolidation_then_expansion_up"] = (
            tight & expansion_high_close.gt(base_high)
        ).where(base_high.notna())
        group["consolidation_then_expansion_down"] = (
            tight & expansion_low_close.lt(base_low)
        ).where(base_low.notna())
        group["base_range_atr_ratio"] = base_ratio
        base_mean_volume = volume.shift(expansion_n).rolling(base_n, min_periods=base_n).mean()
        expansion_mean_volume = volume.rolling(expansion_n, min_periods=expansion_n).mean()
        group["expansion_volume_ratio"] = expansion_mean_volume.div(
            base_mean_volume.where(base_mean_volume > 0)
        )

        swing_highs: list[dict[str, Any]] = []
        swing_lows: list[dict[str, Any]] = []
        bounce_features, _ = _bounce_features([])
        bounce_context, _ = _bounce_context_features([])
        stair_features, _ = _stair_step_features([], [])
        pullback_features, _ = _pullback_quality_features(group.iloc[:0], [], [])
        latest_reclaim_index: int | None = None

        active_swing_high: dict[str, Any] | None = None
        first_breakout_index: int | None = None
        breakout_pre_volume: float | None = None
        breakout_max_high: float | None = None
        breakout_hold_bars = 0

        for local_pos in range(len(group)):
            prefix = group.iloc[: local_pos + 1]
            current_close = float(group.at[local_pos, "close"])

            if local_pos >= 1 and not bool(above.iloc[local_pos - 1]) and bool(above.iloc[local_pos]):
                latest_reclaim_index = local_pos
            if local_pos < 3:
                vwap_retest = {
                    "vwap_retest_recent": None,
                    "vwap_retest_held": None,
                    "vwap_retest_failed": None,
                    "vwap_retest_distance_atr": None,
                }
            elif latest_reclaim_index is None:
                vwap_retest = {
                    "vwap_retest_recent": False,
                    "vwap_retest_held": False,
                    "vwap_retest_failed": False,
                    "vwap_retest_distance_atr": None,
                }
            elif latest_reclaim_index >= local_pos:
                vwap_retest = {
                    "vwap_retest_recent": False,
                    "vwap_retest_held": False,
                    "vwap_retest_failed": False,
                    "vwap_retest_distance_atr": None,
                }
            else:
                reclaim_atr = _number(group.at[latest_reclaim_index, "atr"]) or _number(
                    group.at[local_pos, "atr"]
                )
                tolerance = max(
                    (reclaim_atr or 0.0) * 0.20,
                    current_close * 0.0005,
                )
                retest_indices = [
                    idx
                    for idx in range(latest_reclaim_index + 1, local_pos + 1)
                    if float(group.at[idx, "low"])
                    <= float(group.at[idx, "vwap"]) + tolerance
                ]
                if not retest_indices:
                    vwap_retest = {
                        "vwap_retest_recent": False,
                        "vwap_retest_held": False,
                        "vwap_retest_failed": False,
                        "vwap_retest_distance_atr": None,
                    }
                else:
                    retest_index = retest_indices[-1]
                    retest_vwap = _number(group.at[retest_index, "vwap"])
                    distance_atr = (
                        (float(group.at[retest_index, "low"]) - float(retest_vwap))
                        / float(reclaim_atr)
                        if retest_vwap is not None
                        and reclaim_atr is not None
                        and reclaim_atr > 0
                        else None
                    )
                    tail_start = max(retest_index, local_pos - 1)
                    hold_tail = group.iloc[tail_start : local_pos + 1]
                    latest_above = bool(
                        current_close >= float(group.at[local_pos, "vwap"])
                    )
                    held = latest_above and bool(
                        (hold_tail["close"] >= hold_tail["vwap"]).all()
                    )
                    vwap_retest = {
                        "vwap_retest_recent": True,
                        "vwap_retest_held": held,
                        "vwap_retest_failed": not latest_above,
                        "vwap_retest_distance_atr": distance_atr,
                    }
            for name, value in vwap_retest.items():
                group.at[local_pos, name] = value

            confirmed_high_this_row = False
            pivot_changed = False
            pivot_pos = local_pos - radius
            if pivot_pos >= radius:
                left = pivot_pos - radius
                right = pivot_pos + radius + 1
                window = group.iloc[left:right]
                pivot_high = float(group.at[pivot_pos, "high"])
                pivot_low = float(group.at[pivot_pos, "low"])
                if pivot_high >= float(window["high"].max()):
                    item = {
                        "index": pivot_pos,
                        "price": pivot_high,
                        "timestamp": group.at[pivot_pos, "timestamp"],
                        "confirmed_at_index": local_pos,
                    }
                    swing_highs.append(item)
                    active_swing_high = item
                    first_breakout_index = None
                    breakout_pre_volume = None
                    breakout_max_high = None
                    breakout_hold_bars = 0
                    confirmed_high_this_row = True
                    pivot_changed = True
                if pivot_low <= float(window["low"].min()):
                    swing_lows.append(
                        {
                            "index": pivot_pos,
                            "price": pivot_low,
                            "timestamp": group.at[pivot_pos, "timestamp"],
                            "confirmed_at_index": local_pos,
                        }
                    )
                    pivot_changed = True

            if pivot_changed:
                bounces = _completed_bounces(prefix, swing_highs, swing_lows)
                bounce_features, _ = _bounce_features(bounces)
                bounce_context, _ = _bounce_context_features(bounces)
                stair_features, _ = _stair_step_features(swing_highs, swing_lows)
                pullback_features, _ = _pullback_quality_features(
                    prefix, swing_highs, swing_lows
                )

            high_label = _structure_label(swing_highs, high=True)
            low_label = _structure_label(swing_lows, high=False)
            group.at[local_pos, "last_swing_high_structure"] = high_label
            group.at[local_pos, "last_swing_low_structure"] = low_label
            group.at[local_pos, "confirmed_swing_high_count"] = len(swing_highs)
            group.at[local_pos, "confirmed_swing_low_count"] = len(swing_lows)
            group.at[local_pos, "uptrend_structure"] = bool(
                high_label == "HH" and low_label == "HL"
            )
            group.at[local_pos, "downtrend_structure"] = bool(
                high_label == "LH" and low_label == "LL"
            )

            for values in (bounce_features, bounce_context, stair_features, pullback_features):
                for name, value in values.items():
                    group.at[local_pos, name] = value

            if active_swing_high is None:
                breakout_values = {
                    "breakout_above_last_swing_high": None,
                    "failed_breakout_last_swing_high": None,
                    "breakout_state": None,
                    "breakout_hold_bars": None,
                    "breakout_volume_ratio": None,
                    "breakout_max_extension_pct": None,
                }
            else:
                level = float(active_swing_high["price"])
                if (
                    not confirmed_high_this_row
                    and local_pos > int(active_swing_high["confirmed_at_index"])
                    and first_breakout_index is None
                    and current_close > level
                ):
                    first_breakout_index = local_pos
                    before = group.iloc[max(0, local_pos - 5) : local_pos]
                    breakout_pre_volume = (
                        float(before["volume"].mean()) if not before.empty else 0.0
                    )
                    breakout_max_high = float(group.at[local_pos, "high"])

                if first_breakout_index is None:
                    breakout_values = {
                        "breakout_above_last_swing_high": False,
                        "failed_breakout_last_swing_high": False,
                        "breakout_state": "not_broken",
                        "breakout_hold_bars": 0,
                        "breakout_volume_ratio": None,
                        "breakout_max_extension_pct": None,
                    }
                else:
                    breakout_max_high = max(
                        float(breakout_max_high or group.at[local_pos, "high"]),
                        float(group.at[local_pos, "high"]),
                    )
                    if current_close > level:
                        breakout_hold_bars += 1
                    else:
                        breakout_hold_bars = 0
                    latest_above = current_close > level
                    state = (
                        "holding"
                        if latest_above and breakout_hold_bars >= 2
                        else "testing"
                        if latest_above
                        else "failed"
                    )
                    breakout_window_end = min(
                        local_pos + 1,
                        first_breakout_index + 2,
                    )
                    breakout_window = group.iloc[
                        first_breakout_index:breakout_window_end
                    ]
                    breakout_mean_volume = (
                        float(breakout_window["volume"].mean())
                        if not breakout_window.empty
                        else 0.0
                    )
                    volume_ratio = (
                        breakout_mean_volume / breakout_pre_volume
                        if breakout_pre_volume is not None and breakout_pre_volume > 0
                        else None
                    )
                    breakout_values = {
                        "breakout_above_last_swing_high": latest_above,
                        "failed_breakout_last_swing_high": not latest_above,
                        "breakout_state": state,
                        "breakout_hold_bars": breakout_hold_bars,
                        "breakout_volume_ratio": volume_ratio,
                        "breakout_max_extension_pct": (
                            ((float(breakout_max_high) / level) - 1.0) * 100.0
                            if level > 0
                            else None
                        ),
                    }
            for name, value in breakout_values.items():
                group.at[local_pos, name] = value

        for name in MARKET_FEATURE_COLUMNS:
            data.loc[indices, name] = group[name].to_numpy()

    for name in (
        "price_above_vwap",
        "price_below_vwap",
        "vwap_reclaim_recent",
        "vwap_rejection_recent",
        "uptrend_structure",
        "downtrend_structure",
        "bounce_2_present",
        "bounce_3_present",
    ):
        data[name] = data[name].fillna(False).astype(bool)
    return data


def build_market_features(
    rows: list[dict[str, Any]],
    *,
    swing_radius: int = 3,
    volume_window: int = 5,
    prior_volume_window: int = 10,
) -> dict[str, Any]:
    """Build reusable intraday features from candles available at scan time."""
    frame = _frame(rows)
    result = MarketFeatureSnapshot()
    if frame.empty:
        result.missing_data.append("ohlc_bars")
        return result.to_dict()

    if len(frame) < 2:
        result.missing_data.append("bar_history")

    frame["vwap"] = _session_vwap(frame)
    frame["atr"] = _atr(frame)
    last = frame.iloc[-1]
    close = float(last["close"])
    vwap = _number(last["vwap"])
    atr = _number(last["atr"])

    above_vwap = bool(vwap is not None and close > vwap)
    below_vwap = bool(vwap is not None and close < vwap)
    result.features.update(
        {
            "bar_count": int(len(frame)),
            "last_price": close,
            "session_vwap": vwap,
            "price_above_vwap": above_vwap,
            "price_below_vwap": below_vwap,
            "atr": atr,
            "atr_pct": round((atr / close) * 100.0, 4) if atr is not None and close > 0 else None,
        }
    )

    recent = frame.tail(min(8, len(frame))).copy()
    recent["above"] = recent["close"] > recent["vwap"]
    reclaim_index: int | None = None
    rejection_index: int | None = None
    for i in range(1, len(recent)):
        previous = bool(recent.iloc[i - 1]["above"])
        current = bool(recent.iloc[i]["above"])
        if not previous and current:
            reclaim_index = int(recent.index[i])
        elif previous and not current:
            rejection_index = int(recent.index[i])
    bars_above = 0
    for value in reversed(recent["above"].tolist()):
        if not value:
            break
        bars_above += 1
    result.features["vwap_reclaim_recent"] = reclaim_index is not None and bars_above >= 2
    result.features["vwap_rejection_recent"] = rejection_index is not None and not bool(recent.iloc[-1]["above"])
    result.features["vwap_hold_bars"] = bars_above
    result.evidence["vwap"] = {
        "last_price": close,
        "vwap": vwap,
        "reclaim_index": reclaim_index,
        "rejection_index": rejection_index,
        "hold_bars": bars_above,
    }

    vwap_retest_features, vwap_retest_evidence = _vwap_retest_features(frame)
    result.features.update(vwap_retest_features)
    result.evidence["vwap_retest"] = vwap_retest_evidence

    recent_n = max(2, int(volume_window))
    prior_n = max(recent_n, int(prior_volume_window))
    if len(frame) >= recent_n + prior_n:
        recent_rate = float(frame["volume"].iloc[-recent_n:].mean())
        prior_rate = float(frame["volume"].iloc[-(recent_n + prior_n):-recent_n].mean())
        ratio = recent_rate / prior_rate if prior_rate > 0 else None
        result.features["volume_acceleration_ratio"] = ratio
        result.features["volume_accelerating"] = bool(ratio is not None and ratio >= 1.5)
        result.features["volume_contracting"] = bool(ratio is not None and ratio <= 0.7)
        result.evidence["volume_acceleration"] = {
            "recent_mean_volume": recent_rate,
            "prior_mean_volume": prior_rate,
            "ratio": ratio,
        }
    else:
        result.features["volume_acceleration_ratio"] = None
        result.features["volume_accelerating"] = None
        result.features["volume_contracting"] = None
        result.missing_data.append("volume_acceleration_history")

    swings_high, swings_low = _confirmed_swings(frame, radius=swing_radius)
    high_label = _structure_label(swings_high, high=True)
    low_label = _structure_label(swings_low, high=False)
    result.features.update(
        {
            "last_swing_high_structure": high_label,
            "last_swing_low_structure": low_label,
            "confirmed_swing_high_count": len(swings_high),
            "confirmed_swing_low_count": len(swings_low),
            "uptrend_structure": high_label == "HH" and low_label == "HL",
            "downtrend_structure": high_label == "LH" and low_label == "LL",
        }
    )
    result.evidence["market_structure"] = {
        "last_two_swing_highs": swings_high[-2:],
        "last_two_swing_lows": swings_low[-2:],
        "swing_radius": swing_radius,
    }

    bounces = _completed_bounces(frame, swings_high, swings_low)
    bounce_features, bounce_evidence = _bounce_features(bounces)
    result.features.update(bounce_features)
    result.evidence["bounce_sequence"] = bounce_evidence
    if not bounces:
        result.missing_data.append("completed_bounce_sequence")

    bounce_context_features, bounce_context_evidence = _bounce_context_features(bounces)
    result.features.update(bounce_context_features)
    result.evidence["bounce_context"] = bounce_context_evidence

    stair_features, stair_evidence = _stair_step_features(swings_high, swings_low)
    result.features.update(stair_features)
    result.evidence["stair_step"] = stair_evidence
    if stair_features["stair_step_up"] is None:
        result.missing_data.append("stair_step_structure")

    expansion_features, expansion_evidence = _consolidation_expansion_features(frame)
    result.features.update(expansion_features)
    result.evidence["consolidation_expansion"] = expansion_evidence
    if expansion_features["consolidation_then_expansion_up"] is None:
        result.missing_data.append("consolidation_expansion_history")

    pullback_features, pullback_evidence = _pullback_quality_features(
        frame, swings_high, swings_low
    )
    result.features.update(pullback_features)
    result.evidence["pullback_quality"] = pullback_evidence
    if pullback_features["pullback_quality"] is None:
        result.missing_data.append("confirmed_pullback_sequence")

    last_swing_high = swings_high[-1] if swings_high else None
    breakout_features, breakout_evidence = _breakout_quality_features(frame, last_swing_high)
    result.features.update(breakout_features)
    result.evidence["breakout"] = breakout_evidence
    if last_swing_high is None:
        result.missing_data.append("confirmed_swing_high")

    return result.to_dict()


def pyindicators_market_structure(rows: list[dict[str, Any]], *, length: int = 5) -> dict[str, Any] | None:
    """Optional PyIndicators adapter for comparison/validation."""
    frame = _frame(rows)
    if frame.empty:
        return None
    try:
        from pyindicators import market_structure_choch_bos
    except ImportError:
        return None

    external = frame.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    external = market_structure_choch_bos(external, length=length)
    if external.empty:
        return None
    last = external.iloc[-1]
    fields = ("choch_bullish", "choch_bearish", "bos_bullish", "bos_bearish", "market_trend")
    return {name: (last[name].item() if hasattr(last.get(name), "item") else last.get(name)) for name in fields if name in external.columns}
