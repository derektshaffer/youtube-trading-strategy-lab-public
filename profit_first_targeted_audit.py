"""Run one read-only current-protocol Profit First revalidation.

This audit intentionally does not mutate the durable Trading Intelligence library.
It uses the same autonomous research method and persistence guard calculations, then
writes only a compact JSON report for CI/artifact inspection.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any

from stock_strategy_finder import holdout_reuse_audit
from trading_auto_research import (
    AUTONOMOUS_VALIDATION_METHOD_VERSION,
    run_autonomous_research,
)
from trading_intelligence_core import research_readiness, strategy_integrity_report
from youtube_strategy_engine import AlpacaMarketData, AppError, normalize_machine_rules, safe_float


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _without_float_language(values: Any) -> Any:
    """Remove float-specific source clauses only for an explicitly derived hypothesis."""
    if isinstance(values, list):
        return [
            value
            for value in values
            if "float" not in str(value or "").casefold()
        ]
    if isinstance(values, str) and "float" in values.casefold():
        return ""
    return values


def build_float_agnostic_derivative(strategy: dict[str, Any]) -> dict[str, Any]:
    """Create a separately named research hypothesis; never mutate or relabel the source strategy."""
    item = deepcopy(strategy)
    parent_id = str(strategy.get("id") or "")
    item["id"] = f"{parent_id}-float-agnostic-v1"
    item["name"] = (
        str(strategy.get("name") or "Strategy")
        + " — Float-Agnostic Research Variant"
    )
    item["source_type"] = "derived_research_hypothesis"
    item["derived_from_strategy_id"] = parent_id
    item.pop("parent_strategy_id", None)
    item["validation_status"] = "unvalidated"
    item["optimization_status"] = "not_run"
    item.pop("validated_rules", None)
    item.pop("validated_backtest_settings", None)
    item.pop("validated_at", None)

    for field in (
        "summary",
        "stock_selection",
        "entry_conditions",
        "avoid_conditions",
        "market_context",
        "unresolved_rules",
    ):
        if field in item:
            item[field] = _without_float_language(item.get(field))

    overrides = dict(item.get("research_rule_overrides") or {})
    # These values are explicit research assumptions, not claims about the author.
    # 0.5% matches the historical engine's former implicit EMA-touch default.
    # 0.25% creates a genuinely-below-EMA stop seed that the optimizer may vary.
    overrides.setdefault("pullback_touch_tolerance_pct", 0.5)
    overrides.setdefault("stop_ema_buffer_pct", 0.25)
    item["research_rule_overrides"] = overrides

    options = dict(item.get("ai_candidate_rule_options") or {})
    options["pullback_touch_tolerance_pct"] = [0.1, 0.25, 0.5, 0.75]
    options["stop_ema_buffer_pct"] = [0.05, 0.1, 0.25, 0.5]
    item["ai_candidate_rule_options"] = options
    item["derived_hypothesis_changes"] = [
        {
            "requirement": "Historical float filter",
            "change": "excluded",
            "reason": (
                "Test whether the EMA pullback structure has edge independent of "
                "the source's low-float universe requirement."
            ),
        },
        {
            "requirement": "EMA pullback tolerance",
            "change": "research_assumption",
            "seed": 0.5,
        },
        {
            "requirement": "EMA structural-stop buffer",
            "change": "research_assumption",
            "seed": 0.25,
        },
    ]
    return item


def _compact_result(
    library: dict[str, Any],
    requested: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    results = [item for item in report.get("results") or [] if isinstance(item, dict)]
    result = results[0] if results else {}
    optimization = result.get("optimization_report") if isinstance(result.get("optimization_report"), dict) else {}
    winner = optimization.get("winner") if isinstance(optimization.get("winner"), dict) else {}
    strength = result.get("strength") if isinstance(result.get("strength"), dict) else {}
    generalization = result.get("generalization") if isinstance(result.get("generalization"), dict) else {}
    walk = result.get("walk_forward") if isinstance(result.get("walk_forward"), dict) else {}

    reuse = (
        holdout_reuse_audit(
            library,
            {
                "symbol": result.get("anchor_symbol"),
                "timeframe": report.get("timeframe"),
                "optimization": optimization,
                "generated_at": report.get("generated_at"),
            },
        )
        if result
        else {}
    )
    gate_reasons = list(result.get("gate_reasons") or [])
    effective_status = str(result.get("validation_status") or "research_only")
    if effective_status == "validated" and not reuse.get("pristine", True):
        effective_status = "research_only"
        gate_reasons.append(
            "Autonomous final holdout overlaps outcomes exposed by an earlier research cycle."
        )

    return {
        "read_only": True,
        "requested_strategy_id": requested.get("id"),
        "requested_strategy_name": requested.get("name"),
        "requested_parent_strategy_id": requested.get("parent_strategy_id"),
        "requested_source_type": requested.get("source_type"),
        "requested_readiness": research_readiness(requested),
        "generated_at": report.get("generated_at"),
        "validation_method_version": int(
            report.get("validation_method_version") or AUTONOMOUS_VALIDATION_METHOD_VERSION
        ),
        "run_status": report.get("run_status"),
        "sampling_boundary": report.get("sampling_boundary") or {},
        "timeframe": report.get("timeframe"),
        "universe_source": (report.get("universe") or {}).get("source"),
        "universe_point_in_time_capable": bool(
            (report.get("universe") or {}).get("point_in_time_capable")
        ),
        "eligible_strategies": report.get("eligible_strategies"),
        "strategies_with_opportunities": report.get("strategies_with_opportunities"),
        "deep_strategies_attempted": report.get("deep_strategies_attempted"),
        "deep_strategies_tested": report.get("deep_strategies_tested"),
        "deep_strategies_failed": report.get("deep_strategies_failed"),
        "failed_finalists": report.get("failed_finalists") or [],
        "timing_profile": report.get("timing_profile") or {},
        "tested_strategy_id": result.get("strategy_id"),
        "tested_strategy_name": result.get("strategy_name"),
        "anchor_symbol": result.get("anchor_symbol"),
        "candidate_symbols": result.get("candidate_symbols") or [],
        "pre_persistence_validation_status": result.get("validation_status"),
        "validation_status": effective_status,
        "gate_reasons": gate_reasons,
        "global_score": result.get("global_score"),
        "optimizer_status": winner.get("status"),
        "robustness_score": strength.get("score"),
        "robustness_label": strength.get("label"),
        "independently_positive": strength.get("independently_positive"),
        "training_metrics": winner.get("training_metrics") or {},
        "validation_metrics": winner.get("validation_metrics") or {},
        "holdout_metrics": winner.get("holdout_metrics") or {},
        "stress_metrics": winner.get("stress_metrics") or {},
        "execution_sensitivity": winner.get("execution_sensitivity") or {},
        "holdout_execution_sensitivity": winner.get("holdout_execution_sensitivity") or {},
        "walk_forward_summary": walk.get("summary") or {},
        "generalization_summary": generalization.get("summary") or {},
        "holdout_reuse_audit": reuse,
        "strict_profit_edge": bool(
            effective_status == "validated"
            and int(safe_float((winner.get("validation_metrics") or {}).get("trade_count"), 0) or 0) > 0
            and (safe_float((winner.get("validation_metrics") or {}).get("net_pnl"), 0.0) or 0.0) > 0
            and int(safe_float((winner.get("holdout_metrics") or {}).get("trade_count"), 0) or 0) > 0
            and (safe_float((winner.get("holdout_metrics") or {}).get("net_pnl"), 0.0) or 0.0) > 0
            and int(safe_float((winner.get("stress_metrics") or {}).get("trade_count"), 0) or 0) > 0
            and (safe_float((winner.get("stress_metrics") or {}).get("net_pnl"), 0.0) or 0.0) > 0
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("library")
    parser.add_argument("output")
    parser.add_argument(
        "--strategy-id",
        default=_env("PROFIT_FIRST_TARGET_STRATEGY_ID", "til-e375e878fadbe87a841b"),
    )
    parser.add_argument(
        "--universe-size",
        type=int,
        default=int(_env("PROFIT_FIRST_TARGET_UNIVERSE_SIZE", "250") or 250),
    )
    parser.add_argument(
        "--symbols-per-strategy",
        type=int,
        default=int(_env("PROFIT_FIRST_TARGET_SYMBOLS_PER_STRATEGY", "6") or 6),
    )
    args = parser.parse_args()

    library = json.loads(Path(args.library).read_text(encoding="utf-8"))
    requested = next(
        (
            item
            for item in library.get("strategies") or []
            if isinstance(item, dict) and str(item.get("id") or "") == args.strategy_id
        ),
        None,
    )
    if requested is None:
        raise AppError(f"Target strategy {args.strategy_id} was not found in the durable library.")

    readiness = research_readiness(requested)
    use_derivative = _env(
        "PROFIT_FIRST_FLOAT_AGNOSTIC_DERIVATIVE",
        "0",
    ).casefold() in {"1", "true", "yes"}
    tested_strategy = requested
    derivative_diagnostic = None
    if readiness.get("label") != "ready_for_backtest" and use_derivative:
        candidate = build_float_agnostic_derivative(requested)
        candidate_readiness = research_readiness(candidate)
        derivative_diagnostic = {
            "id": candidate.get("id"),
            "name": candidate.get("name"),
            "derived_from_strategy_id": candidate.get("derived_from_strategy_id"),
            "readiness": candidate_readiness,
            "changes": candidate.get("derived_hypothesis_changes") or [],
            "research_rule_overrides": candidate.get("research_rule_overrides") or {},
        }
        if candidate_readiness.get("label") == "ready_for_backtest":
            tested_strategy = candidate
            readiness = candidate_readiness

    if readiness.get("label") != "ready_for_backtest":
        diagnostic = {
            "read_only": True,
            "requested_strategy_id": requested.get("id"),
            "requested_strategy_name": requested.get("name"),
            "requested_parent_strategy_id": requested.get("parent_strategy_id"),
            "requested_source_type": requested.get("source_type"),
            "status": "blocked_before_revalidation",
            "blocker": "strategy_integrity",
            "readiness": readiness,
            "integrity": strategy_integrity_report(tested_strategy),
            "derivative_attempt": derivative_diagnostic,
            "machine_rules": {
                key: value
                for key, value in normalize_machine_rules(
                    requested.get("machine_rules")
                ).items()
                if value is not None
            },
            "research_rule_overrides": {
                key: value
                for key, value in normalize_machine_rules(
                    requested.get("research_rule_overrides")
                ).items()
                if value is not None
            },
            "unresolved_rules": [
                str(value)
                for value in requested.get("unresolved_rules") or []
                if str(value).strip()
            ],
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(diagnostic, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(diagnostic, indent=2, default=str))
        return 0

    market = AlpacaMarketData(
        _env("ALPACA_API_KEY"),
        _env("ALPACA_SECRET_KEY"),
        _env("ALPACA_LIVE_FEED", "iex"),
        _env("ALPACA_HISTORICAL_FEED", "sip"),
    )

    def progress(message: str) -> None:
        print(f"[profit-first-read-only] {message}", flush=True)

    report = run_autonomous_research(
        market,
        [dict(tested_strategy)],
        universe_sample_size=max(50, int(args.universe_size)),
        deep_strategy_limit=1,
        symbols_per_strategy=max(3, int(args.symbols_per_strategy)),
        parallel_workers=1,
        progress=progress,
    )
    compact = _compact_result(library, tested_strategy, report)
    compact["source_strategy_id"] = requested.get("id")
    compact["source_strategy_name"] = requested.get("name")
    compact["derived_hypothesis"] = bool(
        str(tested_strategy.get("id") or "") != str(requested.get("id") or "")
    )
    compact["derived_hypothesis_changes"] = (
        tested_strategy.get("derived_hypothesis_changes") or []
    )
    compact["research_rule_overrides"] = (
        tested_strategy.get("research_rule_overrides") or {}
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(compact, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "strategy": compact.get("tested_strategy_name"),
        "status": compact.get("validation_status"),
        "strict_profit_edge": compact.get("strict_profit_edge"),
        "anchor": compact.get("anchor_symbol"),
        "gate_reasons": compact.get("gate_reasons"),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
