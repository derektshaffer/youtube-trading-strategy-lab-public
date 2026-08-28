"""Stock-first strategy discovery and robustness research.

The Stock Strategy Finder deliberately separates *search ordering* from *candidate
eligibility*. AI or heuristics may prioritize work, but a technically valid
combination is never rejected merely because it looks unconventional. Historical
evidence decides what survives.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from statistics import median
from typing import Any, Callable

from trading_validation_core import validation_strength, walk_forward_validate
from youtube_strategy_engine import (
    AppError,
    BacktestSettings,
    OptimizationSettings,
    bars_to_frame,
    generate_local_strategy_refinements,
    normalize_machine_rules,
    optimize_stock_timeframes,
    resample_intraday_bars,
    run_backtest,
    safe_float,
)


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


def search_profile(name: str) -> StockSearchProfile:
    profile = SEARCH_PROFILES.get(str(name or "").strip())
    if profile is None:
        raise AppError("Choose Quick, Deep, or Very Deep stock-strategy research.")
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
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    if not any(value is not None for value in rules.values()):
        return False, "no machine-testable rules"
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
    holdout_rows = _rows_for_sessions(timeframe_rows, list(optimization_report.get("holdout_sessions") or []))
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
        metrics = (run_backtest(holdout_rows, candidate, str(optimization_report.get("symbol") or ""), settings).get("metrics") or {})
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
            "Nearby rule perturbations are evaluated only as a post-selection holdout diagnostic; "
            "they do not get to re-optimize the winner."
        ),
    }


def _verdict(
    robustness: dict[str, Any],
    stability: dict[str, Any],
    walk_forward: dict[str, Any],
) -> dict[str, Any]:
    score = safe_float(robustness.get("score"), 0.0) or 0.0
    independent = bool(robustness.get("independently_positive"))
    stable_pct = safe_float(stability.get("positive_pct"), 0.0) or 0.0
    walk_pct = safe_float((walk_forward.get("summary") or {}).get("profitable_fold_pct"), 0.0) or 0.0

    if independent and score >= 65.0 and stable_pct >= 55.0 and walk_pct >= 50.0:
        return {
            "code": "ready_for_paper",
            "label": "READY FOR PAPER TESTING",
            "tone": "success",
            "reason": "The selected stock-specific strategy survived the current holdout, cost, walk-forward, and parameter-stability gates.",
        }
    if score >= 50.0 or (walk_pct >= 50.0 and stable_pct >= 40.0):
        return {
            "code": "promising",
            "label": "PROMISING — NEEDS MORE EVIDENCE",
            "tone": "warning",
            "reason": "Some evidence survived, but the strategy has not cleared every robustness gate.",
        }
    return {
        "code": "no_robust_strategy",
        "label": "NO ROBUST STRATEGY FOUND",
        "tone": "error",
        "reason": "The broad search found historical candidates, but the strongest candidate did not survive enough independent tests to justify paper deployment.",
    }


def run_stock_strategy_finder(
    one_minute_rows: list[dict[str, Any]],
    strategies: list[dict[str, Any]],
    symbol: str,
    *,
    profile_name: str = "Deep",
    backtest_settings: BacktestSettings | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    profile = search_profile(profile_name)
    selected, skipped = selected_strategies_for_profile(strategies, symbol, profile)
    if not selected:
        raise AppError("No machine-testable long strategy families are available for this stock yet.")

    settings = backtest_settings or BacktestSettings()
    settings.validate()
    optimizer = OptimizationSettings(
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

    if progress:
        progress(0, 1000, f"{profile.name} search: testing {len(selected)} strategy families without AI vetoes…")

    optimization = optimize_stock_timeframes(
        one_minute_rows,
        selected,
        symbol,
        settings,
        optimizer,
        timeframes=profile.timeframes,
        progress=progress,
    )

    distinct_ids = _top_distinct_strategy_ids(optimization, profile.walk_forward_family_limit)
    walk_strategies = [
        item for item in selected
        if str(item.get("id") or "") in set(distinct_ids)
    ]
    chosen_timeframe = str(optimization.get("timeframe") or "5Min")
    chosen_rows = resample_intraday_bars(one_minute_rows, chosen_timeframe, include_extended_hours=True)

    if progress:
        progress(910, 1000, f"Walk-forward: trying to disprove the top {len(walk_strategies)} family candidates…")

    walk_optimizer = replace(
        optimizer,
        max_variants_per_strategy=min(profile.max_variants_per_strategy, 140 if profile.name == "Quick" else 180),
        finalists_per_strategy=min(profile.finalists_per_strategy, 8),
    )
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
    robustness = validation_strength(optimization, walk)

    winner = optimization.get("winner") or {}
    source_id = str(winner.get("source_strategy_id") or "")
    winner_source = next((item for item in selected if str(item.get("id") or "") == source_id), None)
    if winner_source is None:
        raise AppError("The winning strategy family could not be resolved after optimization.")

    if progress:
        progress(965, 1000, "Parameter stability: perturbing the winning rules on untouched holdout data…")

    stability = parameter_stability_test(
        one_minute_rows,
        winner_source,
        optimization,
        maximum=profile.stability_variants,
    )
    verdict = _verdict(robustness, stability, walk)

    return {
        "version": "stock-strategy-finder-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": str(symbol or "").strip().upper(),
        "profile": asdict(profile),
        "search_policy": {
            "ai_may_prioritize": True,
            "ai_may_veto_valid_combinations": False,
            "deep_modes_test_all_technically_eligible_families": True,
            "diversity_scheduling": "round_robin_behavior_buckets",
            "selection_basis": "historical evidence + independent validation, not largest optimized P/L",
        },
        "strategies_considered": len(strategies),
        "strategies_tested": len(selected),
        "technical_skips": skipped,
        "estimated_work": estimate_search_work(profile, len(selected)),
        "optimization": optimization,
        "walk_forward": walk,
        "robustness": robustness,
        "parameter_stability": stability,
        "verdict": verdict,
        "winner_source_strategy_id": source_id,
        "winner_strategy_name": winner.get("strategy_name"),
        "timeframe": chosen_timeframe,
        "unique_configurations_tested": int(optimization.get("unique_configurations_tested") or 0),
        "configuration_history": list(optimization.get("configuration_history") or []),
    }


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


def merge_finder_report_into_library(data: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Persist search summary + exact compact configuration ledger."""
    result = dict(data or {})
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
        "verdict": report.get("verdict") or {},
        "winner_strategy_name": report.get("winner_strategy_name"),
        "winner_source_strategy_id": report.get("winner_source_strategy_id"),
        "timeframe": report.get("timeframe"),
        "unique_configurations_tested": report.get("unique_configurations_tested"),
        "strategies_tested": report.get("strategies_tested"),
        "robustness": report.get("robustness") or {},
        "parameter_stability": report.get("parameter_stability") or {},
        "walk_forward_summary": (report.get("walk_forward") or {}).get("summary") or {},
        "training_metrics": winner.get("training_metrics") or {},
        "validation_metrics": winner.get("validation_metrics") or {},
        "holdout_metrics": winner.get("holdout_metrics") or {},
        "stress_metrics": winner.get("stress_metrics") or {},
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
    return result
