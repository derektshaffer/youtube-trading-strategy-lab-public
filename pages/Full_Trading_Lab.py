"""Full version of the original YouTube Trading Strategy Lab.

The underlying research/backtest engine remains unchanged. This page adds a
small reproducibility layer around the manual backtest UI so users can choose
fixed historical dates, see which stop/target settings are actually in effect,
and re-run a previously saved test against the exact same candle window.
"""

from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
core_path = ROOT / "youtube_strategy_app_core.py"
source = core_path.read_text(encoding="utf-8")

old_form = '''        with st.form("backtest_form"):
            ticker_column, history_column, timeframe_column = st.columns(3)
            tickers_raw = ticker_column.text_input(
                "Tickers to test",
                value=optimized_symbol or "AAPL, NVDA",
                help="Use up to five tickers, separated by commas.",
            )
            history_days = history_column.slider("Calendar days of history", min_value=7, max_value=120, value=preferred_history)
            timeframe = timeframe_column.selectbox(
                "Candle interval", supported_timeframes, index=supported_timeframes.index(preferred_timeframe)
            )
            settings_columns = st.columns(4)
            starting_cash = settings_columns[0].number_input(
                "Starting cash ($)", min_value=100.0,
                value=max(100.0, safe_float(optimized_profile.get("starting_cash"), 10_000.0) or 10_000.0),
                step=500.0,
            )
            risk_per_trade = settings_columns[1].number_input(
                "Risk per trade (%)", min_value=0.05, max_value=10.0,
                value=max(0.05, min(10.0, safe_float(optimized_profile.get("risk_per_trade_pct"), 0.5) or 0.5)),
                step=0.05,
            )
            position_cap = settings_columns[2].number_input(
                "Maximum position (%)", min_value=1.0, max_value=100.0,
                value=max(1.0, min(100.0, safe_float(optimized_profile.get("max_position_pct"), 20.0) or 20.0)),
                step=1.0,
            )
            default_stop = settings_columns[3].number_input(
                "Fallback stop (%)", min_value=0.1, max_value=30.0,
                value=max(0.1, min(30.0, safe_float(optimized_profile.get("default_stop_pct"), 2.0) or 2.0)),
                step=0.1,
            )
            friction_columns = st.columns(4)
            default_ratio = friction_columns[0].number_input(
                "Fallback reward/risk", min_value=0.2, max_value=10.0,
                value=max(0.2, min(10.0, safe_float(optimized_profile.get("default_reward_risk"), 2.0) or 2.0)),
                step=0.1,
            )
            spread_bps = friction_columns[1].number_input(
                "Spread estimate (bps)", min_value=0.0, max_value=2_000.0,
                value=max(0.0, min(2_000.0, safe_float(optimized_profile.get("spread_bps"), 12.0) or 0.0)),
                step=1.0,
            )
            slippage_bps = friction_columns[2].number_input(
                "Slippage per fill (bps)", min_value=0.0, max_value=500.0,
                value=max(0.0, min(500.0, safe_float(optimized_profile.get("slippage_bps"), 8.0) or 0.0)),
                step=1.0,
            )
            order_fee = friction_columns[3].number_input(
                "Fee per order ($)", min_value=0.0, max_value=50.0,
                value=max(0.0, min(50.0, safe_float(optimized_profile.get("fee_per_order"), 0.0) or 0.0)),
                step=0.1,
            )
            run_requested = st.form_submit_button("Run historical backtest", use_container_width=True)
'''

new_form = '''        saved_backtest_rules = normalize_machine_rules(chosen.get("machine_rules"))
        saved_stop_rule = safe_float(saved_backtest_rules.get("stop_loss_pct"))
        saved_reward_rule = safe_float(saved_backtest_rules.get("reward_risk"))

        run_history = list(chosen.get("backtest_run_history") or [])
        if run_history:
            with st.expander("🕘 Backtest run history — reproduce an earlier test", expanded=False):
                st.caption(
                    "Each entry stores the exact candle window and the settings used. "
                    "Re-run exact settings uses those same historical timestamps, so the comparison is apples-to-apples."
                )
                for history_index, previous_run in enumerate(run_history[:12]):
                    previous_results = previous_run.get("results") or []
                    total_pnl = sum(safe_float(item.get("net_pnl"), 0.0) or 0.0 for item in previous_results)
                    total_trades = sum(int(safe_float(item.get("trades"), 0.0) or 0.0) for item in previous_results)
                    window_text = str(previous_run.get("window_label") or "Saved historical window")
                    history_columns = st.columns([4.4, 1.35])
                    history_columns[0].markdown(
                        f'**{local_timestamp(previous_run.get("tested_at"))}** · '
                        f'{", ".join(previous_run.get("tickers") or []) or "—"} · '
                        f'{previous_run.get("timeframe") or "?"} · {window_text}'
                    )
                    history_columns[0].caption(
                        f'Net {money(total_pnl)} · {total_trades} trades · '
                        f'actual stop {safe_float(previous_run.get("actual_stop_pct"), 0.0):.2f}% '
                        f'({previous_run.get("stop_source") or "unknown source"}) · '
                        f'actual reward/risk {safe_float(previous_run.get("actual_reward_risk"), 0.0):.2f}x '
                        f'({previous_run.get("reward_source") or "unknown source"})'
                    )
                    if history_columns[1].button(
                        "Re-run exact settings",
                        key=f'backtest_replay_{chosen.get("id")}_{history_index}',
                        use_container_width=True,
                    ):
                        st.session_state["backtest_replay_payload"] = previous_run
                        st.session_state["backtest_replay_requested"] = True
                        st.rerun()
                    if history_index < min(len(run_history), 12) - 1:
                        st.divider()

        with st.form("backtest_form"):
            ticker_column, window_column, timeframe_column = st.columns(3)
            tickers_raw = ticker_column.text_input(
                "Tickers to test",
                value=optimized_symbol or "AAPL, NVDA",
                help="Use up to five tickers, separated by commas.",
            )
            window_mode = window_column.selectbox(
                "Historical window",
                ["Rolling — last N calendar days", "Fixed dates — reproducible"],
                index=0,
                help=(
                    "Rolling moves forward as time passes. Fixed dates use completed trading days and are better when "
                    "you want to compare parameter changes against exactly the same market period."
                ),
            )
            timeframe = timeframe_column.selectbox(
                "Candle interval", supported_timeframes, index=supported_timeframes.index(preferred_timeframe)
            )

            fixed_start = None
            fixed_end = None
            if window_mode.startswith("Rolling"):
                history_days = st.slider("Calendar days of history", min_value=7, max_value=120, value=preferred_history)
            else:
                last_completed_day = utc_now().astimezone(ET).date() - timedelta(days=1)
                default_fixed_end = last_completed_day
                default_fixed_start = last_completed_day - timedelta(days=max(1, preferred_history - 1))
                date_columns = st.columns(2)
                fixed_start = date_columns[0].date_input(
                    "Fixed start date",
                    value=default_fixed_start,
                    max_value=last_completed_day,
                    help="Start of the exact historical period to test.",
                )
                fixed_end = date_columns[1].date_input(
                    "Fixed end date",
                    value=default_fixed_end,
                    max_value=last_completed_day,
                    help="End of the exact historical period. Today is excluded so the same test can be reproduced later.",
                )
                history_days = max(1, (fixed_end - fixed_start).days + 1)
                st.caption(f"Fixed historical span: {history_days} calendar day(s).")

            settings_columns = st.columns(4)
            starting_cash = settings_columns[0].number_input(
                "Starting cash ($)", min_value=100.0,
                value=max(100.0, safe_float(optimized_profile.get("starting_cash"), 10_000.0) or 10_000.0),
                step=500.0,
            )
            risk_per_trade = settings_columns[1].number_input(
                "Risk per trade (%)", min_value=0.05, max_value=10.0,
                value=max(0.05, min(10.0, safe_float(optimized_profile.get("risk_per_trade_pct"), 0.5) or 0.5)),
                step=0.05,
            )
            position_cap = settings_columns[2].number_input(
                "Maximum position (%)", min_value=1.0, max_value=100.0,
                value=max(1.0, min(100.0, safe_float(optimized_profile.get("max_position_pct"), 20.0) or 20.0)),
                step=1.0,
            )
            default_stop = settings_columns[3].number_input(
                "Fallback stop (%) — only if strategy has no saved stop", min_value=0.1, max_value=30.0,
                value=max(0.1, min(30.0, safe_float(optimized_profile.get("default_stop_pct"), 2.0) or 2.0)),
                step=0.1,
            )
            friction_columns = st.columns(4)
            default_ratio = friction_columns[0].number_input(
                "Fallback reward/risk — only if strategy has no saved target", min_value=0.2, max_value=10.0,
                value=max(0.2, min(10.0, safe_float(optimized_profile.get("default_reward_risk"), 2.0) or 2.0)),
                step=0.1,
            )
            spread_bps = friction_columns[1].number_input(
                "Spread estimate (bps)", min_value=0.0, max_value=2_000.0,
                value=max(0.0, min(2_000.0, safe_float(optimized_profile.get("spread_bps"), 12.0) or 0.0)),
                step=1.0,
            )
            slippage_bps = friction_columns[2].number_input(
                "Slippage per fill (bps)", min_value=0.0, max_value=500.0,
                value=max(0.0, min(500.0, safe_float(optimized_profile.get("slippage_bps"), 8.0) or 0.0)),
                step=1.0,
            )
            order_fee = friction_columns[3].number_input(
                "Fee per order ($)", min_value=0.0, max_value=50.0,
                value=max(0.0, min(50.0, safe_float(optimized_profile.get("fee_per_order"), 0.0) or 0.0)),
                step=0.1,
            )

            actual_stop_preview = saved_stop_rule if saved_stop_rule is not None else float(default_stop)
            actual_reward_preview = saved_reward_rule if saved_reward_rule is not None else float(default_ratio)
            stop_preview_source = "saved strategy rule — fallback box is NOT used" if saved_stop_rule is not None else "fallback input above"
            reward_preview_source = "saved strategy rule — fallback box is NOT used" if saved_reward_rule is not None else "fallback input above"
            st.info(
                f"Actual settings this backtest will use → Stop: {actual_stop_preview:.2f}% ({stop_preview_source}) · "
                f"Reward/risk: {actual_reward_preview:.2f}x ({reward_preview_source})"
            )
            manual_run_requested = st.form_submit_button("Run historical backtest", use_container_width=True)

        replay_payload = None
        if st.session_state.pop("backtest_replay_requested", False):
            replay_payload = st.session_state.pop("backtest_replay_payload", None)
        run_requested = bool(manual_run_requested or replay_payload)
        if replay_payload:
            replay_settings = replay_payload.get("settings") or {}
            tickers_raw = ", ".join(replay_payload.get("tickers") or [])
            timeframe = str(replay_payload.get("timeframe") or timeframe)
            starting_cash = safe_float(replay_settings.get("starting_cash"), starting_cash) or starting_cash
            risk_per_trade = safe_float(replay_settings.get("risk_per_trade_pct"), risk_per_trade) or risk_per_trade
            position_cap = safe_float(replay_settings.get("max_position_pct"), position_cap) or position_cap
            default_stop = safe_float(replay_settings.get("default_stop_pct"), default_stop) or default_stop
            default_ratio = safe_float(replay_settings.get("default_reward_risk"), default_ratio) or default_ratio
            spread_bps = safe_float(replay_settings.get("spread_bps"), spread_bps) or 0.0
            slippage_bps = safe_float(replay_settings.get("slippage_bps"), slippage_bps) or 0.0
            order_fee = safe_float(replay_settings.get("fee_per_order"), order_fee) or 0.0
            history_days = int(safe_float(replay_payload.get("history_days"), history_days) or history_days)
            st.info(
                f'Re-running the exact saved candle window from {replay_payload.get("start_date") or "?"} '
                f'through {replay_payload.get("end_date") or "?"} with the original settings.'
            )
'''

if old_form not in source:
    raise RuntimeError("Could not locate the manual backtest form for reproducibility upgrade.")
source = source.replace(old_form, new_form, 1)

old_window = '''                    delay = 16 if market.historical_feed == "sip" and market.live_feed != "sip" else 1
                    end = utc_now() - timedelta(minutes=delay)
                    start = end - timedelta(days=int(history_days))
                    with st.spinner("Downloading Alpaca historical candles and running conservative simulations…"):
'''

new_window = '''                    if replay_payload and replay_payload.get("start_iso") and replay_payload.get("end_iso"):
                        try:
                            start = datetime.fromisoformat(str(replay_payload["start_iso"]).replace("Z", "+00:00"))
                            end = datetime.fromisoformat(str(replay_payload["end_iso"]).replace("Z", "+00:00"))
                        except (TypeError, ValueError) as replay_error:
                            raise AppError("The saved backtest window could not be read. Run a new backtest and save it again.") from replay_error
                        history_days_for_record = int(safe_float(replay_payload.get("history_days"), history_days) or history_days)
                        window_label_for_record = str(replay_payload.get("window_label") or "Exact saved window")
                    elif window_mode.startswith("Fixed"):
                        if fixed_start is None or fixed_end is None or fixed_start > fixed_end:
                            raise AppError("For a fixed historical test, the start date must be on or before the end date.")
                        start = datetime.combine(fixed_start, datetime.min.time(), tzinfo=ET)
                        end = datetime.combine(fixed_end + timedelta(days=1), datetime.min.time(), tzinfo=ET)
                        history_days_for_record = (fixed_end - fixed_start).days + 1
                        window_label_for_record = f"Fixed {fixed_start.isoformat()} → {fixed_end.isoformat()}"
                    else:
                        delay = 16 if market.historical_feed == "sip" and market.live_feed != "sip" else 1
                        end = utc_now() - timedelta(minutes=delay)
                        start = end - timedelta(days=int(history_days))
                        history_days_for_record = int(history_days)
                        window_label_for_record = f"Rolling last {int(history_days)} calendar days"
                    with st.spinner("Downloading Alpaca historical candles and running conservative simulations…"):
'''

if old_window not in source:
    raise RuntimeError("Could not locate the historical-window calculation for reproducibility upgrade.")
source = source.replace(old_window, new_window, 1)

old_save = '''                    store.record_backtest(
                        str(chosen.get("id") or ""),
                        results,
                        timeframe=timeframe,
                        history_days=int(history_days),
                    )
                    st.session_state["backtest_results"] = results
                    st.session_state["backtest_strategy_id"] = chosen.get("id")
'''

new_save = '''                    store.record_backtest(
                        str(chosen.get("id") or ""),
                        results,
                        timeframe=timeframe,
                        history_days=int(history_days_for_record),
                    )

                    actual_stop_used = saved_stop_rule if saved_stop_rule is not None else float(default_stop)
                    actual_reward_used = saved_reward_rule if saved_reward_rule is not None else float(default_ratio)
                    stop_source = "saved strategy rule" if saved_stop_rule is not None else "fallback input"
                    reward_source = "saved strategy rule" if saved_reward_rule is not None else "fallback input"
                    tested_at = isoformat_utc(utc_now())
                    end_display = (end.astimezone(ET) - timedelta(seconds=1)).date().isoformat()
                    run_record = {
                        "tested_at": tested_at,
                        "tickers": list(tickers),
                        "timeframe": str(timeframe),
                        "history_days": int(history_days_for_record),
                        "window_label": window_label_for_record,
                        "start_iso": isoformat_utc(start),
                        "end_iso": isoformat_utc(end),
                        "start_date": start.astimezone(ET).date().isoformat(),
                        "end_date": end_display,
                        "actual_stop_pct": float(actual_stop_used),
                        "actual_reward_risk": float(actual_reward_used),
                        "stop_source": stop_source,
                        "reward_source": reward_source,
                        "settings": {
                            "starting_cash": float(starting_cash),
                            "risk_per_trade_pct": float(risk_per_trade),
                            "max_position_pct": float(position_cap),
                            "default_stop_pct": float(default_stop),
                            "default_reward_risk": float(default_ratio),
                            "spread_bps": float(spread_bps),
                            "slippage_bps": float(slippage_bps),
                            "fee_per_order": float(order_fee),
                        },
                        "results": [
                            {
                                "symbol": str(result.get("symbol") or "?"),
                                "sessions": int(safe_float(result.get("sessions"), 0) or 0),
                                "trades": int(safe_float((result.get("metrics") or {}).get("trade_count"), 0) or 0),
                                "net_pnl": safe_float((result.get("metrics") or {}).get("net_pnl"), 0.0) or 0.0,
                                "holdout_trades": int(safe_float((result.get("out_of_sample") or {}).get("trade_count"), 0) or 0),
                                "holdout_pnl": safe_float((result.get("out_of_sample") or {}).get("net_pnl"), 0.0) or 0.0,
                            }
                            for result in results
                        ],
                    }
                    previous_history = list(chosen.get("backtest_run_history") or [])
                    store.update_strategy(
                        str(chosen.get("id") or ""),
                        {"backtest_run_history": [run_record, *previous_history][:20]},
                    )
                    st.session_state["backtest_results"] = results
                    st.session_state["backtest_strategy_id"] = chosen.get("id")
                    st.session_state["backtest_run_context"] = run_record
'''

if old_save not in source:
    raise RuntimeError("Could not locate backtest save block for run-history upgrade.")
source = source.replace(old_save, new_save, 1)

old_results_header = '''        if results:
            section("Backtest results", "Historical results are hypothetical. Holdout results were not used to define the in-sample segment.")
            summary = []
'''

new_results_header = '''        if results:
            section("Backtest results", "Historical results are hypothetical. Holdout results were not used to define the in-sample segment.")
            active_run_context = st.session_state.get("backtest_run_context") or {}
            if active_run_context:
                st.caption(
                    f'Tested window: {active_run_context.get("window_label") or "—"} · '
                    f'Actual stop used: {safe_float(active_run_context.get("actual_stop_pct"), 0.0):.2f}% '
                    f'({active_run_context.get("stop_source") or "—"}) · '
                    f'Actual reward/risk used: {safe_float(active_run_context.get("actual_reward_risk"), 0.0):.2f}x '
                    f'({active_run_context.get("reward_source") or "—"})'
                )
            summary = []
'''

if old_results_header not in source:
    raise RuntimeError("Could not locate backtest results header for settings summary.")
source = source.replace(old_results_header, new_results_header, 1)

code = compile(source, str(core_path), "exec")
exec(code, globals(), globals())

with st.sidebar:
    st.divider()
    if st.button("← Trading Dashboard", key="full_lab_back_dashboard", use_container_width=True):
        st.switch_page("youtube_strategy_app.py")
