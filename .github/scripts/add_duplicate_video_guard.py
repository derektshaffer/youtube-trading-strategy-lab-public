from pathlib import Path

path = Path("youtube_strategy_app.py")
text = path.read_text(encoding="utf-8")

helper_anchor = '''def selected_strategy_options(strategies: list[dict[str, Any]], approved_only: bool = False) -> dict[str, dict[str, Any]]:
    filtered = [item for item in strategies if not approved_only or item.get("approved")]
    result: dict[str, dict[str, Any]] = {}
    for item in filtered:
        creator = str(item.get("creator") or "Unknown creator")
        label = f'{item.get("name", "Unnamed strategy")} — {creator}'
        if label in result:
            label = f'{label} [{str(item.get("id", ""))[:6]}]'
        result[label] = item
    return result


'''
helper_replacement = helper_anchor + '''def analyzed_youtube_video_index(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return one canonical record per YouTube video already represented in the library."""
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
            "source": "Video analysis",
        }

    # Legacy/restored libraries may contain source strategies even if the video record
    # itself is missing. Include those URLs too so duplicate protection still works.
    for strategy in video_source_strategies(data.get("strategies") or []):
        raw_url = str(strategy.get("source_url") or "").strip()
        if not raw_url:
            continue
        try:
            normalized_url = normalize_youtube_url(raw_url)
        except AppError:
            continue
        if normalized_url in result:
            continue
        result[normalized_url] = {
            "url": normalized_url,
            "video_title": strategy.get("video_title") or strategy.get("source_video_title") or "Previously analyzed video",
            "creator": strategy.get("creator") or "Unknown creator",
            "analyzed_at": strategy.get("created_at") or strategy.get("analyzed_at"),
            "source": "Saved strategy",
        }

    return result


'''
if helper_replacement not in text:
    if helper_anchor not in text:
        raise SystemExit("Could not find selected_strategy_options helper anchor")
    text = text.replace(helper_anchor, helper_replacement, 1)

videos_start = '''with videos_tab:
    section("Give the AI trading videos to inspect", "Paste one public YouTube video link per line. The app reads both the visible charts and the audio.")
    with st.form("analyze_video_form"):
'''
videos_replacement = '''with videos_tab:
    analyzed_video_lookup = analyzed_youtube_video_index(library)
    section(
        "Previously analyzed YouTube videos",
        "Duplicate protection is on. These videos will be skipped unless you explicitly allow re-analysis below.",
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
        analyzed_video_rows.sort(key=lambda item: str(item.get("Analyzed") or ""), reverse=True)
        st.dataframe(
            pd.DataFrame(analyzed_video_rows),
            hide_index=True,
            use_container_width=True,
        )
        st.caption(f"{len(analyzed_video_lookup)} unique YouTube video(s) are already in your library.")
    else:
        st.info("No previously analyzed YouTube videos are currently saved.")

    section("Give the AI trading videos to inspect", "Paste one public YouTube video link per line. The app reads both the visible charts and the audio.")
    with st.form("analyze_video_form"):
'''
if videos_replacement not in text:
    if videos_start not in text:
        raise SystemExit("Could not find Analyze Videos tab start")
    text = text.replace(videos_start, videos_replacement, 1)

submit_anchor = '''        duration_override_text = st.text_input(
            "Long video length (optional)",
            value="",
            placeholder="Example: 3:06:12",
            help=(
                "Long videos are split into 40-minute sections automatically when YouTube provides the runtime. "
                "If a long video fails, enter its total length here and analyze that video by itself."
            ),
        )
        submitted = st.form_submit_button("Analyze YouTube videos", use_container_width=True)
'''
submit_replacement = '''        duration_override_text = st.text_input(
            "Long video length (optional)",
            value="",
            placeholder="Example: 3:06:12",
            help=(
                "Long videos are split into 40-minute sections automatically when YouTube provides the runtime. "
                "If a long video fails, enter its total length here and analyze that video by itself."
            ),
        )
        allow_duplicate_reanalysis = st.checkbox(
            "Allow re-analysis of videos already in my library",
            value=False,
            help=(
                "Leave this off to protect against accidental duplicate uploads and unnecessary Gemini usage. "
                "Turn it on only when you intentionally want to analyze the same YouTube video again."
            ),
        )
        submitted = st.form_submit_button("Analyze YouTube videos", use_container_width=True)
'''
if submit_replacement not in text:
    if submit_anchor not in text:
        raise SystemExit("Could not find Analyze Videos form submit anchor")
    text = text.replace(submit_anchor, submit_replacement, 1)

submitted_anchor = '''    if submitted:
        urls, invalid = parse_youtube_urls(raw_urls)
        duration_override = None
'''
submitted_replacement = '''    if submitted:
        urls, invalid = parse_youtube_urls(raw_urls)
        duplicate_urls = [url for url in urls if url in analyzed_video_lookup]
        if duplicate_urls:
            duplicate_lines = []
            for url in duplicate_urls[:12]:
                record = analyzed_video_lookup[url]
                duplicate_lines.append(
                    f'- **{record.get("video_title") or "Previously analyzed video"}** '
                    f'— {record.get("creator") or "Unknown creator"}  \\n  {url}'
                )
            if len(duplicate_urls) > 12:
                duplicate_lines.append(f'- …and {len(duplicate_urls) - 12} more duplicate video(s)')
            st.warning(
                "Already analyzed — duplicate protection caught the following YouTube video(s):\n\n"
                + "\n".join(duplicate_lines)
            )
            if not allow_duplicate_reanalysis:
                duplicate_set = set(duplicate_urls)
                urls = [url for url in urls if url not in duplicate_set]
                st.info(
                    f"Skipped {len(duplicate_urls)} already-analyzed video(s). "
                    "They will not be sent to Gemini again."
                )

        duration_override = None
'''
if submitted_replacement not in text:
    if submitted_anchor not in text:
        raise SystemExit("Could not find submitted Analyze Videos block")
    text = text.replace(submitted_anchor, submitted_replacement, 1)

no_urls_anchor = '''        if not urls:
            st.error("Add at least one valid public YouTube video link.")
        elif duration_error:
'''
no_urls_replacement = '''        if not urls:
            if duplicate_urls and not allow_duplicate_reanalysis:
                st.warning(
                    "Nothing new to analyze. Every valid YouTube link you entered is already in your library. "
                    "If you intentionally want a fresh analysis, enable the re-analysis checkbox and submit again."
                )
            else:
                st.error("Add at least one valid public YouTube video link.")
        elif duration_error:
'''
if no_urls_replacement not in text:
    if no_urls_anchor not in text:
        raise SystemExit("Could not find empty URL validation block")
    text = text.replace(no_urls_anchor, no_urls_replacement, 1)

path.write_text(text, encoding="utf-8")
