"""Session-independent Strategy Lab execution.

The Streamlit page may be rerun or disconnected while this function continues
inside the process. It deliberately has no Streamlit calls and reports all UI
state through callbacks so Very Deep work is not owned by a browser session.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from stock_strategy_finder import (
    apply_historical_spread_integrity_guard,
    apply_holdout_reuse_guard,
    apply_paper_fidelity_to_verdict,
    finder_evidence_verdict,
    parameter_stability_test,
    record_holdout_exposure,
)
from trading_catalyst_core import (
    enrich_bars_with_point_in_time_catalysts,
    historical_news,
)
from trading_intelligence_core import paper_execution_fidelity
from trading_validation_core import validation_strength, walk_forward_validate
from youtube_strategy_engine import (
    AppError,
    BacktestSettings,
    OptimizationSettings,
    historical_entry_spread_audit,
    normalize_machine_rules,
    optimize_stock_strategies,
    safe_float,
    split_safe_raw_research_rows,
    utc_now,
)


ProgressCallback = Callable[[float, str, str], None]
OptimizerCheckpointCallback = Callable[[dict[str, Any]], None]


def _progress(
    callback: ProgressCallback | None,
    fraction: float,
    stage: str,
    message: str,
) -> None:
    if callback:
        callback(max(0.0, min(1.0, float(fraction))), str(stage), str(message))


def execute_strategy_lab_run(
    job: dict[str, Any],
    *,
    market: Any,
    main_store: Any,
    progress: ProgressCallback | None = None,
    optimizer_resume_state: dict[str, Any] | None = None,
    optimizer_checkpoint: OptimizerCheckpointCallback | None = None,
) -> dict[str, Any]:
    """Execute one complete Strategy Lab run without depending on a UI session."""

    ticker = str(job.get("ticker") or "").strip().upper()
    timeframe = str(job.get("timeframe") or "5Min").strip()
    history_days = int(job.get("history_days") or 30)
    search_depth = int(job.get("search_depth") or 36)
    candidates = [
        deepcopy(item)
        for item in job.get("candidates") or []
        if isinstance(item, dict)
    ]
    if not ticker:
        raise AppError("A stock ticker is required for Strategy Lab.")
    if timeframe not in {"1Min", "5Min", "15Min"}:
        raise AppError("Strategy Lab received an unsupported candle size.")
    if search_depth not in {12, 36, 96, 160}:
        raise AppError("Strategy Lab received an unsupported optimization depth.")
    if not candidates:
        raise AppError("No compatible strategies are available for this run.")

    raw_research_end = str(job.get("research_end") or job.get("started_at") or "").strip()
    try:
        end_time = datetime.fromisoformat(raw_research_end.replace("Z", "+00:00"))
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        end_time = end_time.astimezone(timezone.utc)
    except ValueError:
        end_time = utc_now()
    if market.historical_feed == "sip" and market.live_feed != "sip":
        end_time -= timedelta(minutes=16)
    start_time = end_time - timedelta(days=history_days)

    _progress(progress, 0.05, "history", f"Downloading {ticker} historical candles")
    rows_by_symbol = market.bars(
        [ticker],
        start=start_time,
        end=end_time,
        timeframe=timeframe,
        adjustment="raw",
        max_pages=30,
        progress=lambda page: _progress(
            progress,
            0.05 + 0.20 * min(1.0, page / 30.0),
            "history",
            f"Downloading {ticker} historical candles · page {page}",
        ),
    )
    rows = list(rows_by_symbol.get(ticker) or [])
    _progress(progress, 0.25, "history", f"Downloaded {len(rows):,} candles")
    if not rows:
        raise AppError(f"No historical {timeframe} candles were returned for {ticker}.")

    split_actions = market.research_reset_actions(
        [ticker],
        start=start_time,
        end=end_time,
    )
    rows, market_data_integrity = split_safe_raw_research_rows(
        rows,
        split_actions,
        ticker,
    )
    if not rows:
        raise AppError(f"No split-safe raw-price history remained for {ticker}.")
    if market_data_integrity.get("corporate_action_reset_detected"):
        _progress(
            progress,
            0.26,
            "integrity",
            "Corporate-action integrity guard · raw-price research restarted at "
            f"{market_data_integrity.get('latest_split_date')}",
        )

    blocked_spread_candidates = [
        item
        for item in candidates
        if normalize_machine_rules(item.get("machine_rules")).get("max_spread_pct")
        is not None
    ]
    if blocked_spread_candidates:
        if bool(job.get("compared_all")):
            blocked_ids = {str(item.get("id") or "") for item in blocked_spread_candidates}
            candidates = [
                item
                for item in candidates
                if str(item.get("id") or "") not in blocked_ids
            ]
            if not candidates:
                raise AppError(
                    "Every selected strategy requires a historical max-spread rule, "
                    "which remains fail-closed until quote history is fully integrated "
                    "as an entry filter."
                )
        else:
            raise AppError(
                "This strategy requires max_spread_pct. Strategy Lab will not validate "
                "that rule using a fixed spread/slippage proxy; use it only after "
                "point-in-time quote filtering is implemented."
            )

    catalyst_summary = None
    needs_historical_catalysts = any(
        bool(normalize_machine_rules(item.get("machine_rules")).get("catalyst_required"))
        for item in candidates
    )
    if needs_historical_catalysts:
        _progress(
            progress,
            0.27,
            "catalysts",
            "Downloading point-in-time historical catalyst news",
        )
        articles = historical_news(
            market,
            [ticker],
            start=start_time - timedelta(hours=24),
            end=end_time,
            max_pages=60,
            progress=lambda page: _progress(
                progress,
                0.27 + 0.08 * min(1.0, page / 60.0),
                "catalysts",
                f"Downloading historical catalyst news · page {page}",
            ),
        )
        rows, catalyst_summary = enrich_bars_with_point_in_time_catalysts(
            rows,
            articles,
            lookback_hours=24.0,
        )
        _progress(
            progress,
            0.35,
            "catalysts",
            "Catalyst history ready · "
            f"{catalyst_summary.get('specific_catalysts', 0)} classified events",
        )
    else:
        _progress(
            progress,
            0.35,
            "history",
            "Historical data ready · no catalyst history required",
        )

    backtest_settings = BacktestSettings(
        starting_cash=float(job.get("starting_cash") or 2000.0),
        risk_per_trade_pct=float(job.get("risk_per_trade") or 10.0),
        max_position_pct=float(job.get("max_position") or 100.0),
        train_fraction=0.70,
    )
    optimization_settings = OptimizationSettings(
        max_variants_per_strategy=search_depth,
        finalists_per_strategy=min(6, search_depth),
        minimum_training_trades=int(job.get("minimum_training_trades") or 5),
        minimum_validation_trades=int(job.get("minimum_validation_trades") or 2),
        training_fraction=float(job.get("training_fraction") or 0.60),
        validation_fraction=float(job.get("validation_fraction") or 0.20),
        stress_cost_multiplier=1.75,
        automatic_slippage=True,
        maximum_drawdown_pct=float(job.get("max_drawdown") or 15.0),
        selection_mode="validated",
    )

    _progress(progress, 0.38, "optimization", "Starting validated optimization")

    def optimizer_progress(done: int, total: int, message: str) -> None:
        _progress(
            progress,
            0.38 + 0.40 * min(1.0, done / max(1, total)),
            "optimization",
            message,
        )

    report = optimize_stock_strategies(
        rows,
        candidates,
        ticker,
        backtest_settings,
        optimization_settings,
        progress=optimizer_progress,
        finalize_holdout=True,
        resume_state=optimizer_resume_state,
        checkpoint=optimizer_checkpoint,
    )
    _progress(
        progress,
        0.78,
        "holdout",
        "Training, validation, and final holdout complete",
    )

    walk_report = None
    if bool(job.get("run_walk_forward")):
        _progress(progress, 0.80, "walk_forward", "Starting walk-forward validation")

        def walk_progress(done: int, total: int, message: str) -> None:
            _progress(
                progress,
                0.80 + 0.16 * min(1.0, done / max(1, total)),
                "walk_forward",
                message,
            )

        walk_report = walk_forward_validate(
            rows,
            candidates,
            ticker,
            backtest_settings,
            optimization_settings,
            minimum_history_sessions=int(job.get("wf_history_sessions") or 8),
            test_sessions_per_fold=int(job.get("wf_test_sessions") or 2),
            max_folds=int(job.get("wf_folds") or 3),
            progress=walk_progress,
        )
        _progress(progress, 0.96, "walk_forward", "Walk-forward validation complete")
    else:
        _progress(
            progress,
            0.96,
            "holdout",
            "Optimization and holdout validation complete",
        )

    strength = validation_strength(report, walk_report)
    winner = report.get("winner") or {}
    winner_source_id = str(winner.get("source_strategy_id") or "")
    winner_source = next(
        (
            item
            for item in candidates
            if str(item.get("id") or "") == winner_source_id
        ),
        None,
    )
    stability_report = {}
    if walk_report and winner_source is not None:
        _progress(
            progress,
            0.97,
            "stability",
            "Testing nearby parameter stability on untouched holdout",
        )
        stability_report = parameter_stability_test(
            rows,
            winner_source,
            report,
            maximum=min(24, max(12, search_depth // 4)),
        )

    winner = report.get("winner") or {}
    optimized_settings_for_spread = winner.get("optimized_backtest_settings") or {}
    sensitivity_multipliers = [
        safe_float(value)
        for value in (
            (report.get("optimization_settings") or {}).get(
                "execution_sensitivity_multipliers"
            )
            or (1.25, 1.5, 1.75, 2.0)
        )
    ]
    _progress(
        progress,
        0.98,
        "spread_audit",
        "Checking historical entry spreads on untouched holdout",
    )
    spread_audit = historical_entry_spread_audit(
        market,
        ticker,
        list((report.get("winning_backtest") or {}).get("trades") or []),
        list(report.get("holdout_sessions") or []),
        modeled_spread_bps=(
            safe_float(optimized_settings_for_spread.get("spread_bps"), 12.0) or 12.0
        ),
        maximum_stress_multiplier=max(
            [value for value in sensitivity_multipliers if value is not None] or [2.0]
        ),
    )
    integrity_wrapper = apply_historical_spread_integrity_guard(
        {
            "symbol": ticker,
            "timeframe": timeframe,
            "optimization": report,
            "robustness": strength,
        },
        spread_audit,
    )
    _progress(progress, 0.99, "saving", "Saving holdout exposure and final result")
    commit_exposure = getattr(main_store, "commit_holdout_exposure", None)
    if callable(commit_exposure):
        # Cloud workers re-evaluate the guard on each fresh CAS snapshot. Never
        # replay an entire stale library or carry a prior eligibility verdict.
        integrity_wrapper = commit_exposure(
            integrity_wrapper, generated_at=str(report.get("generated_at") or "")
        )
    else:
        current_integrity_library = main_store.load_latest()
        integrity_wrapper = apply_holdout_reuse_guard(
            current_integrity_library, integrity_wrapper,
        )
        exposure_library = record_holdout_exposure(
            current_integrity_library, integrity_wrapper,
            source="manual_strategy_lab",
            generated_at=str(report.get("generated_at") or ""),
        )
        main_store.save(exposure_library)

    report = integrity_wrapper.get("optimization") or report
    strength = integrity_wrapper.get("robustness") or strength
    winner = report.get("winner") or winner
    holdout_reuse_audit = integrity_wrapper.get("holdout_reuse_audit") or {}
    evidence_verdict = finder_evidence_verdict(
        strength,
        stability_report,
        walk_report or {},
        report,
    )
    paper_fidelity = {}
    if winner_source is not None:
        paper_fidelity = paper_execution_fidelity(
            {
                **winner_source,
                "validation_status": "research_only",
                "validated_rules": None,
                "machine_rules": (
                    winner.get("optimized_rules")
                    or winner_source.get("machine_rules")
                    or {}
                ),
            }
        )
    evidence_verdict = apply_paper_fidelity_to_verdict(
        evidence_verdict,
        paper_fidelity,
    )

    result = {
        "ticker": ticker,
        "timeframe": timeframe,
        "history_days": history_days,
        "report": report,
        "walk_forward": walk_report,
        "strength": strength,
        "parameter_stability": stability_report,
        "evidence_verdict": evidence_verdict,
        "paper_execution_fidelity": paper_fidelity,
        "historical_spread_audit": spread_audit,
        "holdout_reuse_audit": holdout_reuse_audit,
        "market_data_integrity": market_data_integrity,
        "compared_all": bool(job.get("compared_all")),
        "catalyst_summary": catalyst_summary,
    }
    _progress(progress, 1.0, "complete", "Optimization + validation complete")
    return result
