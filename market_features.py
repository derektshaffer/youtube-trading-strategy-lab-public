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

    last_swing_high = swings_high[-1] if swings_high else None
    if last_swing_high:
        level = float(last_swing_high["price"])
        after = frame.iloc[int(last_swing_high["confirmed_at_index"]) + 1 :]
        broke = bool(not after.empty and (after["close"] > level).any())
        latest_above = close > level
        result.features["breakout_above_last_swing_high"] = broke and latest_above
        result.features["failed_breakout_last_swing_high"] = broke and not latest_above
        result.evidence["breakout"] = {
            "level": level,
            "level_timestamp": last_swing_high.get("timestamp"),
            "ever_closed_above_after_confirmation": broke,
            "latest_close_above": latest_above,
        }
    else:
        result.features["breakout_above_last_swing_high"] = None
        result.features["failed_breakout_last_swing_high"] = None
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
