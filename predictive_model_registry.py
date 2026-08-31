"""Research-only champion/challenger selection for shadow probability models."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
from typing import Any, Iterable


DEFAULT_MAX_SHADOW_MODELS = 6
REQUIRED_TRAINING_DATA_INTEGRITY_CONTRACT = "split_safe_raw_v1"
MIN_PROMOTION_DECISIONS = 50
MIN_PROMOTION_SYMBOLS = 5
MIN_PROMOTION_SESSIONS = 5
MIN_BRIER_SKILL_ADVANTAGE = 0.01
MAX_ECE_DISADVANTAGE = 0.02
MIN_BRIER_SCORE_ADVANTAGE = 0.01


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def model_target_key(model: dict[str, Any]) -> str:
    return "|".join(
        [
            str(model.get("target") or ""),
            str(model.get("session_mode") or ""),
            str(model.get("profit_target_pct") or ""),
            str(model.get("stop_loss_pct") or ""),
        ]
    )


def ready_shadow_models(
    runs: Iterable[dict[str, Any]],
    *,
    maximum: int = DEFAULT_MAX_SHADOW_MODELS,
) -> list[dict[str, Any]]:
    """Return newest unique shadow-ready models, bounded to avoid needless scoring."""
    prepared: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for run in runs or []:
        if not isinstance(run, dict):
            continue
        dataset_summary = (
            run.get("dataset_summary")
            if isinstance(run.get("dataset_summary"), dict)
            else {}
        )
        if (
            str(dataset_summary.get("market_data_integrity_contract") or "")
            != REQUIRED_TRAINING_DATA_INTEGRITY_CONTRACT
        ):
            # Old models may have been trained on split-adjusted price/liquidity
            # context. Keep them in history, but do not surface them as current
            # shadow candidates after the raw/split-safe integrity contract changed.
            continue
        candidates = [
            item
            for item in run.get("probability_models") or []
            if isinstance(item, dict)
        ]
        legacy = run.get("probability_model")
        if isinstance(legacy, dict) and not any(
            str(item.get("id") or "") == str(legacy.get("id") or "")
            for item in candidates
        ):
            candidates.insert(0, legacy)

        for model in candidates:
            if not model.get("shadow_scoring_enabled"):
                continue
            model_id = str(model.get("id") or "").strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            stamp = str(
                run.get("completed_at")
                or model.get("created_at")
                or ""
            )
            prepared.append((stamp, deepcopy(model)))
    prepared.sort(key=lambda item: item[0], reverse=True)
    return [model for _, model in prepared[: max(1, int(maximum))]]


def _monitor_rows(monitor: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("model_id") or ""): dict(item)
        for item in monitor.get("models") or []
        if isinstance(item, dict) and str(item.get("model_id") or "").strip()
    }


def _has_promotion_breadth(row: dict[str, Any]) -> bool:
    return (
        int(row.get("evaluated_decisions") or 0) >= MIN_PROMOTION_DECISIONS
        and int(row.get("symbol_count") or 0) >= MIN_PROMOTION_SYMBOLS
        and int(row.get("session_count") or 0) >= MIN_PROMOTION_SESSIONS
    )


def _candidate_beats_incumbent(
    candidate: dict[str, Any],
    incumbent: dict[str, Any],
) -> tuple[bool, str]:
    if str(candidate.get("status") or "") != "HEALTHY":
        return False, "Challenger has not reached HEALTHY live-shadow status."
    if not _has_promotion_breadth(candidate):
        return False, "Challenger does not yet have enough independent live-shadow breadth."

    candidate_skill = _number(candidate.get("brier_skill_vs_naive"))
    candidate_ece = _number(candidate.get("expected_calibration_error"))
    candidate_brier = _number(candidate.get("brier_score"))
    incumbent_status = str(incumbent.get("status") or "")
    incumbent_skill = _number(incumbent.get("brier_skill_vs_naive"))
    incumbent_ece = _number(incumbent.get("expected_calibration_error"))
    incumbent_brier = _number(incumbent.get("brier_score"))

    if incumbent_status == "DRIFT_ALERT":
        if candidate_skill is not None and candidate_skill > 0:
            return True, "Healthy challenger replaces a drift-alert incumbent."
        return False, "Incumbent is drifting, but challenger has not shown positive live Brier skill."

    if not _has_promotion_breadth(incumbent):
        return False, "Incumbent has not accumulated enough comparable live-shadow evidence yet."

    skill_win = (
        candidate_skill is not None
        and incumbent_skill is not None
        and candidate_skill >= incumbent_skill + MIN_BRIER_SKILL_ADVANTAGE
        and (
            candidate_ece is None
            or incumbent_ece is None
            or candidate_ece <= incumbent_ece + MAX_ECE_DISADVANTAGE
        )
    )
    brier_win = (
        candidate_brier is not None
        and incumbent_brier is not None
        and candidate_brier <= incumbent_brier - MIN_BRIER_SCORE_ADVANTAGE
        and (
            candidate_ece is None
            or incumbent_ece is None
            or candidate_ece <= incumbent_ece
        )
    )
    if skill_win:
        return True, "Challenger has materially better live Brier skill without a meaningful calibration penalty."
    if brier_win:
        return True, "Challenger has materially lower live Brier score with equal-or-better calibration."
    return False, "Challenger has not demonstrated a material live advantage over the incumbent."


def build_model_registry(
    models: Iterable[dict[str, Any]],
    monitor: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Choose one research-only shadow champion and keep others as challengers.

    Initial selection is provisional. After live outcomes mature, replacement is
    evidence-based and only compares models with the same target/session contract.
    """
    models = [dict(item) for item in models or [] if isinstance(item, dict)]
    by_id = {
        str(item.get("id") or ""): item
        for item in models
        if str(item.get("id") or "").strip()
    }
    previous = dict(previous or {})
    if not by_id:
        return {
            "status": "NO_READY_MODELS",
            "champion_model_id": None,
            "challenger_model_ids": [],
            "research_only": True,
            "affects_live_ranking": False,
            "affects_execution": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    prior_id = str(previous.get("champion_model_id") or "").strip()
    champion_id = prior_id if prior_id in by_id else next(iter(by_id))
    champion_model = by_id[champion_id]
    target_key = model_target_key(champion_model)
    monitor_by_id = _monitor_rows(monitor or {})
    incumbent_live = monitor_by_id.get(champion_id) or {}

    compatible = [
        model_id
        for model_id, model in by_id.items()
        if model_id != champion_id and model_target_key(model) == target_key
    ]
    incompatible = [
        model_id
        for model_id, model in by_id.items()
        if model_id != champion_id and model_target_key(model) != target_key
    ]

    promoted_from = None
    promotion_reason = ""
    promotable: list[tuple[float, float, str, str]] = []
    for model_id in compatible:
        live = monitor_by_id.get(model_id) or {}
        wins, reason = _candidate_beats_incumbent(live, incumbent_live)
        if not wins:
            continue
        skill = _number(live.get("brier_skill_vs_naive"))
        ece = _number(live.get("expected_calibration_error"))
        promotable.append((
            -(skill if skill is not None else -999.0),
            ece if ece is not None else 999.0,
            model_id,
            reason,
        ))

    if promotable:
        promotable.sort()
        _, _, winner_id, reason = promotable[0]
        promoted_from = champion_id
        champion_id = winner_id
        champion_model = by_id[champion_id]
        target_key = model_target_key(champion_model)
        incumbent_live = monitor_by_id.get(champion_id) or {}
        promotion_reason = reason
        compatible = [
            model_id
            for model_id, model in by_id.items()
            if model_id != champion_id and model_target_key(model) == target_key
        ]
        incompatible = [
            model_id
            for model_id, model in by_id.items()
            if model_id != champion_id and model_target_key(model) != target_key
        ]

    live_status = str(incumbent_live.get("status") or "")
    if live_status == "DRIFT_ALERT":
        status = "CHAMPION_DRIFT_ALERT"
    elif _has_promotion_breadth(incumbent_live):
        status = "CHAMPION_CONFIRMED"
    else:
        status = "CHAMPION_PROVISIONAL"

    if promoted_from:
        reason = promotion_reason
    elif prior_id and prior_id == champion_id:
        reason = "Incumbent retained; no compatible challenger has proved a material live advantage."
    else:
        reason = "Initial shadow champion selected from the newest historically validated model."

    return {
        "status": status,
        "champion_model_id": champion_id,
        "champion_target_key": target_key,
        "challenger_model_ids": compatible,
        "incompatible_ready_model_ids": incompatible,
        "promoted_from_model_id": promoted_from,
        "decision_reason": reason,
        "champion_live_status": live_status or "COLLECTING",
        "champion_evaluated_decisions": int(incumbent_live.get("evaluated_decisions") or 0),
        "research_only": True,
        "affects_live_ranking": False,
        "affects_execution": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Champion selection only controls which research-only probability is displayed. "
            "All ready compatible models may be scored in parallel for fair challenger evaluation."
        ),
    }
