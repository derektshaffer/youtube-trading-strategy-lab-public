from pathlib import Path

engine_path = Path("youtube_strategy_engine.py")
engine = engine_path.read_text(encoding="utf-8")

# Limit expensive sizing sweeps to the strongest few rule candidates. The previous
# adaptive version could run 64 sizing variants across 24 finalists per strategy.
old_finalists = '''        finalists = rule_candidates[:min(len(rule_candidates), optimizer.finalists_per_strategy)]
        baseline = rule_candidates[0] if rule_candidates else None
'''
new_finalists = '''        sizing_finalist_count = min(len(rule_candidates), min(6, optimizer.finalists_per_strategy))
        finalists = rule_candidates[:sizing_finalist_count]
        baseline = rule_candidates[0] if rule_candidates else None
'''
if new_finalists not in engine:
    if old_finalists not in engine:
        raise SystemExit("Could not find historical finalist block")
    engine = engine.replace(old_finalists, new_finalists, 1)

# Replace historical automatic-timeframe comparison. First screen all requested
# intervals with a bounded quick search; then deep-optimize only the strongest interval.
start = engine.index("def _optimize_stock_timeframes_historical(")
end = engine.index("\ndef optimize_stock_strategies(", start)
new_function = r'''def _optimize_stock_timeframes_historical(
    one_minute_rows: list[dict[str, Any]],
    strategies: list[dict[str, Any]],
    symbol: str,
    backtest_settings: BacktestSettings | None,
    optimization_settings: OptimizationSettings,
    *,
    timeframes: tuple[str, ...],
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Screen candle sizes cheaply, then deep-optimize only the best interval.

    Running a comprehensive adaptive optimizer independently on 1-, 5-, and 15-minute
    candles can require tens of thousands of full backtests. This two-stage approach
    keeps the adaptive search while avoiding Streamlit Cloud time/memory exhaustion.
    """
    requested = list(dict.fromkeys(str(item) for item in timeframes))
    if not requested or any(item not in {"1Min", "5Min", "15Min"} for item in requested):
        raise AppError("Select one or more supported candle intervals: 1Min, 5Min, or 15Min.")

    settings = backtest_settings or BacktestSettings()
    settings.validate()
    optimization_settings.validate()

    # The screening pass is intentionally bounded. It still uses the adaptive engine,
    # but with a small enough search to compare candle sizes without exhausting runtime.
    screening_optimizer = replace(
        optimization_settings,
        max_variants_per_strategy=min(48, optimization_settings.max_variants_per_strategy),
        finalists_per_strategy=min(4, optimization_settings.finalists_per_strategy),
        max_execution_variants_per_finalist=min(8, optimization_settings.max_execution_variants_per_finalist),
    )
    screening_optimizer.validate()

    screened: list[tuple[str, list[dict[str, Any]], dict[str, Any]]] = []
    screening_weight = 45
    interval_weight = screening_weight / max(1, len(requested))

    for interval_index, interval in enumerate(requested):
        interval_rows = resample_intraday_bars(one_minute_rows, interval)

        def screen_progress(completed: int, total: int, message: str) -> None:
            if progress:
                fraction = min(1.0, completed / max(total, 1))
                overall = int((interval_index * interval_weight + fraction * interval_weight) * 10)
                progress(overall, 1000, f"Screening {interval}: {message}")

        report = _optimize_stock_strategies_historical(
            interval_rows,
            strategies,
            symbol,
            settings,
            screening_optimizer,
            progress=screen_progress,
        )
        report["timeframe"] = interval
        report["timeframes_tested"] = [interval]
        for candidate in report.get("rankings") or []:
            candidate["timeframe"] = interval
        if report.get("winner"):
            report["winner"]["timeframe"] = interval
        screened.append((interval, interval_rows, report))

    if not screened:
        raise AppError("No candle interval produced an optimization result.")

    screened.sort(
        key=lambda item: _historical_metric_key(
            (item[2].get("winner") or {}).get("full_metrics") or {},
            optimization_settings.maximum_drawdown_pct,
        ),
        reverse=True,
    )
    chosen_interval, chosen_rows, best_screen = screened[0]

    if progress:
        progress(450, 1000, f"Best preliminary candle size: {chosen_interval}. Starting deep adaptive search…")

    def deep_progress(completed: int, total: int, message: str) -> None:
        if progress:
            fraction = min(1.0, completed / max(total, 1))
            progress(450 + int(fraction * 540), 1000, f"Deep {chosen_interval} search: {message}")

    deep_report = _optimize_stock_strategies_historical(
        chosen_rows,
        strategies,
        symbol,
        settings,
        optimization_settings,
        progress=deep_progress,
    )
    deep_report["timeframe"] = chosen_interval
    deep_report["timeframes_tested"] = requested
    for candidate in deep_report.get("rankings") or []:
        candidate["timeframe"] = chosen_interval
    if deep_report.get("winner"):
        deep_report["winner"]["timeframe"] = chosen_interval

    screening_variants = sum(int(report.get("variants_tested") or 0) for _, _, report in screened)
    screening_adaptive = sum(int(report.get("adaptive_refinement_tests") or 0) for _, _, report in screened)
    deep_variants = int(deep_report.get("variants_tested") or 0)
    deep_adaptive = int(deep_report.get("adaptive_refinement_tests") or 0)

    deep_report["screening_variants_tested"] = screening_variants
    deep_report["deep_variants_tested"] = deep_variants
    deep_report["variants_tested"] = screening_variants + deep_variants
    deep_report["screening_adaptive_refinements"] = screening_adaptive
    deep_report["adaptive_refinement_tests"] = screening_adaptive + deep_adaptive
    deep_report["timeframe_comparison"] = [
        {
            "timeframe": interval,
            "strategy_name": (report.get("winner") or {}).get("strategy_name"),
            "validation_metrics": (report.get("winner") or {}).get("full_metrics") or {},
            "full_metrics": (report.get("winner") or {}).get("full_metrics") or {},
            "score": (report.get("winner") or {}).get("score"),
            "status": (report.get("winner") or {}).get("status"),
            "variants_tested": report.get("variants_tested", 0),
            "screening_only": interval != chosen_interval,
        }
        for interval, _, report in screened
    ]
    deep_report["warnings"] = list(dict.fromkeys([
        *(
            "Automatic candle comparison used a bounded screening pass for 1-, 5-, and 15-minute candles, "
            f"then ran the full adaptive search only on {chosen_interval}."
        ,),
        *(deep_report.get("warnings") or []),
    ]))

    if progress:
        progress(990, 1000, f"Deep optimization complete for {chosen_interval}; preparing results…")
    return deep_report
'''
engine = engine[:start] + new_function + engine[end:]
engine_path.write_text(engine, encoding="utf-8")

app_path = Path("youtube_strategy_app.py")
app = app_path.read_text(encoding="utf-8")
old_help = 'help="Automatic comparison downloads one-minute candles once, then builds the other intervals locally.",'
new_help = 'help="Automatic comparison quickly screens 1-, 5-, and 15-minute candles, then runs the full adaptive optimization only on the strongest interval. This is much faster and more reliable on long histories.",'
if old_help in app:
    app = app.replace(old_help, new_help, 1)

old_caption = '''            if optimizer_goal.startswith("Maximum historical"):
                st.caption("Historical-P/L mode searches the whole window and can overfit. Use Validated edge afterward as a robustness check.")
'''
new_caption = '''            if optimizer_goal.startswith("Maximum historical"):
                st.caption("Historical-P/L mode searches the whole window and can overfit. Use Validated edge afterward as a robustness check. Automatic candle comparison screens all intervals first, then deeply optimizes only the strongest one.")
'''
if old_caption in app:
    app = app.replace(old_caption, new_caption, 1)

app_path.write_text(app, encoding="utf-8")
