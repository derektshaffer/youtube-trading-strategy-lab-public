"""Bounded authenticated smoke for the real autonomous validation path.

This is intentionally manual-only through GitHub Actions. It performs two
batched Alpaca history requests, writes only to temporary local job storage,
and never prints or persists credentials or raw provider payloads.
"""

from __future__ import annotations

from datetime import timedelta
import os
from tempfile import TemporaryDirectory
from typing import Any

from stock_strategy_finder import parameter_stability_test
from trading_auto_research import (
    AUTO_PARAMETER_STABILITY_VARIANTS,
    CURRENT_AUTONOMOUS_VALIDATION_VERSION,
    _automatic_backtest_settings,
    _automatic_optimization_settings,
    _global_validation_gate,
    effective_autonomous_validation_status,
    merge_autonomous_research_into_library,
    rank_historical_opportunities,
    reconcile_autonomous_validation_statuses,
)
from trading_universe_research import cross_stock_generalization
from trading_validation_core import (
    adaptive_vs_static_compare,
    validation_strength,
    walk_forward_validate,
)
from youtube_strategy_engine import (
    AlpacaMarketData,
    AppError,
    BacktestSettings,
    StrategyStore,
    safe_float,
    utc_now,
)


SYMBOLS = ["AAPL", "MSFT", "NVDA"]


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _safe_error(exc: BaseException) -> str:
    message = str(exc).replace("\n", " ")[:800]
    for name in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
        secret = _env(name)
        if secret:
            message = message.replace(secret, "<redacted>")
    return message


def _require_credentials() -> None:
    missing = [name for name in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY") if not _env(name)]
    if missing:
        raise RuntimeError("Missing required Actions secret name(s): " + ", ".join(missing))


def _validate_provider_rows(
    rows_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    minimum_rows: int,
) -> dict[str, dict[str, Any]]:
    required = {"t", "o", "h", "l", "c", "v"}
    summary: dict[str, dict[str, Any]] = {}
    for symbol in SYMBOLS:
        rows = list(rows_by_symbol.get(symbol) or [])
        if len(rows) < minimum_rows:
            raise RuntimeError(f"{symbol} returned only {len(rows)} rows; expected at least {minimum_rows}.")
        if not required.issubset(rows[0]) or not required.issubset(rows[-1]):
            raise RuntimeError(f"{symbol} provider bars do not match the expected OHLCV timestamp schema.")
        first_timestamp = str(rows[0].get("t") or "")
        last_timestamp = str(rows[-1].get("t") or "")
        if not first_timestamp or not last_timestamp or first_timestamp >= last_timestamp:
            raise RuntimeError(f"{symbol} provider timestamps are missing or not increasing.")
        summary[symbol] = {
            "rows": len(rows),
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
        }
    return summary


def _candidate() -> dict[str, Any]:
    return {
        "id": "authenticated-validation-smoke-v1",
        "name": "Authenticated validation smoke candidate",
        "direction": "long",
        "backtest_supported": True,
        "machine_rules": {
            "min_price": 1.0,
            "max_price": 10_000.0,
        },
        "validation_status": "unvalidated",
    }


def run_smoke() -> dict[str, Any]:
    _require_credentials()
    market = AlpacaMarketData(
        _env("ALPACA_API_KEY"),
        _env("ALPACA_SECRET_KEY"),
        _env("ALPACA_LIVE_FEED", "iex"),
        _env("ALPACA_HISTORICAL_FEED", "sip"),
    )
    end = utc_now() - timedelta(minutes=20)
    daily = market.bars(
        SYMBOLS,
        start=end - timedelta(days=90),
        end=end,
        timeframe="1Day",
        max_pages=2,
    )
    daily_summary = _validate_provider_rows(daily, minimum_rows=35)

    candidate = _candidate()
    discovery = rank_historical_opportunities(daily, candidate, limit=len(SYMBOLS))
    if not discovery:
        raise RuntimeError("Real daily data produced no valid discovery inputs.")
    discovered_symbols = [str(item.get("symbol") or "") for item in discovery]
    if not all(symbol in discovered_symbols for symbol in SYMBOLS):
        raise RuntimeError("Candidate discovery did not preserve the bounded smoke universe.")

    intraday = market.bars(
        SYMBOLS,
        start=end - timedelta(days=45),
        end=end,
        timeframe="5Min",
        max_pages=4,
    )
    intraday_summary = _validate_provider_rows(intraday, minimum_rows=500)
    anchor = discovered_symbols[0]
    anchor_rows = list(intraday.get(anchor) or [])
    settings = _automatic_backtest_settings(candidate)
    optimizer = _automatic_optimization_settings()

    from youtube_strategy_engine import optimize_stock_strategies

    optimization = optimize_stock_strategies(
        anchor_rows,
        [candidate],
        anchor,
        settings,
        optimizer,
        finalize_holdout=True,
    )
    winner = optimization.get("winner") or {}
    if not winner:
        raise RuntimeError("Optimization completed without a candidate winner record.")

    walk = walk_forward_validate(
        anchor_rows,
        [candidate],
        anchor,
        settings,
        optimizer,
        minimum_history_sessions=8,
        test_sessions_per_fold=2,
        max_folds=2,
    )
    folds = list(walk.get("folds") or [])
    if len(folds) != 2:
        raise RuntimeError(f"Walk-forward produced {len(folds)} folds instead of the required two.")

    neighborhood = parameter_stability_test(
        anchor_rows,
        candidate,
        optimization,
        maximum=AUTO_PARAMETER_STABILITY_VARIANTS,
    )
    comparison = adaptive_vs_static_compare(
        anchor_rows,
        [candidate],
        anchor,
        settings,
        optimizer,
        adaptive_report=walk,
    )

    seen_external_sessions: set[str] = set()
    for fold in comparison.get("folds") or []:
        external_sessions = [str(value) for value in fold.get("external_test_sessions") or []]
        history_end = str(fold.get("adaptive_history_end") or "")
        if not external_sessions or not history_end or history_end >= min(external_sessions):
            raise RuntimeError("Adaptive/static runtime evidence contains an invalid fold boundary.")
        if seen_external_sessions.intersection(external_sessions):
            raise RuntimeError("Adaptive/static runtime evidence contains overlapping unseen folds.")
        seen_external_sessions.update(external_sessions)

    strength = validation_strength(optimization, walk)
    frozen = {
        **candidate,
        "validation_status": "validated",
        "validated_rules": winner.get("optimized_rules") or {},
    }
    optimized_settings = winner.get("optimized_backtest_settings") or {}
    cross_settings = BacktestSettings(**optimized_settings) if optimized_settings else settings
    generalization = cross_stock_generalization(intraday, frozen, cross_settings)
    validation_status, gate_reasons = _global_validation_gate(
        anchor_report=optimization,
        strength=strength,
        generalization=generalization,
        walk_forward=walk,
        broad_universe=False,
        parameter_stability=neighborhood,
        adaptive_static_comparison=comparison,
    )
    if validation_status != "research_only":
        raise RuntimeError("The bounded non-broad smoke universe was incorrectly promoted.")
    if not any("selection bias" in str(reason).lower() for reason in gate_reasons):
        raise RuntimeError("The bounded-universe fail-closed reason was not surfaced.")

    missing_status, missing_reasons = _global_validation_gate(
        anchor_report=optimization,
        strength=strength,
        generalization=generalization,
        walk_forward=walk,
        broad_universe=False,
        parameter_stability=None,
        adaptive_static_comparison=None,
    )
    if missing_status != "research_only":
        raise RuntimeError("Missing robustness/comparison evidence did not fail closed.")
    if not any("neighborhood" in str(reason).lower() for reason in missing_reasons):
        raise RuntimeError("Missing neighborhood evidence was not reported.")
    if not any("adaptive" in str(reason).lower() for reason in missing_reasons):
        raise RuntimeError("Missing adaptive/static evidence was not reported.")

    result = {
        "strategy_id": candidate["id"],
        "strategy_name": candidate["name"],
        "anchor_symbol": anchor,
        "candidate_symbols": SYMBOLS,
        "optimization_report": optimization,
        "walk_forward": walk,
        "parameter_stability": neighborhood,
        "adaptive_static_comparison": comparison,
        "validation_version": CURRENT_AUTONOMOUS_VALIDATION_VERSION,
        "strength": strength,
        "generalization": generalization,
        "global_score": round(
            (safe_float(strength.get("score"), 0.0) or 0.0) * 0.65
            + (safe_float((generalization.get("summary") or {}).get("score"), 0.0) or 0.0) * 0.35,
            1,
        ),
        "validation_status": validation_status,
        "gate_reasons": gate_reasons,
        "promotion_gate_passed": False,
    }
    report = {
        "validation_version": CURRENT_AUTONOMOUS_VALIDATION_VERSION,
        "generated_at": utc_now().isoformat(),
        "timeframe": "5Min",
        "intraday_lookback_days": 45,
        "daily_lookback_days": 90,
        "event_window_days": 45,
        "point_in_time_horizon_years": round(90 / 365.0, 2),
        "universe": {
            "source": "bounded_authenticated_smoke",
            "symbols": SYMBOLS,
            "point_in_time_capable": False,
        },
        "eligible_strategies": 1,
        "strategies_with_opportunities": 1,
        "deep_strategies_attempted": 1,
        "deep_strategies_tested": 1,
        "deep_strategies_failed": 0,
        "failed_finalists": [],
        "run_status": "complete",
        "results": [result],
        "limitations": ["Bounded authenticated smoke; not a broad research run."],
    }

    with TemporaryDirectory(prefix="trading-validation-smoke-") as directory:
        store = StrategyStore(directory=directory)
        library = StrategyStore.blank()
        library["strategies"] = [candidate]
        merged = merge_autonomous_research_into_library(library, report)
        store.save(merged)
        reloaded = store.load_latest()
        reconciled, changed = reconcile_autonomous_validation_statuses(reloaded)
        persisted = next(
            item for item in reconciled.get("strategies") or [] if item.get("id") == candidate["id"]
        )
        effective_status, evidence = effective_autonomous_validation_status(
            persisted.get("last_autonomous_research") or {}
        )
        if changed:
            raise RuntimeError("Reload unexpectedly changed a current smoke validation record.")
        if persisted.get("validation_status") != validation_status:
            raise RuntimeError("Persisted validation status changed during reload.")
        if effective_status != validation_status:
            raise RuntimeError("Reloaded effective validation status disagrees with the gate result.")
        if evidence.get("validation_version") != CURRENT_AUTONOMOUS_VALIDATION_VERSION:
            raise RuntimeError("Reloaded smoke evidence lost its current validation version.")
        if len(reconciled.get("validation_runs") or []) != 1:
            raise RuntimeError("Validation persistence did not retain exactly one smoke record.")

    return {
        "provider": "Alpaca",
        "symbols": SYMBOLS,
        "daily": daily_summary,
        "intraday": intraday_summary,
        "discovery_candidates": len(discovery),
        "anchor": anchor,
        "optimizer_status": winner.get("status"),
        "walk_forward_folds": len(folds),
        "walk_forward_trades": (walk.get("summary") or {}).get("external_trade_count"),
        "neighborhood_status": neighborhood.get("status"),
        "neighborhood_tested": neighborhood.get("tested"),
        "neighborhood_positive_pct": neighborhood.get("positive_pct"),
        "adaptive_static_status": comparison.get("status"),
        "adaptive_static_decision": comparison.get("decision"),
        "adaptive_static_evidence_valid": comparison.get("evidence_valid"),
        "validation_status": validation_status,
        "gate_reason_count": len(gate_reasons),
        "persistence": "pass",
        "reload_reconciliation": "pass",
    }


def main() -> int:
    try:
        summary = run_smoke()
    except Exception as exc:
        print(f"FAIL · authenticated validation pipeline · {_safe_error(exc)}", flush=True)
        return 1
    print("PASS · authenticated validation pipeline", flush=True)
    for key, value in summary.items():
        print(f"{key}: {value}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
