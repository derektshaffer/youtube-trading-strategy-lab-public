"""Causal volume-profile and volume-exhaustion features.

Every row is computed only from the current bar and earlier bars in the same
session. The profile is a bar-data approximation: each candle's volume is
assigned to its HLC3 price bin because historical price-at-volume prints are
not available in ordinary OHLCV bars.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _value_area(
    volumes: np.ndarray,
    edges: np.ndarray,
    *,
    target_fraction: float,
) -> tuple[float, float, int]:
    if len(volumes) == 0 or float(volumes.sum()) <= 0:
        return float("nan"), float("nan"), -1
    poc = int(np.argmax(volumes))
    target = float(volumes.sum()) * target_fraction
    included = float(volumes[poc])
    left = poc
    right = poc
    while included < target and (left > 0 or right < len(volumes) - 1):
        left_volume = float(volumes[left - 1]) if left > 0 else -1.0
        right_volume = (
            float(volumes[right + 1]) if right < len(volumes) - 1 else -1.0
        )
        if right_volume > left_volume:
            right += 1
            included += max(0.0, right_volume)
        else:
            left -= 1
            included += max(0.0, left_volume)
    return float(edges[left]), float(edges[right + 1]), poc


def volume_profile_snapshot(
    history: pd.DataFrame,
    *,
    bins: int = 24,
    value_area_pct: float = 0.70,
) -> dict[str, Any]:
    """Return one causal volume-profile snapshot for an already-cut-off history."""
    if history.empty:
        return {}
    bins = min(80, max(8, int(bins)))
    value_area_pct = min(0.95, max(0.50, float(value_area_pct)))

    high = pd.to_numeric(history["high"], errors="coerce")
    low = pd.to_numeric(history["low"], errors="coerce")
    close = pd.to_numeric(history["close"], errors="coerce")
    volume = pd.to_numeric(history["volume"], errors="coerce").fillna(0).clip(lower=0)
    valid = high.notna() & low.notna() & close.notna() & volume.notna()
    if not bool(valid.any()):
        return {}

    typical = ((high + low + close) / 3.0)[valid].astype(float)
    weights = volume[valid].astype(float)
    price_min = float(low[valid].min())
    price_max = float(high[valid].max())
    current_close = float(close[valid].iloc[-1])
    if price_max <= price_min or float(weights.sum()) <= 0:
        return {}

    weighted, edges = np.histogram(
        typical.to_numpy(),
        bins=bins,
        range=(price_min, price_max),
        weights=weights.to_numpy(),
    )
    value_low, value_high, poc_index = _value_area(
        weighted.astype(float),
        edges.astype(float),
        target_fraction=value_area_pct,
    )
    if poc_index < 0:
        return {}

    centers = (edges[:-1] + edges[1:]) / 2.0
    poc = float(centers[poc_index])
    total_volume = float(weighted.sum())
    shares = weighted.astype(float) / total_volume if total_volume > 0 else weighted
    positive = shares[shares > 0]
    entropy = (
        float(-(positive * np.log(positive)).sum() / math.log(len(weighted)))
        if len(positive) > 1 and len(weighted) > 1
        else 0.0
    )
    if current_close < value_low:
        location = -1.0
    elif current_close > value_high:
        location = 1.0
    else:
        location = 0.0

    return {
        "vp_poc": poc,
        "vp_value_area_low": value_low,
        "vp_value_area_high": value_high,
        "vp_distance_to_poc_pct": (
            ((current_close / poc) - 1.0) * 100.0 if poc > 0 else None
        ),
        "vp_value_area_location": location,
        "vp_poc_volume_share": (
            float(weighted[poc_index]) / total_volume if total_volume > 0 else None
        ),
        "vp_value_area_width_pct": (
            ((value_high - value_low) / current_close) * 100.0
            if current_close > 0
            else None
        ),
        "vp_profile_entropy": entropy,
    }


def _exhaustion_features(history: pd.DataFrame) -> dict[str, float | None]:
    if history.empty:
        return {}
    row = history.iloc[-1]
    close = float(row["close"])
    high = float(row["high"])
    low = float(row["low"])
    volume = float(row["volume"])
    bar_range = max(0.0, high - low)
    close_location = (close - low) / bar_range if bar_range > 0 else 0.5
    upper_wick = (high - max(float(row["open"]), close)) / bar_range if bar_range > 0 else 0.0
    lower_wick = (min(float(row["open"]), close) - low) / bar_range if bar_range > 0 else 0.0

    prior = history.iloc[max(0, len(history) - 21) : -1]
    prior_volume = pd.to_numeric(prior.get("volume"), errors="coerce").dropna()
    prior_range = (
        pd.to_numeric(prior.get("high"), errors="coerce")
        - pd.to_numeric(prior.get("low"), errors="coerce")
    ).dropna()
    volume_ratio = (
        volume / float(prior_volume.median())
        if len(prior_volume) >= 5 and float(prior_volume.median()) > 0
        else None
    )
    range_ratio = (
        bar_range / float(prior_range.median())
        if len(prior_range) >= 5 and float(prior_range.median()) > 0
        else None
    )

    def pressure(direction: str) -> float | None:
        if volume_ratio is None or range_ratio is None:
            return None
        volume_component = min(1.0, max(0.0, (volume_ratio - 1.0) / 2.0))
        range_component = min(1.0, max(0.0, (range_ratio - 1.0) / 1.5))
        if direction == "upper":
            wick_component = min(1.0, max(0.0, upper_wick / 0.40))
            rejection_component = 1.0 - close_location
        else:
            wick_component = min(1.0, max(0.0, lower_wick / 0.40))
            rejection_component = close_location
        return round(
            100.0
            * (
                0.35 * volume_component
                + 0.25 * range_component
                + 0.25 * wick_component
                + 0.15 * rejection_component
            ),
            4,
        )

    return {
        "volume_climax_ratio": volume_ratio,
        "range_expansion_ratio": range_ratio,
        "upper_wick_fraction": upper_wick,
        "lower_wick_fraction": lower_wick,
        "upper_exhaustion_pressure": pressure("upper"),
        "lower_exhaustion_pressure": pressure("lower"),
    }


def apply_causal_volume_profile_features(
    frame: pd.DataFrame,
    *,
    lookback_bars: int = 60,
    bins: int = 24,
    value_area_pct: float = 0.70,
    minimum_bars: int = 10,
) -> pd.DataFrame:
    """Attach trailing profile/exhaustion features without future leakage."""
    data = frame.copy().sort_values("timestamp").reset_index(drop=True)
    output_columns = (
        "vp_poc",
        "vp_value_area_low",
        "vp_value_area_high",
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
    )
    for column in output_columns:
        data[column] = float("nan")

    lookback_bars = max(minimum_bars, int(lookback_bars))
    minimum_bars = max(3, int(minimum_bars))
    for _, session in data.groupby("session", sort=False):
        positions = list(session.index)
        for local_pos, global_pos in enumerate(positions):
            start = max(0, local_pos - lookback_bars + 1)
            window_positions = positions[start : local_pos + 1]
            if len(window_positions) < minimum_bars:
                continue
            history = data.loc[window_positions]
            features = {
                **volume_profile_snapshot(
                    history,
                    bins=bins,
                    value_area_pct=value_area_pct,
                ),
                **_exhaustion_features(history),
            }
            for name, raw in features.items():
                number = _finite(raw)
                if number is not None:
                    data.at[global_pos, name] = number
    return data
