"""Validation and walk-forward research for Trading Intelligence Lab."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, replace
from statistics import mean, median, pstdev
from typing import Any, Callable

import pandas as pd

from youtube_strategy_engine import (
    AppError,
    BacktestSettings,
    OptimizationSettings,
    bars_to_frame,
    normalize_machine_rules,
    optimize_stock_strategies,
    run_backtest,
    safe_float,
    summarize_trades,
)


def _frame_to_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    keep = ["open", "high", "low", "close", "volume", "timestamp"]
    return frame[keep].to_dict("records")


def _profit_factor(metrics: dict[str, Any]) -> float:
    value = safe_float(metrics.get("profit_factor"))
    if value is None:
        return 2.0 if int(safe_float(metrics.get("trade_count"), 0) or 0) > 0 else 0.0
    return max(0.0, value)


def validation_strength(
    optimization_report: dict[str, Any],
    walk_forward_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Transparent 0-100 robustness score; it is not a forecast of future returns."""
    winner = optimization_report.get("winner") or {}
    training = winner.get("training_metrics") or {}
    validation = winner.get("validation_metrics") or {}
    holdout = winner.get("holdout_metrics") or {}
    stress = winner.get("stress_metrics") or {}
    development_sensitivity = winner.get("execution_sensitivity") or {}
    holdout_sensitivity = winner.get("holdout_execution_sensitivity") or {}
    uses_holdout_sensitivity = bool(holdout_sensitivity)
    sensitivity = (
        holdout_sensitivity
        if uses_holdout_sensitivity
        else development_sensitivity
    )
    sensitivity_scope = (
        "untouched_holdout"
        if uses_holdout_sensitivity
        else ("validation" if sensitivity else None)
    )
    sensitivity_score = safe_float(sensitivity.get("score"))
    sensitivity_label = str(sensitivity.get("label") or "").strip().upper()
    sensitivity_pass = bool(sensitivity.get("passes_validation_gate"))
    settings = optimization_report.get("optimization_settings") or {}
    minimum_validation = max(1, int(safe_float(settings.get("minimum_validation_trades"), 2) or 2))
    maximum_drawdown = max(0.5, safe_float(settings.get("maximum_drawdown_pct"), 15.0) or 15.0)

    score = 0.0
    reasons: list[str] = []

    validation_trades = int(safe_float(validation.get("trade_count"), 0) or 0)
    holdout_trades = int(safe_float(holdout.get("trade_count"), 0) or 0)
    validation_pnl = safe_float(validation.get("net_pnl"), 0.0) or 0.0
    holdout_pnl = safe_float(holdout.get("net_pnl"), 0.0) or 0.0
    stress_pnl = safe_float(stress.get("net_pnl"), 0.0) or 0.0

    if validation_trades >= minimum_validation:
        score += 10
    else:
        reasons.append("Validation sample is small.")
    if validation_pnl > 0:
        score += 15
    else:
        reasons.append("Validation P/L is not positive.")

    if holdout_trades >= minimum_validation:
        score += 10
    else:
        reasons.append("Final holdout sample is small.")
    if holdout_pnl > 0:
        score += 20
    else:
        reasons.append("Final untouched holdout P/L is not positive.")

    sensitivity_applicable = (
        sensitivity_score is not None
        and sensitivity_label not in {"", "NOT APPLICABLE"}
    )
    if sensitivity_applicable:
        score += max(0.0, min(10.0, sensitivity_score / 10.0))
        if sensitivity_label == "FRAGILE":
            reasons.append("The edge degrades quickly across the execution-cost sensitivity curve.")
        elif sensitivity_label == "MIXED":
            reasons.append("Execution-cost robustness is mixed across the tested multiplier range.")
    elif uses_holdout_sensitivity:
        reasons.append(
            "Untouched-holdout execution-cost robustness could not be established."
        )
    elif stress_pnl > 0:
        score += 10
    else:
        reasons.append("The setup is sensitive to higher assumed trading costs.")

    holdout_pf = _profit_factor(holdout)
    score += min(10.0, max(0.0, holdout_pf / 1.5 * 10.0))

    worst_drawdown = max(
        safe_float(training.get("max_drawdown_pct"), 0.0) or 0.0,
        safe_float(validation.get("max_drawdown_pct"), 0.0) or 0.0,
        safe_float(holdout.get("max_drawdown_pct"), 0.0) or 0.0,
    )
    score += 15.0 * max(0.0, min(1.0, 1.0 - worst_drawdown / maximum_drawdown))

    positive_periods = sum(
        1
        for metrics in (training, validation, holdout)
        if int(safe_float(metrics.get("trade_count"), 0) or 0) > 0
        and (safe_float(metrics.get("net_pnl"), 0.0) or 0.0) > 0
    )
    score += positive_periods / 3.0 * 10.0

    base_score = max(0.0, min(100.0, score))
    final_score = base_score
    walk_score = None
    if walk_forward_report:
        walk_score = safe_float((walk_forward_report.get("summary") or {}).get("score"))
        if walk_score is not None:
            final_score = base_score * 0.60 + walk_score * 0.40

    raw_score = round(max(0.0, min(100.0, final_score)), 1)

    # Robustness must not contradict the optimizer's own stability verdict. Additive
    # scoring can otherwise reward tiny positive unseen slices + low drawdown enough
    # to produce a 90+ score even when the training period is negative or the optimizer
    # explicitly labels the setup UNSTABLE.
    optimizer_status = str(winner.get("status") or "").strip().upper()
    training_pnl = safe_float(training.get("net_pnl"), 0.0) or 0.0
    validation_pf = _profit_factor(validation)
    stress_pf = _profit_factor(stress)
    score_cap = 100.0
    penalties: list[str] = []

    status_caps = {
        "LIMITED DATA": 39.0,
        "DRAWDOWN TOO HIGH": 39.0,
        "NO VALIDATED EDGE": 39.0,
        "UNSTABLE": 49.0,
        "COST SENSITIVE": 59.0,
        "HOLDOUT LIMITED": 39.0,
        "HOLDOUT FAILED": 39.0,
        "HOLDOUT COST SENSITIVE": 49.0,
    }
    if optimizer_status in status_caps:
        score_cap = min(score_cap, status_caps[optimizer_status])
        penalties.append(
            f"Optimizer status is {optimizer_status}; robustness is capped at {int(score_cap)}/100."
        )

    if training_pnl <= 0:
        score_cap = min(score_cap, 49.0)
        penalties.append("Training P/L is not positive, so the setup is not stable across the anchor history.")

    if validation_pnl <= 0:
        score_cap = min(score_cap, 39.0)
    if holdout_pnl <= 0:
        score_cap = min(score_cap, 39.0)
    if sensitivity_applicable:
        if sensitivity_label == "FRAGILE" or not sensitivity_pass:
            score_cap = min(score_cap, 49.0)
            penalties.append(
                "Execution-cost sensitivity is fragile across the full multiplier curve."
            )
        elif sensitivity_label == "MIXED":
            score_cap = min(score_cap, 69.0)
            penalties.append(
                "Execution-cost sensitivity is mixed, so robustness cannot receive the highest rating."
            )
    elif uses_holdout_sensitivity:
        score_cap = min(score_cap, 49.0)
        penalties.append(
            "Untouched-holdout execution-cost robustness is unavailable, so execution "
            "robustness fails closed."
        )
    elif stress_pnl <= 0:
        score_cap = min(score_cap, 49.0)

    minimum_unseen_trades_for_high_confidence = 15
    if (
        validation_trades < minimum_unseen_trades_for_high_confidence
        or holdout_trades < minimum_unseen_trades_for_high_confidence
    ):
        score_cap = min(score_cap, 50.0)
        penalties.append(
            "High robustness requires at least "
            f"{minimum_unseen_trades_for_high_confidence} validation trades and "
            f"{minimum_unseen_trades_for_high_confidence} untouched holdout trades."
        )

    if validation_pnl > 0 and validation_pf < 1.05:
        score_cap = min(score_cap, 59.0)
        penalties.append("Validation profit factor is too close to breakeven for a high robustness rating.")
    if (
        not uses_holdout_sensitivity
        and not sensitivity_applicable
        and stress_pnl > 0
        and stress_pf < 1.05
    ):
        score_cap = min(score_cap, 59.0)
        penalties.append("Stress-test profit factor is too close to breakeven for a high robustness rating.")

    wf_profitable_pct = None
    wf_fold_count = 0
    wf_active_fold_count = 0
    wf_profitable_fold_count = 0
    wf_temporal_coverage_pct = None
    wf_profitable_scheduled_pct = None
    if walk_forward_report:
        wf_summary = walk_forward_report.get("summary") or {}
        wf_profitable_pct = safe_float(wf_summary.get("profitable_fold_pct"), 0.0) or 0.0
        wf_fold_count = int(safe_float(wf_summary.get("fold_count"), 0) or 0)
        wf_active_fold_count = int(safe_float(wf_summary.get("active_fold_count"), 0) or 0)
        wf_profitable_fold_count = int(safe_float(wf_summary.get("profitable_fold_count"), 0) or 0)
        if wf_fold_count > 0:
            wf_temporal_coverage_pct = wf_active_fold_count / wf_fold_count * 100.0
            wf_profitable_scheduled_pct = wf_profitable_fold_count / wf_fold_count * 100.0
            minimum_active_folds = min(2, wf_fold_count)
            if wf_active_fold_count < minimum_active_folds:
                score_cap = min(score_cap, 49.0)
                penalties.append(
                    "Too few scheduled walk-forward folds produced trades to establish temporal robustness."
                )
            elif wf_temporal_coverage_pct < 66.7:
                score_cap = min(score_cap, 59.0)
                penalties.append(
                    "The frozen setup traded in fewer than two-thirds of scheduled walk-forward folds."
                )
            if wf_profitable_scheduled_pct < 50.0:
                score_cap = min(score_cap, 49.0)
                penalties.append(
                    "Fewer than half of all scheduled walk-forward folds were profitable once inactive folds are counted."
                )
        if wf_profitable_pct < 50.0:
            score_cap = min(score_cap, 49.0)
            penalties.append("Fewer than half of active walk-forward folds were profitable.")
        elif wf_profitable_pct < 66.7:
            score_cap = min(score_cap, 64.0)
            penalties.append("Walk-forward profitability is mixed rather than broadly consistent.")

    final_score = round(min(raw_score, score_cap), 1)
    if final_score >= 80:
        label = "STRONG"
    elif final_score >= 65:
        label = "PROMISING"
    elif final_score >= 50:
        label = "MIXED"
    else:
        label = "WEAK"

    execution_robust_enough = (
        sensitivity_pass
        if uses_holdout_sensitivity or sensitivity_applicable
        else stress_pnl > 0
    )
    independently_positive = (
        optimizer_status == "VALIDATED"
        and training_pnl > 0
        and validation_pnl > 0
        and holdout_pnl > 0
        and execution_robust_enough
    )
    if walk_forward_report:
        independently_positive = independently_positive and (wf_profitable_pct or 0.0) >= 50.0
        if wf_fold_count > 0:
            independently_positive = (
                independently_positive
                and wf_active_fold_count >= min(2, wf_fold_count)
                and (wf_profitable_scheduled_pct or 0.0) >= 50.0
            )

    return {
        "score": final_score,
        "raw_score_before_caps": raw_score,
        "score_cap": round(score_cap, 1),
        "base_score": round(base_score, 1),
        "walk_forward_score": round(walk_score, 1) if walk_score is not None else None,
        "walk_forward_fold_count": wf_fold_count or None,
        "walk_forward_active_fold_count": wf_active_fold_count if wf_fold_count else None,
        "walk_forward_temporal_coverage_pct": (
            round(wf_temporal_coverage_pct, 1)
            if wf_temporal_coverage_pct is not None
            else None
        ),
        "walk_forward_profitable_scheduled_pct": (
            round(wf_profitable_scheduled_pct, 1)
            if wf_profitable_scheduled_pct is not None
            else None
        ),
        "execution_sensitivity_score": (
            round(sensitivity_score, 1) if sensitivity_score is not None else None
        ),
        "execution_sensitivity_label": sensitivity_label or None,
        "execution_sensitivity_scope": sensitivity_scope,
        "optimizer_status": optimizer_status or None,
        "minimum_unseen_trades_for_high_confidence": minimum_unseen_trades_for_high_confidence,
        "label": label,
        "independently_positive": bool(independently_positive),
        "reasons": list(dict.fromkeys([*reasons, *penalties])),
        "note": (
            "Robustness summarizes anchor-stock historical validation. When available, "
            "execution-cost robustness is taken from the frozen winner's untouched holdout "
            "curve rather than the development validation curve. Walk-forward temporal coverage "
            "also counts inactive scheduled folds so sparse activity cannot masquerade as broad "
            "consistency. Stability caps prevent negative/unstable, undersized, or near-breakeven "
            "evidence from receiving a misleading high rating; it is not a probability of profit."
        ),
    }


def _walk_forward_session_splits(
    sessions: list[str],
    *,
    minimum_history_sessions: int,
    test_sessions_per_fold: int,
    embargo_sessions: int,
) -> list[dict[str, Any]]:
    """Build expanding session folds with an explicit unused embargo gap."""
    minimum_history_sessions = max(1, int(minimum_history_sessions))
    test_sessions_per_fold = max(1, int(test_sessions_per_fold))
    embargo_sessions = max(0, int(embargo_sessions))
    splits: list[dict[str, Any]] = []
    test_start = minimum_history_sessions + embargo_sessions
    while test_start + test_sessions_per_fold <= len(sessions):
        history_end = test_start - embargo_sessions
        history_sessions = list(sessions[:history_end])
        embargo_block = list(sessions[history_end:test_start])
        external_test = list(
            sessions[test_start : test_start + test_sessions_per_fold]
        )
        if len(history_sessions) >= minimum_history_sessions and external_test:
            splits.append(
                {
                    "history_sessions": history_sessions,
                    "embargo_sessions": embargo_block,
                    "external_test_sessions": external_test,
                }
            )
        test_start += test_sessions_per_fold
    return splits


def _adaptive_walk_forward_strategies(
    strategies: list[dict[str, Any]],
    completed_experience: list[dict[str, Any]],
    *,
    max_values_per_rule: int = 4,
) -> list[dict[str, Any]]:
    """Seed the next fold only with evidence available before that fold starts.

    A profitable completed unseen fold may contribute its exact winning rules. If
    that same unseen fold also confirmed multiple nearby profitable configurations,
    the externally profitable rule values are carried forward as a neighborhood
    rather than collapsing learning to one exact parameter point.
    """
    max_values_per_rule = max(1, min(12, int(max_values_per_rule)))
    adapted: list[dict[str, Any]] = []
    for strategy in strategies:
        if not isinstance(strategy, dict):
            continue
        clone = dict(strategy)
        raw_options = strategy.get("candidate_rule_options")
        candidate_options: dict[str, list[Any]] = {}
        if isinstance(raw_options, dict):
            for field_name, values in raw_options.items():
                if isinstance(values, list):
                    candidate_options[str(field_name)] = list(values)

        strategy_id = str(strategy.get("id") or "")
        relevant = [
            item
            for item in completed_experience
            if str(item.get("source_strategy_id") or "") == strategy_id
        ]
        profitable = [
            item
            for item in relevant
            if int(safe_float(item.get("trade_count"), 0) or 0) > 0
            and (safe_float(item.get("net_pnl"), 0.0) or 0.0) > 0
        ]
        seeded_values = 0
        neighborhood_seeded_values = 0
        neighborhood_fold_count = 0

        # Prefer recent successful unseen experience while keeping a small memory
        # of older winners so adaptation does not collapse into one exact setting.
        for experience in profitable[-max_values_per_rule:]:
            seed_values: dict[str, list[Any]] = {}
            learned_rules = normalize_machine_rules(
                experience.get("optimized_rules") or {}
            )
            for field_name, learned_value in learned_rules.items():
                if learned_value is not None:
                    seed_values.setdefault(field_name, []).append(learned_value)

            neighborhood = experience.get("profitable_neighborhood") or {}
            if bool(neighborhood.get("broad_profitable")):
                neighborhood_fold_count += 1
                raw_rule_values = neighborhood.get("rule_values") or {}
                if isinstance(raw_rule_values, dict):
                    for field_name, values in raw_rule_values.items():
                        if not isinstance(values, list):
                            continue
                        for value in values:
                            parsed = normalize_machine_rules(
                                {str(field_name): value}
                            ).get(str(field_name))
                            if parsed is not None:
                                seed_values.setdefault(str(field_name), []).append(parsed)

            for field_name, learned_values in seed_values.items():
                values = candidate_options.setdefault(field_name, [])
                for learned_value in learned_values:
                    if learned_value in values:
                        continue
                    values.append(learned_value)
                    seeded_values += 1
                    if bool(neighborhood.get("broad_profitable")):
                        neighborhood_values = (
                            (neighborhood.get("rule_values") or {}).get(field_name) or []
                        )
                        if learned_value in neighborhood_values:
                            neighborhood_seeded_values += 1
                if len(values) > max_values_per_rule:
                    del values[:-max_values_per_rule]

        if candidate_options:
            clone["candidate_rule_options"] = candidate_options
        clone["_adaptive_walk_forward_completed_fold_count"] = len(
            completed_experience
        )
        clone["_adaptive_walk_forward_profitable_fold_count"] = len(profitable)
        clone["_adaptive_walk_forward_seeded_rule_values"] = seeded_values
        clone["_adaptive_walk_forward_neighborhood_fold_count"] = neighborhood_fold_count
        clone["_adaptive_walk_forward_neighborhood_seeded_rule_values"] = (
            neighborhood_seeded_values
        )
        adapted.append(clone)
    return adapted


def _validation_neighbor_candidates(
    optimization_report: dict[str, Any],
    source_strategy_id: str,
    winner_rules: dict[str, Any],
    winner_settings: BacktestSettings,
    *,
    max_neighbors: int = 4,
) -> list[dict[str, Any]]:
    """Return nearby validation-profitable finalists without using external-fold data."""
    max_neighbors = max(0, min(12, int(max_neighbors)))
    if max_neighbors <= 0:
        return []

    ranking = next(
        (
            item
            for item in optimization_report.get("rankings") or []
            if isinstance(item, dict)
            and str(item.get("source_strategy_id") or "") == str(source_strategy_id or "")
        ),
        None,
    )
    if not ranking:
        return []

    optimizer_settings = optimization_report.get("optimization_settings") or {}
    minimum_trades = max(
        1,
        int(safe_float(optimizer_settings.get("minimum_validation_trades"), 2) or 2),
    )
    maximum_drawdown = max(
        0.5,
        safe_float(optimizer_settings.get("maximum_drawdown_pct"), 15.0) or 15.0,
    )
    normalized_winner_rules = normalize_machine_rules(winner_rules or {})
    winner_settings_payload = asdict(winner_settings)
    important_setting_fields = (
        "risk_per_trade_pct",
        "max_position_pct",
        "default_stop_pct",
        "default_reward_risk",
        "max_concurrent_positions",
        "allow_extended_hours",
        "extended_hours_position_scale",
    )

    candidates: list[dict[str, Any]] = []
    for raw in ranking.get("validation_neighborhood") or []:
        if not isinstance(raw, dict):
            continue
        metrics = raw.get("metrics") or {}
        trade_count = int(safe_float(metrics.get("trade_count"), 0) or 0)
        net_pnl = safe_float(metrics.get("net_pnl"), 0.0) or 0.0
        drawdown = safe_float(metrics.get("max_drawdown_pct"), 0.0) or 0.0
        if (
            trade_count < minimum_trades
            or net_pnl <= 0
            or drawdown > maximum_drawdown
        ):
            continue

        rules = normalize_machine_rules(raw.get("rules") or {})
        settings_payload = dict(raw.get("settings") or {})
        if rules == normalized_winner_rules and settings_payload == winner_settings_payload:
            continue

        rule_fields = set(normalized_winner_rules) | set(rules)
        rule_distance = sum(
            1
            for field_name in rule_fields
            if normalized_winner_rules.get(field_name) != rules.get(field_name)
        )
        setting_distance = sum(
            1
            for field_name in important_setting_fields
            if winner_settings_payload.get(field_name) != settings_payload.get(field_name)
        )
        candidates.append(
            {
                "rules": rules,
                "settings": settings_payload,
                "validation_metrics": dict(metrics),
                "validation_score": safe_float(raw.get("validation_score"), 0.0) or 0.0,
                "distance": rule_distance + setting_distance,
            }
        )

    candidates.sort(
        key=lambda item: (
            int(item.get("distance") or 0),
            -(safe_float((item.get("validation_metrics") or {}).get("profit_factor"), 0.0) or 0.0),
            -(safe_float((item.get("validation_metrics") or {}).get("net_pnl"), 0.0) or 0.0),
        )
    )
    return candidates[:max_neighbors]


def _profitable_external_neighborhood(
    winner_rules: dict[str, Any],
    winner_settings: BacktestSettings,
    winner_metrics: dict[str, Any],
    neighbor_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize parameter values that were profitable in the same unseen fold."""
    winner_profitable = (
        int(safe_float(winner_metrics.get("trade_count"), 0) or 0) > 0
        and (safe_float(winner_metrics.get("net_pnl"), 0.0) or 0.0) > 0
    )
    profitable_neighbors = [
        item
        for item in neighbor_results
        if int(safe_float((item.get("external_metrics") or {}).get("trade_count"), 0) or 0) > 0
        and (safe_float((item.get("external_metrics") or {}).get("net_pnl"), 0.0) or 0.0) > 0
    ]
    tested_neighbor_count = len(neighbor_results)
    profitable_neighbor_pct = (
        len(profitable_neighbors) / tested_neighbor_count * 100.0
        if tested_neighbor_count
        else 0.0
    )

    profitable_configurations: list[dict[str, Any]] = []
    if winner_profitable:
        profitable_configurations.append(
            {
                "rules": normalize_machine_rules(winner_rules or {}),
                "settings": asdict(winner_settings),
                "external_metrics": dict(winner_metrics),
                "role": "selected_winner",
            }
        )
    profitable_configurations.extend(
        {
            "rules": normalize_machine_rules(item.get("rules") or {}),
            "settings": dict(item.get("settings") or {}),
            "external_metrics": dict(item.get("external_metrics") or {}),
            "role": "nearby_validation_finalist",
        }
        for item in profitable_neighbors
    )

    def distinct_values(
        field_source: str,
        field_names: set[str] | tuple[str, ...] | None = None,
    ) -> dict[str, list[Any]]:
        names = set(field_names or ())
        if not names:
            for item in profitable_configurations:
                payload = item.get(field_source) or {}
                if isinstance(payload, dict):
                    names.update(str(key) for key in payload)
        output: dict[str, list[Any]] = {}
        for field_name in sorted(names):
            values: list[Any] = []
            for item in profitable_configurations:
                payload = item.get(field_source) or {}
                if not isinstance(payload, dict):
                    continue
                value = payload.get(field_name)
                if value is None or value in values:
                    continue
                values.append(value)
            if len(values) >= 2:
                output[field_name] = values
        return output

    rule_values = distinct_values("rules")
    setting_values = distinct_values(
        "settings",
        (
            "risk_per_trade_pct",
            "max_position_pct",
            "default_stop_pct",
            "default_reward_risk",
            "max_concurrent_positions",
            "extended_hours_position_scale",
        ),
    )

    def numeric_ranges(values_by_field: dict[str, list[Any]]) -> dict[str, dict[str, float]]:
        output: dict[str, dict[str, float]] = {}
        for field_name, values in values_by_field.items():
            numeric = [
                safe_float(value)
                for value in values
                if not isinstance(value, bool)
            ]
            numeric = [value for value in numeric if value is not None]
            if len(numeric) >= 2:
                output[field_name] = {
                    "min": round(min(numeric), 8),
                    "max": round(max(numeric), 8),
                }
        return output

    broad_profitable = bool(
        winner_profitable
        and tested_neighbor_count >= 2
        and len(profitable_neighbors) >= 2
        and profitable_neighbor_pct >= 50.0
        and (rule_values or setting_values)
    )
    return {
        "winner_profitable": bool(winner_profitable),
        "tested_neighbor_count": tested_neighbor_count,
        "profitable_neighbor_count": len(profitable_neighbors),
        "profitable_neighbor_pct": round(profitable_neighbor_pct, 1),
        "profitable_configuration_count": len(profitable_configurations),
        "broad_profitable": broad_profitable,
        "rule_values": rule_values,
        "rule_ranges": numeric_ranges(rule_values),
        "setting_values": setting_values,
        "setting_ranges": numeric_ranges(setting_values),
        "neighbors": neighbor_results,
        "note": (
            "Nearby configurations are selected using only the fold's earlier internal "
            "validation data, frozen, then tested on the same unseen external block as the "
            "winner. A broad neighborhood requires at least two nearby configurations to "
            "remain profitable out of sample."
        ),
    }


def _walk_forward_fold_summary(
    folds: list[dict[str, Any]],
    all_external_trades: list[dict[str, Any]],
    settings: BacktestSettings,
    optimizer: OptimizationSettings,
) -> dict[str, Any]:
    active_folds = [
        fold
        for fold in folds
        if int(safe_float((fold.get("external_metrics") or {}).get("trade_count"), 0) or 0) > 0
    ]
    profitable_folds = [
        fold
        for fold in active_folds
        if (safe_float((fold.get("external_metrics") or {}).get("net_pnl"), 0.0) or 0.0) > 0
    ]
    returns = [
        safe_float((fold.get("external_metrics") or {}).get("return_pct"), 0.0) or 0.0
        for fold in active_folds
    ]
    combined = summarize_trades(all_external_trades, settings.starting_cash)
    combined_pf = _profit_factor(combined)
    max_drawdown = max(
        [
            safe_float((fold.get("external_metrics") or {}).get("max_drawdown_pct"), 0.0) or 0.0
            for fold in active_folds
        ]
        or [0.0]
    )
    profitable_pct = (
        len(profitable_folds) / len(active_folds) * 100.0
        if active_folds
        else 0.0
    )
    coverage = min(
        1.0,
        int(safe_float(combined.get("trade_count"), 0) or 0)
        / max(6.0, len(folds) * 2.0),
    )
    profitability_score = profitable_pct / 100.0
    pf_score = min(1.0, combined_pf / 1.5)
    drawdown_score = max(
        0.0,
        min(
            1.0,
            1.0
            - max_drawdown / max(0.5, optimizer.maximum_drawdown_pct),
        ),
    )
    consistency_score = 0.0
    if returns and mean(returns) > 0:
        dispersion = pstdev(returns) if len(returns) > 1 else 0.0
        consistency_score = max(
            0.0,
            min(
                1.0,
                1.0 - dispersion / max(abs(mean(returns)), 1.0),
            ),
        )

    score = round(
        40.0 * profitability_score
        + 20.0 * coverage
        + 20.0 * pf_score
        + 10.0 * drawdown_score
        + 10.0 * consistency_score,
        1,
    )
    if score >= 80:
        label = "STRONG"
    elif score >= 65:
        label = "PROMISING"
    elif score >= 50:
        label = "MIXED"
    else:
        label = "WEAK"

    strategy_counts = Counter(
        str(fold.get("selected_strategy_name") or "Unnamed")
        for fold in folds
    )
    return {
        "score": score,
        "label": label,
        "fold_count": len(folds),
        "active_fold_count": len(active_folds),
        "profitable_fold_count": len(profitable_folds),
        "profitable_fold_pct": round(profitable_pct, 1),
        "external_trade_count": int(safe_float(combined.get("trade_count"), 0) or 0),
        "external_net_pnl": safe_float(combined.get("net_pnl"), 0.0) or 0.0,
        "external_return_pct": safe_float(combined.get("return_pct"), 0.0) or 0.0,
        "external_profit_factor": combined.get("profit_factor"),
        "max_fold_drawdown_pct": round(max_drawdown, 2),
        "median_fold_return_pct": round(median(returns), 3) if returns else 0.0,
        "average_fold_return_pct": round(mean(returns), 3) if returns else 0.0,
        "selected_strategy_counts": dict(strategy_counts),
    }


def walk_forward_validate(
    rows: list[dict[str, Any]],
    strategies: list[dict[str, Any]],
    symbol: str,
    backtest_settings: BacktestSettings | None = None,
    optimization_settings: OptimizationSettings | None = None,
    *,
    minimum_history_sessions: int = 8,
    test_sessions_per_fold: int = 2,
    embargo_sessions: int = 1,
    max_folds: int = 3,
    adaptive_learning: bool = True,
    compare_static_baseline: bool = False,
    max_neighborhood_candidates: int = 4,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Nested expanding-window walk-forward research with causal adaptation.

    Each fold optimizes only on sessions before the fold's external test block.
    A configurable whole-session embargo is left completely unused immediately
    before that external block. The optimizer keeps its own
    training/validation/holdout separation inside the earlier historical window.

    The selected winner and a small set of nearby validation-profitable finalists
    are frozen before the external test begins. Only after that unseen block ends
    may its results influence later folds. This lets the learner carry forward a
    genuinely profitable parameter neighborhood instead of memorizing one exact
    setting. When compare_static_baseline is enabled, later folds also run a
    counterfactual non-adaptive optimizer on the exact same history/test split.
    """
    settings = backtest_settings or BacktestSettings()
    settings.validate()
    optimizer = optimization_settings or OptimizationSettings(selection_mode="validated")
    optimizer = replace(optimizer, selection_mode="validated")
    optimizer.validate()

    frame = bars_to_frame(rows, include_extended_hours=True)
    sessions = list(dict.fromkeys(frame.get("session", pd.Series(dtype=str)).tolist()))
    minimum_history_sessions = max(5, int(minimum_history_sessions))
    test_sessions_per_fold = max(1, int(test_sessions_per_fold))
    embargo_sessions = max(0, min(5, int(embargo_sessions)))
    max_folds = max(1, min(8, int(max_folds)))
    max_neighborhood_candidates = max(
        0,
        min(12, int(max_neighborhood_candidates)),
    )

    required_sessions = (
        minimum_history_sessions + embargo_sessions + test_sessions_per_fold
    )
    if len(sessions) < required_sessions:
        raise AppError(
            "Walk-forward testing needs more trading sessions. Increase the historical window "
            f"to provide at least {required_sessions} sessions, including the "
            f"{embargo_sessions}-session embargo."
        )

    possible = _walk_forward_session_splits(
        sessions,
        minimum_history_sessions=minimum_history_sessions,
        test_sessions_per_fold=test_sessions_per_fold,
        embargo_sessions=embargo_sessions,
    )
    folds_to_run = possible[-max_folds:]
    folds: list[dict[str, Any]] = []
    all_external_trades: list[dict[str, Any]] = []
    adaptive_experience: list[dict[str, Any]] = []
    static_folds: list[dict[str, Any]] = []
    static_external_trades: list[dict[str, Any]] = []
    total_steps = len(folds_to_run)

    for fold_number, split in enumerate(folds_to_run, start=1):
        history_sessions = list(split.get("history_sessions") or [])
        embargo_block = list(split.get("embargo_sessions") or [])
        external_test_sessions = list(split.get("external_test_sessions") or [])
        if progress:
            progress(
                fold_number - 1,
                total_steps,
                f"Walk-forward fold {fold_number}/{total_steps}: optimizing prior sessions…",
            )

        history_frame = frame[
            frame["session"].isin(history_sessions)
        ].copy().reset_index(drop=True)
        external_frame = frame[
            frame["session"].isin(external_test_sessions)
        ].copy().reset_index(drop=True)
        history_rows = _frame_to_rows(history_frame)
        external_rows = _frame_to_rows(external_frame)

        experience_count_before_fold = len(adaptive_experience)
        learning_cutoff = (
            adaptive_experience[-1].get("external_test_end")
            if adaptive_experience
            else None
        )
        fold_strategies = (
            _adaptive_walk_forward_strategies(
                strategies,
                adaptive_experience,
            )
            if adaptive_learning
            else [dict(item) for item in strategies if isinstance(item, dict)]
        )
        adaptive_seeded_rule_values = sum(
            int(item.get("_adaptive_walk_forward_seeded_rule_values") or 0)
            for item in fold_strategies
        )
        adaptive_neighborhood_seeded_rule_values = sum(
            int(
                item.get(
                    "_adaptive_walk_forward_neighborhood_seeded_rule_values"
                )
                or 0
            )
            for item in fold_strategies
        )

        fold_report = optimize_stock_strategies(
            history_rows,
            fold_strategies,
            symbol,
            settings,
            optimizer,
            finalize_holdout=True,
        )
        winner = fold_report.get("winner") or {}
        source_id = str(winner.get("source_strategy_id") or "")
        source = next(
            (
                item
                for item in strategies
                if str(item.get("id") or "") == source_id
            ),
            None,
        )
        if source is None:
            raise AppError(
                "A walk-forward fold selected a strategy that is no longer available."
            )

        selected_settings = BacktestSettings(
            **(
                winner.get("optimized_backtest_settings")
                or fold_report.get("backtest_settings")
                or {}
            )
        )
        selected_rules = normalize_machine_rules(
            winner.get("optimized_rules") or source.get("machine_rules")
        )
        selected_strategy = {
            **source,
            "machine_rules": selected_rules,
        }

        validation_neighbors = _validation_neighbor_candidates(
            fold_report,
            source_id,
            selected_rules,
            selected_settings,
            max_neighbors=max_neighborhood_candidates,
        )

        external_result = run_backtest(
            external_rows,
            selected_strategy,
            symbol,
            selected_settings,
        )
        external_metrics = external_result.get("metrics") or {}
        external_trades = list(external_result.get("trades") or [])
        all_external_trades.extend(external_trades)

        neighbor_results: list[dict[str, Any]] = []
        for neighbor in validation_neighbors:
            try:
                neighbor_settings = BacktestSettings(
                    **(neighbor.get("settings") or {})
                )
                neighbor_strategy = {
                    **source,
                    "machine_rules": normalize_machine_rules(
                        neighbor.get("rules") or {}
                    ),
                }
                neighbor_result = run_backtest(
                    external_rows,
                    neighbor_strategy,
                    symbol,
                    neighbor_settings,
                )
                neighbor_results.append(
                    {
                        "rules": normalize_machine_rules(
                            neighbor.get("rules") or {}
                        ),
                        "settings": asdict(neighbor_settings),
                        "distance": int(neighbor.get("distance") or 0),
                        "internal_validation_metrics": dict(
                            neighbor.get("validation_metrics") or {}
                        ),
                        "external_metrics": dict(
                            neighbor_result.get("metrics") or {}
                        ),
                    }
                )
            except (AppError, TypeError, ValueError):
                continue

        profitable_neighborhood = _profitable_external_neighborhood(
            selected_rules,
            selected_settings,
            external_metrics,
            neighbor_results,
        )

        static_fold_record: dict[str, Any] | None = None
        if compare_static_baseline:
            static_reused_adaptive = (
                not adaptive_learning or experience_count_before_fold == 0
            )
            if static_reused_adaptive:
                static_winner = winner
                static_source_id = source_id
                static_source = source
                static_settings = selected_settings
                static_rules = selected_rules
                static_metrics = dict(external_metrics)
                static_trades = list(external_trades)
            else:
                static_report = optimize_stock_strategies(
                    history_rows,
                    [
                        dict(item)
                        for item in strategies
                        if isinstance(item, dict)
                    ],
                    symbol,
                    settings,
                    optimizer,
                    finalize_holdout=True,
                )
                static_winner = static_report.get("winner") or {}
                static_source_id = str(
                    static_winner.get("source_strategy_id") or ""
                )
                static_source = next(
                    (
                        item
                        for item in strategies
                        if str(item.get("id") or "") == static_source_id
                    ),
                    None,
                )
                if static_source is None:
                    raise AppError(
                        "The static walk-forward baseline selected a strategy "
                        "that is no longer available."
                    )
                static_settings = BacktestSettings(
                    **(
                        static_winner.get("optimized_backtest_settings")
                        or static_report.get("backtest_settings")
                        or {}
                    )
                )
                static_rules = normalize_machine_rules(
                    static_winner.get("optimized_rules")
                    or static_source.get("machine_rules")
                )
                static_result = run_backtest(
                    external_rows,
                    {
                        **static_source,
                        "machine_rules": static_rules,
                    },
                    symbol,
                    static_settings,
                )
                static_metrics = dict(static_result.get("metrics") or {})
                static_trades = list(static_result.get("trades") or [])

            static_external_trades.extend(static_trades)
            static_fold_record = {
                "fold": fold_number,
                "history_start": history_sessions[0],
                "history_end": history_sessions[-1],
                "external_test_start": external_test_sessions[0],
                "external_test_end": external_test_sessions[-1],
                "selected_strategy_id": static_source_id,
                "selected_strategy_name": (
                    static_winner.get("strategy_name")
                    or static_source.get("name")
                ),
                "optimizer_status": static_winner.get("status"),
                "external_metrics": static_metrics,
                "optimized_rules": static_rules,
                "optimized_backtest_settings": asdict(static_settings),
                "reused_adaptive_first_fold": bool(static_reused_adaptive),
            }
            static_folds.append(static_fold_record)

        # Only now—after the winner, nearby candidates, and optional static
        # counterfactual were frozen and scored—may this external fold become
        # learning evidence for the next unseen fold.
        adaptive_feedback = {
            "fold": fold_number,
            "source_strategy_id": source_id,
            "selected_strategy_name": (
                winner.get("strategy_name") or source.get("name")
            ),
            "external_test_start": external_test_sessions[0],
            "external_test_end": external_test_sessions[-1],
            "trade_count": int(
                safe_float(external_metrics.get("trade_count"), 0) or 0
            ),
            "net_pnl": (
                safe_float(external_metrics.get("net_pnl"), 0.0) or 0.0
            ),
            "return_pct": (
                safe_float(external_metrics.get("return_pct"), 0.0) or 0.0
            ),
            "profitable": (
                int(
                    safe_float(
                        external_metrics.get("trade_count"),
                        0,
                    )
                    or 0
                )
                > 0
                and (
                    safe_float(
                        external_metrics.get("net_pnl"),
                        0.0,
                    )
                    or 0.0
                )
                > 0
            ),
            "optimized_rules": selected_rules,
            "profitable_neighborhood": profitable_neighborhood,
        }
        adaptive_experience.append(adaptive_feedback)

        folds.append(
            {
                "fold": fold_number,
                "history_start": history_sessions[0],
                "history_end": history_sessions[-1],
                "external_test_start": external_test_sessions[0],
                "external_test_end": external_test_sessions[-1],
                "history_session_count": len(history_sessions),
                "embargo_session_count": len(embargo_block),
                "embargo_start": embargo_block[0] if embargo_block else None,
                "embargo_end": embargo_block[-1] if embargo_block else None,
                "test_session_count": len(external_test_sessions),
                "selected_strategy_id": source_id,
                "selected_strategy_name": (
                    winner.get("strategy_name") or source.get("name")
                ),
                "optimizer_status": winner.get("status"),
                "internal_holdout_metrics": winner.get("holdout_metrics") or {},
                "external_metrics": external_metrics,
                "optimized_rules": selected_rules,
                "optimized_backtest_settings": asdict(selected_settings),
                "adaptive_learning_enabled": bool(adaptive_learning),
                "adaptive_experience_count_before_fold": (
                    experience_count_before_fold
                ),
                "adaptive_learning_cutoff": learning_cutoff,
                "adaptive_seeded_rule_values": adaptive_seeded_rule_values,
                "adaptive_neighborhood_seeded_rule_values": (
                    adaptive_neighborhood_seeded_rule_values
                ),
                "validation_neighbor_candidate_count": len(
                    validation_neighbors
                ),
                "profitable_neighborhood": profitable_neighborhood,
                "static_baseline_external_metrics": (
                    dict(static_fold_record.get("external_metrics") or {})
                    if static_fold_record
                    else None
                ),
                "adaptive_feedback": adaptive_feedback,
            }
        )

        if progress:
            progress(
                fold_number,
                total_steps,
                f"Walk-forward fold {fold_number}/{total_steps}: external test complete.",
            )

    summary = _walk_forward_fold_summary(
        folds,
        all_external_trades,
        settings,
        optimizer,
    )
    broad_neighborhood_fold_count = sum(
        1
        for fold in folds
        if bool(
            (fold.get("profitable_neighborhood") or {}).get(
                "broad_profitable"
            )
        )
    )
    summary.update(
        {
            "embargo_sessions": embargo_sessions,
            "adaptive_learning_enabled": bool(adaptive_learning),
            "adaptive_experience_count": len(adaptive_experience),
            "adaptive_profitable_experience_count": sum(
                1
                for item in adaptive_experience
                if item.get("profitable")
            ),
            "broad_profitable_neighborhood_fold_count": (
                broad_neighborhood_fold_count
            ),
        }
    )

    static_summary: dict[str, Any] = {}
    comparison: dict[str, Any] = {
        "enabled": bool(compare_static_baseline),
        "verdict": "NOT RUN",
        "adaptive_added_value": None,
    }
    if compare_static_baseline:
        static_summary = _walk_forward_fold_summary(
            static_folds,
            static_external_trades,
            settings,
            optimizer,
        )
        static_summary["embargo_sessions"] = embargo_sessions
        score_delta = (
            safe_float(summary.get("score"), 0.0) or 0.0
        ) - (
            safe_float(static_summary.get("score"), 0.0) or 0.0
        )
        pnl_delta = (
            safe_float(summary.get("external_net_pnl"), 0.0) or 0.0
        ) - (
            safe_float(
                static_summary.get("external_net_pnl"),
                0.0,
            )
            or 0.0
        )
        profitable_fold_delta = (
            safe_float(summary.get("profitable_fold_pct"), 0.0) or 0.0
        ) - (
            safe_float(
                static_summary.get("profitable_fold_pct"),
                0.0,
            )
            or 0.0
        )
        if score_delta > 0 and pnl_delta > 0:
            comparison_verdict = "ADAPTIVE BETTER"
        elif score_delta < 0 and pnl_delta < 0:
            comparison_verdict = "STATIC BETTER"
        elif abs(score_delta) < 0.05 and abs(pnl_delta) < 0.01:
            comparison_verdict = "TIE"
        else:
            comparison_verdict = "MIXED"
        comparison = {
            "enabled": True,
            "verdict": comparison_verdict,
            "adaptive_added_value": (
                comparison_verdict == "ADAPTIVE BETTER"
            ),
            "adaptive_score": summary.get("score"),
            "static_score": static_summary.get("score"),
            "score_delta": round(score_delta, 2),
            "adaptive_external_net_pnl": summary.get(
                "external_net_pnl"
            ),
            "static_external_net_pnl": static_summary.get(
                "external_net_pnl"
            ),
            "external_net_pnl_delta": round(pnl_delta, 2),
            "adaptive_profitable_fold_pct": summary.get(
                "profitable_fold_pct"
            ),
            "static_profitable_fold_pct": static_summary.get(
                "profitable_fold_pct"
            ),
            "profitable_fold_pct_delta": round(
                profitable_fold_delta,
                1,
            ),
            "note": (
                "Both modes use the same expanding history, embargo, and unseen "
                "test blocks. The static baseline never receives completed-fold "
                "rule seeds; the first fold is reused because neither mode has "
                "prior unseen experience yet."
            ),
        }

    warnings: list[str] = []
    no_trade_folds = int(summary.get("fold_count") or 0) - int(
        summary.get("active_fold_count") or 0
    )
    if no_trade_folds:
        warnings.append(
            f"{no_trade_folds} walk-forward fold(s) produced no trades "
            "in the external test block."
        )
    strategy_counts = dict(summary.get("selected_strategy_counts") or {})
    if len(strategy_counts) > 1:
        warnings.append(
            "Different strategy families won different folds. That can be useful "
            "regime evidence, but it also means there is no single consistently "
            "dominant setup."
        )
    if (
        safe_float(summary.get("profitable_fold_pct"), 0.0) or 0.0
    ) < 50.0:
        warnings.append(
            "Fewer than half of the active walk-forward folds were profitable."
        )
    if adaptive_learning and broad_neighborhood_fold_count <= 0:
        warnings.append(
            "No unseen fold established a broad profitable parameter neighborhood; "
            "adaptation is still relying mainly on exact completed-fold winners."
        )
    if (
        compare_static_baseline
        and comparison.get("verdict") == "STATIC BETTER"
    ):
        warnings.append(
            "The non-adaptive static baseline outperformed the adaptive learner "
            "across these unseen folds. Do not promote the adaptive layer from "
            "this sample."
        )

    return {
        "symbol": symbol,
        "folds": folds,
        "summary": summary,
        "adaptive_learning": {
            "enabled": bool(adaptive_learning),
            "mode": "causal_neighborhood_replay",
            "experience_count": len(adaptive_experience),
            "profitable_experience_count": sum(
                1
                for item in adaptive_experience
                if item.get("profitable")
            ),
            "broad_profitable_neighborhood_fold_count": (
                broad_neighborhood_fold_count
            ),
            "experience": adaptive_experience,
            "note": (
                "Each unseen fold is frozen before scoring. Only after that fold "
                "ends may its exact winner and externally confirmed profitable "
                "neighborhood seed the next optimizer search. Losing folds still "
                "enter the expanding historical window; future folds are never "
                "visible early."
            ),
        },
        "static_baseline": {
            "enabled": bool(compare_static_baseline),
            "folds": static_folds,
            "summary": static_summary,
            "note": (
                "Static comparison uses the same fold boundaries without carrying "
                "forward completed-fold rule seeds."
                if compare_static_baseline
                else "Static comparison was not requested for this run."
            ),
        },
        "comparison": comparison,
        "warnings": warnings,
        "note": (
            "Each external fold is unseen by that fold's optimizer, with "
            f"{embargo_sessions} whole session(s) deliberately omitted immediately "
            "before the test block. Nearby parameter candidates are also frozen "
            "before external scoring. Results remain historical simulations and "
            "do not establish future profitability."
        ),
    }

