"""Profit-first candidate ranking for automatic strict revalidation.

This module is deliberately lightweight and deterministic. It never promotes a
strategy or relaxes validation gates; it only decides which *testable* research
candidate deserves scarce validation compute next.
"""

from __future__ import annotations

from math import isfinite
from typing import Any

from trading_intelligence_core import research_readiness


CURRENT_AUTONOMOUS_VALIDATION_METHOD_VERSION = 5


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if isfinite(result) else default


def _metrics_positive(metrics: Any) -> bool:
    row = metrics if isinstance(metrics, dict) else {}
    return int(_number(row.get("trade_count"))) > 0 and _number(row.get("net_pnl")) > 0.0


def _latest_validation_by_strategy(
    validation_runs: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    ordered = sorted(
        [item for item in validation_runs if isinstance(item, dict)],
        key=lambda item: str(item.get("generated_at") or ""),
        reverse=True,
    )
    latest: dict[str, dict[str, Any]] = {}
    for run in ordered:
        strategy_id = str(run.get("strategy_id") or "").strip()
        if strategy_id and strategy_id not in latest:
            latest[strategy_id] = run
    return latest


def _is_current_protocol(run: dict[str, Any]) -> bool:
    autonomous = bool(run.get("autonomous"))
    version = int(_number(run.get("validation_method_version")))
    verdict = (
        run.get("evidence_verdict")
        if isinstance(run.get("evidence_verdict"), dict)
        else {}
    )
    verdict_code = str(verdict.get("code") or "").strip()
    return bool(
        (autonomous and version >= CURRENT_AUTONOMOUS_VALIDATION_METHOD_VERSION)
        or (not autonomous and verdict_code)
    )


def _legacy_evidence_score(run: dict[str, Any]) -> tuple[float, int]:
    validation = run.get("validation_metrics") if isinstance(run.get("validation_metrics"), dict) else {}
    holdout = run.get("holdout_metrics") if isinstance(run.get("holdout_metrics"), dict) else {}
    stress = run.get("stress_metrics") if isinstance(run.get("stress_metrics"), dict) else {}
    robustness = run.get("robustness") if isinstance(run.get("robustness"), dict) else {}
    walk = run.get("walk_forward_summary") if isinstance(run.get("walk_forward_summary"), dict) else {}

    positive_count = sum(
        (
            _metrics_positive(validation),
            _metrics_positive(holdout),
            _metrics_positive(stress),
        )
    )
    robustness_score = max(0.0, min(100.0, _number(robustness.get("score"))))
    walk_pct = max(
        0.0,
        min(
            100.0,
            _number(
                walk.get("profitable_fold_pct")
                if walk.get("profitable_fold_pct") is not None
                else walk.get("profitable_pct")
            ),
        ),
    )
    holdout_pnl = max(-100.0, min(100.0, _number(holdout.get("net_pnl"))))
    total_trades = sum(
        max(0, int(_number(metrics.get("trade_count"))))
        for metrics in (validation, holdout, stress)
    )

    score = (
        positive_count * 30.0
        + robustness_score * 0.25
        + walk_pct * 0.15
        + holdout_pnl * 0.10
        + min(10.0, total_trades / 10.0)
    )
    return round(score, 3), positive_count


def active_profit_first_validation_job(library: dict[str, Any]) -> dict[str, Any] | None:
    for item in library.get("research_queue") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != "autonomous_validation":
            continue
        if str(item.get("status") or "") not in {"queued", "running", "retry"}:
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if str(payload.get("origin") or "") in {
            "profit_first_revalidation",
            "automatic_profit_first_validation",
        }:
            return item
    return None


def profit_first_validation_candidates(
    library: dict[str, Any],
    *,
    maximum: int = 3,
    minimum_positive_legacy_periods: int = 2,
) -> dict[str, Any]:
    """Rank testable unproven strategies without retesting current-protocol failures.

    Phase 1 prefers legacy candidates with evidence worth rechecking.
    Phase 2 only runs after those are exhausted and selects ready strategies that
    have never had a validation record.
    """
    strategies = [
        item
        for item in library.get("strategies") or []
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    latest = _latest_validation_by_strategy(
        [
            item
            for item in library.get("validation_runs") or []
            if isinstance(item, dict)
        ]
    )

    legacy: list[dict[str, Any]] = []
    fresh: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    current_protocol_skipped: list[str] = []

    for strategy in strategies:
        strategy_id = str(strategy.get("id") or "").strip()
        readiness = research_readiness(strategy)
        run = latest.get(strategy_id)

        if run is not None and _is_current_protocol(run):
            current_protocol_skipped.append(strategy_id)
            continue

        if str(readiness.get("label") or "") != "ready_for_backtest":
            blocked.append(
                {
                    "strategy_id": strategy_id,
                    "strategy_name": strategy.get("name"),
                    "readiness": readiness.get("label"),
                    "critical_missing": list(
                        readiness.get("semantic_critical_missing_requirements") or []
                    ),
                }
            )
            continue

        if run is None:
            fresh.append(
                {
                    "strategy_id": strategy_id,
                    "strategy_name": strategy.get("name"),
                    "phase": "never_validated",
                    "score": round(_number(readiness.get("score")), 3),
                    "latest_validation_generated_at": None,
                    "positive_evidence_periods": 0,
                }
            )
            continue

        score, positive_count = _legacy_evidence_score(run)
        if positive_count < max(1, int(minimum_positive_legacy_periods)):
            continue
        legacy.append(
            {
                "strategy_id": strategy_id,
                "strategy_name": strategy.get("name"),
                "phase": "legacy_revalidation",
                "score": score,
                "latest_validation_generated_at": run.get("generated_at"),
                "positive_evidence_periods": positive_count,
                "optimizer_status": run.get("optimizer_status"),
                "validation_method_version": run.get("validation_method_version"),
                "holdout_net_pnl": _number(
                    (run.get("holdout_metrics") or {}).get("net_pnl")
                    if isinstance(run.get("holdout_metrics"), dict)
                    else 0.0
                ),
            }
        )

    legacy.sort(
        key=lambda item: (
            _number(item.get("score")),
            int(item.get("positive_evidence_periods") or 0),
            _number(item.get("holdout_net_pnl")),
        ),
        reverse=True,
    )
    fresh.sort(
        key=lambda item: _number(item.get("score")),
        reverse=True,
    )

    phase = "legacy_revalidation" if legacy else "never_validated"
    ranked = legacy if legacy else fresh
    return {
        "phase": phase,
        "candidates": ranked[: max(0, int(maximum))],
        "eligible_count": len(ranked),
        "legacy_eligible_count": len(legacy),
        "never_validated_ready_count": len(fresh),
        "fidelity_blocked_count": len(blocked),
        "fidelity_blocked": blocked[:20],
        "current_protocol_skipped_count": len(current_protocol_skipped),
    }


def profit_first_candidate_dedupe_key(candidate: dict[str, Any]) -> str:
    strategy_id = str(candidate.get("strategy_id") or "").strip()
    stamp = str(candidate.get("latest_validation_generated_at") or "never")
    safe_stamp = "".join(ch for ch in stamp if ch.isalnum())[-24:] or "never"
    return (
        "profit-first-validation:"
        f"v{CURRENT_AUTONOMOUS_VALIDATION_METHOD_VERSION}:"
        f"{strategy_id}:{safe_stamp}"
    )
