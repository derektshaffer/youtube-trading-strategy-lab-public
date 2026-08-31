"""Clean Live Strategy Runner UI for YouTube Trading Strategy Lab.

Signal inspection works with any saved strategy. Alpaca paper auto-entry remains
restricted to strategies the user explicitly approved in the main Trading Lab.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
from typing import Any

import pandas as pd
import streamlit as st

from app_access import require_app_access
from trading_app_runtime import market_client, setting
from trading_intelligence_core import paper_execution_fidelity
from trading_progress_ui import LongTaskMonitor, session_task_profiles
from alpaca_paper_trader import (
    AlpacaPaperTrader,
    PaperTradeError,
    daily_account_pnl,
    position_size_from_risk,
)
from youtube_strategy_engine import (
    DEFAULT_GITHUB_BACKUP_PATH,
    ET,
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


MAX_MARKET_DATA_AGE_SECONDS = 90.0
MAX_MARKET_DATA_FUTURE_SKEW_SECONDS = 15.0


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


DEFAULT_PRIVATE_BACKUP_REPOSITORY = "derektshaffer/derektshaffer-youtube-trading-strategy-lab"
OBSOLETE_PRIVATE_BACKUP_REPOSITORIES = {
    "derektshaffer/youtube-trading-strategy-backups",
}


def _resolved_backup_repository() -> str:
    repository = setting(
        "GITHUB_BACKUP_REPOSITORY",
        DEFAULT_PRIVATE_BACKUP_REPOSITORY,
    )
    if repository in OBSOLETE_PRIVATE_BACKUP_REPOSITORIES:
        return DEFAULT_PRIVATE_BACKUP_REPOSITORY
    return repository


def _backup_token() -> str:
    return (
        setting("GITHUB_BACKUP_TOKEN")
        or setting("GITHUB_TOKEN")
        or setting("GH_TOKEN")
    )


def build_intelligence_store() -> StrategyStore:
    """Use the same durable Intelligence Lab store as Stock Strategy Finder."""
    repository = _resolved_backup_repository()
    token = _backup_token()
    cloud_backup = None
    if repository and token:
        cloud_backup = GitHubCloudBackup(
            repository,
            token,
            branch=setting("GITHUB_BACKUP_BRANCH"),
            path=setting(
                "TRADING_INTELLIGENCE_BACKUP_PATH",
                "trading-intelligence-lab/intelligence_library.json",
            ),
        )
    directory = Path(
        os.environ.get("TRADING_INTELLIGENCE_DATA_DIR")
        or ".trading_intelligence_data"
    )
    return StrategyStore(directory=directory, cloud_backup=cloud_backup)


def merge_runner_libraries(
    legacy_library: dict[str, Any],
    intelligence_library: dict[str, Any],
) -> dict[str, Any]:
    """Expose Finder children without moving or rewriting either durable store."""
    merged = dict(legacy_library or {})
    strategies: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for library in (intelligence_library, legacy_library):
        for strategy in (library or {}).get("strategies") or []:
            if not isinstance(strategy, dict):
                continue
            strategy_id = str(strategy.get("id") or "").strip()
            if strategy_id and strategy_id in seen_ids:
                continue
            prepared = dict(strategy)
            if (
                str(prepared.get("source_type") or "").strip().casefold()
                == "stock_specific_finder"
            ):
                # Finder children have distinct optimized rules. Fail closed for
                # both new and legacy children until that exact child has its own
                # explicit approval workflow.
                prepared["approved"] = False
            strategies.append(prepared)
            if strategy_id:
                seen_ids.add(strategy_id)
    merged["strategies"] = strategies
    return merged


def load_runner_library() -> dict[str, Any]:
    """Combine legacy strategies with the current durable Finder child library."""
    legacy_library = build_store().load()
    intelligence_library = build_intelligence_store().load_latest()
    return merge_runner_libraries(legacy_library, intelligence_library)


def requested_strategy_label(
    options: dict[str, dict[str, Any]],
    strategy_id: str,
) -> str:
    """Resolve a cross-page handoff by stable strategy ID."""
    target = str(strategy_id or "").strip()
    if target:
        for label, strategy in options.items():
            if str(strategy.get("id") or "") == target:
                return label
    return ""


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
            "avwap_anchor_mode",
            "require_price_above_avwap",
            "avwap_reclaim",
            "require_avwap_rising",
            "require_avwap_pullback",
            "stop_below_avwap",
            "exit_below_avwap",
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
    market = market_client()
    snapshot = market.snapshots([ticker]).get(ticker)
    if not snapshot:
        raise AppError(f"No current Alpaca snapshot was available for {ticker}.")

    warnings: list[str] = []
    if optimized_symbol and optimized_symbol != ticker:
        warnings.append(
            f"This strategy was optimized/backtested for {optimized_symbol}, but you are evaluating {ticker}. "
            "Its historical results may not transfer to this stock."
        )
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
    return "long" in direction or direction in {"both", "bullish", "buy"}


def _market_timestamp(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def market_data_freshness(
    metrics: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: float = MAX_MARKET_DATA_AGE_SECONDS,
) -> tuple[bool, str, dict[str, float]]:
    """Require independently timestamped, recent quote and trade data."""
    current = (now or utc_now()).astimezone(timezone.utc)
    ages: dict[str, float] = {}
    for label, field in (("quote", "quote_timestamp"), ("trade", "trade_timestamp")):
        timestamp = _market_timestamp(metrics.get(field))
        if timestamp is None:
            return False, f"{label} timestamp is missing or invalid", ages
        age_seconds = (current - timestamp).total_seconds()
        ages[label] = age_seconds
        if age_seconds < -MAX_MARKET_DATA_FUTURE_SKEW_SECONDS:
            return False, f"{label} timestamp is unexpectedly in the future", ages
        if age_seconds > max_age_seconds:
            return False, f"{label} data is stale ({age_seconds:.0f} seconds old)", ages
    return True, "quote and trade timestamps are recent", ages


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
    if parse_symbols(ticker) != [ticker]:
        return {"submitted": False, "message": "No paper order: the market snapshot did not contain one valid ticker."}
    if not strategy.get("approved"):
        return {"submitted": False, "message": "Paper Auto is locked until this strategy is explicitly approved in the main Trading Lab."}
    if (
        str(strategy.get("validation_status") or "").strip().lower() != "validated"
        or not isinstance(strategy.get("validated_rules"), dict)
    ):
        return {
            "submitted": False,
            "message": (
                "Paper Auto is locked until this exact strategy has current historical "
                "validation and frozen validated rules."
            ),
        }
    if signal.get("status") != "MATCH":
        return {"submitted": False, "message": f"No paper order: live signal is {signal.get('status', 'UNKNOWN')}."}
    if int(safe_float(signal.get("unknown"), 0) or 0) > 0:
        return {"submitted": False, "message": "No paper order: at least one rule still needs verification."}
    if not is_long_strategy(strategy):
        return {"submitted": False, "message": "Paper Auto currently supports LONG strategies only."}

    execution_fidelity = paper_execution_fidelity(strategy)
    if str(execution_fidelity.get("status") or "") != "ready":
        unsupported = ", ".join(
            str(item) for item in execution_fidelity.get("unsupported_management") or []
        )
        detail = unsupported or str(execution_fidelity.get("reason") or "trade-management mismatch")
        return {
            "submitted": False,
            "message": (
                "No paper order: Paper Auto cannot yet reproduce this strategy's validated "
                f"trade management ({detail})."
            ),
        }

    fresh, freshness_message, _ = market_data_freshness(metrics)
    if not fresh:
        return {
            "submitted": False,
            "message": f"No paper order: current market data could not be verified as recent ({freshness_message}).",
        }

    price = safe_float(metrics.get("price"))
    stop = safe_float(signal.get("suggested_stop"))
    target = safe_float(signal.get("suggested_target"))
    if price is None or stop is None or target is None or stop >= price or target <= price:
        return {"submitted": False, "message": "No paper order: the strategy did not produce a valid stop and target."}

    trader = paper_client()
    account = trader.account()
    clock = trader.clock()
    if account.get("trading_blocked") is not False:
        return {"submitted": False, "message": "No paper order: Alpaca did not explicitly confirm that paper trading is allowed."}
    if clock.get("is_open") is not True:
        return {"submitted": False, "message": "No paper order: Alpaca did not explicitly confirm that the U.S. stock market is open."}

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

    fresh, freshness_message, _ = market_data_freshness(metrics)
    if not fresh:
        return {
            "submitted": False,
            "message": f"No paper order: market data expired during safety checks ({freshness_message}).",
        }

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
    require_app_access(st)
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
        library = load_runner_library()
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

    approved_count = sum(
        bool(item.get("approved"))
        and str(item.get("validation_status") or "").strip().lower() == "validated"
        and isinstance(item.get("validated_rules"), dict)
        for item in strategies
    )
    summary_cols = st.columns(3)
    summary_cols[0].metric("Saved strategies", str(len(strategies)))
    summary_cols[1].metric("Approved for Paper Auto", str(approved_count))
    summary_cols[2].metric("Live market data", "Connected" if market_ready else "Not connected")

    options = strategy_options(strategies)
    requested_strategy_id = str(
        st.session_state.pop("til_selected_strategy_id", "") or ""
    )
    requested_label = requested_strategy_label(options, requested_strategy_id)
    if requested_label:
        # Apply the cross-page request before the selectbox is instantiated.
        st.session_state["runner_strategy_v2"] = requested_label
    selected_label = st.selectbox("Strategy to run", list(options), key="runner_strategy_v2")
    strategy = options[selected_label]
    approved = bool(strategy.get("approved"))
    historically_validated = (
        str(strategy.get("validation_status") or "").strip().lower() == "validated"
        and isinstance(strategy.get("validated_rules"), dict)
    )
    execution_fidelity = paper_execution_fidelity(strategy)
    paper_execution_ready = str(execution_fidelity.get("status") or "") == "ready"
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

    if approved and historically_validated and paper_execution_ready:
        st.success("This strategy is approved, historically validated, and its paper execution matches the validated backtest lifecycle.")
    elif approved and not historically_validated:
        st.warning(
            "This strategy is approved for review, but Paper Auto is integrity-locked until "
            "the current strategy has fresh historical validation and frozen validated rules."
        )
    elif approved:
        unsupported = ", ".join(
            str(item) for item in execution_fidelity.get("unsupported_management") or []
        )
        st.warning(
            "This strategy is approved for review, but Paper Auto is integrity-locked because the "
            "current runner cannot fully reproduce the backtest trade lifecycle."
            + (f" Missing: {unsupported}." if unsupported else "")
        )
    else:
        st.info("This strategy is not approved yet. You can still use Signal Only. Paper Auto remains locked.")

    controls = st.columns([1.25, 1, 1])
    ticker = controls[0].text_input(
        "Ticker to watch",
        value=optimized_symbol or "",
        placeholder="SDOT",
        key=f"runner_ticker_{strategy.get('id')}",
        disabled=False,
        help="The optimized ticker is filled in by default, but you can inspect another stock. Cross-ticker paper trading requires a separate acknowledgement.",
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

    ticker_mismatch = bool(optimized_symbol and ticker and optimized_symbol != ticker)
    if ticker_mismatch:
        st.warning(
            f"Cross-ticker test: this strategy was optimized for {optimized_symbol}, not {ticker}. "
            "Signal Only is allowed, but its historical performance should not be assumed to transfer."
        )

    if mode == "Alpaca paper auto-entry" and not approved:
        st.warning("Paper Auto is locked for this strategy. Approve it in Strategy library first, or switch to Signal Only.")
    if mode == "Alpaca paper auto-entry" and approved and not historically_validated:
        st.warning(
            "Paper Auto is also locked until this exact strategy passes the current historical validation protocol."
        )
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

    cross_ticker_confirmed = not ticker_mismatch
    if mode == "Alpaca paper auto-entry" and ticker_mismatch:
        cross_ticker_confirmed = st.checkbox(
            f"I understand this strategy was optimized for {optimized_symbol} and want to PAPER-test it on {ticker}",
            value=False,
            help="This applies only to simulated Alpaca paper trading and does not change the saved strategy.",
        )

    armed = False
    if mode == "Alpaca paper auto-entry":
        armed = st.checkbox(
            "ARM PAPER AUTO-ENTRY",
            value=False,
            disabled=(
                not approved
                or not historically_validated
                or not paper_ready
                or not paper_execution_ready
                or not is_long_strategy(strategy)
                or not cross_ticker_confirmed
            ),
            help=(
                "When execution fidelity is available, arming allows Refresh to submit a simulated "
                "Alpaca order after every safeguard passes. It stays disabled while the paper runner "
                "cannot reproduce the validated backtest lifecycle."
            ),
        )
        if armed:
            st.warning("PAPER AUTO-ENTRY IS ARMED. A full MATCH on Refresh can submit a simulated order.")

    refresh_slot = st.empty()
    refresh = refresh_slot.button(
        "Refresh live signal",
        type="primary",
        width="stretch",
        disabled=not market_ready or not bool(ticker),
        key="runner_refresh_live_signal",
    )

    if refresh:
        refresh_slot.button(
            "Checking…",
            type="primary",
            width="stretch",
            disabled=True,
            key="runner_refresh_live_signal_busy",
        )
        refresh_monitor = LongTaskMonitor(
            "live_runner_refresh",
            session_task_profiles(st.session_state, "live_runner_refresh"),
        )
        refresh_bar = st.progress(
            0.08,
            text=refresh_monitor.text(0.08, f"Checking {ticker} live signal…"),
        )
        try:
            refresh_bar.progress(0.25, text=refresh_monitor.text(0.25, "Loading market data and evaluating strategy rules"))
            with st.spinner(f"Checking {ticker} against {strategy.get('name') or 'the selected strategy'}…"):
                metrics, signal, warnings = current_signal(ticker, strategy)
            refresh_bar.progress(0.75, text=refresh_monitor.text(0.75, "Signal evaluation complete"))
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
                refresh_bar.progress(0.86, text=refresh_monitor.text(0.86, "Running paper-entry safeguards"))
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
            refresh_monitor.finish(st.session_state)
            refresh_bar.progress(1.0, text="Live signal refresh complete · 100%")
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
        fresh, freshness_message, ages = market_data_freshness(metrics)
        freshness_text = (
            f"Verified recent · quote {ages.get('quote', 0):.0f}s · trade {ages.get('trade', 0):.0f}s"
            if fresh
            else f"Not safe for Paper Auto · {freshness_message}"
        )
        st.caption(f"Market-data freshness: {freshness_text}")

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
