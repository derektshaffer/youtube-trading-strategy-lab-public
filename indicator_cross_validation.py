"""Independent indicator consistency checks.

The deterministic Trading Lab engine remains the source of truth for its own
definitions. This module compares selected calculations against the MIT-licensed
ta package when the definitions are genuinely equivalent, and uses an
independent reference loop for session-reset VWAP.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
from ta.trend import EMAIndicator
from ta.volatility import AverageTrueRange

from youtube_strategy_engine import add_indicators, bars_to_frame


def _max_abs_difference(left: pd.Series, right: pd.Series) -> float | None:
    pair = pd.concat(
        [
            pd.to_numeric(left, errors="coerce"),
            pd.to_numeric(right, errors="coerce"),
        ],
        axis=1,
    ).dropna()
    if pair.empty:
        return None
    return float((pair.iloc[:, 0] - pair.iloc[:, 1]).abs().max())


def _session_vwap_reference(frame: pd.DataFrame) -> pd.Series:
    result = pd.Series(float("nan"), index=frame.index, dtype="float64")
    for _, group in frame.groupby("session", sort=False):
        cumulative_volume = 0.0
        cumulative_pv = 0.0
        for index, row in group.iterrows():
            volume = max(0.0, float(row["volume"]))
            typical = (
                float(row["high"]) + float(row["low"]) + float(row["close"])
            ) / 3.0
            cumulative_volume += volume
            cumulative_pv += typical * volume
            if cumulative_volume > 0:
                result.at[index] = cumulative_pv / cumulative_volume
    return result


def cross_validate_indicators(
    rows: list[dict[str, Any]],
    *,
    ema_period: int = 9,
    atr_window: int = 14,
    include_extended_hours: bool = False,
    tolerance: float = 1e-9,
) -> dict[str, Any]:
    """Compare the engine against independent equivalent implementations."""
    ema_period = max(2, int(ema_period))
    atr_window = max(2, int(atr_window))
    tolerance = max(1e-12, float(tolerance))

    frame = bars_to_frame(
        rows,
        include_extended_hours=include_extended_hours,
    )
    if frame.empty:
        return {
            "status": "unavailable",
            "reason": "No usable historical bars were available.",
            "checks": {},
        }

    strategy = {"machine_rules": {"fast_ema_period": ema_period}}
    enriched = add_indicators(frame, strategy)
    checks: dict[str, dict[str, Any]] = {}

    ema_differences: list[float] = []
    atr_differences: list[float] = []
    for _, group in enriched.groupby("session", sort=False):
        if len(group) >= ema_period:
            external_ema = EMAIndicator(
                close=group["close"].astype(float),
                window=ema_period,
                fillna=False,
            ).ema_indicator()
            diff = _max_abs_difference(group["fast_ema"], external_ema)
            if diff is not None:
                ema_differences.append(diff)

        if len(group) >= atr_window:
            external_atr = AverageTrueRange(
                high=group["high"].astype(float),
                low=group["low"].astype(float),
                close=group["close"].astype(float),
                window=atr_window,
                fillna=False,
            ).average_true_range()
            valid_external = external_atr.copy()
            valid_external.iloc[: atr_window - 1] = float("nan")
            diff = _max_abs_difference(group["atr_14"], valid_external)
            if diff is not None:
                atr_differences.append(diff)

    ema_max = max(ema_differences) if ema_differences else None
    atr_max = max(atr_differences) if atr_differences else None
    checks["ema"] = {
        "external_reference": "bukosabino/ta EMAIndicator (MIT)",
        "definition": f"EMA({ema_period}), adjust=False",
        "max_abs_difference": ema_max,
        "passed": ema_max is not None and ema_max <= tolerance,
    }
    checks["atr"] = {
        "external_reference": "bukosabino/ta AverageTrueRange (MIT)",
        "definition": f"Wilder ATR({atr_window}), reset per trading session",
        "max_abs_difference": atr_max,
        "passed": atr_max is not None and atr_max <= max(tolerance, 1e-10),
    }

    reference_vwap = _session_vwap_reference(enriched)
    vwap_max = _max_abs_difference(enriched["vwap"], reference_vwap)
    checks["session_vwap"] = {
        "external_reference": "independent reference loop",
        "definition": "session-reset HLC3 volume-weighted average",
        "max_abs_difference": vwap_max,
        "passed": vwap_max is not None and vwap_max <= tolerance,
        "note": (
            "The ta package's VWAP is rolling-window based, so it is not used as "
            "an oracle for the Lab's session-reset VWAP."
        ),
    }

    passed = all(bool(item.get("passed")) for item in checks.values())
    finite_differences = [
        float(item["max_abs_difference"])
        for item in checks.values()
        if item.get("max_abs_difference") is not None
        and math.isfinite(float(item["max_abs_difference"]))
    ]
    return {
        "status": "passed" if passed else "mismatch",
        "passed": passed,
        "checks": checks,
        "maximum_observed_difference": (
            max(finite_differences) if finite_differences else None
        ),
        "bar_count": int(len(enriched)),
        "session_count": int(enriched["session"].nunique()),
        "policy": (
            "Only mathematically equivalent definitions are compared. A semantic "
            "difference between indicators is not treated as a calculation error."
        ),
    }
