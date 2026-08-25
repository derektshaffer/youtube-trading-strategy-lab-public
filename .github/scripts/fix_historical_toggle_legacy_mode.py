from pathlib import Path

path = Path("youtube_strategy_engine.py")
text = path.read_text(encoding="utf-8")

# Replace the historical ranking helper so None means the exact pre-filter ranking tuple.
start = text.index("def _historical_metric_key(")
end = text.index("\n\ndef _optimize_stock_strategies_historical(", start)
new_helper = '''def _historical_metric_key(
    metrics: dict[str, Any],
    maximum_drawdown_pct: float,
    minimum_trades: int | None = None,
) -> tuple[Any, ...]:
    pnl = safe_float(metrics.get("net_pnl"), 0.0) or 0.0
    drawdown = safe_float(metrics.get("max_drawdown_pct"), 0.0) or 0.0
    return_pct = safe_float(metrics.get("return_pct"), 0.0) or 0.0
    profit_factor = safe_float(metrics.get("profit_factor"), -1.0)
    trades = int(safe_float(metrics.get("trade_count"), 0.0) or 0.0)
    drawdown_ok = drawdown <= maximum_drawdown_pct

    # OFF must be byte-for-byte equivalent in ranking semantics to the historical
    # optimizer before the minimum-trade feature existed. In particular, do not add
    # a sample-size tuple component: the cheap 5-minute screening stage can have zero
    # trades even when the same strategy later produces strong 1-minute candidates.
    if minimum_trades is None:
        return (
            drawdown_ok,
            pnl,
            return_pct,
            profit_factor if profit_factor is not None else -1.0,
            -drawdown,
            trades,
        )

    required = max(1, int(minimum_trades))
    sample_ok = trades >= required
    # With the filter ON, any qualifying sample outranks an undersized sample.
    # If no candidate qualifies, prefer more observations before raw dollars.
    return (
        sample_ok,
        drawdown_ok,
        pnl if sample_ok else trades,
        return_pct if sample_ok else pnl,
        profit_factor if profit_factor is not None else -1.0,
        -drawdown,
        trades,
    )
'''
text = text[:start] + new_helper + text[end:]

# Deep historical optimizer: keep the display/report minimum separate from the
# ranking key. OFF uses None, which invokes the exact legacy ranking path.
start = text.index("def _optimize_stock_strategies_historical(")
end = text.index("\ndef _screen_historical_strategies(", start)
block = text[start:end]
anchor = '''    minimum_historical_trades = (
        int(optimizer.minimum_historical_trades)
        if optimizer.enforce_historical_minimum_trades
        else 1
    )

    warnings = [
'''
replacement = '''    minimum_historical_trades = (
        int(optimizer.minimum_historical_trades)
        if optimizer.enforce_historical_minimum_trades
        else 1
    )
    ranking_minimum_historical_trades = (
        minimum_historical_trades if optimizer.enforce_historical_minimum_trades else None
    )

    warnings = [
'''
if anchor not in block:
    raise SystemExit("Historical minimum assignment anchor not found")
block = block.replace(anchor, replacement, 1)
block = block.replace(
    "_historical_metric_key(item[\"metrics\"], optimizer.maximum_drawdown_pct, minimum_historical_trades)",
    "_historical_metric_key(item[\"metrics\"], optimizer.maximum_drawdown_pct, ranking_minimum_historical_trades)",
)
block = block.replace(
    "_historical_metric_key(item[\"full_metrics\"], optimizer.maximum_drawdown_pct, minimum_historical_trades)",
    "_historical_metric_key(item[\"full_metrics\"], optimizer.maximum_drawdown_pct, ranking_minimum_historical_trades)",
)
text = text[:start] + block + text[end:]

# Screening function: None means legacy screening. Do not coerce OFF to 1.
start = text.index("def _screen_historical_strategies(")
end = text.index("\ndef _optimize_stock_timeframes_historical(", start)
block = text[start:end]
block = block.replace(
    "    minimum_historical_trades: int = 1,\n",
    "    minimum_historical_trades: int | None = None,\n",
    1,
)
old = '''    minimum_historical_trades = max(1, int(minimum_historical_trades))
    candidates: list[dict[str, Any]] = []
'''
new = '''    ranking_minimum_historical_trades = (
        None if minimum_historical_trades is None else max(1, int(minimum_historical_trades))
    )
    candidates: list[dict[str, Any]] = []
'''
if old not in block:
    raise SystemExit("Historical screening minimum anchor not found")
block = block.replace(old, new, 1)
block = block.replace(
    "_historical_metric_key(metrics, maximum_drawdown_pct, minimum_historical_trades)",
    "_historical_metric_key(metrics, maximum_drawdown_pct, ranking_minimum_historical_trades)",
)
block = block.replace(
    "_historical_metric_key(best[\"metrics\"], maximum_drawdown_pct, minimum_historical_trades)",
    "_historical_metric_key(best[\"metrics\"], maximum_drawdown_pct, ranking_minimum_historical_trades)",
)
block = block.replace(
    "_historical_metric_key(item[\"metrics\"], maximum_drawdown_pct, minimum_historical_trades)",
    "_historical_metric_key(item[\"metrics\"], maximum_drawdown_pct, ranking_minimum_historical_trades)",
)
text = text[:start] + block + text[end:]

# Timeframe funnel: OFF passes None into the cheap screening stage and uses legacy
# ranking again when comparing preliminary candle intervals.
start = text.index("def _optimize_stock_timeframes_historical(")
block = text[start:]
old_call = '''        (
            int(optimization_settings.minimum_historical_trades)
            if optimization_settings.enforce_historical_minimum_trades
            else 1
        ),
'''
new_call = '''        (
            int(optimization_settings.minimum_historical_trades)
            if optimization_settings.enforce_historical_minimum_trades
            else None
        ),
'''
if old_call not in block:
    raise SystemExit("Historical screening call toggle anchor not found")
block = block.replace(old_call, new_call, 1)

old_interval = '''            optimization_settings.maximum_drawdown_pct,
            int(item[2].get("minimum_historical_trades") or 1),
        ),
'''
new_interval = '''            optimization_settings.maximum_drawdown_pct,
            (
                int(item[2].get("minimum_historical_trades") or 8)
                if item[2].get("historical_minimum_trades_enabled", True)
                else None
            ),
        ),
'''
if old_interval not in block:
    raise SystemExit("Historical timeframe ranking toggle anchor not found")
block = block.replace(old_interval, new_interval, 1)
text = text[:start] + block

path.write_text(text, encoding="utf-8")
