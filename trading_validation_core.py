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
    """Seed the next fold with rules that already survived prior unseen folds.

    This is causal experience replay: only *completed* external-fold outcomes are
    accepted. Profitable out-of-sample rule values are added as search options for
    the next fold; losing folds are still learned through the expanding historical
    window, but their exact rule settings are not promoted as positive seeds.
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
        # Prefer recent successful unseen experience while keeping a small memory
        # of older winners so adaptation does not collapse into one exact setting.
        for experience in profitable[-max_values_per_rule:]:
            learned_rules = normalize_machine_rules(
                experience.get("optimized_rules") or {}
            )
            for field_name, learned_value in learned_rules.items():
                if learned_value is None:
                    continue
                values = candidate_options.setdefault(field_name, [])
                if learned_value not in values:
                    values.append(learned_value)
                    seeded_values += 1
                if len(values) > max_values_per_rule:
                    del values[:-max_values_per_rule]

        if candidate_options:
            clone["candidate_rule_options"] = candidate_options
        clone["_adaptive_walk_forward_completed_fold_count"] = len(
            completed_experience
        )
        clone["_adaptive_walk_forward_profitable_fold_count"] = len(profitable)
        clone["_adaptive_walk_forward_seeded_rule_values"] = seeded_values
        adapted.append(clone)
    return adapted


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
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Nested expanding-window walk-forward research.

    Each fold optimizes only on sessions before the fold's external test block.
    A configurable whole-session embargo is left completely unused immediately
    before that external block. The optimizer still keeps its own
    training/validation/holdout separation inside the earlier historical window.
    The selected rules are then frozen and run on the next unseen sessions.

    When adaptive_learning is enabled, a completed unseen fold becomes causal
    experience for later folds only after its result is known. Profitable rule
    values seed the next optimizer search, while all completed folds—including
    losses—enter the expanding historical window. No future fold is exposed early.
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

        history_frame = frame[frame["session"].isin(history_sessions)].copy().reset_index(drop=True)
        external_frame = frame[frame["session"].isin(external_test_sessions)].copy().reset_index(drop=True)
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
        source = next((item for item in strategies if str(item.get("id")) == source_id), None)
        if source is None:
            raise AppError("A walk-forward fold selected a strategy that is no longer available.")

        selected_settings = BacktestSettings(
            **(winner.get("optimized_backtest_settings") or fold_report.get("backtest_settings") or {})
        )
        selected_strategy = {
            **source,
            "machine_rules": normalize_machine_rules(winner.get("optimized_rules") or source.get("machine_rules")),
        }
        external_result = run_backtest(
            external_rows,
            selected_strategy,
            symbol,
            selected_settings,
        )
        external_metrics = external_result.get("metrics") or {}
        external_trades = list(external_result.get("trades") or [])
        all_external_trades.extend(external_trades)

        # Only now—after the fold was frozen and scored—may its outcome become
        # learning evidence for the next unseen fold.
        adaptive_feedback = {
            "fold": fold_number,
            "source_strategy_id": source_id,
            "selected_strategy_name": winner.get("strategy_name") or source.get("name"),
            "external_test_start": external_test_sessions[0],
            "external_test_end": external_test_sessions[-1],
            "trade_count": int(safe_float(external_metrics.get("trade_count"), 0) or 0),
            "net_pnl": safe_float(external_metrics.get("net_pnl"), 0.0) or 0.0,
            "return_pct": safe_float(external_metrics.get("return_pct"), 0.0) or 0.0,
            "profitable": (
                int(safe_float(external_metrics.get("trade_count"), 0) or 0) > 0
                and (safe_float(external_metrics.get("net_pnl"), 0.0) or 0.0) > 0
            ),
            "optimized_rules": normalize_machine_rules(
                winner.get("optimized_rules") or {}
            ),
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
                "selected_strategy_name": winner.get("strategy_name") or source.get("name"),
                "optimizer_status": winner.get("status"),
                "internal_holdout_metrics": winner.get("holdout_metrics") or {},
                "external_metrics": external_metrics,
                "optimized_rules": winner.get("optimized_rules") or {},
                "optimized_backtest_settings": asdict(selected_settings),
                "adaptive_learning_enabled": bool(adaptive_learning),
                "adaptive_experience_count_before_fold": experience_count_before_fold,
                "adaptive_learning_cutoff": learning_cutoff,
                "adaptive_seeded_rule_values": adaptive_seeded_rule_values,
                "adaptive_feedback": adaptive_feedback,
            }
        )

        if progress:
            progress(
                fold_number,
                total_steps,
                f"Walk-forward fold {fold_number}/{total_steps}: external test complete.",
            )

    active_folds = [
        fold for fold in folds
        if int(safe_float((fold.get("external_metrics") or {}).get("trade_count"), 0) or 0) > 0
    ]
    profitable_folds = [
        fold for fold in active_folds
        if (safe_float((fold.get("external_metrics") or {}).get("net_pnl"), 0.0) or 0.0) > 0
    ]
    returns = [
        safe_float((fold.get("external_metrics") or {}).get("return_pct"), 0.0) or 0.0
        for fold in active_folds
    ]
    combined = summarize_trades(all_external_trades, settings.starting_cash)
    combined_pf = _profit_factor(combined)
    max_drawdown = max(
        [safe_float((fold.get("external_metrics") or {}).get("max_drawdown_pct"), 0.0) or 0.0 for fold in active_folds]
        or [0.0]
    )
    profitable_pct = len(profitable_folds) / len(active_folds) * 100.0 if active_folds else 0.0
    coverage = min(
        1.0,
        int(safe_float(combined.get("trade_count"), 0) or 0) / max(6.0, len(folds) * 2.0),
    )
    profitability_score = profitable_pct / 100.0
    pf_score = min(1.0, combined_pf / 1.5)
    drawdown_score = max(
        0.0,
        min(1.0, 1.0 - max_drawdown / max(0.5, optimizer.maximum_drawdown_pct)),
    )
    consistency_score = 0.0
    if returns and mean(returns) > 0:
        dispersion = pstdev(returns) if len(returns) > 1 else 0.0
        consistency_score = max(0.0, min(1.0, 1.0 - dispersion / max(abs(mean(returns)), 1.0)))

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

    strategy_counts = Counter(str(fold.get("selected_strategy_name") or "Unnamed") for fold in folds)
    warnings: list[str] = []
    no_trade_folds = len(folds) - len(active_folds)
    if no_trade_folds:
        warnings.append(
            f"{no_trade_folds} walk-forward fold(s) produced no trades in the external test block."
        )
    if len(strategy_counts) > 1:
        warnings.append(
            "Different strategy families won different folds. That can be useful regime evidence, "
            "but it also means there is no single consistently dominant setup."
        )
    if profitable_pct < 50.0:
        warnings.append("Fewer than half of the active walk-forward folds were profitable.")

    return {
        "symbol": symbol,
        "folds": folds,
        "summary": {
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
            "embargo_sessions": embargo_sessions,
            "adaptive_learning_enabled": bool(adaptive_learning),
            "adaptive_experience_count": len(adaptive_experience),
            "adaptive_profitable_experience_count": sum(
                1 for item in adaptive_experience if item.get("profitable")
            ),
        },
        "adaptive_learning": {
            "enabled": bool(adaptive_learning),
            "mode": "causal_experience_replay",
            "experience_count": len(adaptive_experience),
            "profitable_experience_count": sum(
                1 for item in adaptive_experience if item.get("profitable")
            ),
            "experience": adaptive_experience,
            "note": (
                "Each unseen fold is frozen before scoring. Only after that fold ends may its "
                "outcome seed rule options for the next fold. Losing folds still enter the "
                "expanding historical window; future folds are never visible early."
            ),
        },
        "warnings": warnings,
        "note": (
            "Each external fold is unseen by that fold's optimizer, with "
            f"{embargo_sessions} whole session(s) deliberately omitted immediately before the test block. "
            "Results remain historical simulations and do not establish future profitability."
        ),
    }
