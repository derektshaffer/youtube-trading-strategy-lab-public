"""Standalone page body for experimental explosive-stock discovery."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from app_access import require_app_access
from explosive_stock_core import (
    EXPLOSIVE_MODEL_VERSION,
    MAX_EXPLOSIVE_SCAN_SYMBOLS,
    scan_explosive_candidates,
)
from explosive_stock_storage import (
    DEFAULT_EXPLOSIVE_BACKUP_PATH,
    build_explosive_store,
)
from sec_catalyst_intelligence import SecEdgarClient, classify_recent_sec_filings
from trading_app_runtime import market_client, setting
from trading_glass_theme import inject_research_glass_theme
from trading_market_discovery import merge_momentum_candidate_universe
from youtube_strategy_engine import AppError, parse_symbols, safe_float, utc_now

require_app_access(st, app_name="Explosive Stock Lab")
inject_research_glass_theme()

st.markdown(
    """
    <style>
      .explode-hero {
        border: 1px solid #31445f;
        border-radius: 18px;
        padding: 22px 24px;
        background: linear-gradient(125deg,#17263c,#0d1625 65%,#1d2940);
        margin-bottom: 16px;
      }
      .explode-title {font-size:32px;font-weight:900;letter-spacing:-.035em;}
      .explode-sub {color:#afbed1;max-width:1050px;line-height:1.55;margin-top:7px;}
      .explode-badge {
        display:inline-block;border:1px solid #5c7291;border-radius:999px;
        padding:4px 9px;margin-top:10px;color:#b9c9dd;font-size:.78rem;
      }
    </style>
    <div class="explode-hero">
      <div class="explode-title">⚡ Explosive Stock Lab</div>
      <div class="explode-sub">
        A separate research system for finding stocks with ingredients associated with unusually
        large upside moves, then deciding whether that potential is dormant, igniting, active,
        or already dangerously extended.
      </div>
      <div class="explode-badge">Experimental research model · not a probability of profit</div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.warning(
    "This system is intentionally isolated from Trading Lab strategy ranking. "
    "Its current profile score is an experimental heuristic while we build and validate "
    "historical +30%, +50%, +100%, and +200% outcome models."
)

pending_view = st.session_state.pop("_explosive_pending_view", None)
if pending_view in {"Scanner", "Analyzer"}:
    st.session_state["explosive_stock_view"] = pending_view

view = st.sidebar.radio(
    "Explosive Stock Lab",
    ["Scanner", "Analyzer"],
    key="explosive_stock_view",
)
st.sidebar.caption(f"Model: {EXPLOSIVE_MODEL_VERSION}")

DEFAULT_EXPLOSIVE_BACKUP_REPOSITORY = "derektshaffer/derektshaffer-youtube-trading-strategy-lab"


@st.cache_data(ttl=120, show_spinner=False)
def _load_cloud_latent_prescreen() -> dict[str, Any]:
    """Read only the Explosive Lab artifact; never touch Trading Lab's library."""
    repository = (
        setting("EXPLOSIVE_STOCK_BACKUP_REPOSITORY")
        or setting("GITHUB_BACKUP_REPOSITORY")
        or DEFAULT_EXPLOSIVE_BACKUP_REPOSITORY
    )
    token = (
        setting("EXPLOSIVE_STOCK_BACKUP_TOKEN")
        or setting("GITHUB_BACKUP_TOKEN")
        or setting("GITHUB_TOKEN")
    )
    if not repository or not token:
        return {"error": "Explosive Lab cloud storage is not configured."}
    try:
        store = build_explosive_store(
            repository,
            token,
            branch=setting("EXPLOSIVE_STOCK_BACKUP_BRANCH") or setting("GITHUB_BACKUP_BRANCH"),
            path=setting("EXPLOSIVE_STOCK_BACKUP_PATH", DEFAULT_EXPLOSIVE_BACKUP_PATH),
            directory=".streamlit_explosive_stock_lab",
        )
        data = store.load_latest()
    except AppError as exc:
        return {"error": str(exc)}
    research_system = data.get("research_system") if isinstance(data.get("research_system"), dict) else {}
    prescreen = research_system.get("explosive_prescreen")
    return dict(prescreen) if isinstance(prescreen, dict) else {}


def _fmt(value: Any, suffix: str = "", digits: int = 1) -> str:
    number = safe_float(value)
    return "—" if number is None else f"{number:,.{digits}f}{suffix}"


def _result_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in results:
        metrics = item.get("metrics") or {}
        catalysts = item.get("catalyst_profile") or {}
        rows.append(
            {
                "Ticker": item.get("symbol"),
                "State": item.get("activation_state"),
                "Latent": safe_float((item.get("latent_prescreen") or {}).get("latent_score")),
                "Profile": safe_float(item.get("profile_score"), 0.0) or 0.0,
                "Risk": safe_float(item.get("risk_score"), 0.0) or 0.0,
                "Day %": safe_float(metrics.get("day_change_pct")),
                "RVOL": safe_float(metrics.get("relative_volume")),
                "Spread %": safe_float(metrics.get("spread_pct")),
                "Near high %": safe_float(metrics.get("distance_from_high_pct")),
                "Fresh catalysts": int(catalysts.get("fresh_specific_count") or 0),
                "Dilution flags": int(catalysts.get("fresh_dilution_count") or 0),
            }
        )
    return rows


def _render_intraday_chart(item: dict[str, Any]) -> None:
    rows = [
        row
        for row in item.get("chart_intraday_rows") or []
        if isinstance(row, dict)
    ]
    if not rows:
        st.caption(
            "No completed intraday candles are attached to this analysis yet. "
            "Run Analyze explosive potential again to load the interactive chart."
        )
        return

    frame = pd.DataFrame(rows)
    required = {"t", "o", "h", "l", "c", "v"}
    if frame.empty or not required.issubset(frame.columns):
        st.caption("Intraday candles were unavailable for charting.")
        return

    frame["timestamp"] = pd.to_datetime(frame["t"], utc=True, errors="coerce")
    for column in ("o", "h", "l", "c", "v"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = (
        frame.dropna(subset=["timestamp", "o", "h", "l", "c"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    if frame.empty:
        st.caption("Intraday candles were unavailable for charting.")
        return

    frame["timestamp"] = frame["timestamp"].dt.tz_convert("America/New_York")
    frame["ema9"] = frame["c"].ewm(span=9, adjust=False).mean()
    frame["ema20"] = frame["c"].ewm(span=20, adjust=False).mean()
    typical_price = (frame["h"] + frame["l"] + frame["c"]) / 3.0
    cumulative_volume = frame["v"].fillna(0.0).cumsum()
    frame["vwap"] = (
        (typical_price * frame["v"].fillna(0.0)).cumsum()
        / cumulative_volume.where(cumulative_volume > 0)
    )

    symbol = str(item.get("symbol") or "").upper()
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.035,
        row_heights=[0.78, 0.22],
    )
    fig.add_trace(
        go.Candlestick(
            x=frame["timestamp"],
            open=frame["o"],
            high=frame["h"],
            low=frame["l"],
            close=frame["c"],
            name="Price",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=frame["timestamp"],
            y=frame["vwap"],
            mode="lines",
            name="VWAP",
            line={"width": 2.2},
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=frame["timestamp"],
            y=frame["ema9"],
            mode="lines",
            name="EMA 9",
            line={"width": 1.5},
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=frame["timestamp"],
            y=frame["ema20"],
            mode="lines",
            name="EMA 20",
            line={"width": 1.5, "dash": "dot"},
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            x=frame["timestamp"],
            y=frame["v"].fillna(0.0),
            name="Volume",
            opacity=0.6,
        ),
        row=2,
        col=1,
    )

    feature_snapshot = item.get("market_features") or {}
    features = feature_snapshot.get("features") or {}
    evidence = feature_snapshot.get("evidence") or {}
    structure = evidence.get("market_structure") or {}
    swing_highs = list(structure.get("last_two_swing_highs") or [])
    swing_lows = list(structure.get("last_two_swing_lows") or [])

    if swing_highs:
        swing_high = safe_float(swing_highs[-1].get("price"))
        if swing_high is not None:
            label = str(features.get("last_swing_high_structure") or "swing high")
            fig.add_hline(
                y=swing_high,
                line_dash="dot",
                line_width=1,
                annotation_text=f"Confirmed swing high · {label}",
                annotation_position="top left",
                row=1,
                col=1,
            )
    if swing_lows:
        swing_low = safe_float(swing_lows[-1].get("price"))
        if swing_low is not None:
            label = str(features.get("last_swing_low_structure") or "swing low")
            fig.add_hline(
                y=swing_low,
                line_dash="dot",
                line_width=1,
                annotation_text=f"Confirmed swing low · {label}",
                annotation_position="bottom left",
                row=1,
                col=1,
            )

    breakout = evidence.get("breakout") or {}
    breakout_index = breakout.get("first_breakout_index")
    if isinstance(breakout_index, int) and 0 <= breakout_index < len(frame):
        row = frame.iloc[breakout_index]
        fig.add_trace(
            go.Scatter(
                x=[row["timestamp"]],
                y=[row["h"]],
                mode="markers+text",
                name="Breakout",
                text=["Breakout"],
                textposition="top center",
                marker={"size": 10, "symbol": "triangle-up"},
            ),
            row=1,
            col=1,
        )

    vwap_evidence = evidence.get("vwap_retest") or {}
    reclaim_index = vwap_evidence.get("reclaim_index")
    if isinstance(reclaim_index, int) and 0 <= reclaim_index < len(frame):
        row = frame.iloc[reclaim_index]
        fig.add_trace(
            go.Scatter(
                x=[row["timestamp"]],
                y=[row["c"]],
                mode="markers+text",
                name="VWAP reclaim",
                text=["VWAP reclaim"],
                textposition="bottom center",
                marker={"size": 9, "symbol": "circle"},
            ),
            row=1,
            col=1,
        )
    retest_index = vwap_evidence.get("retest_index")
    if isinstance(retest_index, int) and 0 <= retest_index < len(frame):
        row = frame.iloc[retest_index]
        held = bool(features.get("vwap_retest_held"))
        fig.add_trace(
            go.Scatter(
                x=[row["timestamp"]],
                y=[row["l"]],
                mode="markers+text",
                name="VWAP retest",
                text=["VWAP retest held" if held else "VWAP retest"],
                textposition="bottom center",
                marker={"size": 9, "symbol": "diamond"},
            ),
            row=1,
            col=1,
        )

    session_high_index = int(frame["h"].idxmax())
    session_low_index = int(frame["l"].idxmin())
    session_high_row = frame.loc[session_high_index]
    session_low_row = frame.loc[session_low_index]
    fig.add_annotation(
        x=session_high_row["timestamp"],
        y=session_high_row["h"],
        text="Session high",
        showarrow=True,
        arrowhead=2,
        yshift=12,
        row=1,
        col=1,
    )
    fig.add_annotation(
        x=session_low_row["timestamp"],
        y=session_low_row["l"],
        text="Session low",
        showarrow=True,
        arrowhead=2,
        yshift=-12,
        row=1,
        col=1,
    )

    fig.update_layout(
        title=f"{symbol} · Completed 1-minute candles",
        template="plotly_dark",
        height=660,
        margin={"l": 12, "r": 12, "t": 56, "b": 10},
        hovermode="x",
        legend={"orientation": "h", "y": 1.02, "x": 0},
        xaxis_rangeslider_visible=False,
    )
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1, rangemode="tozero")
    fig.update_xaxes(showspikes=True, spikemode="across", spikesnap="cursor")
    st.plotly_chart(
        fig,
        width="stretch",
        config={
            "displaylogo": False,
            "scrollZoom": True,
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        },
        key=f"explosive_chart_{symbol}",
    )
    st.caption(
        "Hover for candle values; drag to zoom; double-click to reset. "
        "VWAP, EMA 9, EMA 20, volume, confirmed swing structure, and detected "
        "VWAP/breakout events use completed candles only."
    )


def _render_candidate(item: dict[str, Any], *, detailed: bool) -> None:
    metrics = item.get("metrics") or {}
    daily = item.get("daily_profile") or {}
    features = (item.get("market_features") or {}).get("features") or {}
    catalysts = item.get("catalyst_profile") or {}
    structural = item.get("structural_supply") or {}

    top = st.columns(6)
    top[0].metric("Explosion profile", f"{safe_float(item.get('profile_score'), 0.0):.0f}/100")
    top[1].metric("Activation", str(item.get("activation_state") or "—"))
    top[2].metric("Risk", f"{safe_float(item.get('risk_score'), 0.0):.0f}/100")
    top[3].metric("Day move", _fmt(metrics.get("day_change_pct"), "%"))
    top[4].metric("RVOL", _fmt(metrics.get("relative_volume"), "×", 2))
    top[5].metric("Spread", _fmt(metrics.get("spread_pct"), "%", 2))

    if item.get("activation_state") == "ACTIVE":
        st.success(
            "Explosive characteristics are active now. This is still not an entry signal; "
            "use a validated strategy/setup before paper or real-money execution."
        )
    elif item.get("activation_state") == "IGNITING":
        st.info("Potential is beginning to activate, but the move is not yet classified as fully active.")
    elif item.get("activation_state") == "EXTENDED / CHASE RISK":
        st.warning("The stock may be explosive, but current price extension makes chasing especially risky.")

    if detailed:
        st.markdown("#### Interactive price chart")
        _render_intraday_chart(item)

    component_rows = [
        {"Component": name.replace("_", " ").title(), "Points": value}
        for name, value in (item.get("components") or {}).items()
    ]
    if component_rows:
        st.markdown("#### Why it scored this way")
        st.dataframe(pd.DataFrame(component_rows), hide_index=True, width="stretch")

    reasons = list(item.get("reasons") or [])
    if reasons:
        st.markdown("#### Supporting evidence")
        for reason in reasons:
            st.write("✅ " + str(reason))

    warnings = list(item.get("warnings") or [])
    if warnings:
        st.markdown("#### Risk flags")
        for warning in warnings:
            st.write("⚠️ " + str(warning))

    if structural.get("status") == "provider_not_connected":
        st.caption(
            "Float and market-cap data are intentionally shown as unavailable rather than guessed. "
            "They are important next inputs for this model and will get their own verified data source."
        )

    if not detailed:
        return

    st.markdown("#### Market structure")
    structure_cols = st.columns(4)
    structure_cols[0].metric("ATR %", _fmt(features.get("atr_pct"), "%", 2))
    structure_cols[1].metric(
        "Volume acceleration",
        _fmt(features.get("volume_acceleration_ratio"), "×", 2),
    )
    structure_cols[2].metric(
        "10-day range",
        _fmt(daily.get("recent_10d_range_pct"), "%", 1),
    )
    structure_cols[3].metric(
        "5d/20d compression",
        _fmt(daily.get("compression_ratio_5v20"), "×", 2),
    )

    history_cols = st.columns(4)
    history_cols[0].metric(
        "Largest recent 1-day gain",
        _fmt(daily.get("largest_single_day_gain_pct"), "%", 1),
    )
    history_cols[1].metric("20% runner days", int(daily.get("runner_days_20pct") or 0))
    history_cols[2].metric("30% runner days", int(daily.get("runner_days_30pct") or 0))
    history_cols[3].metric(
        "Distance from 60d high",
        _fmt(daily.get("distance_from_60d_high_pct"), "%", 1),
    )

    evidence = list(catalysts.get("top_evidence") or [])
    if evidence:
        st.markdown("#### Catalyst / structural evidence")
        for item_evidence in evidence:
            icon = "⚠️" if item_evidence.get("is_structural_risk") else (
                "▲" if item_evidence.get("is_positive") else "•"
            )
            st.write(
                f"{icon} **{item_evidence.get('category') or 'Evidence'}** — "
                f"{item_evidence.get('headline') or 'Source evidence'} "
                f"({str(item_evidence.get('freshness') or 'unknown').title()})"
            )

    st.markdown("#### Interpretation")
    st.write(
        "The profile score answers **how many explosive-move ingredients are present**. "
        "The activation state answers **whether those ingredients appear to be waking up now**. "
        "The risk score stays separate so a dangerous microcap can still be recognized as explosive "
        "without being mistaken for a good trade."
    )
    st.info(
        "Next research step: match this candidate against historically validated entry strategies "
        "in the existing Trading Lab rather than inventing a new entry rule here."
    )


if view == "Scanner":
    st.markdown("## Explosive Stock Scanner")
    cloud_prescreen = _load_cloud_latent_prescreen()
    cloud_candidates = [
        item
        for item in cloud_prescreen.get("candidates") or []
        if isinstance(item, dict) and str(item.get("symbol") or "").strip()
    ]
    if cloud_candidates:
        st.success(
            "Whole-market latent prescreen available · "
            f"{len(cloud_candidates)} saved candidates · "
            f"generated {cloud_prescreen.get('generated_at') or 'recently'}."
        )
        st.caption(
            "The cloud pre-screen searches active U.S. equities using completed daily history first. "
            "The interactive scan then adds live price/volume, intraday structure, and catalyst evidence."
        )
    elif cloud_prescreen.get("error"):
        st.caption("Early Opportunity Watchlist unavailable: " + str(cloud_prescreen.get("error")))
    else:
        st.caption(
            "The Early Opportunity Watchlist is not available yet. "
            "You can still scan Stocks Moving Right Now or enter your own tickers."
        )

    scan_source_options = [
        "Early Opportunity Watchlist",
        "Stocks Moving Right Now",
        "Enter My Own Tickers",
    ]
    scan_source = st.radio(
        "Stocks to scan",
        scan_source_options,
        index=0 if cloud_candidates else 1,
        horizontal=True,
    )
    st.caption(
        "**Early Opportunity Watchlist** = background scan found potentially interesting names before "
        "they necessarily become obvious movers.  "
        "**Stocks Moving Right Now** = current gainers and most-active stocks already attracting attention."
    )
    scan_limit = st.slider(
        "How many stocks to scan",
        min_value=10,
        max_value=min(80, MAX_EXPLOSIVE_SCAN_SYMBOLS),
        value=40,
        step=10,
    )
    manual_symbols = ""
    if scan_source == "Enter My Own Tickers":
        manual_symbols = st.text_area(
            "Tickers",
            placeholder="WETO, SDOT, LUCY, ...",
            height=90,
        )

    if st.button("⚡ Scan for explosive candidates", type="primary", width="stretch"):
        status = st.status("Building the stock list to scan…", expanded=True)
        try:
            market = market_client()
            latent_lookup: dict[str, dict[str, Any]] = {}
            if scan_source == "Enter My Own Tickers":
                symbols = parse_symbols(manual_symbols)
            elif scan_source == "Early Opportunity Watchlist":
                latent_lookup = {
                    str(item.get("symbol") or "").strip().upper(): dict(item)
                    for item in cloud_candidates
                }
                symbols = [
                    str(item.get("symbol") or "").strip().upper()
                    for item in cloud_candidates[:scan_limit]
                ]
                if not symbols:
                    raise AppError(
                        "The Early Opportunity Watchlist is not populated yet. "
                        "Use Stocks Moving Right Now for now."
                    )
                status.write(
                    f"Loaded {len(symbols)} stocks from the Early Opportunity Watchlist…"
                )
            else:
                status.write("Loading stocks moving right now from current gainers and most-active lists…")
                gainers = market.movers(top=min(50, scan_limit))
                active = market.most_active(top=min(100, scan_limit * 2))
                symbols = merge_momentum_candidate_universe(
                    gainers,
                    active,
                    limit=scan_limit,
                )
            if not symbols:
                raise AppError("No candidate symbols were available for this scan.")

            results = scan_explosive_candidates(
                market,
                symbols[:scan_limit],
                progress=status.write,
            )
            for item in results:
                symbol_key = str(item.get("symbol") or "").strip().upper()
                if symbol_key in latent_lookup:
                    item["latent_prescreen"] = latent_lookup[symbol_key]
            st.session_state["explosive_scan_results"] = results
            status.update(
                label=f"Explosive scan complete · {len(results)} stocks scored",
                state="complete",
                expanded=False,
            )
        except AppError as exc:
            status.update(label="Explosive scan stopped", state="error", expanded=True)
            st.error(str(exc))
        except Exception as exc:
            status.update(label="Explosive scan failed", state="error", expanded=True)
            st.error(f"Explosive scan failed: {exc}")

    results = list(st.session_state.get("explosive_scan_results") or [])
    if results:
        st.markdown("### Ranked candidates")
        st.caption("Click any candidate row to open that ticker in the Explosive Analyzer.")
        ranked_rows = _result_rows(results)
        table_event = st.dataframe(
            pd.DataFrame(ranked_rows),
            hide_index=True,
            width="stretch",
            on_select="rerun",
            selection_mode="single-row",
            key="explosive_ranked_candidates_table",
        )
        selected_rows = list(table_event.selection.rows or [])
        if selected_rows:
            selected_index = int(selected_rows[0])
            if 0 <= selected_index < len(results):
                selected = results[selected_index]
                st.session_state["explosive_analyzer_symbol"] = str(
                    selected.get("symbol") or ""
                )
                st.session_state["_explosive_pending_view"] = "Analyzer"
                st.rerun()

else:
    st.markdown("## Explosive Stock Analyzer")
    st.caption(
        "Deeper single-stock inspection. It reuses the same causal market features but adds a longer "
        "history window and SEC evidence when your SEC user-agent setting is available."
    )

    default_symbol = str(st.session_state.get("explosive_analyzer_symbol") or "")
    ticker = st.text_input("Ticker", value=default_symbol, placeholder="WETO").strip().upper()

    if st.button("Analyze explosive potential", type="primary", width="stretch"):
        status = st.status(f"Analyzing {ticker or 'stock'}…", expanded=True)
        try:
            symbols = parse_symbols(ticker)
            if len(symbols) != 1:
                raise AppError("Enter one valid ticker.")
            symbol = symbols[0]
            market = market_client()

            sec_items: list[dict[str, Any]] = []
            sec_user_agent = setting("SEC_USER_AGENT") or setting("SEC_EDGAR_USER_AGENT")
            if sec_user_agent:
                status.write("Checking recent SEC filings for structural/dilution risk…")
                try:
                    payload = SecEdgarClient(sec_user_agent).recent_filings(
                        symbol,
                        days=60,
                        limit=100,
                        as_of=utc_now(),
                    )
                    sec_items = classify_recent_sec_filings(payload)
                except AppError as exc:
                    status.write("SEC evidence unavailable: " + str(exc))

            results = scan_explosive_candidates(
                market,
                [symbol],
                progress=status.write,
                history_days=180,
                news_hours=168,
                sec_items_by_symbol={symbol: sec_items},
                include_chart_data=True,
            )
            if not results:
                raise AppError("No usable market snapshot was returned for that ticker.")
            result = results[0]
            st.session_state["explosive_analysis_result"] = result
            st.session_state["explosive_analyzer_symbol"] = symbol
            status.update(
                label=f"{symbol} explosive analysis complete",
                state="complete",
                expanded=False,
            )
            st.rerun()
        except AppError as exc:
            status.update(label="Explosive analysis stopped", state="error", expanded=True)
            st.error(str(exc))
        except Exception as exc:
            status.update(label="Explosive analysis failed", state="error", expanded=True)
            st.error(f"Explosive analysis failed: {exc}")

    result = st.session_state.get("explosive_analysis_result") or {}
    if result and str(result.get("symbol") or "") == str(ticker or default_symbol):
        st.divider()
        st.markdown(f"### {result.get('symbol')} · Explosive Potential")
        _render_candidate(result, detailed=True)
