"""Trading Intelligence Lab — unified trading research platform."""

from __future__ import annotations

import hashlib
import os
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from live_strategy_runner_page import market_client, setting
from trading_catalyst_core import (
    classify_catalyst,
    enrich_bars_with_point_in_time_catalysts,
    historical_news,
)
from trading_market_discovery import analyze_stock_strategies, scan_strategy_universe
from trading_progress_ui import (
    AutonomousResearchEtaEstimator,
    AutonomousResearchProgressEstimator,
    AutonomousResearchTimingRecorder,
    LongTaskMonitor,
    format_eta_range,
    session_task_profiles,
)
from trading_auto_research import (
    merge_autonomous_research_into_library,
    run_autonomous_research,
)
from trading_strategy_dna import (
    DNA_DIMENSIONS,
    DNA_LABELS,
    build_candidate_blueprints,
    build_concept_graph,
    build_strategy_families,
    compile_candidate_blueprint,
    infer_strategy_dna,
    is_synthetic_strategy,
    source_identity,
)
from trading_intelligence_core import (
    DEFAULT_GEMINI_BOOK_MODEL,
    DEFAULT_GEMINI_BOOK_SPECIALIST_MODEL,
    GeminiBookAnalyzer,
    GeminiRuleCompiler,
    canonicalize_existing_strategy,
    effective_strategy_for_live,
    effective_strategy_for_research,
    extract_source_text,
    merge_ingestion_checkpoint_strategies,
    merge_strategies,
    prepare_strategies_with_ai,
    reconcile_knowledge_sources,
    research_readiness,
    upgrade_native_strategy_rules,
)
from trading_universe_research import cross_stock_generalization
from trading_validation_core import validation_strength, walk_forward_validate
from youtube_strategy_engine import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GITHUB_BACKUP_PATH,
    AppError,
    BacktestSettings,
    GitHubCloudBackup,
    OptimizationSettings,
    StrategyStore,
    normalize_machine_rules,
    optimize_stock_strategies,
    safe_float,
    utc_now,
)

st.set_page_config(
    page_title="Trading Intelligence Lab",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .til-hero {
        padding: 24px 27px;
        border: 1px solid #31445f;
        border-radius: 18px;
        background: linear-gradient(125deg,#17263c,#0d1625 65%,#1d2940);
        margin-bottom: 18px;
    }
    .til-title {font-size:34px;font-weight:900;letter-spacing:-.035em;}
    .til-sub {color:#b9c7d9;line-height:1.58;margin-top:8px;max-width:1050px;}
    .til-card {
        border:1px solid #293a53;border-radius:14px;padding:16px 17px;
        min-height:132px;background:#111b2b;margin-bottom:10px;
    }
    .til-card strong {font-size:1.03rem;}
    .muted {color:#91a2b7;}
    </style>
    <div class="til-hero">
      <div class="til-title">🧠 Trading Intelligence Lab</div>
      <div class="til-sub">
        Learn trading ideas from books, videos, and existing research; convert them into explicit
        rules; test them against historical markets; validate them on unseen data; and match
        validated strategies to current market conditions.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)


DEFAULT_PRIVATE_BACKUP_REPOSITORY = "derektshaffer/derektshaffer-youtube-trading-strategy-lab"
OBSOLETE_PRIVATE_BACKUP_REPOSITORIES = {
    "derektshaffer/youtube-trading-strategy-backups",
}


def resolved_backup_repository() -> str:
    repository = setting(
        "GITHUB_BACKUP_REPOSITORY",
        DEFAULT_PRIVATE_BACKUP_REPOSITORY,
    )
    if repository in OBSOLETE_PRIVATE_BACKUP_REPOSITORIES:
        return DEFAULT_PRIVATE_BACKUP_REPOSITORY
    return repository


def backup_token() -> str:
    return (
        setting("GITHUB_BACKUP_TOKEN")
        or setting("GITHUB_TOKEN")
        or setting("GH_TOKEN")
    )


def build_intelligence_store() -> StrategyStore:
    repository = resolved_backup_repository()
    token = backup_token()
    cloud = None
    if repository and token:
        cloud = GitHubCloudBackup(
            repository,
            token,
            branch=setting("GITHUB_BACKUP_BRANCH"),
            path=setting(
                "TRADING_INTELLIGENCE_BACKUP_PATH",
                "trading-intelligence-lab/intelligence_library.json",
            ),
        )
    directory = Path(os.environ.get("TRADING_INTELLIGENCE_DATA_DIR") or ".trading_intelligence_data")
    return StrategyStore(directory=directory, cloud_backup=cloud)


def build_legacy_store() -> StrategyStore:
    repository = resolved_backup_repository()
    token = backup_token()
    cloud = None
    if repository and token:
        cloud = GitHubCloudBackup(
            repository,
            token,
            branch=setting("GITHUB_BACKUP_BRANCH"),
            path=setting("GITHUB_BACKUP_PATH", DEFAULT_GITHUB_BACKUP_PATH),
        )
    return StrategyStore(cloud_backup=cloud)


def intelligence_store() -> StrategyStore:
    # Lightweight by design. Do not cache this object: Streamlit hot deploys can otherwise
    # retain a StrategyStore created from an older class/configuration and keep using stale
    # backup repository settings after the code has been fixed.
    return build_intelligence_store()


def load_library() -> dict[str, Any]:
    store = intelligence_store()
    data = store.load_latest()
    data.setdefault("knowledge_sources", [])
    data.setdefault("strategies", [])
    data.setdefault("research_runs", [])
    data.setdefault("validation_runs", [])

    upgraded_strategies: list[dict[str, Any]] = []
    for raw in data.get("strategies") or []:
        if not isinstance(raw, dict):
            continue
        upgraded = upgrade_native_strategy_rules(raw)
        upgraded["research_readiness"] = research_readiness(upgraded)
        upgraded_strategies.append(upgraded)
    data["strategies"] = upgraded_strategies

    # Older Trading Lab versions saved source provenance only on strategy records.
    # Rebuild the Saved Sources catalog automatically so existing books/videos are
    # visible without asking the user to upload or analyze them again.
    data, sources_changed = reconcile_knowledge_sources(data)
    if sources_changed:
        try:
            store.save(data)
        except AppError:
            # Keep the repaired catalog visible in this session. The storage-health
            # panel will surface persistence problems and the migration retries next load.
            pass
    return data


def save_ingestion_checkpoint(
    analysis: dict[str, Any],
    *,
    filename: str,
    extraction_metadata: dict[str, Any],
    ingest_id: str,
    stage: str,
) -> dict[str, Any]:
    """Persist book progress immediately so Streamlit disconnects cannot erase completed work."""
    store = intelligence_store()
    data = store.load_latest()
    data.setdefault("knowledge_sources", [])
    data.setdefault("strategies", [])
    data.setdefault("research_runs", [])
    data.setdefault("validation_runs", [])

    strategies = list(analysis.get("strategies") or [])
    existing_source = next(
        (
            item
            for item in data.get("knowledge_sources") or []
            if str(item.get("ingest_id") or "") == ingest_id
        ),
        None,
    )
    replace_progressive_source = not (
        isinstance(existing_source, dict)
        and str(existing_source.get("analysis_stage") or "") == "complete"
    )
    if strategies:
        data["strategies"] = merge_ingestion_checkpoint_strategies(
            list(data.get("strategies") or []),
            strategies,
            source_id=str(analysis.get("id") or ""),
            replace_source=replace_progressive_source,
        )

    source_record = {key: value for key, value in analysis.items() if key != "strategies"}
    source_record["filename"] = filename
    source_record["extraction_metadata"] = extraction_metadata
    source_record["ingest_id"] = ingest_id
    source_record["analysis_stage"] = stage
    source_record["analysis_in_progress"] = stage != "complete"
    source_record["checkpointed_at"] = utc_now().isoformat()

    data["knowledge_sources"] = [
        item
        for item in data.get("knowledge_sources") or []
        if str(item.get("ingest_id") or "") != ingest_id
        and str(item.get("id") or "") != str(source_record.get("id") or "")
    ]
    data["knowledge_sources"].insert(0, source_record)
    return store.save(data)


def persistence_summary() -> dict[str, Any]:
    store = intelligence_store()
    status = store.persistence_status(verify=True)
    if (
        status.get("configured")
        and status.get("verified")
        and (status.get("last_error") or not status.get("write_verified"))
    ):
        # Prove that THIS deployment can write, not merely read. This runs automatically
        # after a fresh Streamlit filesystem/process or after a previous cloud-save error.
        try:
            store.verify_cloud_write_access()
        except AppError:
            pass
        status = store.persistence_status(verify=True)
    return status


def long_task_monitor(task_key: str) -> LongTaskMonitor:
    return LongTaskMonitor(
        task_key=task_key,
        profiles=session_task_profiles(st.session_state, task_key),
    )


def update_task_bar(
    bar,
    monitor: LongTaskMonitor,
    fraction: float,
    message: str,
) -> None:
    bar.progress(
        max(0.01, min(0.999, float(fraction))),
        text=monitor.text(fraction, message),
    )


def complete_task_bar(
    bar,
    monitor: LongTaskMonitor,
    message: str,
) -> None:
    monitor.finish(st.session_state)
    bar.progress(1.0, text=f"{message} · 100%")


def source_label(strategy: dict[str, Any]) -> str:
    kind = str(strategy.get("source_type") or "legacy").replace("_", " ").title()
    return f"{kind} · {strategy.get('source_title') or 'Unknown source'}"


def upsert_strategy_record(
    data: dict[str, Any],
    strategy: dict[str, Any],
) -> dict[str, Any]:
    """Insert or replace one strategy record by stable ID."""
    result = dict(data or {})
    strategy_id = str(strategy.get("id") or "")
    existing = [
        dict(item)
        for item in result.get("strategies") or []
        if isinstance(item, dict)
        and str(item.get("id") or "") != strategy_id
    ]
    result["strategies"] = [dict(strategy), *existing]
    return result


with st.sidebar:
    st.markdown("### Research workspace")
    module = st.radio(
        "Section",
        [
            "Overview",
            "Knowledge Sources",
            "Strategy Library",
            "Strategy DNA",
            "Rule Compiler",
            "AI Research Autopilot",
            "Strategy Lab",
            "Universe Research",
            "Validation",
            "Catalyst Intelligence",
            "Market Discovery",
            "Stock Analyzer",
            "Live / Paper",
        ],
        label_visibility="collapsed",
    )
    st.divider()
    st.caption(
        "This is a new app entrypoint. The existing YouTube Trading Lab home screen is unchanged."
    )

try:
    library = load_library()
except AppError as exc:
    st.error(str(exc))
    st.stop()

strategies = list(library.get("strategies") or [])
sources = list(library.get("knowledge_sources") or [])


if module == "Overview":
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Knowledge sources", len(sources))
    c2.metric("Strategies", len(strategies))
    c3.metric(
        "Validated",
        sum(1 for s in strategies if str(s.get("validation_status")) == "validated"),
    )
    c4.metric(
        "Approved for live/paper",
        sum(1 for s in strategies if bool(s.get("approved"))),
    )

    st.markdown("### Platform pipeline")
    cols = st.columns(4)
    cards = [
        ("1 · Learn", "Books + PDFs + YouTube + human ideas", "Extract evidence-grounded strategy hypotheses."),
        ("2 · Test", "Historical Strategy Lab", "Run deterministic backtests and systematic parameter searches."),
        ("3 · Validate", "Out-of-sample + walk-forward", "Reject unstable strategies and likely overfitting."),
        ("4 · Apply", "Scanner + analyzer + live runner", "Match validated setups to current stocks and regimes."),
    ]
    for col, (title, subtitle, body) in zip(cols, cards):
        col.markdown(
            f'<div class="til-card"><strong>{title}</strong><br><span class="muted">{subtitle}</span>'
            f'<p>{body}</p></div>',
            unsafe_allow_html=True,
        )

    st.markdown("### What is usable now")
    st.success(
        "Book/PDF ingestion, AI strategy extraction, Strategy DNA + cross-book synthesis, automatic "
        "AI rule preparation, the historical Strategy Lab, validation, catalyst intelligence, universe "
        "research, market discovery, and the unified strategy library are all connected."
    )
    st.info(
        "The normal workflow is now AI-first: upload a source once, let the AI extract and prepare "
        "its strategies, then use deterministic market data and validation to decide what survives. "
        "Manual review tools remain available when you want them, but they are no longer required for preparation."
    )


elif module == "Knowledge Sources":
    st.markdown("## Knowledge Sources")
    st.caption(
        "Upload a source you have lawful access to. The AI extracts trading hypotheses and short "
        "evidence references; it does not reproduce the book or treat the author's claims as validated."
    )

    storage = persistence_summary()
    if storage.get("healthy"):
        st.success(
            "Permanent library storage verified: "
            f"{storage.get('repository')} · {storage.get('path')}. "
            "Book progress is checkpointed to GitHub as the AI works."
        )
    elif storage.get("configured"):
        st.error(
            "Permanent GitHub storage is not healthy yet. "
            + str(
                storage.get("verification_error")
                or storage.get("last_error")
                or "The backup repository/path could not be verified."
            )
        )
    else:
        st.error(
            "Permanent library storage is NOT configured in this Streamlit deployment. "
            "Local Streamlit files can disappear when the app sleeps or restarts."
        )

    uploaded = st.file_uploader(
        "Book or research document",
        type=["pdf", "txt", "md", "markdown"],
        help="PDF, TXT, and Markdown are supported in the first version.",
    )
    a, b = st.columns(2)
    title = a.text_input(
        "Title (optional)",
        placeholder="AI will detect it when possible",
        help="You can leave this blank. The AI will use the source itself to identify the title when it can.",
    )
    author = b.text_input(
        "Author / creator (optional)",
        placeholder="AI will detect it when possible",
        help="You can leave this blank. The AI will identify the author/creator when the source clearly supports it.",
    )
    focus = st.text_area(
        "Optional research focus",
        placeholder="Leave blank to extract every strategy and trading principle the AI can find.",
        height=90,
    )
    autopilot_prepare = st.checkbox(
        "AI Autopilot — automatically prepare every extracted strategy for backtesting",
        value=True,
        help=(
            "After extraction, AI translates defensible qualitative ideas into machine-testable research "
            "assumptions. Those assumptions stay clearly separate from rules explicitly stated by the author."
        ),
    )
    if autopilot_prepare:
        st.caption(
            "Autopilot only prepares research hypotheses. It does not label anything profitable or validated; "
            "historical testing and unseen-data validation still make that determination."
        )
    autopilot_research = st.checkbox(
        "Continue automatically into historical opportunity discovery + validation",
        value=True,
        disabled=not autopilot_prepare,
        help=(
            "After extraction, the Lab builds its own stock universe, finds historical opportunities, "
            "selects research finalists, optimizes them, runs holdout/walk-forward checks, tests frozen "
            "rules across multiple stocks, and saves the results automatically."
        ),
    )
    if autopilot_research and autopilot_prepare:
        st.caption(
            "No ticker or optimizer setup is required. The broad scan is cheap; deeper intraday testing "
            "is automatically limited to the strongest research finalists."
        )

    can_analyze = uploaded is not None and bool(storage.get("healthy"))
    if uploaded is not None and not storage.get("healthy"):
        st.warning(
            "Analysis is temporarily disabled until permanent GitHub storage is healthy. "
            "This prevents a long book run from being lost if Streamlit restarts."
        )
    analyze = st.button(
        "🧠 Analyze source and extract strategies",
        type="primary",
        use_container_width=True,
        disabled=not can_analyze,
    )

    if analyze and uploaded is not None:
        try:
            payload = uploaded.getvalue()
            text, metadata = extract_source_text(uploaded.name, payload)
            ingest_id = hashlib.sha256(payload).hexdigest()[:24]

            current_library = intelligence_store().load_latest()
            existing_source = next(
                (
                    item
                    for item in current_library.get("knowledge_sources") or []
                    if str(item.get("ingest_id") or "") == ingest_id
                ),
                None,
            )
            resume_state = None
            if existing_source:
                resume_state = dict(existing_source)
                source_id = str(existing_source.get("id") or "")
                resume_state["strategies"] = [
                    item
                    for item in current_library.get("strategies") or []
                    if str(item.get("source_id") or "") == source_id
                ]
            else:
                pending_analysis = {
                    "id": f"pending-{ingest_id}",
                    "source_type": "book_or_document",
                    "title": title.strip() or uploaded.name,
                    "author": author.strip(),
                    "summary": "Analysis started. Completed sections will be saved automatically.",
                    "analyzed_at": utc_now().isoformat(),
                    "chunk_count": 0,
                    "checkpoint_version": 0,
                    "completed_section_indices": [],
                    "completed_sections": 0,
                    "analysis_incomplete": True,
                    "failed_sections": [],
                    "strategies": [],
                }
                save_ingestion_checkpoint(
                    pending_analysis,
                    filename=uploaded.name,
                    extraction_metadata=metadata,
                    ingest_id=ingest_id,
                    stage="reading",
                )

            analyzer = GeminiBookAnalyzer(
                setting("GEMINI_API_KEY"),
                setting("GEMINI_BOOK_MODEL", DEFAULT_GEMINI_BOOK_MODEL),
                fallback_api_key=setting("GEMINI_PAID_API_KEY", ""),
                fallback_model=setting("GEMINI_BOOK_FALLBACK_MODEL", "gemini-3.5-flash"),
                specialist_model=setting(
                    "GEMINI_BOOK_SPECIALIST_MODEL",
                    DEFAULT_GEMINI_BOOK_SPECIALIST_MODEL,
                ),
            )
            page_note = ""
            if metadata.get("pages"):
                page_note = f" · {int(metadata.get('pages') or 0)} pages"
            progress = st.progress(
                0.0,
                text=(
                    "File upload succeeded and readable text was extracted"
                    + page_note
                    + ". Preparing the AI reading batches…"
                ),
            )
            def on_progress(index: int, total: int, message: str | None = None) -> None:
                progress.progress(
                    min(0.98, max(0.0, (index - 1) / max(1, total))),
                    text=message or f"Analyzing source section {index} of {total}…",
                )

            def on_checkpoint(partial_analysis: dict[str, Any]) -> None:
                save_ingestion_checkpoint(
                    partial_analysis,
                    filename=uploaded.name,
                    extraction_metadata=metadata,
                    ingest_id=ingest_id,
                    stage="reading",
                )

            analysis = analyzer.analyze(
                text,
                title=title.strip(),
                author=author.strip(),
                focus=focus,
                progress_callback=on_progress,
                checkpoint_callback=on_checkpoint,
                resume_state=resume_state,
            )

            # Extraction itself is valuable work. Save it before the Rule Compiler or market research starts.
            save_ingestion_checkpoint(
                analysis,
                filename=uploaded.name,
                extraction_metadata=metadata,
                ingest_id=ingest_id,
                stage="extracted",
            )
            completion_text = "Strategy extraction complete"
            if analysis.get("analysis_incomplete"):
                completion_text = (
                    f"Partial extraction saved · {int(analysis.get('completed_sections') or 0)} of "
                    f"{int(analysis.get('chunk_count') or 0)} sections completed"
                )
            if analysis.get("specialist_used"):
                specialist_sections = ", ".join(
                    str(value) for value in analysis.get("specialist_sections") or []
                )
                completion_text += (
                    f" · {analysis.get('specialist_model')} specialist review"
                    + (f" on section(s) {specialist_sections}" if specialist_sections else "")
                )
            if analysis.get("model_fallback_used"):
                completion_text += f" · reliability fallback used: {analysis.get('model')}"
            if analysis.get("paid_fallback_used"):
                completion_text += " · backup API key used"
            progress.progress(1.0, text=completion_text)
            if analysis.get("analysis_incomplete"):
                failed_numbers = [
                    str(item.get("section"))
                    for item in analysis.get("failed_sections") or []
                    if isinstance(item, dict) and item.get("section") is not None
                ]
                st.warning(
                    "Gemini remained unavailable for "
                    + (", ".join(f"section {value}" for value in failed_numbers) if failed_numbers else "part of the source")
                    + ". Everything else was saved. Historical validation is paused so an incomplete "
                    "book cannot be mistaken for a fully extracted strategy source. Re-running the same "
                    "source resumes only the missing work."
                )

            if autopilot_prepare and analysis.get("strategies"):
                prep_status = st.status(
                    "AI Autopilot is translating strategies into testable research rules…",
                    expanded=True,
                )
                compiler = GeminiRuleCompiler(
                    setting("GEMINI_API_KEY"),
                    setting("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
                    fallback_api_key=setting("GEMINI_PAID_API_KEY", ""),
                )

                def on_prepare(index: int, total: int, strategy_name: str) -> None:
                    prep_status.write(
                        f"Preparing {index} of {total}: {strategy_name}"
                    )

                analysis["strategies"] = prepare_strategies_with_ai(
                    list(analysis.get("strategies") or []),
                    compiler,
                    minimum_confidence=65.0,
                    progress_callback=on_prepare,
                )
                prepared = list(analysis.get("strategies") or [])
                applied = sum(
                    int((item.get("autopilot_preparation") or {}).get("suggestions_auto_applied") or 0)
                    for item in prepared
                )
                ready = sum(
                    1
                    for item in prepared
                    if (item.get("research_readiness") or {}).get("label") == "ready_for_backtest"
                )
                prep_status.update(
                    label=(
                        f"AI Autopilot prepared {len(prepared)} strategies · "
                        f"{applied} research assumptions added · {ready} ready for backtesting"
                    ),
                    state="complete",
                    expanded=False,
                )
                analysis["autopilot_summary"] = {
                    "enabled": True,
                    "strategies_prepared": len(prepared),
                    "research_assumptions_added": applied,
                    "ready_for_backtest": ready,
                }
                save_ingestion_checkpoint(
                    analysis,
                    filename=uploaded.name,
                    extraction_metadata=metadata,
                    ingest_id=ingest_id,
                    stage="prepared",
                )
            else:
                for item in analysis.get("strategies") or []:
                    item["research_readiness"] = research_readiness(item)
                analysis["autopilot_summary"] = {
                    "enabled": False,
                    "strategies_prepared": 0,
                    "research_assumptions_added": 0,
                    "ready_for_backtest": sum(
                        1
                        for item in analysis.get("strategies") or []
                        if (item.get("research_readiness") or {}).get("label") == "ready_for_backtest"
                    ),
                }
                save_ingestion_checkpoint(
                    analysis,
                    filename=uploaded.name,
                    extraction_metadata=metadata,
                    ingest_id=ingest_id,
                    stage="extracted",
                )

            analysis["autonomous_research_summary"] = {
                "completed": False,
                "status": "pending" if autopilot_research and autopilot_prepare else "not_requested",
            }
            save_ingestion_checkpoint(
                analysis,
                filename=uploaded.name,
                extraction_metadata=metadata,
                ingest_id=ingest_id,
                stage="prepared" if autopilot_prepare else "extracted",
            )
            data = intelligence_store().load()

            autonomous_report = None
            autonomous_error = ""
            if (
                autopilot_research
                and autopilot_prepare
                and analysis.get("strategies")
                and not analysis.get("analysis_incomplete")
            ):
                ready_for_deep = [
                    item
                    for item in analysis.get("strategies") or []
                    if (item.get("research_readiness") or {}).get("label") == "ready_for_backtest"
                ]
                if ready_for_deep:
                    auto_status = st.status(
                        "Historical Research Autopilot is building its own stock universe…",
                        expanded=True,
                    )
                    try:
                        autonomous_report = run_autonomous_research(
                            market_client(),
                            ready_for_deep,
                            progress=lambda message: auto_status.write(message),
                        )
                        data = merge_autonomous_research_into_library(data, autonomous_report)
                        intelligence_store().save(data)
                        validated_count = sum(
                            1
                            for item in autonomous_report.get("results") or []
                            if item.get("validation_status") == "validated"
                        )
                        analysis["autonomous_research_summary"] = {
                            "completed": True,
                            "generated_at": autonomous_report.get("generated_at"),
                            "deep_strategies_tested": autonomous_report.get("deep_strategies_tested"),
                            "validated": validated_count,
                            "universe_source": (autonomous_report.get("universe") or {}).get("source"),
                        }
                        auto_status.update(
                            label=(
                                f"Historical Research Autopilot complete · "
                                f"{int(autonomous_report.get('deep_strategies_tested') or 0)} finalists tested · "
                                f"{validated_count} passed the full gate"
                            ),
                            state="complete",
                            expanded=False,
                        )
                        st.session_state["til_auto_research_result"] = autonomous_report
                    except AppError as exc:
                        autonomous_error = str(exc)
                        analysis["autonomous_research_summary"] = {
                            "completed": False,
                            "error": autonomous_error,
                        }
                        auto_status.update(
                            label="Strategy extraction saved; historical Autopilot could not complete",
                            state="error",
                            expanded=False,
                        )
                else:
                    autonomous_error = (
                        "No extracted strategy had enough machine-testable entry/filter rules for deep historical research."
                    )
                    analysis["autonomous_research_summary"] = {
                        "completed": False,
                        "error": autonomous_error,
                    }

            save_ingestion_checkpoint(
                analysis,
                filename=uploaded.name,
                extraction_metadata=metadata,
                ingest_id=ingest_id,
                stage="partial" if analysis.get("analysis_incomplete") else "complete",
            )
            st.session_state["til_last_analysis"] = analysis
            source_name = analysis.get("title") or title.strip() or uploaded.name
            autopilot_summary = analysis.get("autopilot_summary") or {}
            message = (
                f"Extracted {len(analysis.get('strategies') or [])} strategy hypotheses from "
                f"{source_name}."
            )
            if autopilot_summary.get("enabled"):
                message += (
                    f" AI Autopilot added {int(autopilot_summary.get('research_assumptions_added') or 0)} "
                    f"clearly labeled research assumptions and marked "
                    f"{int(autopilot_summary.get('ready_for_backtest') or 0)} strategies ready for backtesting."
                )
            if analysis.get("analysis_incomplete"):
                message += (
                    f" {int(analysis.get('completed_sections') or 0)} of "
                    f"{int(analysis.get('chunk_count') or 0)} source sections were saved; "
                    "historical validation is intentionally paused until extraction is complete."
                )
            elif autonomous_report:
                validated_count = sum(
                    1
                    for item in autonomous_report.get("results") or []
                    if item.get("validation_status") == "validated"
                )
                message += (
                    f" Historical Autopilot deep-tested "
                    f"{int(autonomous_report.get('deep_strategies_tested') or 0)} finalists and "
                    f"{validated_count} passed the full autonomous validation gate."
                )
            st.success(message)
            if autonomous_error:
                st.warning("Historical Autopilot note: " + autonomous_error)
            st.rerun()
        except AppError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Source analysis failed: {exc}")

    if sources:
        st.markdown("### Saved sources")
        st.caption(
            "This is the master research-source catalog. Books, PDFs, YouTube videos, and future "
            "research sources stay here even when they were originally analyzed by an older version of the Lab."
        )
        for src in sources:
            source_type = str(src.get("source_type") or "document").strip().casefold()
            source_icon = {
                "youtube": "▶️",
                "book_or_document": "📘",
                "book": "📘",
                "document": "📄",
            }.get(source_type, "🧠")
            with st.expander(
                f"{source_icon} {src.get('title') or 'Untitled'}"
                + (f" — {src.get('author')}" if src.get("author") else "")
            ):
                st.write(src.get("summary") or "No source-level summary saved.")
                stage = str(src.get("analysis_stage") or "complete").replace("_", " ").title()
                completed = int(src.get("completed_sections") or 0)
                total = int(src.get("chunk_count") or 0)
                strategy_count = int(src.get("strategy_count") or 0)
                if src.get("recovered_from_strategies"):
                    progress_label = (
                        f"recovered from saved library · {strategy_count} "
                        + ("strategy" if strategy_count == 1 else "strategies")
                    )
                elif total:
                    progress_label = f"{completed}/{total} sections"
                elif stage.casefold() == "complete":
                    progress_label = "complete"
                else:
                    progress_label = "waiting for first section"
                st.caption(
                    f"Type: {source_type.replace('_', ' ')} · "
                    f"Status: {stage} · "
                    f"Progress: {progress_label} · "
                    f"Analyzed: {src.get('analyzed_at', '—')}"
                )
                source_url = str(src.get("source_url") or "").strip()
                if source_url:
                    st.caption(f"Source link: {source_url}")
                if src.get("recovered_from_strategies"):
                    st.info(
                        "This source was recovered automatically from strategy records saved by an older "
                        "Trading Lab version. You do not need to upload or analyze it again."
                    )
                if src.get("analysis_in_progress"):
                    st.info(
                        "This source has a saved checkpoint. Re-uploading the same file and pressing Analyze "
                        "will resume from the durable completed sections instead of starting over."
                    )
                auto = src.get("autopilot_summary") or {}
                if auto.get("enabled"):
                    st.caption(
                        "AI Autopilot: "
                        f"{int(auto.get('strategies_prepared') or 0)} strategies prepared · "
                        f"{int(auto.get('research_assumptions_added') or 0)} assumptions added · "
                        f"{int(auto.get('ready_for_backtest') or 0)} ready for backtesting"
                    )
    else:
        st.info("No research sources are saved yet.")


elif module == "Strategy Library":
    st.markdown("## Unified Strategy Library")
    st.caption(
        "This library uses one strategy representation regardless of whether the idea came from "
        "a book, YouTube video, or a future AI/human research source."
    )

    try:
        legacy = build_legacy_store().load()
        legacy_strategies = list(legacy.get("strategies") or [])
    except AppError:
        legacy_strategies = []

    if legacy_strategies:
        already = {str(item.get("id")) for item in strategies}
        importable = [item for item in legacy_strategies if str(item.get("id")) not in already]
        c1, c2 = st.columns([1.2, 3])
        c1.metric("Existing YouTube-lab strategies available", len(legacy_strategies))
        if c2.button(
            f"Import {len(importable)} new strategy record(s)",
            disabled=not importable,
            use_container_width=True,
        ):
            data = load_library()
            additions = [canonicalize_existing_strategy(item) for item in importable]
            data["strategies"] = merge_strategies(list(data.get("strategies") or []), additions)
            intelligence_store().save(data)
            st.success(f"Imported {len(additions)} strategies into Trading Intelligence Lab.")
            st.rerun()

    if not strategies:
        st.info("The unified library is empty. Analyze a source or import the existing Trading Lab library.")
    else:
        search = st.text_input("Filter strategies", placeholder="pullback, VWAP, breakout, catalyst…").strip().casefold()
        filtered = [
            s for s in strategies
            if not search
            or search in str(s.get("name") or "").casefold()
            or search in str(s.get("category") or "").casefold()
            or search in str(s.get("summary") or "").casefold()
            or search in source_label(s).casefold()
        ]
        rows = []
        for s in filtered:
            rules = normalize_machine_rules(s.get("machine_rules"))
            readiness = s.get("research_readiness") or research_readiness(s)
            rows.append(
                {
                    "Strategy": s.get("name"),
                    "Category": s.get("category"),
                    "Direction": s.get("direction"),
                    "Source": source_label(s),
                    "Measurable rules": sum(v is not None for v in rules.values()),
                    "AI readiness": str(readiness.get("label") or "unknown").replace("_", " ").title(),
                    "Confidence / support": s.get("confidence"),
                    "Validation": s.get("validation_status") or "unvalidated",
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        labels = {
            f"{s.get('name')} · {source_label(s)}": s
            for s in filtered
        }
        if labels:
            selected = labels[st.selectbox("Inspect strategy", list(labels))]
            st.markdown(f"### {selected.get('name')}")
            st.write(selected.get("summary") or "No summary.")
            readiness = selected.get("research_readiness") or research_readiness(selected)
            x1, x2, x3, x4 = st.columns(4)
            confidence_label = (
                "Synthesis support"
                if is_synthetic_strategy(selected)
                else "Extraction confidence"
            )
            x1.metric(confidence_label, f"{float(selected.get('confidence') or 0):.0f}%")
            x2.metric("AI research readiness", f"{safe_float(readiness.get('score'), 0.0):.0f}/100")
            x3.metric("Validation", selected.get("validation_status") or "unvalidated")
            x4.metric("Optimization", selected.get("optimization_status") or "not_run")
            st.caption(str(readiness.get("note") or ""))
            st.markdown("#### Source-extracted machine rules")
            active_rules = {
                key: value for key, value in normalize_machine_rules(selected.get("machine_rules")).items()
                if value is not None
            }
            st.json(active_rules or {"status": "No objective thresholds extracted yet."})
            research_overrides = {
                key: value
                for key, value in normalize_machine_rules(selected.get("research_rule_overrides")).items()
                if value is not None
            }
            if research_overrides:
                st.markdown("#### AI / accepted research assumptions")
                st.json(research_overrides)
                auto_prep = selected.get("autopilot_preparation") or {}
                auto_count = int(auto_prep.get("suggestions_auto_applied") or 0)
                if auto_count:
                    st.caption(
                        f"AI Autopilot added {auto_count} of these assumptions. They fill machine-testable "
                        "gaps for research and are never presented as explicit source rules."
                    )
                else:
                    st.caption(
                        "These fill machine-testable gaps for research. They are not presented as explicit source rules."
                    )
            if (
                str(selected.get("validation_status") or "").lower() == "validated"
                and isinstance(selected.get("validated_rules"), dict)
            ):
                st.markdown("#### Frozen validated rules used by live modules")
                validated_rules = {
                    key: value
                    for key, value in normalize_machine_rules(selected.get("validated_rules")).items()
                    if value is not None
                }
                st.json(validated_rules or {"status": "Validated run did not contain objective rules."})
                st.caption(
                    "The original source rules are preserved above. Market Discovery and Stock Analyzer "
                    "use this frozen validated rule set so later research edits do not silently change a validated setup."
                )
            if selected.get("unresolved_rules"):
                st.markdown("#### Requires interpretation / unavailable data")
                for item in selected.get("unresolved_rules") or []:
                    st.write("• " + str(item))


elif module == "Strategy DNA":
    st.markdown("## Strategy DNA & Cross-Book Synthesis")
    st.caption(
        "Break every extracted strategy into reusable components, measure where independent sources "
        "agree, cluster related setups, and generate research-only cross-source candidates. "
        "Source agreement and historical validation are deliberately shown as separate kinds of evidence."
    )

    if not strategies:
        st.info("Add book, document, or YouTube strategies before building the Strategy DNA map.")
    else:
        dna_strategies = []
        for item in strategies:
            if is_synthetic_strategy(item):
                continue
            enriched = dict(item)
            enriched["strategy_dna"] = infer_strategy_dna(enriched)
            dna_strategies.append(enriched)

        concept_graph = build_concept_graph(dna_strategies)
        strategy_families = build_strategy_families(dna_strategies)
        minimum_sources = int(
            st.slider(
                "Minimum independent sources for a synthesized candidate",
                min_value=2,
                max_value=max(2, min(8, len({source_identity(item) for item in dna_strategies}))),
                value=2,
                step=1,
                help=(
                    "A source can contain several strategies, but it only counts once toward independent-source support."
                ),
            )
        )
        candidate_blueprints = build_candidate_blueprints(
            strategy_families,
            min_sources=minimum_sources,
        )
        independent_sources = len({source_identity(item) for item in dna_strategies})
        corroborated = [
            item for item in concept_graph
            if int(item.get("independent_source_count") or 0) >= 2
        ]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Independent sources", independent_sources)
        m2.metric("DNA concepts", len(concept_graph))
        m3.metric("Cross-source concepts", len(corroborated))
        m4.metric("Research candidates", len(candidate_blueprints))

        st.info(
            "A concept appearing in several books means several sources teach something similar. "
            "It does NOT mean the concept works. Validated-source counts only rise after strategies "
            "survive the app's historical validation process."
        )

        tab_dna, tab_concepts, tab_families, tab_candidates = st.tabs(
            ["Strategy fingerprints", "Cross-source concepts", "Strategy families", "Candidate blueprints"]
        )

        with tab_dna:
            rows = []
            for item in dna_strategies:
                dna = item.get("strategy_dna") or {}
                row = {
                    "Strategy": item.get("name"),
                    "Source": item.get("source_title") or item.get("source_type"),
                    "Direction": item.get("direction"),
                    "Validation": item.get("validation_status") or "unvalidated",
                }
                for dimension in DNA_DIMENSIONS:
                    row[DNA_LABELS[dimension]] = ", ".join(dna.get(dimension) or [])
                rows.append(row)
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            fingerprint_labels = {
                f"{item.get('name')} · {item.get('source_title') or 'Unknown source'}": item
                for item in dna_strategies
            }
            inspected = fingerprint_labels[
                st.selectbox("Inspect DNA fingerprint", list(fingerprint_labels), key="til_dna_strategy")
            ]
            dna = inspected.get("strategy_dna") or {}
            for dimension in DNA_DIMENSIONS:
                concepts = list(dna.get(dimension) or [])
                if concepts:
                    st.markdown(f"**{DNA_LABELS[dimension]}:** " + " · ".join(concepts))

        with tab_concepts:
            only_cross_source = st.checkbox(
                "Show only concepts found in at least two independent sources",
                value=True,
                key="til_dna_cross_source_only",
            )
            visible_concepts = [
                item for item in concept_graph
                if not only_cross_source or int(item.get("independent_source_count") or 0) >= 2
            ]
            if not visible_concepts:
                st.info(
                    "No cross-source concepts yet. Add more books/documents and the map will automatically "
                    "start measuring agreement across them."
                )
            else:
                concept_rows = [
                    {
                        "Dimension": item.get("dimension_label"),
                        "Concept": item.get("concept"),
                        "Independent sources": item.get("independent_source_count"),
                        "Strategies": item.get("strategy_count"),
                        "Validated sources": item.get("validated_source_count"),
                        "Mean validated score": item.get("mean_validated_score"),
                        "Source support": item.get("support_label"),
                    }
                    for item in visible_concepts
                ]
                st.dataframe(pd.DataFrame(concept_rows), use_container_width=True, hide_index=True)

                concept_labels = {
                    (
                        f"{item.get('dimension_label')} · {item.get('concept')} · "
                        f"{int(item.get('independent_source_count') or 0)} source(s)"
                    ): item
                    for item in visible_concepts
                }
                concept = concept_labels[
                    st.selectbox("Inspect concept evidence", list(concept_labels), key="til_dna_concept")
                ]
                c1, c2, c3 = st.columns(3)
                c1.metric("Independent source support", int(concept.get("independent_source_count") or 0))
                c2.metric("Validated sources", int(concept.get("validated_source_count") or 0))
                score = safe_float(concept.get("mean_validated_score"))
                c3.metric("Mean validated score", f"{score:.1f}/100" if score is not None else "Not validated")
                st.markdown("**Sources teaching this concept**")
                for source in concept.get("source_titles") or []:
                    st.write("• " + str(source))
                if concept.get("strategy_names"):
                    with st.expander("Contributing strategies"):
                        for name in concept.get("strategy_names") or []:
                            st.write("• " + str(name))

        with tab_families:
            family_rows = [
                {
                    "Family": family.get("name"),
                    "Direction": family.get("direction"),
                    "Strategies": family.get("strategy_count"),
                    "Independent sources": family.get("independent_source_count"),
                    "Validated sources": family.get("validated_source_count"),
                    "Shared DNA concepts": len(family.get("common_concepts") or []),
                    "Explicit rule conflicts": sum(
                        1
                        for rule in (family.get("rule_consensus") or {}).values()
                        if rule.get("conflict")
                    ),
                }
                for family in strategy_families
            ]
            st.dataframe(pd.DataFrame(family_rows), use_container_width=True, hide_index=True)
            family_labels = {
                f"{family.get('name')} · {int(family.get('independent_source_count') or 0)} source(s)": family
                for family in strategy_families
            }
            family = family_labels[
                st.selectbox("Inspect strategy family", list(family_labels), key="til_dna_family")
            ]
            shared = family.get("common_concepts") or []
            if shared:
                st.markdown("**Shared DNA across independent sources**")
                for item in shared:
                    st.write(
                        f"• {item.get('dimension_label')}: **{item.get('concept')}** "
                        f"({int(item.get('source_count') or 0)} sources)"
                    )
            else:
                st.caption("This family currently has only single-source concepts.")
            with st.expander("Explicit machine-rule agreement / conflicts"):
                st.json(family.get("rule_consensus") or {"status": "No explicit measurable rules available."})

        with tab_candidates:
            st.caption(
                "These candidates are synthesis hypotheses, not live strategies. The priority score ranks "
                "research usefulness from source support and already-validated contributors; it is not a win-rate "
                "or profit forecast."
            )
            if not candidate_blueprints:
                st.info(
                    f"No strategy family currently has enough shared DNA across {minimum_sources} independent sources."
                )
            else:
                candidate_rows = [
                    {
                        "Candidate": item.get("name"),
                        "Research priority": item.get("research_priority_score"),
                        "Sources": item.get("supporting_source_count"),
                        "Strategies": item.get("supporting_strategy_count"),
                        "Validated sources": item.get("validated_source_count"),
                        "Consistent explicit rules": len(item.get("consistent_explicit_rules") or {}),
                        "Rule conflicts": len(item.get("conflicting_explicit_rules") or []),
                    }
                    for item in candidate_blueprints
                ]
                st.dataframe(pd.DataFrame(candidate_rows), use_container_width=True, hide_index=True)
                candidate_labels = {
                    f"{item.get('name')} · priority {safe_float(item.get('research_priority_score'), 0.0):.0f}": item
                    for item in candidate_blueprints
                }
                candidate = candidate_labels[
                    st.selectbox("Inspect synthesized candidate", list(candidate_labels), key="til_dna_candidate")
                ]
                st.markdown(f"### {candidate.get('name')}")
                st.warning(str(candidate.get("note") or "Research hypothesis only."))
                st.markdown("**Supporting independent sources**")
                for source in candidate.get("supporting_sources") or []:
                    st.write("• " + str(source))
                st.markdown("**Core Strategy DNA**")
                core_dna = candidate.get("core_dna") or {}
                for dimension in DNA_DIMENSIONS:
                    concepts = list(core_dna.get(dimension) or [])
                    if concepts:
                        st.write(f"**{DNA_LABELS[dimension]}:** " + " · ".join(concepts))
                st.markdown("**Explicit rules the contributing sources currently agree on**")
                st.json(
                    candidate.get("consistent_explicit_rules")
                    or {"status": "No identical explicit thresholds across the contributing sources yet."}
                )
                conflicts = list(candidate.get("conflicting_explicit_rules") or [])
                if conflicts:
                    st.markdown("**Conflicting explicit rules — keep separate until tested**")
                    for rule in conflicts:
                        details = (candidate.get("rule_consensus") or {}).get(rule) or {}
                        st.write(f"• **{rule}**: {details.get('distinct_values')}")
                st.caption(
                    "Next step for this candidate is deterministic rule compilation plus historical optimization, "
                    "walk-forward testing, untouched holdout validation, and cross-stock generalization."
                )

                executable = compile_candidate_blueprint(candidate)
                readiness = research_readiness(executable)
                effective_rules = normalize_machine_rules(
                    effective_strategy_for_research(executable).get("machine_rules")
                )
                active_effective_rules = {
                    key: value for key, value in effective_rules.items() if value is not None
                }
                st.divider()
                st.markdown("### Research pipeline")
                p1, p2, p3 = st.columns(3)
                p1.metric(
                    "Executable rule count",
                    sum(value is not None for value in effective_rules.values()),
                )
                p2.metric(
                    "Research readiness",
                    f"{safe_float(readiness.get('score'), 0.0):.0f}/100",
                )
                p3.metric(
                    "Exact source-seed rules",
                    len(executable.get("candidate_rule_options") or {}),
                )
                st.caption(
                    "Conflicting source thresholds are not averaged. One exact source value is used only as "
                    "the initial research seed, and every source-supported alternative is injected into the "
                    "optimizer before generic nearby values are explored."
                )

                with st.expander("Compiled research strategy", expanded=False):
                    st.markdown("**Effective starting rules**")
                    st.json(active_effective_rules or {"status": "No executable rules yet."})
                    if executable.get("candidate_rule_options"):
                        st.markdown("**Exact source-supported alternatives tested first**")
                        st.json(executable.get("candidate_rule_options"))
                    untranslated = executable.get("untranslated_dna") or {}
                    if untranslated:
                        st.markdown("**Shared DNA not directly represented by a machine rule yet**")
                        for dimension in DNA_DIMENSIONS:
                            concepts = list(untranslated.get(dimension) or [])
                            if concepts:
                                st.write(
                                    f"**{DNA_LABELS[dimension]}:** " + " · ".join(concepts)
                                )
                    if executable.get("unresolved_rules"):
                        st.caption(
                            "Entry/context concepts below remain visible to the AI Rule Compiler or "
                            "direct historical testing; risk/exit concepts do not falsely block research readiness."
                        )

                action_cols = st.columns(2)
                save_candidate = action_cols[0].button(
                    "💾 Save / refresh research candidate",
                    use_container_width=True,
                    key=f"save_synth_{candidate.get('id')}",
                )
                run_candidate = action_cols[1].button(
                    "🧪 Run full historical research pipeline",
                    type="primary",
                    use_container_width=True,
                    disabled=not bool(candidate.get("backtest_supported")),
                    key=f"run_synth_{candidate.get('id')}",
                    help=(
                        "Runs historical opportunity discovery, optimization, walk-forward checks, untouched "
                        "holdout validation, cost stress testing, and frozen-rule cross-stock generalization."
                    ),
                )

                if not candidate.get("backtest_supported"):
                    st.warning(
                        "This synthesized family is not currently supported by the long-only backtester. "
                        "Its DNA remains useful for research, but automatic validation is disabled for now."
                    )

                if save_candidate:
                    data = load_library()
                    executable["research_readiness"] = readiness
                    data = upsert_strategy_record(data, executable)
                    intelligence_store().save(data)
                    st.success("Saved the synthesized research candidate to the unified Strategy Library.")
                    st.rerun()

                if run_candidate:
                    try:
                        candidate_to_run = dict(executable)
                        candidate_to_run["research_readiness"] = research_readiness(candidate_to_run)

                        # Deterministic compilation happens first. Only if the shared DNA still does not
                        # provide a testable entry/filter rule do we ask the existing AI Rule Compiler
                        # for clearly labeled research assumptions.
                        if (
                            (candidate_to_run.get("research_readiness") or {}).get("label")
                            != "ready_for_backtest"
                        ):
                            compile_status = st.status(
                                "The shared DNA still has untestable gaps. AI Rule Compiler is creating "
                                "clearly labeled research assumptions…",
                                expanded=True,
                            )
                            compiler = GeminiRuleCompiler(
                                setting("GEMINI_API_KEY"),
                                setting("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
                                fallback_api_key=setting("GEMINI_PAID_API_KEY", ""),
                            )
                            candidate_to_run = prepare_strategies_with_ai(
                                [candidate_to_run],
                                compiler,
                                minimum_confidence=65.0,
                            )[0]
                            candidate_to_run["research_readiness"] = research_readiness(candidate_to_run)
                            compile_status.update(
                                label="Research-rule compilation finished",
                                state="complete",
                                expanded=False,
                            )

                        final_readiness = candidate_to_run.get("research_readiness") or {}
                        if final_readiness.get("label") != "ready_for_backtest":
                            raise AppError(
                                "This synthesized candidate still does not contain an objective entry/filter "
                                "rule that the deterministic backtester can enforce."
                            )

                        data = load_library()
                        data = upsert_strategy_record(data, candidate_to_run)
                        intelligence_store().save(data)

                        research_status = st.status(
                            "Running the synthesized candidate through Historical Research Autopilot…",
                            expanded=True,
                        )
                        report = run_autonomous_research(
                            market_client(),
                            [candidate_to_run],
                            deep_strategy_limit=1,
                            progress=lambda message: research_status.write(message),
                        )
                        data = load_library()
                        data = merge_autonomous_research_into_library(data, report)
                        intelligence_store().save(data)
                        st.session_state["til_synth_research_result"] = {
                            "candidate_id": candidate_to_run.get("id"),
                            "report": report,
                        }

                        result = (report.get("results") or [{}])[0]
                        status = str(result.get("validation_status") or "research_only")
                        score = safe_float(result.get("global_score"), 0.0) or 0.0
                        research_status.update(
                            label=(
                                f"Cross-source research complete · {status.replace('_', ' ').title()} · "
                                f"global robustness {score:.1f}/100"
                            ),
                            state="complete",
                            expanded=False,
                        )
                        st.success(
                            "The candidate was saved, optimized, walk-forward tested, holdout tested, "
                            "stress tested, and checked across multiple stocks. Only candidates that pass "
                            "the existing validation gates receive frozen validated rules."
                        )
                        st.rerun()
                    except AppError as exc:
                        st.error(str(exc))
                    except Exception as exc:
                        st.error(f"Synthesized candidate research failed: {exc}")

                stored_synth = st.session_state.get("til_synth_research_result") or {}
                if stored_synth.get("candidate_id") == executable.get("id"):
                    report = stored_synth.get("report") or {}
                    results = list(report.get("results") or [])
                    if results:
                        result = results[0]
                        st.divider()
                        st.markdown("### Latest synthesized-candidate research result")
                        r1, r2, r3, r4 = st.columns(4)
                        r1.metric(
                            "Validation",
                            str(result.get("validation_status") or "research_only")
                            .replace("_", " ")
                            .title(),
                        )
                        r2.metric(
                            "Global robustness",
                            f"{safe_float(result.get('global_score'), 0.0):.1f}/100",
                        )
                        r3.metric(
                            "Anchor stock",
                            result.get("anchor_symbol") or "—",
                        )
                        r4.metric(
                            "Stocks tested",
                            len(result.get("candidate_symbols") or []),
                        )
                        reasons = list(result.get("gate_reasons") or [])
                        if reasons:
                            with st.expander("Why it did not pass every validation gate", expanded=True):
                                for reason in reasons:
                                    st.write("• " + str(reason))


elif module == "Rule Compiler":
    st.markdown("## Rule Compiler")
    st.caption(
        "Advanced manual control for qualitative source lessons. AI Autopilot now does this automatically "
        "during book ingestion; this page lets you inspect, replace, or add research proxies yourself. "
        "All proxies stay separate from source-extracted rules."
    )

    if not strategies:
        st.info("Add or import a strategy before using the Rule Compiler.")
    else:
        compiler_choices = {}
        for item in strategies:
            label = f"{item.get('name') or 'Unnamed strategy'} · {source_label(item)}"
            if label in compiler_choices:
                label += f" · {str(item.get('id') or '')[:7]}"
            compiler_choices[label] = item
        compiler_strategy = compiler_choices[
            st.selectbox("Strategy to compile", list(compiler_choices), key="til_compiler_strategy")
        ]

        explicit = {
            key: value
            for key, value in normalize_machine_rules(compiler_strategy.get("machine_rules")).items()
            if value is not None
        }
        accepted_overrides = {
            key: value
            for key, value in normalize_machine_rules(
                compiler_strategy.get("research_rule_overrides")
            ).items()
            if value is not None
        }
        compiler_cols = st.columns(3)
        compiler_cols[0].metric("Explicit source rules", len(explicit))
        compiler_cols[1].metric("Accepted research proxies", len(accepted_overrides))
        compiler_cols[2].metric(
            "Unresolved source requirements",
            len(compiler_strategy.get("unresolved_rules") or []),
        )

        if explicit:
            with st.expander("Explicit source rules — protected from compiler edits", expanded=False):
                st.json(explicit)
        if compiler_strategy.get("unresolved_rules"):
            with st.expander("Qualitative / unresolved requirements", expanded=True):
                for rule in compiler_strategy.get("unresolved_rules") or []:
                    st.write("• " + str(rule))
        if accepted_overrides:
            with st.expander("Accepted research assumptions", expanded=True):
                st.json(accepted_overrides)
                st.warning(
                    "These values are research assumptions, not claims about what the source author explicitly specified."
                )

        compile_rules = st.button(
            "🧩 Ask AI for measurable proxy suggestions",
            type="primary",
            use_container_width=True,
        )
        if compile_rules:
            try:
                with st.status("Compiling qualitative requirements…", expanded=True) as status:
                    compiler = GeminiRuleCompiler(
                        setting("GEMINI_API_KEY"),
                        setting("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
                        fallback_api_key=setting("GEMINI_PAID_API_KEY", ""),
                    )
                    compiled = compiler.compile(compiler_strategy)
                    st.session_state["til_rule_compiler_result"] = {
                        "strategy_id": compiler_strategy.get("id"),
                        "result": compiled,
                    }
                    status.update(label="Rule Compiler suggestions ready", state="complete", expanded=False)
                st.rerun()
            except AppError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Rule Compiler failed: {exc}")

        stored_compiler = st.session_state.get("til_rule_compiler_result") or {}
        if stored_compiler.get("strategy_id") == compiler_strategy.get("id"):
            compiled = stored_compiler.get("result") or {}
            suggestions = list(compiled.get("suggestions") or [])
            if compiled.get("summary"):
                st.info(str(compiled.get("summary")))
            if suggestions:
                st.markdown("### Suggested measurable proxies")
                suggestion_rows = []
                labels = {}
                for number, suggestion in enumerate(suggestions, start=1):
                    label = (
                        f"{number}. {suggestion.get('target_rule')} = "
                        f"{suggestion.get('parsed_value')} · "
                        f"{safe_float(suggestion.get('confidence'), 0.0):.0f}% confidence"
                    )
                    labels[label] = suggestion
                    suggestion_rows.append(
                        {
                            "#": number,
                            "Source requirement": suggestion.get("source_requirement"),
                            "Machine rule": suggestion.get("target_rule"),
                            "Proposed value": suggestion.get("parsed_value"),
                            "Research assumption": bool(suggestion.get("is_research_assumption")),
                            "Confidence": safe_float(suggestion.get("confidence"), 0.0),
                            "Why this proxy": suggestion.get("rationale"),
                        }
                    )
                st.dataframe(pd.DataFrame(suggestion_rows), use_container_width=True, hide_index=True)
                chosen_labels = st.multiselect(
                    "Accept suggestions into the research rule set",
                    list(labels),
                    default=[],
                    help=(
                        "Nothing is applied until you select suggestions here and press Save. "
                        "They remain separate from explicit source rules."
                    ),
                )
                save_compiler = st.button(
                    "💾 Save selected research assumptions",
                    use_container_width=True,
                    disabled=not chosen_labels,
                )
                if save_compiler:
                    data = load_library()
                    for item in data.get("strategies") or []:
                        if str(item.get("id") or "") != str(compiler_strategy.get("id") or ""):
                            continue
                        overrides = dict(item.get("research_rule_overrides") or {})
                        assumption_log = list(item.get("compiler_assumptions") or [])
                        for label in chosen_labels:
                            suggestion = labels[label]
                            target = str(suggestion.get("target_rule") or "")
                            if normalize_machine_rules(item.get("machine_rules")).get(target) is not None:
                                continue
                            overrides[target] = suggestion.get("parsed_value")
                            assumption_log.append(
                                {
                                    "target_rule": target,
                                    "value": suggestion.get("parsed_value"),
                                    "source_requirement": suggestion.get("source_requirement"),
                                    "rationale": suggestion.get("rationale"),
                                    "confidence": suggestion.get("confidence"),
                                    "accepted_at": compiled.get("generated_at"),
                                    "model": compiled.get("model"),
                                }
                            )
                        item["research_rule_overrides"] = overrides
                        item["compiler_assumptions"] = assumption_log[-100:]
                        item["validation_status"] = "unvalidated"
                        item.pop("validated_rules", None)
                        item.pop("validated_backtest_settings", None)
                        item.pop("validated_at", None)
                        break
                    intelligence_store().save(data)
                    st.success(
                        "Research assumptions saved. Any previous validation was cleared because the executable rule set changed."
                    )
                    st.session_state.pop("til_rule_compiler_result", None)
                    st.rerun()
            else:
                st.info(
                    "The compiler did not find a defensible mapping to the machine rules the backtester currently supports."
                )

            if compiled.get("unmapped_requirements"):
                with st.expander("Still not machine-testable", expanded=False):
                    for item in compiled.get("unmapped_requirements") or []:
                        st.write("• " + str(item))

        if accepted_overrides and st.button(
            "Remove all accepted research assumptions",
            use_container_width=True,
        ):
            data = load_library()
            for item in data.get("strategies") or []:
                if str(item.get("id") or "") == str(compiler_strategy.get("id") or ""):
                    item["research_rule_overrides"] = {}
                    item["validation_status"] = "unvalidated"
                    item.pop("validated_rules", None)
                    item.pop("validated_backtest_settings", None)
                    item.pop("validated_at", None)
                    break
            intelligence_store().save(data)
            st.success("Research assumptions removed; source-extracted rules were left unchanged.")
            st.rerun()


elif module == "AI Research Autopilot":
    st.markdown("## AI Research Autopilot")
    st.caption(
        "No ticker selection is required. The Lab builds a broad stock universe, finds historical "
        "opportunity candidates from information available at the time, deep-tests the best strategy/stock "
        "families, runs untouched holdout and walk-forward checks, freezes finalist rules, tests them across "
        "multiple stocks, and saves the outcome automatically."
    )

    ready_strategies = [
        item
        for item in strategies
        if (item.get("research_readiness") or research_readiness(item)).get("label") == "ready_for_backtest"
    ]
    auto_metrics = st.columns(4)
    auto_metrics[0].metric("Strategies ready", len(ready_strategies))
    auto_metrics[1].metric(
        "Already validated",
        sum(1 for item in strategies if str(item.get("validation_status") or "") == "validated"),
    )
    auto_metrics[2].metric(
        "Autopilot runs",
        sum(1 for item in library.get("research_runs") or [] if item.get("kind") == "autonomous_research"),
    )
    auto_metrics[3].metric(
        "Manual ticker setup",
        "None",
    )

    st.info(
        "The first stage now samples from Alpaca's active + inactive exchange-listed U.S. equity master "
        "catalog and reserves part of every run for inactive/delisted names. Dated daily bars infer when "
        "each symbol actually existed; future trade P/L is never used to choose candidate stocks. Deep "
        "testing then downloads bounded intraday windows around the actual historical opportunity dates."
    )

    auto_button_slot = st.empty()
    run_auto = auto_button_slot.button(
        "🤖 Run full autonomous research now",
        type="primary",
        use_container_width=True,
        disabled=not ready_strategies,
        key="til_run_full_autonomous_research",
    )
    if run_auto:
        auto_button_slot.button(
            "🤖 Researching…",
            type="primary",
            use_container_width=True,
            disabled=True,
            key="til_run_full_autonomous_research_busy",
        )

        attempt_started_at = utc_now().isoformat()
        timing_started = time.monotonic()
        timing_recorder = AutonomousResearchTimingRecorder()
        eta_estimator = AutonomousResearchEtaEstimator.from_research_runs(
            library.get("research_runs") or []
        )
        st.session_state["til_auto_research_last_attempt"] = {
            "status": "running",
            "started_at": attempt_started_at,
        }
        estimator = AutonomousResearchProgressEstimator()
        activity_log: list[str] = []
        auto_progress = st.progress(
            0.01,
            text="Estimated progress: 1% · starting autonomous research…",
        )
        st.caption(
            "Progress is estimated because API downloads, optimization, and walk-forward stages "
            "do not all take the same amount of time. Time remaining is learned from completed "
            "Autopilot runs and is shown as a range rather than a fake countdown."
        )

        st.markdown("**Latest activity**")
        with st.container(height=180, border=True):
            latest_activity = st.empty()

        with st.expander("View full research activity", expanded=False):
            full_activity = st.empty()

        def update_auto_activity(message: str) -> None:
            text = str(message or "").strip()
            if not text:
                return
            activity_log.append(text)
            if len(activity_log) > 250:
                del activity_log[:-250]

            fraction = estimator.update(text)
            current_elapsed = time.monotonic() - timing_started
            timing_recorder.record(fraction, current_elapsed, text)
            eta_range = eta_estimator.estimate_range(
                fraction,
                current_elapsed_seconds=current_elapsed,
            )
            eta_text = format_eta_range(eta_range)
            progress_text = f"Estimated progress: {estimator.percent}%"
            if eta_text:
                progress_text += f" · Estimated time remaining: {eta_text}"
            else:
                progress_text += " · Estimating time remaining…"
            auto_progress.progress(
                max(0.01, fraction),
                text=progress_text,
            )

            # Keep the compact panel focused on the newest work so the user never has to scroll it.
            recent = activity_log[-4:]
            latest_activity.markdown(
                "\n\n".join(f"• {item}" for item in recent)
            )
            full_activity.markdown(
                "\n\n".join(f"• {item}" for item in activity_log)
            )

        update_auto_activity("Starting autonomous research funnel…")

        try:
            report = run_autonomous_research(
                market_client(),
                ready_strategies,
                progress=update_auto_activity,
            )
            update_auto_activity("Saving autonomous research results…")
            total_seconds = time.monotonic() - timing_started
            universe_info = report.get("universe") or {}
            report["timing_profile"] = timing_recorder.finish(
                total_seconds,
                deep_strategies_attempted=int(report.get("deep_strategies_attempted") or 0),
                universe_sample_size=int(
                    universe_info.get("sampled_stocks")
                    or universe_info.get("sample_size")
                    or universe_info.get("requested_sample_size")
                    or 0
                ),
            )
            data = merge_autonomous_research_into_library(load_library(), report)
            intelligence_store().save(data)
            st.session_state["til_auto_research_result"] = report
            validated_count = sum(
                1
                for item in report.get("results") or []
                if item.get("validation_status") == "validated"
            )
            failed_count = int(report.get("deep_strategies_failed") or 0)
            st.session_state["til_auto_research_last_attempt"] = {
                "status": str(report.get("run_status") or "complete"),
                "started_at": attempt_started_at,
                "completed_at": report.get("generated_at") or utc_now().isoformat(),
                "failed_finalists": report.get("failed_finalists") or [],
            }
            auto_progress.progress(
                1.0,
                text="Research complete · 100%",
            )
            latest_activity.success(
                f"Autonomous research complete · "
                f"{int(report.get('deep_strategies_tested') or 0)} deep finalists completed · "
                f"{failed_count} skipped · {validated_count} validated"
            )
            st.rerun()
        except AppError as exc:
            st.session_state["til_auto_research_last_attempt"] = {
                "status": "stopped",
                "started_at": attempt_started_at,
                "stopped_at": utc_now().isoformat(),
                "error": str(exc),
            }
            auto_progress.progress(
                max(0.01, estimator.fraction),
                text=f"Research stopped · estimated {estimator.percent}%",
            )
            latest_activity.error("Autonomous research stopped safely.")
            st.error(str(exc))
        except Exception as exc:
            st.session_state["til_auto_research_last_attempt"] = {
                "status": "failed",
                "started_at": attempt_started_at,
                "stopped_at": utc_now().isoformat(),
                "error": str(exc),
            }
            auto_progress.progress(
                max(0.01, estimator.fraction),
                text=f"Research failed · estimated {estimator.percent}%",
            )
            latest_activity.error("Autonomous research failed.")
            st.error(f"Autonomous research failed: {exc}")

    current_auto = st.session_state.get("til_auto_research_result")
    if not current_auto:
        current_auto = next(
            (
                item
                for item in library.get("research_runs") or []
                if item.get("kind") == "autonomous_research"
            ),
            None,
        )

    if current_auto:
        st.divider()
        last_attempt = st.session_state.get("til_auto_research_last_attempt") or {}
        attempt_status = str(last_attempt.get("status") or "")
        attempt_started = str(last_attempt.get("started_at") or "")
        report_generated = str(current_auto.get("generated_at") or "")
        if (
            attempt_status in {"stopped", "failed"}
            and attempt_started
            and (not report_generated or attempt_started > report_generated)
        ):
            st.warning(
                "The leaderboard below is from the previous completed research run. "
                "The newest attempt stopped before it could replace these results."
            )
        st.markdown("### Latest autonomous leaderboard")
        universe = current_auto.get("universe") or {}
        sampled_count = (
            len(universe.get("symbols") or [])
            if universe.get("symbols")
            else universe.get("population_size", "—")
        )
        run_status = str(current_auto.get("run_status") or "complete").replace("_", " ")
        st.caption(
            f"Run: {run_status} · "
            f"deep attempted: {current_auto.get('deep_strategies_attempted', current_auto.get('deep_strategies_tested', '—'))} · "
            f"completed: {current_auto.get('deep_strategies_tested', '—')} · "
            f"skipped: {current_auto.get('deep_strategies_failed', 0)} · "
            f"Universe: {universe.get('source') or '—'} · "
            f"sampled stocks: {sampled_count} · "
            f"active sampled: {universe.get('active_sampled', '—')} · "
            f"inactive sampled: {universe.get('inactive_sampled', '—')} · "
            f"symbols with historical bars: {universe.get('symbols_with_historical_bars', '—')} · "
            f"history horizon: {current_auto.get('point_in_time_horizon_years', '—')} years · "
            f"generated: {current_auto.get('generated_at') or '—'}"
        )
        failed_finalists = current_auto.get("failed_finalists") or []
        if failed_finalists:
            with st.expander(
                f"Skipped deep-research finalists ({len(failed_finalists)})",
                expanded=False,
            ):
                for failed in failed_finalists:
                    st.write(
                        f"**{failed.get('strategy_name') or 'Strategy'}** — "
                        f"{failed.get('error') or 'Research step could not be completed.'}"
                    )

        def current_result_scores(result: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], float]:
            general = result.get("generalization") or {}
            summary = general.get("summary") or {}
            strength = validation_strength(
                result.get("optimization_report") or {},
                result.get("walk_forward") or None,
            )
            global_score = round(
                (safe_float(strength.get("score"), 0.0) or 0.0) * 0.65
                + (safe_float(summary.get("score"), 0.0) or 0.0) * 0.35,
                1,
            )
            return strength, summary, global_score

        result_rows = []
        for result in current_auto.get("results") or []:
            strength, summary, display_global_score = current_result_scores(result)
            winner = (result.get("optimization_report") or {}).get("winner") or {}
            holdout = winner.get("holdout_metrics") or {}
            result_rows.append(
                {
                    "Strategy": result.get("strategy_name"),
                    "Status": str(result.get("validation_status") or "research_only").replace("_", " ").title(),
                    "Global score": display_global_score,
                    "Robustness": safe_float(strength.get("score"), 0.0) or 0.0,
                    "Cross-stock": safe_float(summary.get("score"), 0.0) or 0.0,
                    "Anchor": result.get("anchor_symbol"),
                    "Stocks tested": int(summary.get("active_symbols") or 0),
                    "Cross-stock trades": int(summary.get("total_trades") or 0),
                    "Holdout P/L": safe_float(holdout.get("net_pnl"), 0.0) or 0.0,
                }
            )
        if result_rows:
            st.dataframe(
                pd.DataFrame(result_rows).sort_values(
                    ["Status", "Global score"],
                    ascending=[True, False],
                ),
                use_container_width=True,
                hide_index=True,
            )

        for result in current_auto.get("results") or []:
            display_strength, _, display_global_score = current_result_scores(result)
            with st.expander(
                f"{result.get('strategy_name') or 'Strategy'} · "
                f"{str(result.get('validation_status') or 'research_only').replace('_', ' ').title()} · "
                f"{display_global_score:.1f}/100",
                expanded=False,
            ):
                st.write(
                    f"Historical opportunity anchor: **{result.get('anchor_symbol') or '—'}** · "
                    f"cross-stock candidates: {', '.join(result.get('candidate_symbols') or []) or '—'}"
                )
                stored_strength = safe_float((result.get("strength") or {}).get("score"))
                current_strength = safe_float(display_strength.get("score"), 0.0) or 0.0
                if stored_strength is not None and abs(stored_strength - current_strength) >= 0.1:
                    st.info(
                        f"Robustness recalibrated from the saved {stored_strength:.1f}/100 to "
                        f"{current_strength:.1f}/100 using the current conservative stability rules."
                    )
                if display_strength.get("reasons"):
                    st.markdown("**Robustness cautions:**")
                    for reason in display_strength.get("reasons") or []:
                        st.write("• " + str(reason))
                opportunities = result.get("opportunities") or []
                if opportunities:
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "Stock": item.get("symbol"),
                                    "Current status": (result.get("asset_status_by_symbol") or {}).get(item.get("symbol")) or "—",
                                    "Opportunity days": item.get("event_count"),
                                    "Discovery score": item.get("score"),
                                    "Peak move %": item.get("peak_directional_move_pct"),
                                    "Peak RVOL": item.get("peak_relative_volume"),
                                    "Observed from": ((result.get("symbol_lifecycles") or {}).get(item.get("symbol")) or {}).get("first_observed_date"),
                                    "Observed through": ((result.get("symbol_lifecycles") or {}).get(item.get("symbol")) or {}).get("last_observed_date"),
                                    "Research window": (
                                        f"{((result.get('research_windows') or {}).get(item.get('symbol')) or {}).get('start_date', '—')} → "
                                        f"{((result.get('research_windows') or {}).get(item.get('symbol')) or {}).get('end_date', '—')}"
                                    ),
                                    "Selection": item.get("candidate_selection_mode"),
                                }
                                for item in opportunities
                            ]
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )
                if result.get("gate_reasons"):
                    st.markdown("**Why it did not pass every autonomous gate:**")
                    for reason in result.get("gate_reasons") or []:
                        st.write("• " + str(reason))
                else:
                    st.success(
                        "Passed anchor validation, untouched holdout, stress, cross-stock breadth, "
                        "trade-count, available walk-forward gates, and the point-in-time universe gate."
                    )

        for limitation in current_auto.get("limitations") or []:
            if limitation:
                st.warning(str(limitation))
    elif ready_strategies:
        st.info(
            "No autonomous run has been saved yet. New book uploads can run this automatically, "
            "or the single button above can research the current library."
        )
    else:
        st.info(
            "No strategy is machine-testable enough for deep autonomous research yet. Book ingestion "
            "Autopilot will keep trying to translate qualitative rules into testable research assumptions."
        )


elif module == "Strategy Lab":
    st.markdown("## Strategy Lab")
    st.caption(
        "Choose a strategy from any source, download historical Alpaca candles, optimize only on "
        "earlier sessions, then evaluate separate validation and untouched holdout periods."
    )

    if not strategies:
        st.info("Add or import at least one strategy before running the Strategy Lab.")
    else:
        strategy_labels: dict[str, dict[str, Any]] = {}
        for item in strategies:
            label = f"{item.get('name') or 'Unnamed strategy'} · {source_label(item)}"
            if label in strategy_labels:
                label += f" · {str(item.get('id') or '')[:7]}"
            strategy_labels[label] = item

        selected_label = st.selectbox("Strategy to research", list(strategy_labels))
        selected_strategy = strategy_labels[selected_label]
        compare_all = st.checkbox(
            "Compare all compatible strategies and let validation choose the winner",
            value=False,
            help=(
                "Useful for discovery, but it increases selection pressure because more strategy "
                "families are competing for the same historical periods."
            ),
        )
        candidates = (
            [effective_strategy_for_research(item) for item in strategies]
            if compare_all
            else [effective_strategy_for_research(selected_strategy)]
        )
        selected_research_strategy = effective_strategy_for_research(selected_strategy)

        active_rules = {
            key: value
            for key, value in normalize_machine_rules(selected_research_strategy.get("machine_rules")).items()
            if value is not None
        }
        entry_rule_count = sum(
            1
            for key, value in active_rules.items()
            if key not in {"stop_loss_pct", "reward_risk", "max_hold_minutes"} and value is not None
        )
        if entry_rule_count == 0 and not compare_all:
            st.warning(
                "This strategy currently has no objective entry/filter rule that the backtester can "
                "enforce. The AI may have extracted only qualitative conditions. It should be translated "
                "into measurable rules before treating a backtest as meaningful."
            )

        top = st.columns(4)
        ticker = top[0].text_input("Stock ticker", value="SDOT", max_chars=10).strip().upper()
        history_days = top[1].slider("Historical calendar days", 7, 180, 30, 1)
        timeframe = top[2].selectbox("Candle size", ["1Min", "5Min", "15Min"], index=1)
        search_depth = top[3].selectbox(
            "Optimization depth",
            [12, 36, 96, 160],
            index=1,
            format_func=lambda value: {
                12: "Quick · 12 base variants",
                36: "Balanced · 36 base variants",
                96: "Deep · 96 base variants",
                160: "Very deep · 160 base variants",
            }[value],
        )

        risk_cols = st.columns(4)
        starting_cash = float(
            risk_cols[0].number_input("Starting simulation cash ($)", 1000.0, 1000000.0, 10000.0, 1000.0)
        )
        risk_per_trade = float(risk_cols[1].number_input("Risk budget per trade (%)", 0.1, 10.0, 0.5, 0.1))
        max_position = float(risk_cols[2].number_input("Maximum total position (%)", 1.0, 100.0, 20.0, 1.0))
        max_drawdown = float(risk_cols[3].number_input("Validation drawdown ceiling (%)", 1.0, 75.0, 15.0, 1.0))

        with st.expander("Advanced validation settings", expanded=False):
            v1, v2, v3, v4 = st.columns(4)
            training_fraction = v1.slider("Training share", 0.40, 0.75, 0.60, 0.05)
            validation_fraction = v2.slider("Validation share", 0.10, 0.35, 0.20, 0.05)
            minimum_training_trades = v3.number_input("Minimum training trades", 1, 50, 5, 1)
            minimum_validation_trades = v4.number_input("Minimum validation/holdout trades", 1, 25, 2, 1)
            run_walk_forward = st.checkbox(
                "Also run rolling walk-forward re-optimization",
                value=False,
                help=(
                    "Much more computationally expensive. Each fold re-optimizes using only earlier "
                    "sessions, freezes the winner, and tests it on the next unseen block."
                ),
            )
            if run_walk_forward:
                w1, w2, w3 = st.columns(3)
                wf_history_sessions = int(w1.number_input("Minimum prior sessions per fold", 5, 60, 8, 1))
                wf_test_sessions = int(w2.number_input("Unseen sessions per fold", 1, 10, 2, 1))
                wf_folds = int(w3.number_input("Walk-forward folds", 1, 6, 3, 1))
            else:
                wf_history_sessions, wf_test_sessions, wf_folds = 8, 2, 3

        split_ok = training_fraction + validation_fraction <= 0.90
        if not split_ok:
            st.error("Training + validation must leave at least 10% of sessions untouched for final holdout.")

        run_lab = st.button(
            "🧪 Optimize + validate strategy",
            type="primary",
            use_container_width=True,
            disabled=not ticker or not split_ok or (entry_rule_count == 0 and not compare_all),
        )

        if run_lab:
            try:
                market = market_client()
                end_time = utc_now()
                if market.historical_feed == "sip" and market.live_feed != "sip":
                    end_time -= timedelta(minutes=16)
                start_time = end_time - timedelta(days=int(history_days))

                data_progress = st.progress(0.0, text=f"Downloading {ticker} historical candles…")
                rows_by_symbol = market.bars(
                    [ticker],
                    start=start_time,
                    end=end_time,
                    timeframe=timeframe,
                    max_pages=30,
                    progress=lambda page: data_progress.progress(
                        min(0.95, page / 30.0),
                        text=f"Downloading {ticker} historical candles · page {page}",
                    ),
                )
                rows = list(rows_by_symbol.get(ticker) or [])
                data_progress.progress(1.0, text=f"Downloaded {len(rows):,} candles")
                if not rows:
                    raise AppError(f"No historical {timeframe} candles were returned for {ticker}.")

                catalyst_summary = None
                needs_historical_catalysts = any(
                    bool(normalize_machine_rules(item.get("machine_rules")).get("catalyst_required"))
                    for item in candidates
                )
                if needs_historical_catalysts:
                    catalyst_progress = st.progress(
                        0.0,
                        text="Downloading point-in-time historical catalyst news…",
                    )
                    articles = historical_news(
                        market,
                        [ticker],
                        start=start_time - timedelta(hours=24),
                        end=end_time,
                        max_pages=60,
                        progress=lambda page: catalyst_progress.progress(
                            min(0.95, page / 60.0),
                            text=f"Downloading historical catalyst news · page {page}",
                        ),
                    )
                    rows, catalyst_summary = enrich_bars_with_point_in_time_catalysts(
                        rows,
                        articles,
                        lookback_hours=24.0,
                    )
                    catalyst_progress.progress(
                        1.0,
                        text=(
                            f"Catalyst history ready · {catalyst_summary.get('specific_catalysts', 0)} "
                            "classified events"
                        ),
                    )

                backtest_settings = BacktestSettings(
                    starting_cash=starting_cash,
                    risk_per_trade_pct=risk_per_trade,
                    max_position_pct=max_position,
                    train_fraction=0.70,
                )
                optimization_settings = OptimizationSettings(
                    max_variants_per_strategy=int(search_depth),
                    finalists_per_strategy=min(6, int(search_depth)),
                    minimum_training_trades=int(minimum_training_trades),
                    minimum_validation_trades=int(minimum_validation_trades),
                    training_fraction=float(training_fraction),
                    validation_fraction=float(validation_fraction),
                    maximum_drawdown_pct=max_drawdown,
                    selection_mode="validated",
                )

                opt_progress = st.progress(0.0, text="Starting validated optimization…")
                def optimizer_progress(done: int, total: int, message: str) -> None:
                    opt_progress.progress(min(1.0, done / max(1, total)), text=message)

                report = optimize_stock_strategies(
                    rows,
                    candidates,
                    ticker,
                    backtest_settings,
                    optimization_settings,
                    progress=optimizer_progress,
                    finalize_holdout=True,
                )
                opt_progress.progress(1.0, text="Training, validation, and final holdout complete.")

                walk_report = None
                if run_walk_forward:
                    wf_progress_bar = st.progress(0.0, text="Starting walk-forward validation…")
                    def walk_progress(done: int, total: int, message: str) -> None:
                        wf_progress_bar.progress(min(1.0, done / max(1, total)), text=message)

                    walk_report = walk_forward_validate(
                        rows,
                        candidates,
                        ticker,
                        backtest_settings,
                        optimization_settings,
                        minimum_history_sessions=wf_history_sessions,
                        test_sessions_per_fold=wf_test_sessions,
                        max_folds=wf_folds,
                        progress=walk_progress,
                    )
                    wf_progress_bar.progress(1.0, text="Walk-forward validation complete.")

                strength = validation_strength(report, walk_report)
                st.session_state["til_strategy_lab_result"] = {
                    "ticker": ticker,
                    "timeframe": timeframe,
                    "history_days": history_days,
                    "report": report,
                    "walk_forward": walk_report,
                    "strength": strength,
                    "compared_all": compare_all,
                    "catalyst_summary": catalyst_summary,
                }
                st.rerun()
            except AppError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Strategy Lab run failed: {exc}")

        lab_result = st.session_state.get("til_strategy_lab_result") or {}
        if lab_result:
            report = lab_result.get("report") or {}
            winner = report.get("winner") or {}
            strength = lab_result.get("strength") or validation_strength(report)
            walk_report = lab_result.get("walk_forward")
            training = winner.get("training_metrics") or {}
            validation = winner.get("validation_metrics") or {}
            holdout = winner.get("holdout_metrics") or {}
            stress = winner.get("stress_metrics") or {}

            st.divider()
            st.markdown(f"## Result · {lab_result.get('ticker')}")
            headline = st.columns(5)
            headline[0].metric("Robustness score", f"{safe_float(strength.get('score'), 0.0):.1f}/100")
            headline[1].metric("Grade", strength.get("label") or "—")
            headline[2].metric("Selected strategy", winner.get("strategy_name") or "—")
            headline[3].metric("Optimizer status", winner.get("status") or "—")
            headline[4].metric("Variants tested", f"{int(report.get('variants_tested') or 0):,}")
            st.caption(strength.get("note") or "")

            catalyst_summary = lab_result.get("catalyst_summary")
            if catalyst_summary:
                st.success(
                    "Point-in-time catalyst filter applied: "
                    f"{int(catalyst_summary.get('specific_catalysts') or 0)} classified catalyst events "
                    f"({int(catalyst_summary.get('positive_catalysts') or 0)} positive, "
                    f"{int(catalyst_summary.get('negative_catalysts') or 0)} negative). "
                    "News published after a bar is not visible to that bar."
                )

            period_rows = []
            for name, metrics in (
                ("Training", training),
                ("Validation", validation),
                ("Untouched holdout", holdout),
                ("Higher-cost stress", stress),
            ):
                period_rows.append(
                    {
                        "Period": name,
                        "Trades": int(safe_float(metrics.get("trade_count"), 0) or 0),
                        "Net P/L": safe_float(metrics.get("net_pnl"), 0.0) or 0.0,
                        "Return %": safe_float(metrics.get("return_pct"), 0.0) or 0.0,
                        "Win rate %": safe_float(metrics.get("win_rate_pct"), 0.0) or 0.0,
                        "Profit factor": metrics.get("profit_factor"),
                        "Max drawdown %": safe_float(metrics.get("max_drawdown_pct"), 0.0) or 0.0,
                    }
                )
            st.dataframe(pd.DataFrame(period_rows), use_container_width=True, hide_index=True)

            if strength.get("reasons"):
                with st.expander("Why the robustness score was reduced", expanded=False):
                    for reason in strength.get("reasons") or []:
                        st.write("• " + str(reason))

            if walk_report:
                summary = walk_report.get("summary") or {}
                st.markdown("### Rolling walk-forward")
                wf_cols = st.columns(5)
                wf_cols[0].metric("Walk-forward score", f"{safe_float(summary.get('score'), 0.0):.1f}/100")
                wf_cols[1].metric("Profitable folds", f"{safe_float(summary.get('profitable_fold_pct'), 0.0):.0f}%")
                wf_cols[2].metric("External trades", int(summary.get("external_trade_count") or 0))
                wf_cols[3].metric("External net P/L", f"${safe_float(summary.get('external_net_pnl'), 0.0):,.2f}")
                pf = summary.get("external_profit_factor")
                wf_cols[4].metric("External profit factor", f"{safe_float(pf, 0.0):.2f}" if pf is not None else "—")

                fold_rows = []
                for fold in walk_report.get("folds") or []:
                    metrics = fold.get("external_metrics") or {}
                    fold_rows.append(
                        {
                            "Fold": fold.get("fold"),
                            "Optimized through": fold.get("history_end"),
                            "Unseen test": f"{fold.get('external_test_start')} → {fold.get('external_test_end')}",
                            "Strategy": fold.get("selected_strategy_name"),
                            "Trades": int(safe_float(metrics.get("trade_count"), 0) or 0),
                            "Net P/L": safe_float(metrics.get("net_pnl"), 0.0) or 0.0,
                            "Return %": safe_float(metrics.get("return_pct"), 0.0) or 0.0,
                            "Profit factor": metrics.get("profit_factor"),
                        }
                    )
                if fold_rows:
                    st.dataframe(pd.DataFrame(fold_rows), use_container_width=True, hide_index=True)
                for warning in walk_report.get("warnings") or []:
                    st.warning(str(warning))

            for warning in report.get("warnings") or []:
                st.warning(str(warning))

            winner_id = str(winner.get("source_strategy_id") or "")
            can_mark_validated = (
                winner.get("status") == "VALIDATED"
                and bool(strength.get("independently_positive"))
                and safe_float(strength.get("score"), 0.0) >= 65.0
            )
            save_validation = st.button(
                "💾 Save this validation result to the strategy library",
                use_container_width=True,
            )
            if save_validation:
                data = load_library()
                validation_status = "validated" if can_mark_validated else "research_only"
                for item in data.get("strategies") or []:
                    if str(item.get("id") or "") == winner_id:
                        item["validation_status"] = validation_status
                        if validation_status == "validated":
                            item["validated_rules"] = winner.get("optimized_rules") or {}
                            item["validated_backtest_settings"] = winner.get("optimized_backtest_settings") or {}
                            item["validated_at"] = report.get("generated_at")
                        item["last_validation"] = {
                            "symbol": report.get("symbol"),
                            "generated_at": report.get("generated_at"),
                            "robustness_score": strength.get("score"),
                            "robustness_label": strength.get("label"),
                            "optimizer_status": winner.get("status"),
                            "training_metrics": training,
                            "validation_metrics": validation,
                            "holdout_metrics": holdout,
                            "stress_metrics": stress,
                            "walk_forward_summary": (walk_report or {}).get("summary"),
                        }
                        break

                run_id = f"{winner_id}:{report.get('symbol')}:{report.get('generated_at')}"
                record = {
                    "id": run_id,
                    "strategy_id": winner_id,
                    "strategy_name": winner.get("strategy_name"),
                    "symbol": report.get("symbol"),
                    "generated_at": report.get("generated_at"),
                    "timeframe": lab_result.get("timeframe"),
                    "history_days": lab_result.get("history_days"),
                    "robustness": strength,
                    "optimizer_status": winner.get("status"),
                    "validation_status": validation_status,
                    "training_metrics": training,
                    "validation_metrics": validation,
                    "holdout_metrics": holdout,
                    "stress_metrics": stress,
                    "walk_forward_summary": (walk_report or {}).get("summary"),
                    "optimized_rules": winner.get("optimized_rules") or {},
                    "optimized_backtest_settings": winner.get("optimized_backtest_settings") or {},
                }
                existing_runs = [
                    item for item in data.get("validation_runs") or []
                    if item.get("id") != run_id
                ]
                data["validation_runs"] = [record, *existing_runs][:200]
                intelligence_store().save(data)
                st.success(
                    "Validation saved. "
                    + (
                        "This candidate met the current validation gate."
                        if validation_status == "validated"
                        else "It remains research-only because one or more validation gates were not met."
                    )
                )


elif module == "Universe Research":
    st.markdown("## Universe Research")
    st.caption(
        "Run one frozen strategy unchanged across several stocks. This is designed to expose ticker-specific "
        "overfitting: a strategy that only works on one symbol should look narrow here."
    )

    if not strategies:
        st.info("Add or import a strategy before running cross-stock research.")
    else:
        universe_choices = {}
        for item in sorted(
            strategies,
            key=lambda value: (
                str(value.get("validation_status") or "").lower() != "validated",
                str(value.get("name") or ""),
            ),
        ):
            status = str(item.get("validation_status") or "unvalidated").replace("_", " ").title()
            label = f"{item.get('name') or 'Unnamed strategy'} · {status}"
            if label in universe_choices:
                label += f" · {str(item.get('id') or '')[:7]}"
            universe_choices[label] = item
        universe_strategy = universe_choices[
            st.selectbox("Strategy to generalize", list(universe_choices), key="til_universe_strategy")
        ]
        effective_universe_strategy = effective_strategy_for_live(universe_strategy)
        if not effective_universe_strategy.get("using_validated_rules"):
            st.warning(
                "This strategy has no frozen validated rule set yet. The test will use its current research rules."
            )

        u1, u2, u3 = st.columns([2.3, 1.0, 1.0])
        raw_universe = u1.text_input(
            "Stocks to compare",
            value="AAPL NVDA TSLA AMD META",
            help="Use spaces or commas. Five or more different stocks gives a more useful portability check.",
        )
        universe_days = int(u2.slider("Calendar days", 14, 180, 45, 1, key="til_universe_days"))
        universe_timeframe = u3.selectbox(
            "Candle size",
            ["1Min", "5Min", "15Min"],
            index=1,
            key="til_universe_timeframe",
        )
        universe_symbols = []
        for token in raw_universe.replace(",", " ").split():
            symbol = token.strip().upper()
            if symbol and symbol not in universe_symbols:
                universe_symbols.append(symbol)
        universe_symbols = universe_symbols[:12]

        run_universe = st.button(
            "🧬 Test strategy across stocks",
            type="primary",
            use_container_width=True,
            disabled=len(universe_symbols) < 2,
        )
        if run_universe:
            try:
                market = market_client()
                end_time = utc_now()
                if market.historical_feed == "sip" and market.live_feed != "sip":
                    end_time -= timedelta(minutes=16)
                start_time = end_time - timedelta(days=universe_days)
                status_box = st.status(
                    f"Downloading history for {len(universe_symbols)} stocks…",
                    expanded=True,
                )
                rows_by_symbol = market.bars(
                    universe_symbols,
                    start=start_time,
                    end=end_time,
                    timeframe=universe_timeframe,
                    max_pages=40,
                    progress=lambda page: status_box.write(f"Historical candle page {page}…"),
                )

                rules = normalize_machine_rules(effective_universe_strategy.get("machine_rules"))
                catalyst_summary_by_symbol = {}
                if rules.get("catalyst_required"):
                    status_box.write("Downloading point-in-time historical catalyst news…")
                    articles = historical_news(
                        market,
                        universe_symbols,
                        start=start_time - timedelta(hours=24),
                        end=end_time,
                        max_pages=80,
                    )
                    for symbol in universe_symbols:
                        symbol_articles = [
                            article
                            for article in articles
                            if symbol in [str(x).upper() for x in article.get("symbols") or []]
                        ]
                        enriched, cat_summary = enrich_bars_with_point_in_time_catalysts(
                            list(rows_by_symbol.get(symbol) or []),
                            symbol_articles,
                            lookback_hours=24.0,
                        )
                        rows_by_symbol[symbol] = enriched
                        catalyst_summary_by_symbol[symbol] = cat_summary

                report = cross_stock_generalization(
                    {symbol: list(rows_by_symbol.get(symbol) or []) for symbol in universe_symbols},
                    universe_strategy,
                    BacktestSettings(),
                )
                report["timeframe"] = universe_timeframe
                report["history_days"] = universe_days
                report["catalyst_summary_by_symbol"] = catalyst_summary_by_symbol
                st.session_state["til_universe_result"] = report
                status_box.update(
                    label=f"Cross-stock test complete · {report.get('symbols_tested')} stocks",
                    state="complete",
                    expanded=False,
                )
                st.rerun()
            except AppError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Universe research failed: {exc}")

        universe_result = st.session_state.get("til_universe_result") or {}
        if universe_result:
            summary = universe_result.get("summary") or {}
            st.divider()
            st.markdown(f"### {universe_result.get('strategy_name') or 'Strategy'} · cross-stock result")
            cols = st.columns(5)
            cols[0].metric("Generalization score", f"{safe_float(summary.get('score'), 0.0):.1f}/100")
            cols[1].metric("Breadth", summary.get("label") or "—")
            cols[2].metric("Profitable stocks", f"{safe_float(summary.get('profitable_symbol_pct'), 0.0):.0f}%")
            cols[3].metric("Stocks with trades", f"{safe_float(summary.get('coverage_pct'), 0.0):.0f}%")
            cols[4].metric("Total trades", int(summary.get("total_trades") or 0))
            st.caption(universe_result.get("note") or "")

            table_rows = []
            for item in universe_result.get("results") or []:
                metrics = item.get("metrics") or {}
                table_rows.append(
                    {
                        "Symbol": item.get("symbol"),
                        "Trades": int(safe_float(metrics.get("trade_count"), 0) or 0),
                        "Net P/L": safe_float(metrics.get("net_pnl"), 0.0) or 0.0,
                        "Return %": safe_float(metrics.get("return_pct"), 0.0) or 0.0,
                        "Win rate %": safe_float(metrics.get("win_rate_pct"), 0.0) or 0.0,
                        "Profit factor": metrics.get("profit_factor"),
                        "Max drawdown %": safe_float(metrics.get("max_drawdown_pct"), 0.0) or 0.0,
                        "Catalyst filter": bool(item.get("historical_catalyst_filter_applied")),
                    }
                )
            if table_rows:
                st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)
            for warning in universe_result.get("warnings") or []:
                st.warning(str(warning))


elif module == "Validation":
    st.markdown("## Validation History")
    st.caption(
        "Saved research runs are kept separate from strategy approval. A validated label requires "
        "positive validation and untouched holdout behavior plus the robustness gate; it never auto-enables trading."
    )
    runs = list(library.get("validation_runs") or [])
    if not runs:
        st.info("No validation runs have been saved yet. Run a strategy in Strategy Lab first.")
    else:
        rows = []
        for run in runs:
            robustness = run.get("robustness") or {}
            holdout = run.get("holdout_metrics") or {}
            walk = run.get("walk_forward_summary") or {}
            rows.append(
                {
                    "Date": run.get("generated_at"),
                    "Stock": run.get("symbol"),
                    "Strategy": run.get("strategy_name"),
                    "Status": run.get("validation_status"),
                    "Robustness": robustness.get("score"),
                    "Grade": robustness.get("label"),
                    "Holdout trades": int(safe_float(holdout.get("trade_count"), 0) or 0),
                    "Holdout P/L": safe_float(holdout.get("net_pnl"), 0.0) or 0.0,
                    "Walk-forward profitable folds %": walk.get("profitable_fold_pct"),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(
            "Validation history is evidence tracking, not a leaderboard. Large historical P/L with weak "
            "holdout or walk-forward behavior should rank below a smaller but more stable result."
        )


elif module == "Catalyst Intelligence":
    st.markdown("## Catalyst Intelligence")
    st.caption(
        "Inspect the same timestamped historical news taxonomy used by catalyst-aware backtests. "
        "Generic articles are kept visible but do not receive a catalyst score."
    )

    cat_cols = st.columns([1.1, 1.0, 2.0])
    catalyst_ticker = cat_cols[0].text_input(
        "Catalyst ticker",
        value=str(st.session_state.get("til_catalyst_ticker") or "SDOT"),
        max_chars=10,
    ).strip().upper()
    catalyst_days = int(cat_cols[1].slider("History", 7, 180, 30, 1, key="til_catalyst_days"))
    cat_cols[2].caption(
        "The classifier is deliberately conservative. It identifies event categories from headline/summary "
        "keywords; it does not claim the event caused the subsequent price move."
    )

    load_catalysts = st.button(
        "📰 Load + classify historical catalysts",
        type="primary",
        use_container_width=True,
        disabled=not catalyst_ticker,
    )
    if load_catalysts:
        try:
            st.session_state["til_catalyst_ticker"] = catalyst_ticker
            market = market_client()
            cat_end = utc_now()
            cat_start = cat_end - timedelta(days=catalyst_days)
            status_box = st.status(f"Loading {catalyst_ticker} historical news…", expanded=True)
            raw_articles = historical_news(
                market,
                [catalyst_ticker],
                start=cat_start,
                end=cat_end,
                max_pages=60,
                progress=lambda page: status_box.write(f"Historical news page {page}…"),
            )
            classified = [classify_catalyst(item) for item in raw_articles]
            classified.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
            st.session_state["til_catalyst_result"] = {
                "symbol": catalyst_ticker,
                "days": catalyst_days,
                "articles": classified,
            }
            status_box.update(
                label=f"{len(classified)} historical news items classified",
                state="complete",
                expanded=False,
            )
            st.rerun()
        except AppError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Catalyst history failed: {exc}")

    catalyst_result = st.session_state.get("til_catalyst_result") or {}
    if catalyst_result and catalyst_result.get("symbol") == catalyst_ticker:
        classified = list(catalyst_result.get("articles") or [])
        specific = [item for item in classified if item.get("is_specific_catalyst")]
        positive = [item for item in specific if item.get("is_positive")]
        negative = [item for item in specific if item.get("is_negative")]

        st.divider()
        summary_cols = st.columns(4)
        summary_cols[0].metric("News items", len(classified))
        summary_cols[1].metric("Specific catalysts", len(specific))
        summary_cols[2].metric("Positive categories", len(positive))
        summary_cols[3].metric("Negative/risk categories", len(negative))

        filter_mode = st.radio(
            "Show",
            ["Specific catalysts", "All news", "Positive only", "Negative / risk only"],
            horizontal=True,
        )
        if filter_mode == "Specific catalysts":
            visible = specific
        elif filter_mode == "Positive only":
            visible = positive
        elif filter_mode == "Negative / risk only":
            visible = negative
        else:
            visible = classified

        table_rows = [
            {
                "Published (UTC)": item.get("published_at"),
                "Category": item.get("category"),
                "Score": safe_float(item.get("score"), 0.0) or 0.0,
                "Headline": item.get("headline"),
                "Source": item.get("source"),
            }
            for item in visible
        ]
        if table_rows:
            st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)
            inspect_labels = {
                f"{item.get('published_at') or 'Unknown time'} · {item.get('category')} · {str(item.get('headline') or '')[:70]}": item
                for item in visible
            }
            selected_article = inspect_labels[st.selectbox("Inspect catalyst", list(inspect_labels))]
            st.markdown(f"### {selected_article.get('category')}")
            st.write(selected_article.get("headline") or "No headline")
            if selected_article.get("summary"):
                st.write(selected_article.get("summary"))
            st.caption(
                f"Published: {selected_article.get('published_at') or '—'} · "
                f"Source: {selected_article.get('source') or '—'} · "
                f"Classifier score: {safe_float(selected_article.get('score'), 0.0):+.1f}"
            )
            if selected_article.get("keywords"):
                st.write("Matched terms: " + ", ".join(str(x) for x in selected_article.get("keywords") or []))
        else:
            st.info("No articles match this catalyst filter in the selected period.")


elif module == "Market Discovery":
    st.markdown("## Market Discovery")
    st.caption(
        "Use current Alpaca market data as a sensor, then apply a saved strategy's actual rules "
        "to the candidates. Validation status and live setup matching remain separate."
    )

    validated_strategies = [
        item for item in strategies
        if str(item.get("validation_status") or "").lower() == "validated"
    ]
    include_research = st.checkbox(
        "Include unvalidated research strategies",
        value=not bool(validated_strategies),
        help="Unvalidated strategies can be explored here but should not be treated as proven live edges.",
    )
    discovery_strategies = strategies if include_research else validated_strategies

    if not discovery_strategies:
        st.info("No validated strategies are available yet. Validate a strategy or include research strategies.")
    else:
        strategy_choices = {}
        for item in discovery_strategies:
            status = str(item.get("validation_status") or "unvalidated").replace("_", " ").title()
            label = f"{item.get('name') or 'Unnamed strategy'} · {status}"
            if label in strategy_choices:
                label += f" · {str(item.get('id') or '')[:7]}"
            strategy_choices[label] = item
        selected_discovery_strategy = strategy_choices[
            st.selectbox("Strategy to scan for", list(strategy_choices))
        ]

        scan_cols = st.columns([1.1, 1.0, 2.0])
        universe_mode = scan_cols[0].selectbox(
            "Market universe",
            ["Top gainers", "Most active", "Custom watchlist"],
        )
        candidate_count = int(scan_cols[1].slider("Candidates", 5, 30, 15, 5))
        custom_symbols = scan_cols[2].text_input(
            "Custom tickers",
            placeholder="SDOT LUCY REAX",
            disabled=universe_mode != "Custom watchlist",
        )

        scan_now = st.button("🔎 Scan current market", type="primary", use_container_width=True)
        if scan_now:
            try:
                market = market_client()
                status_box = st.status("Building live candidate universe…", expanded=True)
                if universe_mode == "Top gainers":
                    symbols = market.movers(top=candidate_count)
                elif universe_mode == "Most active":
                    symbols = market.most_active(top=candidate_count)
                else:
                    symbols = [
                        token.strip().upper()
                        for token in custom_symbols.replace(",", " ").split()
                        if token.strip()
                    ][:candidate_count]
                if not symbols:
                    raise AppError("No valid symbols were available for this scan.")

                status_box.write(f"Applying {selected_discovery_strategy.get('name')} to {len(symbols)} candidates…")
                results = scan_strategy_universe(
                    market,
                    symbols,
                    selected_discovery_strategy,
                    progress=lambda message: status_box.write(message),
                )
                st.session_state["til_market_discovery_result"] = {
                    "strategy_id": selected_discovery_strategy.get("id"),
                    "strategy_name": selected_discovery_strategy.get("name"),
                    "validation_status": selected_discovery_strategy.get("validation_status"),
                    "universe_mode": universe_mode,
                    "results": results,
                }
                status_box.update(label=f"Scan complete · {len(results)} stocks evaluated", state="complete", expanded=False)
                st.rerun()
            except AppError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Market scan failed: {exc}")

        discovery_result = st.session_state.get("til_market_discovery_result") or {}
        live_results = list(discovery_result.get("results") or [])
        if live_results:
            st.divider()
            st.markdown(
                f"### Current matches · {discovery_result.get('strategy_name') or 'Strategy'}"
            )
            if str(discovery_result.get("validation_status") or "").lower() != "validated":
                st.warning(
                    "This is an unvalidated research strategy. A live rule match is not evidence "
                    "that the trade has positive expected value."
                )

            table_rows = []
            for item in live_results:
                metrics = item.get("metrics") or {}
                table_rows.append(
                    {
                        "Symbol": item.get("symbol"),
                        "Setup": item.get("status"),
                        "Rule match %": safe_float(item.get("score"), 0.0) or 0.0,
                        "Price": safe_float(metrics.get("price")),
                        "Day move %": safe_float(metrics.get("day_change_pct")),
                        "RVOL": safe_float(metrics.get("relative_volume")),
                        "Spread %": safe_float(metrics.get("spread_pct")),
                        "Catalyst": item.get("has_catalyst"),
                        "Needs verification": int(safe_float(item.get("unknown"), 0) or 0),
                    }
                )
            st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

            matches = [item for item in live_results if str(item.get("status")).upper() == "MATCH"]
            watches = [
                item for item in live_results
                if str(item.get("status")).upper() in {"WATCH", "VERIFY"}
            ]
            summary_cols = st.columns(3)
            summary_cols[0].metric("Full rule matches", len(matches))
            summary_cols[1].metric("Watch / verify", len(watches))
            summary_cols[2].metric("Stocks evaluated", len(live_results))

            inspect_labels = {
                f"{item.get('symbol')} · {item.get('status')} · {safe_float(item.get('score'), 0.0):.0f}%": item
                for item in live_results
            }
            inspected = inspect_labels[st.selectbox("Inspect candidate", list(inspect_labels))]
            metrics = inspected.get("metrics") or {}
            signal = inspected.get("signal") or {}
            detail_cols = st.columns(4)
            detail_cols[0].metric("Price", f"${safe_float(metrics.get('price'), 0.0):,.4f}")
            detail_cols[1].metric("Day move", f"{safe_float(metrics.get('day_change_pct'), 0.0):+.2f}%")
            rvol = safe_float(metrics.get("relative_volume"))
            detail_cols[2].metric("Relative volume", f"{rvol:.2f}×" if rvol is not None else "—")
            detail_cols[3].metric("Rule match", f"{safe_float(signal.get('score'), 0.0):.0f}%")

            checks = signal.get("checks") or []
            if checks:
                with st.expander("Why this stock matched or failed", expanded=True):
                    for check in checks:
                        state = str(check.get("status") or "").upper()
                        icon = "✅" if state == "PASS" else "❓" if state == "UNKNOWN" else "❌"
                        st.write(
                            f"{icon} **{check.get('label') or 'Rule'}** — "
                            f"current: {check.get('actual')} · required: {check.get('required')}"
                        )


elif module == "Stock Analyzer":
    st.markdown("## Stock Analyzer")
    st.caption(
        "Compare one stock against the strategy library using shared live market data, then inspect "
        "which validated setup currently fits best."
    )

    if not strategies:
        st.info("No strategies are available yet.")
    else:
        analyzer_cols = st.columns([1.2, 1.0, 2.0])
        analyzer_ticker = analyzer_cols[0].text_input(
            "Ticker to analyze",
            value=str(st.session_state.get("til_analyzer_ticker") or "SDOT"),
            max_chars=10,
        ).strip().upper()
        validated_only = analyzer_cols[1].checkbox(
            "Validated only",
            value=True,
            help="Turn this off to compare research-only strategies too.",
        )
        analyzer_cols[2].caption(
            "The analyzer ranks setup fit separately from historical robustness. "
            "A 100% rule match is not the same thing as a 100% chance of profit."
        )

        analyzer_strategies = [
            item for item in strategies
            if not validated_only
            or str(item.get("validation_status") or "").lower() == "validated"
        ]
        if validated_only and not analyzer_strategies:
            st.info("No validated strategies are available yet. Turn off Validated only to explore research strategies.")

        analyze_stock = st.button(
            "🧭 Analyze stock against strategies",
            type="primary",
            use_container_width=True,
            disabled=not analyzer_ticker or not analyzer_strategies,
        )
        if analyze_stock:
            try:
                st.session_state["til_analyzer_ticker"] = analyzer_ticker
                status_box = st.status(f"Analyzing {analyzer_ticker}…", expanded=True)
                analysis = analyze_stock_strategies(
                    market_client(),
                    analyzer_ticker,
                    analyzer_strategies,
                    progress=lambda message: status_box.write(message),
                )
                st.session_state["til_stock_analysis"] = analysis
                status_box.update(label=f"{analyzer_ticker} analysis complete", state="complete", expanded=False)
                st.rerun()
            except AppError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Stock analysis failed: {exc}")

        stock_result = st.session_state.get("til_stock_analysis") or {}
        if stock_result and stock_result.get("symbol") == analyzer_ticker:
            metrics = stock_result.get("metrics") or {}
            comparisons = list(stock_result.get("comparisons") or [])
            st.divider()
            st.markdown(f"### {analyzer_ticker} strategy-fit report")

            market_cols = st.columns(5)
            market_cols[0].metric("Price", f"${safe_float(metrics.get('price'), 0.0):,.4f}")
            market_cols[1].metric("Day move", f"{safe_float(metrics.get('day_change_pct'), 0.0):+.2f}%")
            rvol = safe_float(metrics.get("relative_volume"))
            market_cols[2].metric("RVOL", f"{rvol:.2f}×" if rvol is not None else "—")
            market_cols[3].metric("Spread", f"{safe_float(metrics.get('spread_pct'), 0.0):.2f}%")
            market_cols[4].metric("Recent catalyst items", int(stock_result.get("news_count") or 0))

            if comparisons:
                best = comparisons[0]
                best_validation = str(best.get("validation_status") or "unvalidated").replace("_", " ").title()
                st.markdown(
                    f"**Best current strategy fit:** {best.get('strategy_name')} · "
                    f"{best.get('status')} · {safe_float(best.get('score'), 0.0):.0f}% rule match · "
                    f"{best_validation}"
                )

                comparison_rows = []
                for item in comparisons:
                    comparison_rows.append(
                        {
                            "Strategy": item.get("strategy_name"),
                            "Validation": item.get("validation_status"),
                            "Robustness": item.get("robustness_score"),
                            "Current setup": item.get("status"),
                            "Rule match %": safe_float(item.get("score"), 0.0) or 0.0,
                            "Needs verification": int(safe_float(item.get("unknown"), 0) or 0),
                            "Source": item.get("source_title") or item.get("source_type"),
                        }
                    )
                st.dataframe(pd.DataFrame(comparison_rows), use_container_width=True, hide_index=True)

                inspect_options = {
                    f"{item.get('strategy_name')} · {item.get('status')} · {safe_float(item.get('score'), 0.0):.0f}%": item
                    for item in comparisons
                }
                chosen = inspect_options[st.selectbox("Inspect strategy fit", list(inspect_options))]
                signal = chosen.get("signal") or {}
                if chosen.get("robustness_score") is not None:
                    st.caption(
                        f"Historical robustness score: {safe_float(chosen.get('robustness_score'), 0.0):.1f}/100. "
                        "This is a historical validation score, not a probability forecast."
                    )
                checks = signal.get("checks") or []
                if checks:
                    for check in checks:
                        state = str(check.get("status") or "").upper()
                        icon = "✅" if state == "PASS" else "❓" if state == "UNKNOWN" else "❌"
                        st.write(
                            f"{icon} **{check.get('label') or 'Rule'}** — "
                            f"current: {check.get('actual')} · required: {check.get('required')}"
                        )


elif module == "Live / Paper":
    st.markdown("## Live / Paper Strategy Runner")
    st.caption(
        "Research and validation remain separate from execution. Existing safety checks and Alpaca "
        "paper-trading controls stay in place."
    )
    if st.button("Open existing Live Strategy Runner", use_container_width=True):
        st.switch_page("pages/Live_Strategy_Runner.py")
