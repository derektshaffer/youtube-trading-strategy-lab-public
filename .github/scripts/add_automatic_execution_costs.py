from pathlib import Path

engine_path = Path("youtube_strategy_engine.py")
engine = engine_path.read_text(encoding="utf-8")

# Add automatic slippage toggle to optimizer settings.
old_fields = '''    stress_cost_multiplier: float = 1.5
    optimize_position_sizing: bool = True
    max_execution_variants_per_finalist: int = 7
'''
new_fields = '''    stress_cost_multiplier: float = 1.5
    optimize_position_sizing: bool = True
    automatic_slippage: bool = False
    max_execution_variants_per_finalist: int = 7
'''
if old_fields not in engine:
    raise SystemExit("OptimizationSettings field anchor not found")
engine = engine.replace(old_fields, new_fields, 1)

# Add an explainable slippage estimator before conservative_stock_costs.
anchor = '''def conservative_stock_costs(
    settings: BacktestSettings,
    snapshot: dict[str, Any] | None,
) -> tuple[BacktestSettings, float | None]:
'''
helper = '''def estimate_slippage_bps(
    rows_or_frame: list[dict[str, Any]] | pd.DataFrame,
    settings: BacktestSettings,
    rules: dict[str, Any] | None = None,
) -> tuple[float, dict[str, float]]:
    """Estimate per-fill slippage from recent liquidity, volatility, and order size.

    This is deliberately a conservative execution-cost heuristic, not an optimizer
    target. The user-entered slippage is treated as a floor so automatic mode cannot
    improve a backtest merely by assuming unrealistically perfect fills.
    """
    settings.validate()
    fallback = max(0.0, float(settings.slippage_bps))
    frame = rows_or_frame.copy() if isinstance(rows_or_frame, pd.DataFrame) else bars_to_frame(rows_or_frame)
    if frame.empty or not {"close", "high", "low", "volume"}.issubset(frame.columns):
        return fallback, {
            "estimated_slippage_bps": fallback,
            "order_notional": 0.0,
            "minute_dollar_volume": 0.0,
            "range_bps": 0.0,
            "participation_ratio": 0.0,
        }

    sample = frame.tail(5000).copy()
    for field_name in ("close", "high", "low", "volume"):
        sample[field_name] = pd.to_numeric(sample[field_name], errors="coerce")
    sample = sample.dropna(subset=["close", "high", "low", "volume"])
    sample = sample[(sample["close"] > 0) & (sample["volume"] > 0)]
    if sample.empty:
        return fallback, {
            "estimated_slippage_bps": fallback,
            "order_notional": 0.0,
            "minute_dollar_volume": 0.0,
            "range_bps": 0.0,
            "participation_ratio": 0.0,
        }

    interval_minutes = 1.0
    if "timestamp" in sample.columns:
        timestamps = pd.to_datetime(sample["timestamp"], errors="coerce", utc=True).dropna().sort_values()
        if len(timestamps) > 1:
            differences = timestamps.diff().dt.total_seconds().div(60.0)
            differences = differences[(differences >= 0.5) & (differences <= 30.0)]
            if not differences.empty:
                interval_minutes = max(1.0, float(differences.median()))

    minute_dollar_volume_series = (sample["close"] * sample["volume"]) / interval_minutes
    minute_dollar_volume_series = minute_dollar_volume_series[minute_dollar_volume_series > 0]
    minute_dollar_volume = (
        float(minute_dollar_volume_series.median()) if not minute_dollar_volume_series.empty else 0.0
    )
    range_series = ((sample["high"] - sample["low"]).clip(lower=0) / sample["close"]) * 10_000.0
    # Normalize multi-minute bars toward an approximate one-minute volatility scale.
    range_series = range_series / math.sqrt(interval_minutes)
    range_series = range_series[range_series >= 0]
    typical_range_bps = float(range_series.median()) if not range_series.empty else 0.0

    normalized_rules = normalize_machine_rules(rules or {})
    stop_pct = safe_float(normalized_rules.get("stop_loss_pct"), settings.default_stop_pct) or settings.default_stop_pct
    stop_pct = max(0.1, float(stop_pct))
    risk_budget = settings.starting_cash * settings.risk_per_trade_pct / 100.0
    risk_limited_notional = risk_budget / (stop_pct / 100.0)
    position_cap_notional = settings.starting_cash * settings.max_position_pct / 100.0
    order_notional = max(0.0, min(position_cap_notional, risk_limited_notional))

    participation_ratio = order_notional / minute_dollar_volume if minute_dollar_volume > 0 else 1.0
    participation_for_model = min(25.0, max(0.0, participation_ratio))
    liquidity_component = 18.0 * math.sqrt(participation_for_model)
    volatility_component = min(100.0, typical_range_bps * 0.06)
    modeled = 2.0 + liquidity_component + volatility_component
    estimate = round(min(200.0, max(fallback, modeled)), 2)
    return estimate, {
        "estimated_slippage_bps": estimate,
        "order_notional": round(order_notional, 2),
        "minute_dollar_volume": round(minute_dollar_volume, 2),
        "range_bps": round(typical_range_bps, 2),
        "participation_ratio": round(participation_ratio, 6),
    }


def _automatic_slippage_settings(
    frame: pd.DataFrame,
    rules: dict[str, Any],
    settings: BacktestSettings,
    enabled: bool,
) -> BacktestSettings:
    if not enabled:
        return settings
    estimated_bps, _ = estimate_slippage_bps(frame, settings, rules)
    return replace(settings, slippage_bps=estimated_bps)


''' + anchor
if anchor not in engine:
    raise SystemExit("conservative_stock_costs anchor not found")
engine = engine.replace(anchor, helper, 1)

# Historical optimizer: make every candidate's slippage depend on its own sizing/rules.
hist_start = engine.index("def _optimize_stock_strategies_historical(")
hist_end = engine.index("\ndef _screen_historical_strategies(", hist_start)
hist = engine[hist_start:hist_end]

old_eval = '''        def evaluate(candidate_rules: dict[str, Any], chosen_settings: BacktestSettings) -> dict[str, Any]:
            candidate_strategy = {**source_strategy, "machine_rules": candidate_rules}
'''
new_eval = '''        def effective_settings(candidate_rules: dict[str, Any], chosen_settings: BacktestSettings) -> BacktestSettings:
            return _automatic_slippage_settings(frame, candidate_rules, chosen_settings, optimizer.automatic_slippage)

        def evaluate(candidate_rules: dict[str, Any], chosen_settings: BacktestSettings) -> dict[str, Any]:
            candidate_strategy = {**source_strategy, "machine_rules": candidate_rules}
'''
if old_eval not in hist:
    raise SystemExit("historical evaluate anchor not found")
hist = hist.replace(old_eval, new_eval, 1)

# Apply effective settings after each candidate settings construction in historical mode.
patterns = [
'''            candidate_settings = replace(
                settings,
                default_stop_pct=float(rules.get("stop_loss_pct") or settings.default_stop_pct),
                default_reward_risk=float(rules.get("reward_risk") or settings.default_reward_risk),
            )
            result = evaluate(rules, candidate_settings)
''',
'''                candidate_settings = replace(
                    settings,
                    default_stop_pct=float(refined_rules.get("stop_loss_pct") or settings.default_stop_pct),
                    default_reward_risk=float(refined_rules.get("reward_risk") or settings.default_reward_risk),
                )
                metrics = evaluate(refined_rules, candidate_settings)["metrics"]
''',
'''                candidate_settings = replace(
                    execution,
                    default_stop_pct=float(finalist["rules"].get("stop_loss_pct") or execution.default_stop_pct),
                    default_reward_risk=float(finalist["rules"].get("reward_risk") or execution.default_reward_risk),
                )
                result = evaluate(finalist["rules"], candidate_settings)
''',
'''            candidate_settings = replace(
                local_seed["settings"],
                default_stop_pct=float(refined_rules.get("stop_loss_pct") or local_seed["settings"].default_stop_pct),
                default_reward_risk=float(refined_rules.get("reward_risk") or local_seed["settings"].default_reward_risk),
            )
            metrics = evaluate(refined_rules, candidate_settings)["metrics"]
''',
'''            candidate_settings = replace(
                execution,
                default_stop_pct=float(local_seed["rules"].get("stop_loss_pct") or execution.default_stop_pct),
                default_reward_risk=float(local_seed["rules"].get("reward_risk") or execution.default_reward_risk),
            )
            metrics = evaluate(local_seed["rules"], candidate_settings)["metrics"]
''',
]
replacements = [
patterns[0].replace('            result = evaluate(rules, candidate_settings)\n', '            candidate_settings = effective_settings(rules, candidate_settings)\n            result = evaluate(rules, candidate_settings)\n'),
patterns[1].replace('                metrics = evaluate(refined_rules, candidate_settings)["metrics"]\n', '                candidate_settings = effective_settings(refined_rules, candidate_settings)\n                metrics = evaluate(refined_rules, candidate_settings)["metrics"]\n'),
patterns[2].replace('                result = evaluate(finalist["rules"], candidate_settings)\n', '                candidate_settings = effective_settings(finalist["rules"], candidate_settings)\n                result = evaluate(finalist["rules"], candidate_settings)\n'),
patterns[3].replace('            metrics = evaluate(refined_rules, candidate_settings)["metrics"]\n', '            candidate_settings = effective_settings(refined_rules, candidate_settings)\n            metrics = evaluate(refined_rules, candidate_settings)["metrics"]\n'),
patterns[4].replace('            metrics = evaluate(local_seed["rules"], candidate_settings)["metrics"]\n', '            candidate_settings = effective_settings(local_seed["rules"], candidate_settings)\n            metrics = evaluate(local_seed["rules"], candidate_settings)["metrics"]\n'),
]
for old, new in zip(patterns, replacements):
    if old not in hist:
        raise SystemExit("historical candidate settings anchor not found")
    hist = hist.replace(old, new, 1)

# Surface the estimated winning slippage in candidate/report data.
old_candidate = '''            "optimized_backtest_settings": asdict(chosen_settings),
            "changed_backtest_settings": changed_backtest_settings,
'''
new_candidate = '''            "optimized_backtest_settings": asdict(chosen_settings),
            "automatic_slippage_enabled": bool(optimizer.automatic_slippage),
            "estimated_slippage_bps": chosen_settings.slippage_bps if optimizer.automatic_slippage else None,
            "changed_backtest_settings": changed_backtest_settings,
'''
if old_candidate not in hist:
    raise SystemExit("historical candidate output anchor not found")
hist = hist.replace(old_candidate, new_candidate, 1)
old_report = '''        "selection_mode": "historical_pnl",
        "session_count": len(sessions),
'''
new_report = '''        "selection_mode": "historical_pnl",
        "automatic_slippage_enabled": bool(optimizer.automatic_slippage),
        "session_count": len(sessions),
'''
if old_report not in hist:
    raise SystemExit("historical report output anchor not found")
hist = hist.replace(old_report, new_report, 1)
engine = engine[:hist_start] + hist + engine[hist_end:]

# Screening stage: automatic slippage must affect which strategies advance.
screen_start = engine.index("def _screen_historical_strategies(")
screen_end = engine.index("\ndef _optimize_stock_timeframes_historical(", screen_start)
screen = engine[screen_start:screen_end]
old_sig = '''    maximum_drawdown_pct: float,
    minimum_historical_trades: int | None = None,
) -> list[dict[str, Any]]:
'''
new_sig = '''    maximum_drawdown_pct: float,
    minimum_historical_trades: int | None = None,
    automatic_slippage: bool = False,
) -> list[dict[str, Any]]:
'''
if old_sig not in screen:
    raise SystemExit("screen signature anchor not found")
screen = screen.replace(old_sig, new_sig, 1)
old_screen_settings = '''                candidate_settings = replace(
                    settings,
                    default_stop_pct=float(stop),
                    default_reward_risk=float(reward),
                )
                result = run_backtest(
'''
new_screen_settings = '''                candidate_settings = replace(
                    settings,
                    default_stop_pct=float(stop),
                    default_reward_risk=float(reward),
                )
                candidate_settings = _automatic_slippage_settings(
                    frame, rules, candidate_settings, automatic_slippage
                )
                result = run_backtest(
'''
if old_screen_settings not in screen:
    raise SystemExit("screen settings anchor not found")
screen = screen.replace(old_screen_settings, new_screen_settings, 1)
engine = engine[:screen_start] + screen + engine[screen_end:]

# Pass auto-slippage into the stage-1 screening call.
call_anchor = '''        (
            int(optimization_settings.minimum_historical_trades)
            if optimization_settings.enforce_historical_minimum_trades
            else None
        ),
    )
'''
call_replacement = '''        (
            int(optimization_settings.minimum_historical_trades)
            if optimization_settings.enforce_historical_minimum_trades
            else None
        ),
        bool(optimization_settings.automatic_slippage),
    )
'''
frame_start = engine.index("def _optimize_stock_timeframes_historical(")
call_pos = engine.find(call_anchor, frame_start)
if call_pos == -1:
    raise SystemExit("timeframe screening call anchor not found")
engine = engine[:call_pos] + call_replacement + engine[call_pos + len(call_anchor):]

# Validated optimizer: estimate slippage from training data only, then keep it fixed
# into validation/holdout so execution-cost estimation itself does not peek at holdout.
val_start = engine.index("def optimize_stock_strategies(")
val_end = engine.index("\ndef optimize_stock_timeframes(", val_start)
val = engine[val_start:val_end]
old_eval = '''    def evaluate(candidate_strategy: dict[str, Any], period: str, chosen_settings: BacktestSettings) -> dict[str, Any]:
        rules = normalize_machine_rules(candidate_strategy.get("machine_rules"))
'''
new_eval = '''    def effective_settings(rules: dict[str, Any], chosen_settings: BacktestSettings) -> BacktestSettings:
        return _automatic_slippage_settings(
            frames["training"], rules, chosen_settings, optimizer.automatic_slippage
        )

    def evaluate(candidate_strategy: dict[str, Any], period: str, chosen_settings: BacktestSettings) -> dict[str, Any]:
        rules = normalize_machine_rules(candidate_strategy.get("machine_rules"))
'''
if old_eval not in val:
    raise SystemExit("validated evaluate anchor not found")
val = val.replace(old_eval, new_eval, 1)

# Reorder the initial training candidate so settings are known before running.
old_initial = '''            candidate_strategy = {**source_strategy, "machine_rules": candidate_rules}
            result = evaluate(candidate_strategy, "training", settings)
            metrics = result["metrics"]
            candidate_settings = replace(
                settings,
                default_stop_pct=float(candidate_rules.get("stop_loss_pct") or settings.default_stop_pct),
                default_reward_risk=float(candidate_rules.get("reward_risk") or settings.default_reward_risk),
            )
'''
new_initial = '''            candidate_strategy = {**source_strategy, "machine_rules": candidate_rules}
            candidate_settings = replace(
                settings,
                default_stop_pct=float(candidate_rules.get("stop_loss_pct") or settings.default_stop_pct),
                default_reward_risk=float(candidate_rules.get("reward_risk") or settings.default_reward_risk),
            )
            candidate_settings = effective_settings(candidate_rules, candidate_settings)
            result = evaluate(candidate_strategy, "training", candidate_settings)
            metrics = result["metrics"]
'''
if old_initial not in val:
    raise SystemExit("validated initial candidate anchor not found")
val = val.replace(old_initial, new_initial, 1)

val_patterns = [
'''                candidate_settings = replace(
                    settings,
                    default_stop_pct=float(refined_rules.get("stop_loss_pct") or settings.default_stop_pct),
                    default_reward_risk=float(refined_rules.get("reward_risk") or settings.default_reward_risk),
                )
                candidate_strategy = {**source_strategy, "machine_rules": refined_rules}
''',
'''                candidate_settings = replace(
                    sizing,
                    default_stop_pct=float(candidate["rules"].get("stop_loss_pct") or sizing.default_stop_pct),
                    default_reward_risk=float(candidate["rules"].get("reward_risk") or sizing.default_reward_risk),
                )
                candidate_strategy = {**source_strategy, "machine_rules": candidate["rules"]}
''',
'''            candidate_settings = replace(
                local_seed["settings"],
                default_stop_pct=float(refined_rules.get("stop_loss_pct") or local_seed["settings"].default_stop_pct),
                default_reward_risk=float(refined_rules.get("reward_risk") or local_seed["settings"].default_reward_risk),
            )
            candidate_strategy = {**source_strategy, "machine_rules": refined_rules}
''',
'''            candidate_settings = replace(
                execution,
                default_stop_pct=float(local_seed["rules"].get("stop_loss_pct") or execution.default_stop_pct),
                default_reward_risk=float(local_seed["rules"].get("reward_risk") or execution.default_reward_risk),
            )
            candidate_strategy = {**source_strategy, "machine_rules": local_seed["rules"]}
''',
]
val_replacements = [
val_patterns[0].replace('                candidate_strategy = {**source_strategy, "machine_rules": refined_rules}\n', '                candidate_settings = effective_settings(refined_rules, candidate_settings)\n                candidate_strategy = {**source_strategy, "machine_rules": refined_rules}\n'),
val_patterns[1].replace('                candidate_strategy = {**source_strategy, "machine_rules": candidate["rules"]}\n', '                candidate_settings = effective_settings(candidate["rules"], candidate_settings)\n                candidate_strategy = {**source_strategy, "machine_rules": candidate["rules"]}\n'),
val_patterns[2].replace('            candidate_strategy = {**source_strategy, "machine_rules": refined_rules}\n', '            candidate_settings = effective_settings(refined_rules, candidate_settings)\n            candidate_strategy = {**source_strategy, "machine_rules": refined_rules}\n'),
val_patterns[3].replace('            candidate_strategy = {**source_strategy, "machine_rules": local_seed["rules"]}\n', '            candidate_settings = effective_settings(local_seed["rules"], candidate_settings)\n            candidate_strategy = {**source_strategy, "machine_rules": local_seed["rules"]}\n'),
]
for old, new in zip(val_patterns, val_replacements):
    if old not in val:
        raise SystemExit("validated candidate settings anchor not found")
    val = val.replace(old, new, 1)

# Candidate/report metadata.
if old_candidate not in val:
    raise SystemExit("validated candidate output anchor not found")
val = val.replace(old_candidate, new_candidate, 1)
old_val_report = '''        "symbol": target_symbol,
        "generated_at": isoformat_utc(utc_now()),
        "session_count": len(sessions),
'''
new_val_report = '''        "symbol": target_symbol,
        "generated_at": isoformat_utc(utc_now()),
        "automatic_slippage_enabled": bool(optimizer.automatic_slippage),
        "session_count": len(sessions),
'''
if old_val_report not in val:
    raise SystemExit("validated report output anchor not found")
val = val.replace(old_val_report, new_val_report, 1)
engine = engine[:val_start] + val + engine[val_end:]

engine_path.write_text(engine, encoding="utf-8")

# ---------------- App UI ----------------
app_path = Path("youtube_strategy_app.py")
app = app_path.read_text(encoding="utf-8")

# Extend the input legend.
old_legend = '''            "🟢 **AUTO-SEARCH** = tests multiple supported values and refines promising ones · "
            "🟠 **CEILING** = auto-searches below the number you enter, but never above it · "
            "🔒 **FIXED** = uses exactly the value you enter · "
            "🟡 **THRESHOLD** = qualification/ranking rule, not a value being optimized · "
            "🔵 **SEARCH CONTROL** = controls how much or what the optimizer searches."
'''
new_legend = '''            "🟢 **AUTO-SEARCH** = tests multiple supported values and refines promising ones · "
            "🟠 **CEILING** = auto-searches below the number you enter, but never above it · "
            "🔒 **FIXED** = uses exactly the value you enter · "
            "🟣 **FALLBACK FLOOR** = automatic mode estimates the value, but never assumes less than this floor · "
            "🟡 **THRESHOLD** = qualification/ranking rule, not a value being optimized · "
            "🔵 **SEARCH CONTROL** = controls how much or what the optimizer searches."
'''
if old_legend not in app:
    raise SystemExit("optimizer legend anchor not found")
app = app.replace(old_legend, new_legend, 1)

# Replace execution-cost inputs and old live-spread checkbox.
old_costs = '''            cost_row = st.columns(3)
            optimizer_spread = cost_row[0].number_input(
                "🔒 FIXED · Spread estimate (bps)",
                min_value=0.0,
                max_value=500.0,
                value=float(manual_optimizer_defaults.get("spread_bps", 12.0)),
                step=1.0,
                help=(
                    "FIXED trading-cost assumption: the optimizer does not search for a better spread. "
                    "If the actual-quoted-spread option is enabled, the app may replace this with a wider live quoted spread."
                ),
            )
            optimizer_slippage = cost_row[1].number_input(
                "🔒 FIXED · Slippage per fill (bps)",
                min_value=0.0,
                max_value=500.0,
                value=float(manual_optimizer_defaults.get("slippage_bps", 8.0)),
                step=1.0,
                help="FIXED trading-cost assumption: every candidate uses this slippage per simulated fill.",
            )
            optimizer_fee = cost_row[2].number_input(
                "🔒 FIXED · Fee per order ($)",
                min_value=0.0,
                max_value=50.0,
                value=float(manual_optimizer_defaults.get("fee_per_order", 0.0)),
                step=0.1,
                help="FIXED trading-cost assumption: every candidate uses this fee per simulated order.",
            )
            protection_row = st.columns(3)
            optimizer_drawdown = protection_row[0].number_input(
                "🟡 THRESHOLD · Maximum acceptable drawdown (%)",
                min_value=0.5,
                max_value=50.0,
                value=15.0,
                step=0.5,
                help="Settings that exceed this historical loss limit receive a strong ranking penalty.",
            )
            sizing_depth = protection_row[1].selectbox(
                "🔵 SEARCH CONTROL · Position-size search depth",
                ["Quick — 8 sizing combinations", "Balanced — 24 sizing combinations", "Comprehensive — 48 sizing combinations", "Exhaustive — 64 sizing combinations"],
                index=2,
            )
            use_live_spread = protection_row[2].checkbox(
                "🔒 FIXED/OVERRIDE · Use the stock's actual quoted spread",
                value=False,
                help="Leave this off when you want an apples-to-apples comparison with the manual backtest. Turn it on for a more conservative live-spread assumption.",
            )
'''
new_costs = '''            execution_cost_mode = st.selectbox(
                "Execution-cost model",
                [
                    "Automatic — quoted spread + estimated slippage (recommended)",
                    "Manual — use fixed spread and slippage",
                ],
                index=0,
                help=(
                    "Automatic mode uses the wider of the current quoted spread or the fallback floor, then estimates slippage "
                    "for each candidate from recent liquidity, intraday volatility, risk, stop distance, and simulated position size. "
                    "Manual mode uses exactly the spread and slippage values you enter."
                ),
            )
            automatic_execution_costs = execution_cost_mode.startswith("Automatic")
            if automatic_execution_costs:
                st.caption(
                    "Automatic execution costs are ON. The values below are conservative fallback floors, not values you need to optimize yourself."
                )
            cost_row = st.columns(3)
            optimizer_spread = cost_row[0].number_input(
                "🟣 FALLBACK FLOOR · Spread (bps)" if automatic_execution_costs else "🔒 FIXED · Spread (bps)",
                min_value=0.0,
                max_value=500.0,
                value=float(manual_optimizer_defaults.get("spread_bps", 12.0)),
                step=1.0,
                disabled=automatic_execution_costs,
                help=(
                    "AUTOMATIC: the app checks the current quote and uses whichever is wider: this floor or the quoted spread."
                    if automatic_execution_costs else
                    "MANUAL: every optimizer candidate uses exactly this spread assumption."
                ),
            )
            optimizer_slippage = cost_row[1].number_input(
                "🟣 FALLBACK FLOOR · Slippage per fill (bps)" if automatic_execution_costs else "🔒 FIXED · Slippage per fill (bps)",
                min_value=0.0,
                max_value=500.0,
                value=float(manual_optimizer_defaults.get("slippage_bps", 8.0)),
                step=1.0,
                disabled=automatic_execution_costs,
                help=(
                    "AUTOMATIC: this is the minimum slippage assumption. The engine can raise it for candidates with larger orders, lower liquidity, or higher volatility."
                    if automatic_execution_costs else
                    "MANUAL: every optimizer candidate uses exactly this slippage per simulated fill."
                ),
            )
            optimizer_fee = cost_row[2].number_input(
                "🔒 FIXED · Fee per order ($)",
                min_value=0.0,
                max_value=50.0,
                value=float(manual_optimizer_defaults.get("fee_per_order", 0.0)),
                step=0.1,
                help="FIXED: every candidate uses this fee per simulated order.",
            )
            protection_row = st.columns(2)
            optimizer_drawdown = protection_row[0].number_input(
                "🟡 THRESHOLD · Maximum acceptable drawdown (%)",
                min_value=0.5,
                max_value=50.0,
                value=15.0,
                step=0.5,
                help="Settings that exceed this historical loss limit receive a strong ranking penalty.",
            )
            sizing_depth = protection_row[1].selectbox(
                "🔵 SEARCH CONTROL · Position-size search depth",
                ["Quick — 8 sizing combinations", "Balanced — 24 sizing combinations", "Comprehensive — 48 sizing combinations", "Exhaustive — 64 sizing combinations"],
                index=2,
            )
'''
if old_costs not in app:
    raise SystemExit("optimizer cost UI anchor not found")
app = app.replace(old_costs, new_costs, 1)

# Wire auto slippage into tuning settings.
old_tuning = '''                        minimum_historical_trades=8,
                        max_execution_variants_per_finalist=sizing_limit,
'''
new_tuning = '''                        minimum_historical_trades=8,
                        automatic_slippage=bool(automatic_execution_costs),
                        max_execution_variants_per_finalist=sizing_limit,
'''
if old_tuning not in app:
    raise SystemExit("tuning settings anchor not found")
app = app.replace(old_tuning, new_tuning, 1)

# Automatic mode always tries to use a current quoted spread. Manual mode does not.
old_quote = '''                        observed_spread: float | None = None
                        quote_warning = ""
                        if use_live_spread:
                            try:
                                current_snapshot = market.snapshots([ticker]).get(ticker, {})
                                engine_settings, observed_spread = conservative_stock_costs(engine_settings, current_snapshot)
                                if observed_spread is not None and observed_spread > float(optimizer_spread):
                                    quote_warning = (
                                        f"{ticker}'s current quoted spread is {observed_spread:.1f} bps, "
                                        f"wider than your {float(optimizer_spread):.1f} bps estimate. "
                                        "The wider stock-specific spread was used for every test."
                                    )
                            except AppError as quote_error:
                                quote_warning = (
                                    f"A current spread quote was unavailable for {ticker}; your entered spread estimate "
                                    f"was used instead. {quote_error}"
                                )
'''
new_quote = '''                        observed_spread: float | None = None
                        quote_warning = ""
                        if automatic_execution_costs:
                            try:
                                current_snapshot = market.snapshots([ticker]).get(ticker, {})
                                engine_settings, observed_spread = conservative_stock_costs(engine_settings, current_snapshot)
                                if observed_spread is None:
                                    quote_warning = (
                                        f"A current spread quote was unavailable for {ticker}; the {float(optimizer_spread):.1f} bps "
                                        "fallback spread floor was used."
                                    )
                                elif observed_spread > float(optimizer_spread):
                                    quote_warning = (
                                        f"Automatic spread: {ticker}'s current quote is {observed_spread:.1f} bps, wider than the "
                                        f"{float(optimizer_spread):.1f} bps fallback floor, so the quoted spread was used."
                                    )
                                else:
                                    quote_warning = (
                                        f"Automatic spread: {ticker}'s current quote is {observed_spread:.1f} bps. The more conservative "
                                        f"{float(optimizer_spread):.1f} bps fallback floor was retained."
                                    )
                            except AppError as quote_error:
                                quote_warning = (
                                    f"A current spread quote was unavailable for {ticker}; the {float(optimizer_spread):.1f} bps "
                                    f"fallback spread floor was used instead. {quote_error}"
                                )
'''
if old_quote not in app:
    raise SystemExit("quote handling anchor not found")
app = app.replace(old_quote, new_quote, 1)

# Record execution-cost mode in report.
old_report_meta = '''                        report["history_days"] = int(optimizer_history_days)
                        report["observed_spread_bps"] = observed_spread
'''
new_report_meta = '''                        report["history_days"] = int(optimizer_history_days)
                        report["execution_cost_mode"] = "automatic" if automatic_execution_costs else "manual"
                        report["observed_spread_bps"] = observed_spread
'''
if old_report_meta not in app:
    raise SystemExit("report metadata anchor not found")
app = app.replace(old_report_meta, new_report_meta, 1)

# Show the effective automatic costs alongside recommended settings.
old_winning_profile = '''            winning_profile = winning.get("optimized_backtest_settings") or optimization_report.get("backtest_settings") or {}
            winning_rules = normalize_machine_rules(winning.get("optimized_rules"))
'''
new_winning_profile = '''            winning_profile = winning.get("optimized_backtest_settings") or optimization_report.get("backtest_settings") or {}
            winning_rules = normalize_machine_rules(winning.get("optimized_rules"))
            if optimization_report.get("execution_cost_mode") == "automatic":
                quoted_spread = safe_float(optimization_report.get("observed_spread_bps"))
                spread_used = safe_float(winning_profile.get("spread_bps"), 0.0) or 0.0
                slippage_used = safe_float(winning_profile.get("slippage_bps"), 0.0) or 0.0
                quote_text = f" Current quote: {quoted_spread:.1f} bps." if quoted_spread is not None else " Current quote unavailable."
                st.info(
                    f"Automatic execution-cost estimate for the winning setup: spread used {spread_used:.1f} bps; "
                    f"estimated slippage {slippage_used:.1f} bps per fill.{quote_text} "
                    "Slippage is estimated from recent liquidity/volatility and the simulated order size, with your fallback floor as a minimum."
                )
'''
if old_winning_profile not in app:
    raise SystemExit("winning profile anchor not found")
app = app.replace(old_winning_profile, new_winning_profile, 1)

# Persist mode metadata with saved strategy summary.
old_summary_meta = '''                        "observed_spread_bps": optimization_report.get("observed_spread_bps"),
                        "timeframes_tested": optimization_report.get("timeframes_tested") or [],
'''
new_summary_meta = '''                        "observed_spread_bps": optimization_report.get("observed_spread_bps"),
                        "execution_cost_mode": optimization_report.get("execution_cost_mode") or "manual",
                        "timeframes_tested": optimization_report.get("timeframes_tested") or [],
'''
if old_summary_meta not in app:
    raise SystemExit("saved strategy summary anchor not found")
app = app.replace(old_summary_meta, new_summary_meta, 1)

# Add glossary terms for automatic execution costs.
glossary_anchor = '''HELP_GLOSSARY: list[dict[str, str]] = [
'''
entries = '''HELP_GLOSSARY: list[dict[str, str]] = [
    {"term": "Automatic execution costs", "category": "Execution", "meaning": "Optimizer mode that uses a current quoted-spread floor and estimates slippage from recent liquidity, volatility, risk, stop distance, and simulated order size instead of asking you to choose a single slippage number."},
    {"term": "FALLBACK FLOOR optimizer input", "category": "Optimizer", "meaning": "A conservative minimum used by an automatic estimate. The automatic model may use a higher value when market conditions imply greater trading costs, but it will not assume a lower cost than the floor."},
'''
if glossary_anchor not in app:
    raise SystemExit("glossary anchor not found")
if '"term": "Automatic execution costs"' not in app:
    app = app.replace(glossary_anchor, entries, 1)

app_path.write_text(app, encoding="utf-8")
