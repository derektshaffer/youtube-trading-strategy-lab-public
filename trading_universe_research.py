"""Cross-stock generalization research for Trading Intelligence Lab."""

from __future__ import annotations

from statistics import mean, median
from typing import Any

from trading_intelligence_core import effective_strategy_for_live
from youtube_strategy_engine import BacktestSettings, AppError, run_backtest, safe_float


def _pf_value(metrics: dict[str, Any]) -> float | None:
    value = safe_float(metrics.get("profit_factor"))
    return value if value is not None and value >= 0 else None


def cross_stock_generalization(
    rows_by_symbol: dict[str, list[dict[str, Any]]],
    strategy: dict[str, Any],
    settings: BacktestSettings | None = None,
) -> dict[str, Any]:
    """Run one frozen strategy unchanged across multiple symbols."""
    chosen_settings = settings or BacktestSettings()
    chosen_settings.validate()
    effective = effective_strategy_for_live(strategy)

    results: list[dict[str, Any]] = []
    for raw_symbol, rows in rows_by_symbol.items():
        symbol = str(raw_symbol or "").strip().upper()
        if not symbol or not rows:
            continue
        result = run_backtest(rows, effective, symbol, chosen_settings)
        metrics = result.get("metrics") or {}
        results.append(
            {
                "symbol": symbol,
                "metrics": metrics,
                "historical_catalyst_filter_applied": bool(
                    result.get("historical_catalyst_filter_applied")
                ),
                "limitations": result.get("limitations") or [],
            }
        )

    if not results:
        raise AppError("No cross-stock backtests could be completed.")

    active = [
        item for item in results
        if int(safe_float((item.get("metrics") or {}).get("trade_count"), 0) or 0) > 0
    ]
    profitable = [
        item for item in active
        if (safe_float((item.get("metrics") or {}).get("net_pnl"), 0.0) or 0.0) > 0
    ]
    returns = [
        safe_float((item.get("metrics") or {}).get("return_pct"), 0.0) or 0.0
        for item in active
    ]
    pfs = [
        value
        for item in active
        if (value := _pf_value(item.get("metrics") or {})) is not None
    ]
    total_trades = sum(
        int(safe_float((item.get("metrics") or {}).get("trade_count"), 0) or 0)
        for item in active
    )
    profitable_pct = len(profitable) / len(active) * 100.0 if active else 0.0
    coverage_pct = len(active) / len(results) * 100.0 if results else 0.0
    worst_drawdown = max(
        [
            safe_float((item.get("metrics") or {}).get("max_drawdown_pct"), 0.0) or 0.0
            for item in active
        ]
        or [0.0]
    )

    # Generalization score rewards breadth and profitable cross-symbol behavior.
    trade_coverage = min(1.0, total_trades / max(10.0, len(results) * 3.0))
    breadth = coverage_pct / 100.0
    profitability = profitable_pct / 100.0
    median_return = median(returns) if returns else 0.0
    return_component = max(0.0, min(1.0, median_return / 5.0)) if median_return > 0 else 0.0
    median_pf = median(pfs) if pfs else 0.0
    pf_component = max(0.0, min(1.0, median_pf / 1.5))
    score = round(
        25.0 * breadth
        + 30.0 * profitability
        + 15.0 * trade_coverage
        + 15.0 * return_component
        + 15.0 * pf_component,
        1,
    )
    if score >= 80:
        label = "BROAD"
    elif score >= 65:
        label = "PROMISING"
    elif score >= 50:
        label = "MIXED"
    else:
        label = "NARROW / WEAK"

    results.sort(
        key=lambda item: (
            (safe_float((item.get("metrics") or {}).get("net_pnl"), 0.0) or 0.0) > 0,
            safe_float((item.get("metrics") or {}).get("return_pct"), 0.0) or 0.0,
            safe_float((item.get("metrics") or {}).get("profit_factor"), 0.0) or 0.0,
        ),
        reverse=True,
    )

    warnings: list[str] = []
    if coverage_pct < 60.0:
        warnings.append(
            "The strategy rarely triggered across the selected stocks, so cross-stock evidence is sparse."
        )
    if profitable_pct < 50.0:
        warnings.append("The strategy was profitable on fewer than half of the stocks where it traded.")
    if len(results) < 5:
        warnings.append("Use at least five reasonably different stocks before treating this as generalization evidence.")

    return {
        "strategy_id": effective.get("id"),
        "strategy_name": effective.get("name"),
        "using_validated_rules": bool(effective.get("using_validated_rules")),
        "symbols_tested": len(results),
        "results": results,
        "summary": {
            "score": score,
            "label": label,
            "active_symbols": len(active),
            "profitable_symbols": len(profitable),
            "profitable_symbol_pct": round(profitable_pct, 1),
            "coverage_pct": round(coverage_pct, 1),
            "total_trades": total_trades,
            "median_return_pct": round(median_return, 3) if returns else 0.0,
            "average_return_pct": round(mean(returns), 3) if returns else 0.0,
            "median_profit_factor": round(median_pf, 3) if pfs else None,
            "worst_symbol_drawdown_pct": round(worst_drawdown, 2),
        },
        "warnings": warnings,
        "note": (
            "Each symbol is simulated independently with the same frozen rules and starting capital. "
            "This tests portability, not portfolio-level simultaneous execution."
        ),
    }
