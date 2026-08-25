from pathlib import Path

engine_path = Path("youtube_strategy_engine.py")
engine = engine_path.read_text(encoding="utf-8")

# Historical deep search: only a few rule finalists get the broad sizing grid.
engine = engine.replace(
    'sizing_finalist_count = min(len(rule_candidates), min(6, optimizer.finalists_per_strategy))',
    'sizing_finalist_count = min(len(rule_candidates), min(3, optimizer.finalists_per_strategy))',
    1,
)
engine = engine.replace(
    'generate_execution_variants(settings, maximum=optimizer.max_execution_variants_per_finalist)',
    'generate_execution_variants(settings, maximum=min(16, optimizer.max_execution_variants_per_finalist))',
    1,
)

# Replace automatic historical timeframe optimization with a true funnel:
# 1) cheap baseline strategy screening on 5Min,
# 2) cheap interval screening using only top 2 strategies,
# 3) full adaptive optimization on winning interval + top 2 strategies.
start = engine.index('def _optimize_stock_timeframes_historical(')
end = engine.index('\ndef optimize_stock_strategies(', start)
new_function = r'''def _screen_historical_strategies(
    rows: list[dict[str, Any]],
    strategies: list[dict[str, Any]],
    symbol: str,
    settings: BacktestSettings,
    maximum_drawdown_pct: float,
) -> list[dict[str, Any]]:
    """Cheaply rank saved strategies before expensive adaptive optimization.

    Each strategy gets the same small stop/target sweep. This is intentionally only a
    screening stage; the winners are fully optimized afterward.
    """
    frame = bars_to_frame(rows)
    if frame.empty:
        return []
    candidates: list[dict[str, Any]] = []
    stop_grid = [2.0, 4.0, 5.0, 7.5, 10.0]
    reward_grid = [1.0, 1.5, 2.0, 3.0]
    for strategy in strategies:
        if not isinstance(strategy, dict) or not strategy.get("id"):
            continue
        if str(strategy.get("direction", "long")).lower() not in {"long", "both"}:
            continue
        target = str(strategy.get("optimized_for_symbol") or "").strip().upper()
        if target and target != symbol:
            continue
        original = normalize_machine_rules(strategy.get("machine_rules"))
        baseline_stop = safe_float(original.get("stop_loss_pct"), settings.default_stop_pct) or settings.default_stop_pct
        baseline_reward = safe_float(original.get("reward_risk"), settings.default_reward_risk) or settings.default_reward_risk
        stops = list(dict.fromkeys([round(float(baseline_stop), 4), *stop_grid]))
        rewards = list(dict.fromkeys([round(float(baseline_reward), 4), *reward_grid]))
        best: dict[str, Any] | None = None
        indicator_cache: dict[tuple[int, int], pd.DataFrame] = {}
        for stop in stops:
            for reward in rewards:
                rules = normalize_machine_rules({**original, "stop_loss_pct": stop, "reward_risk": reward})
                candidate_strategy = {**strategy, "machine_rules": rules}
                key = (
                    int(rules.get("breakout_lookback_bars") or 20),
                    int(rules.get("opening_range_minutes") or 15),
                )
                if key not in indicator_cache:
                    indicator_cache[key] = add_indicators(frame, candidate_strategy)
                candidate_settings = replace(
                    settings,
                    default_stop_pct=float(stop),
                    default_reward_risk=float(reward),
                )
                result = run_backtest(
                    [], candidate_strategy, symbol, candidate_settings,
                    prepared_indicators=indicator_cache[key],
                )
                metrics = result.get("metrics") or {}
                record = {
                    "strategy": strategy,
                    "strategy_id": strategy.get("id"),
                    "strategy_name": strategy.get("name") or "Unnamed strategy",
                    "metrics": metrics,
                    "rules": rules,
                    "settings": candidate_settings,
                }
                if best is None or _historical_metric_key(metrics, maximum_drawdown_pct) > _historical_metric_key(best["metrics"], maximum_drawdown_pct):
                    best = record
        if best is not None:
            candidates.append(best)
    candidates.sort(
        key=lambda item: _historical_metric_key(item["metrics"], maximum_drawdown_pct),
        reverse=True,
    )
    return candidates


def _optimize_stock_timeframes_historical(
    one_minute_rows: list[dict[str, Any]],
    strategies: list[dict[str, Any]],
    symbol: str,
    backtest_settings: BacktestSettings | None,
    optimization_settings: OptimizationSettings,
    *,
    timeframes: tuple[str, ...],
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Use a runtime-safe funnel before the expensive adaptive search."""
    requested = list(dict.fromkeys(str(item) for item in timeframes))
    if not requested or any(item not in {"1Min", "5Min", "15Min"} for item in requested):
        raise AppError("Select one or more supported candle intervals: 1Min, 5Min, or 15Min.")
    settings = backtest_settings or BacktestSettings()
    settings.validate()
    optimization_settings.validate()
    target_symbols = parse_symbols(symbol)
    if len(target_symbols) != 1:
        raise AppError("Enter exactly one valid stock ticker to optimize.")
    target_symbol = target_symbols[0]

    # Stage 1: screen all saved strategies on 5-minute candles with a tiny, equal grid.
    if progress:
        progress(20, 1000, f"Screening {len(strategies)} saved strategies…")
    five_minute_rows = resample_intraday_bars(one_minute_rows, "5Min")
    strategy_screen = _screen_historical_strategies(
        five_minute_rows,
        strategies,
        target_symbol,
        settings,
        optimization_settings.maximum_drawdown_pct,
    )
    if not strategy_screen:
        raise AppError("No saved long strategy produced a screenable historical result for this stock.")
    top_strategy_records = strategy_screen[:min(2, len(strategy_screen))]
    top_strategies = [item["strategy"] for item in top_strategy_records]
    screened_names = [item["strategy_name"] for item in top_strategy_records]
    if progress:
        progress(180, 1000, "Top strategy candidates: " + ", ".join(screened_names))

    # Stage 2: compare candle intervals using only the top two strategies and a small
    # optimizer budget. This keeps interval selection representative but cheap.
    screening_optimizer = replace(
        optimization_settings,
        max_variants_per_strategy=min(24, optimization_settings.max_variants_per_strategy),
        finalists_per_strategy=min(2, optimization_settings.finalists_per_strategy),
        max_execution_variants_per_finalist=min(4, optimization_settings.max_execution_variants_per_finalist),
    )
    screening_optimizer.validate()
    screened_intervals: list[tuple[str, list[dict[str, Any]], dict[str, Any]]] = []
    for index, interval in enumerate(requested):
        interval_rows = resample_intraday_bars(one_minute_rows, interval)
        if progress:
            progress(200 + index * 80, 1000, f"Comparing {interval} candles…")
        report = _optimize_stock_strategies_historical(
            interval_rows,
            top_strategies,
            target_symbol,
            settings,
            screening_optimizer,
            progress=None,
        )
        report["timeframe"] = interval
        for candidate in report.get("rankings") or []:
            candidate["timeframe"] = interval
        if report.get("winner"):
            report["winner"]["timeframe"] = interval
        screened_intervals.append((interval, interval_rows, report))

    screened_intervals.sort(
        key=lambda item: _historical_metric_key(
            (item[2].get("winner") or {}).get("full_metrics") or {},
            optimization_settings.maximum_drawdown_pct,
        ),
        reverse=True,
    )
    chosen_interval, chosen_rows, _ = screened_intervals[0]
    if progress:
        progress(460, 1000, f"Best preliminary interval: {chosen_interval}. Starting deep adaptive search…")

    # Stage 3: full adaptive search, but only for the two strategies that survived the
    # initial fair screening. This is where fine values such as a 9.5% stop are found.
    def deep_progress(completed: int, total: int, message: str) -> None:
        if progress:
            fraction = min(1.0, completed / max(total, 1))
            progress(460 + int(fraction * 520), 1000, f"Deep {chosen_interval}: {message}")

    deep_report = _optimize_stock_strategies_historical(
        chosen_rows,
        top_strategies,
        target_symbol,
        settings,
        optimization_settings,
        progress=deep_progress,
    )
    if not deep_report.get("rankings") or not deep_report.get("winner"):
        raise AppError("The deep optimizer finished without a winning result.")
    deep_report["timeframe"] = chosen_interval
    deep_report["timeframes_tested"] = requested
    deep_report["strategies_screened"] = len(strategy_screen)
    deep_report["strategies_deep_optimized"] = len(top_strategies)
    deep_report["strategy_screening"] = [
        {
            "strategy_name": item["strategy_name"],
            "net_pnl": safe_float(item["metrics"].get("net_pnl"), 0.0) or 0.0,
            "return_pct": safe_float(item["metrics"].get("return_pct"), 0.0) or 0.0,
            "max_drawdown_pct": safe_float(item["metrics"].get("max_drawdown_pct"), 0.0) or 0.0,
        }
        for item in strategy_screen
    ]
    for candidate in deep_report.get("rankings") or []:
        candidate["timeframe"] = chosen_interval
    deep_report["winner"]["timeframe"] = chosen_interval
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
        for interval, _, report in screened_intervals
    ]
    screening_variants = sum(int(report.get("variants_tested") or 0) for _, _, report in screened_intervals)
    deep_report["screening_variants_tested"] = screening_variants
    deep_report["deep_variants_tested"] = int(deep_report.get("variants_tested") or 0)
    deep_report["variants_tested"] = screening_variants + int(deep_report.get("variants_tested") or 0)
    deep_report["warnings"] = list(dict.fromkeys([
        f"Runtime-safe funnel screened {len(strategy_screen)} saved strategies, advanced {len(top_strategies)} to deep optimization, "
        f"screened all requested candle intervals, then ran the full adaptive search on {chosen_interval}.",
        *(deep_report.get("warnings") or []),
    ]))
    if progress:
        progress(985, 1000, "Deep optimizer finished; preparing results…")
    return deep_report
'''
engine = engine[:start] + new_function + engine[end:]
engine_path.write_text(engine, encoding="utf-8")

app_path = Path("youtube_strategy_app.py")
app = app_path.read_text(encoding="utf-8")
app = app.replace(
    '"Test each stock\'s strategy, candle size, stop, target, risk per trade, and position size on earlier sessions. "\n        "Choose settings using separate validation data, then inspect one untouched final holdout.",',
    '"Screen all saved strategies first, then deeply optimize the strongest candidates across candle size, stop, target, risk per trade, and position size. "\n        "Use Maximum historical P/L for best-fit research or Validated edge for a stricter robustness check.",',
    1,
)
app = app.replace(
    'help="Automatic comparison quickly screens 1-, 5-, and 15-minute candles, then runs the full adaptive optimization only on the strongest interval. This is much faster and more reliable on long histories.",',
    'help="Automatic comparison first screens all saved strategies, keeps the strongest two, compares 1-, 5-, and 15-minute candles, then runs the full adaptive optimizer only on the best interval. This prevents long scans from exhausting Streamlit Cloud.",',
    1,
)
app_path.write_text(app, encoding="utf-8")
