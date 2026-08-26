"""Live signal + Alpaca paper execution page for YouTube Trading Strategy Lab."""

from __future__ import annotations

from datetime import datetime, timedelta
import os
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


st.set_page_config(
    page_title="Live Strategy Runner",
    page_icon="🟢",
    layout="wide",
    initial_sidebar_state="expanded",
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


def selected_strategy_options(strategies: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in strategies:
        creator = str(item.get("creator") or "Unknown creator")
        symbol = str(item.get("optimized_for_symbol") or "").strip().upper()
        optimized = f" · {symbol} optimized" if symbol else ""
        label = f'{item.get("name", "Unnamed strategy")} — {creator}{optimized}'
        if label in result:
            label = f'{label} [{str(item.get("id", ""))[:6]}]'
        result[label] = item
    return result


def build_store() -> StrategyStore:
    backup_repository = setting("GITHUB_BACKUP_REPOSITORY")
    backup_token = setting("GITHUB_BACKUP_TOKEN")
    cloud_backup = None
    if backup_repository and backup_token:
        cloud_backup = GitHubCloudBackup(
            backup_repository,
            backup_token,
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


def needs_chart_candles(strategy: dict[str, Any]) -> bool:
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    return any(
        rules.get(field_name) is not None and rules.get(field_name) is not False
        for field_name in (
            "vwap_reclaim",
            "breakout_lookback_bars",
            "opening_range_minutes",
            "volume_surge_ratio",
            "minimum_green_bars",
        )
    )


def current_strategy_signal(symbol: str, strategy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    clean = parse_symbols(symbol)
    if len(clean) != 1:
        raise AppError("Enter exactly one valid ticker for the live runner.")
    ticker = clean[0]
    optimized_symbol = str(strategy.get("optimized_for_symbol") or "").strip().upper()
    if optimized_symbol and optimized_symbol != ticker:
        raise AppError(
            f'This saved optimized strategy is locked to {optimized_symbol}. '
            f"Choose {optimized_symbol} or select a different strategy."
        )

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
            warnings.append(f"Current chart candles unavailable; chart triggers may show VERIFY: {error}")

    signal = match_strategy(enriched, strategy)
    return metrics, signal, warnings


def is_long_strategy(strategy: dict[str, Any]) -> bool:
    direction = str(strategy.get("direction") or "").strip().lower()
    if "short" in direction and "long" not in direction:
        return False
    return "long" in direction or direction in {"bullish", "buy"}


def session_start_iso() -> str:
    now_et = utc_now().astimezone(ET)
    start = datetime.combine(now_et.date(), datetime.min.time(), tzinfo=ET)
    return isoformat_utc(start)


def paper_entry_decision(
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
    if signal.get("status") != "MATCH":
        return {"submitted": False, "message": f'No paper order: signal status is {signal.get("status", "UNKNOWN")}.'}
    if int(safe_float(signal.get("unknown"), 0) or 0) > 0:
        return {"submitted": False, "message": "No paper order: at least one required rule still needs chart verification."}
    if not is_long_strategy(strategy):
        return {"submitted": False, "message": "Paper auto-entry currently supports approved LONG strategies only."}

    price = safe_float(metrics.get("price"))
    stop = safe_float(signal.get("suggested_stop"))
    target = safe_float(signal.get("suggested_target"))
    if price is None or stop is None or target is None or stop >= price or target <= price:
        return {
            "submitted": False,
            "message": "No paper order: this strategy did not produce a valid stop below price and target above price.",
        }

    trader = paper_client()
    account = trader.account()
    clock = trader.clock()
    if account.get("trading_blocked"):
        return {"submitted": False, "message": "No paper order: Alpaca reports trading is blocked on this paper account."}
    if not bool(clock.get("is_open")):
        return {
            "submitted": False,
            "message": "No paper order: the U.S. stock market is closed. Stale signals are not queued for the next session.",
        }

    equity = safe_float(account.get("equity"), 0.0) or 0.0
    day_pnl = daily_account_pnl(account)
    if max_daily_loss > 0 and day_pnl <= -abs(max_daily_loss):
        return {
            "submitted": False,
            "message": f"No paper order: daily paper P/L {money(day_pnl)} reached the {money(max_daily_loss)} loss limit.",
        }

    positions = trader.positions()
    if any(str(item.get("symbol") or "").upper() == ticker for item in positions):
        return {"submitted": False, "message": f"No paper order: a {ticker} paper position is already open."}
    if len(positions) >= int(max_open_positions):
        return {
            "submitted": False,
            "message": f"No paper order: {len(positions)} positions are already open (limit {max_open_positions}).",
        }

    open_orders = trader.orders(status="open", limit=500)
    if any(
        str(item.get("symbol") or "").upper() == ticker
        and str(item.get("side") or "").lower() == "buy"
        for item in open_orders
    ):
        return {"submitted": False, "message": f"No paper order: an open {ticker} buy order already exists."}

    today_orders = trader.orders(status="all", after=session_start_iso(), limit=500)
    filled_entries = [
        item
        for item in today_orders
        if str(item.get("side") or "").lower() == "buy" and str(item.get("status") or "").lower() == "filled"
    ]
    if len(filled_entries) >= int(max_entries_per_day):
        return {
            "submitted": False,
            "message": f"No paper order: today's filled-entry limit ({max_entries_per_day}) has been reached.",
        }

    if one_entry_per_symbol_day:
        prefix = f"ytlab-{ticker.lower()}-"
        if any(str(item.get("client_order_id") or "").lower().startswith(prefix) for item in today_orders):
            return {
                "submitted": False,
                "message": f"No paper order: the runner already submitted a {ticker} entry today.",
            }

    qty = position_size_from_risk(
        equity=equity,
        price=price,
        stop_price=stop,
        risk_per_trade_pct=risk_per_trade_pct,
        max_position_pct=max_position_pct,
        max_position_dollars=max_position_dollars if max_position_dollars > 0 else None,
    )
    if qty < 1:
        return {
            "submitted": False,
            "message": "No paper order: the risk limits allow less than one whole share at this stop distance.",
        }

    strategy_id = re.sub(r"[^A-Za-z0-9]", "", str(strategy.get("id") or "strategy"))[:10] or "strategy"
    timestamp = utc_now().strftime("%Y%m%d%H%M")
    client_order_id = f"ytlab-{ticker.lower()}-{strategy_id}-{timestamp}"
    order = trader.submit_bracket_market_order(
        symbol=ticker,
        qty=qty,
        stop_price=stop,
        target_price=target,
        client_order_id=client_order_id,
    )
    return {
        "submitted": True,
        "message": (
            f"Submitted Alpaca PAPER bracket order: {qty} {ticker} share(s), "
            f"stop {money(stop)}, target {money(target)}."
        ),
        "order": order,
        "qty": qty,
        "day_pnl": day_pnl,
        "equity": equity,
    }


def recent_order_rows(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for order in orders:
        rows.append(
            {
                "Submitted": local_timestamp(order.get("submitted_at")),
                "Ticker": order.get("symbol"),
                "Side": str(order.get("side") or "").upper(),
                "Qty": order.get("qty"),
                "Type": order.get("type"),
                "Status": order.get("status"),
                "Filled": order.get("filled_qty"),
                "Avg fill": money(order.get("filled_avg_price"), 4),
                "Client ID": order.get("client_order_id"),
            }
        )
    return rows


st.markdown(
    """
    <style>
    .runner-hero {padding:22px 24px;border:1px solid #263854;border-radius:18px;
      background:linear-gradient(125deg,#14233a,#0d1524 68%,#18213b);margin-bottom:16px}
    .runner-title {font-size:31px;font-weight:900;letter-spacing:-.03em}
    .runner-sub {color:#a9b9cf;margin-top:7px}
    </style>
    <div class="runner-hero">
      <div class="runner-title">🟢 Live Strategy Runner</div>
      <div class="runner-sub">
        Apply one approved YouTube Trading Lab strategy to current Alpaca data.
        Signal Only never places an order. Paper Auto can submit bracket orders only to Alpaca's paper endpoint.
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
    st.stop()

approved = [item for item in library.get("strategies", []) if item.get("approved")]
market_ready = bool(setting("ALPACA_API_KEY") and setting("ALPACA_SECRET_KEY"))
paper_ready = bool(
    (setting("ALPACA_PAPER_API_KEY") or setting("ALPACA_API_KEY"))
    and (setting("ALPACA_PAPER_SECRET_KEY") or setting("ALPACA_SECRET_KEY"))
)

with st.sidebar:
    st.markdown("### Runner connections")
    st.success("Alpaca market data connected") if market_ready else st.error("Alpaca market-data credentials needed")
    if paper_ready:
        st.success("Paper-trading credentials available")
    else:
        st.warning("Paper-trading credentials needed for Paper Auto")
    st.caption("Paper execution is hard-coded to Alpaca's paper API endpoint.")
    st.divider()
    st.markdown("### Optional separate paper keys")
    st.caption(
        "If your existing ALPACA_API_KEY / ALPACA_SECRET_KEY already belong to your Alpaca paper account, "
        "you do not need anything new. Otherwise add:"
    )
    st.code(
        'ALPACA_PAPER_API_KEY="your_paper_key"\n'
        'ALPACA_PAPER_SECRET_KEY="your_paper_secret"',
        language="toml",
    )
    st.warning("There is intentionally no Live Trading mode on this page.")

if not approved:
    st.warning(
        "No approved strategies are available. In the main Trading Lab, open Strategy library and approve "
        "the strategy you want this runner to use."
    )
    st.stop()

options = selected_strategy_options(approved)
strategy_label = st.selectbox("Approved strategy", list(options), key="runner_strategy")
strategy = options[strategy_label]
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

strategy_cols = st.columns([1.2, 1, 1])
ticker = strategy_cols[0].text_input(
    "Ticker to watch",
    value=optimized_symbol or "",
    placeholder="SDOT",
    disabled=bool(optimized_symbol),
    help="Stock-optimized strategies are locked to the ticker they were optimized for.",
).strip().upper()
mode = strategy_cols[1].radio(
    "Runner mode",
    ["Signal only", "Alpaca paper auto-entry"],
    horizontal=False,
)
strategy_cols[2].metric("Preferred candle interval", preferred_timeframe)
strategy_cols[2].caption(
    f'Direction: {strategy.get("direction") or "Unclear"} · '
    f'Optimized ticker: {optimized_symbol or "Any"}'
)

st.caption(
    "This first version evaluates a fresh live signal whenever you press Refresh below. "
    "It does not keep trading after the Streamlit page/server stops."
)

st.markdown("### Risk controls")
defaults_cash = safe_float(profile.get("starting_cash"), 10_000.0) or 10_000.0
risk_default = max(0.05, min(5.0, safe_float(profile.get("risk_per_trade_pct"), 0.5) or 0.5))
position_default = max(1.0, min(100.0, safe_float(profile.get("max_position_pct"), 20.0) or 20.0))
risk_cols = st.columns(4)
risk_per_trade_pct = risk_cols[0].number_input(
    "Max risk per trade (%)",
    min_value=0.05,
    max_value=5.0,
    value=float(risk_default),
    step=0.05,
)
max_position_pct = risk_cols[1].number_input(
    "Max position (% of paper equity)",
    min_value=1.0,
    max_value=100.0,
    value=float(position_default),
    step=1.0,
)
max_position_dollars = risk_cols[2].number_input(
    "Extra dollar cap ($, 0 = off)",
    min_value=0.0,
    value=0.0,
    step=100.0,
)
max_daily_loss = risk_cols[3].number_input(
    "Max paper daily loss ($)",
    min_value=1.0,
    value=float(max(25.0, round(defaults_cash * 0.02, 2))),
    step=25.0,
)

limit_cols = st.columns(3)
max_entries_per_day = int(
    limit_cols[0].number_input("Max filled entries / day", min_value=1, max_value=50, value=5, step=1)
)
max_open_positions = int(
    limit_cols[1].number_input("Max open paper positions", min_value=1, max_value=50, value=3, step=1)
)
one_entry_per_symbol_day = limit_cols[2].checkbox(
    "Only one runner entry per ticker/day",
    value=True,
    help="Helps prevent repeated entries while the same MATCH signal remains true.",
)

armed = False
if mode == "Alpaca paper auto-entry":
    if not paper_ready:
        st.error("Paper Auto needs Alpaca paper-account credentials.")
    if not is_long_strategy(strategy):
        st.warning("Paper Auto currently supports LONG strategies only. Signal Only still works for inspection.")
    armed = st.checkbox(
        "ARM PAPER AUTO-ENTRY",
        value=False,
        help=(
            "When armed, pressing Refresh can submit a paper bracket order if every rule passes and every "
            "risk safeguard allows it. This cannot submit a live-money order."
        ),
    )
    if armed:
        st.warning("ARMED for PAPER trading. A MATCH on Refresh can place a simulated Alpaca order.")

refresh = st.button(
    "Refresh live signal",
    type="primary",
    use_container_width=True,
    disabled=not market_ready or not bool(ticker),
)

if refresh:
    try:
        with st.spinner(f"Checking {ticker} against {strategy.get('name') or 'the selected strategy'}…"):
            metrics, signal, warnings = current_strategy_signal(ticker, strategy)
        st.session_state["runner_snapshot"] = {
            "checked_at": isoformat_utc(utc_now()),
            "strategy_id": strategy.get("id"),
            "ticker": ticker,
            "metrics": metrics,
            "signal": signal,
            "warnings": warnings,
        }
        st.session_state.pop("runner_execution", None)
        if mode == "Alpaca paper auto-entry" and armed and paper_ready:
            execution = paper_entry_decision(
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
            st.session_state["runner_execution"] = execution
    except (AppError, PaperTradeError) as error:
        st.error(str(error))

snapshot = st.session_state.get("runner_snapshot") or {}
if snapshot and snapshot.get("strategy_id") == strategy.get("id") and snapshot.get("ticker") == ticker:
    metrics = snapshot.get("metrics") or {}
    signal = snapshot.get("signal") or {}
    status = str(signal.get("status") or "UNKNOWN")
    status_icon = {"MATCH": "🟢", "VERIFY": "🔵", "WATCH": "🟠", "NO MATCH": "🔴"}.get(status, "⚪")

    st.markdown("### Current live decision")
    cards = st.columns(5)
    cards[0].metric("Signal", f"{status_icon} {status}")
    cards[1].metric("Rule match", f'{safe_float(signal.get("score"), 0.0):.0f}%')
    cards[2].metric("Current price", money(metrics.get("price"), 4))
    cards[3].metric("Strategy stop", money(signal.get("suggested_stop"), 4))
    cards[4].metric("Strategy target", money(signal.get("suggested_target"), 4))
    st.caption(
        f'Checked {local_timestamp(snapshot.get("checked_at"))} · '
        f'{int(safe_float(signal.get("passed"), 0) or 0)} rules passed · '
        f'{int(safe_float(signal.get("unknown"), 0) or 0)} need verification'
    )

    detail_cols = st.columns(4)
    detail_cols[0].metric("Today", percent(metrics.get("day_change_pct"), signed=True))
    rvol = safe_float(metrics.get("relative_volume"))
    detail_cols[1].metric("Relative volume", f"{rvol:.2f}x" if rvol is not None else "—")
    detail_cols[2].metric("Spread", percent(metrics.get("spread_pct")))
    detail_cols[3].metric("VWAP", "Above" if metrics.get("above_vwap") else "Below / unavailable")

    for warning in snapshot.get("warnings") or []:
        st.warning(str(warning))

    checks = [
        {
            "Rule": item.get("label"),
            "Actual": item.get("actual"),
            "Required": item.get("required"),
            "Result": str(item.get("status") or "").upper(),
        }
        for item in signal.get("checks") or []
    ]
    if checks:
        with st.expander("See every strategy rule", expanded=status in {"VERIFY", "NO MATCH"}):
            st.dataframe(pd.DataFrame(checks), hide_index=True, use_container_width=True)

execution = st.session_state.get("runner_execution")
if execution:
    if execution.get("submitted"):
        st.success(execution.get("message"))
        order = execution.get("order") or {}
        st.caption(
            f'Paper order ID: {order.get("id", "—")} · Status: {order.get("status", "accepted")} · '
            f'Paper equity when checked: {money(execution.get("equity"))} · '
            f'Day P/L: {money(execution.get("day_pnl"))}'
        )
    else:
        st.info(execution.get("message"))

st.divider()
st.markdown("### Alpaca paper account")
st.caption("These controls affect only the Alpaca PAPER account associated with the configured paper credentials.")

account_cols = st.columns(3)
if account_cols[0].button("Refresh paper account", use_container_width=True, disabled=not paper_ready):
    try:
        trader = paper_client()
        account = trader.account()
        positions = trader.positions()
        recent_orders = trader.orders(status="all", limit=50)
        st.session_state["runner_paper_account"] = account
        st.session_state["runner_paper_positions"] = positions
        st.session_state["runner_paper_orders"] = recent_orders
        st.session_state["runner_paper_checked_at"] = isoformat_utc(utc_now())
    except PaperTradeError as error:
        st.error(str(error))

if account_cols[1].button("Cancel all open PAPER orders", use_container_width=True, disabled=not paper_ready):
    try:
        paper_client().cancel_all_orders()
        st.success("Canceled open Alpaca PAPER orders.")
        st.session_state.pop("runner_paper_orders", None)
    except PaperTradeError as error:
        st.error(str(error))

paper_positions = st.session_state.get("runner_paper_positions") or []
position_symbols = [str(item.get("symbol") or "").upper() for item in paper_positions if item.get("symbol")]
close_symbol = account_cols[2].selectbox(
    "Paper position to close",
    ["—"] + position_symbols,
    key="runner_close_symbol",
)
if close_symbol != "—":
    if st.button(f"Close {close_symbol} PAPER position", use_container_width=True):
        try:
            paper_client().close_position(close_symbol)
            st.success(f"Submitted a PAPER close request for {close_symbol}.")
            st.session_state.pop("runner_paper_positions", None)
        except PaperTradeError as error:
            st.error(str(error))

account = st.session_state.get("runner_paper_account") or {}
if account:
    account_metrics = st.columns(5)
    account_metrics[0].metric("Paper equity", money(account.get("equity")))
    account_metrics[1].metric("Buying power", money(account.get("buying_power")))
    day_pnl = daily_account_pnl(account)
    account_metrics[2].metric("Today P/L", money(day_pnl))
    account_metrics[3].metric("Open positions", str(len(paper_positions)))
    account_metrics[4].metric("Trading blocked", "YES" if account.get("trading_blocked") else "No")
    st.caption(f'Paper account last checked: {local_timestamp(st.session_state.get("runner_paper_checked_at"))}')

    if paper_positions:
        position_rows = []
        for item in paper_positions:
            qty = safe_float(item.get("qty"), 0.0) or 0.0
            current = safe_float(item.get("current_price"))
            avg_entry = safe_float(item.get("avg_entry_price"))
            position_rows.append(
                {
                    "Ticker": item.get("symbol"),
                    "Qty": qty,
                    "Avg entry": money(avg_entry, 4),
                    "Current": money(current, 4),
                    "Market value": money(item.get("market_value")),
                    "Unrealized P/L": money(item.get("unrealized_pl")),
                    "Unrealized %": percent((safe_float(item.get("unrealized_plpc"), 0.0) or 0.0) * 100.0, signed=True),
                }
            )
        st.dataframe(pd.DataFrame(position_rows), hide_index=True, use_container_width=True)

orders = st.session_state.get("runner_paper_orders") or []
if orders:
    st.markdown("#### Recent paper order log")
    st.dataframe(pd.DataFrame(recent_order_rows(orders)), hide_index=True, use_container_width=True)

st.info(
    "Safety design: this page uses live Alpaca market data but its execution client is permanently pointed at "
    "Alpaca's paper-api endpoint. It does not contain a live brokerage URL or a Live Trading switch."
)
