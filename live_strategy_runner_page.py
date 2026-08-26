"""Clean Live Strategy Runner UI for YouTube Trading Strategy Lab.

Signal inspection works with any saved strategy. Alpaca paper auto-entry remains
restricted to strategies the user explicitly approved in the main Trading Lab.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import os
import re
from typing import Any

import pandas as pd
import streamlit as st

from alpaca_paper_trader import (
    AlpacaPaperTrader,
    PaperTradeError,
    daily_account_pnl,
    position_size_from_risk,
)
from youtube_strategy_engine import (
    DEFAULT_GITHUB_BACKUP_PATH,
    ET,
    AlpacaMarketData,
    AppError,
    GitHubCloudBackup,
    StrategyStore,
    average_completed_daily_volume,
    chart_trigger_checks,
    isoformat_utc,
    match_strategy,
    normalize_machine_rules,
    parse_symbols,
    safe_float,
    snapshot_metrics,
    utc_now,
)


def setting(name: str, default: str = "") -> str:
    try:
        if name in st.secrets and str(st.secrets[name]).strip():
            return str(st.secrets[name]).strip()
    except (FileNotFoundError, KeyError, RuntimeError, AttributeError):
        pass
    return str(os.environ.get(name, default)).strip()


def money(value: Any, decimals: int = 2) -> str:
    number = safe_float(value)
    return f"${number:,.{decimals}f}" if number is not None else "—"


def percent(value: Any, signed: bool = False) -> str:
    number = safe_float(value)
    if number is None:
        return "—"
    return f"{number:+.2f}%" if signed else f"{number:.2f}%"


def local_timestamp(raw: str | None) -> str:
    if not raw:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return parsed.astimezone(ET).strftime("%b %d, %Y · %I:%M:%S %p ET")
    except (TypeError, ValueError):
        return str(raw)


def build_store() -> StrategyStore:
    repository = setting("GITHUB_BACKUP_REPOSITORY")
    token = setting("GITHUB_BACKUP_TOKEN")
    cloud_backup = None
    if repository and token:
        cloud_backup = GitHubCloudBackup(
            repository,
            token,
            branch=setting("GITHUB_BACKUP_BRANCH"),
            path=setting("GITHUB_BACKUP_PATH", DEFAULT_GITHUB_BACKUP_PATH),
        )
    return StrategyStore(cloud_backup=cloud_backup)


def market_client() -> AlpacaMarketData:
    return AlpacaMarketData(
        setting("ALPACA_API_KEY"),
        setting("ALPACA_SECRET_KEY"),
        setting("ALPACA_LIVE_FEED", "iex"),
        setting("ALPACA_HISTORICAL_FEED", "sip"),
    )


def paper_client() -> AlpacaPaperTrader:
    api_key = setting("ALPACA_PAPER_API_KEY") or setting("ALPACA_API_KEY")
    secret_key = setting("ALPACA_PAPER_SECRET_KEY") or setting("ALPACA_SECRET_KEY")
    return AlpacaPaperTrader(api_key, secret_key)


def strategy_options(strategies: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    options: dict[str, dict[str, Any]] = {}
    for strategy in strategies:
        name = str(strategy.get("name") or "Unnamed strategy")
        optimized_symbol = str(strategy.get("optimized_for_symbol") or "").strip().upper()
        approval = "✓ APPROVED" if strategy.get("approved") else "REVIEW ONLY"
        suffix = f" · {optimized_symbol}" if optimized_symbol else ""
        label = f"{name}{suffix} · {approval}"
        if label in options:
            label += f" [{str(strategy.get('id') or '')[:6]}]"
        options[label] = strategy
    return options


def needs_chart_candles(strategy: dict[str, Any]) -> bool:
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    return any(
        rules.get(name) is not None and rules.get(name) is not False
        for name in (
            "vwap_reclaim",
            "breakout_lookback_bars",
            "opening_range_minutes",
            "volume_surge_ratio",
            "minimum_green_bars",
        )
    )


def current_signal(symbol: str, strategy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    parsed = parse_symbols(symbol)
    if len(parsed) != 1:
        raise AppError("Enter exactly one valid ticker.")
    ticker = parsed[0]
    optimized_symbol = str(strategy.get("optimized_for_symbol") or "").strip().upper()
    if optimized_symbol and optimized_symbol != ticker:
        raise AppError(f"This optimized strategy is locked to {optimized_symbol}.")

    market = market_client()
    snapshot = market.snapshots([ticker]).get(ticker)
    if not snapshot:
        raise AppError(f"No current Alpaca snapshot was available for {ticker}.")

    warnings: list[str] = []
    historical_end = utc_now() - timedelta(
        minutes=16 if market.historical_feed == "sip" and market.live_feed != "sip" else 1
    )
    average_volume = None
    try:
        daily = market.bars(
            [ticker],
            start=historical_end - timedelta(days=40),
            end=historical_end,
            timeframe="1Day",
            max_pages=5,
        )
        average_volume = average_completed_daily_volume(daily.get(ticker, []))
    except AppError as error:
        warnings.append(f"Relative-volume baseline unavailable: {error}")

    metrics = snapshot_metrics(ticker, snapshot, average_daily_volume=average_volume)
    if metrics is None:
        raise AppError(f"Alpaca returned an incomplete snapshot for {ticker}.")

    enriched = dict(metrics)
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    if rules.get("catalyst_required"):
        try:
            recent_news = market.news([ticker], hours=24)
            enriched["has_catalyst"] = bool(recent_news.get(ticker))
        except AppError as error:
            enriched["has_catalyst"] = None
            warnings.append(f"Recent-news check unavailable; catalyst rule needs verification: {error}")

    if needs_chart_candles(strategy):
        now_et = utc_now().astimezone(ET)
        session_day = now_et.date()
        if now_et.hour * 60 + now_et.minute < 9 * 60 + 30:
            session_day -= timedelta(days=1)
        while session_day.weekday() >= 5:
            session_day -= timedelta(days=1)
        session_start = datetime.combine(session_day, datetime.min.time(), tzinfo=ET).replace(hour=9, minute=30)
        try:
            intraday = market.bars(
                [ticker],
                start=session_start,
                end=utc_now(),
                timeframe="1Min",
                feed=market.live_feed,
                max_pages=8,
            )
            if intraday.get(ticker):
                enriched["chart_checks"] = chart_trigger_checks(intraday[ticker], strategy)
        except AppError as error:
            warnings.append(f"Intraday chart data unavailable; chart-specific rules may show VERIFY: {error}")

    return metrics, match_strategy(enriched, strategy), warnings


def is_long_strategy(strategy: dict[str, Any]) -> bool:
    direction = str(strategy.get("direction") or "").strip().lower()
    if "short" in direction and "long" not in direction:
        return False
    return "long" in direction or direction in {"bullish", "buy"}


def session_start_iso() -> str:
    now_et = utc_now().astimezone(ET)
    session_start = datetime.combine(now_et.date(), datetime.min.time(), tzinfo=ET)
    return isoformat_utc(session_start)


def paper_entry(
    *,
    strategy: dict[str, Any],
    metrics: dict[str, Any],
    signal: dict[str, Any],
    risk_per_trade_pct: float,
    max_position_pct: float,
    max_position_dollars: float,
    max_daily_loss: float,
    max_entries_per_day: int,
    max_open_positions: int,
    one_entry_per_symbol_day: bool,
) -> dict[str, Any]:
    ticker = str(metrics.get("symbol") or "").strip().upper()
    if not strategy.get("approved"):
        return {"submitted": False, "message": "Paper Auto is locked until this strategy is explicitly approved in the main Trading Lab."}
    if signal.get("status") != "MATCH":
        return {"submitted": False, "message": f"No paper order: live signal is {signal.get('status', 'UNKNOWN')}."}
    if int(safe_float(signal.get("unknown"), 0) or 0) > 0:
        return {"submitted": False, "message": "No paper order: at least one rule still needs verification."}
    if not is_long_strategy(strategy):
        return {"submitted": False, "message": "Paper Auto currently supports LONG strategies only."}

    price = safe_float(metrics.get("price"))
    stop = safe_float(signal.get("suggested_stop"))
    target = safe_float(signal.get("suggested_target"))
    if price is None or stop is None or target is None or stop >= price or target <= price:
        return {"submitted": False, "message": "No paper order: the strategy did not produce a valid stop and target."}

    trader = paper_client()
    account = trader.account()
    clock = trader.clock()
    if account.get("trading_blocked"):
        return {"submitted": False, "message": "No paper order: Alpaca reports that paper trading is blocked."}
    if not bool(clock.get("is_open")):
        return {"submitted": False, "message": "No paper order: the U.S. stock market is closed."}

    equity = safe_float(account.get("equity"), 0.0) or 0.0
    day_pnl = daily_account_pnl(account)
    if max_daily_loss > 0 and day_pnl <= -abs(max_daily_loss):
        return {"submitted": False, "message": f"No paper order: daily paper P/L {money(day_pnl)} reached your loss limit."}

    positions = trader.positions()
    if any(str(item.get("symbol") or "").upper() == ticker for item in positions):
        return {"submitted": False, "message": f"No paper order: a {ticker} paper position is already open."}
    if len(positions) >= max_open_positions:
        return {"submitted": False, "message": f"No paper order: open-position limit ({max_open_positions}) reached."}

    open_orders = trader.orders(status="open", limit=500)
    if any(str(item.get("symbol") or "").upper() == ticker and str(item.get("side") or "").lower() == "buy" for item in open_orders):
        return {"submitted": False, "message": f"No paper order: an open {ticker} buy order already exists."}

    today_orders = trader.orders(status="all", after=session_start_iso(), limit=500)
    filled_entries = [
        item for item in today_orders
        if str(item.get("side") or "").lower() == "buy" and str(item.get("status") or "").lower() == "filled"
    ]
    if len(filled_entries) >= max_entries_per_day:
        return {"submitted": False, "message": f"No paper order: filled-entry limit ({max_entries_per_day}) reached."}

    if one_entry_per_symbol_day:
        prefix = f"ytlab-{ticker.lower()}-"
        if any(str(item.get("client_order_id") or "").lower().startswith(prefix) for item in today_orders):
            return {"submitted": False, "message": f"No paper order: the runner already submitted {ticker} today."}

    qty = position_size_from_risk(
        equity=equity,
        price=price,
        stop_price=stop,
        risk_per_trade_pct=risk_per_trade_pct,
        max_position_pct=max_position_pct,
        max_position_dollars=max_position_dollars if max_position_dollars > 0 else None,
    )
    if qty < 1:
        return {"submitted": False, "message": "No paper order: risk limits allow less than one whole share."}

    strategy_id = re.sub(r"[^A-Za-z0-9]", "", str(strategy.get("id") or "strategy"))[:10] or "strategy"
    client_order_id = f"ytlab-{ticker.lower()}-{strategy_id}-{utc_now().strftime('%Y%m%d%H%M')}"
    order = trader.submit_bracket_market_order(
        symbol=ticker,
        qty=qty,
        stop_price=stop,
        target_price=target,
        client_order_id=client_order_id,
    )
    return {
        "submitted": True,
        "message": f"Submitted PAPER bracket order: {qty} {ticker} share(s), stop {money(stop)}, target {money(target)}.",
        "order": order,
        "equity": equity,
        "day_pnl": day_pnl,
    }


def render() -> None:
    st.markdown(
        """
        <style>
        .runner-hero {padding:22px 24px;border:1px solid #263854;border-radius:18px;
          background:linear-gradient(125deg,#14233a,#0d1524 68%,#18213b);margin-bottom:16px}
        .runner-title {font-size:31px;font-weight:900;letter-spacing:-.03em}
        .runner-sub {color:#a9b9cf;margin-top:7px;line-height:1.6}
        </style>
        <div class="runner-hero">
          <div class="runner-title">🟢 Live Strategy Runner</div>
          <div class="runner-sub">
            Apply a saved Trading Lab strategy to current Alpaca data. Signal Only is inspection-only.
            Paper Auto can submit bracket orders only to Alpaca's paper endpoint and only after strategy approval.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    try:
        store = build_store()
        library = store.load()
    except AppError as error:
        st.error(str(error))
        return

    strategies = list(library.get("strategies") or [])
    market_ready = bool(setting("ALPACA_API_KEY") and setting("ALPACA_SECRET_KEY"))
    paper_ready = bool(
        (setting("ALPACA_PAPER_API_KEY") or setting("ALPACA_API_KEY"))
        and (setting("ALPACA_PAPER_SECRET_KEY") or setting("ALPACA_SECRET_KEY"))
    )

    with st.sidebar:
        st.markdown("### Runner connections")
        if market_ready:
            st.success("Alpaca market data connected")
        else:
            st.error("Alpaca market-data credentials needed")
        if paper_ready:
            st.success("Paper-trading credentials available")
        else:
            st.warning("Paper-trading credentials needed for Paper Auto")
        st.caption("Paper execution is hard-coded to Alpaca's paper API endpoint.")
        st.divider()
        if st.button("← Back to main Trading Lab", width="stretch"):
            st.switch_page("youtube_strategy_app.py")

    if not strategies:
        st.warning("No saved strategies were found yet. Create or save a strategy in the main Trading Lab first.")
        if st.button("Open main Trading Lab", type="primary", width="stretch"):
            st.switch_page("youtube_strategy_app.py")
        return

    approved_count = sum(bool(item.get("approved")) for item in strategies)
    summary_cols = st.columns(3)
    summary_cols[0].metric("Saved strategies", str(len(strategies)))
    summary_cols[1].metric("Approved for Paper Auto", str(approved_count))
    summary_cols[2].metric("Live market data", "Connected" if market_ready else "Not connected")

    options = strategy_options(strategies)
    selected_label = st.selectbox("Strategy to run", list(options), key="runner_strategy_v2")
    strategy = options[selected_label]
    approved = bool(strategy.get("approved"))
    optimized_symbol = str(strategy.get("optimized_for_symbol") or "").strip().upper()
    profile = (
        strategy.get("optimized_backtest_settings")
        or (strategy.get("optimization_summary") or {}).get("optimized_backtest_settings")
        or {}
    )
    preferred_timeframe = str(
        strategy.get("preferred_timeframe")
        or (strategy.get("optimization_summary") or {}).get("timeframe")
        or "5Min"
    )

    if approved:
        st.success("This strategy is approved for Paper Auto if all live rules and risk checks pass.")
    else:
        st.info("This strategy is not approved yet. You can still use Signal Only. Paper Auto remains locked.")

    controls = st.columns([1.25, 1, 1])
    ticker = controls[0].text_input(
        "Ticker to watch",
        value=optimized_symbol or "",
        placeholder="SDOT",
        disabled=bool(optimized_symbol),
        help="Stock-optimized strategies stay locked to the stock they were optimized for.",
    ).strip().upper()
    available_modes = ["Signal only", "Alpaca paper auto-entry"]
    mode = controls[1].radio(
        "Runner mode",
        available_modes,
        index=0,
        disabled=False,
    )
    controls[2].metric("Preferred candle interval", preferred_timeframe)
    controls[2].caption(f'Direction: {strategy.get("direction") or "Unclear"} · Optimized ticker: {optimized_symbol or "Any"}')

    if mode == "Alpaca paper auto-entry" and not approved:
        st.warning("Paper Auto is locked for this strategy. Approve it in Strategy library first, or switch to Signal Only.")
    if mode == "Alpaca paper auto-entry" and not paper_ready:
        st.error("Paper Auto needs Alpaca paper-account credentials.")

    st.markdown("### Risk controls")
    default_cash = safe_float(profile.get("starting_cash"), 10_000.0) or 10_000.0
    risk_default = max(0.05, min(5.0, safe_float(profile.get("risk_per_trade_pct"), 0.5) or 0.5))
    position_default = max(1.0, min(100.0, safe_float(profile.get("max_position_pct"), 20.0) or 20.0))
    risk_cols = st.columns(4)
    risk_per_trade_pct = risk_cols[0].number_input("Max risk / trade (%)", 0.05, 5.0, float(risk_default), 0.05)
    max_position_pct = risk_cols[1].number_input("Max position (% equity)", 1.0, 100.0, float(position_default), 1.0)
    max_position_dollars = risk_cols[2].number_input("Extra dollar cap ($, 0=off)", min_value=0.0, value=0.0, step=100.0)
    max_daily_loss = risk_cols[3].number_input(
        "Max paper daily loss ($)",
        min_value=1.0,
        value=float(max(25.0, round(default_cash * 0.02, 2))),
        step=25.0,
    )

    limits = st.columns(3)
    max_entries_per_day = int(limits[0].number_input("Max filled entries / day", 1, 50, 5, 1))
    max_open_positions = int(limits[1].number_input("Max open paper positions", 1, 50, 3, 1))
    one_entry_per_symbol_day = limits[2].checkbox("One runner entry / ticker / day", value=True)

    armed = False
    if mode == "Alpaca paper auto-entry":
        armed = st.checkbox(
            "ARM PAPER AUTO-ENTRY",
            value=False,
            disabled=not approved or not paper_ready or not is_long_strategy(strategy),
            help="When armed, Refresh can submit a simulated Alpaca bracket order after every safeguard passes.",
        )
        if armed:
            st.warning("PAPER AUTO-ENTRY IS ARMED. A full MATCH on Refresh can submit a simulated order.")

    refresh = st.button(
        "Refresh live signal",
        type="primary",
        width="stretch",
        disabled=not market_ready or not bool(ticker),
    )

    if refresh:
        try:
            with st.spinner(f"Checking {ticker} against {strategy.get('name') or 'the selected strategy'}…"):
                metrics, signal, warnings = current_signal(ticker, strategy)
            st.session_state["runner_snapshot_v2"] = {
                "checked_at": isoformat_utc(utc_now()),
                "strategy_id": strategy.get("id"),
                "ticker": ticker,
                "metrics": metrics,
                "signal": signal,
                "warnings": warnings,
            }
            st.session_state.pop("runner_execution_v2", None)
            if mode == "Alpaca paper auto-entry" and armed:
                st.session_state["runner_execution_v2"] = paper_entry(
                    strategy=strategy,
                    metrics=metrics,
                    signal=signal,
                    risk_per_trade_pct=float(risk_per_trade_pct),
                    max_position_pct=float(max_position_pct),
                    max_position_dollars=float(max_position_dollars),
                    max_daily_loss=float(max_daily_loss),
                    max_entries_per_day=max_entries_per_day,
                    max_open_positions=max_open_positions,
                    one_entry_per_symbol_day=one_entry_per_symbol_day,
                )
        except (AppError, PaperTradeError) as error:
            st.error(str(error))

    snapshot = st.session_state.get("runner_snapshot_v2") or {}
    if snapshot and snapshot.get("strategy_id") == strategy.get("id") and snapshot.get("ticker") == ticker:
        metrics = snapshot.get("metrics") or {}
        signal = snapshot.get("signal") or {}
        status = str(signal.get("status") or "UNKNOWN")
        icon = {"MATCH": "🟢", "VERIFY": "🔵", "WATCH": "🟠", "NO MATCH": "🔴"}.get(status, "⚪")

        raw_checks = list(signal.get("checks") or [])
        passed_count = sum(str(item.get("status") or "").lower() == "pass" for item in raw_checks)
        failed_count = sum(str(item.get("status") or "").lower() == "fail" for item in raw_checks)
        unknown_count = sum(str(item.get("status") or "").lower() == "unknown" for item in raw_checks)
        total_count = len(raw_checks)

        if status == "MATCH":
            decision_label = "🟢 ENTRY MATCH"
        elif status == "VERIFY":
            decision_label = "🔵 WAIT / VERIFY"
        elif status == "WATCH":
            decision_label = "🟠 WATCH"
        else:
            decision_label = "🔴 NO ENTRY"

        st.markdown("### Current live decision")
        if status == "MATCH":
            st.success(f"ENTRY CONDITIONS MATCH — {passed_count} of {total_count} conditions passed.")
        elif failed_count > 0:
            extra = f" · {unknown_count} need verification" if unknown_count else ""
            st.error(
                f"NO ENTRY — {failed_count} of {total_count} conditions failed · "
                f"{passed_count} passed{extra}."
            )
        elif unknown_count > 0:
            st.info(
                f"WAIT / VERIFY — {passed_count} of {total_count} conditions passed · "
                f"{unknown_count} still need verification."
            )
        else:
            st.warning("NO ENTRY — this strategy has no currently measurable entry conditions to confirm.")

        st.caption(
            "A strong backtest means the strategy performed well when its entry setup occurred historically. "
            "This live decision only answers whether that setup is present right now."
        )

        cards = st.columns(5)
        cards[0].metric("Live decision", decision_label)
        cards[1].metric("Conditions passed", f"{passed_count} / {total_count}")
        cards[2].metric("Current price", money(metrics.get("price"), 4))
        cards[3].metric("Strategy stop", money(signal.get("suggested_stop"), 4))
        cards[4].metric("Strategy target", money(signal.get("suggested_target"), 4))
        st.caption(
            f'Checked {local_timestamp(snapshot.get("checked_at"))} · '
            f'Rule score {safe_float(signal.get("score"), 0.0):.0f}% · '
            "Stop/target are reference levels only; they are not an entry recommendation unless the strategy reaches a full MATCH."
        )

        details = st.columns(4)
        details[0].metric("Today", percent(metrics.get("day_change_pct"), signed=True))
        rvol = safe_float(metrics.get("relative_volume"))
        details[1].metric("Relative volume", f"{rvol:.2f}x" if rvol is not None else "—")
        details[2].metric("Spread", percent(metrics.get("spread_pct")))
        details[3].metric("VWAP", "Above" if metrics.get("above_vwap") else "Below / unavailable")

        for warning in snapshot.get("warnings") or []:
            st.warning(str(warning))

        st.markdown("#### Entry-condition checklist")
        if raw_checks:
            for item in raw_checks:
                check_status = str(item.get("status") or "").lower()
                check_icon = {"pass": "✅", "fail": "❌", "unknown": "❓"}.get(check_status, "•")
                label = str(item.get("label") or "Strategy condition")
                actual = item.get("actual")
                required = item.get("required")
                actual_text = "Unavailable" if actual is None else str(actual)
                required_text = "—" if required is None else str(required)
                st.markdown(
                    f"{check_icon} **{label}** — Current: `{actual_text}` · Required: `{required_text}`"
                )

            checks_table = [
                {
                    "Rule": item.get("label"),
                    "Current": item.get("actual"),
                    "Required": item.get("required"),
                    "Result": str(item.get("status") or "").upper(),
                }
                for item in raw_checks
            ]
            with st.expander("Technical rule table", expanded=False):
                st.dataframe(pd.DataFrame(checks_table), hide_index=True, width="stretch")
        else:
            st.caption("No measurable entry rules are currently saved for this strategy.")


    execution = st.session_state.get("runner_execution_v2") or {}
    if execution:
        if execution.get("submitted"):
            st.success(execution.get("message"))
        else:
            st.info(execution.get("message"))

    st.divider()
    st.markdown("### Alpaca paper account")
    st.caption("This section affects only Alpaca PAPER trading.")
    account_cols = st.columns(2)
    if account_cols[0].button("Refresh paper account", width="stretch", disabled=not paper_ready):
        try:
            trader = paper_client()
            st.session_state["runner_paper_account_v2"] = trader.account()
            st.session_state["runner_paper_positions_v2"] = trader.positions()
            st.session_state["runner_paper_checked_v2"] = isoformat_utc(utc_now())
        except PaperTradeError as error:
            st.error(str(error))
    if account_cols[1].button("Cancel all open PAPER orders", width="stretch", disabled=not paper_ready):
        try:
            paper_client().cancel_all_orders()
            st.success("Canceled all open PAPER orders.")
        except PaperTradeError as error:
            st.error(str(error))

    account = st.session_state.get("runner_paper_account_v2") or {}
    positions = st.session_state.get("runner_paper_positions_v2") or []
    if account:
        account_cards = st.columns(4)
        account_cards[0].metric("Paper equity", money(account.get("equity")))
        account_cards[1].metric("Buying power", money(account.get("buying_power")))
        account_cards[2].metric("Today P/L", money(daily_account_pnl(account)))
        account_cards[3].metric("Open positions", str(len(positions)))
        st.caption(f'Last checked: {local_timestamp(st.session_state.get("runner_paper_checked_v2"))}')

    if positions:
        rows = []
        for item in positions:
            rows.append(
                {
                    "Ticker": item.get("symbol"),
                    "Qty": item.get("qty"),
                    "Avg entry": money(item.get("avg_entry_price"), 4),
                    "Current": money(item.get("current_price"), 4),
                    "Market value": money(item.get("market_value")),
                    "Unrealized P/L": money(item.get("unrealized_pl")),
                }
            )
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.info(
        "This page evaluates only when you press Refresh. It does not continue running if the Streamlit page/server stops. "
        "Paper execution is permanently pointed at Alpaca's paper endpoint."
    )
