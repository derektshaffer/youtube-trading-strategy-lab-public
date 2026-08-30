"""Portable, leakage-aware probability model for Trading Intelligence Lab shadow scoring.

The model is deliberately research-only. It trains a simple numeric logistic model
using chronological walk-forward validation, optionally applies Platt calibration,
serializes all parameters as JSON-safe values, and can score fresh scanner/analyzer
results without changing rankings or execution.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
from typing import Any

import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _logit(probability: float) -> float:
    value = min(1.0 - 1e-6, max(1e-6, float(probability)))
    return math.log(value / (1.0 - value))


def _safe_auc(actual: list[int], probabilities: list[float]) -> float | None:
    if not actual or len(set(actual)) < 2:
        return None
    return float(roc_auc_score(actual, probabilities))


def _target_name(
    dataset: dict[str, Any],
    target_horizon: int,
    target_mode: str,
) -> tuple[str, str]:
    mode = str(target_mode or "").strip().lower()
    horizon = int(target_horizon)
    if mode == "positive_return":
        return (
            f"label__positive_return_{horizon}bar",
            f"Price closes above the observation price after {horizon} bars.",
        )
    if mode == "target_before_stop":
        target = dataset.get("profit_target_pct")
        stop = dataset.get("stop_loss_pct")
        target_text = "configured upside barrier"
        stop_text = "configured downside barrier"
        if _number(target) is not None:
            target_text = f"+{float(target):g}%"
        if _number(stop) is not None:
            stop_text = f"-{float(stop):g}%"
        return (
            f"label__target_before_stop_{horizon}bar",
            f"Price reaches {target_text} before {stop_text} within {horizon} bars.",
        )
    raise ValueError("target_mode must be 'positive_return' or 'target_before_stop'.")


def _usable_numeric_features(
    records: list[dict[str, Any]],
    feature_columns: list[str],
    *,
    minimum_non_null: int = 20,
) -> list[str]:
    """Use live-compatible numeric detector features only.

    Context features are intentionally excluded from this first portable model because
    the current live observation record does not yet reconstruct the same lagged
    context vector. Missing live-compatible detector values may still be imputed, but
    the scorer enforces a minimum observed-feature coverage before emitting a number.
    """
    output: list[str] = []
    for column in feature_columns:
        name = str(column or "")
        if not name.startswith("feature__") or name.startswith("feature__context_"):
            continue
        seen = 0
        converted = 0
        for row in records:
            if name not in row or row.get(name) is None:
                continue
            seen += 1
            if _number(row.get(name)) is not None:
                converted += 1
        if seen >= max(1, int(minimum_non_null)) and converted == seen:
            output.append(name)
    return sorted(output)


def _fit_state(
    frame: pd.DataFrame,
    feature_columns: list[str],
    target: str,
) -> dict[str, Any]:
    medians: dict[str, float] = {}
    prepared = pd.DataFrame(index=frame.index)
    for column in feature_columns:
        series = frame[column].map(_number)
        clean = series.dropna()
        if clean.empty:
            raise ValueError(f"Feature {column} has no numeric training values.")
        median = float(clean.median())
        medians[column] = median
        prepared[column] = series.fillna(median).astype(float)

    means = {column: float(prepared[column].mean()) for column in feature_columns}
    scales: dict[str, float] = {}
    standardized = pd.DataFrame(index=frame.index)
    for column in feature_columns:
        scale = float(prepared[column].std(ddof=0))
        if not math.isfinite(scale) or scale <= 1e-12:
            scale = 1.0
        scales[column] = scale
        standardized[column] = (prepared[column] - means[column]) / scale

    model = LogisticRegression(max_iter=1000, solver="liblinear")
    model.fit(standardized[feature_columns], frame[target].astype(int))
    return {
        "feature_columns": list(feature_columns),
        "medians": medians,
        "means": means,
        "scales": scales,
        "coefficients": [
            float(value) for value in model.coef_[0].tolist()
        ],
        "intercept": float(model.intercept_[0]),
    }


def _predict_frame(state: dict[str, Any], frame: pd.DataFrame) -> list[float]:
    feature_columns = list(state.get("feature_columns") or [])
    coefficients = list(state.get("coefficients") or [])
    if not feature_columns or len(coefficients) != len(feature_columns):
        raise ValueError("Portable probability model parameters are incomplete.")

    probabilities: list[float] = []
    for _, row in frame.iterrows():
        score = float(state.get("intercept") or 0.0)
        for column, coefficient in zip(feature_columns, coefficients):
            value = _number(row.get(column))
            if value is None:
                value = float(state["medians"][column])
            mean = float(state["means"][column])
            scale = float(state["scales"][column]) or 1.0
            score += float(coefficient) * ((float(value) - mean) / scale)
        probabilities.append(_sigmoid(score))
    return probabilities


def _reliability_bins(
    actual: list[int],
    probabilities: list[float],
    *,
    bins: int = 10,
) -> list[dict[str, Any]]:
    groups: list[list[tuple[int, float]]] = [[] for _ in range(max(2, int(bins)))]
    for outcome, probability in zip(actual, probabilities):
        index = min(len(groups) - 1, int(max(0.0, min(0.999999, probability)) * len(groups)))
        groups[index].append((int(outcome), float(probability)))
    rows = []
    for index, group in enumerate(groups):
        if not group:
            continue
        rows.append(
            {
                "bin_low": index / len(groups),
                "bin_high": (index + 1) / len(groups),
                "rows": len(group),
                "mean_probability": float(sum(p for _, p in group) / len(group)),
                "observed_rate": float(sum(a for a, _ in group) / len(group)),
            }
        )
    return rows


def _fit_platt_calibrator(
    actual: list[int],
    probabilities: list[float],
) -> dict[str, float] | None:
    if len(actual) < 40 or len(set(actual)) < 2:
        return None
    frame = pd.DataFrame({"logit": [_logit(value) for value in probabilities]})
    model = LogisticRegression(max_iter=1000, solver="liblinear", C=1e6)
    model.fit(frame[["logit"]], actual)
    return {
        "coefficient": float(model.coef_[0][0]),
        "intercept": float(model.intercept_[0]),
    }


def _apply_calibration(
    probabilities: list[float],
    calibrator: dict[str, Any] | None,
) -> list[float]:
    if not calibrator:
        return [float(value) for value in probabilities]
    coefficient = float(calibrator.get("coefficient") or 0.0)
    intercept = float(calibrator.get("intercept") or 0.0)
    return [
        _sigmoid(intercept + coefficient * _logit(float(value)))
        for value in probabilities
    ]


def build_portable_probability_model(
    dataset: dict[str, Any],
    *,
    target_horizon: int = 15,
    target_mode: str = "target_before_stop",
    generalization: dict[str, Any] | None = None,
    min_train_sessions: int = 8,
    test_sessions_per_fold: int = 2,
    embargo_sessions: int = 1,
    min_train_rows: int = 250,
    minimum_feature_count: int = 5,
    minimum_feature_coverage: float = 0.55,
    minimum_oos_rows: int = 250,
    minimum_auc: float = 0.52,
) -> dict[str, Any]:
    """Train a portable logistic probability candidate after causal walk-forward testing."""
    records = [
        dict(row) for row in dataset.get("records") or [] if isinstance(row, dict)
    ]
    target, target_description = _target_name(
        dataset, target_horizon, target_mode
    )
    if not records:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No supervised records are available.",
            "research_only": True,
            "affects_live_ranking": False,
            "shadow_scoring_enabled": False,
        }

    frame = pd.DataFrame(records)
    if target not in frame.columns or "session" not in frame.columns:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "The supervised dataset is missing the requested target or session field.",
            "research_only": True,
            "affects_live_ranking": False,
            "shadow_scoring_enabled": False,
        }
    frame = frame[frame[target].notna() & frame["session"].notna()].copy()
    if frame.empty:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No rows have both the requested target and a market session.",
            "research_only": True,
            "affects_live_ranking": False,
            "shadow_scoring_enabled": False,
        }
    frame[target] = frame[target].astype(bool).astype(int)
    frame["_session_key"] = frame["session"].astype(str)
    frame["_time_key"] = pd.to_datetime(
        frame.get("timestamp"), utc=True, errors="coerce"
    )
    frame = frame.sort_values(
        ["_session_key", "_time_key", "symbol"], na_position="last"
    ).reset_index(drop=True)

    candidate_feature_columns = sorted(
        str(column)
        for column in (dataset.get("feature_columns") or [])
        if str(column).startswith("feature__")
        and not str(column).startswith("feature__context_")
    )
    if len(candidate_feature_columns) < max(1, int(minimum_feature_count)):
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": (
                "Not enough live-compatible detector feature columns are available "
                "to build the portable probability model."
            ),
            "feature_count": len(candidate_feature_columns),
            "research_only": True,
            "affects_live_ranking": False,
            "shadow_scoring_enabled": False,
        }

    sessions = sorted(frame["_session_key"].unique().tolist())
    min_train_sessions = max(2, int(min_train_sessions))
    test_sessions_per_fold = max(1, int(test_sessions_per_fold))
    embargo_sessions = max(0, int(embargo_sessions))
    min_train_rows = max(20, int(min_train_rows))

    all_actual: list[int] = []
    all_probability: list[float] = []
    all_naive: list[float] = []
    prediction_rows: list[dict[str, Any]] = []
    folds: list[dict[str, Any]] = []

    test_start = min_train_sessions + embargo_sessions
    fold_number = 0
    while test_start < len(sessions):
        test_sessions = sessions[test_start : test_start + test_sessions_per_fold]
        if not test_sessions:
            break
        train_end = max(0, test_start - embargo_sessions)
        train_sessions = sessions[:train_end]
        train = frame[frame["_session_key"].isin(train_sessions)]
        test = frame[frame["_session_key"].isin(test_sessions)]
        if (
            len(train) < min_train_rows
            or test.empty
            or train[target].nunique() < 2
        ):
            test_start += test_sessions_per_fold
            continue

        fold_feature_columns = _usable_numeric_features(
            train.to_dict("records"),
            candidate_feature_columns,
            minimum_non_null=max(10, min(50, len(train) // 20)),
        )
        if len(fold_feature_columns) < max(1, int(minimum_feature_count)):
            test_start += test_sessions_per_fold
            continue

        state = _fit_state(train, fold_feature_columns, target)
        probability = _predict_frame(state, test)
        actual = test[target].astype(int).tolist()
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
                "test_sessions": list(test_sessions),
                "test_rows": len(test),
                "feature_count": len(fold_feature_columns),
                "feature_columns": list(fold_feature_columns),
                "roc_auc": _safe_auc(actual, probability),
                "brier_score": model_brier,
                "naive_brier_score": naive_brier,
                "brier_skill_vs_naive": (
                    None if naive_brier <= 0
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
        test_start += test_sessions_per_fold

    if not all_actual:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No chronological walk-forward fold met the training requirements.",
            "session_count": len(sessions),
            "feature_count": len(candidate_feature_columns),
            "research_only": True,
            "affects_live_ranking": False,
            "shadow_scoring_enabled": False,
        }

    feature_columns = _usable_numeric_features(
        frame.to_dict("records"),
        candidate_feature_columns,
        minimum_non_null=max(10, min(50, len(frame) // 20)),
    )
    if len(feature_columns) < max(1, int(minimum_feature_count)):
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": (
                "Not enough live-compatible numeric detector features are populated "
                "in the full training history to build the final portable model."
            ),
            "feature_count": len(feature_columns),
            "research_only": True,
            "affects_live_ranking": False,
            "shadow_scoring_enabled": False,
        }

    model_brier = float(brier_score_loss(all_actual, all_probability))
    naive_brier = float(brier_score_loss(all_actual, all_naive))
    auc = _safe_auc(all_actual, all_probability)
    brier_skill = (
        None if naive_brier <= 0 else 1.0 - (model_brier / naive_brier)
    )

    # Calibration is fit on the earlier OOS calibration slice and evaluated on a
    # later chronological OOS holdout. The holdout is never reused for fitting.
    ordered = sorted(
        zip(prediction_rows, all_actual, all_probability),
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
        cal_train_actual = [item[1] for item in cal_train]
        cal_train_probability = [item[2] for item in cal_train]
        cal_test_actual = [item[1] for item in cal_test]
        cal_test_probability = [item[2] for item in cal_test]
        trial = _fit_platt_calibrator(
            cal_train_actual, cal_train_probability
        )
        if trial and len(set(cal_test_actual)) >= 2:
            calibrated_test = _apply_calibration(cal_test_probability, trial)
            raw_test_brier = float(
                brier_score_loss(cal_test_actual, cal_test_probability)
            )
            calibrated_test_brier = float(
                brier_score_loss(cal_test_actual, calibrated_test)
            )
            if calibrated_test_brier <= raw_test_brier + 0.002:
                calibrator = trial
                calibration_report = {
                    "method": "platt_logit",
                    "enabled": bool(calibrator),
                    "fit_rows": len(cal_train_actual),
                    "selection_holdout_rows": len(cal_test_actual),
                    "raw_holdout_brier": raw_test_brier,
                    "calibrated_holdout_brier": calibrated_test_brier,
                    "holdout_brier_improvement": (
                        raw_test_brier - calibrated_test_brier
                    ),
                }
            else:
                calibration_report = {
                    "method": "none",
                    "enabled": False,
                    "selection_holdout_rows": len(cal_test_actual),
                    "raw_holdout_brier": raw_test_brier,
                    "calibrated_holdout_brier": calibrated_test_brier,
                    "reason": "Platt scaling worsened the chronological calibration holdout.",
                }

    gate_reasons: list[str] = []
    if len(all_actual) < max(1, int(minimum_oos_rows)):
        gate_reasons.append(
            f"Need at least {int(minimum_oos_rows)} OOS predictions; found {len(all_actual)}."
        )
    if len(folds) < 2:
        gate_reasons.append("Need at least two chronological walk-forward folds.")
    if auc is None or auc < float(minimum_auc):
        gate_reasons.append(
            f"Portable-model OOS ROC AUC must be at least {float(minimum_auc):.2f}."
        )
    if brier_skill is None or brier_skill <= 0:
        gate_reasons.append("Portable-model OOS Brier skill must beat the naive benchmark.")

    generalization = dict(generalization or {})
    if str(generalization.get("status") or "") != "EVALUATED":
        gate_reasons.append(
            "Held-out-stock generalization has not produced an evaluated result."
        )
    else:
        gen_auc = _number(generalization.get("roc_auc"))
        gen_skill = _number(generalization.get("brier_skill_vs_naive"))
        gen_rows = int(generalization.get("oos_rows") or 0)
        if gen_rows < 100:
            gate_reasons.append("Held-out-stock validation needs at least 100 OOS rows.")
        if gen_auc is None or gen_auc <= 0.50:
            gate_reasons.append("Held-out-stock ROC AUC must exceed 0.50.")
        if gen_skill is None or gen_skill <= 0:
            gate_reasons.append(
                "Held-out-stock Brier skill must beat its naive benchmark."
            )

    final_state = _fit_state(frame, feature_columns, target)
    trained_through = max(sessions) if sessions else ""
    identity = "|".join(
        [
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
        "model_type": "portable_numeric_logistic_regression",
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
        "minimum_feature_coverage": max(0.0, min(1.0, float(minimum_feature_coverage))),
        "validation": {
            "fold_count": len(folds),
            "oos_rows": len(all_actual),
            "roc_auc": auc,
            "brier_score": model_brier,
            "naive_brier_score": naive_brier,
            "brier_skill_vs_naive": brier_skill,
            "positive_rate": float(sum(all_actual) / len(all_actual)),
            "folds": folds,
            "reliability_bins": _reliability_bins(
                all_actual, all_probability
            ),
        },
        "generalization_gate": {
            "status": generalization.get("status"),
            "oos_rows": generalization.get("oos_rows"),
            "roc_auc": generalization.get("roc_auc"),
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
            "Shadow probabilities are research-only. This model uses only live-compatible "
            "numeric detector features, passes a minimum feature-coverage check on each score, "
            "and never changes scanner ordering, strategy ranking, or execution."
        ),
    }


def score_scan_result_probability(
    model: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Score one fresh scanner/analyzer result with a portable research model."""
    if not isinstance(model, dict) or not model.get("shadow_scoring_enabled"):
        return {
            "status": "MODEL_NOT_READY",
            "research_only": True,
            "affects_live_ranking": False,
        }

    parameters = model.get("parameters") if isinstance(model.get("parameters"), dict) else {}
    feature_columns = list(parameters.get("feature_columns") or model.get("feature_columns") or [])
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
        1 for column in feature_columns
        if _number(feature_values.get(column)) is not None
    )
    coverage = (
        float(present / len(feature_columns)) if feature_columns else 0.0
    )
    minimum_coverage = float(model.get("minimum_feature_coverage") or 0.0)
    if not feature_columns or coverage < minimum_coverage:
        return {
            "status": "INSUFFICIENT_FEATURE_COVERAGE",
            "feature_coverage": coverage,
            "required_feature_coverage": minimum_coverage,
            "research_only": True,
            "affects_live_ranking": False,
        }

    score = float(parameters.get("intercept") or 0.0)
    coefficients = list(parameters.get("coefficients") or [])
    if len(coefficients) != len(feature_columns):
        return {
            "status": "MODEL_PARAMETERS_INVALID",
            "research_only": True,
            "affects_live_ranking": False,
        }

    for column, coefficient in zip(feature_columns, coefficients):
        value = _number(feature_values.get(column))
        if value is None:
            value = _number((parameters.get("medians") or {}).get(column))
        mean = _number((parameters.get("means") or {}).get(column))
        scale = _number((parameters.get("scales") or {}).get(column))
        if value is None or mean is None:
            return {
                "status": "MODEL_PARAMETERS_INVALID",
                "research_only": True,
                "affects_live_ranking": False,
            }
        if scale is None or abs(scale) <= 1e-12:
            scale = 1.0
        score += float(coefficient) * ((value - mean) / scale)

    raw_probability = _sigmoid(score)
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
        "target": model.get("target"),
        "target_description": model.get("target_description"),
        "target_horizon": model.get("target_horizon"),
        "research_only": True,
        "affects_live_ranking": False,
        "affects_execution": False,
    }
