from pathlib import Path

app_path = Path("youtube_strategy_app.py")
engine_path = Path("youtube_strategy_engine.py")
app = app_path.read_text(encoding="utf-8")
engine = engine_path.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# 1) StrategyStore: allow an explicit saved name for stock-optimized strategies.
# ---------------------------------------------------------------------------
sig_old = '''    def save_optimized_strategy(
        self,
        source_strategy_id: str,
        symbol: str,
        machine_rules: dict[str, Any],
        optimization_summary: dict[str, Any] | None = None,
'''
sig_new = '''    def save_optimized_strategy(
        self,
        source_strategy_id: str,
        symbol: str,
        machine_rules: dict[str, Any],
        optimization_summary: dict[str, Any] | None = None,
        custom_name: str | None = None,
'''
if sig_new not in engine:
    if sig_old not in engine:
        raise SystemExit("Could not find save_optimized_strategy signature")
    engine = engine.replace(sig_old, sig_new, 1)

name_anchor = '''        optimized = json.loads(json.dumps(source, default=str))
        source_name = str(source.get("name") or "Trading strategy")
        summary = optimization_summary or {}
'''
name_replacement = '''        optimized = json.loads(json.dumps(source, default=str))
        source_name = str(source.get("name") or "Trading strategy")
        saved_name = str(custom_name or "").strip()[:120] or f"{source_name} — {target_symbol} optimized"
        summary = optimization_summary or {}
'''
if name_replacement not in engine:
    if name_anchor not in engine:
        raise SystemExit("Could not find optimized strategy name anchor")
    engine = engine.replace(name_anchor, name_replacement, 1)

engine = engine.replace(
    '                "name": f"{source_name} — {target_symbol} optimized",',
    '                "name": saved_name,',
    1,
)

# ---------------------------------------------------------------------------
# 2) Global click acknowledgement + persistent success styling.
#    Focus/active means the click registered; actual operation success/error is still
#    reported separately, so a click acknowledgment cannot hide a failed operation.
# ---------------------------------------------------------------------------
css_anchor = '''.stApp .stButton button:hover,
.stApp .stDownloadButton button:hover,
.stApp [data-testid="stFormSubmitButton"] button:hover {
 border-color:#7ac7ff !important;
 background:linear-gradient(115deg,#25567f,#316fa1) !important;
}
div[data-baseweb="tab-list"] {gap:14px}
'''
css_replacement = '''.stApp .stButton button:hover,
.stApp .stDownloadButton button:hover,
.stApp [data-testid="stFormSubmitButton"] button:hover {
 border-color:#7ac7ff !important;
 background:linear-gradient(115deg,#25567f,#316fa1) !important;
}
/* Immediate visual acknowledgement that a button click registered. */
.stApp .stButton button:focus:not(:disabled),
.stApp .stButton button:active:not(:disabled),
.stApp [data-testid="stFormSubmitButton"] button:focus:not(:disabled),
.stApp [data-testid="stFormSubmitButton"] button:active:not(:disabled) {
 border-color:#48e0a8 !important;
 background:linear-gradient(115deg,#13724f,#1d8e65) !important;
 box-shadow:0 0 0 2px rgba(53,213,151,.20) !important;
}
.stApp .stButton button:focus:not(:disabled) p::before,
.stApp .stButton button:active:not(:disabled) p::before,
.stApp [data-testid="stFormSubmitButton"] button:focus:not(:disabled) p::before,
.stApp [data-testid="stFormSubmitButton"] button:active:not(:disabled) p::before {
 content:"✓ "; font-weight:900;
}
.action-success {
 border:1px solid rgba(53,213,151,.65); border-radius:10px; padding:12px 14px;
 background:linear-gradient(115deg,rgba(19,114,79,.95),rgba(29,142,101,.92));
 color:#f4fff9; font-weight:850; text-align:center; margin-top:8px; margin-bottom:8px;
}
div[data-baseweb="tab-list"] {gap:14px}
'''
if css_replacement not in app:
    if css_anchor not in app:
        raise SystemExit("Could not find button CSS anchor")
    app = app.replace(css_anchor, css_replacement, 1)

helper_anchor = '''def status_pill(label: str, color: str = "blue") -> str:
    return f'<span class="pill pill-{escape(color)}">{escape(label)}</span>'


'''
helper_replacement = helper_anchor + '''def action_success(message: str) -> None:
    st.markdown(f'<div class="action-success">✓ {escape(message)}</div>', unsafe_allow_html=True)


def analyzed_youtube_video_index(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return canonical URLs for videos already represented in the saved library."""
    result: dict[str, dict[str, Any]] = {}
    for video in data.get("videos") or []:
        if not isinstance(video, dict):
            continue
        raw_url = str(video.get("url") or video.get("source_url") or "").strip()
        if not raw_url:
            continue
        try:
            normalized_url = normalize_youtube_url(raw_url)
        except AppError:
            continue
        result[normalized_url] = {
            "url": normalized_url,
            "video_title": video.get("video_title") or "Untitled video",
            "creator": video.get("creator") or "Unknown creator",
            "analyzed_at": video.get("analyzed_at"),
        }
    # Protect legacy/restored records even if their top-level video object is missing.
    for strategy in video_source_strategies(data.get("strategies") or []):
        raw_url = str(strategy.get("source_url") or "").strip()
        if not raw_url:
            continue
        try:
            normalized_url = normalize_youtube_url(raw_url)
        except AppError:
            continue
        result.setdefault(
            normalized_url,
            {
                "url": normalized_url,
                "video_title": strategy.get("video_title") or strategy.get("source_video_title") or "Previously analyzed video",
                "creator": strategy.get("creator") or "Unknown creator",
                "analyzed_at": strategy.get("created_at") or strategy.get("analyzed_at"),
            },
        )
    return result


'''
if "def action_success(" not in app or "def analyzed_youtube_video_index(" not in app:
    if helper_anchor not in app:
        raise SystemExit("Could not find status_pill helper anchor")
    # If one helper somehow already exists, avoid duplicate definitions by adding only missing ones.
    additions = ""
    if "def action_success(" not in app:
        additions += '''def action_success(message: str) -> None:\n    st.markdown(f'<div class="action-success">✓ {escape(message)}</div>', unsafe_allow_html=True)\n\n\n'''
    if "def analyzed_youtube_video_index(" not in app:
        additions += '''def analyzed_youtube_video_index(data: dict[str, Any]) -> dict[str, dict[str, Any]]:\n    """Return canonical URLs for videos already represented in the saved library."""\n    result: dict[str, dict[str, Any]] = {}\n    for video in data.get("videos") or []:\n        if not isinstance(video, dict):\n            continue\n        raw_url = str(video.get("url") or video.get("source_url") or "").strip()\n        if not raw_url:\n            continue\n        try:\n            normalized_url = normalize_youtube_url(raw_url)\n        except AppError:\n            continue\n        result[normalized_url] = {\n            "url": normalized_url,\n            "video_title": video.get("video_title") or "Untitled video",\n            "creator": video.get("creator") or "Unknown creator",\n            "analyzed_at": video.get("analyzed_at"),\n        }\n    for strategy in video_source_strategies(data.get("strategies") or []):\n        raw_url = str(strategy.get("source_url") or "").strip()\n        if not raw_url:\n            continue\n        try:\n            normalized_url = normalize_youtube_url(raw_url)\n        except AppError:\n            continue\n        result.setdefault(normalized_url, {\n            "url": normalized_url,\n            "video_title": strategy.get("video_title") or strategy.get("source_video_title") or "Previously analyzed video",\n            "creator": strategy.get("creator") or "Unknown creator",\n            "analyzed_at": strategy.get("created_at") or strategy.get("analyzed_at"),\n        })\n    return result\n\n\n'''
    app = app.replace(helper_anchor, helper_anchor + additions, 1)

# ---------------------------------------------------------------------------
# 3) Duplicate YouTube protection (fixed version of the previous failed patch).
# ---------------------------------------------------------------------------
videos_start = '''with videos_tab:
    section("Give the AI trading videos to inspect", "Paste one public YouTube video link per line. The app reads both the visible charts and the audio.")
    with st.form("analyze_video_form"):
'''
if "Duplicate protection is on" not in app:
    videos_replacement = '''with videos_tab:
    analyzed_video_lookup = analyzed_youtube_video_index(library)
    section(
        "Previously analyzed YouTube videos",
        "Duplicate protection is on. Videos already listed here are skipped unless you explicitly allow re-analysis.",
    )
    if analyzed_video_lookup:
        analyzed_video_rows = [
            {
                "Video": item.get("video_title") or "Untitled video",
                "Creator": item.get("creator") or "Unknown creator",
                "Analyzed": local_timestamp(item.get("analyzed_at")) if item.get("analyzed_at") else "Previously saved",
                "YouTube URL": item.get("url"),
            }
            for item in analyzed_video_lookup.values()
        ]
        st.dataframe(pd.DataFrame(analyzed_video_rows), hide_index=True, use_container_width=True)
        st.caption(f"{len(analyzed_video_lookup)} unique YouTube video(s) are already in your library.")
    else:
        st.info("No previously analyzed YouTube videos are currently saved.")

    section("Give the AI trading videos to inspect", "Paste one public YouTube video link per line. The app reads both the visible charts and the audio.")
    with st.form("analyze_video_form"):
'''
    if videos_start not in app:
        raise SystemExit("Could not find Analyze Videos tab start")
    app = app.replace(videos_start, videos_replacement, 1)

submit_anchor = '''        submitted = st.form_submit_button("Analyze YouTube videos", use_container_width=True)
'''
if "allow_duplicate_reanalysis = st.checkbox(" not in app:
    submit_replacement = '''        allow_duplicate_reanalysis = st.checkbox(
            "Allow re-analysis of videos already in my library",
            value=False,
            help="Leave this off to prevent accidental duplicate analysis and unnecessary Gemini usage.",
        )
        submitted = st.form_submit_button("Analyze YouTube videos", use_container_width=True)
'''
    if submit_anchor not in app:
        raise SystemExit("Could not find Analyze Videos submit button")
    app = app.replace(submit_anchor, submit_replacement, 1)

submitted_anchor = '''    if submitted:
        urls, invalid = parse_youtube_urls(raw_urls)
        duration_override = None
'''
if "duplicate_urls = [url for url in urls if url in analyzed_video_lookup]" not in app:
    submitted_replacement = '''    if submitted:
        urls, invalid = parse_youtube_urls(raw_urls)
        duplicate_urls = [url for url in urls if url in analyzed_video_lookup]
        if duplicate_urls:
            duplicate_names = [
                str(analyzed_video_lookup[url].get("video_title") or url)
                for url in duplicate_urls
            ]
            st.warning(
                "Already analyzed — duplicate protection caught: "
                + "; ".join(duplicate_names[:12])
                + (f"; and {len(duplicate_names) - 12} more" if len(duplicate_names) > 12 else "")
            )
            if not allow_duplicate_reanalysis:
                duplicate_set = set(duplicate_urls)
                urls = [url for url in urls if url not in duplicate_set]
                st.info(f"Skipped {len(duplicate_urls)} already-analyzed video(s). They will not be sent to Gemini again.")
        duration_override = None
'''
    if submitted_anchor not in app:
        raise SystemExit("Could not find submitted video block")
    app = app.replace(submitted_anchor, submitted_replacement, 1)

no_urls_anchor = '''        if not urls:
            st.error("Add at least one valid public YouTube video link.")
        elif duration_error:
'''
if "Nothing new to analyze" not in app:
    no_urls_replacement = '''        if not urls:
            if duplicate_urls and not allow_duplicate_reanalysis:
                st.warning(
                    "Nothing new to analyze. Every valid YouTube link entered is already in your library. "
                    "Enable re-analysis only if you intentionally want to process it again."
                )
            else:
                st.error("Add at least one valid public YouTube video link.")
        elif duration_error:
'''
    if no_urls_anchor not in app:
        raise SystemExit("Could not find no-URLs validation block")
    app = app.replace(no_urls_anchor, no_urls_replacement, 1)

# ---------------------------------------------------------------------------
# 4) Editable name + durable green save confirmation for optimized strategies.
# ---------------------------------------------------------------------------
save_warning_anchor = '''            if st.button(
                f"Save optimized {optimized_symbol} strategy",
                key=f"save_optimized_{optimized_symbol}_{inspected.get('source_strategy_id', 'unknown')}",
                use_container_width=True,
                disabled=save_blocked_for_sample,
            ):
                try:
'''
if "Saved strategy name" not in app:
    save_replacement = '''            source_strategy_name = str(inspected.get("strategy_name") or "Trading strategy")
            default_optimized_name = f"{source_strategy_name} — {optimized_symbol} optimized"
            saved_name_key = f"optimized_saved_name_{optimized_symbol}_{inspected.get('source_strategy_id', 'unknown')}"
            custom_optimized_name = st.text_input(
                "Saved strategy name",
                value=default_optimized_name,
                key=saved_name_key,
                max_chars=120,
                help="Edit this before saving. This is the name that will appear in Strategy library and Backtesting.",
            ).strip()
            save_confirmation = st.session_state.get("optimized_save_confirmation") or {}
            saved_this_exact_name = (
                save_confirmation.get("symbol") == optimized_symbol
                and save_confirmation.get("source_strategy_id") == inspected.get("source_strategy_id")
                and save_confirmation.get("name") == custom_optimized_name
            )
            if saved_this_exact_name:
                action_success(f'Saved as “{custom_optimized_name}”')
            elif st.button(
                f"Save optimized {optimized_symbol} strategy",
                key=f"save_optimized_{optimized_symbol}_{inspected.get('source_strategy_id', 'unknown')}",
                use_container_width=True,
                disabled=save_blocked_for_sample or not bool(custom_optimized_name),
            ):
                try:
'''
    if save_warning_anchor not in app:
        raise SystemExit("Could not find optimized save button block")
    app = app.replace(save_warning_anchor, save_replacement, 1)

call_old = '''                    store.save_optimized_strategy(
                        str(inspected.get("source_strategy_id") or ""),
                        optimized_symbol,
                        inspected.get("optimized_rules") or {},
                        summary,
                    )
'''
call_new = '''                    store.save_optimized_strategy(
                        str(inspected.get("source_strategy_id") or ""),
                        optimized_symbol,
                        inspected.get("optimized_rules") or {},
                        summary,
                        custom_name=custom_optimized_name,
                    )
'''
if call_new not in app:
    if call_old not in app:
        raise SystemExit("Could not find save_optimized_strategy call")
    app = app.replace(call_old, call_new, 1)

notice_old = '''                    st.session_state["optimizer_saved_notice"] = (
                        f"Saved the {optimized_symbol}-specific strategy and its recommended risk, position size, "
                        "stop, reward target, trading costs, and candle interval. Select it in Backtesting "
                        "to load those settings automatically."
                    )
                    st.rerun()
'''
notice_new = '''                    st.session_state["optimizer_saved_notice"] = (
                        f'Saved “{custom_optimized_name}” with the {optimized_symbol}-specific risk, position size, '
                        "stop, reward target, trading costs, and candle interval. Select it in Backtesting "
                        "to load those settings automatically."
                    )
                    st.session_state["optimized_save_confirmation"] = {
                        "symbol": optimized_symbol,
                        "source_strategy_id": inspected.get("source_strategy_id"),
                        "name": custom_optimized_name,
                    }
                    st.rerun()
'''
if notice_new not in app:
    if notice_old not in app:
        raise SystemExit("Could not find optimizer saved notice block")
    app = app.replace(notice_old, notice_new, 1)

engine_path.write_text(engine, encoding="utf-8")
app_path.write_text(app, encoding="utf-8")
