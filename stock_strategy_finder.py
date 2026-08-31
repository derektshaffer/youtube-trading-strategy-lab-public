"""Stock-first strategy discovery and robustness research.

The Stock Strategy Finder deliberately separates *search ordering* from *candidate
eligibility*. AI or heuristics may prioritize work, but a technically valid
combination is never rejected merely because it looks unconventional. Historical
evidence decides what survives.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from statistics import median
from time import perf_counter
from typing import Any, Callable

from finder_report_persistence import (
    finder_summary_to_report,
    latest_completed_finder_report,
)
from trading_intelligence_core import paper_execution_fidelity, strategy_integrity_report
from trading_strategy_dna import is_family_source_strategy
from trading_validation_core import validation_strength, walk_forward_validate
from youtube_strategy_engine import (
    AppError,
    BacktestSettings,
    _period_metrics,
    OptimizationSettings,
    bars_to_frame,
    generate_local_strategy_refinements,
    normalize_machine_rules,
    optimize_stock_timeframes,
    resample_intraday_bars,
    run_backtest,
    safe_float,
)


STRATEGY_FIDELITY_ENGINE_VERSION = 1


@dataclass(frozen=True)
class StockSearchProfile:
    name: str
    history_days: int
    timeframes: tuple[str, ...]
    max_variants_per_strategy: int
    finalists_per_strategy: int
    execution_variants_per_finalist: int
    walk_forward_folds: int
    walk_forward_family_limit: int
    quick_family_limit: int | None
    stability_variants: int
    description: str


SEARCH_PROFILES: dict[str, StockSearchProfile] = {
    "Quick": StockSearchProfile(
        name="Quick",
        history_days=60,
        timeframes=("5Min",),
        max_variants_per_strategy=72,
        finalists_per_strategy=4,
        execution_variants_per_finalist=5,
        walk_forward_folds=2,
        walk_forward_family_limit=2,
        quick_family_limit=10,
        stability_variants=12,
        description="Broad first pass for fast feedback. Uses a diversity-balanced subset of families.",
    ),
    "Deep": StockSearchProfile(
        name="Deep",
        history_days=140,
        timeframes=("1Min", "5Min", "15Min"),
        max_variants_per_strategy=180,
        finalists_per_strategy=8,
        execution_variants_per_finalist=8,
        walk_forward_folds=4,
        walk_forward_family_limit=4,
        quick_family_limit=None,
        stability_variants=24,
        description="Default stock-specific research. Tests every technically eligible family across three timeframes.",
    ),
    "Current Regime": StockSearchProfile(
        name="Current Regime",
        history_days=35,
        timeframes=("1Min", "5Min", "15Min"),
        max_variants_per_strategy=120,
        finalists_per_strategy=6,
        execution_variants_per_finalist=6,
        walk_forward_folds=3,
        walk_forward_family_limit=4,
        quick_family_limit=None,
        stability_variants=18,
        description=(
            "Recent-behavior search for stocks whose character changes quickly. "
            "Tests every technically eligible family on roughly the latest month, "
            "then still applies holdout, walk-forward, cost, and parameter-stability checks."
        ),
    ),
    "Very Deep": StockSearchProfile(
        name="Very Deep",
        history_days=260,
        timeframes=("1Min", "5Min", "15Min"),
        max_variants_per_strategy=320,
        finalists_per_strategy=12,
        execution_variants_per_finalist=12,
        walk_forward_folds=6,
        walk_forward_family_limit=6,
        quick_family_limit=None,
        stability_variants=40,
        description="Maximum built-in search depth. Intended for long research runs, not quick iteration.",
    ),
}


def stock_finder_strategy_families(
    strategies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only root research families, never prior stock-specific children."""
    records = [dict(item) for item in strategies if isinstance(item, dict)]
    canonical = [
        item
        for item in records
        if str(item.get("source_type") or "").strip().casefold()
        == "canonical_family"
    ]
    if canonical:
        return canonical
    return [item for item in records if is_family_source_strategy(item)]


def search_profile(name: str) -> StockSearchProfile:
    profile = SEARCH_PROFILES.get(str(name or "").strip())
    if profile is None:
        raise AppError("Choose Quick, Deep, Current Regime, or Very Deep stock-strategy research.")
    return profile


def _technical_eligibility(strategy: dict[str, Any], symbol: str) -> tuple[bool, str]:
    if not isinstance(strategy, dict) or not strategy.get("id"):
        return False, "missing stable strategy id"
    if str(strategy.get("direction") or "long").strip().casefold() not in {"long", "both"}:
        return False, "short-only strategies are not supported by the current deterministic backtester"
    if strategy.get("backtest_supported") is False:
        return False, "the deterministic backtester cannot execute this strategy yet"
    locked = str(strategy.get("optimized_for_symbol") or "").strip().upper()
    if locked and locked != symbol.upper():
        return False, f"strategy is explicitly locked to {locked}"
    integrity = strategy_integrity_report(strategy)
    if str(integrity.get("status") or "") == "blocked":
        missing = list(integrity.get("critical_missing_requirements") or [])
        detail = ", ".join(str(item) for item in missing[:3]) or "important source logic"
        return False, "strategy fidelity audit failed: " + detail
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    if not any(value is not None for value in rules.values()):
        return False, "no machine-testable rules"
    if rules.get("max_spread_pct") is not None:
        return False, (
            "max_spread_pct requires point-in-time historical bid/ask quotes; "
            "fixed spread/slippage costs are not accepted as a substitute"
        )
    return True, ""


def strategy_behavior_bucket(strategy: dict[str, Any]) -> str:
    """Group strategies for diversity scheduling without declaring any bucket superior."""
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    category = str(strategy.get("category") or "").strip().casefold()
    tags: list[str] = []
    if rules.get("catalyst_required"):
        tags.append("catalyst")
    if rules.get("vwap_reclaim") or rules.get("above_vwap") is not None:
        tags.append("vwap")
    if rules.get("require_fast_ema_pullback") or rules.get("require_pullback_breakout"):
        tags.append("pullback")
    if rules.get("breakout_lookback_bars") or rules.get("previous_day_high_breakout"):
        tags.append("breakout")
    if rules.get("opening_range_minutes"):
        tags.append("opening-range")
    if rules.get("volume_surge_ratio") or rules.get("min_relative_volume"):
        tags.append("volume")
    if not tags:
        tags.append("other")
    return "|".join([category or "uncategorized", *sorted(set(tags))])


def diverse_strategy_order(strategies: list[dict[str, Any]], symbol: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Round-robin across behavior buckets.

    This controls search *order only*. It never removes a technically eligible
    family. Deep and Very Deep therefore test every eligible family.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    skipped: list[str] = []
    for strategy in strategies:
        eligible, reason = _technical_eligibility(strategy, symbol)
        if not eligible:
            skipped.append(f"{strategy.get('name') or 'Unnamed strategy'}: {reason}")
            continue
        buckets.setdefault(strategy_behavior_bucket(strategy), []).append(strategy)

    for values in buckets.values():
        values.sort(key=lambda item: (str(item.get("name") or ""), str(item.get("id") or "")))

    ordered: list[dict[str, Any]] = []
    bucket_names = sorted(buckets)
    while True:
        added = False
        for bucket in bucket_names:
            values = buckets[bucket]
            if values:
                ordered.append(values.pop(0))
                added = True
        if not added:
            break
    return ordered, skipped


def selected_strategies_for_profile(
    strategies: list[dict[str, Any]],
    symbol: str,
    profile: StockSearchProfile,
) -> tuple[list[dict[str, Any]], list[str]]:
    ordered, skipped = diverse_strategy_order(strategies, symbol)
    if profile.quick_family_limit is not None:
        return ordered[: profile.quick_family_limit], skipped
    return ordered, skipped


def stock_finder_optimizer_settings(profile: StockSearchProfile) -> OptimizationSettings:
    return OptimizationSettings(
        max_variants_per_strategy=profile.max_variants_per_strategy,
        finalists_per_strategy=profile.finalists_per_strategy,
        minimum_training_trades=5,
        minimum_validation_trades=2,
        enforce_historical_minimum_trades=True,
        minimum_historical_trades=8,
        training_fraction=0.60,
        validation_fraction=0.20,
        stress_cost_multiplier=1.75,
        optimize_position_sizing=True,
        automatic_slippage=True,
        max_execution_variants_per_finalist=profile.execution_variants_per_finalist,
        maximum_drawdown_pct=20.0,
        selection_mode="validated",
    )


def estimate_search_work(profile: StockSearchProfile, family_count: int) -> dict[str, int]:
    """Conservative estimate of the number of deterministic simulations.

    The optimizer performs coarse variants plus adaptive/local refinements and
    execution trials, so the real count is reported exactly after the run.
    """
    family_count = max(0, int(family_count))
    coarse = family_count * profile.max_variants_per_strategy * len(profile.timeframes)
    adaptive = family_count * min(120, max(24, profile.max_variants_per_strategy // 2)) * len(profile.timeframes)
    execution = (
        family_count
        * profile.finalists_per_strategy
        * max(0, profile.execution_variants_per_finalist - 1)
        * len(profile.timeframes)
    )
    validation = family_count * profile.finalists_per_strategy * len(profile.timeframes)
    return {
        "families": family_count,
        "coarse_rule_tests": coarse,
        "adaptive_rule_tests_estimate": adaptive,
        "execution_tests_estimate": execution,
        "validation_tests_estimate": validation,
        "minimum_estimated_simulations": coarse + adaptive + execution + validation,
    }


def _top_distinct_strategy_ids(report: dict[str, Any], maximum: int) -> list[str]:
    result: list[str] = []
    for item in report.get("rankings") or []:
        strategy_id = str(item.get("source_strategy_id") or "")
        if strategy_id and strategy_id not in result:
            result.append(strategy_id)
        if len(result) >= maximum:
            break
    return result


def _rows_for_sessions(rows: list[dict[str, Any]], sessions: list[str]) -> list[dict[str, Any]]:
    if not rows or not sessions:
        return []
    frame = bars_to_frame(rows, include_extended_hours=True)
    if "session" not in frame.columns:
        return []
    selected = frame[frame["session"].isin(set(sessions))].copy()
    if selected.empty:
        return []
    wanted = [
        column
        for column in ("timestamp", "open", "high", "low", "close", "volume", "vwap", "trade_count")
        if column in selected.columns
    ]
    return selected[wanted].to_dict("records")


def parameter_stability_test(
    rows: list[dict[str, Any]],
    source_strategy: dict[str, Any],
    optimization_report: dict[str, Any],
    *,
    maximum: int,
) -> dict[str, Any]:
    """Perturb the selected rules on the untouched holdout.

    This diagnostic never selects a winner. It asks whether nearby settings also
    survive, which helps expose brittle one-point optima.
    """
    winner = optimization_report.get("winner") or {}
    optimized_rules = normalize_machine_rules(winner.get("optimized_rules") or source_strategy.get("machine_rules"))
    settings = BacktestSettings(
        **(winner.get("optimized_backtest_settings") or optimization_report.get("backtest_settings") or {})
    )
    chosen_timeframe = str(optimization_report.get("timeframe") or "5Min")
    timeframe_rows = resample_intraday_bars(rows, chosen_timeframe, include_extended_hours=True)
    holdout_sessions = list(optimization_report.get("holdout_sessions") or [])
    holdout_rows = _rows_for_sessions(timeframe_rows, holdout_sessions)
    if not holdout_rows:
        return {
            "status": "insufficient_holdout",
            "tested": 0,
            "positive_pct": 0.0,
            "median_net_pnl": 0.0,
            "note": "Parameter stability could not run because the untouched holdout rows were unavailable.",
        }

    candidates = [optimized_rules, *generate_local_strategy_refinements(
        optimized_rules,
        settings,
        maximum=max(1, maximum - 1),
        stage="final",
    )]
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rules in candidates[:maximum]:
        signature = hashlib.sha256(
            json.dumps(rules, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:20]
        if signature in seen:
            continue
        seen.add(signature)
        candidate = {**source_strategy, "machine_rules": rules}
        # Preserve all pre-holdout warmup/context (EMA state, prior-session features,
        # VWAP-derived structure, etc.) and score only trades whose entries belong
        # to the frozen holdout sessions. Running the isolated holdout rows would
        # reset those causal features at the boundary and make nearby variants
        # incomparable with the selected winner.
        full_result = run_backtest(
            timeframe_rows,
            candidate,
            str(optimization_report.get("symbol") or ""),
            settings,
        )
        metrics = _period_metrics(
            full_result,
            set(holdout_sessions),
            settings.starting_cash,
        )
        results.append(
            {
                "signature": signature,
                "net_pnl": safe_float(metrics.get("net_pnl"), 0.0) or 0.0,
                "trade_count": int(safe_float(metrics.get("trade_count"), 0) or 0),
                "profit_factor": metrics.get("profit_factor"),
                "max_drawdown_pct": safe_float(metrics.get("max_drawdown_pct"), 0.0) or 0.0,
            }
        )

    active = [item for item in results if item["trade_count"] > 0]
    positive = [item for item in active if item["net_pnl"] > 0]
    positive_pct = len(positive) / len(active) * 100.0 if active else 0.0
    pnls = [item["net_pnl"] for item in active]
    if positive_pct >= 70.0:
        label = "STRONG"
    elif positive_pct >= 55.0:
        label = "PROMISING"
    elif positive_pct >= 40.0:
        label = "MIXED"
    else:
        label = "BRITTLE"
    return {
        "status": "complete",
        "label": label,
        "tested": len(results),
        "active": len(active),
        "positive": len(positive),
        "positive_pct": round(positive_pct, 1),
        "median_net_pnl": round(median(pnls), 2) if pnls else 0.0,
        "worst_net_pnl": round(min(pnls), 2) if pnls else 0.0,
        "best_net_pnl": round(max(pnls), 2) if pnls else 0.0,
        "results": results,
        "note": (
            "Nearby rule perturbations are evaluated only as a post-selection holdout diagnostic. "
            "Each variant retains the full pre-holdout causal warmup/context and only its holdout trades "
            "are scored; variants do not get to re-optimize the winner."
        ),
    }


def finder_evidence_verdict(
    robustness: dict[str, Any],
    stability: dict[str, Any],
    walk_forward: dict[str, Any],
    optimization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify the best stock-specific candidate without weakening validation.

    "ready_for_paper" remains the strict validation tier. Lower tiers deliberately
    preserve useful evidence so a historically profitable candidate is not hidden
    merely because it failed walk-forward or parameter-stability gates.
    """
    score = safe_float(robustness.get("score"), 0.0) or 0.0
    independent = bool(robustness.get("independently_positive"))
    stable_pct = safe_float(stability.get("positive_pct"), 0.0) or 0.0
    walk_summary = walk_forward.get("summary") or {}
    walk_pct = safe_float(walk_summary.get("profitable_fold_pct"), 0.0) or 0.0

    winner = (optimization or {}).get("winner") or {}
    periods = [
        winner.get("training_metrics") or {},
        winner.get("validation_metrics") or {},
        winner.get("holdout_metrics") or {},
        winner.get("stress_metrics") or {},
    ]
    period_pnls = [
        safe_float(metrics.get("net_pnl"), 0.0) or 0.0
        for metrics in periods
    ]
    period_trades = [
        int(safe_float(metrics.get("trade_count"), 0) or 0)
        for metrics in periods
    ]
    aggregate_pnl = sum(period_pnls)
    aggregate_trades = sum(period_trades)
    validation_positive = period_trades[1] > 0 and period_pnls[1] > 0
    holdout_positive = period_trades[2] > 0 and period_pnls[2] > 0

    if independent and score >= 65.0 and stable_pct >= 55.0 and walk_pct >= 50.0:
        return {
            "code": "ready_for_paper",
            "label": "READY FOR PAPER TESTING",
            "tone": "success",
            "research_tier": "validated",
            "paper_ready": True,
            "reason": "The selected stock-specific strategy survived the current holdout, cost, walk-forward, and parameter-stability gates.",
        }

    if score >= 50.0 or (walk_pct >= 50.0 and stable_pct >= 40.0):
        return {
            "code": "promising",
            "label": "PROMISING STOCK-SPECIFIC SETUP",
            "tone": "warning",
            "research_tier": "promising",
            "paper_ready": False,
            "reason": "Meaningful evidence survived, but one or more robustness gates still failed. Keep the candidate visible for research instead of treating it as validated.",
        }

    if (
        aggregate_trades > 0
        and aggregate_pnl > 0
        and (validation_positive or holdout_positive)
    ):
        return {
            "code": "historical_candidate",
            "label": "HISTORICALLY PROFITABLE CANDIDATE — NOT VALIDATED",
            "tone": "warning",
            "research_tier": "historical_candidate",
            "paper_ready": False,
            "reason": "The Finder found a configuration with useful historical profitability, but it was not durable enough across the independent validation, walk-forward, stress, or nearby-parameter checks.",
        }

    return {
        "code": "no_robust_strategy",
        "label": "NO RELIABLE EDGE FOUND",
        "tone": "error",
        "research_tier": "no_reliable_edge",
        "paper_ready": False,
        "reason": "The broad search tested historical candidates, but the strongest configuration did not show enough positive evidence to justify even a promising stock-specific classification.",
    }


def apply_paper_fidelity_to_verdict(
    verdict: dict[str, Any],
    paper_fidelity: dict[str, Any],
) -> dict[str, Any]:
    """Downgrade paper readiness when live/paper execution cannot reproduce it."""
    if (
        str(verdict.get("code") or "") == "ready_for_paper"
        and str(paper_fidelity.get("status") or "") != "ready"
    ):
        return {
            "code": "historically_robust_execution_gap",
            "label": "ROBUST HISTORICALLY — PAPER ENGINE NOT YET FAITHFUL",
            "tone": "warning",
            "research_tier": "historically_robust_execution_gap",
            "paper_ready": False,
            "reason": (
                "The strategy survived the historical robustness gates, but Paper Auto cannot yet "
                "reproduce the same trade-management rules. Keep it in research/paper-manual mode "
                "until live execution fidelity is implemented."
            ),
        }
    return dict(verdict or {})


def validated_status_ready(
    verdict: dict[str, Any],
    paper_fidelity: dict[str, Any],
    walk_forward: dict[str, Any] | None,
) -> bool:
    """Return whether the research evidence itself qualifies as historically validated.

    Historical validation and Paper Auto execution fidelity are separate claims.
    A strategy may pass the full holdout/walk-forward/stability protocol while the
    current paper runner remains unable to reproduce its lifecycle. Paper readiness
    is checked independently wherever execution is enabled.
    """
    code = str(verdict.get("code") or "")
    historically_robust = code in {
        "ready_for_paper",
        "historically_robust_execution_gap",
    }
    return bool(walk_forward) and historically_robust


def regime_diagnostics(
    rows: list[dict[str, Any]],
    source_strategy: dict[str, Any],
    optimization_report: dict[str, Any],
) -> dict[str, Any]:
    """Describe how the frozen winning rules behave across trailing regimes.

    These windows are diagnostics only. They do not re-optimize or participate in
    winner selection, so they cannot turn a research-only candidate into a
    validated strategy.
    """
    winner = optimization_report.get("winner") or {}
    if not winner:
        return {"status": "unavailable", "windows": []}

    timeframe = str(optimization_report.get("timeframe") or "5Min")
    timeframe_rows = resample_intraday_bars(
        rows,
        timeframe,
        include_extended_hours=True,
    )
    frame = bars_to_frame(timeframe_rows, include_extended_hours=True)
    if "session" not in frame.columns:
        return {"status": "unavailable", "windows": []}
    sessions = list(dict.fromkeys(frame["session"].tolist()))
    if not sessions:
        return {"status": "unavailable", "windows": []}

    settings = BacktestSettings(
        **(
            winner.get("optimized_backtest_settings")
            or optimization_report.get("backtest_settings")
            or {}
        )
    )
    candidate = {
        **source_strategy,
        "machine_rules": normalize_machine_rules(
            winner.get("optimized_rules")
            or source_strategy.get("machine_rules")
        ),
    }

    requested = [
        ("Recent regime", 20),
        ("Intermediate regime", 60),
        ("Longer regime", 120),
        ("Full search history", len(sessions)),
    ]
    windows: list[dict[str, Any]] = []
    seen_counts: set[int] = set()
    for label, requested_sessions in requested:
        count = min(len(sessions), max(1, int(requested_sessions)))
        if count in seen_counts:
            continue
        seen_counts.add(count)
        selected_sessions = sessions[-count:]
        selected_rows = _rows_for_sessions(timeframe_rows, selected_sessions)
        metrics = (
            run_backtest(
                selected_rows,
                candidate,
                str(optimization_report.get("symbol") or ""),
                settings,
            ).get("metrics")
            or {}
        )
        windows.append(
            {
                "label": label,
                "session_count": count,
                "start_session": selected_sessions[0],
                "end_session": selected_sessions[-1],
                "metrics": metrics,
                "profitable": (
                    int(safe_float(metrics.get("trade_count"), 0) or 0) > 0
                    and (safe_float(metrics.get("net_pnl"), 0.0) or 0.0) > 0
                ),
            }
        )
    return {
        "status": "complete",
        "timeframe": timeframe,
        "windows": windows,
        "note": (
            "Regime diagnostics replay the already-selected frozen rules over trailing windows. "
            "They are descriptive and never count as independent validation."
        ),
    }


def _verdict(
    robustness: dict[str, Any],
    stability: dict[str, Any],
    walk_forward: dict[str, Any],
    optimization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Backward-compatible internal alias."""
    return finder_evidence_verdict(
        robustness,
        stability,
        walk_forward,
        optimization,
    )


def complete_stock_strategy_finder_from_optimization(
    one_minute_rows: list[dict[str, Any]],
    all_strategies: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    skipped: list[str],
    symbol: str,
    profile: StockSearchProfile,
    settings: BacktestSettings,
    optimizer: OptimizationSettings,
    optimization: dict[str, Any],
    *,
    progress: Callable[[int, int, str], None] | None = None,
    total_started: float | None = None,
    optimization_seconds: float | None = None,
    parallel_workers: int = 1,
    strategies_considered_count: int | None = None,
) -> dict[str, Any]:
    stage_timings: dict[str, float] = {}
    if optimization_seconds is not None:
        stage_timings["optimization"] = round(float(optimization_seconds), 3)

    distinct_ids = _top_distinct_strategy_ids(
        optimization,
        profile.walk_forward_family_limit,
    )
    wanted = set(distinct_ids)
    walk_strategies = [
        item for item in selected
        if str(item.get("id") or "") in wanted
    ]
    chosen_timeframe = str(optimization.get("timeframe") or "5Min")
    chosen_rows = resample_intraday_bars(
        one_minute_rows,
        chosen_timeframe,
        include_extended_hours=True,
    )

    if progress:
        progress(
            910,
            1000,
            f"Walk-forward: trying to disprove the top {len(walk_strategies)} family candidates…",
        )

    walk_optimizer = replace(
        optimizer,
        max_variants_per_strategy=min(
            profile.max_variants_per_strategy,
            140 if profile.name == "Quick" else 180,
        ),
        finalists_per_strategy=min(profile.finalists_per_strategy, 8),
    )
    walk_started = perf_counter()
    walk = walk_forward_validate(
        chosen_rows,
        walk_strategies,
        symbol,
        settings,
        walk_optimizer,
        minimum_history_sessions=8,
        test_sessions_per_fold=2,
        max_folds=profile.walk_forward_folds,
    )
    stage_timings["walk_forward"] = round(
        perf_counter() - walk_started,
        3,
    )
    robustness = validation_strength(optimization, walk)

    winner = optimization.get("winner") or {}
    source_id = str(winner.get("source_strategy_id") or "")
    winner_source = next(
        (
            item for item in selected
            if str(item.get("id") or "") == source_id
        ),
        None,
    )
    if winner_source is None:
        raise AppError(
            "The winning strategy family could not be resolved after optimization."
        )

    if progress:
        progress(
            965,
            1000,
            "Parameter stability: perturbing the winning rules on untouched holdout data…",
        )

    stability_started = perf_counter()
    stability = parameter_stability_test(
        one_minute_rows,
        winner_source,
        optimization,
        maximum=profile.stability_variants,
    )
    stage_timings["parameter_stability"] = round(
        perf_counter() - stability_started,
        3,
    )
    regime_report = regime_diagnostics(
        one_minute_rows,
        winner_source,
        optimization,
    )
    verdict = _verdict(robustness, stability, walk, optimization)
    paper_fidelity = paper_execution_fidelity({
        **winner_source,
        "validation_status": "research_only",
        "validated_rules": None,
        "machine_rules": winner.get("optimized_rules") or winner_source.get("machine_rules") or {},
    })
    verdict = apply_paper_fidelity_to_verdict(verdict, paper_fidelity)
    if total_started is not None:
        stage_timings["total"] = round(
            perf_counter() - total_started,
            3,
        )
    else:
        stage_timings["total"] = round(
            sum(stage_timings.values()),
            3,
        )

    return {
        "version": "stock-strategy-finder-v1",
        "strategy_fidelity_engine_version": STRATEGY_FIDELITY_ENGINE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": str(symbol or "").strip().upper(),
        "profile": asdict(profile),
        "search_policy": {
            "ai_may_prioritize": True,
            "ai_may_veto_valid_combinations": False,
            "deep_modes_test_all_technically_eligible_families": True,
            "diversity_scheduling": "round_robin_behavior_buckets",
            "selection_basis": (
                "historical evidence + independent validation, "
                "not largest optimized P/L"
            ),
        },
        "strategies_considered": (
            int(strategies_considered_count)
            if strategies_considered_count is not None
            else len(all_strategies)
        ),
        "strategies_tested": len(selected),
        "technical_skips": skipped,
        "estimated_work": estimate_search_work(profile, len(selected)),
        "optimization": optimization,
        "walk_forward": walk,
        "robustness": robustness,
        "parameter_stability": stability,
        "regime_diagnostics": regime_report,
        "paper_execution_fidelity": paper_fidelity,
        "verdict": verdict,
        "winner_source_strategy_id": source_id,
        "winner_strategy_name": winner.get("strategy_name"),
        "timeframe": chosen_timeframe,
        "unique_configurations_tested": int(
            optimization.get("unique_configurations_tested") or 0
        ),
        "configuration_history": list(
            optimization.get("configuration_history") or []
        ),
        "stage_timings_seconds": stage_timings,
        "parallel_workers": int(
            optimization.get("parallel_workers") or parallel_workers or 1
        ),
        "parallelized_by": optimization.get("parallelized_by") or "none",
    }


def run_stock_strategy_finder(
    one_minute_rows: list[dict[str, Any]],
    strategies: list[dict[str, Any]],
    symbol: str,
    *,
    profile_name: str = "Deep",
    backtest_settings: BacktestSettings | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    resume_state: dict[str, Any] | None = None,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
    parallel_workers: int = 1,
) -> dict[str, Any]:
    total_started = perf_counter()
    stage_timings: dict[str, float] = {}
    profile = search_profile(profile_name)
    selected, skipped = selected_strategies_for_profile(strategies, symbol, profile)
    if not selected:
        raise AppError("No machine-testable long strategy families are available for this stock yet.")

    settings = backtest_settings or BacktestSettings()
    settings.validate()
    optimizer = stock_finder_optimizer_settings(profile)

    if progress:
        progress(0, 1000, f"{profile.name} search: testing {len(selected)} strategy families without AI vetoes…")

    optimization_started = perf_counter()
    optimization = optimize_stock_timeframes(
        one_minute_rows,
        selected,
        symbol,
        settings,
        optimizer,
        timeframes=profile.timeframes,
        progress=progress,
        resume_state=resume_state,
        checkpoint=checkpoint,
        parallel_workers=parallel_workers,
    )
    stage_timings["optimization"] = round(perf_counter() - optimization_started, 3)

    return complete_stock_strategy_finder_from_optimization(
        one_minute_rows,
        strategies,
        selected,
        skipped,
        symbol,
        profile,
        settings,
        optimizer,
        optimization,
        progress=progress,
        total_started=total_started,
        optimization_seconds=stage_timings["optimization"],
        parallel_workers=parallel_workers,
    )

def compact_configuration_record(record: dict[str, Any], *, symbol: str) -> dict[str, Any]:
    rules = {
        key: value
        for key, value in (record.get("rules") or {}).items()
        if value is not None
    }
    settings = record.get("settings") or {}
    keep_settings = {
        key: settings.get(key)
        for key in (
            "risk_per_trade_pct",
            "max_position_pct",
            "default_stop_pct",
            "default_reward_risk",
            "spread_bps",
            "slippage_bps",
            "max_concurrent_positions",
            "allow_extended_hours",
            "extended_hours_position_scale",
            "ignore_strategy_session_end",
            "allow_price_extension_after_qualification",
            "require_pullback_breakout_for_pullback_strategies",
        )
        if key in settings
    }
    return {
        "symbol": symbol,
        "timeframe": record.get("timeframe"),
        "signature": record.get("signature"),
        "strategy_id": record.get("strategy_id"),
        "strategy_name": record.get("strategy_name"),
        "phases": record.get("phases") or [],
        "rules": rules,
        "settings": keep_settings,
        "metrics": record.get("metrics") or {},
    }


def latest_finder_checkpoint(
    data: dict[str, Any],
    symbol: str,
    profile_name: str | None = None,
) -> dict[str, Any] | None:
    target_symbol = str(symbol or "").strip().upper()
    target_profile = str(profile_name or "").strip()
    for item in data.get("stock_strategy_finder_checkpoints") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("symbol") or "").strip().upper() != target_symbol:
            continue
        if target_profile and str(item.get("profile") or "").strip() != target_profile:
            continue
        return dict(item)
    return None


def upsert_finder_checkpoint(
    data: dict[str, Any],
    checkpoint: dict[str, Any],
) -> dict[str, Any]:
    result = dict(data or {})
    record = dict(checkpoint or {})
    symbol = str(record.get("symbol") or "").strip().upper()
    profile_name = str(record.get("profile") or "").strip()
    started_at = str(record.get("started_at") or record.get("updated_at") or "")
    if not symbol or not profile_name:
        raise AppError("A Finder checkpoint needs both a stock symbol and search profile.")
    record["symbol"] = symbol
    record["profile"] = profile_name
    if not record.get("id"):
        record["id"] = hashlib.sha256(
            f"{symbol}|{profile_name}|{started_at}".encode("utf-8")
        ).hexdigest()[:24]
    record_id = str(record.get("id") or "")
    existing = [
        dict(item)
        for item in result.get("stock_strategy_finder_checkpoints") or []
        if isinstance(item, dict) and str(item.get("id") or "") != record_id
    ]
    result["stock_strategy_finder_checkpoints"] = [record, *existing][:25]
    return result


def merge_finder_checkpoint_into_library(
    data: dict[str, Any],
    checkpoint: dict[str, Any],
) -> dict[str, Any]:
    """Persist a lean resumable checkpoint plus completed configuration evidence.

    Raw configuration histories can be very large. They are merged into the
    existing bounded configuration ledger, while the resumable engine state keeps
    rankings and counts but drops duplicated configuration payloads.
    """
    result = dict(data or {})
    record = dict(checkpoint or {})
    symbol = str(record.get("symbol") or "").strip().upper()
    profile_name = str(record.get("profile") or "").strip()
    previous_checkpoint = latest_finder_checkpoint(result, symbol, profile_name)
    previous_engine_state = dict((previous_checkpoint or {}).get("engine_state") or {})
    previous_timeframes = dict(previous_engine_state.get("timeframes") or {})
    engine_state = dict(record.get("engine_state") or {})
    timeframes = dict(engine_state.get("timeframes") or {})
    durable_timeframes: dict[str, Any] = {}
    new_records: list[dict[str, Any]] = []

    for timeframe, raw_state in timeframes.items():
        state = dict(raw_state or {})
        history = [
            dict(item)
            for item in state.get("configuration_history") or []
            if isinstance(item, dict)
        ]
        total_configuration_count = max(
            int(state.get("configuration_count") or 0),
            len(history),
        )
        previous_state = dict(previous_timeframes.get(str(timeframe)) or {})
        previous_configuration_count = int(previous_state.get("configuration_count") or 0)
        new_configuration_count = max(0, total_configuration_count - previous_configuration_count)
        history_to_persist = (
            history[-new_configuration_count:]
            if 0 < new_configuration_count < len(history)
            else (history if new_configuration_count else [])
        )
        for raw in history_to_persist:
            raw.setdefault("timeframe", str(timeframe))
            compact = compact_configuration_record(raw, symbol=symbol)
            if compact.get("signature"):
                new_records.append(compact)
        durable_state = dict(state)
        durable_state["configuration_count"] = total_configuration_count
        durable_state["configuration_history"] = []
        durable_timeframes[str(timeframe)] = durable_state

    durable_engine_state = dict(engine_state)
    durable_engine_state["timeframes"] = durable_timeframes
    record["engine_state"] = durable_engine_state
    result = upsert_finder_checkpoint(result, record)

    existing_records = list(result.get("stock_strategy_configuration_ledger") or [])
    seen = {
        (
            str(item.get("symbol") or ""),
            str(item.get("timeframe") or ""),
            str(item.get("signature") or ""),
        )
        for item in existing_records
        if isinstance(item, dict)
    }
    additions: list[dict[str, Any]] = []
    for compact in new_records:
        key = (
            str(compact.get("symbol") or ""),
            str(compact.get("timeframe") or ""),
            str(compact.get("signature") or ""),
        )
        if not key[2] or key in seen:
            continue
        seen.add(key)
        additions.append(compact)
    result["stock_strategy_configuration_ledger"] = [*additions, *existing_records][:50000]
    return result


def apply_historical_spread_integrity_guard(
    report: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    """Attach real-quote execution evidence and fail closed when stress was too mild."""
    guarded = deepcopy(report or {})
    guarded["historical_spread_audit"] = deepcopy(audit or {})
    if str((audit or {}).get("status") or "").upper() != "UNDERMODELED":
        return guarded

    optimization = dict(guarded.get("optimization") or {})
    winner = dict(optimization.get("winner") or {})
    if str(winner.get("status") or "").strip().upper() == "VALIDATED":
        winner["pre_spread_audit_status"] = winner.get("status")
        winner["status"] = "HOLDOUT SPREAD UNDERMODELED"
    optimization["winner"] = winner
    guarded["optimization"] = optimization

    robustness = dict(guarded.get("robustness") or {})
    robustness["independently_positive"] = False
    reasons = list(robustness.get("reasons") or [])
    reasons.append(
        "Real holdout entry quotes exceeded the maximum spread assumed by the execution-cost sensitivity curve."
    )
    robustness["reasons"] = list(dict.fromkeys(reasons))
    score = safe_float(robustness.get("score"))
    if score is not None:
        robustness["score"] = min(float(score), 49.0)
        robustness["label"] = "WEAK"
    guarded["robustness"] = robustness

    verdict = dict(guarded.get("verdict") or {})
    if str(verdict.get("code") or "") == "ready_for_paper":
        guarded["verdict"] = {
            "code": "historical_spread_under_modeled",
            "label": "PROMISING — REAL SPREADS EXCEEDED STRESS RANGE",
            "tone": "warning",
            "research_tier": "execution_model_gap",
            "paper_ready": False,
            "reason": (
                "The frozen winner survived modeled execution stress, but real bid/ask spreads "
                "at untouched-holdout entry moments exceeded the largest spread assumption tested. "
                "Increase the execution-cost envelope and revalidate before treating it as robust."
            ),
        }
    return guarded


def holdout_reuse_audit(
    data: dict[str, Any],
    report: dict[str, Any],
    *,
    material_overlap_pct: float = 0.0,
) -> dict[str, Any]:
    """Detect whether a supposed final holdout has already been inspected.

    A holdout is only independent the first time its outcomes are revealed. Reusing
    substantially the same dates in later research can still be useful, but it must
    not retain the same validation meaning after developers have seen those outcomes.
    """
    optimization = (
        report.get("optimization")
        if isinstance(report.get("optimization"), dict)
        else report
    )
    current_sessions = sorted(
        {
            str(value)
            for value in optimization.get("holdout_sessions") or []
            if str(value).strip()
        }
    )
    symbol = str(report.get("symbol") or "").strip().upper()
    timeframe = str(report.get("timeframe") or "").strip()
    fingerprint = hashlib.sha256(
        "|".join([symbol, timeframe, *current_sessions]).encode("utf-8")
    ).hexdigest()[:24] if current_sessions else ""

    exposures: list[dict[str, Any]] = []
    current_set = set(current_sessions)
    threshold = max(0.0, min(100.0, float(material_overlap_pct)))
    prior_runs = [
        *list(data.get("stock_strategy_finder_runs") or []),
        *list(data.get("validation_runs") or []),
        *list(data.get("holdout_exposure_ledger") or []),
    ]
    for prior in prior_runs:
        if not isinstance(prior, dict):
            continue
        if str(prior.get("symbol") or "").strip().upper() != symbol:
            continue
        prior_sessions = {
            str(value)
            for value in prior.get("holdout_sessions") or []
            if str(value).strip()
        }
        if not prior_sessions or not current_set:
            continue
        overlap = sorted(current_set & prior_sessions)
        if not overlap:
            continue
        current_overlap_pct = len(overlap) / len(current_set) * 100.0
        prior_overlap_pct = len(overlap) / len(prior_sessions) * 100.0
        if current_overlap_pct <= threshold:
            continue
        exposures.append(
            {
                "run_id": prior.get("id"),
                "generated_at": prior.get("generated_at"),
                "profile": prior.get("profile"),
                "timeframe": prior.get("timeframe"),
                "overlap_sessions": overlap,
                "current_overlap_pct": round(current_overlap_pct, 1),
                "prior_overlap_pct": round(prior_overlap_pct, 1),
                "exact_fingerprint_match": bool(
                    fingerprint
                    and str(prior.get("holdout_fingerprint") or "") == fingerprint
                ),
            }
        )

    return {
        "status": "PRISTINE" if not exposures else "REUSED",
        "pristine": not exposures,
        "symbol": symbol,
        "timeframe": timeframe,
        "holdout_sessions": current_sessions,
        "holdout_fingerprint": fingerprint or None,
        "prior_material_exposure_count": len(exposures),
        "material_overlap_threshold_pct": threshold,
        "prior_exposures": exposures[:20],
        "note": (
            "This holdout has not appeared materially in a prior saved Finder run."
            if not exposures
            else (
                "This holdout overlaps previously inspected outcomes. Even one reused "
                "session means the final evidence is no longer pristine independent confirmation."
            )
        ),
    }


def record_holdout_exposure(
    data: dict[str, Any],
    report: dict[str, Any],
    *,
    source: str,
    generated_at: str | None = None,
    maximum_records: int = 5000,
) -> dict[str, Any]:
    """Durably record that holdout outcomes have been revealed, even if not saved as validated."""
    result = dict(data or {})
    optimization = (
        report.get("optimization")
        if isinstance(report.get("optimization"), dict)
        else report
    )
    sessions = sorted(
        {
            str(value)
            for value in (optimization or {}).get("holdout_sessions") or []
            if str(value).strip()
        }
    )
    symbol = str(report.get("symbol") or "").strip().upper()
    timeframe = str(report.get("timeframe") or optimization.get("timeframe") or "").strip()
    if not symbol or not sessions:
        return result

    fingerprint = hashlib.sha256(
        "|".join([symbol, timeframe, *sessions]).encode("utf-8")
    ).hexdigest()[:24]
    record = {
        "id": "holdout-exposure-" + hashlib.sha256(
            "|".join(
                [
                    str(source or "unknown"),
                    symbol,
                    timeframe,
                    fingerprint,
                    str(generated_at or report.get("generated_at") or ""),
                ]
            ).encode("utf-8")
        ).hexdigest()[:24],
        "source": str(source or "unknown"),
        "symbol": symbol,
        "timeframe": timeframe,
        "generated_at": str(generated_at or report.get("generated_at") or ""),
        "holdout_sessions": sessions,
        "holdout_fingerprint": fingerprint,
    }
    existing = [
        dict(item)
        for item in result.get("holdout_exposure_ledger") or []
        if isinstance(item, dict)
        and str(item.get("id") or "") != record["id"]
    ]
    result["holdout_exposure_ledger"] = [record, *existing][
        : max(1, int(maximum_records))
    ]
    return result


def apply_holdout_reuse_guard(
    data: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Downgrade paper-validation claims when the final holdout was already seen."""
    guarded = deepcopy(report or {})
    audit = holdout_reuse_audit(data, guarded)
    guarded["holdout_reuse_audit"] = audit
    if audit.get("pristine"):
        return guarded

    verdict = dict(guarded.get("verdict") or {})
    if str(verdict.get("code") or "") == "ready_for_paper":
        guarded["verdict"] = {
            "code": "holdout_reused",
            "label": "PROMISING — HOLDOUT NO LONGER PRISTINE",
            "tone": "warning",
            "research_tier": "holdout_reused",
            "paper_ready": False,
            "reason": (
                "The strategy passed the current calculations, but at least half of this "
                "final holdout was already exposed in prior saved research. Use a genuinely "
                "new later holdout before restoring validated/paper-ready status."
            ),
        }

    robustness = dict(guarded.get("robustness") or {})
    robustness["independently_positive"] = False
    reasons = list(robustness.get("reasons") or [])
    reasons.append(
        "Final holdout materially overlaps previously inspected outcomes, so it is no longer independent evidence."
    )
    robustness["reasons"] = list(dict.fromkeys(reasons))
    guarded["robustness"] = robustness

    nested_optimizer = isinstance(guarded.get("optimization"), dict)
    optimization = (
        dict(guarded.get("optimization") or {})
        if nested_optimizer
        else guarded
    )
    winner = dict(optimization.get("winner") or {})
    if str(winner.get("status") or "").strip().upper() == "VALIDATED":
        winner["pre_holdout_reuse_status"] = winner.get("status")
        winner["status"] = "HOLDOUT REUSED"
    optimization["winner"] = winner
    if nested_optimizer:
        guarded["optimization"] = optimization
    else:
        guarded["winner"] = winner
    return guarded


def merge_finder_report_into_library(data: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Persist search summary + exact compact configuration ledger."""
    result = dict(data or {})
    report = apply_holdout_reuse_guard(result, report)
    symbol = str(report.get("symbol") or "").upper()
    generated_at = str(report.get("generated_at") or "")
    run_id = hashlib.sha256(
        f"{symbol}|{generated_at}|{report.get('winner_source_strategy_id') or ''}".encode("utf-8")
    ).hexdigest()[:24]

    optimization = report.get("optimization") or {}
    winner = optimization.get("winner") or {}
    summary = {
        "id": run_id,
        "generated_at": generated_at,
        "symbol": symbol,
        "profile": (report.get("profile") or {}).get("name"),
        "profile_details": report.get("profile") or {},
        "strategy_fidelity_engine_version": int(report.get("strategy_fidelity_engine_version") or 0),
        "search_policy": report.get("search_policy") or {},
        "verdict": report.get("verdict") or {},
        "winner_strategy_name": report.get("winner_strategy_name"),
        "winner_source_strategy_id": report.get("winner_source_strategy_id"),
        "timeframe": report.get("timeframe"),
        "unique_configurations_tested": report.get("unique_configurations_tested"),
        "strategies_considered": report.get("strategies_considered"),
        "strategies_tested": report.get("strategies_tested"),
        "technical_skips": report.get("technical_skips") or [],
        "estimated_work": report.get("estimated_work") or {},
        "stage_timings_seconds": report.get("stage_timings_seconds") or {},
        "parallel_workers": int(report.get("parallel_workers") or 1),
        "parallelized_by": report.get("parallelized_by") or "none",
        "distributed": report.get("distributed") or {},
        "robustness": report.get("robustness") or {},
        "parameter_stability": report.get("parameter_stability") or {},
        "regime_diagnostics": report.get("regime_diagnostics") or {},
        "paper_execution_fidelity": report.get("paper_execution_fidelity") or {},
        "historical_spread_audit": report.get("historical_spread_audit") or {},
        "market_data_integrity": report.get("market_data_integrity") or {},
        "holdout_reuse_audit": report.get("holdout_reuse_audit") or {},
        "holdout_sessions": list(optimization.get("holdout_sessions") or []),
        "holdout_fingerprint": (report.get("holdout_reuse_audit") or {}).get("holdout_fingerprint"),
        "walk_forward_summary": (report.get("walk_forward") or {}).get("summary") or {},
        "training_metrics": winner.get("training_metrics") or {},
        "validation_metrics": winner.get("validation_metrics") or {},
        "holdout_metrics": winner.get("holdout_metrics") or {},
        "stress_metrics": winner.get("stress_metrics") or {},
        "execution_sensitivity": winner.get("execution_sensitivity") or {},
        "holdout_execution_sensitivity": winner.get("holdout_execution_sensitivity") or {},
        "optimizer_status": winner.get("status"),
        "optimized_rules": winner.get("optimized_rules") or {},
        "optimized_backtest_settings": winner.get("optimized_backtest_settings") or {},
    }
    old_runs = [
        item for item in result.get("stock_strategy_finder_runs") or []
        if str(item.get("id") or "") != run_id
    ]
    result["stock_strategy_finder_runs"] = [summary, *old_runs][:100]

    existing_records = list(result.get("stock_strategy_configuration_ledger") or [])
    seen = {
        (
            str(item.get("symbol") or ""),
            str(item.get("timeframe") or ""),
            str(item.get("signature") or ""),
        )
        for item in existing_records
    }
    new_records = []
    for raw in report.get("configuration_history") or []:
        compact = compact_configuration_record(raw, symbol=symbol)
        key = (
            str(compact.get("symbol") or ""),
            str(compact.get("timeframe") or ""),
            str(compact.get("signature") or ""),
        )
        if not key[2] or key in seen:
            continue
        seen.add(key)
        new_records.append(compact)
    # Keep a large but bounded durable ledger. It includes losers as well as winners.
    result["stock_strategy_configuration_ledger"] = [*new_records, *existing_records][:50000]

    # Materialize the selected stock-specific strategy as a child of the source
    # family. This preserves the general research family while giving paper/live
    # workflows an explicit ticker-locked candidate to track.
    source_id = str(report.get("winner_source_strategy_id") or "")
    source = next(
        (
            item for item in result.get("strategies") or []
            if str(item.get("id") or "") == source_id
        ),
        None,
    )
    if isinstance(source, dict) and winner:
        child_id = "stockfinder-" + hashlib.sha256(
            f"{source_id}|{symbol}".encode("utf-8")
        ).hexdigest()[:18]
        verdict = report.get("verdict") or {}
        historically_validated = validated_status_ready(
            verdict,
            report.get("paper_execution_fidelity") or {},
            report.get("walk_forward") or {},
        )
        paper_ready = (
            historically_validated
            and str(verdict.get("code") or "") == "ready_for_paper"
            and str((report.get("paper_execution_fidelity") or {}).get("status") or "")
            == "ready"
        )
        child = {
            **source,
            "id": child_id,
            "name": f"{source.get('name') or 'Strategy'} — {symbol} optimized",
            "source_type": "stock_specific_finder",
            "parent_strategy_id": source_id,
            "parent_is_master_strategy": True,
            "optimized_for_symbol": symbol,
            "optimized_at": generated_at,
            "machine_rules": winner.get("optimized_rules") or source.get("machine_rules") or {},
            "optimized_backtest_settings": winner.get("optimized_backtest_settings") or {},
            "validated_rules": (
                winner.get("optimized_rules") or source.get("machine_rules") or {}
                if historically_validated
                else None
            ),
            "validated_backtest_settings": (
                winner.get("optimized_backtest_settings") or {}
                if historically_validated
                else None
            ),
            "validated_at": generated_at if historically_validated else None,
            "validation_status": "validated" if historically_validated else "research_only",
            "paper_validation_status": "ready" if paper_ready else "not_ready",
            # Approval belongs to the exact optimized child. Never inherit the
            # parent family's approval after its rules have changed.
            "approved": False,
            "stock_strategy_finder_verdict": verdict,
            "stock_strategy_finder_run_id": run_id,
            "last_validation": {
                "symbol": symbol,
                "generated_at": generated_at,
                "robustness_score": (report.get("robustness") or {}).get("score"),
                "robustness_label": (report.get("robustness") or {}).get("label"),
                "optimizer_status": winner.get("status"),
                "training_metrics": winner.get("training_metrics") or {},
                "validation_metrics": winner.get("validation_metrics") or {},
                "holdout_metrics": winner.get("holdout_metrics") or {},
                "stress_metrics": winner.get("stress_metrics") or {},
                "execution_sensitivity": winner.get("execution_sensitivity") or {},
                "holdout_execution_sensitivity": winner.get("holdout_execution_sensitivity") or {},
                "walk_forward_summary": (report.get("walk_forward") or {}).get("summary") or {},
                "parameter_stability": report.get("parameter_stability") or {},
                "historical_spread_audit": report.get("historical_spread_audit") or {},
                "market_data_integrity": report.get("market_data_integrity") or {},
                "holdout_reuse_audit": report.get("holdout_reuse_audit") or {},
            },
        }
        if not historically_validated:
            child.pop("validated_rules", None)
            child.pop("validated_backtest_settings", None)
            child.pop("validated_at", None)

        existing_strategies = [
            item for item in result.get("strategies") or []
            if str(item.get("id") or "") != child_id
        ]
        result["strategies"] = [child, *existing_strategies]
        summary["stock_specific_strategy_id"] = child_id
        summary["paper_validation_status"] = child["paper_validation_status"]

    result = record_holdout_exposure(
        result,
        report,
        source="stock_strategy_finder",
        generated_at=str(report.get("generated_at") or ""),
    )

    checkpoint = latest_finder_checkpoint(
        result,
        symbol,
        (report.get("profile") or {}).get("name"),
    )
    if checkpoint:
        completed_checkpoint = {
            **checkpoint,
            "status": "complete",
            "progress": 1.0,
            "message": f"{symbol} strategy research complete",
            "updated_at": generated_at,
            "completed_at": generated_at,
            "last_error": None,
            "engine_state": {},
            "result_run_id": run_id,
        }
        result = upsert_finder_checkpoint(result, completed_checkpoint)

    return result
