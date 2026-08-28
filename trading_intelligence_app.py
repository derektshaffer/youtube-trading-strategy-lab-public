"""Trading Intelligence Lab — unified trading research platform."""

from __future__ import annotations

import hashlib
import html
import os
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from trading_glass_theme import inject_research_glass_theme
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
    build_canonical_family_strategies,
    build_concept_graph,
    build_strategy_families,
    compile_candidate_blueprint,
    infer_strategy_dna,
    is_family_source_strategy,
    is_synthetic_strategy,
    source_identity,
)
from trading_intelligence_core import (
    DEFAULT_GEMINI_BOOK_MODEL,
    DEFAULT_GEMINI_BOOK_SPECIALIST_MODEL,
    GeminiBookAnalyzer,
    GeminiRuleCompiler,
    apply_compiler_suggestions,
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

    /* Research Workspace navigation: one continuous radio keeps existing
       routing/session-state behavior, while visual section headers make the
       workflow readable as an ordered process. */
    .st-key-til_workspace_section div[role="radiogroup"] {
        gap: .12rem !important;
    }
    .st-key-til_workspace_section div[role="radiogroup"] > label {
        position: relative !important;
        min-height: 2.15rem !important;
        padding-top: .08rem !important;
        padding-bottom: .08rem !important;
    }
    .st-key-til_workspace_section div[role="radiogroup"] > label p {
        font-weight: 650 !important;
        letter-spacing: -.01em !important;
    }

    /* Non-clickable workflow section labels. */
    .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(1),
    .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(4),
    .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(9),
    .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(13) {
        margin-top: 1.8rem !important;
    }
    .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(1)::before,
    .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(4)::before,
    .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(9)::before,
    .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(13)::before {
        position: absolute !important;
        left: 0 !important;
        top: -1.38rem !important;
        color: #7f93ad !important;
        font-size: .68rem !important;
        font-weight: 850 !important;
        letter-spacing: .12em !important;
        line-height: 1 !important;
        pointer-events: none !important;
    }
    .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(1)::before {
        content: "RESEARCH";
    }
    .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(4)::before {
        content: "STRATEGY DEVELOPMENT";
    }
    .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(9)::before {
        content: "MARKET RESEARCH";
    }
    .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(13)::before {
        content: "EXECUTION";
    }
    </style>
    <div class="til-hero">
      <div class="til-kicker"><span class="til-brand-mark">◈</span> Research Workspace</div>
      <div class="til-title">Trading Intelligence Lab</div>
      <div class="til-sub">
        Learn trading ideas from books, videos, and existing research; convert them into explicit
        rules; test them against historical markets; validate them on unseen data; and match
        validated strategies to current market conditions.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Apply the scanner-inspired glass theme after the local hero/sidebar CSS so
# this shared design layer wins the final cascade without changing page logic.
inject_research_glass_theme()


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

    # Automatically pull newly analyzed YouTube strategies from the original Trading Lab.
    # The user should not have to remember to import them before the family manager can use them.
    legacy_changed = False
    try:
        legacy_data = build_legacy_store().load()
        existing_ids = {
            str(item.get("id") or "")
            for item in data.get("strategies") or []
            if isinstance(item, dict) and item.get("id")
        }
        legacy_additions = [
            canonicalize_existing_strategy(item)
            for item in legacy_data.get("strategies") or []
            if isinstance(item, dict)
            and str(item.get("id") or "") not in existing_ids
        ]
        if legacy_additions:
            data["strategies"] = merge_strategies(
                list(data.get("strategies") or []),
                legacy_additions,
            )
            legacy_changed = True
    except AppError:
        # The unified library remains usable even if the older YouTube library is temporarily unavailable.
        pass

    upgraded_strategies: list[dict[str, Any]] = []
    for raw in data.get("strategies") or []:
        if not isinstance(raw, dict):
            continue
        upgraded = upgrade_native_strategy_rules(raw)
        upgraded["research_readiness"] = research_readiness(upgraded)
        upgraded_strategies.append(upgraded)
    data["strategies"] = upgraded_strategies

    # Older Trading Lab versions saved source provenance only on strategy records.
    data, sources_changed = reconcile_knowledge_sources(data)

    existing_canonical = [
        dict(item)
        for item in data.get("strategies") or []
        if isinstance(item, dict)
        and str(item.get("source_type") or "").lower() == "canonical_family"
    ]
    source_and_other = [
        dict(item)
        for item in data.get("strategies") or []
        if isinstance(item, dict)
        and str(item.get("source_type") or "").lower() != "canonical_family"
    ]

    canonical_families, _ = build_canonical_family_strategies(
        source_and_other,
        existing=existing_canonical,
    )
    canonical_families = [
        upgrade_native_strategy_rules(item)
        for item in canonical_families
    ]
    for item in canonical_families:
        item["research_readiness"] = research_readiness(item)

    canonical_changed = canonical_families != existing_canonical
    data["strategies"] = [*source_and_other, *canonical_families]

    if legacy_changed or sources_changed or canonical_changed:
        try:
            store.save(data)
        except AppError:
            # Keep the repaired/consolidated library visible for this session. Storage health
            # surfaces any cloud-write problem and the migration retries on the next load.
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


RULE_FRIENDLY_LABELS = {
    "min_price": "Minimum stock price",
    "max_price": "Maximum stock price",
    "min_day_change_pct": "Current-day price move",
    "min_relative_volume": "Current relative volume",
    "min_dollar_volume": "Minimum dollar volume",
    "previous_day_high_breakout": "Previous-day high breakout",
    "min_previous_day_volume_ratio": "Previous-day volume",
    "min_previous_day_change_pct": "Previous-day price move",
    "max_spread_pct": "Maximum bid/ask spread",
    "above_vwap": "Price above VWAP",
    "vwap_reclaim": "VWAP reclaim",
    "max_vwap_distance_pct": "Maximum distance from VWAP",
    "fast_ema_period": "Fast EMA period",
    "slow_ema_period": "Secondary EMA period",
    "trend_ema_period": "Long-term EMA period",
    "require_price_above_fast_ema": "Price above fast EMA",
    "require_price_above_slow_ema": "Price above secondary EMA",
    "require_price_above_trend_ema": "Price above long-term EMA",
    "require_fast_ema_rising": "Fast EMA rising",
    "require_fast_ema_pullback": "Pullback to fast EMA",
    "max_fast_ema_distance_pct": "Maximum distance from fast EMA",
    "pullback_touch_tolerance_pct": "EMA pullback tolerance",
    "max_pullback_number": "Latest allowed pullback number",
    "require_pullback_breakout": "Pullback breakout confirmation",
    "stop_below_fast_ema": "Stop below fast EMA",
    "stop_ema_buffer_pct": "EMA stop buffer",
    "breakout_lookback_bars": "Breakout lookback",
    "opening_range_minutes": "Opening-range breakout",
    "volume_surge_ratio": "Volume surge",
    "minimum_green_bars": "Consecutive green candles",
    "catalyst_required": "News catalyst",
    "stop_loss_pct": "Stop loss",
    "reward_risk": "Reward-to-risk target",
    "latest_entry_time": "Latest entry time",
}


def friendly_rule_label(rule_name: str) -> str:
    key = str(rule_name or "")
    return RULE_FRIENDLY_LABELS.get(
        key,
        key.replace("_", " ").strip().title() or "Trading rule",
    )


def _friendly_number(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return str(value)
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def friendly_rule_text(rule_name: str, value: Any) -> str:
    key = str(rule_name or "")
    number = _friendly_number(value)
    enabled = bool(value)

    templates = {
        "min_price": f"Only test stocks priced at or above **USD {number}**.",
        "max_price": f"Only test stocks priced at or below **USD {number}**.",
        "min_day_change_pct": f"Require the current session to be up at least **{number}%**.",
        "min_relative_volume": f"Require current relative volume of at least **{number}× normal**.",
        "min_dollar_volume": f"Require at least **USD {number}** of dollar volume.",
        "min_previous_day_volume_ratio": (
            f"Require the previous trading day's volume to be at least **{number}× its recent average**."
        ),
        "min_previous_day_change_pct": (
            f"Require the previous trading day to have gained at least **{number}%**."
        ),
        "max_spread_pct": f"Reject stocks with a bid/ask spread wider than **{number}%**.",
        "max_vwap_distance_pct": f"Keep price within **{number}% of VWAP**.",
        "fast_ema_period": f"Use a **{number}-period EMA** as the fast/pullback average.",
        "slow_ema_period": f"Use a **{number}-period EMA** as the secondary trend average.",
        "trend_ema_period": f"Use a **{number}-period EMA** as the long-term trend average.",
        "max_fast_ema_distance_pct": f"Keep price within **{number}% of the fast EMA**.",
        "pullback_touch_tolerance_pct": f"Count a pullback as touching/near the fast EMA within **{number}%**.",
        "max_pullback_number": f"Only allow the first **{number} EMA pullback(s)** in the sequence.",
        "stop_ema_buffer_pct": f"Place the structural stop **{number}% below the fast EMA**.",
        "breakout_lookback_bars": f"Require a breakout above the prior **{number} candles**.",
        "opening_range_minutes": f"Use the first **{number} minutes** to define the opening range.",
        "volume_surge_ratio": f"Require a volume surge of at least **{number}× normal**.",
        "minimum_green_bars": f"Require at least **{number} consecutive green candles**.",
        "stop_loss_pct": f"Use a research stop about **{number}% below entry**.",
        "reward_risk": f"Target about **{number}× the amount risked**.",
        "latest_entry_time": f"Do not open a new trade after **{value}**.",
    }
    if key in templates:
        return templates[key]

    boolean_templates = {
        "previous_day_high_breakout": (
            "Enter only when price **crosses above the previous trading day's high**."
        ),
        "above_vwap": "Require price to be **above VWAP**.",
        "vwap_reclaim": "Require price to **reclaim VWAP from below**.",
        "require_price_above_fast_ema": "Require price to be **above the fast EMA**.",
        "require_price_above_slow_ema": "Require price to be **above the secondary EMA**.",
        "require_price_above_trend_ema": "Require price to be **above the long-term EMA**.",
        "require_fast_ema_rising": "Require the **fast EMA to be rising**.",
        "require_fast_ema_pullback": "Require a **recent pullback to the fast EMA**.",
        "require_pullback_breakout": "Require **breakout confirmation after the pullback**.",
        "stop_below_fast_ema": "Use a **structural stop below the fast EMA**.",
        "catalyst_required": "Require a **qualifying news catalyst**.",
    }
    if key in boolean_templates:
        if enabled:
            return boolean_templates[key]
        return f"{friendly_rule_label(key)} is **not required**."

    return f"{friendly_rule_label(key)}: **{value}**."


def render_plain_rules(
    rules: dict[str, Any],
    *,
    assumption: bool = False,
) -> None:
    for rule_name, value in rules.items():
        st.markdown(f"**{friendly_rule_label(rule_name)}**")
        st.markdown(friendly_rule_text(rule_name, value))
        if assumption:
            st.caption(
                "AI-added research assumption — this value is for historical testing and was not "
                "necessarily specified by the source author."
            )


def source_label(strategy: dict[str, Any]) -> str:
    kind = str(strategy.get("source_type") or "legacy").replace("_", " ").title()
    return f"{kind} · {strategy.get('source_title') or 'Unknown source'}"


def _source_page_count(source: dict[str, Any]) -> int:
    metadata = source.get("extraction_metadata") or {}
    for value in (
        metadata.get("pages"),
        source.get("pages"),
        source.get("page_count"),
    ):
        try:
            if value is not None:
                return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def _source_is_complete(source: dict[str, Any]) -> bool:
    if bool(source.get("analysis_incomplete")) or bool(source.get("analysis_in_progress")):
        return False
    stage = str(source.get("analysis_stage") or "complete").strip().casefold()
    return stage in {"complete", "completed"} or bool(source.get("recovered_from_strategies"))


def _source_file_badge(source: dict[str, Any]) -> tuple[str, str]:
    source_type = str(source.get("source_type") or "document").strip().casefold()
    filename = str(source.get("filename") or "")
    suffix = Path(filename).suffix.strip(".").upper()
    if source_type == "youtube":
        return "▶", "VIDEO"
    if suffix:
        return "▤", suffix[:6]
    if source_type in {"book_or_document", "book"}:
        return "▤", "DOC"
    return "◇", "SOURCE"


def render_recent_source_cards(source_items: list[dict[str, Any]]) -> None:
    if not source_items:
        return
    recent = sorted(
        source_items,
        key=lambda item: str(item.get("analyzed_at") or item.get("checkpointed_at") or ""),
        reverse=True,
    )[:3]
    cards: list[str] = []
    for source in recent:
        icon, kind = _source_file_badge(source)
        title = html.escape(str(source.get("title") or source.get("filename") or "Untitled source"))
        author = html.escape(str(source.get("author") or "Unknown creator"))
        pages = _source_page_count(source)
        meta = f"{pages:,} pages · {kind}" if pages else kind
        status = "PROCESSED" if _source_is_complete(source) else "IN PROGRESS"
        status_class = "ready" if _source_is_complete(source) else "working"
        cards.append(
            '<div class="til-source-card">'
            f'<div class="til-source-fileicon">{icon}</div>'
            '<div class="til-source-main">'
            f'<div class="til-source-title">{title}</div>'
            f'<div class="til-source-author">{author}</div>'
            f'<div class="til-source-meta">{meta}</div>'
            '</div>'
            f'<div class="til-source-status {status_class}">◆ {status}</div>'
            '</div>'
        )
    st.markdown(
        '<div class="til-recent-source-grid">' + "".join(cards) + '</div>',
        unsafe_allow_html=True,
    )


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


# Internal page IDs stay unchanged so existing buttons, deep links, saved
# session state, and page logic continue to work. Only the visible navigation
# order/names are changed.
WORKSPACE_SECTIONS = [
    "Overview",
    "Knowledge Sources",
    "AI Research Autopilot",
    "Strategy Library",
    "Strategy DNA",
    "Make Strategy Testable",
    "Strategy Lab",
    "Validation",
    "Universe Research",
    "Market Discovery",
    "Catalyst Intelligence",
    "Stock Analyzer",
    "Live / Paper",
]

WORKSPACE_DISPLAY_LABELS = {
    "Overview": "1. Overview",
    "Knowledge Sources": "2. Knowledge Sources",
    "AI Research Autopilot": "3. AI Research Autopilot",
    "Strategy Library": "4. Strategy Library",
    "Strategy DNA": "5. Strategy Blueprint",
    "Make Strategy Testable": "6. Rule Builder",
    "Strategy Lab": "7. Strategy Lab",
    "Validation": "8. Validation",
    "Universe Research": "9. Market Universe",
    "Market Discovery": "10. Market Discovery",
    "Catalyst Intelligence": "11. Catalyst Intelligence",
    "Stock Analyzer": "12. Stock Analyzer",
    "Live / Paper": "13. Paper & Live Trading",
}
WORKSPACE_DISPLAY_TO_INTERNAL = {
    label: internal
    for internal, label in WORKSPACE_DISPLAY_LABELS.items()
}

WORKSPACE_PAGE_META = {
    "Overview": {
        "step": "01",
        "group": "Research",
        "title": "Overview",
        "subtitle": "See what the research system knows, what has been validated, and where the workflow should go next.",
    },
    "Knowledge Sources": {
        "step": "02",
        "group": "Research",
        "title": "Knowledge Sources",
        "subtitle": "Bring books, PDFs, videos, notes, and research into one durable evidence library.",
    },
    "AI Research Autopilot": {
        "step": "03",
        "group": "Research",
        "title": "AI Research Autopilot",
        "subtitle": "Let AI consolidate source ideas, prepare testable hypotheses, and move promising families into research.",
    },
    "Strategy Library": {
        "step": "04",
        "group": "Strategy Development",
        "title": "Strategy Library",
        "subtitle": "Review the organized strategy families that AI has extracted and consolidated from your sources.",
    },
    "Strategy DNA": {
        "step": "05",
        "group": "Strategy Development",
        "title": "Strategy Blueprint",
        "subtitle": "See the structural components shared across strategies and where source authors meaningfully disagree.",
    },
    "Make Strategy Testable": {
        "step": "06",
        "group": "Strategy Development",
        "title": "Rule Builder",
        "subtitle": "Translate vague trading language into measurable research hypotheses without rewriting source-authored rules.",
    },
    "Strategy Lab": {
        "step": "07",
        "group": "Strategy Development",
        "title": "Strategy Lab",
        "subtitle": "Optimize candidate rules, test unseen data, and measure whether an apparent edge survives serious historical research.",
    },
    "Validation": {
        "step": "08",
        "group": "Strategy Development",
        "title": "Validation",
        "subtitle": "Review frozen validation results, robustness scores, holdouts, and walk-forward evidence.",
    },
    "Universe Research": {
        "step": "09",
        "group": "Market Research",
        "title": "Market Universe",
        "subtitle": "Test whether a strategy generalizes across stocks instead of only fitting one ticker.",
    },
    "Market Discovery": {
        "step": "10",
        "group": "Market Research",
        "title": "Market Discovery",
        "subtitle": "Search the current market for stocks that match the conditions of validated or research-ready strategies.",
    },
    "Catalyst Intelligence": {
        "step": "11",
        "group": "Market Research",
        "title": "Catalyst Intelligence",
        "subtitle": "Add point-in-time news and catalyst context so momentum setups are evaluated with the reason behind the move.",
    },
    "Stock Analyzer": {
        "step": "12",
        "group": "Market Research",
        "title": "Stock Analyzer",
        "subtitle": "Deep-dive one ticker with strategy matches, market structure, catalysts, and current setup quality.",
    },
    "Live / Paper": {
        "step": "13",
        "group": "Execution",
        "title": "Paper & Live Trading",
        "subtitle": "Deploy validated rules into paper or live workflows while keeping research and execution clearly separated.",
    },
}

WORKSPACE_NAV_GROUPS = [
    ("RESEARCH", ["Overview", "Knowledge Sources", "AI Research Autopilot"]),
    (
        "STRATEGY DEVELOPMENT",
        ["Strategy Library", "Strategy DNA", "Make Strategy Testable", "Strategy Lab", "Validation"],
    ),
    (
        "MARKET RESEARCH",
        ["Universe Research", "Market Discovery", "Catalyst Intelligence", "Stock Analyzer"],
    ),
    ("EXECUTION", ["Live / Paper"]),
]

WORKSPACE_NAV_ICONS = {
    "Overview": "⌁",
    "Knowledge Sources": "◇",
    "AI Research Autopilot": "✦",
    "Strategy Library": "▣",
    "Strategy DNA": "⌘",
    "Make Strategy Testable": "⚙",
    "Strategy Lab": "⌬",
    "Validation": "✓",
    "Universe Research": "◎",
    "Market Discovery": "⌖",
    "Catalyst Intelligence": "⚡",
    "Stock Analyzer": "⌕",
    "Live / Paper": "↗",
}


def _nav_key(section: str) -> str:
    return "til_nav_" + "".join(
        char.lower() if char.isalnum() else "_"
        for char in section
    ).strip("_")


def render_workspace_page_header(section: str) -> None:
    meta = WORKSPACE_PAGE_META.get(section) or {
        "step": "—",
        "group": "Workspace",
        "title": section,
        "subtitle": "",
    }
    st.markdown(
        (
            '<div class="til-pagehead">'
            '<div class="til-pagehead-main">'
            f'<div class="til-page-eyebrow">STEP {meta["step"]} <span>•</span> {meta["group"].upper()}</div>'
            f'<div class="til-page-title">{meta["title"]}</div>'
            f'<div class="til-page-sub">{meta["subtitle"]}</div>'
            '</div>'
            '<div class="til-market-mesh" aria-hidden="true">'
            '<svg viewBox="0 0 620 150" preserveAspectRatio="none">'
            '<defs>'
            '<linearGradient id="meshLine" x1="0" y1="0" x2="1" y2="0">'
            '<stop offset="0%" stop-color="#45d7ff" stop-opacity="0"/>'
            '<stop offset="48%" stop-color="#45d7ff" stop-opacity=".85"/>'
            '<stop offset="100%" stop-color="#43e087" stop-opacity=".55"/>'
            '</linearGradient>'
            '<linearGradient id="meshFill" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0%" stop-color="#36d5ff" stop-opacity=".14"/>'
            '<stop offset="100%" stop-color="#32de84" stop-opacity="0"/>'
            '</linearGradient>'
            '</defs>'
            '<path d="M0 111 L74 106 L132 91 L193 105 L260 65 L330 91 L395 45 L463 72 L522 37 L620 62 L620 150 L0 150 Z" fill="url(#meshFill)"/>'
            '<polyline points="0,111 74,106 132,91 193,105 260,65 330,91 395,45 463,72 522,37 620,62" fill="none" stroke="url(#meshLine)" stroke-width="1.5"/>'
            '<polyline points="35,126 105,116 169,121 232,88 302,108 370,71 438,95 501,59 572,76" fill="none" stroke="#45d7ff" stroke-opacity=".28" stroke-width="1"/>'
            '<g fill="#6de7ff">'
            '<circle cx="132" cy="91" r="2.2"/><circle cx="260" cy="65" r="2.5"/><circle cx="395" cy="45" r="2.7"/><circle cx="522" cy="37" r="2.4"/>'
            '</g>'
            '<g stroke="#42dca0" stroke-opacity=".22" stroke-width=".8">'
            '<line x1="132" y1="91" x2="232" y2="88"/><line x1="232" y1="88" x2="330" y2="91"/>'
            '<line x1="330" y1="91" x2="395" y2="45"/><line x1="395" y1="45" x2="501" y2="59"/>'
            '<line x1="260" y1="65" x2="370" y2="71"/><line x1="370" y1="71" x2="463" y2="72"/>'
            '</g>'
            '</svg>'
            '</div>'
            f'<div class="til-page-step">{meta["step"]}</div>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )


requested_workspace = st.session_state.pop("til_navigate_to", None)
requested_workspace = WORKSPACE_DISPLAY_TO_INTERNAL.get(
    requested_workspace,
    requested_workspace,
)
if requested_workspace in WORKSPACE_SECTIONS:
    st.session_state["til_workspace_section"] = requested_workspace

module = str(st.session_state.get("til_workspace_section") or "Overview")
if module not in WORKSPACE_SECTIONS:
    module = "Overview"
    st.session_state["til_workspace_section"] = module

with st.sidebar:
    st.markdown(
        """
        <div class="til-sidebrand">
          <div class="til-sidebrand-mark">
            <span class="til-logo-core"></span>
            <span class="til-logo-orbit til-logo-orbit-a"></span>
            <span class="til-logo-orbit til-logo-orbit-b"></span>
          </div>
          <div>
            <div class="til-sidebrand-name">Trading Intelligence</div>
            <div class="til-sidebrand-sub">Research Workspace</div>
          </div>
        </div>
        <div class="til-sideflow"><span>◈</span> 13-step research workflow</div>
        """,
        unsafe_allow_html=True,
    )

    for group_name, group_sections in WORKSPACE_NAV_GROUPS:
        st.markdown(
            f'<div class="til-nav-group">{group_name}</div>',
            unsafe_allow_html=True,
        )
        for section in group_sections:
            display = WORKSPACE_DISPLAY_LABELS.get(section, section)
            number, _, label = display.partition(". ")
            icon = WORKSPACE_NAV_ICONS.get(section, "◇")
            is_active = section == module
            clicked = st.button(
                f"{icon}   {number.zfill(2)}   {label}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
                key=_nav_key(section),
            )
            if clicked and not is_active:
                st.session_state["til_workspace_section"] = section
                st.rerun()

    st.markdown(
        """
        <div class="til-side-status">
          <div class="til-side-status-row">
            <div>
              <div class="til-side-status-label">RESEARCH SYSTEM</div>
              <div class="til-side-status-value"><span class="til-live-dot"></span> READY</div>
            </div>
            <svg class="til-mini-spark" viewBox="0 0 90 28" aria-hidden="true">
              <polyline points="1,22 15,18 28,21 40,12 53,16 67,8 89,11" fill="none" stroke="#47dda0" stroke-width="2"/>
              <polyline points="1,26 15,24 28,25 40,19 53,21 67,15 89,16" fill="none" stroke="#4bcfff" stroke-opacity=".35" stroke-width="1"/>
            </svg>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Other labs", expanded=False):
        st.page_link("youtube_strategy_app.py", label="Home")
        st.page_link("pages/Full_Trading_Lab.py", label="Full Trading Lab")
        st.page_link("pages/Live_Strategy_Runner.py", label="Live Strategy Runner")
        st.page_link("pages/Machine_Learning_Lab.py", label="Machine Learning Lab")

try:
    library = load_library()
except AppError as exc:
    st.error(str(exc))
    st.stop()

strategies = list(library.get("strategies") or [])
sources = list(library.get("knowledge_sources") or [])
source_strategies = [item for item in strategies if is_family_source_strategy(item)]
canonical_strategies = [
    item
    for item in strategies
    if str(item.get("source_type") or "").lower() == "canonical_family"
]
managed_strategies = canonical_strategies or source_strategies

render_workspace_page_header(module)


if module == "Overview":
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Knowledge sources", len(sources))
    c2.metric("AI strategy families", len(canonical_strategies))
    c3.metric(
        "Validated families",
        sum(
            1
            for s in canonical_strategies
            if str(s.get("validation_status") or "").lower() == "validated"
        ),
    )
    c4.metric("Raw source ideas", len(source_strategies))

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
        "The normal workflow is AI-managed: upload books/videos, keep every extracted idea for provenance, "
        "automatically consolidate similar ideas into strategy families, let historical research optimize the "
        "rule variations, and surface only the families that need your attention or survive validation."
    )


elif module == "Knowledge Sources":
    total_pages = sum(_source_page_count(item) for item in sources)
    completed_sources = sum(1 for item in sources if _source_is_complete(item))
    source_processing_pct = (
        int(round(100.0 * completed_sources / len(sources)))
        if sources
        else 0
    )
    validated_family_count = sum(
        1
        for item in canonical_strategies
        if str(item.get("validation_status") or "").lower() == "validated"
    )

    st.markdown(
        (
            '<div class="til-kpi-grid">'
            '<div class="til-kpi til-kpi-cyan">'
            '<div class="til-kpi-icon">▤</div>'
            '<div><div class="til-kpi-label">TOTAL SOURCES</div>'
            f'<div class="til-kpi-value">{len(sources):,}</div>'
            '<div class="til-kpi-note">durable evidence library</div></div></div>'
            '<div class="til-kpi til-kpi-blue">'
            '<div class="til-kpi-icon">≋</div>'
            '<div><div class="til-kpi-label">PAGES PROCESSED</div>'
            f'<div class="til-kpi-value">{total_pages:,}</div>'
            '<div class="til-kpi-note">from saved documents</div></div></div>'
            '<div class="til-kpi til-kpi-green">'
            '<div class="til-kpi-icon">✦</div>'
            '<div><div class="til-kpi-label">EXTRACTED IDEAS</div>'
            f'<div class="til-kpi-value">{len(source_strategies):,}</div>'
            '<div class="til-kpi-note">research hypotheses retained</div></div></div>'
            '<div class="til-kpi til-kpi-purple">'
            '<div class="til-kpi-icon">◇</div>'
            '<div><div class="til-kpi-label">VALIDATED FAMILIES</div>'
            f'<div class="til-kpi-value">{validated_family_count:,}</div>'
            '<div class="til-kpi-note">passed current validation gate</div></div></div>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )

    storage = persistence_summary()
    if storage.get("healthy"):
        repository = html.escape(str(storage.get("repository") or "GitHub"))
        backup_path = html.escape(str(storage.get("path") or "intelligence library"))
        st.markdown(
            (
                '<div class="til-sync-banner">'
                '<div class="til-sync-icon">i</div>'
                '<div class="til-sync-copy">'
                f'<div class="til-sync-title">Permanent library storage verified: <strong>{repository}</strong> · {backup_path}</div>'
                '<div class="til-sync-sub">All source progress is checkpointed safely as the AI works.</div>'
                '</div>'
                '<div class="til-sync-badge"><span></span> SYNCED</div>'
                '<div class="til-github-mark">⌘</div>'
                '</div>'
            ),
            unsafe_allow_html=True,
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

    main_col, rail_col = st.columns([4.45, 1.18], gap="large")
    with main_col:
        st.markdown(
            """
            <div class="til-panel-heading">
              <div>
                <div class="til-panel-title">Upload a new source</div>
                <div class="til-panel-sub">Books, PDFs, research notes, and text sources become evidence-grounded strategy hypotheses.</div>
              </div>
              <div class="til-panel-chip">AI INGESTION</div>
            </div>
            """,
            unsafe_allow_html=True,
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
        analyze_slot = st.empty()
        analyze = analyze_slot.button(
            "🧠 Analyze source and extract strategies",
            type="primary",
            use_container_width=True,
            disabled=not can_analyze,
            key="til_analyze_source",
        )

        if analyze and uploaded is not None:
            analyze_slot.button(
                "🧠 Analyzing…",
                type="primary",
                use_container_width=True,
                disabled=True,
                key="til_analyze_source_busy",
            )
            task_monitor = long_task_monitor("knowledge_source_analysis")
            task_bar = st.progress(
                0.01,
                text=task_monitor.text(0.01, "Preparing source…"),
            )
            try:
                payload = uploaded.getvalue()
                text, metadata = extract_source_text(uploaded.name, payload)
                update_task_bar(task_bar, task_monitor, 0.03, "Readable source text extracted")
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
                update_task_bar(
                    task_bar,
                    task_monitor,
                    0.06,
                    "File ready"
                    + page_note
                    + " · preparing AI reading batches",
                )

                def on_progress(index: int, total: int, message: str | None = None) -> None:
                    local_fraction = (index - 1) / max(1, total)
                    update_task_bar(
                        task_bar,
                        task_monitor,
                        0.08 + 0.48 * min(1.0, max(0.0, local_fraction)),
                        message or f"Analyzing source section {index} of {total}…",
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
                update_task_bar(task_bar, task_monitor, 0.58, completion_text)
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
                        message = f"Preparing {index} of {total}: {strategy_name}"
                        prep_status.write(message)
                        update_task_bar(
                            task_bar,
                            task_monitor,
                            0.58 + 0.14 * min(1.0, index / max(1, total)),
                            message,
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
                    update_task_bar(
                        task_bar,
                        task_monitor,
                        0.72,
                        "AI rule preparation complete",
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
                # Rebuild the canonical family layer now that this source is saved. New ideas join
                # existing blueprints automatically instead of becoming another manual research queue.
                data = load_library()

                autonomous_report = None
                autonomous_error = ""
                if (
                    autopilot_research
                    and autopilot_prepare
                    and analysis.get("strategies")
                    and not analysis.get("analysis_incomplete")
                ):
                    new_source_strategy_ids = {
                        str(item.get("id") or "")
                        for item in analysis.get("strategies") or []
                        if isinstance(item, dict) and item.get("id")
                    }
                    affected_families = [
                        item
                        for item in data.get("strategies") or []
                        if str(item.get("source_type") or "").lower() == "canonical_family"
                        and new_source_strategy_ids.intersection(
                            {str(value) for value in item.get("source_strategy_ids") or []}
                        )
                    ]

                    # Canonical families can still contain qualitative gaps. Let the same compiler
                    # prepare those automatically before historical research, then persist the prepared
                    # family record so the user never has to resolve each source variation manually.
                    prepared_families = []
                    for family_item in affected_families:
                        prepared_family = dict(family_item)
                        if (
                            (prepared_family.get("research_readiness") or research_readiness(prepared_family)).get("label")
                            != "ready_for_backtest"
                        ):
                            prepared_family = prepare_strategies_with_ai(
                                [prepared_family],
                                compiler,
                                minimum_confidence=65.0,
                            )[0]
                        prepared_family["research_readiness"] = research_readiness(prepared_family)
                        prepared_families.append(prepared_family)
                        data = upsert_strategy_record(data, prepared_family)

                    if prepared_families:
                        intelligence_store().save(data)

                    ready_for_deep = [
                        item
                        for item in prepared_families
                        if (item.get("research_readiness") or {}).get("label") == "ready_for_backtest"
                    ]
                    if ready_for_deep:
                        auto_status = st.status(
                            "Historical Research Autopilot is building its own stock universe…",
                            expanded=True,
                        )
                        nested_estimator = AutonomousResearchProgressEstimator()

                        def on_nested_research(message: str) -> None:
                            auto_status.write(message)
                            nested_fraction = nested_estimator.update(message)
                            update_task_bar(
                                task_bar,
                                task_monitor,
                                0.72 + 0.25 * nested_fraction,
                                message,
                            )

                        try:
                            autonomous_report = run_autonomous_research(
                                market_client(),
                                ready_for_deep,
                                progress=on_nested_research,
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
                            "The newly affected strategy families still do not have enough machine-testable "
                            "entry/filter rules for deep historical research. Their source ideas and family "
                            "membership were saved, and AI can revisit them as more sources are added."
                        )
                        analysis["autonomous_research_summary"] = {
                            "completed": False,
                            "error": autonomous_error,
                        }

                update_task_bar(task_bar, task_monitor, 0.985, "Saving final source and strategy records")
                save_ingestion_checkpoint(
                    analysis,
                    filename=uploaded.name,
                    extraction_metadata=metadata,
                    ingest_id=ingest_id,
                    stage="partial" if analysis.get("analysis_incomplete") else "complete",
                )
                complete_task_bar(
                    task_bar,
                    task_monitor,
                    "Source analysis complete" if not analysis.get("analysis_incomplete") else "Partial source analysis saved",
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
                        f" The AI family manager consolidated the new ideas into the strategy library, then "
                        f"deep-tested {int(autonomous_report.get('deep_strategies_tested') or 0)} affected family finalist(s); "
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


    with rail_col:
        st.markdown(
            (
                '<div class="til-rail-card til-coverage-card">'
                '<div class="til-rail-title">Your Evidence Library</div>'
                f'<div class="til-gauge" style="--coverage:{source_processing_pct * 3.6}deg">'
                '<div class="til-gauge-inner">'
                f'<div class="til-gauge-value">{source_processing_pct}%</div>'
                '<div class="til-gauge-label">SOURCE<br>PROCESSING</div>'
                '</div></div>'
                f'<div class="til-coverage-state">{"Strong foundation" if source_processing_pct >= 70 else "Building foundation"}</div>'
                f'<div class="til-coverage-copy">{completed_sources} of {len(sources)} saved sources are fully processed.</div>'
                '<svg class="til-rail-spark" viewBox="0 0 160 34" aria-hidden="true">'
                '<polyline points="2,27 24,24 46,26 67,21 91,23 113,16 137,18 158,7" fill="none" stroke="#45dfa0" stroke-width="2"/>'
                '<g fill="#64ddff"><circle cx="24" cy="24" r="2"/><circle cx="91" cy="23" r="2"/><circle cx="158" cy="7" r="2"/></g>'
                '</svg>'
                '</div>'
                '<div class="til-rail-card til-quality-card">'
                '<div class="til-rail-title">Source Quality Guide</div>'
                '<div class="til-quality-row high"><span class="til-quality-icon">◇</span><div><strong>High Quality</strong><small>Books, academic papers, primary research</small></div></div>'
                '<div class="til-quality-row medium"><span class="til-quality-icon">⊙</span><div><strong>Useful Context</strong><small>Videos, interviews, experienced practitioners</small></div></div>'
                '<div class="til-quality-row low"><span class="til-quality-icon">△</span><div><strong>Needs Verification</strong><small>Unverified claims, social posts, opinions</small></div></div>'
                '</div>'
                '<div class="til-rail-card til-system-card">'
                '<div class="til-rail-title">Research Pipeline</div>'
                '<div class="til-pipeline-row"><span>01</span><b>Extract</b><em>source ideas</em></div>'
                '<div class="til-pipeline-row"><span>02</span><b>Consolidate</b><em>strategy families</em></div>'
                '<div class="til-pipeline-row"><span>03</span><b>Test</b><em>historical robustness</em></div>'
                '<div class="til-pipeline-row"><span>04</span><b>Validate</b><em>unseen data</em></div>'
                '</div>'
            ),
            unsafe_allow_html=True,
        )

    if sources:
        st.markdown(
            '<div class="til-section-row"><div><div class="til-section-kicker">RECENT SOURCES</div>'
            '<div class="til-section-title">Evidence added to the library</div></div>'
            '<div class="til-section-line"></div></div>',
            unsafe_allow_html=True,
        )
        render_recent_source_cards(sources)
        st.markdown("### Saved source details")
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
    st.info(
        "**You do not need to test these one-by-one.** The Lab keeps every strategy extracted from your "
        "books and videos in the background, groups strategies that share the same underlying blueprint, "
        "and treats rule differences as research variations for the optimizer to test."
    )

    if not canonical_strategies:
        st.info(
            "No strategy families have been built yet. Analyze or import a trading source and the Lab "
            "will create them automatically."
        )
    else:
        validated_families = [
            item
            for item in canonical_strategies
            if str(item.get("validation_status") or "").lower() == "validated"
        ]
        review_families = [
            item
            for item in canonical_strategies
            if not bool(item.get("backtest_supported", True))
        ]
        consolidated_away = max(0, len(source_strategies) - len(canonical_strategies))

        metrics = st.columns(5)
        metrics[0].metric("Raw source ideas", len(source_strategies))
        metrics[1].metric("Consolidated families", len(canonical_strategies))
        metrics[2].metric("Duplicates/variants absorbed", consolidated_away)
        metrics[3].metric("Validated families", len(validated_families))
        metrics[4].metric("Needs your review", len(review_families))

        action_cols = st.columns([2.2, 1.8])
        if action_cols[0].button(
            "🤖 Research all strategy families automatically",
            type="primary",
            use_container_width=True,
            key="til_library_run_ai_manager",
        ):
            st.session_state["til_navigate_to"] = "AI Research Autopilot"
            st.rerun()
        if action_cols[1].button(
            "🔎 Find stocks matching validated families",
            use_container_width=True,
            disabled=not bool(validated_families),
            key="til_library_find_validated",
        ):
            st.session_state["til_market_discovery_include_research"] = False
            st.session_state["til_navigate_to"] = "Market Discovery"
            st.rerun()

        st.caption(
            "The AI research manager favors rule sets that stay useful across unseen periods, multiple stocks, "
            "walk-forward tests, and cost stress—not simply the combination with the biggest historical profit."
        )

        st.markdown("### Strategy families")
        family_search = st.text_input(
            "Search families",
            placeholder="VWAP, pullback, breakout, momentum…",
            label_visibility="collapsed",
            key="til_family_search",
        ).strip().casefold()
        visible_families = [
            item
            for item in canonical_strategies
            if not family_search
            or family_search in str(item.get("name") or "").casefold()
            or family_search in " ".join(item.get("supporting_sources") or []).casefold()
            or family_search in str(item.get("summary") or "").casefold()
        ]

        if not visible_families:
            st.warning("No strategy family matches that search.")
        else:
            family_options = {}
            for item in visible_families:
                raw_count = int(item.get("raw_strategy_count") or 0)
                source_count = int(item.get("supporting_source_count") or 0)
                status = (
                    "Validated"
                    if str(item.get("validation_status") or "").lower() == "validated"
                    else "Researching / unvalidated"
                )
                label = (
                    f"{item.get('name') or 'Strategy family'} · "
                    f"{raw_count} source variation{'s' if raw_count != 1 else ''} · "
                    f"{source_count} source{'s' if source_count != 1 else ''} · {status}"
                )
                family_options[label] = item

            selected_label = st.selectbox(
                "Inspect a strategy family",
                list(family_options),
                key="til_strategy_family_choice",
            )
            family = family_options[selected_label]
            readiness = family.get("research_readiness") or research_readiness(family)
            family_status = str(family.get("validation_status") or "unvalidated").lower()
            rule_options = family.get("candidate_rule_options") or {}
            research = family.get("last_autonomous_research") or {}

            st.markdown(f"## {family.get('name') or 'Strategy family'}")
            st.write(
                f"The AI consolidated **{int(family.get('raw_strategy_count') or 0)} extracted strategy "
                f"variation(s)** from **{int(family.get('supporting_source_count') or 0)} independent source(s)** "
                "into this one research family."
            )

            family_cols = st.columns(4)
            family_cols[0].metric(
                "Backtester ready",
                "Yes" if (readiness.get("label") == "ready_for_backtest") else "AI still preparing",
            )
            family_cols[1].metric(
                "Validation",
                "Passed" if family_status == "validated" else "Not passed yet",
            )
            family_cols[2].metric(
                "Rule variations being tested",
                sum(len(values) for values in rule_options.values() if isinstance(values, list)),
            )
            family_cols[3].metric(
                "Independent sources",
                int(family.get("supporting_source_count") or 0),
            )

            core = family.get("family_core_concepts") or []
            if core:
                st.markdown("### Shared blueprint")
                grouped_core: dict[str, list[str]] = {}
                for item in core:
                    dimension = str(item.get("dimension_label") or item.get("dimension") or "Other")
                    grouped_core.setdefault(dimension, []).append(str(item.get("concept") or ""))
                for dimension, concepts in grouped_core.items():
                    clean = [item for item in concepts if item]
                    if clean:
                        st.write(f"**{dimension}:** " + " · ".join(clean))

            if family_status == "validated":
                st.success(
                    "This family passed the current validation gates. Its frozen validated rules are used "
                    "instead of silently changing them when new source material arrives."
                )
                validated_rules = {
                    key: value
                    for key, value in normalize_machine_rules(family.get("validated_rules")).items()
                    if value is not None
                }
                if validated_rules:
                    with st.expander("Validated trading rules — plain English", expanded=True):
                        render_plain_rules(validated_rules)
            elif research:
                score = safe_float(research.get("global_score"))
                status_text = str(research.get("validation_status") or "research only").replace("_", " ")
                st.warning(
                    "AI has researched this family, but it has not passed every validation gate yet. "
                    + (f"Current global research score: **{score:.1f}/100** · " if score is not None else "")
                    + f"status: **{status_text}**."
                )
            else:
                st.info(
                    "This family is waiting for autonomous historical research. Use the AI research button above; "
                    "you do not need to pick a ticker or test its source variations manually."
                )

            if rule_options:
                with st.expander("What rule differences the AI is resolving", expanded=False):
                    st.caption(
                        "These are variations found across related strategies. The optimizer tests them as "
                        "parameter choices instead of creating a separate strategy for every small difference."
                    )
                    for rule_name, values in rule_options.items():
                        readable = [
                            "not required" if value is None else str(value)
                            for value in values
                        ]
                        st.write(
                            f"**{friendly_rule_label(rule_name)}:** "
                            + " · ".join(readable)
                        )

            member_ids = set(str(value) for value in family.get("source_strategy_ids") or [])
            members = [
                item for item in source_strategies
                if str(item.get("id") or "") in member_ids
            ]
            with st.expander(
                f"Source variations kept in the background ({len(members)})",
                expanded=False,
            ):
                st.caption(
                    "Nothing was deleted. These records preserve exactly what each book/video taught, "
                    "but you do not have to manage them individually."
                )
                for member in members:
                    st.write(
                        f"• **{member.get('name') or 'Unnamed strategy'}** — "
                        f"{member.get('source_title') or 'Unknown source'}"
                    )

            if not bool(family.get("backtest_supported", True)):
                st.error(
                    "This family needs your attention because the current deterministic backtester does not "
                    "support its direction/mechanism well enough for automatic validation yet."
                )

            with st.expander("Advanced manual controls", expanded=False):
                st.caption(
                    "You normally should not need these. They are here for inspecting or manually testing "
                    "a family when you specifically want to."
                )
                advanced = st.columns(2)
                if advanced[0].button(
                    "Open this family in Strategy Lab",
                    use_container_width=True,
                    key="til_family_manual_lab",
                ):
                    st.session_state["til_selected_strategy_id"] = str(family.get("id") or "")
                    st.session_state["til_navigate_to"] = "Strategy Lab"
                    st.rerun()
                if advanced[1].button(
                    "Inspect / improve testable rules",
                    use_container_width=True,
                    key="til_family_manual_compiler",
                ):
                    st.session_state["til_selected_strategy_id"] = str(family.get("id") or "")
                    st.session_state["til_navigate_to"] = "Make Strategy Testable"
                    st.rerun()


elif module == "Strategy DNA":
    st.caption(
        "Break every extracted strategy into reusable components, measure where independent sources "
        "agree, cluster related setups, and generate research-only cross-source candidates. "
        "Source agreement and historical validation are deliberately shown as separate kinds of evidence."
    )

    st.info(
        "Advanced audit view: the AI family manager uses this DNA automatically. You do not need to "
        "manually cluster or consolidate strategies here unless you want to inspect how the grouping works."
    )
    if not source_strategies:
        st.info("Add book, document, or YouTube strategies before building the Strategy DNA map.")
    else:
        dna_strategies = []
        for item in source_strategies:
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
                synth_run_slot = action_cols[1].empty()
                run_candidate = synth_run_slot.button(
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
                    synth_run_slot.button(
                        "🧪 Researching…",
                        type="primary",
                        use_container_width=True,
                        disabled=True,
                        key=f"run_synth_busy_{candidate.get('id')}",
                    )
                    synth_monitor = long_task_monitor("synthesized_strategy_research")
                    synth_bar = st.progress(
                        0.01,
                        text=synth_monitor.text(0.01, "Preparing synthesized strategy…"),
                    )
                    try:
                        candidate_to_run = dict(executable)
                        update_task_bar(synth_bar, synth_monitor, 0.05, "Checking research readiness")
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
                            update_task_bar(
                                synth_bar,
                                synth_monitor,
                                0.12,
                                "AI Rule Compiler is translating remaining qualitative rules",
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
                        update_task_bar(
                            synth_bar,
                            synth_monitor,
                            0.28,
                            "Research candidate saved · starting historical research",
                        )

                        research_status = st.status(
                            "Running the synthesized candidate through Historical Research Autopilot…",
                            expanded=True,
                        )
                        synth_nested = AutonomousResearchProgressEstimator()

                        def on_synth_research(message: str) -> None:
                            research_status.write(message)
                            nested_fraction = synth_nested.update(message)
                            update_task_bar(
                                synth_bar,
                                synth_monitor,
                                0.28 + 0.68 * nested_fraction,
                                message,
                            )

                        report = run_autonomous_research(
                            market_client(),
                            [candidate_to_run],
                            deep_strategy_limit=1,
                            progress=on_synth_research,
                        )
                        data = load_library()
                        data = merge_autonomous_research_into_library(data, report)
                        intelligence_store().save(data)
                        st.session_state["til_synth_research_result"] = {
                            "candidate_id": candidate_to_run.get("id"),
                            "report": report,
                        }
                        complete_task_bar(
                            synth_bar,
                            synth_monitor,
                            "Historical research pipeline complete",
                        )

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


elif module == "Make Strategy Testable":
    st.info(
        "**What this page does:** trading books often use phrases like “very active,” “strong chart,” "
        "or “good news.” A backtester cannot test those phrases directly. This page lets AI translate "
        "the measurable parts into concrete research rules so they can be tested historically."
    )
    st.caption(
        "You normally do not need to do this yourself — AI Autopilot already tries to handle it during "
        "book ingestion. This page is mainly for seeing what the AI understood and what assumptions it added."
    )
    st.markdown(
        "**Source idea → measurable test rule → historical backtest → validation decides whether it survives.**"
    )

    if not managed_strategies:
        st.info("Add or import a strategy before asking AI to make it testable.")
    else:
        compiler_choices = {}
        for item in managed_strategies:
            label = f"{item.get('name') or 'Unnamed strategy'} · {source_label(item)}"
            if label in compiler_choices:
                label += f" · {str(item.get('id') or '')[:7]}"
            compiler_choices[label] = item
        target_id = str(st.session_state.pop("til_selected_strategy_id", "") or "")
        if target_id:
            for label, item in compiler_choices.items():
                if str(item.get("id") or "") == target_id:
                    st.session_state["til_compiler_strategy"] = label
                    break
        compiler_strategy = compiler_choices[
            st.selectbox(
                "Strategy to make testable",
                list(compiler_choices),
                key="til_compiler_strategy",
                help="Choose the strategy whose vague or discretionary requirements you want AI to translate for research.",
            )
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
        compiler_cols[0].metric("Rules from the source", len(explicit))
        compiler_cols[1].metric(
            "AI assumptions queued",
            len(compiler_strategy.get("ai_candidate_rule_options") or accepted_overrides),
        )
        compiler_cols[2].metric(
            "Still vague / subjective",
            len(compiler_strategy.get("unresolved_rules") or []),
        )

        if explicit:
            with st.expander("Rules the source actually specified", expanded=False):
                render_plain_rules(explicit)
                st.caption(
                    "These rules came from the source and are protected from AI assumption edits."
                )
        if compiler_strategy.get("unresolved_rules"):
            with st.expander("Still needs interpretation", expanded=True):
                st.caption(
                    "These ideas are still too subjective or depend on data the backtester cannot measure directly."
                )
                for rule in compiler_strategy.get("unresolved_rules") or []:
                    st.write("• " + str(rule))
        if accepted_overrides:
            with st.expander("AI-added test assumptions", expanded=True):
                render_plain_rules(accepted_overrides, assumption=True)
                st.warning(
                    "These are testing assumptions, not claims about what the author explicitly said. "
                    "Historical validation still has to determine whether they are useful."
                )

        compiler_slot = st.empty()
        compile_rules = compiler_slot.button(
            "🧩 Make remaining rules testable",
            type="primary",
            use_container_width=True,
            key="til_compile_rule_suggestions",
        )
        if compile_rules:
            compiler_slot.button(
                "🧩 Translating…",
                type="primary",
                use_container_width=True,
                disabled=True,
                key="til_compile_rule_suggestions_busy",
            )
            compiler_monitor = long_task_monitor("ai_rule_compiler")
            compiler_bar = st.progress(
                0.10,
                text=compiler_monitor.text(0.10, "Preparing strategy context…"),
            )
            try:
                with st.status("Turning vague requirements into measurable test rules…", expanded=True) as status:
                    compiler = GeminiRuleCompiler(
                        setting("GEMINI_API_KEY"),
                        setting("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
                        fallback_api_key=setting("GEMINI_PAID_API_KEY", ""),
                    )
                    update_task_bar(
                        compiler_bar,
                        compiler_monitor,
                        0.35,
                        "AI is translating subjective language into measurable test rules",
                    )
                    compiled = compiler.compile(compiler_strategy)
                    update_task_bar(
                        compiler_bar,
                        compiler_monitor,
                        0.80,
                        "AI suggestions received · queuing research hypotheses automatically",
                    )

                    # No manual assumption picking. High-confidence suggestions
                    # become research-only seeds plus nearby optimizer candidates.
                    # Explicit source rules are never overwritten.
                    prepared_strategy = apply_compiler_suggestions(
                        compiler_strategy,
                        compiled,
                        minimum_confidence=65.0,
                    )
                    data = load_library()
                    for item in data.get("strategies") or []:
                        if str(item.get("id") or "") != str(compiler_strategy.get("id") or ""):
                            continue
                        for field in (
                            "research_rule_overrides",
                            "ai_candidate_rule_options",
                            "compiler_assumptions",
                            "autopilot_preparation",
                            "research_readiness",
                            "validation_status",
                            "optimization_status",
                        ):
                            if field in prepared_strategy:
                                item[field] = prepared_strategy[field]
                        item.pop("validated_rules", None)
                        item.pop("validated_backtest_settings", None)
                        item.pop("validated_at", None)
                        break
                    intelligence_store().save(data)

                    update_task_bar(
                        compiler_bar,
                        compiler_monitor,
                        0.95,
                        "AI assumptions queued for optimizer, walk-forward, and holdout testing",
                    )
                    st.session_state["til_rule_compiler_result"] = {
                        "strategy_id": compiler_strategy.get("id"),
                        "result": compiled,
                        "auto_queued": True,
                    }
                    status.update(label="Measurable test suggestions ready", state="complete", expanded=False)
                    complete_task_bar(
                        compiler_bar,
                        compiler_monitor,
                        "Test-rule suggestions ready",
                    )
                st.rerun()
            except AppError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"AI could not finish translating the strategy: {exc}")

        stored_compiler = st.session_state.get("til_rule_compiler_result") or {}
        if stored_compiler.get("strategy_id") == compiler_strategy.get("id"):
            compiled = stored_compiler.get("result") or {}
            suggestions = list(compiled.get("suggestions") or [])
            if compiled.get("summary"):
                st.info(str(compiled.get("summary")))
            if suggestions:
                st.markdown("### AI suggestions for historical testing")
                st.caption(
                    "This is now an audit screen. You do not need to choose assumptions manually. "
                    "High-confidence AI interpretations are automatically queued as research-only hypotheses; "
                    "the optimizer tests the proposed value and nearby alternatives, then walk-forward/holdout "
                    "validation decides whether the rule survives."
                )

                # Recompute the same deterministic queue preview so older
                # in-session compiler results also display the automatic action.
                prepared_preview = apply_compiler_suggestions(
                    compiler_strategy,
                    compiled,
                    minimum_confidence=65.0,
                )
                ai_options = prepared_preview.get("ai_candidate_rule_options") or {}
                explicit_rules = normalize_machine_rules(
                    compiler_strategy.get("machine_rules")
                )
                suggestion_rows = []
                auto_queued_count = 0
                for number, suggestion in enumerate(suggestions, start=1):
                    target = str(suggestion.get("target_rule") or "")
                    confidence = safe_float(suggestion.get("confidence"), 0.0) or 0.0
                    source_protected = explicit_rules.get(target) is not None
                    test_values = list(ai_options.get(target) or [])
                    if source_protected:
                        action = "Protected source rule"
                        status_text = "AI cannot replace it"
                    elif confidence < 65.0:
                        action = "Skipped"
                        status_text = "Low confidence"
                    elif test_values:
                        action = "Auto-test"
                        status_text = "Optimizer → walk-forward → holdout"
                        auto_queued_count += 1
                    else:
                        action = "Skipped"
                        status_text = "Could not normalize safely"

                    suggestion_rows.append(
                        {
                            "#": number,
                            "What the source says": suggestion.get("source_requirement"),
                            "AI research seed": friendly_rule_text(
                                target,
                                suggestion.get("parsed_value"),
                            ).replace("**", ""),
                            "Values queued to test": " · ".join(
                                str(value) for value in test_values
                            ) if test_values else "—",
                            "Action": action,
                            "Confidence": confidence,
                            "Validation path": status_text,
                            "Why": suggestion.get("rationale"),
                        }
                    )

                st.dataframe(
                    pd.DataFrame(suggestion_rows),
                    use_container_width=True,
                    hide_index=True,
                )
                if auto_queued_count:
                    st.success(
                        f"{auto_queued_count} AI assumption rule(s) are automatically queued for historical testing. "
                        "No manual selection is required."
                    )
                else:
                    st.info(
                        "No new AI assumptions met the automatic research gate. Source-authored rules remain unchanged."
                    )
            else:
                st.info(
                    "AI could not turn the remaining subjective language into a reliable measurable rule with the data the Lab currently supports."
                )

            if compiled.get("unmapped_requirements"):
                with st.expander("Ideas AI still cannot test reliably", expanded=False):
                    for item in compiled.get("unmapped_requirements") or []:
                        st.write("• " + str(item))

        if accepted_overrides and st.button(
            "Remove all AI test assumptions",
            use_container_width=True,
        ):
            data = load_library()
            for item in data.get("strategies") or []:
                if str(item.get("id") or "") == str(compiler_strategy.get("id") or ""):
                    item["research_rule_overrides"] = {}
                    item["ai_candidate_rule_options"] = {}
                    item["compiler_assumptions"] = []
                    item["autopilot_preparation"] = {}
                    item["validation_status"] = "unvalidated"
                    item["optimization_status"] = "not_run"
                    item.pop("validated_rules", None)
                    item.pop("validated_backtest_settings", None)
                    item.pop("validated_at", None)
                    break
            intelligence_store().save(data)
            st.success(
                "AI test assumptions and their queued optimizer candidates were removed. "
                "Rules that came directly from the source were left unchanged."
            )
            st.rerun()


elif module == "AI Research Autopilot":
    st.caption(
        "This is the AI research manager. It works on consolidated strategy families—not every raw book/video "
        "variation. It prepares vague rules when possible, builds its own historical stock universe, optimizes "
        "family rule variations, runs untouched holdout and walk-forward checks, tests across multiple stocks, "
        "and saves the strongest robust rules automatically."
    )

    ready_strategies = [
        item
        for item in managed_strategies
        if (item.get("research_readiness") or research_readiness(item)).get("label") == "ready_for_backtest"
    ]
    auto_metrics = st.columns(4)
    auto_metrics[0].metric(
        "Families ready now",
        len(ready_strategies),
        delta=f"{len(managed_strategies)} total managed",
        delta_color="off",
    )
    auto_metrics[1].metric(
        "Already validated",
        sum(1 for item in managed_strategies if str(item.get("validation_status") or "") == "validated"),
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
        disabled=not managed_strategies,
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
            research_candidates = [dict(item) for item in managed_strategies]
            needs_preparation = [
                item
                for item in research_candidates
                if (item.get("research_readiness") or research_readiness(item)).get("label")
                != "ready_for_backtest"
            ]
            if needs_preparation:
                update_auto_activity(
                    f"AI is preparing {len(needs_preparation)} strategy family/families with qualitative gaps…"
                )
                compiler = GeminiRuleCompiler(
                    setting("GEMINI_API_KEY"),
                    setting("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
                    fallback_api_key=setting("GEMINI_PAID_API_KEY", ""),
                )

                def on_family_prepare(index: int, total: int, strategy_name: str) -> None:
                    update_auto_activity(
                        f"Preparing family {index}/{total}: {strategy_name}"
                    )

                research_candidates = prepare_strategies_with_ai(
                    research_candidates,
                    compiler,
                    minimum_confidence=65.0,
                    progress_callback=on_family_prepare,
                )
                prepared_library = load_library()
                for prepared_family in research_candidates:
                    prepared_family["research_readiness"] = research_readiness(prepared_family)
                    prepared_library = upsert_strategy_record(
                        prepared_library,
                        prepared_family,
                    )
                intelligence_store().save(prepared_library)

            ready_for_run = [
                item
                for item in research_candidates
                if (item.get("research_readiness") or research_readiness(item)).get("label")
                == "ready_for_backtest"
            ]
            if not ready_for_run:
                raise AppError(
                    "AI could not yet translate any strategy family into enough objective rules for "
                    "historical testing. The families remain saved and can improve as more sources are added."
                )

            update_auto_activity(
                f"{len(ready_for_run)} consolidated strategy family/families are ready for historical research…"
            )
            report = run_autonomous_research(
                market_client(),
                ready_for_run,
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
                                    "RVOL used for rank": item.get("peak_relative_volume_for_ranking", item.get("peak_relative_volume")),
                                    "RVOL regime outliers": item.get("liquidity_regime_outlier_count", 0),
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
                    outlier_total = sum(
                        int(item.get("liquidity_regime_outlier_count") or 0)
                        for item in opportunities
                    )
                    if outlier_total:
                        st.caption(
                            f"{outlier_total} extreme RVOL liquidity-regime transition event(s) were "
                            "preserved for audit but excluded from opportunity counts and anchor ranking."
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
    st.caption(
        "Choose a strategy from any source, download historical Alpaca candles, optimize only on "
        "earlier sessions, then evaluate separate validation and untouched holdout periods."
    )

    if not managed_strategies:
        st.info("Add or import at least one strategy before running the Strategy Lab.")
    else:
        strategy_labels: dict[str, dict[str, Any]] = {}
        for item in managed_strategies:
            label = f"{item.get('name') or 'Unnamed strategy'} · {source_label(item)}"
            if label in strategy_labels:
                label += f" · {str(item.get('id') or '')[:7]}"
            strategy_labels[label] = item

        target_id = str(st.session_state.pop("til_selected_strategy_id", "") or "")
        if target_id:
            for label, item in strategy_labels.items():
                if str(item.get("id") or "") == target_id:
                    st.session_state["til_strategy_lab_choice"] = label
                    break
        selected_label = st.selectbox(
            "Strategy to research",
            list(strategy_labels),
            key="til_strategy_lab_choice",
        )
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
            [effective_strategy_for_research(item) for item in managed_strategies]
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

        strategy_lab_slot = st.empty()
        run_lab = strategy_lab_slot.button(
            "🧪 Optimize + validate strategy",
            type="primary",
            use_container_width=True,
            disabled=not ticker or not split_ok or (entry_rule_count == 0 and not compare_all),
            key="til_optimize_validate_strategy",
        )

        if run_lab:
            strategy_lab_slot.button(
                "🧪 Optimizing…",
                type="primary",
                use_container_width=True,
                disabled=True,
                key="til_optimize_validate_strategy_busy",
            )
            lab_monitor = long_task_monitor("strategy_lab_optimize_validate")
            task_bar = st.progress(
                0.02,
                text=lab_monitor.text(0.02, f"Preparing {ticker} historical research…"),
            )
            try:
                market = market_client()
                end_time = utc_now()
                if market.historical_feed == "sip" and market.live_feed != "sip":
                    end_time -= timedelta(minutes=16)
                start_time = end_time - timedelta(days=int(history_days))

                update_task_bar(task_bar, lab_monitor, 0.05, f"Downloading {ticker} historical candles")
                rows_by_symbol = market.bars(
                    [ticker],
                    start=start_time,
                    end=end_time,
                    timeframe=timeframe,
                    max_pages=30,
                    progress=lambda page: update_task_bar(
                        task_bar,
                        lab_monitor,
                        0.05 + 0.20 * min(1.0, page / 30.0),
                        f"Downloading {ticker} historical candles · page {page}",
                    ),
                )
                rows = list(rows_by_symbol.get(ticker) or [])
                update_task_bar(task_bar, lab_monitor, 0.25, f"Downloaded {len(rows):,} candles")
                if not rows:
                    raise AppError(f"No historical {timeframe} candles were returned for {ticker}.")

                catalyst_summary = None
                needs_historical_catalysts = any(
                    bool(normalize_machine_rules(item.get("machine_rules")).get("catalyst_required"))
                    for item in candidates
                )
                if needs_historical_catalysts:
                    update_task_bar(task_bar, lab_monitor, 0.27, "Downloading point-in-time historical catalyst news")
                    articles = historical_news(
                        market,
                        [ticker],
                        start=start_time - timedelta(hours=24),
                        end=end_time,
                        max_pages=60,
                        progress=lambda page: update_task_bar(
                            task_bar,
                            lab_monitor,
                            0.27 + 0.08 * min(1.0, page / 60.0),
                            f"Downloading historical catalyst news · page {page}",
                        ),
                    )
                    rows, catalyst_summary = enrich_bars_with_point_in_time_catalysts(
                        rows,
                        articles,
                        lookback_hours=24.0,
                    )
                    update_task_bar(
                        task_bar,
                        lab_monitor,
                        0.35,
                        f"Catalyst history ready · {catalyst_summary.get('specific_catalysts', 0)} classified events",
                    )
                else:
                    update_task_bar(task_bar, lab_monitor, 0.35, "Historical data ready · no catalyst history required")

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

                update_task_bar(task_bar, lab_monitor, 0.38, "Starting validated optimization")
                def optimizer_progress(done: int, total: int, message: str) -> None:
                    update_task_bar(
                        task_bar,
                        lab_monitor,
                        0.38 + 0.40 * min(1.0, done / max(1, total)),
                        message,
                    )

                report = optimize_stock_strategies(
                    rows,
                    candidates,
                    ticker,
                    backtest_settings,
                    optimization_settings,
                    progress=optimizer_progress,
                    finalize_holdout=True,
                )
                update_task_bar(task_bar, lab_monitor, 0.78, "Training, validation, and final holdout complete")

                walk_report = None
                if run_walk_forward:
                    update_task_bar(task_bar, lab_monitor, 0.80, "Starting walk-forward validation")
                    def walk_progress(done: int, total: int, message: str) -> None:
                        update_task_bar(
                            task_bar,
                            lab_monitor,
                            0.80 + 0.16 * min(1.0, done / max(1, total)),
                            message,
                        )

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
                    update_task_bar(task_bar, lab_monitor, 0.96, "Walk-forward validation complete")
                else:
                    update_task_bar(task_bar, lab_monitor, 0.96, "Optimization and holdout validation complete")

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
                complete_task_bar(task_bar, lab_monitor, "Optimization + validation complete")
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
    st.caption(
        "Run one frozen strategy unchanged across several stocks. This is designed to expose ticker-specific "
        "overfitting: a strategy that only works on one symbol should look narrow here."
    )

    if not managed_strategies:
        st.info("Add or import a strategy before running cross-stock research.")
    else:
        universe_choices = {}
        for item in sorted(
            managed_strategies,
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

        universe_slot = st.empty()
        run_universe = universe_slot.button(
            "🧬 Test strategy across stocks",
            type="primary",
            use_container_width=True,
            disabled=len(universe_symbols) < 2,
            key="til_test_strategy_across_stocks",
        )
        if run_universe:
            universe_slot.button(
                "🧬 Testing…",
                type="primary",
                use_container_width=True,
                disabled=True,
                key="til_test_strategy_across_stocks_busy",
            )
            universe_monitor = long_task_monitor("universe_cross_stock_test")
            universe_bar = st.progress(
                0.02,
                text=universe_monitor.text(0.02, "Preparing cross-stock research…"),
            )
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
                update_task_bar(
                    universe_bar,
                    universe_monitor,
                    0.05,
                    f"Downloading history for {len(universe_symbols)} stocks",
                )
                def universe_history_progress(page: int) -> None:
                    status_box.write(f"Historical candle page {page}…")
                    update_task_bar(
                        universe_bar,
                        universe_monitor,
                        0.05 + 0.45 * min(1.0, page / 40.0),
                        f"Historical candle page {page}",
                    )
                rows_by_symbol = market.bars(
                    universe_symbols,
                    start=start_time,
                    end=end_time,
                    timeframe=universe_timeframe,
                    max_pages=40,
                    progress=universe_history_progress,
                )
                update_task_bar(universe_bar, universe_monitor, 0.50, "Historical candles ready")

                rules = normalize_machine_rules(effective_universe_strategy.get("machine_rules"))
                catalyst_summary_by_symbol = {}
                if rules.get("catalyst_required"):
                    status_box.write("Downloading point-in-time historical catalyst news…")
                    update_task_bar(
                        universe_bar,
                        universe_monitor,
                        0.52,
                        "Downloading point-in-time historical catalyst news",
                    )
                    articles = historical_news(
                        market,
                        universe_symbols,
                        start=start_time - timedelta(hours=24),
                        end=end_time,
                        max_pages=80,
                        progress=lambda page: update_task_bar(
                            universe_bar,
                            universe_monitor,
                            0.52 + 0.18 * min(1.0, page / 80.0),
                            f"Historical catalyst page {page}",
                        ),
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

                update_task_bar(
                    universe_bar,
                    universe_monitor,
                    0.78,
                    "Running frozen-rule cross-stock simulations",
                )
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
                complete_task_bar(
                    universe_bar,
                    universe_monitor,
                    "Cross-stock test complete",
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

    catalyst_slot = st.empty()
    load_catalysts = catalyst_slot.button(
        "📰 Load + classify historical catalysts",
        type="primary",
        use_container_width=True,
        disabled=not catalyst_ticker,
        key="til_load_classify_catalysts",
    )
    if load_catalysts:
        catalyst_slot.button(
            "📰 Loading…",
            type="primary",
            use_container_width=True,
            disabled=True,
            key="til_load_classify_catalysts_busy",
        )
        catalyst_monitor = long_task_monitor("historical_catalyst_research")
        catalyst_bar = st.progress(
            0.03,
            text=catalyst_monitor.text(0.03, f"Preparing {catalyst_ticker} catalyst history…"),
        )
        try:
            st.session_state["til_catalyst_ticker"] = catalyst_ticker
            market = market_client()
            cat_end = utc_now()
            cat_start = cat_end - timedelta(days=catalyst_days)
            status_box = st.status(f"Loading {catalyst_ticker} historical news…", expanded=True)
            def catalyst_page_progress(page: int) -> None:
                status_box.write(f"Historical news page {page}…")
                update_task_bar(
                    catalyst_bar,
                    catalyst_monitor,
                    0.05 + 0.78 * min(1.0, page / 60.0),
                    f"Historical news page {page}",
                )
            raw_articles = historical_news(
                market,
                [catalyst_ticker],
                start=cat_start,
                end=cat_end,
                max_pages=60,
                progress=catalyst_page_progress,
            )
            update_task_bar(
                catalyst_bar,
                catalyst_monitor,
                0.88,
                f"Classifying {len(raw_articles)} historical news items",
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
            complete_task_bar(
                catalyst_bar,
                catalyst_monitor,
                "Catalyst history complete",
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
    st.caption(
        "Use current Alpaca market data as a sensor, then apply a saved strategy's actual rules "
        "to the candidates. Validation status and live setup matching remain separate."
    )

    validated_strategies = [
        item for item in managed_strategies
        if str(item.get("validation_status") or "").lower() == "validated"
    ]
    include_research = st.checkbox(
        "Include unvalidated research strategies",
        value=bool(
            st.session_state.get(
                "til_market_discovery_include_research",
                not bool(validated_strategies),
            )
        ),
        key="til_market_discovery_include_research",
        help="Unvalidated strategies can be explored here but should not be treated as proven live edges.",
    )
    discovery_strategies = managed_strategies if include_research else validated_strategies

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
        target_id = str(st.session_state.pop("til_selected_strategy_id", "") or "")
        if target_id:
            for label, item in strategy_choices.items():
                if str(item.get("id") or "") == target_id:
                    st.session_state["til_market_discovery_strategy"] = label
                    break
        selected_discovery_strategy = strategy_choices[
            st.selectbox(
                "Strategy to scan for",
                list(strategy_choices),
                key="til_market_discovery_strategy",
            )
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

        scan_slot = st.empty()
        scan_now = scan_slot.button(
            "🔎 Scan current market",
            type="primary",
            use_container_width=True,
            key="til_scan_current_market",
        )
        if scan_now:
            scan_slot.button(
                "🔎 Scanning…",
                type="primary",
                use_container_width=True,
                disabled=True,
                key="til_scan_current_market_busy",
            )
            scan_monitor = long_task_monitor("market_discovery_scan")
            scan_bar = st.progress(
                0.03,
                text=scan_monitor.text(0.03, "Building live candidate universe…"),
            )
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

                update_task_bar(
                    scan_bar,
                    scan_monitor,
                    0.18,
                    f"Applying {selected_discovery_strategy.get('name')} to {len(symbols)} candidates",
                )
                status_box.write(f"Applying {selected_discovery_strategy.get('name')} to {len(symbols)} candidates…")

                def market_scan_progress(message: str) -> None:
                    status_box.write(message)
                    lower = str(message).casefold()
                    fraction = 0.25
                    if "relative-volume" in lower:
                        fraction = 0.45
                    elif "catalyst" in lower:
                        fraction = 0.62
                    elif "intraday chart" in lower:
                        fraction = 0.78
                    update_task_bar(scan_bar, scan_monitor, fraction, message)

                results = scan_strategy_universe(
                    market,
                    symbols,
                    selected_discovery_strategy,
                    progress=market_scan_progress,
                )
                st.session_state["til_market_discovery_result"] = {
                    "strategy_id": selected_discovery_strategy.get("id"),
                    "strategy_name": selected_discovery_strategy.get("name"),
                    "validation_status": selected_discovery_strategy.get("validation_status"),
                    "universe_mode": universe_mode,
                    "results": results,
                }
                status_box.update(label=f"Scan complete · {len(results)} stocks evaluated", state="complete", expanded=False)
                complete_task_bar(scan_bar, scan_monitor, "Market scan complete")
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
    st.caption(
        "Compare one stock against the strategy library using shared live market data, then inspect "
        "which validated setup currently fits best."
    )

    if not managed_strategies:
        st.info("No strategy families are available yet.")
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
            item for item in managed_strategies
            if not validated_only
            or str(item.get("validation_status") or "").lower() == "validated"
        ]
        if validated_only and not analyzer_strategies:
            st.info("No validated strategies are available yet. Turn off Validated only to explore research strategies.")

        analyzer_slot = st.empty()
        analyze_stock = analyzer_slot.button(
            "🧭 Analyze stock against strategies",
            type="primary",
            use_container_width=True,
            disabled=not analyzer_ticker or not analyzer_strategies,
            key="til_analyze_stock_strategies",
        )
        if analyze_stock:
            analyzer_slot.button(
                "🧭 Analyzing…",
                type="primary",
                use_container_width=True,
                disabled=True,
                key="til_analyze_stock_strategies_busy",
            )
            analyzer_monitor = long_task_monitor("stock_strategy_analysis")
            analyzer_bar = st.progress(
                0.03,
                text=analyzer_monitor.text(0.03, f"Preparing {analyzer_ticker} analysis…"),
            )
            try:
                st.session_state["til_analyzer_ticker"] = analyzer_ticker
                status_box = st.status(f"Analyzing {analyzer_ticker}…", expanded=True)
                def stock_analysis_progress(message: str) -> None:
                    status_box.write(message)
                    lower = str(message).casefold()
                    fraction = 0.20
                    if "relative-volume" in lower:
                        fraction = 0.42
                    elif "catalyst" in lower:
                        fraction = 0.60
                    elif "intraday chart" in lower:
                        fraction = 0.78
                    update_task_bar(analyzer_bar, analyzer_monitor, fraction, message)

                analysis = analyze_stock_strategies(
                    market_client(),
                    analyzer_ticker,
                    analyzer_strategies,
                    progress=stock_analysis_progress,
                )
                st.session_state["til_stock_analysis"] = analysis
                status_box.update(label=f"{analyzer_ticker} analysis complete", state="complete", expanded=False)
                complete_task_bar(analyzer_bar, analyzer_monitor, f"{analyzer_ticker} analysis complete")
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
    st.caption(
        "Research and validation remain separate from execution. Existing safety checks and Alpaca "
        "paper-trading controls stay in place."
    )
    if st.button("Open existing Live Strategy Runner", use_container_width=True):
        st.switch_page("pages/Live_Strategy_Runner.py")
