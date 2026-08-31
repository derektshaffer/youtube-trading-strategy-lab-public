"""Reusable machine-learning filter for the simplified Trading Dashboard.

This module extracts the useful part of the Machine Learning Lab into a callable
function. It never submits brokerage orders.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from youtube_strategy_engine import (
    AppError,
    add_indicators,
    bars_to_frame,
    evaluate_signal,
    normalize_machine_rules,
    safe_float,
    split_safe_raw_research_rows,
    utc_now,
)

FEATURE_COLUMNS = [
    "return_1",
    "return_3",
    "return_12",
    "range_pct",
    "body_pct",
    "atr_14_pct",
    "rsi_14",
    "ema_8_gap_pct",
    "ema_21_gap_pct",
    "ema_8_21_spread_pct",
    "rolling_volatility_20",
    "relative_volume",
    "volume_surge",
    "volume_z20",
    "day_change_pct",
    "vwap_distance_signed_pct",
    "green_streak",
    "session_progress",
    "log_cum_dollar_volume",
    "breakout_gap_pct",
    "opening_range_gap_pct",
    "strategy_match",
]


def add_ml_features(frame: pd.DataFrame, strategy: dict[str, Any]) -> pd.DataFrame:
    data = add_indicators(frame, strategy).copy()
    close = pd.to_numeric(data["close"], errors="coerce")
    open_ = pd.to_numeric(data["open"], errors="coerce")
    high = pd.to_numeric(data["high"], errors="coerce")
    low = pd.to_numeric(data["low"], errors="coerce")
    volume = pd.to_numeric(data["volume"], errors="coerce")

    data["return_1"] = close.pct_change() * 100.0
    data["return_3"] = close.pct_change(3) * 100.0
    data["return_12"] = close.pct_change(12) * 100.0
    data["range_pct"] = (high - low).div(close.replace(0, np.nan)) * 100.0
    data["body_pct"] = (close - open_).div(open_.replace(0, np.nan)) * 100.0

    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(14, min_periods=5).mean()
    data["atr_14_pct"] = atr.div(close.replace(0, np.nan)) * 100.0

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=5).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=5).mean()
    rs = gain.div(loss.replace(0, np.nan))
    data["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))
    data.loc[(loss == 0) & (gain > 0), "rsi_14"] = 100.0
    data.loc[(loss == 0) & (gain == 0), "rsi_14"] = 50.0

    ema8 = close.ewm(span=8, adjust=False, min_periods=3).mean()
    ema21 = close.ewm(span=21, adjust=False, min_periods=5).mean()
    data["ema_8_gap_pct"] = (close / ema8.replace(0, np.nan) - 1.0) * 100.0
    data["ema_21_gap_pct"] = (close / ema21.replace(0, np.nan) - 1.0) * 100.0
    data["ema_8_21_spread_pct"] = (ema8 / ema21.replace(0, np.nan) - 1.0) * 100.0
    data["rolling_volatility_20"] = data["return_1"].rolling(20, min_periods=6).std()

    rolling_volume_mean = volume.shift(1).rolling(20, min_periods=5).mean()
    rolling_volume_std = volume.shift(1).rolling(20, min_periods=5).std()
    data["volume_z20"] = (volume - rolling_volume_mean).div(
        rolling_volume_std.replace(0, np.nan)
    )

    if "vwap" in data:
        vwap = pd.to_numeric(data["vwap"], errors="coerce")
        data["vwap_distance_signed_pct"] = (
            close / vwap.replace(0, np.nan) - 1.0
        ) * 100.0
    else:
        data["vwap_distance_signed_pct"] = np.nan

    data["session_progress"] = (
        pd.to_numeric(data["session_minute"], errors="coerce")
        .clip(lower=0, upper=389)
        / 389.0
    )
    cumulative_dollars = pd.to_numeric(data.get("cum_dollar_volume"), errors="coerce")
    data["log_cum_dollar_volume"] = np.log1p(cumulative_dollars.clip(lower=0))

    prior_breakout = pd.to_numeric(data.get("prior_breakout_high"), errors="coerce")
    opening_high = pd.to_numeric(data.get("opening_range_high"), errors="coerce")
    data["breakout_gap_pct"] = (
        close / prior_breakout.replace(0, np.nan) - 1.0
    ) * 100.0
    data["opening_range_gap_pct"] = (
        close / opening_high.replace(0, np.nan) - 1.0
    ) * 100.0

    rules = normalize_machine_rules(strategy.get("machine_rules"))
    data["strategy_match"] = [
        1.0 if evaluate_signal(row, rules) else 0.0
        for _, row in data.iterrows()
    ]
    return data


def add_outcome_labels(
    frame: pd.DataFrame,
    *,
    stop_pct: float,
    reward_risk: float,
    horizon_bars: int,
) -> pd.DataFrame:
    data = frame.copy().reset_index(drop=True)
    labels = np.full(len(data), np.nan, dtype=float)

    opens = pd.to_numeric(data["open"], errors="coerce").to_numpy(dtype=float)
    highs = pd.to_numeric(data["high"], errors="coerce").to_numpy(dtype=float)
    lows = pd.to_numeric(data["low"], errors="coerce").to_numpy(dtype=float)
    closes = pd.to_numeric(data["close"], errors="coerce").to_numpy(dtype=float)
    sessions = data["session"].astype(str).to_numpy()

    stop_fraction = stop_pct / 100.0
    horizon = max(1, int(horizon_bars))

    for signal_index in range(len(data) - 1):
        entry_index = signal_index + 1
        if sessions[entry_index] != sessions[signal_index]:
            continue
        entry = opens[entry_index]
        if not np.isfinite(entry) or entry <= 0:
            continue

        stop_price = entry * (1.0 - stop_fraction)
        target_price = entry + (entry - stop_price) * reward_risk
        final_index = entry_index
        resolved = False

        for idx in range(entry_index, min(len(data), entry_index + horizon)):
            if sessions[idx] != sessions[entry_index]:
                break
            final_index = idx
            if lows[idx] <= stop_price:
                labels[signal_index] = 0.0
                resolved = True
                break
            if highs[idx] >= target_price:
                labels[signal_index] = 1.0
                resolved = True
                break

        if not resolved and final_index >= entry_index and np.isfinite(closes[final_index]):
            labels[signal_index] = 1.0 if closes[final_index] > entry else 0.0

    data["profitable_outcome"] = labels
    return data


def build_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                RandomForestClassifier(
                    n_estimators=260,
                    max_depth=10,
                    min_samples_leaf=8,
                    max_features="sqrt",
                    class_weight="balanced_subsample",
                    random_state=42,
                    n_jobs=-1,
                ),
            ),
        ]
    )


def _walk_forward_probabilities(
    model_data: pd.DataFrame,
    feature_columns: list[str],
    *,
    folds: int = 4,
) -> pd.DataFrame:
    sessions = list(dict.fromkeys(model_data["session"].astype(str).tolist()))
    if len(sessions) < 10:
        raise AppError(
            "The historical pattern check needs at least 10 completed trading sessions."
        )

    initial_train = max(6, int(len(sessions) * 0.50))
    remaining = len(sessions) - initial_train
    if remaining < folds:
        raise AppError("Not enough completed sessions for chronological model testing.")
    fold_size = max(1, remaining // folds)
    predictions: list[pd.DataFrame] = []

    for fold in range(folds):
        test_start = initial_train + fold * fold_size
        test_end = (
            len(sessions)
            if fold == folds - 1
            else min(len(sessions), test_start + fold_size)
        )
        if test_start >= len(sessions):
            break

        train_sessions = sessions[: max(0, test_start - 1)]
        test_sessions = sessions[test_start:test_end]
        train = model_data[
            model_data["session"].astype(str).isin(train_sessions)
        ]
        test = model_data[
            model_data["session"].astype(str).isin(test_sessions)
        ]
        if (
            len(train) < 150
            or len(test) < 20
            or train["profitable_outcome"].nunique() < 2
        ):
            continue

        pipeline = build_pipeline()
        pipeline.fit(
            train[feature_columns],
            train["profitable_outcome"].astype(int),
        )
        probability = pipeline.predict_proba(test[feature_columns])[:, 1]
        out = test[["session", "strategy_match", "profitable_outcome"]].copy()
        out["ml_probability"] = probability
        predictions.append(out)

    if not predictions:
        raise AppError(
            "The available history could not produce a reliable chronological ML test. "
            "Try more history or a different ticker."
        )
    return pd.concat(predictions, ignore_index=True)


def score_setup(
    market: Any,
    symbol: str,
    strategy: dict[str, Any],
    *,
    timeframe: str = "5Min",
    history_days: int = 60,
    threshold: float = 0.65,
) -> dict[str, Any]:
    """Train/test the saved strategy's ML filter and score the latest available bar."""
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        raise AppError("Enter a ticker before running the historical pattern check.")

    rules = normalize_machine_rules(strategy.get("machine_rules"))
    stop_pct = safe_float(rules.get("stop_loss_pct"), 2.0) or 2.0
    reward_risk = safe_float(rules.get("reward_risk"), 2.0) or 2.0
    hold_minutes = int(safe_float(rules.get("max_hold_minutes"), 60) or 60)
    timeframe_minutes = {"1Min": 1, "5Min": 5, "15Min": 15}.get(timeframe, 5)
    horizon_bars = max(1, int(np.ceil(hold_minutes / timeframe_minutes)))

    historical_end = utc_now() - timedelta(minutes=1)
    historical_start = historical_end - timedelta(days=max(10, int(history_days)))
    bars = market.bars(
        [ticker],
        start=historical_start,
        end=historical_end,
        timeframe=timeframe,
        feed=market.live_feed,
        adjustment="raw",
        max_pages=30,
    ).get(ticker, [])
    if not hasattr(market, "research_reset_actions"):
        raise AppError(
            "Historical ML scoring requires split metadata so price/liquidity "
            "features cannot cross an unhandled split boundary."
        )
    split_actions = market.research_reset_actions(
        [ticker],
        start=historical_start,
        end=historical_end,
    )
    bars, market_data_integrity = split_safe_raw_research_rows(
        list(bars or []),
        split_actions,
        ticker,
    )
    if len(bars) < 300:
        raise AppError(
            f"Only {len(bars)} market bars were available for the historical pattern check."
        )

    featured = add_ml_features(bars_to_frame(bars), strategy)
    latest_row = featured.iloc[[-1]].copy()
    latest_session = str(featured.iloc[-1]["session"])

    labeled = add_outcome_labels(
        featured,
        stop_pct=float(stop_pct),
        reward_risk=float(reward_risk),
        horizon_bars=horizon_bars,
    )
    model_data = labeled[
        (labeled["session"].astype(str) != latest_session)
        & labeled["profitable_outcome"].notna()
    ].copy()
    if len(model_data) < 300:
        raise AppError(
            "There are too few completed historical examples for the pattern check."
        )
    if model_data["profitable_outcome"].nunique() < 2:
        raise AppError("Historical examples contain only one outcome class.")

    usable_features = [
        name
        for name in FEATURE_COLUMNS
        if name in model_data.columns
        and model_data[name].replace([np.inf, -np.inf], np.nan).notna().any()
    ]
    if len(usable_features) < 8:
        raise AppError("Too few usable market features were available for the ML filter.")

    model_data[usable_features] = model_data[usable_features].replace(
        [np.inf, -np.inf], np.nan
    )
    latest_row[usable_features] = latest_row[usable_features].replace(
        [np.inf, -np.inf], np.nan
    )

    oos = _walk_forward_probabilities(model_data, usable_features, folds=4)
    baseline = oos[oos["strategy_match"] >= 0.5]
    qualified = baseline[baseline["ml_probability"] >= float(threshold)]
    baseline_win = (
        float(baseline["profitable_outcome"].mean() * 100.0)
        if len(baseline)
        else None
    )
    qualified_win = (
        float(qualified["profitable_outcome"].mean() * 100.0)
        if len(qualified)
        else None
    )

    final_model = build_pipeline()
    final_model.fit(
        model_data[usable_features],
        model_data["profitable_outcome"].astype(int),
    )
    latest_probability = float(
        final_model.predict_proba(latest_row[usable_features])[:, 1][0]
    )

    return {
        "market_data_integrity_contract": "split_safe_raw_v1",
        "market_data_integrity": market_data_integrity,
        "score": latest_probability,
        "threshold": float(threshold),
        "passes": latest_probability >= float(threshold),
        "historical_strategy_triggers": int(len(baseline)),
        "historical_qualified_triggers": int(len(qualified)),
        "raw_trigger_win_rate": baseline_win,
        "qualified_trigger_win_rate": qualified_win,
        "win_rate_lift": (
            qualified_win - baseline_win
            if qualified_win is not None and baseline_win is not None
            else None
        ),
        "timeframe": timeframe,
        "history_days": int(history_days),
        "feed": str(getattr(market, "live_feed", "")),
    }
