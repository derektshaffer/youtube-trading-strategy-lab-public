"""Stable reconstruction helpers for saved Stock Strategy Finder reports."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def finder_summary_to_report(summary: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the compact UI report saved after a completed Finder run."""
    winner = {
        "status": summary.get("optimizer_status"),
        "optimized_rules": summary.get("optimized_rules") or {},
        "optimized_backtest_settings": summary.get("optimized_backtest_settings") or {},
        "training_metrics": summary.get("training_metrics") or {},
        "validation_metrics": summary.get("validation_metrics") or {},
        "holdout_metrics": summary.get("holdout_metrics") or {},
        "stress_metrics": summary.get("stress_metrics") or {},
        "execution_sensitivity": summary.get("execution_sensitivity") or {},
        "holdout_execution_sensitivity": summary.get("holdout_execution_sensitivity") or {},
    }
    return {
        "version": "stock-strategy-finder-v1-restored",
        "run_id": summary.get("id"),
        "generated_at": summary.get("generated_at"),
        "symbol": str(summary.get("symbol") or "").upper(),
        "profile": summary.get("profile_details") or {"name": summary.get("profile")},
        "strategy_fidelity_engine_version": int(summary.get("strategy_fidelity_engine_version") or 0),
        "search_policy": summary.get("search_policy") or {},
        "strategies_considered": summary.get("strategies_considered"),
        "strategies_tested": summary.get("strategies_tested"),
        "tested_strategy_rankings": list(summary.get("tested_strategy_rankings") or []),
        "technical_skips": summary.get("technical_skips") or [],
        "estimated_work": summary.get("estimated_work") or {},
        "stage_timings_seconds": summary.get("stage_timings_seconds") or {},
        "parallel_workers": int(summary.get("parallel_workers") or 1),
        "parallelized_by": summary.get("parallelized_by") or "none",
        "distributed": summary.get("distributed") or {},
        "optimization": {
            "winner": winner,
            "holdout_sessions": list(summary.get("holdout_sessions") or []),
        },
        "walk_forward": {"summary": summary.get("walk_forward_summary") or {}},
        "robustness": summary.get("robustness") or {},
        "parameter_stability": summary.get("parameter_stability") or {},
        "regime_diagnostics": summary.get("regime_diagnostics") or {},
        "paper_execution_fidelity": summary.get("paper_execution_fidelity") or {},
        "historical_spread_audit": summary.get("historical_spread_audit") or {},
        "market_data_integrity": summary.get("market_data_integrity") or {},
        "holdout_reuse_audit": summary.get("holdout_reuse_audit") or {},
        "verdict": summary.get("verdict") or {},
        "winner_source_strategy_id": summary.get("winner_source_strategy_id"),
        "stock_specific_strategy_id": summary.get("stock_specific_strategy_id"),
        "paper_validation_status": summary.get("paper_validation_status"),
        "winner_strategy_name": summary.get("winner_strategy_name"),
        "timeframe": summary.get("timeframe"),
        "unique_configurations_tested": int(summary.get("unique_configurations_tested") or 0),
        "restored_from_library": True,
    }


def latest_completed_finder_report(
    data: dict[str, Any],
    symbol: str,
    profile_name: str | None = None,
) -> dict[str, Any]:
    target_symbol = str(symbol or "").strip().upper()
    target_profile = str(profile_name or "").strip()
    for summary in data.get("stock_strategy_finder_runs") or []:
        if not isinstance(summary, dict):
            continue
        if str(summary.get("symbol") or "").strip().upper() != target_symbol:
            continue
        if target_profile and str(summary.get("profile") or "").strip() != target_profile:
            continue
        return finder_summary_to_report(summary)
    return {}


def newest_matching_finder_report(
    session_report: dict[str, Any] | None,
    saved_report: dict[str, Any] | None,
    symbol: str,
    profile_name: str,
) -> dict[str, Any]:
    """Choose the newest exact saved/session result for the current controls."""
    target_symbol = str(symbol or "").strip().upper()
    target_profile = str(profile_name or "").strip()
    candidates: list[tuple[datetime, int, dict[str, Any]]] = []
    for saved_priority, raw in enumerate((session_report, saved_report)):
        report = dict(raw or {})
        if str(report.get("symbol") or "").strip().upper() != target_symbol:
            continue
        if str((report.get("profile") or {}).get("name") or "").strip() != target_profile:
            continue
        raw_generated_at = str(report.get("generated_at") or "").strip()
        try:
            generated_at = datetime.fromisoformat(raw_generated_at.replace("Z", "+00:00"))
            if generated_at.tzinfo is None:
                generated_at = generated_at.replace(tzinfo=timezone.utc)
            generated_at = generated_at.astimezone(timezone.utc)
        except (TypeError, ValueError):
            generated_at = datetime.min.replace(tzinfo=timezone.utc)
        # Prefer the durable report on an exact timestamp tie.
        candidates.append((generated_at, saved_priority, report))
    if not candidates:
        return {}
    return max(candidates, key=lambda item: (item[0], item[1]))[2]