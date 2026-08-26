"""Simplified default dashboard for YouTube Trading Strategy Lab."""

from __future__ import annotations

import math
from typing import Any

import streamlit as st

from alpaca_paper_trader import PaperTradeError
from live_strategy_runner_page import (
    build_store,
    current_signal,
    market_client,
    paper_client,
    paper_entry,
)
from simple_ml_filter import score_setup
from youtube_strategy_engine import AppError, safe_float


st.set_page_config(
    page_title="Trading Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .simple-hero {
        padding: 24px 26px;
        border: 1px solid #2b4567;
        border-radius: 18px;
        background: linear-gradient(125deg,#15243a,#0d1625 68%,#17243a);
        margin-bottom: 18px;
    }
    .simple-title {font-size: 32px;font-weight: 900;letter-spacing: -.03em;}
    .simple-sub {color:#b8c6d9;margin-top:7px;line-height:1.55;max-width:980px;}
    .tech {color:#8394aa;font-size:.82rem;margin-top:-6px;margin-bottom:8px;}
    .decision {
        padding: 18px 20px;
        border: 1px solid #38536f;
        border-radius: 15px;
        margin: 10px 0 18px 0;
        font-size: 1.18rem;
        font-weight: 800;
    }
    </style>
    <div class="simple-hero">
      <div class="simple-title">📈 Trading Dashboard</div>
      <div class="simple-sub">
        Choose a stock, choose a strategy, set how much you are willing to risk, and press Analyze.
        The strategy rules, historical pattern model, and risk checks run together behind the scenes.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)


def money(value: Any, decimals: int = 2) -> str:
    number = safe_float(value)
    return f"${number:,.{decimals}f}" if number is not None else "—"


def pct(value: Any, digits: int = 1, signed: bool = False) -> str:
    number = safe_float(value)
    if number is None:
        return "—"
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:.{digits}f}%"


def strategy_label(strategy: dict[str, Any]) -> str:
    name = str(strategy.get("name") or "Unnamed strategy")
    optimized = str(strategy.get("optimized_for_symbol") or "").strip().upper()
    approval = "Approved" if strategy.get("approved") else "Review only"
    suffix = f" · {optimized} only" if optimized else ""
    return f"{name}{suffix} · {approval}"


def applicable_strategies(
    strategies: list[dict[str, Any]],
    ticker: str,
) -> list[dict[str, Any]]:
    symbol = ticker.strip().upper()
    result = []
    for strategy in strategies:
        locked = str(strategy.get("optimized_for_symbol") or "").strip().upper()
        if not symbol or not locked or locked == symbol:
            result.append(strategy)
    return sorted(
        result,
        key=lambda item: (
            not bool(item.get("approved")),
            not bool(item.get("optimized_for_symbol")),
            str(item.get("name") or ""),
        ),
    )


with st.sidebar:
    st.markdown("### Simple mode")
    st.caption("This is the normal screen. Advanced tools are still available when you want them.")
    st.divider()
    with st.expander("🧰 Advanced tools", expanded=False):
        if st.button("Full Trading Lab", use_container_width=True):
            st.switch_page("pages/Advanced_Trading_Lab.py")
        if st.button("Machine Learning Lab", use_container_width=True):
            st.switch_page("pages/Advanced_Machine_Learning.py")
        if st.button("Detailed Live Runner", use_container_width=True):
            st.switch_page("pages/Live_Strategy_Runner.py")
    st.divider()
    st.caption(
        "Simple Mode currently checks the market when you press Analyze. "
        "It does not keep running after the page/server stops."
    )

try:
    store = build_store()
    library = store.load()
except AppError as error:
    st.error(str(error))
    st.stop()

strategies = list(library.get("strategies") or [])
if not strategies:
    st.warning("No saved strategies are available yet. Open Advanced tools → Full Trading Lab to create one.")
    st.stop()

top = st.columns([1.0, 2.2, 1.15, 1.0])
with top[0]:
    ticker = st.text_input(
        "Stock",
        value=str(st.session_state.get("simple_ticker", "")),
        placeholder="SDOT",
    ).strip().upper()
    st.caption("Technical: ticker symbol")

available = applicable_strategies(strategies, ticker)
with top[1]:
    if available:
        labels = {strategy_label(item): item for item in available}
        selected_label = st.selectbox("Strategy", list(labels), key="simple_strategy")
        strategy = labels[selected_label]
    else:
        st.selectbox("Strategy", ["No compatible saved strategy"], disabled=True)
        strategy = None
    st.caption("Technical: deterministic strategy rules")

with top[2]:
    mode = st.radio(
        "What should it do?",
        ["Analyze only", "Simulated trading"],
        horizontal=False,
    )
    st.caption("Simulated trading = Alpaca paper account")

with top[3]:
    risk_dollars = float(
        st.number_input(
            "Max loss per trade",
            min_value=1.0,
            max_value=10000.0,
            value=25.0,
            step=5.0,
            format="$%.0f",
        )
    )
    st.caption("Technical: position risk budget")

if strategy is not None:
    optimized_for = str(strategy.get("optimized_for_symbol") or "").strip().upper()
    if optimized_for and not ticker:
        ticker = optimized_for
        st.info(f"This strategy is optimized for {optimized_for}. Enter {optimized_for} above to analyze it.")
    elif optimized_for:
        st.caption(f"Selected strategy is optimized specifically for **{optimized_for}**.")

with st.expander("⚙️ Advanced settings", expanded=False):
    a1, a2, a3, a4 = st.columns(4)
    with a1:
        use_ml = st.checkbox("Use historical pattern filter", value=True)
        st.caption("Technical: Random Forest machine learning")
    with a2:
        ml_threshold_pct = st.slider("Required history score", 50, 90, 65, 1)
        st.caption("Technical: ML qualification threshold")
    with a3:
        ml_timeframe = st.selectbox("Pattern candle size", ["1Min", "5Min", "15Min"], index=1)
        st.caption("Technical: ML timeframe")
    with a4:
        ml_history_days = st.slider("Pattern history", 14, 180, 60, 1)
        st.caption("Technical: training lookback")

    p1, p2, p3 = st.columns(3)
    with p1:
        max_daily_loss = float(
            st.number_input(
                "Max simulated loss per day",
                min_value=0.0,
                max_value=100000.0,
                value=100.0,
                step=25.0,
            )
        )
    with p2:
        max_entries_per_day = int(
            st.number_input(
                "Max simulated entries per day",
                min_value=1,
                max_value=50,
                value=3,
                step=1,
            )
        )
    with p3:
        max_open_positions = int(
            st.number_input(
                "Max open simulated positions",
                min_value=1,
                max_value=20,
                value=2,
                step=1,
            )
        )
    one_entry_per_symbol_day = st.checkbox(
        "Only one simulated entry per stock each day",
        value=True,
    )

analyze_disabled = not ticker or strategy is None
analyze = st.button(
    "🔎 Analyze setup",
    type="primary",
    use_container_width=True,
    disabled=analyze_disabled,
)

if analyze:
    st.session_state["simple_ticker"] = ticker
    st.session_state.pop("simple_execution", None)
    try:
        with st.status(f"Analyzing {ticker}…", expanded=True) as status:
            st.write("Checking the saved trading setup…")
            metrics, signal, warnings = current_signal(ticker, strategy)

            ml_result = None
            ml_error = None
            if use_ml:
                st.write("Comparing this setup with historical market patterns…")
                try:
                    ml_result = score_setup(
                        market_client(),
                        ticker,
                        strategy,
                        timeframe=ml_timeframe,
                        history_days=int(ml_history_days),
                        threshold=ml_threshold_pct / 100.0,
                    )
                except AppError as error:
                    ml_error = str(error)

            status_name = str(signal.get("status") or "UNKNOWN")
            unknown = int(safe_float(signal.get("unknown"), 0) or 0)
            strategy_pass = status_name == "MATCH" and unknown == 0

            price = safe_float(metrics.get("price"))
            stop = safe_float(signal.get("suggested_stop"))
            target = safe_float(signal.get("suggested_target"))
            risk_valid = (
                price is not None
                and stop is not None
                and target is not None
                and stop < price < target
            )

            ml_pass = True
            if use_ml:
                ml_pass = bool(ml_result and ml_result.get("passes"))

            if status_name == "NO MATCH":
                decision = "SKIP"
                reason = "The saved strategy conditions are not currently satisfied."
            elif status_name in {"WATCH", "VERIFY"} or unknown > 0:
                decision = "WAIT"
                reason = "The setup is incomplete or one or more strategy conditions still need confirmation."
            elif use_ml and ml_error:
                decision = "WAIT"
                reason = "The historical pattern check could not be completed, so the combined system will not auto-trade."
            elif strategy_pass and use_ml and not ml_pass:
                decision = "WAIT"
                reason = "The strategy matched, but the historical pattern score is below your required level."
            elif strategy_pass and not risk_valid:
                decision = "WAIT"
                reason = "The setup matched, but it does not currently have a valid entry, stop, and target for automated risk control."
            elif strategy_pass and ml_pass and risk_valid:
                decision = "TRADE QUALIFIED"
                reason = "Strategy conditions, historical pattern filter, and risk structure all passed."
            else:
                decision = "WAIT"
                reason = "The combined checks are not fully qualified yet."

            st.session_state["simple_result"] = {
                "ticker": ticker,
                "strategy_id": strategy.get("id"),
                "strategy_name": strategy.get("name"),
                "approved": bool(strategy.get("approved")),
                "metrics": metrics,
                "signal": signal,
                "warnings": warnings,
                "ml_result": ml_result,
                "ml_error": ml_error,
                "use_ml": use_ml,
                "decision": decision,
                "reason": reason,
                "risk_dollars": risk_dollars,
                "risk_valid": risk_valid,
                "max_daily_loss": max_daily_loss,
                "max_entries_per_day": max_entries_per_day,
                "max_open_positions": max_open_positions,
                "one_entry_per_symbol_day": one_entry_per_symbol_day,
            }
            status.update(label="Analysis complete", state="complete", expanded=False)
    except (AppError, PaperTradeError) as error:
        st.error(str(error))

result = st.session_state.get("simple_result") or {}
if (
    result
    and strategy is not None
    and result.get("ticker") == ticker
    and result.get("strategy_id") == strategy.get("id")
):
    metrics = result.get("metrics") or {}
    signal = result.get("signal") or {}
    ml_result = result.get("ml_result")
    ml_error = result.get("ml_error")
    decision = str(result.get("decision") or "WAIT")

    icon = {
        "TRADE QUALIFIED": "🟢",
        "WAIT": "🟡",
        "SKIP": "🔴",
    }.get(decision, "⚪")
    st.markdown(
        f'<div class="decision">{icon} {decision}<br>'
        f'<span style="font-size:.95rem;font-weight:500;color:#b7c5d7">{result.get("reason")}</span></div>',
        unsafe_allow_html=True,
    )

    summary = st.columns(4)
    summary[0].metric("Current price", money(metrics.get("price"), 4))
    strategy_status = str(signal.get("status") or "UNKNOWN")
    summary[1].metric("Setup check", strategy_status)
    if ml_result:
        summary[2].metric(
            "Historical setup score",
            f'{safe_float(ml_result.get("score"), 0.0) * 100:.1f}%',
        )
    elif result.get("use_ml"):
        summary[2].metric("Historical setup score", "Unavailable")
    else:
        summary[2].metric("Historical setup score", "Off")
    summary[3].metric(
        "Rules matched",
        f'{safe_float(signal.get("score"), 0.0):.0f}%',
    )

    tech = st.columns(4)
    tech[0].caption("Technical: latest Alpaca market price")
    tech[1].caption("Technical: deterministic strategy signal")
    tech[2].caption("Technical: Random Forest ML score")
    tech[3].caption("Technical: strategy-rule match percentage")

    entry = safe_float(metrics.get("price"))
    stop = safe_float(signal.get("suggested_stop"))
    target = safe_float(signal.get("suggested_target"))
    shares = 0
    if entry is not None and stop is not None and entry > stop:
        shares = max(0, int(math.floor(float(result.get("risk_dollars") or 0) / (entry - stop))))

    trade = st.columns(4)
    trade[0].metric("Approx. entry", money(entry, 4))
    trade[1].metric("Protection level", money(stop, 4))
    trade[2].metric("Profit goal", money(target, 4))
    trade[3].metric("Approx. shares", f"{shares:,}" if shares > 0 else "—")

    trade_tech = st.columns(4)
    trade_tech[0].caption("Technical: current-price reference; market fills can differ")
    trade_tech[1].caption("Technical: stop loss")
    trade_tech[2].caption("Technical: profit target")
    trade_tech[3].caption("Technical: risk-based position sizing")

    st.markdown("### What the app is seeing")
    market_cols = st.columns(4)
    market_cols[0].metric("Today's move", pct(metrics.get("day_change_pct"), signed=True))
    rvol = safe_float(metrics.get("relative_volume"))
    market_cols[1].metric("Trading activity", f"{rvol:.2f}×" if rvol is not None else "—")
    market_cols[2].metric("Trading friction", pct(metrics.get("spread_pct"), digits=2))
    above_vwap = metrics.get("above_vwap")
    market_cols[3].metric(
        "Price vs fair-value reference",
        "Above" if above_vwap is True else "Below" if above_vwap is False else "—",
    )

    market_tech = st.columns(4)
    market_tech[0].caption("Technical: day change %")
    market_tech[1].caption("Technical: relative volume (RVOL)")
    market_tech[2].caption("Technical: bid/ask spread")
    market_tech[3].caption("Technical: VWAP")

    if ml_result:
        raw = safe_float(ml_result.get("raw_trigger_win_rate"))
        qualified = safe_float(ml_result.get("qualified_trigger_win_rate"))
        with st.expander("How the historical pattern check performed", expanded=False):
            hist = st.columns(4)
            hist[0].metric(
                "Past strategy examples",
                str(int(ml_result.get("historical_strategy_triggers") or 0)),
            )
            hist[1].metric("Past raw win rate", pct(raw))
            hist[2].metric(
                "Past examples passing filter",
                str(int(ml_result.get("historical_qualified_triggers") or 0)),
            )
            hist[3].metric("Past filtered win rate", pct(qualified))
            st.caption(
                "Technical: chronological out-of-sample comparison. These historical results do not guarantee future performance."
            )

    if ml_error:
        st.warning(f"Historical pattern check: {ml_error}")

    for warning in result.get("warnings") or []:
        st.warning(str(warning))

    checks = signal.get("checks") or []
    if checks:
        with st.expander("Why the strategy did or did not match", expanded=False):
            for item in checks:
                state = str(item.get("status") or "").upper()
                icon2 = "✅" if state == "PASS" else "❓" if state == "UNKNOWN" else "❌"
                st.markdown(
                    f"{icon2} **{item.get('label') or 'Rule'}** — "
                    f"current: `{item.get('actual')}` · needed: `{item.get('required')}`"
                )

    if mode == "Simulated trading":
        st.divider()
        st.markdown("### Simulated trade")
        st.caption("Technical: Alpaca paper trading. No real-money endpoint is used by this runner.")

        if not bool(result.get("approved")):
            st.info(
                "This strategy can be analyzed, but simulated auto-entry stays locked until you approve the strategy in Advanced Tools → Full Trading Lab."
            )
        elif decision != "TRADE QUALIFIED":
            st.info("No simulated trade is available because the combined decision is not TRADE QUALIFIED.")
        else:
            armed = st.checkbox(
                "ARM simulated trade",
                value=False,
                help="This is a separate safety confirmation before an Alpaca paper order can be submitted.",
            )
            place = st.button(
                "Place simulated trade now",
                type="primary",
                use_container_width=True,
                disabled=not armed,
            )
            if place:
                try:
                    trader = paper_client()
                    account = trader.account()
                    equity = safe_float(account.get("equity"), 0.0) or 0.0
                    if equity <= 0:
                        raise PaperTradeError("Paper account equity is unavailable.")
                    requested_risk_pct = float(result.get("risk_dollars") or 0) / equity * 100.0
                    effective_risk_pct = min(requested_risk_pct, 5.0)
                    if effective_risk_pct <= 0:
                        raise PaperTradeError("Max loss per trade must be greater than zero.")

                    execution = paper_entry(
                        strategy=strategy,
                        metrics=metrics,
                        signal=signal,
                        risk_per_trade_pct=effective_risk_pct,
                        max_position_pct=20.0,
                        max_position_dollars=0.0,
                        max_daily_loss=float(result.get("max_daily_loss") or 0),
                        max_entries_per_day=int(result.get("max_entries_per_day") or 3),
                        max_open_positions=int(result.get("max_open_positions") or 2),
                        one_entry_per_symbol_day=bool(result.get("one_entry_per_symbol_day")),
                    )
                    st.session_state["simple_execution"] = execution
                except (AppError, PaperTradeError) as error:
                    st.error(str(error))

        execution = st.session_state.get("simple_execution") or {}
        if execution:
            if execution.get("submitted"):
                st.success(str(execution.get("message")))
            else:
                st.info(str(execution.get("message")))

st.divider()
st.caption(
    "Simple Mode combines the saved strategy, historical-pattern filter, and risk controls. "
    "The technical labs remain available under Advanced tools if you want to inspect or change how any piece works."
)
