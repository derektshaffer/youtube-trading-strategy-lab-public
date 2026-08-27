"""Trading Intelligence Lab — unified trading research platform."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from live_strategy_runner_page import setting
from trading_intelligence_core import (
    GeminiBookAnalyzer,
    canonicalize_existing_strategy,
    extract_source_text,
    merge_strategies,
)
from youtube_strategy_engine import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GITHUB_BACKUP_PATH,
    AppError,
    GitHubCloudBackup,
    StrategyStore,
    normalize_machine_rules,
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


def build_intelligence_store() -> StrategyStore:
    repository = setting("GITHUB_BACKUP_REPOSITORY")
    token = setting("GITHUB_BACKUP_TOKEN")
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
    repository = setting("GITHUB_BACKUP_REPOSITORY")
    token = setting("GITHUB_BACKUP_TOKEN")
    cloud = None
    if repository and token:
        cloud = GitHubCloudBackup(
            repository,
            token,
            branch=setting("GITHUB_BACKUP_BRANCH"),
            path=setting("GITHUB_BACKUP_PATH", DEFAULT_GITHUB_BACKUP_PATH),
        )
    return StrategyStore(cloud_backup=cloud)


@st.cache_resource
def intelligence_store() -> StrategyStore:
    return build_intelligence_store()


def load_library() -> dict[str, Any]:
    data = intelligence_store().load()
    data.setdefault("knowledge_sources", [])
    data.setdefault("strategies", [])
    data.setdefault("research_runs", [])
    data.setdefault("validation_runs", [])
    return data


def source_label(strategy: dict[str, Any]) -> str:
    kind = str(strategy.get("source_type") or "legacy").replace("_", " ").title()
    return f"{kind} · {strategy.get('source_title') or 'Unknown source'}"


with st.sidebar:
    st.markdown("### Research workspace")
    module = st.radio(
        "Section",
        [
            "Overview",
            "Knowledge Sources",
            "Strategy Library",
            "Strategy Lab",
            "Validation",
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

    st.markdown("### What is usable in this first build")
    st.success(
        "Book/PDF ingestion, AI strategy extraction, a canonical strategy format, a separate "
        "persistent intelligence library, and import of existing YouTube-lab strategies are wired up."
    )
    st.info(
        "The Strategy Lab, validation engine, scanner/analyzer integration, and regime/catalyst "
        "engines are represented in the architecture and will be connected incrementally rather "
        "than by rewriting the working tools all at once."
    )


elif module == "Knowledge Sources":
    st.markdown("## Knowledge Sources")
    st.caption(
        "Upload a source you have lawful access to. The AI extracts trading hypotheses and short "
        "evidence references; it does not reproduce the book or treat the author's claims as validated."
    )

    uploaded = st.file_uploader(
        "Book or research document",
        type=["pdf", "txt", "md", "markdown"],
        help="PDF, TXT, and Markdown are supported in the first version.",
    )
    a, b = st.columns(2)
    title = a.text_input("Title", placeholder="How to Day Trade")
    author = b.text_input("Author / creator", placeholder="Ross Cameron")
    focus = st.text_area(
        "Optional research focus",
        placeholder="Example: catalyst momentum, first pullbacks, VWAP, relative volume, entries and exits",
        height=90,
    )

    can_analyze = uploaded is not None and bool(title.strip())
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
            analyzer = GeminiBookAnalyzer(
                setting("GEMINI_API_KEY"),
                setting("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
            )
            progress = st.progress(0.0, text="Preparing source…")
            def on_progress(index: int, total: int) -> None:
                progress.progress(
                    min(1.0, max(0.0, (index - 1) / max(1, total))),
                    text=f"Analyzing source section {index} of {total}…",
                )

            analysis = analyzer.analyze(
                text,
                title=title.strip(),
                author=author.strip(),
                focus=focus,
                progress_callback=on_progress,
            )
            progress.progress(1.0, text="Analysis complete")

            data = load_library()
            source_record = {k: v for k, v in analysis.items() if k != "strategies"}
            source_record["filename"] = uploaded.name
            source_record["extraction_metadata"] = metadata
            data["knowledge_sources"] = [
                item for item in data.get("knowledge_sources") or []
                if item.get("id") != source_record["id"]
            ]
            data["knowledge_sources"].insert(0, source_record)
            data["strategies"] = merge_strategies(
                list(data.get("strategies") or []),
                list(analysis.get("strategies") or []),
            )
            intelligence_store().save(data)
            st.session_state["til_last_analysis"] = analysis
            st.success(
                f"Extracted {len(analysis.get('strategies') or [])} strategy "
                f"hypotheses from {title.strip()}."
            )
            st.rerun()
        except AppError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Source analysis failed: {exc}")

    if sources:
        st.markdown("### Saved sources")
        for src in sources:
            with st.expander(
                f"{src.get('title') or 'Untitled'}"
                + (f" — {src.get('author')}" if src.get("author") else "")
            ):
                st.write(src.get("summary") or "No source-level summary saved.")
                st.caption(
                    f"Type: {src.get('source_type', 'document')} · "
                    f"AI sections: {src.get('chunk_count', '—')} · "
                    f"Analyzed: {src.get('analyzed_at', '—')}"
                )
    else:
        st.info("No book or document sources have been analyzed in the new library yet.")


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
            rows.append(
                {
                    "Strategy": s.get("name"),
                    "Category": s.get("category"),
                    "Direction": s.get("direction"),
                    "Source": source_label(s),
                    "Measurable rules": sum(v is not None for v in rules.values()),
                    "Extraction confidence": s.get("confidence"),
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
            x1, x2, x3 = st.columns(3)
            x1.metric("Extraction confidence", f"{float(selected.get('confidence') or 0):.0f}%")
            x2.metric("Validation", selected.get("validation_status") or "unvalidated")
            x3.metric("Optimization", selected.get("optimization_status") or "not_run")
            st.markdown("#### Machine-testable rules")
            active_rules = {
                key: value for key, value in normalize_machine_rules(selected.get("machine_rules")).items()
                if value is not None
            }
            st.json(active_rules or {"status": "No objective thresholds extracted yet."})
            if selected.get("unresolved_rules"):
                st.markdown("#### Requires interpretation / unavailable data")
                for item in selected.get("unresolved_rules") or []:
                    st.write("• " + str(item))


elif module == "Strategy Lab":
    st.markdown("## Strategy Lab")
    st.info(
        "This module will reuse and expand the existing YouTube backtester/optimizer. "
        "The new canonical strategy records are already structured so they can feed that engine."
    )
    st.markdown(
        """
        **Next build target:** multi-strategy testing, ticker/universe testing, parameter search,
        historical-analog groups, catalyst/regime filters, and stability scoring rather than
        ranking only by maximum historical profit.
        """
    )
    if st.button("Open existing Full Trading Lab", use_container_width=True):
        st.switch_page("pages/Full_Trading_Lab.py")


elif module == "Validation":
    st.markdown("## Validation")
    st.info(
        "Planned validation gate: training → validation → untouched holdout → walk-forward windows. "
        "Strategies will need minimum trade counts, acceptable drawdown, stability across windows, "
        "and positive unseen-data performance before receiving a validated label."
    )


elif module == "Market Discovery":
    st.markdown("## Market Discovery")
    st.info(
        "This is where the existing momentum scanner will plug in as a market sensor. "
        "The larger platform will then rank which validated strategy families fit each candidate."
    )


elif module == "Stock Analyzer":
    st.markdown("## Stock Analyzer")
    st.info(
        "This module will absorb the strongest parts of the single-stock analyzer: current price/volume "
        "context, VWAP, catalysts, historical analogs, support/resistance, ML setup scoring, and "
        "strategy-fit comparison."
    )


elif module == "Live / Paper":
    st.markdown("## Live / Paper Strategy Runner")
    st.caption(
        "Research and validation remain separate from execution. Existing safety checks and Alpaca "
        "paper-trading controls stay in place."
    )
    if st.button("Open existing Live Strategy Runner", use_container_width=True):
        st.switch_page("pages/Live_Strategy_Runner.py")
