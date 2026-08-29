"""Portable nonlinear gradient-boosted probability challenger.

This research-only model is intentionally compact: shallow boosted trees can learn
nonlinear thresholds and pairwise feature interactions while remaining JSON-safe
for the Trading Lab's durable model library.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import brier_score_loss

from predictive_probability_model import (
    _apply_calibration,
    _fit_platt_calibrator,
    _number,
    _reliability_bins,
    _safe_auc,
    _target_name,
    _usable_numeric_features,
)


DEFAULT_ESTIMATORS = 48
DEFAULT_LEARNING_RATE = 0.06
DEFAULT_MAX_DEPTH = 2
DEFAULT_MIN_SAMPLES_LEAF = 35
DEFAULT_SUBSAMPLE = 0.85


def _prepare_matrix(
    frame: pd.DataFrame,
    feature_columns: list[str],
    *,
    medians: dict[str, float] | None = None,
) -> tuple[np.ndarray, dict[str, float]]:
    resolved = dict(medians or {})
    columns: list[np.ndarray] = []
    for column in feature_columns:
        series = frame[column].map(_number)
        clean = series.dropna()
        if column not in resolved:
            if clean.empty:
                raise ValueError(f"Feature {column} has no numeric training values.")
            resolved[column] = float(clean.median())
        values = series.fillna(float(resolved[column])).astype(float).to_numpy()
        columns.append(values)
    if not columns:
        return np.empty((len(frame), 0), dtype=float), resolved
    return np.column_stack(columns).astype(float), resolved


def _serialize_tree(estimator: Any) -> dict[str, Any]:
    tree = estimator.tree_
    return {
        "children_left": [int(value) for value in tree.children_left.tolist()],
        "children_right": [int(value) for value in tree.children_right.tolist()],
        "feature": [int(value) for value in tree.feature.tolist()],
        "threshold": [float(value) for value in tree.threshold.tolist()],
        "value": [float(value[0][0]) for value in tree.value.tolist()],
    }


def _tree_predict(tree: dict[str, Any], values: list[float]) -> float:
    left = list(tree.get("children_left") or [])
    right = list(tree.get("children_right") or [])
    features = list(tree.get("feature") or [])
    thresholds = list(tree.get("threshold") or [])
    outputs = list(tree.get("value") or [])
    if not outputs:
        raise ValueError("Boosted tree parameters are empty.")
    node = 0
    safety = 0
    while node >= 0 and node < len(outputs):
        safety += 1
        if safety > len(outputs) + 2:
            raise ValueError("Boosted tree traversal exceeded its node count.")
        child_left = int(left[node])
        child_right = int(right[node])
        feature_index = int(features[node])
        if child_left == child_right or feature_index < 0:
            return float(outputs[node])
        if feature_index >= len(values):
            raise ValueError("Boosted tree references a missing feature.")
        node = (
            child_left
            if float(values[feature_index]) <= float(thresholds[node])
            else child_right
        )
    raise ValueError("Boosted tree parameters are invalid.")


def _fit_state(
    frame: pd.DataFrame,
    feature_columns: list[str],
    target: str,
    *,
    n_estimators: int = DEFAULT_ESTIMATORS,
    learning_rate: float = DEFAULT_LEARNING_RATE,
    max_depth: int = DEFAULT_MAX_DEPTH,
    min_samples_leaf: int = DEFAULT_MIN_SAMPLES_LEAF,
    subsample: float = DEFAULT_SUBSAMPLE,
) -> dict[str, Any]:
    matrix, medians = _prepare_matrix(frame, feature_columns)
    model = GradientBoostingClassifier(
        n_estimators=max(12, int(n_estimators)),
        learning_rate=float(learning_rate),
        max_depth=max(1, int(max_depth)),
        min_samples_leaf=max(5, int(min_samples_leaf)),
        subsample=max(0.5, min(1.0, float(subsample))),
        random_state=42,
    )
    model.fit(matrix, frame[target].astype(int))

    try:
        initial_raw = float(model._raw_predict_init(matrix[:1])[0][0])
    except Exception:
        positive_rate = min(
            1.0 - 1e-6,
            max(1e-6, float(frame[target].astype(int).mean())),
        )
        initial_raw = math.log(positive_rate / (1.0 - positive_rate))

    trees = [_serialize_tree(stage[0]) for stage in model.estimators_]
    return {
        "feature_columns": list(feature_columns),
        "medians": medians,
        "initial_raw_score": initial_raw,
        "learning_rate": float(model.learning_rate),
        "trees": trees,
        "tree_count": len(trees),
        "max_depth": max(1, int(max_depth)),
        "min_samples_leaf": max(5, int(min_samples_leaf)),
        "subsample": max(0.5, min(1.0, float(subsample))),
    }


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _predict_values(state: dict[str, Any], values: list[float]) -> float:
    raw = float(state.get("initial_raw_score") or 0.0)
    learning_rate = float(state.get("learning_rate") or DEFAULT_LEARNING_RATE)
    for tree in state.get("trees") or []:
        raw += learning_rate * _tree_predict(tree, values)
    return _sigmoid(raw)


def _predict_frame(state: dict[str, Any], frame: pd.DataFrame) -> list[float]:
    feature_columns = list(state.get("feature_columns") or [])
    medians = dict(state.get("medians") or {})
    if not feature_columns or not state.get("trees"):
        raise ValueError("Boosted probability model parameters are incomplete.")
    matrix, _ = _prepare_matrix(
        frame,
        feature_columns,
        medians=medians,
    )
    return [
        _predict_values(state, [float(value) for value in row.tolist()])
        for row in matrix
    ]


def _chronological_oos(
    frame: pd.DataFrame,
    feature_columns: list[str],
    target: str,
    *,
    min_train_sessions: int,
    test_sessions_per_fold: int,
    embargo_sessions: int,
    min_train_rows: int,
) -> dict[str, Any]:
    sessions = sorted(frame["_session_key"].unique().tolist())
    all_actual: list[int] = []
    all_probability: list[float] = []
    all_naive: list[float] = []
    prediction_rows: list[dict[str, Any]] = []
    folds: list[dict[str, Any]] = []

    test_start = max(2, int(min_train_sessions)) + max(0, int(embargo_sessions))
    fold_number = 0
    while test_start < len(sessions):
        test_sessions = sessions[
            test_start : test_start + max(1, int(test_sessions_per_fold))
        ]
        if not test_sessions:
            break
        train_end = max(0, test_start - max(0, int(embargo_sessions)))
        train_sessions = sessions[:train_end]
        train = frame[frame["_session_key"].isin(train_sessions)]
        test = frame[frame["_session_key"].isin(test_sessions)]
        if (
            len(train) < max(20, int(min_train_rows))
            or test.empty
            or train[target].nunique() < 2
        ):
            test_start += max(1, int(test_sessions_per_fold))
            continue

        state = _fit_state(train, feature_columns, target)
        probability = _predict_frame(state, test)
        actual = test[target].astype(int).tolist()
        naive_probability = float(train[target].mean())
        naive = [naive_probability] * len(actual)
        model_brier = float(brier_score_loss(actual, probability))
        naive_brier = float(brier_score_loss(actual, naive))
        fold_number += 1
        folds.append(
            {
                "fold": fold_number,
                "train_sessions": len(train_sessions),
                "train_rows": len(train),
                "test_sessions": list(test_sessions),
                "test_rows": len(test),
                "roc_auc": _safe_auc(actual, probability),
                "brier_score": model_brier,
                "naive_brier_score": naive_brier,
                "brier_skill_vs_naive": (
                    None
                    if naive_brier <= 0
                    else 1.0 - (model_brier / naive_brier)
                ),
            }
        )
        all_actual.extend(actual)
        all_probability.extend(probability)
        all_naive.extend(naive)
        for (_, row), outcome, prob in zip(test.iterrows(), actual, probability):
            prediction_rows.append(
                {
                    "session": str(row.get("session") or ""),
                    "timestamp": row.get("timestamp"),
                    "actual": int(outcome),
                    "probability": float(prob),
                }
            )
        test_start += max(1, int(test_sessions_per_fold))

    if not all_actual:
        return {
            "status": "INSUFFICIENT_DATA",
            "folds": folds,
            "actual": [],
            "probabilities": [],
            "naive": [],
            "prediction_rows": [],
        }
    model_brier = float(brier_score_loss(all_actual, all_probability))
    naive_brier = float(brier_score_loss(all_actual, all_naive))
    return {
        "status": "EVALUATED",
        "folds": folds,
        "actual": all_actual,
        "probabilities": all_probability,
        "naive": all_naive,
        "prediction_rows": prediction_rows,
        "oos_rows": len(all_actual),
        "roc_auc": _safe_auc(all_actual, all_probability),
        "brier_score": model_brier,
        "naive_brier_score": naive_brier,
        "brier_skill_vs_naive": (
            None if naive_brier <= 0 else 1.0 - (model_brier / naive_brier)
        ),
    }


def _held_out_stock_generalization(
    frame: pd.DataFrame,
    feature_columns: list[str],
    target: str,
    *,
    embargo_sessions: int = 1,
    min_train_rows: int = 250,
    test_sessions_per_symbol: int = 4,
) -> dict[str, Any]:
    """Test the boosted model on stocks excluded entirely from their training fit."""
    if "symbol" not in frame.columns:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "Dataset is missing symbol labels.",
        }

    all_actual: list[int] = []
    all_probability: list[float] = []
    all_naive: list[float] = []
    by_symbol: list[dict[str, Any]] = []
    global_sessions = sorted(frame["_session_key"].unique().tolist())

    for symbol in sorted(frame["symbol"].dropna().astype(str).unique().tolist()):
        symbol_frame = frame[frame["symbol"].astype(str) == symbol]
        symbol_sessions = sorted(symbol_frame["_session_key"].unique().tolist())
        if len(symbol_sessions) < 3:
            continue
        test_sessions = symbol_sessions[-max(2, min(int(test_sessions_per_symbol), len(symbol_sessions) - 1)) :]
        first_test = test_sessions[0]
        try:
            first_index = global_sessions.index(first_test)
        except ValueError:
            continue
        train_end = max(0, first_index - max(0, int(embargo_sessions)))
        allowed_train_sessions = set(global_sessions[:train_end])
        train = frame[
            (frame["symbol"].astype(str) != symbol)
            & frame["_session_key"].isin(allowed_train_sessions)
        ]
        test = symbol_frame[symbol_frame["_session_key"].isin(test_sessions)]
        if (
            len(train) < max(20, int(min_train_rows))
            or test.empty
            or train[target].nunique() < 2
        ):
            continue

        state = _fit_state(train, feature_columns, target)
        probability = _predict_frame(state, test)
        actual = test[target].astype(int).tolist()
        naive_probability = float(train[target].mean())
        naive = [naive_probability] * len(actual)
        model_brier = float(brier_score_loss(actual, probability))
        naive_brier = float(brier_score_loss(actual, naive))
        by_symbol.append(
            {
                "symbol": symbol,
                "train_rows": len(train),
                "test_rows": len(test),
                "test_sessions": list(test_sessions),
                "roc_auc": _safe_auc(actual, probability),
                "brier_score": model_brier,
                "naive_brier_score": naive_brier,
                "brier_skill_vs_naive": (
                    None
                    if naive_brier <= 0
                    else 1.0 - (model_brier / naive_brier)
                ),
            }
        )
        all_actual.extend(actual)
        all_probability.extend(probability)
        all_naive.extend(naive)

    if not all_actual:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No held-out stock fit met the minimum training requirements.",
            "by_symbol": by_symbol,
        }
    model_brier = float(brier_score_loss(all_actual, all_probability))
    naive_brier = float(brier_score_loss(all_actual, all_naive))
    return {
        "status": "EVALUATED",
        "held_out_symbols": len(by_symbol),
        "oos_rows": len(all_actual),
        "roc_auc": _safe_auc(all_actual, all_probability),
        "brier_score": model_brier,
        "naive_brier_score": naive_brier,
        "brier_skill_vs_naive": (
            None if naive_brier <= 0 else 1.0 - (model_brier / naive_brier)
        ),
        "by_symbol": by_symbol,
    }


def build_boosted_probability_model(
    dataset: dict[str, Any],
    *,
    target_horizon: int = 15,
    target_mode: str = "target_before_stop",
    min_train_sessions: int = 8,
    test_sessions_per_fold: int = 2,
    embargo_sessions: int = 1,
    min_train_rows: int = 250,
    minimum_feature_count: int = 5,
    minimum_feature_coverage: float = 0.55,
    minimum_oos_rows: int = 250,
    minimum_auc: float = 0.52,
) -> dict[str, Any]:
    """Train and gate a portable shallow gradient-boosted tree challenger."""
    records = [
        dict(row) for row in dataset.get("records") or [] if isinstance(row, dict)
    ]
    target, target_description = _target_name(
        dataset,
        target_horizon,
        target_mode,
    )
    if not records:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No supervised records are available.",
            "model_type": "portable_gradient_boosted_trees",
            "research_only": True,
            "affects_live_ranking": False,
            "affects_execution": False,
            "shadow_scoring_enabled": False,
        }

    frame = pd.DataFrame(records)
    if target not in frame.columns or "session" not in frame.columns:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "The supervised dataset is missing the requested target or session field.",
            "model_type": "portable_gradient_boosted_trees",
            "research_only": True,
            "affects_live_ranking": False,
            "affects_execution": False,
            "shadow_scoring_enabled": False,
        }
    frame = frame[frame[target].notna() & frame["session"].notna()].copy()
    if frame.empty:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No rows have both the requested target and a market session.",
            "model_type": "portable_gradient_boosted_trees",
            "research_only": True,
            "affects_live_ranking": False,
            "affects_execution": False,
            "shadow_scoring_enabled": False,
        }

    frame[target] = frame[target].astype(bool).astype(int)
    frame["_session_key"] = frame["session"].astype(str)
    frame["_time_key"] = pd.to_datetime(
        frame.get("timestamp"),
        utc=True,
        errors="coerce",
    )
    frame = frame.sort_values(
        ["_session_key", "_time_key", "symbol"],
        na_position="last",
    ).reset_index(drop=True)

    feature_columns = _usable_numeric_features(
        records,
        list(dataset.get("feature_columns") or []),
        minimum_non_null=max(10, min(50, len(frame) // 20)),
    )
    if len(feature_columns) < max(1, int(minimum_feature_count)):
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "Not enough live-compatible numeric features for boosted training.",
            "model_type": "portable_gradient_boosted_trees",
            "feature_count": len(feature_columns),
            "research_only": True,
            "affects_live_ranking": False,
            "affects_execution": False,
            "shadow_scoring_enabled": False,
        }

    oos = _chronological_oos(
        frame,
        feature_columns,
        target,
        min_train_sessions=min_train_sessions,
        test_sessions_per_fold=test_sessions_per_fold,
        embargo_sessions=embargo_sessions,
        min_train_rows=min_train_rows,
    )
    if oos.get("status") != "EVALUATED":
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No boosted chronological walk-forward fold met the training requirements.",
            "model_type": "portable_gradient_boosted_trees",
            "feature_count": len(feature_columns),
            "research_only": True,
            "affects_live_ranking": False,
            "affects_execution": False,
            "shadow_scoring_enabled": False,
        }

    actual = list(oos.get("actual") or [])
    probability = list(oos.get("probabilities") or [])
    ordered = sorted(
        zip(
            list(oos.get("prediction_rows") or []),
            actual,
            probability,
        ),
        key=lambda item: (
            str(item[0].get("session") or ""),
            str(item[0].get("timestamp") or ""),
        ),
    )

    calibration_report: dict[str, Any] = {
        "method": "none",
        "enabled": False,
        "reason": "Not enough OOS predictions for a separate calibration holdout.",
    }
    calibrator = None
    if len(ordered) >= 120:
        split = max(60, min(len(ordered) - 40, int(len(ordered) * 0.70)))
        cal_train = ordered[:split]
        cal_test = ordered[split:]
        train_actual = [item[1] for item in cal_train]
        train_probability = [item[2] for item in cal_train]
        test_actual = [item[1] for item in cal_test]
        test_probability = [item[2] for item in cal_test]
        trial = _fit_platt_calibrator(train_actual, train_probability)
        if trial and len(set(test_actual)) >= 2:
            calibrated_test = _apply_calibration(test_probability, trial)
            raw_brier = float(brier_score_loss(test_actual, test_probability))
            calibrated_brier = float(brier_score_loss(test_actual, calibrated_test))
            if calibrated_brier <= raw_brier + 0.002:
                calibrator = _fit_platt_calibrator(actual, probability)
                calibration_report = {
                    "method": "platt_logit",
                    "enabled": bool(calibrator),
                    "fit_rows": len(actual),
                    "selection_holdout_rows": len(test_actual),
                    "raw_holdout_brier": raw_brier,
                    "calibrated_holdout_brier": calibrated_brier,
                    "holdout_brier_improvement": raw_brier - calibrated_brier,
                }
            else:
                calibration_report = {
                    "method": "none",
                    "enabled": False,
                    "selection_holdout_rows": len(test_actual),
                    "raw_holdout_brier": raw_brier,
                    "calibrated_holdout_brier": calibrated_brier,
                    "reason": "Platt scaling worsened the chronological calibration holdout.",
                }

    generalization = _held_out_stock_generalization(
        frame,
        feature_columns,
        target,
        embargo_sessions=embargo_sessions,
        min_train_rows=min_train_rows,
    )

    gate_reasons: list[str] = []
    if int(oos.get("oos_rows") or 0) < max(1, int(minimum_oos_rows)):
        gate_reasons.append(
            f"Need at least {int(minimum_oos_rows)} boosted OOS predictions."
        )
    if len(oos.get("folds") or []) < 2:
        gate_reasons.append("Need at least two boosted chronological walk-forward folds.")
    auc = _number(oos.get("roc_auc"))
    if auc is None or auc < float(minimum_auc):
        gate_reasons.append(
            f"Boosted-model OOS ROC AUC must be at least {float(minimum_auc):.2f}."
        )
    skill = _number(oos.get("brier_skill_vs_naive"))
    if skill is None or skill <= 0:
        gate_reasons.append("Boosted-model OOS Brier skill must beat the naive benchmark.")

    if str(generalization.get("status") or "") != "EVALUATED":
        gate_reasons.append("Boosted held-out-stock validation did not produce an evaluated result.")
    else:
        gen_rows = int(generalization.get("oos_rows") or 0)
        gen_auc = _number(generalization.get("roc_auc"))
        gen_skill = _number(generalization.get("brier_skill_vs_naive"))
        if gen_rows < 100:
            gate_reasons.append("Boosted held-out-stock validation needs at least 100 OOS rows.")
        if gen_auc is None or gen_auc <= 0.50:
            gate_reasons.append("Boosted held-out-stock ROC AUC must exceed 0.50.")
        if gen_skill is None or gen_skill <= 0:
            gate_reasons.append(
                "Boosted held-out-stock Brier skill must beat its naive benchmark."
            )

    final_state = _fit_state(frame, feature_columns, target)
    sessions = sorted(frame["_session_key"].unique().tolist())
    trained_through = max(sessions) if sessions else ""
    identity = "|".join(
        [
            "gradient_boosted_trees_v1",
            target,
            str(dataset.get("session_mode") or ""),
            trained_through,
            str(len(frame)),
            ",".join(feature_columns),
        ]
    )
    model_id = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
    ready = not gate_reasons
    return {
        "id": model_id,
        "status": "READY_FOR_SHADOW_SCORING" if ready else "RESEARCH_ONLY",
        "shadow_scoring_enabled": ready,
        "research_only": True,
        "affects_live_ranking": False,
        "affects_execution": False,
        "model_type": "portable_gradient_boosted_trees",
        "model_family": "gradient_boosting",
        "target": target,
        "target_mode": str(target_mode),
        "target_horizon": int(target_horizon),
        "target_description": target_description,
        "session_mode": dataset.get("session_mode"),
        "profit_target_pct": _number(dataset.get("profit_target_pct")),
        "stop_loss_pct": _number(dataset.get("stop_loss_pct")),
        "trained_rows": len(frame),
        "trained_sessions": len(sessions),
        "trained_through_session": trained_through,
        "feature_count": len(feature_columns),
        "feature_columns": feature_columns,
        "minimum_feature_coverage": max(
            0.0,
            min(1.0, float(minimum_feature_coverage)),
        ),
        "validation": {
            "fold_count": len(oos.get("folds") or []),
            "oos_rows": int(oos.get("oos_rows") or 0),
            "roc_auc": oos.get("roc_auc"),
            "brier_score": oos.get("brier_score"),
            "naive_brier_score": oos.get("naive_brier_score"),
            "brier_skill_vs_naive": oos.get("brier_skill_vs_naive"),
            "positive_rate": float(sum(actual) / len(actual)),
            "folds": list(oos.get("folds") or []),
            "reliability_bins": _reliability_bins(actual, probability),
        },
        "generalization_gate": {
            "status": generalization.get("status"),
            "held_out_symbols": generalization.get("held_out_symbols"),
            "oos_rows": generalization.get("oos_rows"),
            "roc_auc": generalization.get("roc_auc"),
            "brier_score": generalization.get("brier_score"),
            "brier_skill_vs_naive": generalization.get("brier_skill_vs_naive"),
        },
        "calibration": calibration_report,
        "parameters": {
            **final_state,
            "calibrator": calibrator,
        },
        "gate_reasons": gate_reasons,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Research-only nonlinear challenger. Shallow boosted trees can learn thresholds "
            "and pairwise interactions between live-compatible detector features. It cannot "
            "change scanner ordering, strategy ranking, sizing, Paper Auto, or execution."
        ),
    }


def score_boosted_probability_model(
    model: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Score one fresh scanner/analyzer result with the boosted research challenger."""
    if not isinstance(model, dict) or not model.get("shadow_scoring_enabled"):
        return {
            "status": "MODEL_NOT_READY",
            "research_only": True,
            "affects_live_ranking": False,
            "affects_execution": False,
        }
    if str(model.get("model_type") or "") != "portable_gradient_boosted_trees":
        return {
            "status": "MODEL_TYPE_UNSUPPORTED",
            "research_only": True,
            "affects_live_ranking": False,
            "affects_execution": False,
        }

    parameters = (
        model.get("parameters")
        if isinstance(model.get("parameters"), dict)
        else {}
    )
    feature_columns = list(
        parameters.get("feature_columns")
        or model.get("feature_columns")
        or []
    )
    market_features = (
        result.get("market_features")
        if isinstance(result.get("market_features"), dict)
        else {}
    )
    raw_features = (
        market_features.get("features")
        if isinstance(market_features.get("features"), dict)
        else {}
    )
    feature_values = {
        (name if str(name).startswith("feature__") else f"feature__{name}"): value
        for name, value in raw_features.items()
    }

    present = sum(
        1
        for column in feature_columns
        if _number(feature_values.get(column)) is not None
    )
    coverage = float(present / len(feature_columns)) if feature_columns else 0.0
    minimum_coverage = float(model.get("minimum_feature_coverage") or 0.0)
    if not feature_columns or coverage < minimum_coverage:
        return {
            "status": "INSUFFICIENT_FEATURE_COVERAGE",
            "feature_coverage": coverage,
            "required_feature_coverage": minimum_coverage,
            "research_only": True,
            "affects_live_ranking": False,
            "affects_execution": False,
        }

    values: list[float] = []
    medians = dict(parameters.get("medians") or {})
    for column in feature_columns:
        value = _number(feature_values.get(column))
        if value is None:
            value = _number(medians.get(column))
        if value is None:
            return {
                "status": "MODEL_PARAMETERS_INVALID",
                "research_only": True,
                "affects_live_ranking": False,
                "affects_execution": False,
            }
        values.append(float(value))

    try:
        raw_probability = _predict_values(parameters, values)
    except (TypeError, ValueError, IndexError):
        return {
            "status": "MODEL_PARAMETERS_INVALID",
            "research_only": True,
            "affects_live_ranking": False,
            "affects_execution": False,
        }
    calibrated = _apply_calibration(
        [raw_probability],
        parameters.get("calibrator")
        if isinstance(parameters.get("calibrator"), dict)
        else None,
    )[0]
    return {
        "status": "SCORED",
        "probability": float(calibrated),
        "raw_probability": float(raw_probability),
        "feature_coverage": coverage,
        "model_id": model.get("id"),
        "model_type": model.get("model_type"),
        "target": model.get("target"),
        "target_description": model.get("target_description"),
        "target_horizon": model.get("target_horizon"),
        "research_only": True,
        "affects_live_ranking": False,
        "affects_execution": False,
    }
