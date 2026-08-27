"""Full version of the YouTube Trading Strategy Lab with reproducible backtests."""

from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
core_path = ROOT / "youtube_strategy_app_core.py"
source = core_path.read_text(encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Could not apply Full Lab patch: {label}")
    return text.replace(old, new, 1)


# Add saved-rule awareness and persistent run history before the manual backtest form.
source = replace_once(
    source,
    '        with st.form("backtest_form"):\n',
    '''        saved_backtest_rules = normalize_machine_rules(chosen.get("machine_rules"))
        saved_stop_rule = safe_float(saved_backtest_rules.get("stop_loss_pct"))
        saved_reward_rule = safe_float(saved_backtest_rules.get("reward_risk"))

        run_history = list(chosen.get("backtest_run_history") or [])
        if run_history:
            with st.expander("🕘 Backtest run history — reproduce an earlier test", expanded=False):
                st.caption(
                    "These records store the exact historical timestamps and settings used. "
                    "Re-run exact settings tests the same candle window again."
                )
                for history_index, previous_run in enumerate(run_history[:12]):
                    previous_results = previous_run.get("results") or []
                    total_pnl = sum(safe_float(item.get("net_pnl"), 0.0) or 0.0 for item in previous_results)
                    total_trades = sum(int(safe_float(item.get("trades"), 0.0) or 0.0) for item in previous_results)
                    row = st.columns([4.4, 1.35])
                    row[0].markdown(
                        f'**{local_timestamp(previous_run.get("tested_at"))}** · '
                        f'{", ".join(previous_run.get("tickers") or []) or "—"} · '
                        f'{previous_run.get("timeframe") or "?"} · '
                        f'{previous_run.get("window_label") or "Saved window"}'
                    )
                    row[0].caption(
                        f'Net {money(total_pnl)} · {total_trades} trades · '
                        f'actual stop {safe_float(previous_run.get("actual_stop_pct"), 0.0):.2f}% · '
                        f'actual reward/risk {safe_float(previous_run.get("actual_reward_risk"), 0.0):.2f}x'
                    )
                    if row[1].button(
                        "Re-run exact settings",
                        key=f'backtest_replay_{chosen.get("id")}_{history_index}',
                        use_container_width=True,
                    ):
                        st.session_state["backtest_replay_payload"] = previous_run
                        st.session_state["backtest_replay_requested"] = True
                        st.rerun()

        st.caption("Choose a rolling window for the latest data, or fixed dates for an apples-to-apples comparison.")
        with st.form("backtest_form"):
''',
    "insert reproducibility controls",
)

# Replace the old single history-days control with an explicit rolling/fixed selector.
source = replace_once(
    source,
    '''            ticker_column, history_column, timeframe_column = st.columns(3)
            tickers_raw = ticker_column.text_input(
                "Tickers to test",
                value=optimized_symbol or "AAPL, NVDA",
                help="Use up to five tickers, separated by commas.",
            )
            history_days = history_column.slider("Calendar days of history", min_value=7, max_value=120, value=preferred_history)
            timeframe = timeframe_column.selectbox(
                "Candle interval", supported_timeframes, index=supported_timeframes.index(preferred_timeframe)
            )
''',
    '''            ticker_column, window_column, timeframe_column = st.columns(3)
            tickers_raw = ticker_column.text_input(
                "Tickers to test",
                value=optimized_symbol or "AAPL, NVDA",
                help="Use up to five tickers, separated by commas.",
            )
            window_mode = window_column.selectbox(
                "Historical window",
                ["Rolling — last N calendar days", "Fixed dates — reproducible"],
                help=(
                    "Rolling moves forward with time. Fixed dates keep the historical period unchanged, "
                    "which is best for comparing manual setting changes."
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
                fixed_dates = st.columns(2)
                fixed_start = fixed_dates[0].date_input(
                    "Fixed start date",
                    value=default_fixed_start,
                    max_value=last_completed_day,
                )
                fixed_end = fixed_dates[1].date_input(
                    "Fixed end date",
                    value=default_fixed_end,
                    max_value=last_completed_day,
                )
                history_days = max(1, (fixed_end - fixed_start).days + 1)
                st.caption(f"Fixed historical span: {history_days} calendar day(s).")
''',
    "replace historical window controls",
)

source = source.replace('"Fallback stop (%)"', '"Fallback stop (%) — only if strategy has no saved stop"', 1)
source = source.replace('"Fallback reward/risk"', '"Fallback reward/risk — only if strategy has no saved target"', 1)

# Show exactly which stop and reward/risk values the engine will use.
source = replace_once(
    source,
    '            run_requested = st.form_submit_button("Run historical backtest", use_container_width=True)\n',
    '''            actual_stop_preview = saved_stop_rule if saved_stop_rule is not None else float(default_stop)
            actual_reward_preview = saved_reward_rule if saved_reward_rule is not None else float(default_ratio)
            stop_preview_source = "saved strategy rule — fallback is NOT used" if saved_stop_rule is not None else "fallback input"
            reward_preview_source = "saved strategy rule — fallback is NOT used" if saved_reward_rule is not None else "fallback input"
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
            st.info("Re-running the exact saved candle window and settings from the selected history record.")
''',
    "insert actual settings and replay handling",
)

# Use exact timestamps for replay/fixed-date runs; keep the original rolling behavior otherwise.
source = replace_once(
    source,
    '''                    delay = 16 if market.historical_feed == "sip" and market.live_feed != "sip" else 1
                    end = utc_now() - timedelta(minutes=delay)
                    start = end - timedelta(days=int(history_days))
                    backtest_bar.progress(
                        0.18,
                        text=backtest_monitor.text(0.18, "Downloading Alpaca historical candles"),
                    )
                    with st.spinner("Downloading Alpaca historical candles and running conservative simulations…"):
''',
    '''                    if replay_payload and replay_payload.get("start_iso") and replay_payload.get("end_iso"):
                        try:
                            start = datetime.fromisoformat(str(replay_payload["start_iso"]).replace("Z", "+00:00"))
                            end = datetime.fromisoformat(str(replay_payload["end_iso"]).replace("Z", "+00:00"))
                        except (TypeError, ValueError) as replay_error:
                            raise AppError("The saved historical window could not be read. Run a new backtest first.") from replay_error
                        history_days_for_record = int(safe_float(replay_payload.get("history_days"), history_days) or history_days)
                        window_label_for_record = str(replay_payload.get("window_label") or "Exact saved window")
                    elif window_mode.startswith("Fixed"):
                        if fixed_start is None or fixed_end is None or fixed_start > fixed_end:
                            raise AppError("The fixed start date must be on or before the fixed end date.")
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
                    backtest_bar.progress(
                        0.18,
                        text=backtest_monitor.text(0.18, "Downloading Alpaca historical candles"),
                    )
                    with st.spinner("Downloading Alpaca historical candles and running conservative simulations…"):
''',
    "replace historical timestamp calculation",
)

# Save result context first. If the extra persistent history save fails, the successful
# backtest still renders instead of disappearing behind a red error.
source = replace_once(
    source,
    '''                    store.record_backtest(
                        str(chosen.get("id") or ""),
                        results,
                        timeframe=timeframe,
                        history_days=int(history_days),
                    )
                    st.session_state["backtest_results"] = results
                    st.session_state["backtest_strategy_id"] = chosen.get("id")
''',
    '''                    store.record_backtest(
                        str(chosen.get("id") or ""),
                        results,
                        timeframe=timeframe,
                        history_days=int(history_days_for_record),
                    )

                    actual_stop_used = saved_stop_rule if saved_stop_rule is not None else float(default_stop)
                    actual_reward_used = saved_reward_rule if saved_reward_rule is not None else float(default_ratio)
                    stop_source = "saved strategy rule" if saved_stop_rule is not None else "fallback input"
                    reward_source = "saved strategy rule" if saved_reward_rule is not None else "fallback input"
                    end_display = (end.astimezone(ET) - timedelta(seconds=1)).date().isoformat()
                    run_record = {
                        "tested_at": isoformat_utc(utc_now()),
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
                    st.session_state["backtest_results"] = results
                    st.session_state["backtest_strategy_id"] = chosen.get("id")
                    st.session_state["backtest_run_context"] = run_record

                    previous_history = list(chosen.get("backtest_run_history") or [])
                    try:
                        store.update_strategy(
                            str(chosen.get("id") or ""),
                            {"backtest_run_history": [run_record, *previous_history][:20]},
                        )
                    except AppError as history_error:
                        st.warning(
                            "The backtest completed and the results below are still available, but the extra run-history "
                            f"record could not be saved permanently: {history_error}"
                        )
''',
    "save reproducible run context",
)

source = replace_once(
    source,
    '''        if results:
            section("Backtest results", "Historical results are hypothetical. Holdout results were not used to define the in-sample segment.")
            summary = []
''',
    '''        if results:
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
''',
    "add result context",
)

# Compile the patched source before execution. Any future source mismatch fails clearly
# instead of silently falling back to the old form.
code = compile(source, str(core_path), "exec")
exec(code, globals(), globals())

with st.sidebar:
    st.divider()
    st.caption("Build 2026-08-26.7")
    if st.button("← Trading Dashboard", key="full_lab_back_dashboard", use_container_width=True):
        st.switch_page("youtube_strategy_app.py")
