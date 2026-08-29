"""Cross-stock supervised dataset and leakage-safe baseline ML evaluation.

This module deliberately keeps model research separate from live scoring. It builds
point-in-time feature rows across symbols, persists them reproducibly, and evaluates
simple probabilistic classifiers with chronological expanding-window folds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import tempfile
from typing import Any, Callable

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from market_feature_validation import DEFAULT_HORIZONS, build_supervised_feature_rows, limit_rows_to_recent_market_sessions
from youtube_strategy_engine import parse_symbols


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


MARKET_SESSION_MODES = ("regular", "extended")


def _normalize_market_session_mode(value: str) -> str:
    mode = str(value or "regular").strip().lower()
    aliases = {
        "regular": "regular",
        "regular_hours": "regular",
        "rth": "regular",
        "extended": "extended",
        "extended_hours": "extended",
        "premarket_afterhours": "extended",
    }
    normalized = aliases.get(mode)
    if normalized is None:
        raise ValueError("session_mode must be 'regular' or 'extended'.")
    return normalized


def _filter_rows_by_market_session(
    rows: list[dict[str, Any]],
    session_mode: str,
) -> list[dict[str, Any]]:
    """Keep either the regular U.S. session or premarket/after-hours bars.

    Regular is 09:30-16:00 ET. Extended is 04:00-09:30 ET plus 16:00-20:00 ET.
    Rows without parseable timestamps are excluded because they cannot be assigned
    safely to a market-hours regime.
    """
    mode = _normalize_market_session_mode(session_mode)
    selected: list[dict[str, Any]] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        stamp = pd.to_datetime(
            raw.get("t", raw.get("timestamp", raw.get("time"))),
            utc=True,
            errors="coerce",
        )
        if pd.isna(stamp):
            continue
        local = stamp.tz_convert("America/New_York")
        minute = int(local.hour) * 60 + int(local.minute)
        is_regular = (9 * 60 + 30) <= minute < (16 * 60)
        is_extended = (4 * 60) <= minute < (20 * 60) and not is_regular
        if (mode == "regular" and is_regular) or (mode == "extended" and is_extended):
            selected.append(dict(raw))
    return selected


def build_cross_stock_training_dataset(
    market: Any,
    symbols: list[str],
    *,
    start: Any,
    end: Any,
    timeframe: str = "1Min",
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    swing_radius: int = 3,
    max_pages: int = 80,
    require_full_horizon: bool = True,
    session_limit: int | None = None,
    profit_target_pct: float = 1.0,
    stop_loss_pct: float = 0.75,
    session_mode: str = "regular",
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Build one supervised dataset across many symbols from a single batched bar load.

    session_mode="regular" keeps 09:30-16:00 ET. session_mode="extended" keeps
    premarket 04:00-09:30 ET plus after-hours 16:00-20:00 ET. The two regimes are
    never mixed inside one dataset.
    """
    clean = parse_symbols(symbols)
    clean_horizons = tuple(sorted({max(1, int(value)) for value in horizons}))
    clean_profit_target = _number(profit_target_pct)
    clean_stop_loss = _number(stop_loss_pct)
    clean_session_mode = _normalize_market_session_mode(session_mode)
    if clean_profit_target is None or clean_profit_target <= 0:
        raise ValueError("profit_target_pct must be greater than zero.")
    if clean_stop_loss is None or clean_stop_loss <= 0:
        raise ValueError("stop_loss_pct must be greater than zero.")

    if not clean:
        return {
            "causal_replay": True,
            "symbols_requested": 0,
            "symbols_with_data": 0,
            "bars_analyzed": 0,
            "row_count": 0,
            "feature_columns": [],
            "label_columns": [],
            "profit_target_pct": float(clean_profit_target),
            "stop_loss_pct": float(clean_stop_loss),
            "barrier_same_bar_policy": "stop_first_conservative",
            "session_mode": clean_session_mode,
            "session_window_et": (
                "09:30-16:00"
                if clean_session_mode == "regular"
                else "04:00-09:30 + 16:00-20:00"
            ),
            "records": [],
        }

    if progress:
        progress(f"Loading historical {timeframe} bars for {len(clean)} stocks…")
    rows_by_symbol = market.bars(
        clean,
        start=start,
        end=end,
        timeframe=timeframe,
        max_pages=max_pages,
    )

    records: list[dict[str, Any]] = []
    feature_columns: set[str] = set()
    label_columns: set[str] = set()
    by_symbol: list[dict[str, Any]] = []
    bars_loaded = 0
    bars_analyzed = 0
    sessions_analyzed = 0
    observed_market_sessions: set[str] = set()

    for index, symbol in enumerate(clean, start=1):
        raw_rows = list((rows_by_symbol or {}).get(symbol) or [])
        bars_loaded += len(raw_rows)
        rows = _filter_rows_by_market_session(raw_rows, clean_session_mode)
        rows, selected_sessions = limit_rows_to_recent_market_sessions(rows, session_limit)
        observed_market_sessions.update(
            session for session in selected_sessions if session != "session-0"
        )
        bars_analyzed += len(rows)
        if not rows:
            by_symbol.append(
                {
                    "symbol": symbol,
                    "raw_bars": len(raw_rows),
                    "bars": 0,
                    "sessions": 0,
                    "market_sessions": selected_sessions,
                    "rows": 0,
                }
            )
            continue
        if progress:
            progress(f"Building causal ML rows for {symbol} ({index}/{len(clean)})…")
        report = build_supervised_feature_rows(
            rows,
            horizons=clean_horizons,
            swing_radius=swing_radius,
            require_full_horizon=require_full_horizon,
            profit_target_pct=float(clean_profit_target),
            stop_loss_pct=float(clean_stop_loss),
        )
        symbol_records = []
        for item in report.get("records") or []:
            row = dict(item)
            row["symbol"] = symbol
            symbol_records.append(row)
        records.extend(symbol_records)
        feature_columns.update(report.get("feature_columns") or [])
        label_columns.update(report.get("label_columns") or [])
        sessions = int(report.get("sessions_analyzed") or 0)
        sessions_analyzed += sessions
        by_symbol.append(
            {
                "symbol": symbol,
                "raw_bars": len(raw_rows),
                "bars": len(rows),
                "sessions": sessions,
                "market_sessions": selected_sessions,
                "rows": len(symbol_records),
            }
        )

    records.sort(
        key=lambda row: (
            str(row.get("session") or ""),
            str(row.get("timestamp") or ""),
            str(row.get("symbol") or ""),
        )
    )
    return {
        "causal_replay": True,
        "symbols_requested": len(clean),
        "symbols_with_data": sum(1 for item in by_symbol if int(item.get("bars") or 0) > 0),
        "bars_loaded": bars_loaded,
        "bars_analyzed": bars_analyzed,
        "sessions_analyzed": sessions_analyzed,
        "market_sessions_requested": (
            max(1, int(session_limit)) if session_limit is not None else None
        ),
        "market_sessions_observed": len(observed_market_sessions),
        "market_session_dates": sorted(observed_market_sessions),
        "timeframe": timeframe,
        "horizons": list(clean_horizons),
        "require_full_horizon": bool(require_full_horizon),
        "profit_target_pct": float(clean_profit_target),
        "stop_loss_pct": float(clean_stop_loss),
        "barrier_same_bar_policy": "stop_first_conservative",
        "session_mode": clean_session_mode,
        "session_window_et": (
            "09:30-16:00"
            if clean_session_mode == "regular"
            else "04:00-09:30 + 16:00-20:00"
        ),
        "row_count": len(records),
        "feature_columns": sorted(feature_columns),
        "label_columns": sorted(label_columns),
        "records": records,
        "by_symbol": by_symbol,
        "note": (
            "Feature columns are point-in-time causal values. label__ columns use only later bars "
            "from the same market session and must never be supplied to a model as inputs. "
            "Trade-quality labels count an upside target only when it is reached before the "
            "downside barrier; same-candle target/stop ambiguity is scored conservatively as stop first. "
            f"Market-hours regime: {clean_session_mode}; regular and extended-hours rows are never mixed."
        ),
    }

def save_training_dataset(dataset: dict[str, Any], destination: str | Path) -> dict[str, str]:
    """Atomically persist records as JSONL plus a compact metadata sidecar."""
    path = Path(destination)
    if path.suffix.lower() != ".jsonl":
        path = path.with_suffix(".jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path = path.with_suffix(".meta.json")

    records = list(dataset.get("records") or [])
    metadata = {key: value for key, value in dataset.items() if key != "records"}
    metadata["saved_at_utc"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    metadata["data_file"] = path.name

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent) as handle:
        data_temp = Path(handle.name)
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    data_temp.replace(path)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent) as handle:
        meta_temp = Path(handle.name)
        json.dump(metadata, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    meta_temp.replace(metadata_path)
    return {"data_path": str(path), "metadata_path": str(metadata_path)}


def load_training_dataset(source: str | Path) -> dict[str, Any]:
    """Load a persisted JSONL dataset and its metadata sidecar when present."""
    path = Path(source)
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    records.append(value)

    metadata_path = path.with_suffix(".meta.json")
    metadata: dict[str, Any] = {}
    if metadata_path.exists():
        with metadata_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
            if isinstance(loaded, dict):
                metadata = loaded
    metadata["records"] = records
    metadata["row_count"] = len(records)
    return metadata


def _feature_types(frame: pd.DataFrame, feature_columns: list[str]) -> tuple[list[str], list[str]]:
    numeric: list[str] = []
    categorical: list[str] = []
    for column in feature_columns:
        values = [value for value in frame[column].dropna().tolist()]
        if all(isinstance(value, (bool, int, float)) and not isinstance(value, complex) for value in values):
            numeric.append(column)
        else:
            categorical.append(column)
    return numeric, categorical


def _prepare_feature_frame(frame: pd.DataFrame, numeric: list[str], categorical: list[str]) -> pd.DataFrame:
    prepared = frame.copy()
    for column in numeric:
        prepared[column] = prepared[column].map(
            lambda value: float(value) if isinstance(value, bool) else _number(value)
        )
    for column in categorical:
        prepared[column] = prepared[column].map(
            lambda value: None if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)
        )
    return prepared


def _baseline_pipeline(numeric: list[str], categorical: list[str]) -> Pipeline:
    transformers = []
    if numeric:
        transformers.append(
            (
                "numeric",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                numeric,
            )
        )
    if categorical:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical,
            )
        )
    if not transformers:
        raise ValueError("No usable feature columns were available for the baseline model.")
    return Pipeline(
        steps=[
            ("features", ColumnTransformer(transformers=transformers, remainder="drop")),
            ("model", LogisticRegression(max_iter=1000, solver="liblinear")),
        ]
    )


def _safe_auc(y_true: list[int], probabilities: list[float]) -> float | None:
    if len(set(y_true)) < 2:
        return None
    return float(roc_auc_score(y_true, probabilities))


def walk_forward_logistic_baseline(
    dataset: dict[str, Any],
    *,
    target_horizon: int = 15,
    target_mode: str = "positive_return",
    min_train_sessions: int = 10,
    test_sessions_per_fold: int = 2,
    embargo_sessions: int = 1,
    min_train_rows: int = 100,
) -> dict[str, Any]:
    """Evaluate a probability baseline with expanding, session-level walk-forward folds.

    Entire market sessions are kept together. The optional embargo removes the
    sessions immediately preceding each test block from training, which is a
    conservative guard against adjacent-period dependence.

    target_mode="positive_return" predicts whether the horizon close is above the
    observation close. target_mode="target_before_stop" predicts whether the
    dataset's configured upside barrier is reached before its downside barrier.
    """
    records = [dict(row) for row in dataset.get("records") or [] if isinstance(row, dict)]
    normalized_target_mode = str(target_mode or "").strip().lower()
    if normalized_target_mode == "positive_return":
        target = f"label__positive_return_{int(target_horizon)}bar"
        target_description = (
            f"Price closes above the observation price after {int(target_horizon)} bars."
        )
    elif normalized_target_mode == "target_before_stop":
        target = f"label__target_before_stop_{int(target_horizon)}bar"
        profit_target_pct = _number(dataset.get("profit_target_pct"))
        stop_loss_pct = _number(dataset.get("stop_loss_pct"))
        target_description = (
            f"Price reaches +{profit_target_pct:g}% before -{stop_loss_pct:g}% "
            f"within {int(target_horizon)} bars."
            if profit_target_pct is not None and stop_loss_pct is not None
            else f"Configured upside barrier is reached before the downside barrier within {int(target_horizon)} bars."
        )
    else:
        raise ValueError(
            "target_mode must be 'positive_return' or 'target_before_stop'."
        )

    feature_columns = sorted(
        column for column in (dataset.get("feature_columns") or [])
        if str(column).startswith("feature__")
    )
    if not records or not feature_columns:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No supervised rows or feature columns are available.",
            "target_mode": normalized_target_mode,
            "target": target,
        }

    frame = pd.DataFrame(records)
    if target not in frame.columns:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": f"Target {target} is not present in the dataset.",
            "target_mode": normalized_target_mode,
            "target": target,
        }
    frame = frame[frame[target].notna() & frame["session"].notna()].copy()
    if frame.empty:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No rows have both a session and target label.",
            "target_mode": normalized_target_mode,
            "target": target,
        }

    frame[target] = frame[target].astype(bool).astype(int)
    frame["_session_key"] = frame["session"].astype(str)
    frame["_time_key"] = pd.to_datetime(frame.get("timestamp"), utc=True, errors="coerce")
    frame = frame.sort_values(["_session_key", "_time_key", "symbol"], na_position="last").reset_index(drop=True)

    sessions = sorted(frame["_session_key"].unique().tolist())
    min_train_sessions = max(2, int(min_train_sessions))
    test_sessions_per_fold = max(1, int(test_sessions_per_fold))
    embargo_sessions = max(0, int(embargo_sessions))
    if len(sessions) < min_train_sessions + embargo_sessions + test_sessions_per_fold:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "Not enough distinct market sessions for the requested walk-forward split.",
            "session_count": len(sessions),
            "target_mode": normalized_target_mode,
            "target": target,
        }

    numeric, categorical = _feature_types(frame, feature_columns)
    prepared = _prepare_feature_frame(frame, numeric, categorical)

    folds: list[dict[str, Any]] = []
    all_actual: list[int] = []
    all_probability: list[float] = []
    all_naive_probability: list[float] = []
    all_prediction_rows: list[dict[str, Any]] = []

    test_start = min_train_sessions + embargo_sessions
    fold_number = 0
    while test_start < len(sessions):
        test_sessions = sessions[test_start : test_start + test_sessions_per_fold]
        if not test_sessions:
            break
        train_end = max(0, test_start - embargo_sessions)
        train_sessions = sessions[:train_end]
        if len(train_sessions) < min_train_sessions:
            test_start += test_sessions_per_fold
            continue

        train_mask = prepared["_session_key"].isin(train_sessions)
        test_mask = prepared["_session_key"].isin(test_sessions)
        train = prepared.loc[train_mask]
        test = prepared.loc[test_mask]
        if len(train) < min_train_rows or test.empty or train[target].nunique() < 2:
            test_start += test_sessions_per_fold
            continue

        pipeline = _baseline_pipeline(numeric, categorical)
        pipeline.fit(train[feature_columns], train[target])
        probability = pipeline.predict_proba(test[feature_columns])[:, 1]
        actual = test[target].astype(int).tolist()
        predicted = (probability >= 0.5).astype(int)
        naive_probability = float(train[target].mean())
        naive = [naive_probability] * len(actual)

        fold_number += 1
        model_brier = float(brier_score_loss(actual, probability))
        naive_brier = float(brier_score_loss(actual, naive))
        folds.append(
            {
                "fold": fold_number,
                "train_sessions": len(train_sessions),
                "train_rows": len(train),
                "test_sessions": test_sessions,
                "test_rows": len(test),
                "train_positive_rate": naive_probability,
                "test_positive_rate": float(sum(actual) / len(actual)),
                "roc_auc": _safe_auc(actual, probability.tolist()),
                "brier_score": model_brier,
                "naive_brier_score": naive_brier,
                "brier_skill_vs_naive": None if naive_brier <= 0 else 1.0 - (model_brier / naive_brier),
                "accuracy": float(accuracy_score(actual, predicted)),
                "log_loss": float(log_loss(actual, probability, labels=[0, 1])),
            }
        )

        all_actual.extend(actual)
        all_probability.extend(float(value) for value in probability)
        all_naive_probability.extend(naive)
        for (_, row), prob in zip(test.iterrows(), probability):
            all_prediction_rows.append(
                {
                    "symbol": row.get("symbol"),
                    "session": row.get("session"),
                    "timestamp": row.get("timestamp"),
                    "actual": bool(row[target]),
                    "probability": float(prob),
                }
            )
        test_start += test_sessions_per_fold

    if not folds or not all_actual:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No walk-forward fold met the minimum training requirements.",
            "session_count": len(sessions),
            "row_count": len(frame),
            "target_mode": normalized_target_mode,
            "target": target,
        }

    model_brier = float(brier_score_loss(all_actual, all_probability))
    naive_brier = float(brier_score_loss(all_actual, all_naive_probability))
    auc = _safe_auc(all_actual, all_probability)
    return {
        "status": "EVALUATED",
        "model_type": "logistic_regression",
        "target": target,
        "target_mode": normalized_target_mode,
        "target_description": target_description,
        "target_horizon": int(target_horizon),
        "profit_target_pct": _number(dataset.get("profit_target_pct")),
        "stop_loss_pct": _number(dataset.get("stop_loss_pct")),
        "barrier_same_bar_policy": dataset.get("barrier_same_bar_policy"),
        "feature_count": len(feature_columns),
        "numeric_feature_count": len(numeric),
        "categorical_feature_count": len(categorical),
        "session_count": len(sessions),
        "fold_count": len(folds),
        "oos_rows": len(all_actual),
        "oos_positive_rate": float(sum(all_actual) / len(all_actual)),
        "roc_auc": auc,
        "brier_score": model_brier,
        "naive_brier_score": naive_brier,
        "brier_skill_vs_naive": None if naive_brier <= 0 else 1.0 - (model_brier / naive_brier),
        "accuracy": float(accuracy_score(all_actual, [int(value >= 0.5) for value in all_probability])),
        "log_loss": float(log_loss(all_actual, all_probability, labels=[0, 1])),
        "folds": folds,
        "predictions": all_prediction_rows,
        "split_policy": {
            "type": "expanding_session_walk_forward",
            "min_train_sessions": min_train_sessions,
            "test_sessions_per_fold": test_sessions_per_fold,
            "embargo_sessions": embargo_sessions,
            "min_train_rows": min_train_rows,
        },
        "note": (
            "All reported model metrics are out-of-sample. The model is a research baseline only "
            "and is not connected to live rankings or trading decisions."
        ),
    }

def leave_one_symbol_out_walk_forward_logistic_baseline(
    dataset: dict[str, Any],
    *,
    target_horizon: int = 15,
    target_mode: str = "target_before_stop",
    min_train_sessions: int = 8,
    test_sessions_per_fold: int = 2,
    embargo_sessions: int = 1,
    min_train_rows: int = 250,
    min_test_rows: int = 25,
) -> dict[str, Any]:
    """Test cross-stock transfer while preserving chronological causality.

    Each symbol is held out completely from model training. For that held-out
    symbol, predictions are made only on later market sessions; training uses
    earlier sessions from the other symbols, with the requested embargo.
    """

    records = [dict(row) for row in dataset.get("records") or [] if isinstance(row, dict)]
    normalized_target_mode = str(target_mode or "").strip().lower()
    if normalized_target_mode == "positive_return":
        target = f"label__positive_return_{int(target_horizon)}bar"
        target_description = (
            f"Price closes above the observation price after {int(target_horizon)} bars."
        )
    elif normalized_target_mode == "target_before_stop":
        target = f"label__target_before_stop_{int(target_horizon)}bar"
        profit_target_pct = _number(dataset.get("profit_target_pct"))
        stop_loss_pct = _number(dataset.get("stop_loss_pct"))
        target_description = (
            f"Price reaches +{profit_target_pct:g}% before -{stop_loss_pct:g}% "
            f"within {int(target_horizon)} bars."
            if profit_target_pct is not None and stop_loss_pct is not None
            else f"Configured upside barrier is reached before the downside barrier within {int(target_horizon)} bars."
        )
    else:
        raise ValueError("target_mode must be 'positive_return' or 'target_before_stop'.")

    feature_columns = sorted(
        column for column in (dataset.get("feature_columns") or [])
        if str(column).startswith("feature__")
    )
    if not records or not feature_columns:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No supervised rows or feature columns are available.",
            "target_mode": normalized_target_mode,
            "target": target,
        }

    frame = pd.DataFrame(records)
    required_columns = {"symbol", "session", target}
    if not required_columns.issubset(frame.columns):
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "Dataset is missing symbol, session, or requested target labels.",
            "target_mode": normalized_target_mode,
            "target": target,
        }
    frame = frame[
        frame[target].notna()
        & frame["session"].notna()
        & frame["symbol"].notna()
    ].copy()
    if frame.empty:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No rows have symbol, session, and target labels.",
            "target_mode": normalized_target_mode,
            "target": target,
        }

    frame[target] = frame[target].astype(bool).astype(int)
    frame["_session_key"] = frame["session"].astype(str)
    frame["_symbol_key"] = frame["symbol"].astype(str).str.upper()
    frame["_time_key"] = pd.to_datetime(frame.get("timestamp"), utc=True, errors="coerce")
    frame = frame.sort_values(
        ["_session_key", "_time_key", "_symbol_key"],
        na_position="last",
    ).reset_index(drop=True)

    sessions = sorted(frame["_session_key"].unique().tolist())
    symbols = sorted(frame["_symbol_key"].unique().tolist())
    min_train_sessions = max(2, int(min_train_sessions))
    test_sessions_per_fold = max(1, int(test_sessions_per_fold))
    embargo_sessions = max(0, int(embargo_sessions))
    min_train_rows = max(1, int(min_train_rows))
    min_test_rows = max(1, int(min_test_rows))

    if len(symbols) < 2:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "At least two symbols are required for held-out-stock validation.",
            "symbol_count": len(symbols),
            "target_mode": normalized_target_mode,
            "target": target,
        }
    if len(sessions) < min_train_sessions + embargo_sessions + test_sessions_per_fold:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "Not enough market sessions for held-out-stock walk-forward validation.",
            "session_count": len(sessions),
            "target_mode": normalized_target_mode,
            "target": target,
        }

    numeric, categorical = _feature_types(frame, feature_columns)
    prepared = _prepare_feature_frame(frame, numeric, categorical)

    symbol_reports: list[dict[str, Any]] = []
    all_actual: list[int] = []
    all_probability: list[float] = []
    all_naive_probability: list[float] = []
    all_prediction_rows: list[dict[str, Any]] = []

    for held_out_symbol in symbols:
        folds: list[dict[str, Any]] = []
        symbol_actual: list[int] = []
        symbol_probability: list[float] = []
        symbol_naive: list[float] = []
        test_start = min_train_sessions + embargo_sessions
        fold_number = 0

        while test_start < len(sessions):
            test_sessions = sessions[test_start : test_start + test_sessions_per_fold]
            if not test_sessions:
                break
            train_end = max(0, test_start - embargo_sessions)
            train_sessions = sessions[:train_end]
            if len(train_sessions) < min_train_sessions:
                test_start += test_sessions_per_fold
                continue

            train_mask = (
                prepared["_session_key"].isin(train_sessions)
                & prepared["_symbol_key"].ne(held_out_symbol)
            )
            test_mask = (
                prepared["_session_key"].isin(test_sessions)
                & prepared["_symbol_key"].eq(held_out_symbol)
            )
            train = prepared.loc[train_mask]
            test = prepared.loc[test_mask]
            if (
                len(train) < min_train_rows
                or len(test) < min_test_rows
                or train[target].nunique() < 2
            ):
                test_start += test_sessions_per_fold
                continue

            pipeline = _baseline_pipeline(numeric, categorical)
            pipeline.fit(train[feature_columns], train[target])
            probability = pipeline.predict_proba(test[feature_columns])[:, 1]
            actual = test[target].astype(int).tolist()
            predicted = (probability >= 0.5).astype(int)
            naive_probability = float(train[target].mean())
            naive = [naive_probability] * len(actual)
            model_brier = float(brier_score_loss(actual, probability))
            naive_brier = float(brier_score_loss(actual, naive))

            fold_number += 1
            folds.append(
                {
                    "fold": fold_number,
                    "held_out_symbol": held_out_symbol,
                    "train_symbols": sorted(
                        symbol for symbol in symbols if symbol != held_out_symbol
                    ),
                    "train_sessions": len(train_sessions),
                    "train_rows": len(train),
                    "test_sessions": test_sessions,
                    "test_rows": len(test),
                    "train_positive_rate": naive_probability,
                    "test_positive_rate": float(sum(actual) / len(actual)),
                    "roc_auc": _safe_auc(actual, probability.tolist()),
                    "brier_score": model_brier,
                    "naive_brier_score": naive_brier,
                    "brier_skill_vs_naive": (
                        None if naive_brier <= 0 else 1.0 - (model_brier / naive_brier)
                    ),
                    "accuracy": float(accuracy_score(actual, predicted)),
                    "log_loss": float(log_loss(actual, probability, labels=[0, 1])),
                }
            )

            symbol_actual.extend(actual)
            symbol_probability.extend(float(value) for value in probability)
            symbol_naive.extend(naive)
            all_actual.extend(actual)
            all_probability.extend(float(value) for value in probability)
            all_naive_probability.extend(naive)
            for (_, row), prob in zip(test.iterrows(), probability):
                prediction = {
                    "held_out_symbol": held_out_symbol,
                    "symbol": row.get("symbol"),
                    "session": row.get("session"),
                    "timestamp": row.get("timestamp"),
                    "actual": bool(row[target]),
                    "probability": float(prob),
                }
                all_prediction_rows.append(prediction)
            test_start += test_sessions_per_fold

        if symbol_actual:
            symbol_brier = float(brier_score_loss(symbol_actual, symbol_probability))
            symbol_naive_brier = float(brier_score_loss(symbol_actual, symbol_naive))
            symbol_reports.append(
                {
                    "symbol": held_out_symbol,
                    "status": "EVALUATED",
                    "fold_count": len(folds),
                    "oos_rows": len(symbol_actual),
                    "oos_positive_rate": float(sum(symbol_actual) / len(symbol_actual)),
                    "roc_auc": _safe_auc(symbol_actual, symbol_probability),
                    "brier_score": symbol_brier,
                    "naive_brier_score": symbol_naive_brier,
                    "brier_skill_vs_naive": (
                        None
                        if symbol_naive_brier <= 0
                        else 1.0 - (symbol_brier / symbol_naive_brier)
                    ),
                    "accuracy": float(
                        accuracy_score(
                            symbol_actual,
                            [int(value >= 0.5) for value in symbol_probability],
                        )
                    ),
                    "folds": folds,
                }
            )
        else:
            symbol_reports.append(
                {
                    "symbol": held_out_symbol,
                    "status": "INSUFFICIENT_DATA",
                    "fold_count": 0,
                    "oos_rows": 0,
                    "roc_auc": None,
                    "brier_skill_vs_naive": None,
                    "folds": [],
                }
            )

    if not all_actual:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No held-out-stock fold met the minimum train/test requirements.",
            "symbol_count": len(symbols),
            "session_count": len(sessions),
            "target_mode": normalized_target_mode,
            "target": target,
            "by_symbol": symbol_reports,
        }

    model_brier = float(brier_score_loss(all_actual, all_probability))
    naive_brier = float(brier_score_loss(all_actual, all_naive_probability))
    return {
        "status": "EVALUATED",
        "validation_type": "leave_one_symbol_out_walk_forward",
        "model_type": "logistic_regression",
        "target": target,
        "target_mode": normalized_target_mode,
        "target_description": target_description,
        "target_horizon": int(target_horizon),
        "profit_target_pct": _number(dataset.get("profit_target_pct")),
        "stop_loss_pct": _number(dataset.get("stop_loss_pct")),
        "session_mode": dataset.get("session_mode"),
        "symbol_count": len(symbols),
        "held_out_symbols": symbols,
        "session_count": len(sessions),
        "feature_count": len(feature_columns),
        "oos_rows": len(all_actual),
        "oos_positive_rate": float(sum(all_actual) / len(all_actual)),
        "roc_auc": _safe_auc(all_actual, all_probability),
        "brier_score": model_brier,
        "naive_brier_score": naive_brier,
        "brier_skill_vs_naive": (
            None if naive_brier <= 0 else 1.0 - (model_brier / naive_brier)
        ),
        "accuracy": float(
            accuracy_score(
                all_actual,
                [int(value >= 0.5) for value in all_probability],
            )
        ),
        "by_symbol": symbol_reports,
        "predictions": all_prediction_rows,
        "split_policy": {
            "type": "leave_one_symbol_out_plus_expanding_session_walk_forward",
            "held_out_symbol_never_in_training": True,
            "min_train_sessions": min_train_sessions,
            "test_sessions_per_fold": test_sessions_per_fold,
            "embargo_sessions": embargo_sessions,
            "min_train_rows": min_train_rows,
            "min_test_rows": min_test_rows,
        },
        "note": (
            "Each symbol is excluded from all training rows for its evaluation. "
            "Its predictions are also chronological: only earlier sessions from the "
            "other symbols are used for training. This is a stricter cross-stock "
            "generalization test than the ordinary walk-forward baseline."
        ),
    }

