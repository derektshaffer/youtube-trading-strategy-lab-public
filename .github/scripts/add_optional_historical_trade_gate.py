from pathlib import Path

engine_path = Path("youtube_strategy_engine.py")
engine = engine_path.read_text(encoding="utf-8")

# 1) Add explicit optimizer controls, defaulting to the safer 8-trade filter.
old_fields = '''    minimum_training_trades: int = 5
    minimum_validation_trades: int = 2
    training_fraction: float = 0.60
'''
new_fields = '''    minimum_training_trades: int = 5
    minimum_validation_trades: int = 2
    enforce_historical_minimum_trades: bool = True
    minimum_historical_trades: int = 8
    training_fraction: float = 0.60
'''
if old_fields not in engine:
    raise SystemExit("OptimizationSettings fields anchor not found")
engine = engine.replace(old_fields, new_fields, 1)

old_validation = '''        if self.minimum_training_trades < 1 or self.minimum_validation_trades < 1:
            raise AppError("Minimum trade counts must be at least one.")
        if not 0.30 <= self.training_fraction <= 0.80:
'''
new_validation = '''        if self.minimum_training_trades < 1 or self.minimum_validation_trades < 1:
            raise AppError("Minimum trade counts must be at least one.")
        if not 1 <= int(self.minimum_historical_trades) <= 100:
            raise AppError("The historical minimum trade count must be between 1 and 100.")
        if not 0.30 <= self.training_fraction <= 0.80:
'''
if old_validation not in engine:
    raise SystemExit("OptimizationSettings validation anchor not found")
engine = engine.replace(old_validation, new_validation, 1)

# 2) Historical deep optimizer: use the checkbox-controlled setting rather than a forced scaled gate.
old_minimum = '''    minimum_historical_trades = historical_minimum_trade_count(len(sessions))

    warnings = [
'''
new_minimum = '''    minimum_historical_trades = (
        int(optimizer.minimum_historical_trades)
        if optimizer.enforce_historical_minimum_trades
        else 1
    )

    warnings = [
'''
if old_minimum not in engine:
    raise SystemExit("Historical minimum assignment anchor not found")
engine = engine.replace(old_minimum, new_minimum, 1)

old_warning = '''    warnings.append(
        f"Historical best-fit candidates must produce at least {minimum_historical_trades} completed trades "
        f"across these {len(sessions)} trading sessions. Smaller samples cannot outrank qualifying candidates."
    )
'''
new_warning = '''    if optimizer.enforce_historical_minimum_trades:
        warnings.append(
            f"Historical best-fit candidates must produce at least {minimum_historical_trades} completed trades. "
            "Smaller samples cannot outrank qualifying candidates."
        )
    else:
        warnings.append(
            "The historical minimum-trade filter is OFF for this run. Very small samples can rank first, "
            "so treat unusually large P/L from only a few trades with extra caution."
        )
'''
if old_warning not in engine:
    raise SystemExit("Historical warning anchor not found")
engine = engine.replace(old_warning, new_warning, 1)

old_report = '''        "session_count": len(sessions),
        "minimum_historical_trades": minimum_historical_trades,
        "qualifying_strategy_count": len(qualifying_candidates),
'''
new_report = '''        "session_count": len(sessions),
        "historical_minimum_trades_enabled": bool(optimizer.enforce_historical_minimum_trades),
        "minimum_historical_trades": minimum_historical_trades,
        "qualifying_strategy_count": len(qualifying_candidates),
'''
if old_report not in engine:
    raise SystemExit("Historical report anchor not found")
engine = engine.replace(old_report, new_report, 1)

# 3) Cheap strategy screening must use the same gate setting as the deep optimizer.
old_screen_sig = '''def _screen_historical_strategies(
    rows: list[dict[str, Any]],
    strategies: list[dict[str, Any]],
    symbol: str,
    settings: BacktestSettings,
    maximum_drawdown_pct: float,
) -> list[dict[str, Any]]:
'''
new_screen_sig = '''def _screen_historical_strategies(
    rows: list[dict[str, Any]],
    strategies: list[dict[str, Any]],
    symbol: str,
    settings: BacktestSettings,
    maximum_drawdown_pct: float,
    minimum_historical_trades: int = 1,
) -> list[dict[str, Any]]:
'''
if old_screen_sig not in engine:
    raise SystemExit("Historical screening signature anchor not found")
engine = engine.replace(old_screen_sig, new_screen_sig, 1)

old_screen_min = '''    screen_sessions = list(dict.fromkeys(frame.get("session", pd.Series(dtype=str)).tolist()))
    minimum_historical_trades = historical_minimum_trade_count(len(screen_sessions))
    candidates: list[dict[str, Any]] = []
'''
new_screen_min = '''    minimum_historical_trades = max(1, int(minimum_historical_trades))
    candidates: list[dict[str, Any]] = []
'''
if old_screen_min not in engine:
    raise SystemExit("Historical screening minimum anchor not found")
engine = engine.replace(old_screen_min, new_screen_min, 1)

old_screen_call = '''        settings,
        optimization_settings.maximum_drawdown_pct,
    )
'''
new_screen_call = '''        settings,
        optimization_settings.maximum_drawdown_pct,
        (
            int(optimization_settings.minimum_historical_trades)
            if optimization_settings.enforce_historical_minimum_trades
            else 1
        ),
    )
'''
# Replace only inside timeframe historical function after locating its start.
frame_start = engine.index("def _optimize_stock_timeframes_historical(")
call_pos = engine.find(old_screen_call, frame_start)
if call_pos == -1:
    raise SystemExit("Historical screening call anchor not found")
engine = engine[:call_pos] + new_screen_call + engine[call_pos + len(old_screen_call):]

engine_path.write_text(engine, encoding="utf-8")

# 4) Add the checkbox to Stock optimizer and wire it into OptimizationSettings.
app_path = Path("youtube_strategy_app.py")
app = app_path.read_text(encoding="utf-8")

old_goal_caption = '''            if optimizer_goal.startswith("Maximum historical"):
                st.caption("Historical-P/L mode searches the whole window and can overfit. Use Validated edge afterward as a robustness check. Automatic candle comparison screens all intervals first, then deeply optimizes only the strongest one.")

            second_row = st.columns(4)
'''
new_goal_caption = '''            historical_pnl_mode = optimizer_goal.startswith("Maximum historical")
            if historical_pnl_mode:
                st.caption("Historical-P/L mode searches the whole window and can overfit. Use Validated edge afterward as a robustness check. Automatic candle comparison screens all intervals first, then deeply optimizes only the strongest one.")
            require_eight_historical_trades = st.checkbox(
                "Require at least 8 trades before a historical result can win",
                value=True,
                disabled=not historical_pnl_mode,
                help=(
                    "ON: configurations with fewer than 8 completed trades cannot beat configurations that meet the minimum. "
                    "OFF: Maximum historical P/L can select a result based on only a few trades, which can make overfitting much easier. "
                    "This setting applies only to Maximum historical P/L mode."
                ),
            )
            if historical_pnl_mode:
                st.caption(
                    "8-trade sample filter is ON for this run."
                    if require_eight_historical_trades
                    else "Minimum-trade filter is OFF for this run; tiny-sample results are allowed to rank first."
                )

            second_row = st.columns(4)
'''
if old_goal_caption not in app:
    raise SystemExit("Optimizer goal caption anchor not found")
app = app.replace(old_goal_caption, new_goal_caption, 1)

old_settings = '''                        minimum_training_trades=int(minimum_training),
                        minimum_validation_trades=int(minimum_validation),
                        max_execution_variants_per_finalist=sizing_limit,
                        maximum_drawdown_pct=float(optimizer_drawdown),
                        selection_mode=("historical_pnl" if optimizer_goal.startswith("Maximum historical") else "validated"),
                    )
'''
new_settings = '''                        minimum_training_trades=int(minimum_training),
                        minimum_validation_trades=int(minimum_validation),
                        enforce_historical_minimum_trades=bool(historical_pnl_mode and require_eight_historical_trades),
                        minimum_historical_trades=8,
                        max_execution_variants_per_finalist=sizing_limit,
                        maximum_drawdown_pct=float(optimizer_drawdown),
                        selection_mode=("historical_pnl" if historical_pnl_mode else "validated"),
                    )
'''
if old_settings not in app:
    raise SystemExit("OptimizationSettings construction anchor not found")
app = app.replace(old_settings, new_settings, 1)

old_pnl_note = '''                (
                    f'{int(safe_float(selection_metrics.get("trade_count"), 0) or 0)} historical trades · '
                    f'{int(optimization_report.get("minimum_historical_trades") or 1)} required'
                    if historical_fit_mode else
                    f'{int(safe_float(validation.get("trade_count"), 0) or 0)} separate validation trades'
                ),
'''
new_pnl_note = '''                (
                    (
                        f'{int(safe_float(selection_metrics.get("trade_count"), 0) or 0)} historical trades · '
                        f'{int(optimization_report.get("minimum_historical_trades") or 8)} required'
                        if optimization_report.get("historical_minimum_trades_enabled", True)
                        else f'{int(safe_float(selection_metrics.get("trade_count"), 0) or 0)} historical trades · minimum-trade filter OFF'
                    )
                    if historical_fit_mode else
                    f'{int(safe_float(validation.get("trade_count"), 0) or 0)} separate validation trades'
                ),
'''
if old_pnl_note not in app:
    raise SystemExit("Historical P/L metric note anchor not found")
app = app.replace(old_pnl_note, new_pnl_note, 1)

old_quality_note = '''                (
                    f'{optimization_report.get("session_count", 0)} sessions · '
                    f'{optimization_report.get("minimum_historical_trades", 1)} trades required'
                    if historical_fit_mode else
                    f'{optimization_report.get("session_count", 0)} trading sessions reviewed'
                ),
'''
new_quality_note = '''                (
                    (
                        f'{optimization_report.get("session_count", 0)} sessions · '
                        f'{optimization_report.get("minimum_historical_trades", 8)}-trade filter ON'
                        if optimization_report.get("historical_minimum_trades_enabled", True)
                        else f'{optimization_report.get("session_count", 0)} sessions · minimum-trade filter OFF'
                    )
                    if historical_fit_mode else
                    f'{optimization_report.get("session_count", 0)} trading sessions reviewed'
                ),
'''
if old_quality_note not in app:
    raise SystemExit("Historical quality note anchor not found")
app = app.replace(old_quality_note, new_quality_note, 1)

# Add the new term to Help & Glossary if it is not already there.
glossary_anchor = '''HELP_GLOSSARY: list[dict[str, str]] = [
'''
glossary_entry = '''HELP_GLOSSARY: list[dict[str, str]] = [
    {"term": "8-trade sample filter", "category": "Optimizer", "meaning": "Optional Maximum historical P/L safeguard. When ON, a configuration needs at least 8 completed trades to qualify ahead of tiny-sample results. Turn it OFF only when you intentionally want the optimizer to consider results based on fewer trades."},
'''
if glossary_anchor not in app:
    raise SystemExit("Glossary anchor not found")
if '"term": "8-trade sample filter"' not in app:
    app = app.replace(glossary_anchor, glossary_entry, 1)

app_path.write_text(app, encoding="utf-8")
