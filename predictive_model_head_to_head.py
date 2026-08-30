"""Fast historical head-to-head comparison for validated probability models.

This module intentionally reuses validation metrics that were already produced
during the expensive historical backfill. It does not retrain models. A comparison
is only allowed when candidates were evaluated on the same chronological OOS
folds, target contract, and training snapshot.
"""

from __future__ import annotations

import math
from typing import Any, Iterable


MIN_BRIER_SCORE_EDGE = 0.001
MIN_BRIER_SKILL_EDGE = 0.002
MIN_AUC_EDGE = 0.005
MAX_AUC_TRADEOFF = 0.02
MAX_BRIER_SKILL_TRADEOFF = 0.005


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def model_family_label(model: dict[str, Any]) -> str:
    family = str(
        model.get("model_family")
        or model.get("model_type")
        or "probability_model"
    ).strip()
    return family.replace("_", " ").title()


def _fold_signature(model: dict[str, Any]) -> tuple[Any, ...]:
    validation = (
        model.get("validation")
        if isinstance(model.get("validation"), dict)
        else {}
    )
    signature: list[Any] = []
    for fold in validation.get("folds") or []:
        if not isinstance(fold, dict):
            continue
        signature.append(
            (
                tuple(str(item) for item in fold.get("test_sessions") or []),
                int(fold.get("test_rows") or 0),
            )
        )
    return tuple(signature)


def historical_evaluation_signature(model: dict[str, Any]) -> tuple[Any, ...]:
    """Return the fields that prove two models saw the same historical test rows."""
    validation = (
        model.get("validation")
        if isinstance(model.get("validation"), dict)
        else {}
    )
    return (
        str(model.get("target") or ""),
        int(model.get("target_horizon") or 0),
        str(model.get("session_mode") or ""),
        _number(model.get("profit_target_pct")),
        _number(model.get("stop_loss_pct")),
        int(model.get("trained_rows") or 0),
        int(model.get("trained_sessions") or 0),
        str(model.get("trained_through_session") or ""),
        int(validation.get("oos_rows") or 0),
        int(validation.get("fold_count") or 0),
        _fold_signature(model),
    )


def _metrics(model: dict[str, Any]) -> dict[str, Any]:
    validation = (
        model.get("validation")
        if isinstance(model.get("validation"), dict)
        else {}
    )
    generalization = (
        model.get("generalization_gate")
        if isinstance(model.get("generalization_gate"), dict)
        else {}
    )
    return {
        "model_id": str(model.get("id") or ""),
        "model_family": model_family_label(model),
        "model_type": str(model.get("model_type") or ""),
        "oos_rows": int(validation.get("oos_rows") or 0),
        "fold_count": int(validation.get("fold_count") or 0),
        "roc_auc": _number(validation.get("roc_auc")),
        "brier_score": _number(validation.get("brier_score")),
        "brier_skill_vs_naive": _number(validation.get("brier_skill_vs_naive")),
        "held_out_oos_rows": int(generalization.get("oos_rows") or 0),
        "held_out_roc_auc": _number(generalization.get("roc_auc")),
        "held_out_brier_skill_vs_naive": _number(
            generalization.get("brier_skill_vs_naive")
        ),
    }


def _compare_two(
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, Any]:
    a = _metrics(first)
    b = _metrics(second)
    if not a["model_id"] or not b["model_id"]:
        return {
            "status": "NOT_COMPARABLE",
            "reason": "One candidate is missing a model id.",
        }

    required = ("roc_auc", "brier_score", "brier_skill_vs_naive")
    if any(a.get(key) is None or b.get(key) is None for key in required):
        return {
            "status": "NOT_COMPARABLE",
            "reason": "Both models need AUC, Brier score, and Brier skill.",
            "models": [a, b],
        }

    auc_delta = float(a["roc_auc"] - b["roc_auc"])
    brier_delta = float(b["brier_score"] - a["brier_score"])
    skill_delta = float(a["brier_skill_vs_naive"] - b["brier_skill_vs_naive"])

    # Positive deltas favor model A.
    a_probability_edge = (
        brier_delta >= MIN_BRIER_SCORE_EDGE
        and skill_delta >= MIN_BRIER_SKILL_EDGE
        and auc_delta >= -MAX_AUC_TRADEOFF
    )
    b_probability_edge = (
        brier_delta <= -MIN_BRIER_SCORE_EDGE
        and skill_delta <= -MIN_BRIER_SKILL_EDGE
        and auc_delta <= MAX_AUC_TRADEOFF
    )

    # If probability quality is essentially tied, a meaningful AUC edge may
    # identify the historical leader as long as probability quality is not worse.
    probability_near_tie = (
        abs(brier_delta) < MIN_BRIER_SCORE_EDGE
        and abs(skill_delta) < MIN_BRIER_SKILL_EDGE
    )
    a_auc_edge = (
        probability_near_tie
        and auc_delta >= MIN_AUC_EDGE
        and skill_delta >= -MAX_BRIER_SKILL_TRADEOFF
    )
    b_auc_edge = (
        probability_near_tie
        and auc_delta <= -MIN_AUC_EDGE
        and skill_delta <= MAX_BRIER_SKILL_TRADEOFF
    )

    winner = None
    reason = ""
    if a_probability_edge or a_auc_edge:
        winner = a
    elif b_probability_edge or b_auc_edge:
        winner = b

    if winner is None:
        status = "NO_CLEAR_HISTORICAL_LEADER"
        reason = (
            "The models were tested on the same chronological OOS rows, but neither "
            "has a material enough edge in probability quality or discrimination."
        )
    else:
        status = "PROVISIONAL_HISTORICAL_LEADER"
        loser = b if winner["model_id"] == a["model_id"] else a
        if winner["model_id"] == a["model_id"]:
            winner_auc_delta = auc_delta
            winner_brier_delta = brier_delta
            winner_skill_delta = skill_delta
        else:
            winner_auc_delta = -auc_delta
            winner_brier_delta = -brier_delta
            winner_skill_delta = -skill_delta
        reason = (
            f"{winner['model_family']} leads {loser['model_family']} on the same "
            f"{winner['oos_rows']:,} untouched chronological OOS predictions: "
            f"AUC delta {winner_auc_delta:+.3f}, Brier-score improvement "
            f"{winner_brier_delta:+.4f}, Brier-skill delta "
            f"{winner_skill_delta * 100:+.2f} percentage points."
        )

    return {
        "status": status,
        "leader_model_id": winner["model_id"] if winner else None,
        "leader_model_family": winner["model_family"] if winner else None,
        "models": [a, b],
        "reason": reason,
        "same_oos_rows": True,
        "auc_delta_first_minus_second": auc_delta,
        "brier_score_improvement_first_vs_second": brier_delta,
        "brier_skill_delta_first_minus_second": skill_delta,
        "research_only": True,
        "affects_live_ranking": False,
        "affects_execution": False,
        "live_confirmation_required": True,
    }


def build_historical_model_head_to_head(
    models: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Compare the newest pair of ready models sharing the exact OOS fold signature."""
    candidates = [
        dict(model)
        for model in models or []
        if isinstance(model, dict) and model.get("shadow_scoring_enabled")
    ]
    if len(candidates) < 2:
        return {
            "status": "INSUFFICIENT_MODELS",
            "leader_model_id": None,
            "models": [_metrics(model) for model in candidates],
            "research_only": True,
            "live_confirmation_required": True,
        }

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    order: list[tuple[Any, ...]] = []
    for model in candidates:
        signature = historical_evaluation_signature(model)
        if signature not in groups:
            groups[signature] = []
            order.append(signature)
        groups[signature].append(model)

    for signature in order:
        group = groups[signature]
        if len(group) >= 2:
            result = _compare_two(group[0], group[1])
            result["comparison_signature"] = {
                "target": signature[0],
                "target_horizon": signature[1],
                "session_mode": signature[2],
                "trained_rows": signature[5],
                "trained_sessions": signature[6],
                "trained_through_session": signature[7],
                "oos_rows": signature[8],
                "fold_count": signature[9],
            }
            return result

    return {
        "status": "NOT_COMPARABLE",
        "leader_model_id": None,
        "reason": (
            "Validated models exist, but no pair was evaluated on the exact same "
            "historical target, training snapshot, and chronological OOS folds."
        ),
        "models": [_metrics(model) for model in candidates],
        "research_only": True,
        "live_confirmation_required": True,
    }
