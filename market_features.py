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



MARKET_FEATURE_COLUMNS: tuple[str, ...] = (
    "atr_pct",
    "vwap_hold_bars",
    "vwap_reclaim_recent",
    "vwap_rejection_recent",
    "volume_acceleration_ratio",
    "volume_accelerating",
    "volume_contracting",
    "last_swing_high_structure",
    "last_swing_low_structure",
    "uptrend_structure",
    "downtrend_structure",
    "breakout_above_last_swing_high",
    "failed_breakout_last_swing_high",
)


def add_causal_market_feature_columns(
    frame: pd.DataFrame,
    *,
    session_column: str = "session",
    swing_radius: int = 3,
    volume_window: int = 5,
    prior_volume_window: int = 10,
) -> pd.DataFrame:
    """Attach reusable point-in-time market features to every historical bar.

    Each row is computed only from that row and earlier information. Confirmed
    swing structure is delayed until the required right-side bars have actually
    occurred, so historical feature rows can be used for backtesting and future
    ML datasets without repainting.
    """
    if frame.empty:
        return frame.copy()
    data = frame.copy().sort_values("timestamp").reset_index(drop=True)
    if session_column not in data.columns:
        data[session_column] = "session"

    session = data.groupby(session_column, sort=False)
    if "vwap" not in data.columns:
        typical = (data["high"] + data["low"] + data["close"]) / 3.0
        cumulative_volume = session["volume"].cumsum()
        numerator = (typical * data["volume"]).groupby(data[session_column], sort=False).cumsum()
        data["vwap"] = numerator.div(cumulative_volume.where(cumulative_volume > 0))

    if "atr_14" in data.columns:
        atr = pd.to_numeric(data["atr_14"], errors="coerce")
    else:
        atr = pd.Series(float("nan"), index=data.index, dtype="float64")
        for _, group in data.groupby(session_column, sort=False):
            atr.loc[group.index] = _atr(group, period=14).to_numpy()
    data["atr_pct"] = atr.div(data["close"].where(data["close"] > 0)).mul(100.0)

    above = data["close"] > data["vwap"]
    previous_above = above.groupby(data[session_column], sort=False).shift(1)
    hold_bars = pd.Series(0, index=data.index, dtype="int64")
    for _, group in data.groupby(session_column, sort=False):
        group_above = above.loc[group.index].astype(bool)
        run_group = (~group_above).cumsum()
        hold_bars.loc[group.index] = group_above.astype(int).groupby(run_group).cumsum().to_numpy()
    data["vwap_hold_bars"] = hold_bars

    reclaim_event = previous_above.eq(False) & above
    rejection_event = previous_above.eq(True) & ~above
    recent_reclaim = reclaim_event.groupby(data[session_column], sort=False).transform(
        lambda values: values.rolling(8, min_periods=1).max()
    ).fillna(False).astype(bool)
    recent_rejection = rejection_event.groupby(data[session_column], sort=False).transform(
        lambda values: values.rolling(8, min_periods=1).max()
    ).fillna(False).astype(bool)
    data["vwap_reclaim_recent"] = recent_reclaim & (data["vwap_hold_bars"] >= 2)
    data["vwap_rejection_recent"] = recent_rejection & ~above

    recent_n = max(2, int(volume_window))
    prior_n = max(recent_n, int(prior_volume_window))
    recent_mean = session["volume"].transform(
        lambda values: values.rolling(recent_n, min_periods=recent_n).mean()
    )
    prior_mean = session["volume"].transform(
        lambda values: values.shift(recent_n).rolling(prior_n, min_periods=prior_n).mean()
    )
    ratio = recent_mean.div(prior_mean.where(prior_mean > 0))
    data["volume_acceleration_ratio"] = ratio
    data["volume_accelerating"] = ratio.ge(1.5).where(ratio.notna())
    data["volume_contracting"] = ratio.le(0.7).where(ratio.notna())

    for name in (
        "last_swing_high_structure",
        "last_swing_low_structure",
        "uptrend_structure",
        "downtrend_structure",
        "breakout_above_last_swing_high",
        "failed_breakout_last_swing_high",
    ):
        data[name] = None if "structure" in name else False

    radius = max(1, int(swing_radius))
    for _, group in data.groupby(session_column, sort=False):
        indices = list(group.index)
        highs: list[float] = []
        lows: list[float] = []
        active_swing_high: float | None = None
        broke_active_high = False

        for local_pos, row_index in enumerate(indices):
            pivot_pos = local_pos - radius
            if pivot_pos >= radius:
                left = pivot_pos - radius
                right = pivot_pos + radius + 1
                window_indices = indices[left:right]
                pivot_index = indices[pivot_pos]
                pivot_high = float(data.at[pivot_index, "high"])
                pivot_low = float(data.at[pivot_index, "low"])
                window_high = float(data.loc[window_indices, "high"].max())
                window_low = float(data.loc[window_indices, "low"].min())
                if pivot_high >= window_high:
                    highs.append(pivot_high)
                    active_swing_high = pivot_high
                    broke_active_high = False
                if pivot_low <= window_low:
                    lows.append(pivot_low)

            high_label = None
            low_label = None
            if len(highs) >= 2:
                high_label = "HH" if highs[-1] > highs[-2] else "LH" if highs[-1] < highs[-2] else "EH"
            if len(lows) >= 2:
                low_label = "HL" if lows[-1] > lows[-2] else "LL" if lows[-1] < lows[-2] else "EL"

            if active_swing_high is not None and float(data.at[row_index, "close"]) > active_swing_high:
                broke_active_high = True

            latest_above = bool(
                active_swing_high is not None
                and float(data.at[row_index, "close"]) > active_swing_high
            )
            data.at[row_index, "last_swing_high_structure"] = high_label
            data.at[row_index, "last_swing_low_structure"] = low_label
            data.at[row_index, "uptrend_structure"] = bool(high_label == "HH" and low_label == "HL")
            data.at[row_index, "downtrend_structure"] = bool(high_label == "LH" and low_label == "LL")
            data.at[row_index, "breakout_above_last_swing_high"] = bool(broke_active_high and latest_above)
            data.at[row_index, "failed_breakout_last_swing_high"] = bool(broke_active_high and not latest_above)

    for name in (
        "uptrend_structure",
        "downtrend_structure",
        "breakout_above_last_swing_high",
        "failed_breakout_last_swing_high",
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
