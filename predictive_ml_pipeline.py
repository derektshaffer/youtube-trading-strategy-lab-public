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
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Build one supervised dataset across many symbols from a single batched bar load."""
    clean = parse_symbols(symbols)
    clean_horizons = tuple(sorted({max(1, int(value)) for value in horizons}))
    if not clean:
        return {
            "causal_replay": True,
            "symbols_requested": 0,
            "symbols_with_data": 0,
            "bars_analyzed": 0,
            "row_count": 0,
            "feature_columns": [],
            "label_columns": [],
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
    bars_analyzed = 0
    sessions_analyzed = 0
    observed_market_sessions: set[str] = set()

    for index, symbol in enumerate(clean, start=1):
        rows = list((rows_by_symbol or {}).get(symbol) or [])
        rows, selected_sessions = limit_rows_to_recent_market_sessions(rows, session_limit)
        observed_market_sessions.update(
            session for session in selected_sessions if session != "session-0"
        )
        bars_analyzed += len(rows)
        if not rows:
            by_symbol.append(
                {
                    "symbol": symbol,
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
                "bars": len(rows),
                "sessions": sessions,
                "market_sessions": selected_sessions,
                "rows": len(symbol_records),
            }
        )

    records.sort(key=lambda row: (str(row.get("session") or ""), str(row.get("timestamp") or ""), str(row.get("symbol") or "")))
    return {
        "causal_replay": True,
        "symbols_requested": len(clean),
        "symbols_with_data": sum(1 for item in by_symbol if int(item.get("bars") or 0) > 0),
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
        "row_count": len(records),
        "feature_columns": sorted(feature_columns),
        "label_columns": sorted(label_columns),
        "records": records,
        "by_symbol": by_symbol,
        "note": (
            "Feature columns are point-in-time causal values. label__ columns use only later bars "
            "from the same market session and must never be supplied to a model as inputs."
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
    min_train_sessions: int = 10,
    test_sessions_per_fold: int = 2,
    embargo_sessions: int = 1,
    min_train_rows: int = 100,
) -> dict[str, Any]:
    """Evaluate a probability baseline with expanding, session-level walk-forward folds.

    Entire market sessions are kept together. The optional embargo removes the
    sessions immediately preceding each test block from training, which is a
    conservative guard against adjacent-period dependence.
    """
    records = [dict(row) for row in dataset.get("records") or [] if isinstance(row, dict)]
    target = f"label__positive_return_{int(target_horizon)}bar"
    feature_columns = sorted(
        column for column in (dataset.get("feature_columns") or [])
        if str(column).startswith("feature__")
    )
    if not records or not feature_columns:
        return {"status": "INSUFFICIENT_DATA", "reason": "No supervised rows or feature columns are available."}

    frame = pd.DataFrame(records)
    if target not in frame.columns:
        return {"status": "INSUFFICIENT_DATA", "reason": f"Target {target} is not present in the dataset."}
    frame = frame[frame[target].notna() & frame["session"].notna()].copy()
    if frame.empty:
        return {"status": "INSUFFICIENT_DATA", "reason": "No rows have both a session and target label."}

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
        }

    model_brier = float(brier_score_loss(all_actual, all_probability))
    naive_brier = float(brier_score_loss(all_actual, all_naive_probability))
    auc = _safe_auc(all_actual, all_probability)
    return {
        "status": "EVALUATED",
        "model_type": "logistic_regression",
        "target": target,
        "target_horizon": int(target_horizon),
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
