"""Bounded authenticated smoke for the current autonomous validation pipeline.

This is intentionally manual-only through GitHub Actions. It uses a tiny fixed
real-data universe, writes only to a temporary local StrategyStore, and never
prints credentials or raw provider payloads. The smoke proves that the current
validation method can execute its real provider -> discovery -> optimization ->
adaptive walk-forward -> profitable-neighborhood -> static counterfactual ->
cross-stock -> fail-closed gate -> persistence path without weakening any gate.
"""

from __future__ import annotations

from datetime import timedelta
import os
from tempfile import TemporaryDirectory
from typing import Any

from trading_auto_research import (
    AUTONOMOUS_VALIDATION_METHOD_VERSION,
    _automatic_backtest_settings,
    _automatic_optimization_settings,
    _backtest_settings_from_dict,
    _global_validation_gate,
    completed_research_session_cutoff,
    invalidate_legacy_autonomous_validations,
    merge_autonomous_research_into_library,
    rank_historical_opportunities,
)
from trading_universe_research import cross_stock_generalization
from trading_validation_core import validation_strength, walk_forward_validate
from youtube_strategy_engine import (
    AlpacaMarketData,
    StrategyStore,
    optimize_stock_strategies,
    safe_float,
    utc_now,
)


SYMBOLS = ["AAPL", "MSFT", "NVDA"]


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _safe_error(exc: BaseException) -> str:
    message = " ".join(str(exc).split())[:1200]
    for name in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
        secret = _env(name)
        if secret:
            message = message.replace(secret, "<redacted>")
    return message


def _require_credentials() -> None:
    missing = [
        name
        for name in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")
        if not _env(name)
    ]
    if missing:
        raise RuntimeError(
            "Missing required Actions secret name(s): " + ", ".join(missing)
        )


def _row_value(row: dict[str, Any], short: str, long: str) -> Any:
    return row.get(short) if short in row else row.get(long)


def _validate_provider_rows(
    rows_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    minimum_rows: int,
) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for symbol in SYMBOLS:
        rows = list(rows_by_symbol.get(symbol) or [])
        if len(rows) < minimum_rows:
            raise RuntimeError(
                f"{symbol} returned only {len(rows)} rows; expected at least {minimum_rows}."
            )
        first = rows[0]
        last = rows[-1]
        required_pairs = (
            ("t", "timestamp"),
            ("o", "open"),
            ("h", "high"),
            ("l", "low"),
            ("c", "close"),
            ("v", "volume"),
        )
        if any(_row_value(first, short, long) is None for short, long in required_pairs):
            raise RuntimeError(
                f"{symbol} provider bars do not match the expected OHLCV timestamp schema."
            )
        first_timestamp = str(_row_value(first, "t", "timestamp") or "")
        last_timestamp = str(_row_value(last, "t", "timestamp") or "")
        if not first_timestamp or not last_timestamp or first_timestamp >= last_timestamp:
            raise RuntimeError(
                f"{symbol} provider timestamps are missing or not increasing."
            )
        summary[symbol] = {
            "rows": len(rows),
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
        }
    return summary


def _sessions(rows: list[dict[str, Any]]) -> list[str]:
    values: set[str] = set()
    for row in rows:
        raw = _row_value(row, "t", "timestamp")
        text = str(raw or "").strip()
        if text:
            values.add(text[:10])
    return sorted(values)


def _candidate() -> dict[str, Any]:
    return {
        "id": "authenticated-validation-smoke-current",
        "name": "Authenticated current-pipeline smoke candidate",
        "direction": "long",
        "backtest_supported": True,
        "machine_rules": {
            "min_price": 1.0,
            "max_price": 10_000.0,
            "min_relative_volume": 0.5,
        },
        "candidate_rule_options": {
            "min_relative_volume": [0.5, 0.75, 1.0],
        },
        "validation_status": "unvalidated",
    }


def _verify_walk_forward_contract(walk: dict[str, Any]) -> dict[str, Any]:
    folds = list(walk.get("folds") or [])
    if len(folds) < 3:
        raise RuntimeError(
            f"Walk-forward produced {len(folds)} folds; current autonomous validation requires at least three."
        )

    previous_external_end = ""
    for fold in folds:
        history_end = str(fold.get("history_end") or "")
        embargo_start = str(fold.get("embargo_start") or "")
        embargo_end = str(fold.get("embargo_end") or "")
        external_start = str(fold.get("external_test_start") or "")
        external_end = str(fold.get("external_test_end") or "")
        if not history_end or not external_start or not external_end:
            raise RuntimeError("Walk-forward runtime evidence is missing a fold boundary.")
        if embargo_start:
            if not (history_end < embargo_start <= embargo_end < external_start):
                raise RuntimeError(
                    "Walk-forward runtime evidence violates the history/embargo/unseen boundary."
                )
        elif history_end >= external_start:
            raise RuntimeError(
                "Walk-forward runtime evidence lets training cross into an unseen fold."
            )
        if previous_external_end and previous_external_end >= external_start:
            raise RuntimeError("Walk-forward unseen folds overlap.")
        previous_external_end = external_end

        neighborhood = fold.get("profitable_neighborhood")
        if not isinstance(neighborhood, dict):
            raise RuntimeError(
                "A walk-forward fold did not emit profitable-neighborhood evidence."
            )

    adaptive = walk.get("adaptive_learning") or {}
    static = walk.get("static_baseline") or {}
    comparison = walk.get("comparison") or {}
    if not bool(adaptive.get("enabled")):
        raise RuntimeError("Adaptive walk-forward learning was not enabled.")
    if not bool(static.get("enabled")):
        raise RuntimeError("Frozen-static counterfactual was not enabled.")
    if not bool(comparison.get("enabled")):
        raise RuntimeError("Adaptive/static comparison did not run.")
    if str(comparison.get("verdict") or "NOT RUN") == "NOT RUN":
        raise RuntimeError("Adaptive/static comparison produced no verdict.")

    return {
        "fold_count": len(folds),
        "broad_neighborhood_folds": int(
            (walk.get("summary") or {}).get(
                "broad_profitable_neighborhood_fold_count"
            )
            or 0
        ),
        "incomplete_neighborhood_folds": int(
            (walk.get("summary") or {}).get(
                "incomplete_neighborhood_fold_count"
            )
            or 0
        ),
        "comparison_verdict": comparison.get("verdict"),
    }


def run_smoke() -> dict[str, Any]:
    _require_credentials()
    market = AlpacaMarketData(
        _env("ALPACA_API_KEY"),
        _env("ALPACA_SECRET_KEY"),
        _env("ALPACA_LIVE_FEED", "iex"),
        _env("ALPACA_HISTORICAL_FEED", "sip"),
    )

    # Freeze before the current New York session so the smoke never depends on a
    # partially completed trading day.
    end = completed_research_session_cutoff(utc_now())
    daily = market.bars(
        SYMBOLS,
        start=end - timedelta(days=180),
        end=end,
        timeframe="1Day",
        max_pages=2,
    )
    daily_summary = _validate_provider_rows(daily, minimum_rows=70)

    candidate = _candidate()
    discovery = rank_historical_opportunities(
        daily,
        candidate,
        limit=len(SYMBOLS),
    )
    if len(discovery) < 2:
        raise RuntimeError(
            f"Real daily data produced only {len(discovery)} discovery candidates; expected at least two."
        )
    discovered_symbols = [
        str(item.get("symbol") or "").strip().upper()
        for item in discovery
        if str(item.get("symbol") or "").strip()
    ]
    anchor = discovered_symbols[0]

    intraday = market.bars(
        SYMBOLS,
        start=end - timedelta(days=45),
        end=end,
        timeframe="5Min",
        max_pages=10,
    )
    intraday_summary = _validate_provider_rows(intraday, minimum_rows=500)
    anchor_rows = list(intraday.get(anchor) or [])

    settings = _automatic_backtest_settings(candidate)
    optimizer = _automatic_optimization_settings()
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
        raise RuntimeError("Optimization completed without a winner record.")

    walk = walk_forward_validate(
        anchor_rows,
        [candidate],
        anchor,
        settings,
        optimizer,
        minimum_history_sessions=8,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        max_folds=3,
        adaptive_learning=True,
        compare_static_baseline=True,
        max_neighborhood_candidates=4,
    )
    walk_contract = _verify_walk_forward_contract(walk)

    strength = validation_strength(optimization, walk)
    frozen = {
        **candidate,
        "validation_status": "validated",
        "validated_rules": winner.get("optimized_rules") or {},
    }
    optimized_settings = winner.get("optimized_backtest_settings") or {}
    cross_settings = (
        _backtest_settings_from_dict(optimized_settings)
        if optimized_settings
        else settings
    )
    cross_rows = {
        symbol: list(intraday.get(symbol) or [])
        for symbol in SYMBOLS
        if symbol != anchor
    }
    generalization = cross_stock_generalization(
        cross_rows,
        frozen,
        cross_settings,
    )

    validation_status, gate_reasons = _global_validation_gate(
        anchor_report=optimization,
        strength=strength,
        generalization=generalization,
        walk_forward=walk,
        broad_universe=False,
    )
    if validation_status != "research_only":
        raise RuntimeError(
            "The bounded smoke universe was incorrectly promoted instead of failing closed."
        )
    if not any("selection bias" in str(reason).lower() for reason in gate_reasons):
        raise RuntimeError("The bounded-universe selection-bias guard was not surfaced.")

    missing_status, missing_reasons = _global_validation_gate(
        anchor_report=optimization,
        strength=strength,
        generalization=generalization,
        walk_forward=None,
        broad_universe=True,
        walk_forward_error="authenticated-smoke-sentinel",
    )
    if missing_status != "research_only":
        raise RuntimeError("Missing walk-forward evidence did not fail closed.")
    if not any(
        "authenticated-smoke-sentinel" in str(reason)
        for reason in missing_reasons
    ):
        raise RuntimeError("The upstream walk-forward failure detail was not preserved.")

    validation_sessions_by_symbol = {
        symbol: _sessions(list(intraday.get(symbol) or []))
        for symbol in SYMBOLS
    }
    result = {
        "strategy_id": candidate["id"],
        "strategy_name": candidate["name"],
        "anchor_symbol": anchor,
        "candidate_symbols": SYMBOLS,
        "usable_candidate_symbols": SYMBOLS,
        "validation_sessions_by_symbol": validation_sessions_by_symbol,
        "optimization_report": optimization,
        "walk_forward": walk,
        "walk_forward_error": None,
        "strength": strength,
        "generalization": generalization,
        "global_score": round(
            (safe_float(strength.get("score"), 0.0) or 0.0) * 0.65
            + (
                safe_float(
                    (generalization.get("summary") or {}).get("score"),
                    0.0,
                )
                or 0.0
            )
            * 0.35,
            1,
        ),
        "validation_status": validation_status,
        "gate_reasons": gate_reasons,
        "retryable": False,
    }
    report = {
        "validation_method_version": AUTONOMOUS_VALIDATION_METHOD_VERSION,
        "generated_at": utc_now().isoformat(),
        "timeframe": "5Min",
        "intraday_lookback_days": 45,
        "daily_lookback_days": 180,
        "event_window_days": 45,
        "point_in_time_horizon_years": round(180 / 365.0, 2),
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
        "limitations": [
            "Bounded authenticated smoke; this run is deliberately not broad enough for promotion."
        ],
    }

    with TemporaryDirectory(prefix="trading-validation-smoke-") as directory:
        store = StrategyStore(directory=directory)
        library = StrategyStore.blank()
        library["strategies"] = [candidate]
        merged = merge_autonomous_research_into_library(library, report)
        store.save(merged)
        reloaded = store.load_latest()
        persisted = next(
            item
            for item in reloaded.get("strategies") or []
            if item.get("id") == candidate["id"]
        )
        if persisted.get("validation_status") != validation_status:
            raise RuntimeError("Persisted validation status changed during reload.")
        last = persisted.get("last_autonomous_research") or {}
        if int(last.get("validation_method_version") or 0) != int(
            AUTONOMOUS_VALIDATION_METHOD_VERSION
        ):
            raise RuntimeError("Persistence lost the current validation method version.")
        if len(reloaded.get("validation_runs") or []) != 1:
            raise RuntimeError(
                "Validation persistence did not retain exactly one smoke record."
            )
        _, invalidated = invalidate_legacy_autonomous_validations(reloaded)
        if invalidated:
            raise RuntimeError(
                "Reload incorrectly treated the current smoke result as legacy validation."
            )

    return {
        "provider": "Alpaca",
        "validation_method_version": AUTONOMOUS_VALIDATION_METHOD_VERSION,
        "symbols": SYMBOLS,
        "daily_rows": {symbol: item["rows"] for symbol, item in daily_summary.items()},
        "intraday_rows": {
            symbol: item["rows"] for symbol, item in intraday_summary.items()
        },
        "discovery_candidates": len(discovery),
        "anchor": anchor,
        "optimizer_status": winner.get("status"),
        "walk_forward_folds": walk_contract["fold_count"],
        "walk_forward_trades": (walk.get("summary") or {}).get(
            "external_trade_count"
        ),
        "broad_neighborhood_folds": walk_contract["broad_neighborhood_folds"],
        "incomplete_neighborhood_folds": walk_contract[
            "incomplete_neighborhood_folds"
        ],
        "adaptive_static_verdict": walk_contract["comparison_verdict"],
        "validation_status": validation_status,
        "gate_reason_count": len(gate_reasons),
        "persistence": "pass",
        "reload_reconciliation": "pass",
    }


def main() -> int:
    try:
        summary = run_smoke()
    except Exception as exc:
        print(
            f"FAIL · authenticated validation pipeline · {_safe_error(exc)}",
            flush=True,
        )
        return 1

    print("PASS · authenticated validation pipeline", flush=True)
    for key, value in summary.items():
        print(f"{key}: {value}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
