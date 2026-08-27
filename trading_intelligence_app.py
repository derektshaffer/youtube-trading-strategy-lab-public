"""Trading Intelligence Lab — unified trading research platform."""

from __future__ import annotations

import os
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
from trading_intelligence_core import (
    GeminiBookAnalyzer,
    GeminiRuleCompiler,
    canonicalize_existing_strategy,
    effective_strategy_for_live,
    effective_strategy_for_research,
    extract_source_text,
    merge_strategies,
    prepare_strategies_with_ai,
    research_readiness,
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
            "Rule Compiler",
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
        "Book/PDF ingestion, AI strategy extraction, automatic AI rule preparation, the historical "
        "Strategy Lab, validation, catalyst intelligence, universe research, market discovery, and "
        "the unified strategy library are all connected."
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

    can_analyze = uploaded is not None
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
                fallback_api_key=setting("GEMINI_PAID_API_KEY", ""),
            )
            progress = st.progress(0.0, text="Preparing source…")
            def on_progress(index: int, total: int, message: str | None = None) -> None:
                progress.progress(
                    min(0.98, max(0.0, (index - 1) / max(1, total))),
                    text=message or f"Analyzing source section {index} of {total}…",
                )

            analysis = analyzer.analyze(
                text,
                title=title.strip(),
                author=author.strip(),
                focus=focus,
                progress_callback=on_progress,
            )
            completion_text = "Strategy extraction complete"
            if analysis.get("model_fallback_used"):
                completion_text += f" · backup model used: {analysis.get('model')}"
            if analysis.get("paid_fallback_used"):
                completion_text += " · backup API key used"
            progress.progress(1.0, text=completion_text)

            if autopilot_prepare and analysis.get("strategies"):
                prep_status = st.status(
                    "AI Autopilot is translating strategies into testable research rules…",
                    expanded=True,
                )
                compiler = GeminiRuleCompiler(
                    setting("GEMINI_API_KEY"),
                    setting("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
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
            st.success(message)
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
                auto = src.get("autopilot_summary") or {}
                if auto.get("enabled"):
                    st.caption(
                        "AI Autopilot: "
                        f"{int(auto.get('strategies_prepared') or 0)} strategies prepared · "
                        f"{int(auto.get('research_assumptions_added') or 0)} assumptions added · "
                        f"{int(auto.get('ready_for_backtest') or 0)} ready for backtesting"
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
            readiness = s.get("research_readiness") or research_readiness(s)
            rows.append(
                {
                    "Strategy": s.get("name"),
                    "Category": s.get("category"),
                    "Direction": s.get("direction"),
                    "Source": source_label(s),
                    "Measurable rules": sum(v is not None for v in rules.values()),
                    "AI readiness": str(readiness.get("label") or "unknown").replace("_", " ").title(),
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
            readiness = selected.get("research_readiness") or research_readiness(selected)
            x1, x2, x3, x4 = st.columns(4)
            x1.metric("Extraction confidence", f"{float(selected.get('confidence') or 0):.0f}%")
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
