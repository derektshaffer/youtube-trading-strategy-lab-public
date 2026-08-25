from pathlib import Path

engine_path = Path("youtube_strategy_engine.py")
engine = engine_path.read_text(encoding="utf-8")

old_key = '''def _historical_metric_key(metrics: dict[str, Any], maximum_drawdown_pct: float) -> tuple[Any, ...]:
    pnl = safe_float(metrics.get("net_pnl"), 0.0) or 0.0
    drawdown = safe_float(metrics.get("max_drawdown_pct"), 0.0) or 0.0
    return_pct = safe_float(metrics.get("return_pct"), 0.0) or 0.0
    profit_factor = safe_float(metrics.get("profit_factor"), -1.0)
    trades = int(safe_float(metrics.get("trade_count"), 0.0) or 0.0)
    return (
        drawdown <= maximum_drawdown_pct,
        pnl,
        return_pct,
        profit_factor if profit_factor is not None else -1.0,
        -drawdown,
        trades,
    )
'''
new_key = '''def historical_minimum_trade_count(session_count: int) -> int:
    """Require a meaningful sample without making short windows impossible.

    The gate scales at roughly 40% of trading sessions, with a floor of 3 trades and
    a cap of 10. A typical 30-calendar-day window with 22 sessions therefore requires
    9 completed trades before a result can rank as a historical best fit.
    """
    sessions = max(0, int(session_count))
    return max(3, min(10, int(math.ceil(sessions * 0.40))))


def _historical_metric_key(
    metrics: dict[str, Any],
    maximum_drawdown_pct: float,
    minimum_trades: int = 1,
) -> tuple[Any, ...]:
    pnl = safe_float(metrics.get("net_pnl"), 0.0) or 0.0
    drawdown = safe_float(metrics.get("max_drawdown_pct"), 0.0) or 0.0
    return_pct = safe_float(metrics.get("return_pct"), 0.0) or 0.0
    profit_factor = safe_float(metrics.get("profit_factor"), -1.0)
    trades = int(safe_float(metrics.get("trade_count"), 0.0) or 0.0)
    required = max(1, int(minimum_trades))
    sample_ok = trades >= required
    drawdown_ok = drawdown <= maximum_drawdown_pct
    # A qualifying sample always outranks an undersized sample, regardless of raw P/L.
    # If nothing qualifies, prefer the candidate with more observations before dollars.
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
if old_key not in engine:
    raise SystemExit("Could not find historical metric key")
engine = engine.replace(old_key, new_key, 1)

# Patch only the historical optimizer function so validated-mode ranking is untouched.
start = engine.index("def _optimize_stock_strategies_historical(")
end = engine.index("\ndef _screen_historical_strategies(", start)
block = engine[start:end]

session_anchor = '''    if not sessions:
        raise AppError("No regular-session historical candles were available for optimization.")

    warnings = [
'''
session_replacement = '''    if not sessions:
        raise AppError("No regular-session historical candles were available for optimization.")
    minimum_historical_trades = historical_minimum_trade_count(len(sessions))

    warnings = [
'''
if session_anchor not in block:
    raise SystemExit("Could not find historical session anchor")
block = block.replace(session_anchor, session_replacement, 1)

warning_anchor = '''    if len(sessions) < 8:
        warnings.append(
            f"Only {len(sessions)} trading sessions are available, so the historical optimum can be especially noisy."
        )
'''
warning_replacement = '''    warnings.append(
        f"Historical best-fit candidates must produce at least {minimum_historical_trades} completed trades "
        f"across these {len(sessions)} trading sessions. Smaller samples cannot outrank qualifying candidates."
    )
    if len(sessions) < 8:
        warnings.append(
            f"Only {len(sessions)} trading sessions are available, so the historical optimum can be especially noisy."
        )
'''
if warning_anchor not in block:
    raise SystemExit("Could not find historical warning anchor")
block = block.replace(warning_anchor, warning_replacement, 1)

block = block.replace(
    '_historical_metric_key(item["metrics"], optimizer.maximum_drawdown_pct)',
    '_historical_metric_key(item["metrics"], optimizer.maximum_drawdown_pct, minimum_historical_trades)',
)
block = block.replace(
    '_historical_metric_key(item["full_metrics"], optimizer.maximum_drawdown_pct)',
    '_historical_metric_key(item["full_metrics"], optimizer.maximum_drawdown_pct, minimum_historical_trades)',
)

status_old = '''        metrics = best["metrics"]
        pnl = safe_float(metrics.get("net_pnl"), 0.0) or 0.0
        drawdown = safe_float(metrics.get("max_drawdown_pct"), 0.0) or 0.0
        if pnl <= 0:
            status = "NO HISTORICAL PROFIT"
        elif drawdown > optimizer.maximum_drawdown_pct:
            status = "HIGH DRAWDOWN"
        else:
            status = "HISTORICAL BEST FIT"
'''
status_new = '''        metrics = best["metrics"]
        pnl = safe_float(metrics.get("net_pnl"), 0.0) or 0.0
        drawdown = safe_float(metrics.get("max_drawdown_pct"), 0.0) or 0.0
        trade_count = int(safe_float(metrics.get("trade_count"), 0.0) or 0.0)
        adequate_sample = trade_count >= minimum_historical_trades
        if not adequate_sample:
            status = "INSUFFICIENT SAMPLE"
        elif pnl <= 0:
            status = "NO HISTORICAL PROFIT"
        elif drawdown > optimizer.maximum_drawdown_pct:
            status = "HIGH DRAWDOWN"
        else:
            status = "HISTORICAL BEST FIT"
'''
if status_old not in block:
    raise SystemExit("Could not find historical status block")
block = block.replace(status_old, status_new, 1)
block = block.replace(
    '            "adequate_sample": int(safe_float(metrics.get("trade_count"), 0) or 0) >= 1,',
    '            "adequate_sample": adequate_sample,\n            "minimum_historical_trades": minimum_historical_trades,',
    1,
)

rank_anchor = '''    winner = ranked[0]
    winner_source = next(item for item in eligible if item.get("id") == winner.get("source_strategy_id"))
'''
rank_replacement = '''    winner = ranked[0]
    qualifying_candidates = [item for item in ranked if item.get("adequate_sample")]
    if not qualifying_candidates:
        warnings.append(
            f"No tested configuration reached the {minimum_historical_trades}-trade minimum. "
            "The highest-ranked result is shown for research only and is not eligible to be saved as a historical best fit."
        )
    winner_source = next(item for item in eligible if item.get("id") == winner.get("source_strategy_id"))
'''
if rank_anchor not in block:
    raise SystemExit("Could not find historical winner block")
block = block.replace(rank_anchor, rank_replacement, 1)

report_anchor = '''        "session_count": len(sessions),
        "strategies_tested": len(eligible),
'''
report_replacement = '''        "session_count": len(sessions),
        "minimum_historical_trades": minimum_historical_trades,
        "qualifying_strategy_count": len(qualifying_candidates),
        "strategies_tested": len(eligible),
'''
if report_anchor not in block:
    raise SystemExit("Could not find historical report block")
block = block.replace(report_anchor, report_replacement, 1)

engine = engine[:start] + block + engine[end:]

# Screening stage: use the same scaled sample gate so 1-2 lucky trades do not advance.
start = engine.index("def _screen_historical_strategies(")
end = engine.index("\ndef _optimize_stock_timeframes_historical(", start)
block = engine[start:end]
frame_anchor = '''    frame = bars_to_frame(rows)
    if frame.empty:
        return []
    candidates: list[dict[str, Any]] = []
'''
frame_replacement = '''    frame = bars_to_frame(rows)
    if frame.empty:
        return []
    screen_sessions = list(dict.fromkeys(frame.get("session", pd.Series(dtype=str)).tolist()))
    minimum_historical_trades = historical_minimum_trade_count(len(screen_sessions))
    candidates: list[dict[str, Any]] = []
'''
if frame_anchor not in block:
    raise SystemExit("Could not find screen frame anchor")
block = block.replace(frame_anchor, frame_replacement, 1)
block = block.replace(
    '_historical_metric_key(metrics, maximum_drawdown_pct)',
    '_historical_metric_key(metrics, maximum_drawdown_pct, minimum_historical_trades)',
)
block = block.replace(
    '_historical_metric_key(best["metrics"], maximum_drawdown_pct)',
    '_historical_metric_key(best["metrics"], maximum_drawdown_pct, minimum_historical_trades)',
)
block = block.replace(
    'key=lambda item: _historical_metric_key(item["metrics"], maximum_drawdown_pct),',
    'key=lambda item: _historical_metric_key(item["metrics"], maximum_drawdown_pct, minimum_historical_trades),',
)
engine = engine[:start] + block + engine[end:]

# Timeframe screening: respect each report's own minimum-trade threshold.
old_interval_key = '''        key=lambda item: _historical_metric_key(
            (item[2].get("winner") or {}).get("full_metrics") or {},
            optimization_settings.maximum_drawdown_pct,
        ),
'''
new_interval_key = '''        key=lambda item: _historical_metric_key(
            (item[2].get("winner") or {}).get("full_metrics") or {},
            optimization_settings.maximum_drawdown_pct,
            int(item[2].get("minimum_historical_trades") or 1),
        ),
'''
if old_interval_key not in engine:
    raise SystemExit("Could not find interval ranking key")
engine = engine.replace(old_interval_key, new_interval_key, 1)

engine_path.write_text(engine, encoding="utf-8")

# UI: make the gate obvious and prevent saving undersized historical fits.
app_path = Path("youtube_strategy_app.py")
app = app_path.read_text(encoding="utf-8")

old_trade_note = '''                (f'{int(safe_float(selection_metrics.get("trade_count"), 0) or 0)} historical trades' if historical_fit_mode else f'{int(safe_float(validation.get("trade_count"), 0) or 0)} separate validation trades'),
'''
new_trade_note = '''                (
                    f'{int(safe_float(selection_metrics.get("trade_count"), 0) or 0)} historical trades · '
                    f'{int(optimization_report.get("minimum_historical_trades") or 1)} required'
                    if historical_fit_mode else
                    f'{int(safe_float(validation.get("trade_count"), 0) or 0)} separate validation trades'
                ),
'''
if old_trade_note not in app:
    raise SystemExit("Could not find historical trade metric note")
app = app.replace(old_trade_note, new_trade_note, 1)

old_quality_note = '''                f'{optimization_report.get("session_count", 0)} trading sessions reviewed',
'''
new_quality_note = '''                (
                    f'{optimization_report.get("session_count", 0)} sessions · '
                    f'{optimization_report.get("minimum_historical_trades", 1)} trades required'
                    if historical_fit_mode else
                    f'{optimization_report.get("session_count", 0)} trading sessions reviewed'
                ),
'''
if old_quality_note not in app:
    raise SystemExit("Could not find quality note")
app = app.replace(old_quality_note, new_quality_note, 1)

button_anchor = '''            if st.button(
                f"Save optimized {optimized_symbol} strategy",
                key=f"save_optimized_{optimized_symbol}_{inspected.get('source_strategy_id', 'unknown')}",
                use_container_width=True,
            ):
'''
button_replacement = '''            save_blocked_for_sample = historical_fit_mode and not bool(inspected.get("adequate_sample"))
            if save_blocked_for_sample:
                inspected_trades = int(safe_float((inspected.get("full_metrics") or {}).get("trade_count"), 0) or 0)
                required_trades = int(inspected.get("minimum_historical_trades") or optimization_report.get("minimum_historical_trades") or 1)
                st.warning(
                    f"This historical fit has only {inspected_trades} completed trades; at least {required_trades} are required. "
                    "It can be inspected, but it cannot be saved as the optimized strategy."
                )
            if st.button(
                f"Save optimized {optimized_symbol} strategy",
                key=f"save_optimized_{optimized_symbol}_{inspected.get('source_strategy_id', 'unknown')}",
                use_container_width=True,
                disabled=save_blocked_for_sample,
            ):
'''
if button_anchor not in app:
    raise SystemExit("Could not find save optimized button")
app = app.replace(button_anchor, button_replacement, 1)
app_path.write_text(app, encoding="utf-8")
