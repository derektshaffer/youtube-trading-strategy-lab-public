"""Pure session-aware feature and outcome helpers for the Machine Learning Lab."""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_session_aware_ml_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add rolling ML features without carrying windows across market sessions."""
    data = frame.copy()
    required = {"session", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(data.columns))
    if missing:
        raise ValueError("ML feature frame is missing: " + ", ".join(missing))

    open_all = pd.to_numeric(data["open"], errors="coerce")
    high_all = pd.to_numeric(data["high"], errors="coerce")
    low_all = pd.to_numeric(data["low"], errors="coerce")
    close_all = pd.to_numeric(data["close"], errors="coerce")

    data["range_pct"] = (high_all - low_all).div(close_all.replace(0, np.nan)) * 100.0
    data["body_pct"] = (close_all - open_all).div(open_all.replace(0, np.nan)) * 100.0

    rolling_columns = (
        "return_1",
        "return_3",
        "return_12",
        "atr_14_pct",
        "rsi_14",
        "ema_8_gap_pct",
        "ema_21_gap_pct",
        "ema_8_21_spread_pct",
        "rolling_volatility_20",
        "volume_z20",
        "overnight_gap_pct",
    )
    for column in rolling_columns:
        data[column] = np.nan

    previous_session_close: float | None = None
    for _, session in data.groupby("session", sort=False):
        idx = session.index
        close = pd.to_numeric(session["close"], errors="coerce")
        open_ = pd.to_numeric(session["open"], errors="coerce")
        high = pd.to_numeric(session["high"], errors="coerce")
        low = pd.to_numeric(session["low"], errors="coerce")
        volume = pd.to_numeric(session["volume"], errors="coerce")

        return_1 = close.pct_change() * 100.0
        data.loc[idx, "return_1"] = return_1
        data.loc[idx, "return_3"] = close.pct_change(3) * 100.0
        data.loc[idx, "return_12"] = close.pct_change(12) * 100.0

        previous_close = close.shift(1)
        true_range = pd.concat(
            [
                high - low,
                (high - previous_close).abs(),
                (low - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = true_range.rolling(14, min_periods=5).mean()
        data.loc[idx, "atr_14_pct"] = atr.div(close.replace(0, np.nan)) * 100.0

        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14, min_periods=5).mean()
        loss = (-delta.clip(upper=0)).rolling(14, min_periods=5).mean()
        rs = gain.div(loss.replace(0, np.nan))
        rsi = 100.0 - (100.0 / (1.0 + rs))
        rsi.loc[(loss == 0) & (gain > 0)] = 100.0
        rsi.loc[(loss == 0) & (gain == 0)] = 50.0
        data.loc[idx, "rsi_14"] = rsi

        ema8 = close.ewm(span=8, adjust=False, min_periods=3).mean()
        ema21 = close.ewm(span=21, adjust=False, min_periods=5).mean()
        data.loc[idx, "ema_8_gap_pct"] = (
            close / ema8.replace(0, np.nan) - 1.0
        ) * 100.0
        data.loc[idx, "ema_21_gap_pct"] = (
            close / ema21.replace(0, np.nan) - 1.0
        ) * 100.0
        data.loc[idx, "ema_8_21_spread_pct"] = (
            ema8 / ema21.replace(0, np.nan) - 1.0
        ) * 100.0

        data.loc[idx, "rolling_volatility_20"] = return_1.rolling(
            20, min_periods=6
        ).std()
        rolling_volume_mean = volume.shift(1).rolling(20, min_periods=5).mean()
        rolling_volume_std = volume.shift(1).rolling(20, min_periods=5).std()
        data.loc[idx, "volume_z20"] = (
            volume - rolling_volume_mean
        ).div(rolling_volume_std.replace(0, np.nan))

        first_open = open_.iloc[0] if len(open_) else np.nan
        if (
            previous_session_close is not None
            and np.isfinite(previous_session_close)
            and np.isfinite(first_open)
            and previous_session_close > 0
        ):
            gap = (float(first_open) / previous_session_close - 1.0) * 100.0
            data.loc[idx, "overnight_gap_pct"] = gap

        valid_closes = close.dropna()
        if not valid_closes.empty:
            previous_session_close = float(valid_closes.iloc[-1])

    return data


def add_session_outcome_labels(
    frame: pd.DataFrame,
    *,
    stop_pct: float,
    reward_risk: float,
    horizon_bars: int,
    same_bar_policy: str = "ambiguous_exclude",
    require_full_horizon: bool = True,
) -> pd.DataFrame:
    """Label next-bar entries using only later bars from the same session."""
    policy = str(same_bar_policy or "ambiguous_exclude").strip().lower()
    if policy not in {"ambiguous_exclude", "stop_first_conservative"}:
        raise ValueError(
            "same_bar_policy must be 'ambiguous_exclude' or 'stop_first_conservative'."
        )

    data = frame.copy().reset_index(drop=True)
    labels = np.full(len(data), np.nan, dtype=float)
    outcome_return = np.full(len(data), np.nan, dtype=float)

    opens = pd.to_numeric(data["open"], errors="coerce").to_numpy(dtype=float)
    highs = pd.to_numeric(data["high"], errors="coerce").to_numpy(dtype=float)
    lows = pd.to_numeric(data["low"], errors="coerce").to_numpy(dtype=float)
    closes = pd.to_numeric(data["close"], errors="coerce").to_numpy(dtype=float)
    sessions = data["session"].astype(str).to_numpy()

    stop_fraction = float(stop_pct) / 100.0
    horizon = max(1, int(horizon_bars))

    for signal_index in range(len(data) - 1):
        entry_index = signal_index + 1
        if sessions[entry_index] != sessions[signal_index]:
            continue
        entry = opens[entry_index]
        if not np.isfinite(entry) or entry <= 0:
            continue

        future_indices: list[int] = []
        for idx in range(entry_index, min(len(data), entry_index + horizon)):
            if sessions[idx] != sessions[entry_index]:
                break
            future_indices.append(idx)
        if require_full_horizon and len(future_indices) < horizon:
            continue
        if not future_indices:
            continue

        stop_price = entry * (1.0 - stop_fraction)
        target_price = entry + (entry - stop_price) * float(reward_risk)
        resolved = False

        for idx in future_indices:
            hit_stop = lows[idx] <= stop_price
            hit_target = highs[idx] >= target_price
            if hit_stop and hit_target:
                resolved = True
                if policy == "stop_first_conservative":
                    labels[signal_index] = 0.0
                    outcome_return[signal_index] = (
                        stop_price / entry - 1.0
                    ) * 100.0
                break
            if hit_stop:
                labels[signal_index] = 0.0
                outcome_return[signal_index] = (stop_price / entry - 1.0) * 100.0
                resolved = True
                break
            if hit_target:
                labels[signal_index] = 1.0
                outcome_return[signal_index] = (
                    target_price / entry - 1.0
                ) * 100.0
                resolved = True
                break

        if not resolved:
            final_index = future_indices[-1]
            if np.isfinite(closes[final_index]):
                final_return = (closes[final_index] / entry - 1.0) * 100.0
                labels[signal_index] = 1.0 if final_return > 0 else 0.0
                outcome_return[signal_index] = final_return

    data["profitable_outcome"] = labels
    data["outcome_return_pct"] = outcome_return
    return data
