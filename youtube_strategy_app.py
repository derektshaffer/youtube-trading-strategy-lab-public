"""Standalone Streamlit application: public YouTube videos -> tested trading hypotheses."""

from __future__ import annotations

from datetime import datetime, timedelta
import html
import json
import os
from typing import Any

import pandas as pd
import streamlit as st

from youtube_strategy_engine import (
    ALPACA_DATA_URL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GITHUB_BACKUP_PATH,
    ET,
    AlpacaMarketData,
    AppError,
    BacktestSettings,
    GeminiStrategySynthesizer,
    GeminiVideoAnalyzer,
    GitHubCloudBackup,
    OptimizationSettings,
    StrategyStore,
    average_completed_daily_volume,
    backtest_limitations,
    chart_trigger_checks,
    conservative_stock_costs,
    demo_strategy,
    isoformat_utc,
    match_strategy,
    normalize_machine_rules,
    normalize_youtube_url,
    optimize_stock_strategies,
    optimize_stock_timeframes,
    parse_symbols,
    parse_video_duration,
    parse_youtube_urls,
    run_backtest,
    safe_float,
    snapshot_metrics,
    timestamped_youtube_url,
    utc_now,
    video_source_strategies,
)


st.set_page_config(
    page_title="YouTube Trading Strategy Lab",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)


st.markdown(
    """
<style>
:root {
  --bg:#090f1a; --panel:#101a2b; --panel-2:#142239; --line:#263854;
  --text:#f2f6fc; --muted:#a9b9cf; --green:#35d597; --blue:#56b9ff;
  --orange:#ffba63; --red:#ff7878; --violet:#a98bff;
}
.stApp {background:radial-gradient(circle at 15% -10%,#13233b 0,#090f1a 45%); color:var(--text)}
.block-container {max-width:1480px; padding-top:1.35rem; padding-bottom:3.5rem}
section[data-testid="stSidebar"] {background:#0c1422; border-right:1px solid var(--line)}
.hero {padding:25px 27px; border:1px solid var(--line); border-radius:19px;
 background:linear-gradient(125deg,#14233a,#0d1524 68%,#18213b); margin-bottom:18px}
.eyebrow {font-size:11px; letter-spacing:.13em; color:#8da8c9; font-weight:850; text-transform:uppercase}
.hero-title {font-size:34px; line-height:1.15; font-weight:900; letter-spacing:-.035em; margin-top:7px}
.hero-sub {font-size:15px; color:var(--muted); margin-top:9px; max-width:980px}
.metric-card,.info-card,.match-card,.warning-card {border:1px solid var(--line); border-radius:15px;
 padding:17px 18px; background:rgba(16,26,43,.95); margin-bottom:12px}
.metric-card {min-height:118px}.metric-label {font-size:11px; font-weight:850;
 letter-spacing:.09em; text-transform:uppercase; color:var(--muted)}
.metric-value {font-size:31px; font-weight:900; margin-top:7px; line-height:1.12}
.metric-note {font-size:12px; color:var(--muted); margin-top:8px}
.section-title {font-size:23px; font-weight:870; margin:17px 0 5px}
.section-sub {font-size:14px; color:var(--muted); margin-bottom:15px}
.pill {display:inline-block; padding:5px 10px; margin:3px 5px 3px 0;
 border:1px solid transparent; border-radius:999px; font-size:11px; font-weight:850}
.pill-green {color:#b9f6db; background:rgba(53,213,151,.13); border-color:rgba(53,213,151,.32)}
.pill-blue {color:#c8e8ff; background:rgba(86,185,255,.12); border-color:rgba(86,185,255,.3)}
.pill-orange {color:#ffdda7; background:rgba(255,186,99,.12); border-color:rgba(255,186,99,.3)}
.pill-red {color:#ffd0d0; background:rgba(255,120,120,.12); border-color:rgba(255,120,120,.3)}
.pill-violet {color:#dfd1ff; background:rgba(169,139,255,.13); border-color:rgba(169,139,255,.3)}
.match-heading {display:flex; justify-content:space-between; gap:18px; align-items:start}
.match-symbol {font-size:31px; font-weight:950}.match-score {font-size:27px; font-weight:900}
.muted {color:var(--muted)}.good {color:var(--green)}.bad {color:var(--red)}
.warning-card {border-left:4px solid var(--orange)}
div[data-testid="stDataFrame"] {border:1px solid var(--line); border-radius:11px; overflow:hidden}
/* Streamlit's active theme can otherwise leave white text on white inputs. */
.stApp [data-testid="stWidgetLabel"],
.stApp [data-testid="stWidgetLabel"] p,
.stApp [data-testid="stWidgetLabel"] span,
.stApp label,
.stApp label p {
 color:#eaf2ff !important; font-size:15px !important; font-weight:700 !important;
 opacity:1 !important;
}
.stApp div[data-baseweb="base-input"],
.stApp div[data-baseweb="input"],
.stApp div[data-baseweb="textarea"],
.stApp div[data-baseweb="select"] > div,
.stApp [data-testid="stDateInput"] > div > div {
 background:#101d31 !important; border-color:#385172 !important;
 color:#f2f6fc !important; border-radius:10px !important;
}
.stApp input,
.stApp textarea,
.stApp div[data-baseweb="select"] input {
 background:#101d31 !important; color:#f2f6fc !important;
 -webkit-text-fill-color:#f2f6fc !important; caret-color:#8fceff !important;
 font-size:15px !important; opacity:1 !important;
}
.stApp input::placeholder,
.stApp textarea::placeholder {
 color:#aabbd0 !important; -webkit-text-fill-color:#aabbd0 !important;
 opacity:1 !important;
}
.stApp input:-webkit-autofill,
.stApp textarea:-webkit-autofill {
 -webkit-text-fill-color:#f2f6fc !important;
 -webkit-box-shadow:0 0 0 1000px #101d31 inset !important;
}
.stApp div[data-baseweb="select"] [data-testid="stMarkdownContainer"] p,
.stApp div[data-baseweb="select"] [data-baseweb="tag"],
.stApp div[data-baseweb="select"] svg,
.stApp [data-testid="stWidgetLabel"] svg {
 color:#eaf2ff !important; fill:#eaf2ff !important;
}
.stApp [data-testid="stForm"] {
 border-color:#263854 !important; background:rgba(10,18,31,.38) !important;
}
.stApp .stButton button,
.stApp .stDownloadButton button,
.stApp [data-testid="stFormSubmitButton"] button,
.stApp button[kind="secondaryFormSubmit"] {
 border:1px solid #3d79ae !important; border-radius:10px !important;
 min-height:44px !important; font-weight:750 !important;
 background:linear-gradient(115deg,#1c4369,#245985) !important;
 color:#f6f9ff !important; -webkit-text-fill-color:#f6f9ff !important;
}
.stApp .stButton button p,
.stApp .stDownloadButton button p,
.stApp [data-testid="stFormSubmitButton"] button p,
.stApp button[kind="secondaryFormSubmit"] p {
 color:#f6f9ff !important; -webkit-text-fill-color:#f6f9ff !important;
}
.stApp .stButton button:hover,
.stApp .stDownloadButton button:hover,
.stApp [data-testid="stFormSubmitButton"] button:hover {
 border-color:#7ac7ff !important;
 background:linear-gradient(115deg,#25567f,#316fa1) !important;
}
div[data-baseweb="tab-list"] {gap:14px}
@media (max-width:760px) {.hero-title {font-size:27px}.metric-value {font-size:25px}}
</style>
""",
    unsafe_allow_html=True,
)


def setting(name: str, default: str = "") -> str:
    try:
        if name in st.secrets and str(st.secrets[name]).strip():
            return str(st.secrets[name]).strip()
    except (FileNotFoundError, KeyError, RuntimeError, AttributeError):
        pass
    return str(os.environ.get(name, default)).strip()


def escape(value: Any) -> str:
    return html.escape(str(value if value is not None else "—"))


def money(value: Any, decimals: int = 2) -> str:
    number = safe_float(value)
    return f"${number:,.{decimals}f}" if number is not None else "—"


def percent(value: Any, *, signed: bool = False) -> str:
    number = safe_float(value)
    if number is None:
        return "—"
    return f"{number:+.2f}%" if signed else f"{number:.2f}%"


def compact(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "—"
    if abs(number) >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if abs(number) >= 1_000:
        return f"{number / 1_000:.1f}K"
    return f"{number:,.0f}"


def local_timestamp(raw: str | None) -> str:
    if not raw:
        return "Unavailable"
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return parsed.astimezone(ET).strftime("%b %d, %Y · %I:%M %p ET")
    except (TypeError, ValueError):
        return str(raw)


def metric_card(column: Any, label: str, value: str, note: str = "", color: str = "") -> None:
    column.markdown(
        f'<div class="metric-card"><div class="metric-label">{escape(label)}</div>'
        f'<div class="metric-value {escape(color)}">{escape(value)}</div>'
        f'<div class="metric-note">{escape(note)}</div></div>',
        unsafe_allow_html=True,
    )


def section(title: str, subtitle: str = "") -> None:
    st.markdown(
        f'<div class="section-title">{escape(title)}</div><div class="section-sub">{escape(subtitle)}</div>',
        unsafe_allow_html=True,
    )


def status_pill(label: str, color: str = "blue") -> str:
    return f'<span class="pill pill-{escape(color)}">{escape(label)}</span>'


def credentials_ready() -> tuple[bool, bool]:
    gemini_ready = bool(setting("GEMINI_API_KEY"))
    alpaca_ready = bool(setting("ALPACA_API_KEY") and setting("ALPACA_SECRET_KEY"))
    return gemini_ready, alpaca_ready


def client() -> AlpacaMarketData:
    return AlpacaMarketData(
        setting("ALPACA_API_KEY"),
        setting("ALPACA_SECRET_KEY"),
        setting("ALPACA_LIVE_FEED", "iex"),
        setting("ALPACA_HISTORICAL_FEED", "sip"),
    )


def selected_strategy_options(strategies: list[dict[str, Any]], approved_only: bool = False) -> dict[str, dict[str, Any]]:
    filtered = [item for item in strategies if not approved_only or item.get("approved")]
    result: dict[str, dict[str, Any]] = {}
    for item in filtered:
        creator = str(item.get("creator") or "Unknown creator")
        label = f'{item.get("name", "Unnamed strategy")} — {creator}'
        if label in result:
            label = f'{label} [{str(item.get("id", ""))[:6]}]'
        result[label] = item
    return result


try:
    backup_repository = setting("GITHUB_BACKUP_REPOSITORY")
    backup_token = setting("GITHUB_BACKUP_TOKEN")
    cloud_configuration_error = ""
    cloud_backup = None
    if backup_repository and backup_token:
        cloud_backup = GitHubCloudBackup(
            backup_repository,
            backup_token,
            branch=setting("GITHUB_BACKUP_BRANCH"),
            path=setting("GITHUB_BACKUP_PATH", DEFAULT_GITHUB_BACKUP_PATH),
        )
    elif backup_repository or backup_token:
        cloud_configuration_error = (
            "Permanent cloud backup is incomplete. Add both GITHUB_BACKUP_REPOSITORY "
            "and GITHUB_BACKUP_TOKEN to this app's Streamlit Secrets."
        )
    store = StrategyStore(cloud_backup=cloud_backup)
    library = store.load()
except AppError as error:
    st.error(str(error))
    st.stop()


gemini_ready, alpaca_ready = credentials_ready()

st.markdown(
    '<div class="hero"><div class="eyebrow">Independent research application · No live orders</div>'
    '<div class="hero-title">YouTube Trading Strategy Lab</div>'
    '<div class="hero-sub">Let AI inspect day-trading videos, extract timestamped chart rules, '
    'backtest the measurable parts, scan live Alpaca data, and track practice trades—without '
    'changing your existing stock analyzer.</div></div>',
    unsafe_allow_html=True,
)


with st.sidebar:
    st.markdown("### App connections")
    _ = st.success("Gemini connected") if gemini_ready else st.warning("Gemini API key needed")
    paid_gemini_key = setting("GEMINI_PAID_API_KEY")
    if paid_gemini_key:
        if paid_gemini_key == setting("GEMINI_API_KEY"):
            st.warning("Paid Gemini backup needs a different key from a separate paid project")
        else:
            st.success("Paid Gemini backup connected")
            st.caption("Your free Gemini key is used first; paid credits are used only after its quota is reached.")
    else:
        st.caption("Optional paid Gemini backup is not configured.")
    _ =st.success("Alpaca connected") if alpaca_ready else st.warning("Alpaca credentials needed")
    st.caption(f'Model: {setting("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)}')
    st.caption(f'Live feed: {setting("ALPACA_LIVE_FEED", "iex").upper()}')
    st.caption(f'Historical feed: {setting("ALPACA_HISTORICAL_FEED", "sip").upper()}')
    st.divider()
    st.markdown("### Permanent cloud backup")
    backup_status = store.cloud_status()
    if backup_status["configured"]:
        if backup_status.get("last_error"):
            st.error("Cloud backup needs attention")
            st.caption(backup_status["last_error"])
        else:
            st.success("Private GitHub backup connected")
        st.caption(f'Repository: {backup_status["repository"]}')
        if backup_status.get("last_synced_at"):
            st.caption(f'Last saved: {local_timestamp(backup_status["last_synced_at"])}')
        if store.restored_on_startup:
            st.success("Saved strategies restored automatically")
    elif cloud_configuration_error:
        st.warning(cloud_configuration_error)
    else:
        st.warning("Permanent cloud backup not configured")
        st.caption("Open Setup & backups to protect records against Streamlit restarts.")
    st.divider()
    st.markdown("### Required app secrets")
    st.code(
        'ALPACA_API_KEY="your_existing_key"\n'
        'ALPACA_SECRET_KEY="your_existing_secret"\n'
        'GEMINI_API_KEY="your_free_google_key"\n'
        '# Optional: use paid credits only after the free quota is reached\n'
        'GEMINI_PAID_API_KEY="your_separate_paid_google_key"\n'
        'ALPACA_LIVE_FEED="iex"\n'
        'ALPACA_HISTORICAL_FEED="sip"',
        language="toml",
    )
    st.markdown("[Create a Gemini API key](https://aistudio.google.com/apikey)")
    st.divider()
    st.caption(
        "This app is research and paper tracking only. It never places live or paper brokerage orders. "
        "YouTube strategies are hypotheses, not evidence of future profits."
    )


overview_tab, videos_tab, master_tab, strategies_tab, backtest_tab, optimizer_tab, scanner_tab, paper_tab, settings_tab = st.tabs(
    [
        "Overview", "Analyze videos", "Master strategy", "Strategy library", "Backtesting",
        "Stock optimizer", "Live scanner", "Paper journal", "Setup & backups",
    ]
)


with overview_tab:
    approved_count = sum(bool(item.get("approved")) for item in library["strategies"])
    open_positions = [position for position in library["paper_positions"] if position.get("status") == "open"]
    cards = st.columns(4)
    metric_card(cards[0], "Videos analyzed", str(len(library["videos"])), "Public YouTube videos")
    metric_card(cards[1], "Strategies extracted", str(len(library["strategies"])), "Unverified research hypotheses")
    metric_card(cards[2], "Approved strategies", str(approved_count), "Eligible for live matching", "good" if approved_count else "")
    metric_card(cards[3], "Open paper trades", str(len(open_positions)), "No real-money orders")

    section("What this app does", "Each stage is separate so your original analyzer remains untouched.")
    st.markdown(
        "1. Paste public YouTube video links into **Analyze videos**.\n"
        "2. Gemini inspects the chart visuals and spoken explanations.\n"
        "3. Combine lessons across videos in **Master strategy** without replacing the originals.\n"
        "4. Review and edit extracted or combined rules in **Strategy library**.\n"
        "5. Check historical results and holdout performance in **Backtesting**.\n"
        "6. Compare strategies and tune settings for a specific ticker in **Stock optimizer**.\n"
        "7. Approve the strategies you want the **Live scanner** to match.\n"
        "8. Practice and record entries in the **Paper journal**."
    )

    if not library["strategies"]:
        st.info("Add a YouTube video to extract your first strategy, or load an example to explore the interface.")
        if st.button("Load example strategy", key="load_example", use_container_width=False):
            updated = store.load()
            if not any(item.get("id") == "demo_vwap_momentum" for item in updated["strategies"]):
                updated["strategies"].append(demo_strategy())
                store.save(updated)
            st.rerun()

    if library["videos"]:
        section("Recent video analyses")
        recent = [
            {
                "Video": item.get("video_title") or "Untitled",
                "Creator": item.get("creator") or "Unknown",
                "Analyzed": local_timestamp(item.get("analyzed_at")),
                "Model": item.get("model") or "—",
            }
            for item in library["videos"][:8]
        ]
        st.dataframe(pd.DataFrame(recent), use_container_width=True, hide_index=True)


with videos_tab:
    section("Give the AI trading videos to inspect", "Paste one public YouTube video link per line. The app reads both the visible charts and the audio.")
    with st.form("analyze_video_form"):
        raw_urls = st.text_area(
            "YouTube video links",
            height=170,
            placeholder="https://www.youtube.com/watch?v=VIDEO_ID\nhttps://youtu.be/ANOTHER_ID",
            help="Video links inside a playlist are fine, but a playlist-only URL does not contain all its individual video IDs.",
        )
        focus = st.text_area(
            "Anything specific the AI should look for?",
            height=85,
            placeholder="Example: Focus on low-float momentum, VWAP reclaims, entry timing, stop placement, and avoiding bad liquidity.",
        )
        duration_override_text = st.text_input(
            "Long video length (optional)",
            value="",
            placeholder="Example: 3:06:12",
            help=(
                "Long videos are split into 40-minute sections automatically when YouTube provides the runtime. "
                "If a long video fails, enter its total length here and analyze that video by itself."
            ),
        )
        submitted = st.form_submit_button("Analyze YouTube videos", use_container_width=True)

    if submitted:
        urls, invalid = parse_youtube_urls(raw_urls)
        duration_override = None
        duration_error = ""
        try:
            duration_override = parse_video_duration(duration_override_text)
        except AppError as error:
            duration_error = str(error)
        for problem in invalid:
            st.warning(problem)
        if not urls:
            st.error("Add at least one valid public YouTube video link.")
        elif duration_error:
            st.error(duration_error)
        elif duration_override is not None and len(urls) != 1:
            st.error("When entering a long video length, analyze one video link at a time.")
        elif not gemini_ready:
            st.error("Add GEMINI_API_KEY to this separate app's Streamlit Secrets first.")
        elif setting("GEMINI_PAID_API_KEY") == setting("GEMINI_API_KEY"):
            st.error(
                "GEMINI_PAID_API_KEY must be a different key from a separate paid Google project. "
                "Keep GEMINI_API_KEY connected to your free project."
            )
        elif len(urls) > 25:
            st.error("Analyze no more than 25 videos per batch to control API usage and processing time.")
        else:
            analyzer = GeminiVideoAnalyzer(
                setting("GEMINI_API_KEY"),
                setting("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
                fallback_api_key=setting("GEMINI_PAID_API_KEY"),
            )
            progress = st.progress(0, text="Starting video analysis…")
            completed = 0
            extracted = 0
            for index, url in enumerate(urls):
                progress.progress(index / len(urls), text=f"Analyzing video {index + 1} of {len(urls)}…")
                try:
                    def report_section_progress(completed_sections: int, total_sections: int, message: str) -> None:
                        fraction = (index + completed_sections / max(1, total_sections)) / len(urls)
                        progress.progress(min(1.0, fraction), text=f"Video {index + 1} of {len(urls)} · {message}")

                    analysis = analyzer.analyze(
                        url,
                        focus,
                        video_duration_seconds=duration_override,
                        progress=report_section_progress,
                    )
                    store.add_video_analysis(analysis)
                    completed += 1
                    extracted += len(analysis.get("strategies") or [])
                    section_label = (
                        f' across {analysis["segment_count"]} video sections'
                        if analysis.get("segmented_analysis")
                        else ""
                    )
                    st.success(
                        f'{analysis.get("video_title", "Video analyzed")}: '
                        f'{len(analysis.get("strategies") or [])} strategy or strategies extracted{section_label}.'
                    )
                    if analysis.get("paid_fallback_used"):
                        st.info("Your free Gemini quota was reached, so this video continued using your paid backup key.")
                except AppError as error:
                    st.error(f"{url}: {error}")
            progress.progress(1.0, text="Video batch complete")
            if completed:
                st.session_state["analysis_notice"] = f"Analyzed {completed} videos and extracted {extracted} strategies."
                st.rerun()

    if st.session_state.pop("analysis_notice", None):
        st.success("Video analysis complete. Review the extracted strategies below or in Strategy library.")

    if library["videos"]:
        section("Analyzed videos", "Evidence stays linked to the original public video; the app does not download or republish videos.")
        for video in library["videos"]:
            source_url = str(video.get("url") or "")
            related = [item for item in library["strategies"] if item.get("source_url") == source_url]
            heading = f'{video.get("video_title") or "Untitled video"} · {len(related)} strategies'
            with st.expander(heading, expanded=False):
                st.markdown(f'**Creator:** {escape(video.get("creator") or "Unknown")}')
                st.markdown(f'**Analyzed:** {local_timestamp(video.get("analyzed_at"))}')
                if source_url:
                    st.markdown(f"[Open original YouTube video]({source_url})")
                st.write(video.get("summary") or "No summary provided.")
                observations = video.get("visual_observations") or []
                if observations:
                    st.markdown("**What the AI could actually see:**")
                    for observation in observations[:12]:
                        st.markdown(f"- {observation}")
                warnings = video.get("general_risk_warnings") or []
                if warnings:
                    st.warning(" · ".join(str(item) for item in warnings[:6]))
                confirm_video_deletion = st.checkbox(
                    "I understand this removes the video and all of its strategies",
                    value=False,
                    key=f"confirm_video_strategy_deletion_{source_url}",
                )
                remove_col, remove_all_col = st.columns(2)
                if remove_col.button("Remove video only", key=f"remove_video_{source_url}"):
                    store.delete_video(source_url, delete_strategies=False)
                    st.rerun()
                if remove_all_col.button(
                    "Remove video and its strategies",
                    key=f"remove_video_strategies_{source_url}",
                    disabled=not confirm_video_deletion,
                ):
                    store.delete_video(source_url, delete_strategies=True)
                    st.rerun()


with master_tab:
    section(
        "Combine everything the videos taught",
        "Create one complete trading framework from saved video lessons. Your original video strategies remain unchanged.",
    )
    original_strategies = video_source_strategies(library["strategies"])
    available_video_urls = {
        normalize_youtube_url(str(item.get("source_url") or "")) for item in original_strategies
    }
    existing_masters = [
        item for item in library["strategies"]
        if item.get("is_master_strategy") and not item.get("optimized_for_symbol")
    ]
    master_cards = st.columns(4)
    metric_card(master_cards[0], "Source videos", str(len(available_video_urls)), "Previously analyzed YouTube videos")
    metric_card(master_cards[1], "Original strategies", str(len(original_strategies)), "Generated derivatives are excluded")
    metric_card(
        master_cards[2], "Approved source strategies",
        str(sum(bool(item.get("approved")) for item in original_strategies)),
        "Optional reviewed-only source scope",
    )
    metric_card(master_cards[3], "Saved master strategies", str(len(existing_masters)), "Original video strategies stay intact")

    if not original_strategies:
        st.info("Analyze at least one public YouTube trading video first. Example and stock-optimized strategies are not source videos.")
    else:
        with st.form("create_master_strategy_form"):
            master_name = st.text_input("Name for the combined strategy", value="Comprehensive YouTube Master Strategy")
            source_scope = st.selectbox(
                "Which saved video strategies should the AI combine?",
                ["All original video strategies", "Approved original video strategies only"],
            )
            master_focus = st.text_area(
                "Anything the combined strategy should prioritize?",
                height=105,
                placeholder=(
                    "Example: Favor liquid momentum stocks, combine VWAP reclaims and breakouts as "
                    "separate entry setups, and prioritize disciplined stops and avoiding late entries."
                ),
            )
            create_master = st.form_submit_button(
                "Combine video lessons into one master strategy",
                use_container_width=True,
            )

        if create_master:
            chosen_sources = [
                item for item in original_strategies
                if source_scope == "All original video strategies" or item.get("approved")
            ]
            if not chosen_sources:
                st.error("No approved original video strategies are available. Approve a strategy or choose all original strategies.")
            elif not gemini_ready:
                st.error("Add GEMINI_API_KEY to this separate app's Streamlit Secrets first.")
            else:
                try:
                    synthesizer = GeminiStrategySynthesizer(
                        setting("GEMINI_API_KEY"),
                        setting("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
                        fallback_api_key=setting("GEMINI_PAID_API_KEY"),
                    )
                    chosen_video_count = len({str(item.get("source_url") or "") for item in chosen_sources})
                    with st.spinner(
                        f"Combining {len(chosen_sources)} saved strategies from {chosen_video_count} analyzed videos…"
                    ):
                        master_strategy = synthesizer.synthesize(
                            chosen_sources,
                            library["videos"],
                            extra_instructions=master_focus,
                            master_name=master_name,
                        )
                        store.save_master_strategy(master_strategy)
                    st.session_state["master_strategy_notice"] = (
                        f'Saved "{master_strategy["name"]}" from {len(chosen_sources)} strategies and '
                        f'{chosen_video_count} videos. Your original strategies were preserved.'
                        + (
                            " The free Gemini quota was reached, so your paid backup key was used."
                            if master_strategy.get("paid_fallback_used")
                            else ""
                        )
                    )
                    st.rerun()
                except AppError as error:
                    st.error(str(error))

    master_notice = st.session_state.pop("master_strategy_notice", None)
    if master_notice:
        st.success(master_notice)

    if existing_masters:
        section("Your comprehensive trading framework", "Review the combined rules here, then edit, backtest, optimize, or approve it in the other tabs.")
        master_options = selected_strategy_options(existing_masters)
        selected_master = master_options[
            st.selectbox("Choose a saved master strategy", list(master_options), key="master_strategy_inspector")
        ]
        master_rules = normalize_machine_rules(selected_master.get("machine_rules"))
        summary_cards = st.columns(4)
        metric_card(summary_cards[0], "Videos combined", str(len(selected_master.get("source_urls") or [])), "Verified source video links")
        metric_card(summary_cards[1], "Lessons combined", str(len(selected_master.get("source_strategy_ids") or [])), "Original video strategies")
        metric_card(summary_cards[2], "Executable core rules", str(sum(value is not None for value in master_rules.values())), "Source-backed settings only")
        metric_card(summary_cards[3], "Alternative setups", str(len(selected_master.get("setup_branches") or [])), "Explained as separate entry branches")

        st.markdown(
            status_pill("COMPREHENSIVE MASTER", "violet")
            + status_pill("APPROVED" if selected_master.get("approved") else "NEEDS REVIEW", "green" if selected_master.get("approved") else "orange"),
            unsafe_allow_html=True,
        )
        st.write(selected_master.get("summary") or "No combined summary was supplied.")

        if selected_master.get("shared_principles"):
            section("Principles that carry across the videos")
            for principle in selected_master["shared_principles"]:
                st.markdown(f"- {principle}")

        if selected_master.get("setup_branches"):
            section("Alternative ways to enter the same overall framework")
            st.info(
                "The backtester and scanner currently execute the compatible shared core rules. "
                "These alternative entry branches explain the full trading plan but are not automatically evaluated as separate OR conditions."
            )
            source_names = {str(item.get("id")): str(item.get("name") or "Unnamed lesson") for item in original_strategies}
            for branch in selected_master["setup_branches"]:
                with st.expander(str(branch.get("name") or "Entry setup"), expanded=False):
                    if branch.get("best_conditions"):
                        st.write(branch["best_conditions"])
                    for condition in branch.get("entry_conditions") or []:
                        st.markdown(f"- {condition}")
                    references = [source_names[item] for item in branch.get("source_strategy_ids") or [] if item in source_names]
                    if references:
                        st.caption("Supporting source lessons: " + " · ".join(references[:8]))

        master_details = st.columns(3)
        with master_details[0]:
            st.markdown("**Entry and stock-selection rules**")
            for item in selected_master.get("entry_conditions") or ["No executable entry rules were supported by the sources."]:
                st.markdown(f"- {item}")
        with master_details[1]:
            st.markdown("**Exits and risk management**")
            for item in (selected_master.get("exit_conditions") or []) + (selected_master.get("risk_rules") or []):
                st.markdown(f"- {item}")
        with master_details[2]:
            st.markdown("**When to avoid the trade**")
            for item in selected_master.get("avoid_conditions") or ["No additional exclusions were supplied."]:
                st.markdown(f"- {item}")

        if selected_master.get("conflicts_resolved"):
            section("How conflicting lessons were handled", "Different setups stay separate; numeric settings must come from an actual source strategy.")
            for conflict in selected_master["conflicts_resolved"]:
                with st.expander(str(conflict.get("topic") or "Conflicting source rules"), expanded=False):
                    st.markdown(f'**The disagreement:** {conflict.get("differing_rules") or "Not specified"}')
                    st.markdown(f'**Chosen resolution:** {conflict.get("resolution") or "Left unresolved"}')

        if selected_master.get("unresolved_rules"):
            st.warning(
                "Still requires human review or unavailable data: "
                + " · ".join(str(item) for item in selected_master["unresolved_rules"][:10])
            )
        if selected_master.get("excluded_lessons"):
            st.caption("Excluded or non-executable lessons: " + " · ".join(str(item) for item in selected_master["excluded_lessons"][:8]))

        if selected_master.get("evidence"):
            section("Evidence from the original videos")
            for item in selected_master["evidence"][:20]:
                timestamp = str(item.get("timestamp") or "")
                item_url = str(item.get("source_url") or "")
                link = timestamped_youtube_url(item_url, timestamp) if item_url else ""
                stamp = f"[{timestamp}]({link})" if link and timestamp else timestamp or "Source lesson"
                st.markdown(f'**{stamp} — {item.get("description") or "Trading evidence"}**')
                if item.get("visual_evidence"):
                    st.caption(f'Visible on-screen: {item["visual_evidence"]}')
                if item.get("spoken_evidence"):
                    st.caption(f'Presenter said: {item["spoken_evidence"]}')

        if selected_master.get("source_urls"):
            video_titles = {str(item.get("url") or ""): str(item.get("video_title") or "YouTube video") for item in library["videos"]}
            with st.expander("Show all source videos", expanded=False):
                for source_url in selected_master["source_urls"]:
                    st.markdown(f'- [{video_titles.get(source_url, "Open original YouTube video")}]({source_url})')
        st.caption(
            "A master strategy is an evidence-grounded research hypothesis. "
            "Backtest it, inspect the untouched holdout, and review all risk settings before approval."
        )


with strategies_tab:
    section("Extracted trading strategies", "The AI preserves source timestamps and leaves unstated numeric rules blank. Review a strategy before approving it for live scanning.")
    recoverable_strategies = [item for item in library.get("recovery_items", []) if item.get("strategies")]
    if recoverable_strategies:
        st.warning(
            f"{len(recoverable_strategies)} deleted strategy or video group can be restored. "
            "Open Setup & backups → Recently deleted to recover the exact previous rules."
        )
    if not library["strategies"]:
        st.info("Analyze a YouTube video first, or load the example strategy from Overview.")
    else:
        rows = []
        for strategy in library["strategies"]:
            rows.append(
                {
                    "Strategy": strategy.get("name"),
                    "Type": (
                        "Master" if strategy.get("is_master_strategy")
                        else "Stock optimized" if strategy.get("optimized_for_symbol")
                        else "Video lesson"
                    ),
                    "Creator": strategy.get("creator"),
                    "Category": strategy.get("category"),
                    "Direction": strategy.get("direction"),
                    "Target stock": strategy.get("optimized_for_symbol") or "Any stock",
                    "Extraction clarity": f'{safe_float(strategy.get("confidence"), 0):.0f}%',
                    "Unresolved rules": len(strategy.get("unresolved_rules") or []),
                    "Live status": "Approved" if strategy.get("approved") else "Needs review",
                    "Last backtest P/L": money((strategy.get("last_backtest") or {}).get("net_pnl")),
                    "Holdout P/L": money((strategy.get("last_backtest") or {}).get("holdout_net_pnl")),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        options = selected_strategy_options(library["strategies"])
        selection = st.selectbox("Choose a strategy to inspect or edit", list(options), key="strategy_inspector")
        selected = options[selection]
        rules = normalize_machine_rules(selected.get("machine_rules"))

        st.markdown(
            status_pill(str(selected.get("category") or "Uncategorized"), "violet")
            + status_pill(str(selected.get("direction") or "unclear").upper(), "blue")
            + status_pill("APPROVED" if selected.get("approved") else "NEEDS REVIEW", "green" if selected.get("approved") else "orange")
            + (
                status_pill(f'{selected["optimized_for_symbol"]} ONLY', "blue")
                if selected.get("optimized_for_symbol") else ""
            )
            + (status_pill("MASTER STRATEGY", "violet") if selected.get("is_master_strategy") else ""),
            unsafe_allow_html=True,
        )
        st.write(selected.get("summary") or "No description supplied.")
        source_url = selected.get("source_url")
        if source_url:
            st.markdown(f'**Source:** [{selected.get("source_title") or "Open original video"}]({source_url})')
        elif selected.get("source_urls"):
            st.caption(
                f'Combined from {len(selected["source_urls"])} YouTube videos and '
                f'{len(selected.get("source_strategy_ids") or [])} original strategies. '
                "See the Master strategy tab for the complete source list."
            )
        if selected.get("reanalysis_changed_rules"):
            st.info(
                "Reanalyzing this video produced different settings, but your existing strategy was preserved. "
                "The alternate AI version is available under Saved strategy checkpoints below."
            )
        previous_test = selected.get("last_backtest") or {}
        if previous_test:
            st.caption(
                f'Last saved backtest: {money(previous_test.get("net_pnl"))} net · '
                f'{money(previous_test.get("holdout_net_pnl"))} holdout · '
                f'{int(safe_float(previous_test.get("trade_count"), 0) or 0)} trades · '
                f'{local_timestamp(previous_test.get("tested_at"))}'
            )

        detail_columns = st.columns(3)
        with detail_columns[0]:
            st.markdown("**Entry conditions**")
            for item in selected.get("entry_conditions") or ["None extracted"]:
                st.markdown(f"- {item}")
        with detail_columns[1]:
            st.markdown("**Exit and risk rules**")
            for item in (selected.get("exit_conditions") or []) + (selected.get("risk_rules") or []):
                st.markdown(f"- {item}")
        with detail_columns[2]:
            st.markdown("**Reasons to avoid the trade**")
            for item in selected.get("avoid_conditions") or ["None extracted"]:
                st.markdown(f"- {item}")

        unresolved = selected.get("unresolved_rules") or []
        if unresolved:
            st.warning("Not directly testable yet: " + " · ".join(str(item) for item in unresolved))
        if selected.get("source_warnings"):
            st.caption("Source warnings: " + " · ".join(str(item) for item in selected["source_warnings"][:5]))

        evidence = selected.get("evidence") or []
        if evidence:
            section("Timestamped video evidence")
            for item in evidence[:15]:
                timestamp = str(item.get("timestamp") or "")
                evidence_url = str(item.get("source_url") or source_url or "")
                link = timestamped_youtube_url(evidence_url, timestamp) if evidence_url else ""
                stamp = f"[{timestamp}]({link})" if link else timestamp
                st.markdown(f'**{stamp} — {item.get("description") or "Trading example"}**')
                if item.get("visual_evidence"):
                    st.caption(f'Visible on-screen: {item["visual_evidence"]}')
                if item.get("spoken_evidence"):
                    st.caption(f'Presenter said: {item["spoken_evidence"]}')

        section("Edit measurable rules", "A blank box means the video did not provide that threshold or you do not want to require it.")

        def text_rule(label: str, name: str, help_text: str = "") -> str:
            existing = rules.get(name)
            return st.text_input(label, value="" if existing is None else str(existing), key=f"edit_{selected['id']}_{name}", help=help_text)

        with st.form(f"edit_strategy_{selected['id']}"):
            column_one, column_two, column_three = st.columns(3)
            updated: dict[str, Any] = {}
            with column_one:
                updated["min_price"] = text_rule("Minimum stock price ($)", "min_price")
                updated["max_price"] = text_rule("Maximum stock price ($)", "max_price")
                updated["min_day_change_pct"] = text_rule("Minimum move today (%)", "min_day_change_pct")
                updated["min_relative_volume"] = text_rule("Minimum relative volume (x)", "min_relative_volume")
                updated["min_dollar_volume"] = text_rule("Minimum dollar volume ($)", "min_dollar_volume")
                updated["max_spread_pct"] = text_rule("Maximum bid/ask spread (%)", "max_spread_pct")
            with column_two:
                vwap_values = {"No requirement": None, "Must be above VWAP": True, "Must be below VWAP": False}
                current_vwap = next((label for label, value in vwap_values.items() if value is rules.get("above_vwap")), "No requirement")
                vwap_choice = st.selectbox("VWAP position", list(vwap_values), index=list(vwap_values).index(current_vwap))
                updated["above_vwap"] = vwap_values[vwap_choice]
                updated["max_vwap_distance_pct"] = text_rule("Maximum distance above VWAP (%)", "max_vwap_distance_pct")
                updated["breakout_lookback_bars"] = text_rule("Breakout lookback (candles)", "breakout_lookback_bars")
                updated["opening_range_minutes"] = text_rule("Opening range (minutes)", "opening_range_minutes")
                updated["volume_surge_ratio"] = text_rule("Current-candle volume surge (x)", "volume_surge_ratio")
                updated["minimum_green_bars"] = text_rule("Consecutive green candles", "minimum_green_bars")
            with column_three:
                updated["stop_loss_pct"] = text_rule("Stop loss (%)", "stop_loss_pct")
                updated["reward_risk"] = text_rule("Target reward/risk (x)", "reward_risk")
                updated["max_hold_minutes"] = text_rule("Maximum holding time (minutes)", "max_hold_minutes")
                updated["session_start"] = text_rule("Earliest entry, Eastern (HH:MM)", "session_start")
                updated["session_end"] = text_rule("Latest entry, Eastern (HH:MM)", "session_end")
                updated["vwap_reclaim"] = st.checkbox("Require a VWAP reclaim", value=bool(rules.get("vwap_reclaim")))
                updated["catalyst_required"] = st.checkbox("Require recent news", value=bool(rules.get("catalyst_required")))
            approved = st.checkbox(
                "Approve this strategy for matching in the live scanner",
                value=bool(selected.get("approved")),
                help="Approval enables research alerts only. This app never sends brokerage orders.",
            )
            save_strategy = st.form_submit_button("Save strategy rules", use_container_width=True)
        if save_strategy:
            store.update_strategy(selected["id"], {"machine_rules": updated, "approved": approved})
            st.success("Strategy saved.")
            st.rerun()
        confirm_strategy_deletion = st.checkbox(
            "Confirm that I want to remove this strategy",
            value=False,
            key=f"confirm_delete_strategy_{selected['id']}",
        )
        if st.button(
            "Move this strategy to recently deleted",
            key=f"delete_strategy_{selected['id']}",
            disabled=not confirm_strategy_deletion,
        ):
            store.delete_strategy(selected["id"])
            st.rerun()

        checkpoints = [
            item for item in library.get("strategy_versions", [])
            if item.get("strategy_id") == selected.get("id")
        ]
        if checkpoints:
            section(
                "Saved strategy checkpoints",
                "Backtests, rule edits, and alternate AI extractions save the exact rules so an earlier version can be restored.",
            )
            for checkpoint in checkpoints[:20]:
                summary = checkpoint.get("backtest_summary") or {}
                description = f'{checkpoint.get("reason", "Saved version")} · {local_timestamp(checkpoint.get("saved_at"))}'
                if summary:
                    description += (
                        f' · Net {money(summary.get("net_pnl"))}'
                        f' · Holdout {money(summary.get("holdout_net_pnl"))}'
                        f' · {int(safe_float(summary.get("trade_count"), 0) or 0)} trades'
                    )
                with st.expander(description, expanded=False):
                    old_rules = normalize_machine_rules((checkpoint.get("strategy") or {}).get("machine_rules"))
                    differences = [
                        {
                            "Rule": name.replace("_", " ").title(),
                            "Saved value": "—" if value is None else str(value),
                            "Current value": "—" if rules.get(name) is None else str(rules.get(name)),
                        }
                        for name, value in old_rules.items()
                        if value is not None or rules.get(name) is not None
                    ]
                    if differences:
                        st.dataframe(pd.DataFrame(differences), hide_index=True, use_container_width=True)
                    if summary:
                        st.caption(
                            f'Tested tickers: {", ".join(summary.get("symbols") or []) or "—"} · '
                            f'{summary.get("history_days") or "?"} days · '
                            f'{summary.get("timeframe") or "Unknown interval"}'
                        )
                    if st.button("Restore this exact strategy version", key=f'restore_checkpoint_{checkpoint["id"]}'):
                        try:
                            store.restore_strategy_version(checkpoint["id"])
                            st.rerun()
                        except AppError as error:
                            st.error(str(error))


with backtest_tab:
    section("Test extracted strategies against real historical candles", "Entries occur at the next candle's open. Costs, stop losses, same-bar ambiguity, and out-of-sample results are included.")
    if not library["strategies"]:
        st.info("Add or extract a strategy first.")
    elif not alpaca_ready:
        st.error("Add your existing ALPACA_API_KEY and ALPACA_SECRET_KEY to this app's Streamlit Secrets.")
    else:
        options = selected_strategy_options(library["strategies"])
        chosen_label = st.selectbox("Strategy to backtest", list(options), key="backtest_strategy")
        chosen = options[chosen_label]
        optimized_profile = (
            chosen.get("optimized_backtest_settings")
            or (chosen.get("optimization_summary") or {}).get("optimized_backtest_settings")
            or {}
        )
        optimized_symbol = str(chosen.get("optimized_for_symbol") or "").strip().upper()
        preferred_timeframe = str(
            chosen.get("preferred_timeframe") or (chosen.get("optimization_summary") or {}).get("timeframe") or "5Min"
        )
        supported_timeframes = ["1Min", "5Min", "15Min"]
        if preferred_timeframe not in supported_timeframes:
            preferred_timeframe = "5Min"
        preferred_history = int(
            max(7, min(120, safe_float(chosen.get("preferred_history_days"), 30.0) or 30.0))
        )
        if optimized_symbol and optimized_profile:
            st.success(
                f"Loaded the optimized trading settings for {optimized_symbol}, including its recommended "
                "risk, position size, stop, reward target, trading costs, and candle interval."
            )
        else:
            st.caption(
                "To automatically find stock-specific risk, position size, stop, reward, and candle interval, "
                "use Stock optimizer and save the recommended strategy."
            )
        limitations = backtest_limitations(chosen)
        if limitations:
            st.warning("Backtest limitations: " + " · ".join(limitations))
        with st.form("backtest_form"):
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

        if run_requested:
            tickers = parse_symbols(tickers_raw)
            if not tickers:
                st.error("Enter at least one valid ticker symbol.")
            elif len(tickers) > 5:
                st.error("Test no more than five tickers at once so historical downloads remain manageable.")
            else:
                settings_value = BacktestSettings(
                    starting_cash=starting_cash,
                    risk_per_trade_pct=risk_per_trade,
                    max_position_pct=position_cap,
                    default_stop_pct=default_stop,
                    default_reward_risk=default_ratio,
                    spread_bps=spread_bps,
                    slippage_bps=slippage_bps,
                    fee_per_order=order_fee,
                )
                try:
                    market = client()
                    # Basic Alpaca accounts can retrieve consolidated history, but not
                    # its most recent 15 minutes. A 16-minute buffer stays compatible.
                    delay = 16 if market.historical_feed == "sip" and market.live_feed != "sip" else 1
                    end = utc_now() - timedelta(minutes=delay)
                    start = end - timedelta(days=int(history_days))
                    with st.spinner("Downloading Alpaca historical candles and running conservative simulations…"):
                        all_bars = market.bars(tickers, start=start, end=end, timeframe=timeframe)
                        results = [run_backtest(all_bars.get(symbol, []), chosen, symbol, settings_value) for symbol in tickers]
                    store.record_backtest(
                        str(chosen.get("id") or ""),
                        results,
                        timeframe=timeframe,
                        history_days=int(history_days),
                    )
                    st.session_state["backtest_results"] = results
                    st.session_state["backtest_strategy_id"] = chosen.get("id")
                except AppError as error:
                    st.error(str(error))

        results = st.session_state.get("backtest_results") or []
        if results:
            section("Backtest results", "Historical results are hypothetical. Holdout results were not used to define the in-sample segment.")
            summary = []
            for result in results:
                metrics = result["metrics"]
                holdout = result["out_of_sample"]
                summary.append(
                    {
                        "Ticker": result["symbol"],
                        "Sessions": result["sessions"],
                        "Trades": metrics["trade_count"],
                        "Win rate": percent(metrics["win_rate_pct"]),
                        "Net P/L": money(metrics["net_pnl"]),
                        "Return": percent(metrics["return_pct"], signed=True),
                        "Max drawdown": percent(metrics["max_drawdown_pct"]),
                        "Holdout trades": holdout["trade_count"],
                        "Holdout P/L": money(holdout["net_pnl"]),
                    }
                )
            st.dataframe(pd.DataFrame(summary), hide_index=True, use_container_width=True)
            lookup = {result["symbol"]: result for result in results}
            detail_symbol = st.selectbox("Show detailed results for", list(lookup), key="backtest_detail")
            detail = lookup[detail_symbol]
            stat_columns = st.columns(4)
            all_metrics = detail["metrics"]
            metric_card(stat_columns[0], "Net simulated P/L", money(all_metrics["net_pnl"]), f'{all_metrics["trade_count"]} completed trades', "good" if all_metrics["net_pnl"] > 0 else "bad")
            metric_card(stat_columns[1], "Holdout P/L", money(detail["out_of_sample"]["net_pnl"]), f'{detail["out_of_sample"]["trade_count"]} unseen-period trades')
            metric_card(stat_columns[2], "Maximum drawdown", percent(all_metrics["max_drawdown_pct"]), "Peak-to-trough simulated loss")
            factor = all_metrics["profit_factor"]
            metric_card(stat_columns[3], "Profit factor", f"{factor:.2f}x" if factor is not None else "—", "Gross gains divided by gross losses")
            curve = pd.DataFrame(detail["equity_curve"])
            if len(curve) > 1:
                curve["timestamp"] = pd.to_datetime(curve["timestamp"], errors="coerce", utc=True)
                st.line_chart(curve.dropna(subset=["timestamp"]).set_index("timestamp")["equity"], height=280)
            if detail["trades"]:
                st.markdown("**Simulated trade log**")
                st.dataframe(pd.DataFrame(detail["trades"]), use_container_width=True, hide_index=True)
            else:
                st.info("No historical trades met this strategy's current measurable rules for the selected period.")
            if detail["limitations"]:
                st.caption("Research assumptions and exclusions: " + " · ".join(detail["limitations"]))


with optimizer_tab:
    section(
        "Find the strongest saved strategy for one stock",
        "Test each stock's strategy, candle size, stop, target, risk per trade, and position size on earlier sessions. "
        "Choose settings using separate validation data, then inspect one untouched final holdout.",
    )
    if not library["strategies"]:
        st.info("Analyze a YouTube video or load an example strategy before optimizing a stock.")
    elif not alpaca_ready:
        st.error("Add ALPACA_API_KEY and ALPACA_SECRET_KEY to this app's Streamlit Secrets first.")
    else:
        st.caption(
            f'{len(library["strategies"])} saved strategies available. The stock history is downloaded once; '
            "the AI does not need to reanalyze your videos. Account size and realistic trading costs remain fixed."
        )
        with st.form("stock_optimizer_form"):
            first_row = st.columns(4)
            optimizer_symbol_raw = first_row[0].text_input(
                "Stock to optimize",
                value="",
                placeholder="SDOT, LUCY, or NVDA",
                help="Enter one ticker. Every eligible saved strategy will be tested against this stock.",
            )
            optimizer_history_days = first_row[1].slider(
                "Historical calendar days",
                min_value=20,
                max_value=180,
                value=90,
            )
            optimizer_timeframe = first_row[2].selectbox(
                "Candle intervals to test",
                ["Automatically compare 1, 5, and 15 minutes", "1Min only", "5Min only", "15Min only"],
                index=0,
                help="Automatic comparison downloads one-minute candles once, then builds the other intervals locally.",
            )
            optimizer_scope = first_row[3].selectbox(
                "Strategies to compare",
                ["All saved long strategies", "Approved strategies only"],
                index=0,
            )

            second_row = st.columns(4)
            optimizer_depth = second_row[0].selectbox(
                "Search depth",
                ["Quick — 16 combinations", "Balanced — 36 combinations", "Thorough — 64 combinations"],
                index=1,
                help="The combination limit applies to each saved strategy. Thorough searches take longer.",
            )
            minimum_training = second_row[1].number_input(
                "Minimum training trades",
                min_value=1,
                max_value=100,
                value=5,
                step=1,
            )
            minimum_validation = second_row[2].number_input(
                "Minimum validation trades",
                min_value=1,
                max_value=30,
                value=2,
                step=1,
            )
            optimizer_cash = second_row[3].number_input(
                "Starting cash ($)",
                min_value=100.0,
                value=2_000.0,
                step=100.0,
            )

            third_row = st.columns(4)
            optimizer_risk = third_row[0].number_input(
                "Maximum risk per trade to test (%)",
                min_value=0.05,
                max_value=10.0,
                value=2.0,
                step=0.05,
                help="The optimizer compares lower risk levels and will never recommend more than this ceiling.",
            )
            optimizer_position = third_row[1].number_input(
                "Maximum position size to test (%)",
                min_value=1.0,
                max_value=100.0,
                value=100.0,
                step=1.0,
                help="The optimizer compares smaller allocations and will never exceed this percentage of your account.",
            )
            optimizer_stop = third_row[2].number_input(
                "Fallback stop (%)",
                min_value=0.1,
                max_value=30.0,
                value=2.0,
                step=0.1,
            )
            optimizer_reward = third_row[3].number_input(
                "Fallback reward/risk",
                min_value=0.2,
                max_value=10.0,
                value=2.0,
                step=0.1,
            )

            cost_row = st.columns(3)
            optimizer_spread = cost_row[0].number_input(
                "Spread estimate (bps)",
                min_value=0.0,
                max_value=500.0,
                value=12.0,
                step=1.0,
            )
            optimizer_slippage = cost_row[1].number_input(
                "Slippage per fill (bps)",
                min_value=0.0,
                max_value=500.0,
                value=8.0,
                step=1.0,
            )
            optimizer_fee = cost_row[2].number_input(
                "Fee per order ($)",
                min_value=0.0,
                max_value=50.0,
                value=0.0,
                step=0.1,
            )
            protection_row = st.columns(3)
            optimizer_drawdown = protection_row[0].number_input(
                "Maximum acceptable drawdown (%)",
                min_value=0.5,
                max_value=50.0,
                value=15.0,
                step=0.5,
                help="Settings that exceed this historical loss limit receive a strong ranking penalty.",
            )
            sizing_depth = protection_row[1].selectbox(
                "Position-size search depth",
                ["Quick — 4 sizing combinations", "Balanced — 7 sizing combinations", "Thorough — 12 sizing combinations"],
                index=1,
            )
            use_live_spread = protection_row[2].checkbox(
                "Use the stock's actual quoted spread",
                value=True,
                help="The optimizer uses whichever is larger: your spread estimate or the latest quoted spread.",
            )
            optimization_requested = st.form_submit_button(
                "Find the best strategy and settings for this stock",
                use_container_width=True,
            )

        if optimization_requested:
            requested_symbols = parse_symbols(optimizer_symbol_raw)
            if len(requested_symbols) != 1:
                st.error("Enter exactly one stock ticker, such as SDOT, LUCY, or NVDA.")
            else:
                ticker = requested_symbols[0]
                source_strategies = [
                    item for item in library["strategies"]
                    if optimizer_scope != "Approved strategies only" or item.get("approved")
                ]
                if not source_strategies:
                    st.error("No approved strategies are available. Choose all strategies or approve one first.")
                else:
                    limits = {"Quick": 16, "Balanced": 36, "Thorough": 64}
                    combination_limit = limits.get(optimizer_depth.split(" — ", 1)[0], 36)
                    sizing_limits = {"Quick": 4, "Balanced": 7, "Thorough": 12}
                    sizing_limit = sizing_limits.get(sizing_depth.split(" — ", 1)[0], 7)
                    engine_settings = BacktestSettings(
                        starting_cash=optimizer_cash,
                        risk_per_trade_pct=optimizer_risk,
                        max_position_pct=optimizer_position,
                        default_stop_pct=optimizer_stop,
                        default_reward_risk=optimizer_reward,
                        spread_bps=optimizer_spread,
                        slippage_bps=optimizer_slippage,
                        fee_per_order=optimizer_fee,
                    )
                    tuning_settings = OptimizationSettings(
                        max_variants_per_strategy=combination_limit,
                        finalists_per_strategy=min(7, combination_limit),
                        minimum_training_trades=int(minimum_training),
                        minimum_validation_trades=int(minimum_validation),
                        max_execution_variants_per_finalist=sizing_limit,
                        maximum_drawdown_pct=float(optimizer_drawdown),
                    )
                    try:
                        market = client()
                        observed_spread: float | None = None
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
                        delay = 16 if market.historical_feed == "sip" and market.live_feed != "sip" else 1
                        end = utc_now() - timedelta(minutes=delay)
                        start = end - timedelta(days=int(optimizer_history_days))
                        progress_bar = st.progress(0.0, text=f"Downloading historical candles for {ticker}…")
                        automatic_intervals = optimizer_timeframe.startswith("Automatically compare")
                        selected_interval = "1Min" if automatic_intervals else optimizer_timeframe.split(" ", 1)[0]
                        candles = market.bars([ticker], start=start, end=end, timeframe=selected_interval).get(ticker, [])

                        def optimization_progress(completed: int, total: int, message: str) -> None:
                            progress_bar.progress(min(1.0, completed / max(total, 1)), text=message)

                        if automatic_intervals:
                            report = optimize_stock_timeframes(
                                candles,
                                source_strategies,
                                ticker,
                                engine_settings,
                                tuning_settings,
                                progress=optimization_progress,
                            )
                        else:
                            report = optimize_stock_strategies(
                                candles,
                                source_strategies,
                                ticker,
                                engine_settings,
                                tuning_settings,
                                progress=optimization_progress,
                            )
                            report["timeframe"] = selected_interval
                            report["timeframes_tested"] = [selected_interval]
                            for candidate in report.get("rankings") or []:
                                candidate["timeframe"] = selected_interval
                        report["history_days"] = int(optimizer_history_days)
                        report["observed_spread_bps"] = observed_spread
                        if quote_warning:
                            report["warnings"] = list(dict.fromkeys([quote_warning, *(report.get("warnings") or [])]))
                        st.session_state["stock_optimization_report"] = report
                        progress_bar.progress(1.0, text=f"Strategy optimization complete for {ticker}")
                    except AppError as error:
                        st.error(str(error))

        saved_notice = st.session_state.pop("optimizer_saved_notice", None)
        if saved_notice:
            st.success(str(saved_notice))

        optimization_report = st.session_state.get("stock_optimization_report") or {}
        if optimization_report.get("rankings"):
            optimized_symbol = str(optimization_report.get("symbol") or "?")
            winning = optimization_report["winner"]
            validation = winning.get("validation_metrics") or {}
            holdout = winning.get("holdout_metrics") or {}
            section(
                f"Best strategy found for {optimized_symbol}",
                "Rankings use the separate validation period. Only the preselected winner is tested on the final untouched holdout.",
            )
            highlights = st.columns(5)
            metric_card(
                highlights[0],
                "Strategies compared",
                str(optimization_report.get("strategies_tested", 0)),
                f'{optimization_report.get("variants_tested", 0)} settings combinations tested',
            )
            metric_card(
                highlights[1],
                "Validation P/L",
                money(validation.get("net_pnl")),
                f'{int(safe_float(validation.get("trade_count"), 0) or 0)} separate validation trades',
                "good" if (safe_float(validation.get("net_pnl"), 0) or 0) > 0 else "bad",
            )
            metric_card(
                highlights[2],
                "Final holdout P/L",
                money(holdout.get("net_pnl")),
                f'{int(safe_float(holdout.get("trade_count"), 0) or 0)} untouched-period trades',
                "good" if (safe_float(holdout.get("net_pnl"), 0) or 0) > 0 else "bad",
            )
            metric_card(
                highlights[3],
                "Higher-cost P/L",
                money((winning.get("stress_metrics") or {}).get("net_pnl")),
                "Validation repeated with 50% higher spread and slippage",
            )
            metric_card(
                highlights[4],
                "Quality assessment",
                str(winning.get("status") or "UNKNOWN"),
                f'{optimization_report.get("session_count", 0)} trading sessions reviewed',
                "good" if winning.get("status") == "VALIDATED" else "bad",
            )
            st.markdown(
                f'**Top strategy:** {escape(winning.get("strategy_name") or "Unnamed strategy")}  \n'
                f'**Recommended candle interval:** {winning.get("timeframe") or optimization_report.get("timeframe") or "5Min"}  \n'
                f'**Training:** {optimization_report["training_sessions"][0]} to '
                f'{optimization_report["training_sessions"][-1]}  \n'
                f'**Validation:** {optimization_report["validation_sessions"][0]} to '
                f'{optimization_report["validation_sessions"][-1]}  \n'
                f'**Final untouched holdout:** {optimization_report["holdout_sessions"][0]} to '
                f'{optimization_report["holdout_sessions"][-1]}'
            )
            for warning in optimization_report.get("warnings") or []:
                st.warning(str(warning))

            winning_profile = winning.get("optimized_backtest_settings") or optimization_report.get("backtest_settings") or {}
            winning_rules = normalize_machine_rules(winning.get("optimized_rules"))
            section(
                f"Recommended trading settings for {optimized_symbol}",
                "These stock-specific settings are selected using validation performance and saved with the optimized strategy.",
            )
            recommendation_cards = st.columns(5)
            metric_card(
                recommendation_cards[0], "Risk per trade",
                percent(winning_profile.get("risk_per_trade_pct")),
                "Never exceeds your chosen risk ceiling",
            )
            metric_card(
                recommendation_cards[1], "Maximum position",
                percent(winning_profile.get("max_position_pct")),
                "Recommended share of account capital",
            )
            metric_card(
                recommendation_cards[2], "Stop loss",
                percent(winning_rules.get("stop_loss_pct") or winning_profile.get("default_stop_pct")),
                "Stock-specific protective stop",
            )
            recommended_reward = safe_float(winning_rules.get("reward_risk") or winning_profile.get("default_reward_risk"))
            metric_card(
                recommendation_cards[3], "Reward / risk",
                f"{recommended_reward:.2f}x" if recommended_reward is not None else "—",
                "Profit target relative to stop distance",
            )
            metric_card(
                recommendation_cards[4], "Best candle size",
                str(winning.get("timeframe") or optimization_report.get("timeframe") or "5Min"),
                f'{len(optimization_report.get("timeframes_tested") or [1])} interval(s) compared',
            )
            st.caption(
                f'Account cash stays fixed at {money(winning_profile.get("starting_cash"))}. '
                f'Every simulation includes {safe_float(winning_profile.get("spread_bps"), 0.0):.1f} bps spread, '
                f'{safe_float(winning_profile.get("slippage_bps"), 0.0):.1f} bps slippage per fill, '
                f'and {money(winning_profile.get("fee_per_order"))} per order.'
            )

            if optimization_report.get("timeframe_comparison"):
                section("Which candle interval fits this stock best?", "Intervals are compared only on the separate validation data; the final holdout is not used to choose one.")
                interval_rows = []
                for interval_result in optimization_report["timeframe_comparison"]:
                    interval_validation = interval_result.get("validation_metrics") or {}
                    interval_rows.append(
                        {
                            "Candle interval": interval_result.get("timeframe"),
                            "Best strategy": interval_result.get("strategy_name"),
                            "Validation P/L": money(interval_validation.get("net_pnl")),
                            "Validation drawdown": percent(interval_validation.get("max_drawdown_pct")),
                            "Validation trades": interval_validation.get("trade_count", 0),
                            "Assessment": interval_result.get("status"),
                            "Combinations tested": interval_result.get("variants_tested", 0),
                        }
                    )
                st.dataframe(pd.DataFrame(interval_rows), hide_index=True, use_container_width=True)

            section("Strategy rankings", "Every strategy is tested using stock-specific rules, position sizing, and realistic costs; only the overall winner receives a final holdout test.")
            rankings_table = []
            for index, candidate in enumerate(optimization_report["rankings"], start=1):
                candidate_validation = candidate.get("validation_metrics") or {}
                candidate_holdout = candidate.get("holdout_metrics") or {}
                candidate_rules = normalize_machine_rules(candidate.get("optimized_rules"))
                candidate_profile = candidate.get("optimized_backtest_settings") or {}
                rankings_table.append(
                    {
                        "Rank": index,
                        "Strategy": candidate.get("strategy_name"),
                        "Interval": candidate.get("timeframe") or optimization_report.get("timeframe"),
                        "Assessment": candidate.get("status"),
                        "Training P/L": money((candidate.get("training_metrics") or {}).get("net_pnl")),
                        "Validation P/L": money(candidate_validation.get("net_pnl")),
                        "Validation trades": candidate_validation.get("trade_count", 0),
                        "Higher-cost P/L": money((candidate.get("stress_metrics") or {}).get("net_pnl")),
                        "Final holdout P/L": money(candidate_holdout.get("net_pnl")) if candidate_holdout else "Winner only",
                        "Risk/trade": percent(candidate_profile.get("risk_per_trade_pct")),
                        "Max position": percent(candidate_profile.get("max_position_pct")),
                        "Validation drawdown": percent(candidate_validation.get("max_drawdown_pct")),
                        "Stop": percent(candidate_rules.get("stop_loss_pct")),
                        "Reward/risk": (
                            f'{candidate_rules["reward_risk"]:.2f}x'
                            if candidate_rules.get("reward_risk") is not None else "—"
                        ),
                        "Combinations": candidate.get("variants_tested", 0),
                    }
                )
            st.dataframe(pd.DataFrame(rankings_table), hide_index=True, use_container_width=True)

            labels = {
                f'{index}. {candidate.get("strategy_name") or "Unnamed strategy"} '
                f'— {candidate.get("timeframe") or optimization_report.get("timeframe") or "5Min"} '
                f'— {candidate.get("status")}': candidate
                for index, candidate in enumerate(optimization_report["rankings"], start=1)
            }
            inspected_label = st.selectbox(
                "Inspect an optimized strategy",
                list(labels),
                key="inspect_stock_optimization",
            )
            inspected = labels[inspected_label]
            adjusted = inspected.get("changed_rules") or {}
            adjusted_execution = inspected.get("changed_backtest_settings") or {}
            section("Exactly what changed for this stock", "The original strategy stays untouched. Save the new version to preload these values in Backtesting.")
            if adjusted or adjusted_execution:
                changed_rows = [
                    {
                        "Area": "Strategy rule",
                        "Setting": field_name.replace("_", " ").title(),
                        "Original": "Strategy default" if values.get("original") is None else str(values["original"]),
                        "Optimized": str(values.get("optimized")),
                    }
                    for field_name, values in adjusted.items()
                ]
                changed_rows.extend(
                    {
                        "Area": "Backtest / position sizing",
                        "Setting": field_name.replace("_", " ").title(),
                        "Original": str(values.get("original")),
                        "Optimized": str(values.get("optimized")),
                    }
                    for field_name, values in adjusted_execution.items()
                )
                st.dataframe(pd.DataFrame(changed_rows), hide_index=True, use_container_width=True)
            else:
                st.info("The original strategy settings already performed best among the combinations tested.")
            for limitation in inspected.get("limitations") or []:
                st.caption(f"Research limitation: {limitation}")

            is_winner = (
                inspected.get("source_strategy_id") == winning.get("source_strategy_id")
                and inspected.get("optimized_rules") == winning.get("optimized_rules")
                and inspected.get("timeframe") == winning.get("timeframe")
                and inspected.get("optimized_backtest_settings") == winning.get("optimized_backtest_settings")
            )
            if not is_winner:
                st.warning(
                    "Only the top-ranked strategy has an untouched final holdout result. "
                    "Saving a lower-ranked strategy does not provide the same independent confirmation."
                )
            if st.button(
                f"Save optimized {optimized_symbol} strategy",
                key=f"save_optimized_{optimized_symbol}_{inspected.get('source_strategy_id', 'unknown')}",
                use_container_width=True,
            ):
                try:
                    summary = {
                        "optimized_at": optimization_report.get("generated_at"),
                        "symbol": optimized_symbol,
                        "timeframe": inspected.get("timeframe") or optimization_report.get("timeframe"),
                        "history_days": optimization_report.get("history_days"),
                        "status": inspected.get("status"),
                        "score": inspected.get("score"),
                        "variants_tested": inspected.get("variants_tested"),
                        "training_metrics": inspected.get("training_metrics"),
                        "validation_metrics": inspected.get("validation_metrics"),
                        "stress_metrics": inspected.get("stress_metrics"),
                        "holdout_metrics": inspected.get("holdout_metrics") if is_winner else {},
                        "full_metrics": inspected.get("full_metrics") if is_winner else {},
                        "optimized_backtest_settings": inspected.get("optimized_backtest_settings") or {},
                        "observed_spread_bps": optimization_report.get("observed_spread_bps"),
                        "timeframes_tested": optimization_report.get("timeframes_tested") or [],
                    }
                    store.save_optimized_strategy(
                        str(inspected.get("source_strategy_id") or ""),
                        optimized_symbol,
                        inspected.get("optimized_rules") or {},
                        summary,
                    )
                    st.session_state["optimizer_saved_notice"] = (
                        f"Saved the {optimized_symbol}-specific strategy and its recommended risk, position size, "
                        "stop, reward target, trading costs, and candle interval. Select it in Backtesting "
                        "to load those settings automatically."
                    )
                    st.rerun()
                except AppError as error:
                    st.error(str(error))

            winner_result = optimization_report.get("winning_backtest") or {}
            equity = pd.DataFrame(winner_result.get("equity_curve") or [])
            if len(equity) > 1:
                section("Winning strategy equity curve", "The complete curve includes training, validation, and the final untouched holdout.")
                equity["timestamp"] = pd.to_datetime(equity["timestamp"], errors="coerce", utc=True)
                st.line_chart(equity.dropna(subset=["timestamp"]).set_index("timestamp")["equity"], height=260)


def run_live_scan(
    approved: list[dict[str, Any]],
    watchlist: list[str],
    include_movers: bool,
    include_active: bool,
    count: int,
    include_news: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    market = client()
    warnings: list[str] = []
    universe = list(watchlist)
    if include_movers:
        try:
            for symbol in market.movers(count):
                if symbol not in universe:
                    universe.append(symbol)
        except AppError as error:
            warnings.append(f"Top gainers were unavailable: {error}")
    if include_active:
        try:
            for symbol in market.most_active(count):
                if symbol not in universe:
                    universe.append(symbol)
        except AppError as error:
            warnings.append(f"Most-active stocks were unavailable: {error}")
    universe = universe[:100]
    if not universe:
        raise AppError("No tickers were available. Add a manual watchlist or enable a market screener.")

    snapshots = market.snapshots(universe)
    historical_end = utc_now() - timedelta(minutes=16 if market.historical_feed == "sip" and market.live_feed != "sip" else 1)
    daily_bars: dict[str, list[dict[str, Any]]] = {}
    try:
        daily_bars = market.bars(universe, start=historical_end - timedelta(days=40), end=historical_end, timeframe="1Day", max_pages=5)
    except AppError as error:
        warnings.append(f"Relative-volume baselines could not be loaded: {error}")
    stories: dict[str, list[dict[str, Any]]] = {}
    if include_news:
        try:
            stories = market.news(universe)
        except AppError as error:
            warnings.append(f"News lookup was unavailable: {error}")

    needs_chart_candles = any(
        any(
            normalize_machine_rules(strategy.get("machine_rules")).get(field_name)
            for field_name in (
                "vwap_reclaim", "breakout_lookback_bars", "opening_range_minutes",
                "volume_surge_ratio", "minimum_green_bars",
            )
        )
        for strategy in approved
    )
    intraday_bars: dict[str, list[dict[str, Any]]] = {}
    if needs_chart_candles:
        now_et = utc_now().astimezone(ET)
        session_day = now_et.date()
        if now_et.hour * 60 + now_et.minute < 9 * 60 + 30:
            session_day -= timedelta(days=1)
        while session_day.weekday() >= 5:
            session_day -= timedelta(days=1)
        start_local = datetime.combine(session_day, datetime.min.time(), tzinfo=ET).replace(hour=9, minute=30)
        try:
            intraday_bars = market.bars(
                universe,
                start=start_local,
                end=utc_now(),
                timeframe="1Min",
                feed=market.live_feed,
                max_pages=8,
            )
        except AppError as error:
            warnings.append(f"Current chart candles were unavailable; chart-specific triggers will be marked VERIFY: {error}")

    results: list[dict[str, Any]] = []
    for symbol in universe:
        snapshot = snapshots.get(symbol)
        if not snapshot:
            continue
        average_volume = average_completed_daily_volume(daily_bars.get(symbol, []))
        metrics = snapshot_metrics(symbol, snapshot, average_daily_volume=average_volume, news_items=stories.get(symbol))
        if metrics is None:
            continue
        matches = []
        for strategy in approved:
            optimized_symbol = str(strategy.get("optimized_for_symbol") or "").strip().upper()
            if optimized_symbol and optimized_symbol != symbol:
                continue
            enriched_metrics = dict(metrics)
            if intraday_bars.get(symbol):
                enriched_metrics["chart_checks"] = chart_trigger_checks(intraday_bars[symbol], strategy)
            matches.append(match_strategy(enriched_metrics, strategy))
        matches.sort(key=lambda item: (item["status"] == "MATCH", item["status"] == "VERIFY", item["score"]), reverse=True)
        if not matches:
            continue
        results.append({"metrics": metrics, "matches": matches, "best": matches[0]})
    rank = {"MATCH": 3, "VERIFY": 2, "WATCH": 1, "NO MATCH": 0}
    results.sort(key=lambda item: (rank.get(item["best"]["status"], 0), item["best"]["score"]), reverse=True)
    return results, warnings


with scanner_tab:
    section("Match live stocks against reviewed strategies", "Only strategies you explicitly approve are eligible. An unknown chart trigger is marked VERIFY—not silently treated as confirmed.")
    approved = [strategy for strategy in library["strategies"] if strategy.get("approved")]
    if not alpaca_ready:
        st.error("Configure your existing Alpaca API key and secret in this new app first.")
    elif not approved:
        st.info("Open Strategy library, review a strategy, and check ‘Approve this strategy for matching in the live scanner.’")
    else:
        st.caption(f"{len(approved)} approved strategies are available for live matching.")
        with st.form("live_scan_form"):
            symbols_raw = st.text_input("Your watchlist tickers", placeholder="SDOT, LUCY, NVDA")
            source_columns = st.columns(4)
            movers_enabled = source_columns[0].checkbox("Include top gainers", value=True)
            active_enabled = source_columns[1].checkbox("Include most-active stocks", value=False)
            news_enabled = source_columns[2].checkbox("Check recent news", value=True)
            candidate_count = source_columns[3].number_input("Candidates per screener", min_value=5, max_value=50, value=20, step=5)
            scan_requested = st.form_submit_button("Run fresh strategy scan", use_container_width=True)
        if scan_requested:
            try:
                with st.spinner("Checking live Alpaca snapshots against your approved YouTube strategies…"):
                    results, warnings = run_live_scan(approved, parse_symbols(symbols_raw), movers_enabled, active_enabled, int(candidate_count), news_enabled)
                st.session_state["live_scan"] = results
                st.session_state["live_scan_warnings"] = warnings
                st.session_state["live_scan_at"] = isoformat_utc(utc_now())
            except AppError as error:
                st.error(str(error))

        for warning in st.session_state.get("live_scan_warnings") or []:
            st.warning(warning)
        results = st.session_state.get("live_scan") or []
        if results:
            st.caption(f'Last scan: {local_timestamp(st.session_state.get("live_scan_at"))}')
            summary_rows = []
            for item in results:
                metrics, best = item["metrics"], item["best"]
                summary_rows.append(
                    {
                        "Ticker": metrics["symbol"],
                        "Status": best["status"],
                        "Rule match": f'{best["score"]:.0f}%',
                        "Strategy": best["strategy_name"],
                        "Price": money(metrics["price"]),
                        "Today": percent(metrics.get("day_change_pct"), signed=True),
                        "Relative volume": f'{metrics["relative_volume"]:.2f}x' if metrics.get("relative_volume") is not None else "—",
                        "Spread": percent(metrics.get("spread_pct")),
                        "VWAP": "Above" if metrics.get("above_vwap") else "Below / unavailable",
                    }
                )
            st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)

            section("Best current setups", "MATCH means measured rules passed. VERIFY means a chart-specific trigger still needs confirmation.")
            for item in results[:15]:
                metrics, best = item["metrics"], item["best"]
                color = {"MATCH": "green", "VERIFY": "blue", "WATCH": "orange", "NO MATCH": "red"}[best["status"]]
                pills = (
                    status_pill(best["status"], color)
                    + status_pill("ABOVE VWAP" if metrics.get("above_vwap") else "BELOW VWAP", "green" if metrics.get("above_vwap") else "red")
                    + (status_pill("NEWS", "violet") if metrics.get("has_catalyst") else "")
                )
                st.markdown(
                    f'<div class="match-card"><div class="match-heading"><div><div class="match-symbol">{escape(metrics["symbol"])}</div>'
                    f'<div class="muted">{escape(best["strategy_name"])}</div></div>'
                    f'<div class="match-score">{escape(best["score"])}%</div></div>{pills}'
                    f'<p><strong>{escape(money(metrics["price"]))}</strong> · Today {escape(percent(metrics.get("day_change_pct"), signed=True))}'
                    f' · Volume {escape(compact(metrics.get("volume")))} · Dollar volume ${escape(compact(metrics.get("dollar_volume")))}'
                    f' · Spread {escape(percent(metrics.get("spread_pct")))}</p>'
                    f'<p class="muted">Video-rule stop: {escape(money(best.get("suggested_stop")))} · '
                    f'Target: {escape(money(best.get("suggested_target")))} · '
                    f'{escape(best["passed"])} passed / {escape(best["unknown"])} need chart confirmation</p></div>',
                    unsafe_allow_html=True,
                )
                with st.expander(f'{metrics["symbol"]} — see matched rules and news', expanded=False):
                    checks = [
                        {"Rule": rule["label"], "Actual": str(rule["actual"]), "Required": str(rule["required"]), "Result": rule["status"].upper()}
                        for rule in best["checks"]
                    ]
                    if checks:
                        st.dataframe(pd.DataFrame(checks), hide_index=True, use_container_width=True)
                    for article in (metrics.get("news") or [])[:3]:
                        title = str(article.get("headline") or "News item")
                        url = article.get("url")
                        st.markdown(f"[{title}]({url})" if url else title)
                        st.caption(f'Published {local_timestamp(article.get("created_at") or article.get("updated_at"))}')
                    if st.button("Create paper trade from this setup", key=f'paper_from_scan_{metrics["symbol"]}_{best["strategy_id"]}'):
                        st.session_state["paper_prefill"] = {
                            "symbol": metrics["symbol"],
                            "entry_price": metrics["price"],
                            "stop_price": best.get("suggested_stop"),
                            "target_price": best.get("suggested_target"),
                            "strategy_id": best.get("strategy_id"),
                            "strategy_name": best.get("strategy_name"),
                        }
                        st.success("Setup copied. Open the Paper journal tab to enter your practice position.")


with paper_tab:
    section("Practice trades without placing brokerage orders", "Open, edit, close, or delete positions. This journal records research decisions and never connects to your brokerage's order endpoint.")
    positions = library["paper_positions"]
    opened = [item for item in positions if item.get("status") == "open"]
    closed = [item for item in positions if item.get("status") == "closed"]
    realized = sum(safe_float(item.get("realized_pnl"), 0.0) or 0.0 for item in closed)
    journal_cards = st.columns(3)
    metric_card(journal_cards[0], "Open paper positions", str(len(opened)), "Editable practice trades")
    metric_card(journal_cards[1], "Closed paper positions", str(len(closed)), "Finished practice trades")
    metric_card(journal_cards[2], "Realized practice P/L", money(realized), "Not actual brokerage performance", "good" if realized > 0 else ("bad" if realized < 0 else ""))

    prefill = st.session_state.get("paper_prefill") or {}
    with st.form("new_paper_position"):
        first, second, third = st.columns(3)
        paper_symbol = first.text_input("Ticker", value=str(prefill.get("symbol") or ""))
        paper_entry = second.number_input("Entry price ($)", min_value=0.0, value=float(prefill.get("entry_price") or 0.0), step=0.01, format="%.4f")
        paper_quantity = third.number_input("Shares", min_value=1, value=10, step=1)
        details = st.columns(3)
        paper_stop = details[0].number_input("Stop price ($), optional", min_value=0.0, value=float(prefill.get("stop_price") or 0.0), step=0.01, format="%.4f")
        paper_target = details[1].number_input("Target price ($), optional", min_value=0.0, value=float(prefill.get("target_price") or 0.0), step=0.01, format="%.4f")
        paper_strategy = details[2].text_input("Strategy name", value=str(prefill.get("strategy_name") or ""))
        paper_notes = st.text_area("Trade notes", height=80, placeholder="Why is this setup valid? What would invalidate it?")
        add_position = st.form_submit_button("Save paper position", use_container_width=True)
    if add_position:
        try:
            store.add_position(
                {
                    "symbol": paper_symbol,
                    "entry_price": paper_entry,
                    "quantity": paper_quantity,
                    "stop_price": paper_stop or None,
                    "target_price": paper_target or None,
                    "strategy_id": prefill.get("strategy_id"),
                    "strategy_name": paper_strategy,
                    "notes": paper_notes,
                }
            )
            st.session_state.pop("paper_prefill", None)
            st.rerun()
        except AppError as error:
            st.error(str(error))

    live_prices: dict[str, float] = {}
    if opened and alpaca_ready and st.button("Update open positions with live prices", key="refresh_paper_prices"):
        try:
            snapshots = client().snapshots(parse_symbols([item.get("symbol", "") for item in opened]))
            for symbol, snapshot in snapshots.items():
                metrics = snapshot_metrics(symbol, snapshot)
                if metrics:
                    live_prices[symbol] = float(metrics["price"])
            st.session_state["paper_live_prices"] = live_prices
        except AppError as error:
            st.error(str(error))
    live_prices = st.session_state.get("paper_live_prices") or {}

    if positions:
        section("Saved paper positions", "Every position can be edited or deleted if you entered the wrong information.")
    for position in positions:
        symbol = str(position.get("symbol") or "?")
        quantity = safe_float(position.get("quantity"), 0.0) or 0.0
        entry_price = safe_float(position.get("entry_price"), 0.0) or 0.0
        last_price = live_prices.get(symbol)
        unrealized = (last_price - entry_price) * quantity if last_price is not None and position.get("status") == "open" else None
        label = f'{symbol} · {position.get("status", "open").upper()} · {quantity:g} shares @ {money(entry_price)}'
        if position.get("status") == "closed":
            label += f' · P/L {money(position.get("realized_pnl"))}'
        elif unrealized is not None:
            label += f" · Unrealized {money(unrealized)}"
        with st.expander(label, expanded=False):
            st.caption(f'Opened {local_timestamp(position.get("opened_at"))}')
            if last_price is not None:
                st.caption(f"Last checked price: {money(last_price)}")
            with st.form(f'edit_paper_{position["id"]}'):
                edit_columns = st.columns(3)
                edit_quantity = edit_columns[0].number_input("Shares", min_value=1.0, value=float(quantity), step=1.0)
                edit_entry = edit_columns[1].number_input("Entry price ($)", min_value=0.0001, value=max(entry_price, 0.0001), step=0.01, format="%.4f")
                existing_exit = safe_float(position.get("exit_price")) or last_price or 0.0
                edit_exit = edit_columns[2].number_input("Close / exit price ($)", min_value=0.0, value=float(existing_exit), step=0.01, format="%.4f")
                more = st.columns(2)
                edit_stop = more[0].number_input("Stop ($)", min_value=0.0, value=float(safe_float(position.get("stop_price"), 0.0) or 0.0), step=0.01, format="%.4f")
                edit_target = more[1].number_input("Target ($)", min_value=0.0, value=float(safe_float(position.get("target_price"), 0.0) or 0.0), step=0.01, format="%.4f")
                edit_notes = st.text_area("Notes", value=str(position.get("notes") or ""), height=75)
                button_a, button_b = st.columns(2)
                save_edits = button_a.form_submit_button("Save edits", use_container_width=True)
                close_trade = button_b.form_submit_button("Close paper trade", use_container_width=True)
            if save_edits or close_trade:
                try:
                    update = {"quantity": edit_quantity, "entry_price": edit_entry, "stop_price": edit_stop or None, "target_price": edit_target or None, "notes": edit_notes}
                    if close_trade:
                        update.update({"status": "closed", "exit_price": edit_exit})
                    store.update_position(position["id"], update)
                    st.rerun()
                except AppError as error:
                    st.error(str(error))
            if st.button("Delete this paper position", key=f'delete_paper_{position["id"]}'):
                store.delete_position(position["id"])
                st.rerun()


with settings_tab:
    section(
        "Permanent private cloud storage",
        "Automatically keep videos, extracted and master strategies, stock-specific settings, recovery history, and paper trades after Streamlit restarts.",
    )
    cloud_status = store.cloud_status()
    if cloud_status["configured"]:
        st.success(f'Automatic private GitHub backup is configured: {cloud_status["repository"]}')
        st.caption(f'Backup file: {cloud_status["path"]}')
        if cloud_status.get("last_synced_at"):
            st.caption(f'Last successful cloud backup: {local_timestamp(cloud_status["last_synced_at"])}')
        else:
            st.info("Your first permanent backup is created automatically when your strategy library changes.")
        if cloud_status.get("last_error"):
            st.error(cloud_status["last_error"])
        if store.restored_on_startup:
            st.success("This app automatically recovered its saved library from your private cloud backup.")
        backup_left, backup_right = st.columns(2)
        if backup_left.button("Back up current library now", key="sync_private_github_backup", use_container_width=True):
            try:
                store.sync_cloud_backup()
                st.success("Your current library was saved to the private GitHub backup.")
                st.rerun()
            except AppError as error:
                st.error(str(error))
        if backup_right.button("Restore latest cloud backup", key="restore_private_github_backup", use_container_width=True):
            try:
                store.restore_cloud_backup()
                st.success("The latest private GitHub cloud backup was restored.")
                st.rerun()
            except AppError as error:
                st.error(str(error))
        st.caption("Restoring keeps a local recovery snapshot first. The private GitHub repository also preserves previous committed versions.")
    else:
        if cloud_configuration_error:
            st.error(cloud_configuration_error)
        else:
            st.warning("Permanent cloud storage is not configured. Streamlit can erase locally saved records when it replaces this app's server.")
        st.markdown(
            "1. Create a **separate private GitHub repository**, such as `youtube-trading-strategy-backups`, and initialize it with a README.\n"
            "2. Create a fine-grained GitHub personal access token for **that private repository only** with **Contents: Read and write** permission.\n"
            "3. In this Streamlit app, open **Settings → Secrets** and add the two backup values below.\n"
            "4. Save the secrets. The app restarts, then automatically protects every future saved change."
        )
        st.code(
            'GITHUB_BACKUP_REPOSITORY="derektshaffer/youtube-trading-strategy-backups"\n'
            'GITHUB_BACKUP_TOKEN="paste-your-fine-grained-github-token-here"',
            language="toml",
        )
        st.caption("The backup repository must be private. Do not place your token or strategy backup inside the public application repository.")

    section(
        "Recently deleted",
        "Restore accidentally deleted videos, strategies, and paper positions with their exact saved settings.",
    )
    recoverable_items = library.get("recovery_items") or []
    if recoverable_items:
        for recovered in recoverable_items[:30]:
            deleted_label = str(recovered.get("title") or "Deleted item")
            kind = str(recovered.get("kind") or "item").replace("_", " ")
            recovered_strategies = recovered.get("strategies") or []
            with st.expander(
                f'{deleted_label} · {kind} · {local_timestamp(recovered.get("deleted_at"))}',
                expanded=False,
            ):
                if recovered_strategies:
                    st.markdown("**Strategies that will be restored:**")
                    for previous_strategy in recovered_strategies:
                        checkpoint_test = previous_strategy.get("last_backtest") or {}
                        label = str(previous_strategy.get("name") or "Unnamed strategy")
                        if checkpoint_test:
                            label += (
                                f' · Last net {money(checkpoint_test.get("net_pnl"))}'
                                f' · Holdout {money(checkpoint_test.get("holdout_net_pnl"))}'
                            )
                        st.markdown(f"- {label}")
                if st.button("Restore deleted item", key=f'restore_deleted_{recovered["id"]}', use_container_width=True):
                    try:
                        store.restore_recovery_item(recovered["id"])
                        st.rerun()
                    except AppError as error:
                        st.error(str(error))
    else:
        st.info("Nothing is currently in recently deleted. Future deletions will appear here automatically.")

    section(
        "Automatic recovery backups",
        "Local snapshots support quick recovery; configured private GitHub cloud backups also survive Streamlit server resets.",
    )
    automatic_backups = store.list_automatic_backups()
    if automatic_backups:
        backup_options = {
            (
                f'{local_timestamp(item.get("saved_at"))} · '
                f'{item["strategies"]} strategies · {item["videos"]} videos · '
                f'{item["paper_positions"]} paper positions · {item["id"][-13:-5]}'
            ): item
            for item in automatic_backups
        }
        selected_backup_label = st.selectbox(
            "Choose an automatic backup to merge with the current library",
            list(backup_options),
            key="automatic_strategy_backup",
        )
        if st.button("Restore selected automatic backup", key="restore_automatic_backup", use_container_width=True):
            try:
                store.restore_automatic_backup(backup_options[selected_backup_label]["id"])
                st.rerun()
            except AppError as error:
                st.error(str(error))
    else:
        st.caption("Automatic recovery snapshots will be created the next time the strategy library changes.")

    section("Deploy this as a completely separate app", "Your original stock analyzer and its Streamlit deployment do not need to be edited, deleted, or replaced.")
    st.markdown(
        "1. Create a **new GitHub repository**, such as `youtube-trading-strategy-lab`.\n"
        "2. Upload `youtube_strategy_app.py`, `youtube_strategy_engine.py`, and `requirements.txt`.\n"
        "3. In Streamlit Community Cloud, choose **Create app** and select the new repository.\n"
        "4. Set the main file path to `youtube_strategy_app.py`.\n"
        "5. Add the three required secrets below. Your Alpaca values can be the same ones used by your other app.\n"
        "6. Deploy. Your original analyzer remains a separate app with its own URL."
    )
    st.code(
        'ALPACA_API_KEY="paste your existing Alpaca API key"\n'
        'ALPACA_SECRET_KEY="paste your existing Alpaca secret key"\n'
        'GEMINI_API_KEY="paste your FREE Google Gemini API key"\n\n'
        '# Optional settings:\n'
        'GEMINI_PAID_API_KEY="paste a key from a SEPARATE PAID Google project"\n'
        'ALPACA_LIVE_FEED="iex"\n'
        'ALPACA_HISTORICAL_FEED="sip"\n'
        f'GEMINI_MODEL="{DEFAULT_GEMINI_MODEL}"',
        language="toml",
    )
    st.markdown("[Get your Google Gemini API key](https://aistudio.google.com/apikey)")
    st.info(
        "To use free Gemini analysis before spending credits, keep GEMINI_API_KEY on a free Google project. "
        "Create a separate paid project and add its key as GEMINI_PAID_API_KEY. "
        "The app switches automatically only if the free key reaches its quota."
    )
    st.info(
        "On Alpaca Basic, the live feed covers IEX only. The app requests historical SIP data with a "
        "16-minute delay. If you subscribe to consolidated real-time data later, change "
        '`ALPACA_LIVE_FEED="sip"` in this app’s secrets.'
    )

    section(
        "Export and restore your strategy library",
        "Download an additional personal backup whenever you want. Configured private GitHub cloud storage also saves changes automatically.",
    )
    export_data = json.dumps(library, indent=2, default=str).encode("utf-8")
    st.download_button("Download strategy and paper-trade backup", data=export_data, file_name="youtube_strategy_library_backup.json", mime="application/json", use_container_width=True)
    imported = st.file_uploader("Import a previously exported strategy backup", type=["json"])
    if imported is not None and st.button("Merge imported backup into this app", key="import_backup"):
        try:
            store.import_data(imported.getvalue())
            st.success("Backup imported and merged with existing records.")
            st.rerun()
        except AppError as error:
            st.error(str(error))

    section("Safety and limitations")
    st.markdown(
        "- AI-extracted rules can be wrong. Review the linked timestamps and chart evidence.\n"
        "- Historical backtests can miss float, borrowing constraints, halts, exact spreads, and time-correct catalysts.\n"
        "- IEX-only live data can underrepresent activity in thinly traded or fast-moving stocks.\n"
        "- Backtests, paper results, and YouTube examples do not establish future profitability.\n"
        "- This app never places brokerage orders and keeps its files independent from your existing analyzer."
    )
