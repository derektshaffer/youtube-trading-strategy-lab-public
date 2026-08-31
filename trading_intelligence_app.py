"""Trading Intelligence Lab — unified trading research platform."""

from __future__ import annotations

from collections import Counter
import hashlib
from copy import deepcopy
import html
import importlib
import inspect
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import streamlit as st

from app_access import require_app_access

st.set_page_config(
    page_title="Trading Intelligence Lab",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="locked",
)
require_app_access(st)

_boot_message = str(
    st.session_state.get("_trading_app_boot_message") or ""
).strip()
_boot_status_factory = getattr(st, "status", None)
_boot_status = None
_early_library_cold_start = not isinstance(
    st.session_state.get("_til_library_render_cache"),
    dict,
)
if _boot_message or _early_library_cold_start:
    startup_label = _boot_message or "Starting Trading Intelligence Lab…"
    if callable(_boot_status_factory):
        _boot_status = _boot_status_factory(startup_label, expanded=False)
    else:
        st.info(startup_label)

# Heavy research/ML imports intentionally happen only after access is granted.
# This keeps the password screen fast and lets navigation show a loading state
# before the rest of the application stack is initialized on a rerun.
import pandas as pd

from finder_report_persistence import (
    latest_completed_finder_report,
    newest_matching_finder_report,
)
from hot_deploy_imports import load_current_source_module
from market_feature_scorecards import run_detector_scorecards
from market_detector_gate import evaluate_scorecard_report
from market_feature_validation import DETECTOR_SPECS
from live_learning import (
    DEFAULT_MAX_OBSERVATIONS,
    build_scan_shadow_observations,
    earliest_pending_observed_at,
    mature_shadow_observations,
    merge_shadow_observations,
    pending_symbols,
)
from predictive_model_monitor import build_shadow_model_monitor
from predictive_model_registry import (
    build_model_registry,
    ready_shadow_models,
)
from predictive_model_head_to_head import build_historical_model_head_to_head


def _lazy_module_call(module_name: str, function_name: str, *args: Any, **kwargs: Any) -> Any:
    """Import heavy ML modules only when an ML/scoring action actually needs them."""
    module = importlib.import_module(module_name)
    function = getattr(module, function_name, None)
    if function is None and module_name == "predictive_ml_pipeline":
        # Preserve the existing hot-deploy protection without paying the normal
        # pipeline import cost on Finder/Overview startup.
        module = load_current_source_module(module_name)
        function = getattr(module, function_name)
    if function is None:
        raise AttributeError(f"{module_name}.{function_name} is unavailable")
    return function(*args, **kwargs)


def build_portable_probability_model(*args: Any, **kwargs: Any) -> Any:
    return _lazy_module_call(
        "predictive_probability_model",
        "build_portable_probability_model",
        *args,
        **kwargs,
    )


def score_scan_result_probability(*args: Any, **kwargs: Any) -> Any:
    return _lazy_module_call(
        "predictive_probability_model",
        "score_scan_result_probability",
        *args,
        **kwargs,
    )


def score_boosted_probability_model(*args: Any, **kwargs: Any) -> Any:
    return _lazy_module_call(
        "predictive_boosted_probability_model",
        "score_boosted_probability_model",
        *args,
        **kwargs,
    )


def build_cross_stock_training_dataset(*args: Any, **kwargs: Any) -> Any:
    return _lazy_module_call(
        "predictive_ml_pipeline",
        "build_cross_stock_training_dataset",
        *args,
        **kwargs,
    )


def walk_forward_logistic_baseline(*args: Any, **kwargs: Any) -> Any:
    return _lazy_module_call(
        "predictive_ml_pipeline",
        "walk_forward_logistic_baseline",
        *args,
        **kwargs,
    )


def leave_one_symbol_out_walk_forward_logistic_baseline(*args: Any, **kwargs: Any) -> Any:
    return _lazy_module_call(
        "predictive_ml_pipeline",
        "leave_one_symbol_out_walk_forward_logistic_baseline",
        *args,
        **kwargs,
    )


def similarity_weighted_leave_one_symbol_out_walk_forward_logistic_baseline(
    *args: Any,
    **kwargs: Any,
) -> Any:
    return _lazy_module_call(
        "predictive_ml_pipeline",
        "similarity_weighted_leave_one_symbol_out_walk_forward_logistic_baseline",
        *args,
        **kwargs,
    )


def ticker_specific_walk_forward_logistic_baseline(*args: Any, **kwargs: Any) -> Any:
    return _lazy_module_call(
        "predictive_ml_pipeline",
        "ticker_specific_walk_forward_logistic_baseline",
        *args,
        **kwargs,
    )


def archetype_transfer_walk_forward_logistic_baseline(*args: Any, **kwargs: Any) -> Any:
    return _lazy_module_call(
        "predictive_ml_pipeline",
        "archetype_transfer_walk_forward_logistic_baseline",
        *args,
        **kwargs,
    )
from trading_app_runtime import market_client, setting
from trading_glass_theme import inject_research_glass_theme

# Do not reload shared modules in place during a Streamlit rerun. Doing so can
# expose partially initialized modules to the file watcher and other pages.
import stock_strategy_finder as _stock_strategy_finder

# Streamlit Cloud can hot-deploy this page before Python invalidates the already
# imported stock_strategy_finder module. If the page references a newly added
# Finder helper while the cached module is still the prior version, startup
# crashes with AttributeError. Detect that stale-module shape and load the
# current source under a private versioned alias, just as we already do for the
# predictive ML module above.
_required_finder_attributes = (
    "finder_evidence_verdict",
    "apply_paper_fidelity_to_verdict",
    "apply_historical_spread_integrity_guard",
    "apply_holdout_reuse_guard",
    "record_holdout_exposure",
    "stock_finder_strategy_families",
    "validated_status_ready",
)
_finder_module = _stock_strategy_finder
_finder_profiles = getattr(_finder_module, "SEARCH_PROFILES", {}) or {}
if (
    any(not hasattr(_finder_module, name) for name in _required_finder_attributes)
    or "Current Regime" not in _finder_profiles
):
    _finder_module = load_current_source_module("stock_strategy_finder")

_finder_run_parameters = inspect.signature(
    _finder_module.run_stock_strategy_finder
).parameters
_finder_supports_resume = {
    "resume_state",
    "checkpoint",
}.issubset(_finder_run_parameters)

SEARCH_PROFILES = _finder_module.SEARCH_PROFILES
estimate_search_work = _finder_module.estimate_search_work
latest_finder_checkpoint = _finder_module.latest_finder_checkpoint
merge_finder_checkpoint_into_library = _finder_module.merge_finder_checkpoint_into_library
merge_finder_report_into_library = _finder_module.merge_finder_report_into_library
finder_evidence_verdict = _finder_module.finder_evidence_verdict
apply_paper_fidelity_to_verdict = _finder_module.apply_paper_fidelity_to_verdict
apply_historical_spread_integrity_guard = _finder_module.apply_historical_spread_integrity_guard
apply_holdout_reuse_guard = _finder_module.apply_holdout_reuse_guard
record_holdout_exposure = _finder_module.record_holdout_exposure
parameter_stability_test = _finder_module.parameter_stability_test
validated_status_ready = _finder_module.validated_status_ready
run_stock_strategy_finder = _finder_module.run_stock_strategy_finder
search_profile = _finder_module.search_profile
selected_strategies_for_profile = _finder_module.selected_strategies_for_profile
stock_finder_strategy_families = _finder_module.stock_finder_strategy_families
from trading_catalyst_core import (
    catalyst_intelligence_summary,
    classify_catalyst,
    enrich_bars_with_point_in_time_catalysts,
    historical_news,
    rank_catalyst_evidence,
)
from sec_catalyst_intelligence import (
    SecEdgarClient,
    classify_recent_sec_filings,
    sec_filing_summary,
)
from trading_market_discovery import (
    LIVE_SCAN_BATCH_SIZE,
    MAX_LIVE_SCAN_SYMBOLS,
    analyze_stock_strategies,
    merge_momentum_candidate_universe,
    scan_market_strategies,
    scan_strategy_universe,
)
from retrospective_teacher import (
    build_retrospective_teacher_run,
    merge_retrospective_teacher_run,
)
from open_source_reference_catalog import reference_rows
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
from trading_research_orchestrator import (
    DEFAULT_GEMINI_BULK_RESEARCH_MODEL,
    DEFAULT_GEMINI_SPECIALIST_MODEL,
    dispatch_github_workflow,
    enqueue_research_job,
    research_queue_status,
    seed_continuous_research_cycle,
)
from trading_system_health import (
    CLOUD_SMOKE_WORKFLOW,
    cloud_job_display_state,
    configuration_checks,
    latest_workflow_run,
    overall_system_state,
    probe_github_workflow,
    subsystem_ready,
    workflow_run_display_state,
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
    paper_execution_fidelity,
    strategy_integrity_report,
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
    historical_entry_spread_audit,
    normalize_machine_rules,
    optimize_stock_strategies,
    safe_float,
    split_safe_raw_research_rows,
    utc_now,
)

if _boot_status is not None:
    if _early_library_cold_start:
        _boot_status.update(
            label="Core modules loaded · loading research library…",
            expanded=False,
        )
    else:
        _boot_status.update(
            label="Trading Intelligence Lab ready",
            state="complete",
            expanded=False,
        )
        st.session_state.pop("_trading_app_boot_message", None)

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

    .til-guided-flow {
        margin: 10px 0 22px 0;
        padding: 15px;
        border: 1px solid #2d4563;
        border-radius: 15px;
        background: linear-gradient(135deg, rgba(19,35,55,.95), rgba(11,22,36,.96));
    }
    .til-guided-flow-title {
        font-size: .76rem;
        font-weight: 850;
        letter-spacing: .11em;
        color: #8fa9c5;
        margin-bottom: 10px;
    }
    .til-guided-flow-grid {
        display: grid;
        grid-template-columns: repeat(5, minmax(0,1fr));
        gap: 8px;
    }
    .til-guided-step {
        padding: 10px 11px;
        border: 1px solid #293e59;
        border-radius: 11px;
        background: rgba(10,21,34,.72);
        min-height: 78px;
    }
    .til-guided-step.active {
        border-color: #45d7ff;
        box-shadow: inset 0 0 0 1px rgba(69,215,255,.18);
        background: rgba(18,50,70,.78);
    }
    .til-guided-step.complete {
        border-color: #2d7359;
        background: rgba(17,54,43,.55);
    }
    .til-guided-step-num {
        display: inline-flex;
        width: 23px;
        height: 23px;
        border-radius: 999px;
        align-items: center;
        justify-content: center;
        margin-bottom: 5px;
        font-size: .76rem;
        font-weight: 900;
        color: #08131f;
        background: #86dfff;
    }
    .til-guided-step.complete .til-guided-step-num {background:#55dfa1;}
    .til-guided-step strong {display:block;font-size:.92rem;line-height:1.15;}
    .til-guided-step small {display:block;color:#91a7bd;font-size:.76rem;line-height:1.28;margin-top:4px;}
    @media (max-width: 900px) {
        .til-guided-flow-grid {grid-template-columns: 1fr;}
        .til-guided-step {min-height: 0;}
    }

    .til-finder-notice {
        border:1px solid transparent;
        border-radius:11px;
        padding:17px 18px;
        margin:14px 0 16px 0;
        line-height:1.5;
    }
    .til-finder-notice-title {
        font-weight:850;
        letter-spacing:.01em;
        margin-bottom:6px;
    }
    .til-finder-notice-body {
        font-size:.95rem;
        opacity:.94;
    }
    .til-finder-policy-note {
        background:#102c42;
        border-color:#1f607c;
        color:#d5effb;
    }
    .til-finder-checkpoint-note {
        background:#3b321c;
        border-color:#8e7130;
        color:#f4e7bc;
    }
    .til-finder-local-note {
        background:#35261d;
        border-color:#9a6037;
        color:#f6d7bf;
    }
    .til-finder-cloud-note {
        background:#103d35;
        border-color:#278b72;
        color:#d8fff2;
    }
    .til-finder-complete-note {
        background:#172f4d;
        border-color:#377bb0;
        color:#dcefff;
    }
    .til-cloud-progress-wrap {
        margin: 9px 0 18px 0;
    }
    .til-cloud-progress-meta {
        display:flex;
        align-items:center;
        justify-content:space-between;
        gap:12px;
        margin-bottom:7px;
        color:#e8f4ff;
        font-weight:760;
        font-size:.94rem;
    }
    .til-cloud-progress-track {
        position:relative;
        height:15px;
        width:100%;
        overflow:hidden;
        border-radius:999px;
        background:#06111d;
        border:1px solid #4f7391;
        box-shadow:inset 0 0 0 1px rgba(0,0,0,.48);
    }
    .til-cloud-progress-fill {
        height:100%;
        min-width:0;
        border-radius:999px;
        background:linear-gradient(90deg,#2edc9a 0%,#42c8e8 100%);
        box-shadow:0 0 10px rgba(66,200,232,.24);
    }
    .til-cloud-progress-queued {
        height:100%;
        width:100%;
        border-radius:999px;
        background:repeating-linear-gradient(
            135deg,
            #10283a 0px,
            #10283a 12px,
            #17384f 12px,
            #17384f 24px
        );
    }
    .til-cloud-progress-sub {
        margin-top:6px;
        color:#9fb3c7;
        font-size:.84rem;
    }

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


LIBRARY_CLOUD_REFRESH_SECONDS = 60.0
_LIBRARY_RENDER_CACHE_KEY = "_til_library_render_cache"
_LIBRARY_LAST_CLOUD_REFRESH_KEY = "_til_library_last_cloud_refresh_monotonic"
_LIBRARY_REMOTE_SHA_KEY = "_til_library_remote_sha"


def _local_library_mtime_ns(store: StrategyStore) -> int:
    try:
        return int(store.path.stat().st_mtime_ns)
    except OSError:
        return -1


def _recover_cloud_library_conflict(store: StrategyStore, exc: AppError) -> dict[str, Any]:
    conflict_marker = (
        "Both the local Trading Lab library and the private GitHub library changed "
        "since their last shared version."
    )
    if conflict_marker not in str(exc):
        raise exc
    data = store.restore_cloud_backup()
    st.session_state["_til_cloud_conflict_recovered"] = True
    return data


def load_cloud_status_library() -> dict[str, Any]:
    """Refresh raw durable queue/results without rebuilding strategy families."""
    store = intelligence_store()
    try:
        return store.load_latest()
    except AppError as exc:
        return _recover_cloud_library_conflict(store, exc)


MAX_PREDICTIVE_ML_RUN_HISTORY = 12
LIVE_LEARNING_STORAGE_KEY = "live_learning_observations"

def shadow_probability_models(library: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a bounded set of historically validated models for parallel shadow scoring."""
    return ready_shadow_models(library.get("predictive_ml_runs") or [])


def historical_shadow_head_to_head(library: dict[str, Any]) -> dict[str, Any]:
    """Reuse already-saved OOS validation metrics for a fast paired model comparison."""
    return build_historical_model_head_to_head(shadow_probability_models(library))


def active_shadow_champion_id(library: dict[str, Any]) -> str:
    research_system = (
        library.get("research_system")
        if isinstance(library.get("research_system"), dict)
        else {}
    )
    registry = (
        research_system.get("predictive_model_registry")
        if isinstance(research_system.get("predictive_model_registry"), dict)
        else {}
    )
    registry_id = str(registry.get("champion_model_id") or "").strip()
    registry_status = str(registry.get("status") or "").strip().upper()

    # Once live evidence has confirmed a champion (or flagged it for drift),
    # the live registry owns selection. Before that, use the fair historical
    # head-to-head so we do not waste several trading days waiting for an answer
    # that the already-computed untouched historical folds can provide.
    if registry_id and registry_status in {
        "CHAMPION_CONFIRMED",
        "CHAMPION_DRIFT_ALERT",
    }:
        return registry_id

    historical = historical_shadow_head_to_head(library)
    historical_id = str(historical.get("leader_model_id") or "").strip()
    if historical_id:
        return historical_id
    return registry_id


def latest_shadow_probability_model(library: dict[str, Any]) -> dict[str, Any]:
    """Return the current research-only champion, falling back to the newest ready model."""
    models = shadow_probability_models(library)
    if not models:
        return {}
    champion_id = active_shadow_champion_id(library)
    if champion_id:
        for model in models:
            if str(model.get("id") or "") == champion_id:
                return model
    return models[0]


def apply_shadow_probability_scores(
    results: list[dict[str, Any]],
    models: list[dict[str, Any]] | dict[str, Any],
    *,
    champion_model_id: str = "",
) -> list[dict[str, Any]]:
    """Score all ready models in parallel while exposing only the champion as primary."""
    if isinstance(models, dict):
        candidates = [models] if models else []
    else:
        candidates = [item for item in models or [] if isinstance(item, dict)]
    candidates = [
        item for item in candidates if item.get("shadow_scoring_enabled")
    ]
    if not candidates:
        return results

    chosen_id = str(champion_model_id or "").strip()
    if not chosen_id or not any(str(item.get("id") or "") == chosen_id for item in candidates):
        chosen_id = str(candidates[0].get("id") or "")

    for item in results:
        if not isinstance(item, dict):
            continue
        predictions = []
        primary = None
        for model in candidates:
            if str(model.get("model_type") or "") == "portable_gradient_boosted_trees":
                score = score_boosted_probability_model(model, item)
            else:
                score = score_scan_result_probability(model, item)
            if score.get("status") != "SCORED":
                continue
            predictions.append(score)
            if str(score.get("model_id") or "") == chosen_id:
                primary = score
        if predictions:
            item["ml_predictions"] = predictions
            item["ml_prediction"] = primary or predictions[0]
    return results

def shadow_probability_model_lookup(library: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index every saved portable probability artifact by model id."""
    return {
        str(model.get("id") or ""): model
        for model in shadow_probability_models(library)
        if str(model.get("id") or "").strip()
    }


LIVE_LEARNING_STATUS_KEY = "live_learning_status"
LIVE_LEARNING_MAX_NEW_PER_SCAN = 50
LIVE_LEARNING_MAX_MATURATION_SYMBOLS = 25


def persist_live_learning_cycle(
    market: Any,
    results: list[dict[str, Any]],
    *,
    source: str,
    max_new: int = LIVE_LEARNING_MAX_NEW_PER_SCAN,
) -> dict[str, Any]:
    """Durably log causal live observations and mature prior outcomes.

    This is research-only. It never changes scanner ranking, strategy matching,
    execution, or any live recommendation.
    """
    observed_at = utc_now()
    incoming = build_scan_shadow_observations(
        results,
        source=source,
        observed_at=observed_at,
        max_items=max_new,
    )
    if not incoming:
        return {
            "logged": 0,
            "matured": 0,
            "total": 0,
            "complete": 0,
            "partial": 0,
            "pending": 0,
            "research_only": True,
        }

    store = intelligence_store()
    try:
        data = store.load_latest()
    except AppError as exc:
        data = _recover_cloud_library_conflict(store, exc)

    research_system = data.setdefault("research_system", {})
    existing = [
        dict(item)
        for item in research_system.get(LIVE_LEARNING_STORAGE_KEY) or []
        if isinstance(item, dict)
    ]

    current_symbols = sorted(
        {
            str(item.get("symbol") or "").strip().upper()
            for item in incoming
            if str(item.get("symbol") or "").strip()
        }
    )
    maturation_summary = {
        "updated": 0,
        "completed": 0,
        "partial": 0,
        "pending": 0,
    }

    # Keep live scans responsive: mature only a bounded subset of symbols that
    # are already being inspected, and never fail the scan if history is unavailable.
    scoped_pending = pending_symbols(existing, only_symbols=current_symbols)
    scoped_pending = scoped_pending[:LIVE_LEARNING_MAX_MATURATION_SYMBOLS]
    if scoped_pending:
        earliest = earliest_pending_observed_at(
            existing,
            only_symbols=scoped_pending,
        )
        if earliest is not None:
            history_start = max(
                earliest - timedelta(minutes=2),
                observed_at - timedelta(days=7),
            )
            try:
                future_bars = market.bars(
                    scoped_pending,
                    start=history_start,
                    end=observed_at,
                    timeframe="1Min",
                    feed=market.live_feed,
                    adjustment="raw",
                    max_pages=60,
                )
                existing, maturation_summary = mature_shadow_observations(
                    existing,
                    future_bars,
                    now=observed_at,
                    only_symbols=scoped_pending,
                )
            except AppError:
                # Logging the observation is more important than making outcome
                # maturation a hard dependency of an interactive scan.
                pass

    combined = merge_shadow_observations(
        existing,
        incoming,
        max_records=DEFAULT_MAX_OBSERVATIONS,
    )
    counts = {"complete": 0, "partial": 0, "pending": 0}
    for item in combined:
        status = str(item.get("outcome_status") or "PENDING").strip().lower()
        if status in counts:
            counts[status] += 1
        else:
            counts["pending"] += 1

    model_lookup = shadow_probability_model_lookup(data)
    model_monitor = build_shadow_model_monitor(
        combined,
        model_lookup=model_lookup,
    )
    research_system["predictive_model_monitor"] = model_monitor
    previous_registry = (
        research_system.get("predictive_model_registry")
        if isinstance(research_system.get("predictive_model_registry"), dict)
        else {}
    )
    model_registry = build_model_registry(
        shadow_probability_models(data),
        model_monitor,
        previous=previous_registry,
    )
    research_system["predictive_model_registry"] = model_registry

    status_record = {
        "last_logged_at": observed_at.isoformat(),
        "last_source": source,
        "last_logged": len(incoming),
        "last_matured": int(maturation_summary.get("updated") or 0),
        "total": len(combined),
        **counts,
        "horizons_minutes": [5, 15, 30, 60],
        "research_only": True,
        "affects_live_ranking": False,
        "model_monitor_status": (
            (model_monitor.get("latest_model") or {}).get("status")
            if isinstance(model_monitor.get("latest_model"), dict)
            else model_monitor.get("status")
        ),
        "champion_model_id": model_registry.get("champion_model_id"),
        "model_registry_status": model_registry.get("status"),
    }
    research_system[LIVE_LEARNING_STORAGE_KEY] = combined
    research_system[LIVE_LEARNING_STATUS_KEY] = status_record
    data["research_system"] = research_system
    store.save(data)
    return {
        "logged": len(incoming),
        "matured": int(maturation_summary.get("updated") or 0),
        "total": len(combined),
        **counts,
        "research_only": True,
    }


def persist_predictive_ml_result(result: dict[str, Any]) -> dict[str, Any]:
    """Save a compact completed ML result to durable Trading Lab storage."""
    if not isinstance(result, dict) or not result.get("evaluation"):
        raise AppError("Predictive ML result was empty and could not be saved.")

    record = deepcopy(result)
    completed_at = str(record.get("completed_at") or utc_now().isoformat())
    record["completed_at"] = completed_at
    identity = "|".join(
        [
            completed_at,
            " ".join(str(symbol) for symbol in record.get("symbols") or []),
            str(record.get("trading_days") or record.get("days") or ""),
            str(record.get("horizon") or ""),
            str(record.get("target_mode") or ""),
            str(record.get("session_mode") or ""),
        ]
    )
    record["id"] = str(record.get("id") or hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16])

    store = intelligence_store()
    try:
        data = store.load_latest()
    except AppError as exc:
        data = _recover_cloud_library_conflict(store, exc)

    previous = [
        item
        for item in data.get("predictive_ml_runs") or []
        if isinstance(item, dict) and str(item.get("id") or "") != record["id"]
    ]
    data["predictive_ml_runs"] = [record, *previous][:MAX_PREDICTIVE_ML_RUN_HISTORY]
    store.save(data)
    return record


def load_library(
    *,
    force_cloud_refresh: bool = False,
    mutable: bool = False,
) -> dict[str, Any]:
    """Load the prepared library while keeping ordinary Streamlit reruns lightweight.

    Rendering gets the cached object directly. Explicit update/save actions opt
    into a defensive deep copy with mutable=True.
    """
    store = intelligence_store()

    def result(value: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(value) if mutable else value
    now = time.monotonic()
    cached = st.session_state.get(_LIBRARY_RENDER_CACHE_KEY)
    cached_data = cached.get("data") if isinstance(cached, dict) else None
    current_mtime_ns = _local_library_mtime_ns(store)
    cached_mtime_ns = (
        int(cached.get("local_mtime_ns") or -1)
        if isinstance(cached, dict)
        else -2
    )
    local_unchanged = (
        isinstance(cached_data, dict)
        and current_mtime_ns == cached_mtime_ns
    )

    last_cloud_refresh = float(
        st.session_state.get(_LIBRARY_LAST_CLOUD_REFRESH_KEY) or 0.0
    )
    cloud_refresh_due = (
        force_cloud_refresh
        or not isinstance(cached_data, dict)
        or now - last_cloud_refresh >= LIBRARY_CLOUD_REFRESH_SECONDS
    )

    # Most UI interactions land here: no disk JSON parse, no network request,
    # and no strategy-family rebuild when neither local nor cloud refresh is due.
    if local_unchanged and not cloud_refresh_due:
        return result(cached_data)

    remote_sha = ""
    if (
        cloud_refresh_due
        and store.cloud_backup is not None
        and store.path.exists()
    ):
        try:
            revision = store.cloud_backup.library_revision()
            remote_sha = str((revision or {}).get("sha") or "")
        except AppError:
            revision = None

        previous_remote_sha = str(
            st.session_state.get(_LIBRARY_REMOTE_SHA_KEY) or ""
        )
        if (
            local_unchanged
            and remote_sha
            and previous_remote_sha == remote_sha
            and isinstance(cached_data, dict)
        ):
            st.session_state[_LIBRARY_LAST_CLOUD_REFRESH_KEY] = now
            return result(cached_data)

    if cloud_refresh_due:
        # On a fresh browser session there is no Streamlit render cache yet.
        # Compare Git blob SHAs first: if the local file is byte-identical to
        # cloud, parse the local file and avoid a ~75 MB remote download.
        local_matches_remote = False
        if (
            not isinstance(cached_data, dict)
            and remote_sha
            and store.path.exists()
        ):
            try:
                local_revision = store.local_library_revision()
                local_matches_remote = (
                    str((local_revision or {}).get("sha") or "") == remote_sha
                )
            except AppError:
                local_matches_remote = False

        if local_matches_remote:
            data = store.load()
        else:
            try:
                data = store.load_latest()
            except AppError as exc:
                data = _recover_cloud_library_conflict(store, exc)
        st.session_state[_LIBRARY_LAST_CLOUD_REFRESH_KEY] = now
        if not remote_sha:
            remote_sha = str(getattr(store, "restored_cloud_sha", "") or "")
        if remote_sha:
            st.session_state[_LIBRARY_REMOTE_SHA_KEY] = remote_sha
    else:
        # The local file changed since the prepared snapshot, usually because a
        # save completed in this session. Rebuild from that local copy only.
        data = store.load()

    data.setdefault("knowledge_sources", [])
    data.setdefault("strategies", [])
    data.setdefault("research_runs", [])
    data.setdefault("validation_runs", [])
    data.setdefault("predictive_ml_runs", [])

    # Automatically pull newly analyzed YouTube strategies from the original Trading Lab.
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
        pass

    raw_strategy_records = [
        dict(item)
        for item in data.get("strategies") or []
        if isinstance(item, dict)
    ]

    def without_render_only_fields(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        for item in items:
            value = dict(item)
            value.pop("research_readiness", None)
            cleaned.append(value)
        return cleaned

    upgraded_strategies: list[dict[str, Any]] = []
    for raw in raw_strategy_records:
        upgraded = upgrade_native_strategy_rules(raw)
        upgraded["research_readiness"] = research_readiness(upgraded)
        upgraded_strategies.append(upgraded)
    # research_readiness is derived on every render and should not make the
    # durable ~75 MB library look modified by itself.
    native_strategy_changed = (
        without_render_only_fields(upgraded_strategies)
        != without_render_only_fields(raw_strategy_records)
    )
    data["strategies"] = upgraded_strategies

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

    canonical_changed = (
        without_render_only_fields(canonical_families)
        != without_render_only_fields(existing_canonical)
    )
    data["strategies"] = [*source_and_other, *canonical_families]

    prepared_changed = (
        legacy_changed
        or native_strategy_changed
        or sources_changed
        or canonical_changed
    )
    if prepared_changed:
        # Ordinary page rendering must never turn a cold start into a synchronous
        # ~75 MB cloud upload. Apply compatibility/canonical upgrades in memory.
        # Persist them only during an explicit mutable/refresh workflow where the
        # user already expects storage I/O.
        if mutable or force_cloud_refresh:
            try:
                data = store.save(data)
                # save() created a new remote commit, so learn its SHA cheaply on
                # the next scheduled revision probe instead of assuming the old one.
                st.session_state.pop(_LIBRARY_REMOTE_SHA_KEY, None)
                st.session_state[_LIBRARY_LAST_CLOUD_REFRESH_KEY] = now
            except AppError:
                data = store.load()
        else:
            st.session_state["_til_library_prepared_changes_pending"] = True

    st.session_state[_LIBRARY_RENDER_CACHE_KEY] = {
        "updated_at": str(data.get("updated_at") or ""),
        "local_mtime_ns": _local_library_mtime_ns(store),
        "data": data,
    }
    return result(data)


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
    "trailing_stop_pct": "Trailing stop %",
    "move_stop_to_breakeven_at_r": "Move stop to breakeven at R",
    "scale_out_fraction_pct": "Scale-out position %",
    "scale_out_at_r": "Take partial profit at R",
    "exit_below_vwap": "Exit when VWAP is lost",
    "exit_below_fast_ema": "Exit when fast EMA is lost",
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
        "trailing_stop_pct": f"Trail the remaining position by **{number}%** from the highest price reached.",
        "move_stop_to_breakeven_at_r": f"Move the stop to **breakeven after {number}R** of favorable movement.",
        "scale_out_fraction_pct": f"Sell **{number}% of the original position** at the first partial-profit trigger.",
        "scale_out_at_r": f"Take the first partial profit after **{number}R** of favorable movement.",
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
        "exit_below_vwap": "Exit the position when price **closes below VWAP**.",
        "exit_below_fast_ema": "Exit the position when price **closes below the fast EMA**.",
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
    "Stock Strategy Finder",
    "Overview",
    "Knowledge Sources",
    "AI Research Autopilot",
    "Strategy Library",
    "Strategy Integrity",
    "Retrospective Learning",
    "Strategy DNA",
    "Make Strategy Testable",
    "Strategy Lab",
    "Validation",
    "Universe Research",
    "Market Discovery",
    "Pattern Validation",
    "Catalyst Intelligence",
    "Stock Analyzer",
    "Live / Paper",
    "System Health",
]

WORKSPACE_DISPLAY_LABELS = {
    "Stock Strategy Finder": "0. Find Strategy",
    "Overview": "1. Overview",
    "Knowledge Sources": "2. Knowledge Sources",
    "AI Research Autopilot": "3. AI Research Autopilot",
    "Strategy Library": "4. Strategy Library",
    "Strategy Integrity": "4A. Strategy Integrity Audit",
    "Strategy DNA": "5. Strategy Blueprint",
    "Make Strategy Testable": "6. Rule Builder",
    "Strategy Lab": "7. Strategy Lab",
    "Validation": "8. Validation",
    "Universe Research": "9. Market Universe",
    "Market Discovery": "10. Market Discovery",
    "Pattern Validation": "10A. Pattern Validation",
    "Catalyst Intelligence": "11. Catalyst Intelligence",
    "Stock Analyzer": "12. Stock Analyzer",
    "Live / Paper": "13. Paper & Live Trading",
    "System Health": "14. System Health",
}
WORKSPACE_DISPLAY_TO_INTERNAL = {
    label: internal
    for internal, label in WORKSPACE_DISPLAY_LABELS.items()
}

WORKSPACE_PAGE_META = {
    "Stock Strategy Finder": {
        "step": "1",
        "group": "Guided Strategy Workflow",
        "title": "Find & Test a Strategy",
        "subtitle": "Enter a ticker. The Lab finds the strongest strategy candidate, tests it on unseen data, then guides you through the current signal, validation, and confidence.",
    },
    "Overview": {
        "step": "01",
        "group": "Home",
        "title": "Trading Intelligence Home",
        "subtitle": "Choose what you want to accomplish. The Lab handles the research pipeline behind the scenes.",
    },
    "Knowledge Sources": {
        "step": "02",
        "group": "Research",
        "title": "Add Research Material",
        "subtitle": "Build your evidence library. The AI reads, extracts, and organizes trading intelligence.",
    },
    "AI Research Autopilot": {
        "step": "03",
        "group": "Research",
        "title": "AI Discoveries & Research",
        "subtitle": "Let AI consolidate source ideas, prepare testable hypotheses, and move promising families into research.",
    },
    "Strategy Library": {
        "step": "04",
        "group": "Strategy Development",
        "title": "Strategy Library",
        "subtitle": "Review the organized strategy families that AI has extracted and consolidated from your sources.",
    },
    "Strategy Integrity": {
        "step": "04A",
        "group": "Strategy Development",
        "title": "Strategy Integrity Audit",
        "subtitle": "Verify that each source strategy is faithfully represented by the machine rules and backtester before trusting optimization results.",
    },
    "Retrospective Learning": {
        "step": "04B",
        "group": "Strategy Development",
        "title": "Retrospective Teacher → Causal Learner",
        "subtitle": "Use hindsight to label what eventually mattered, while freezing every predictive feature at the timestamp it was actually knowable.",
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
        "title": "Find Stocks Worth Watching",
        "subtitle": "Search the current market for stocks that match the conditions of validated or research-ready strategies.",
    },
    "Pattern Validation": {
        "step": "10A",
        "group": "Market Research",
        "title": "Pattern Validation",
        "subtitle": "Replay market-behavior detectors causally across historical stocks before allowing those observations to affect live rankings.",
    },
    "Catalyst Intelligence": {
        "step": "11",
        "group": "Market Research",
        "title": "Catalyst Intelligence",
        "subtitle": "Add point-in-time news and catalyst context so momentum setups are evaluated with the reason behind the move.",
    },
    "Stock Analyzer": {
        "step": "2–4",
        "group": "Guided Strategy Workflow",
        "title": "Compare → Validate → Current Setup",
        "subtitle": "Compare the strategies Step 1 tested, validate one, then check whether its setup is active now.",
    },
    "Live / Paper": {
        "step": "13",
        "group": "Execution",
        "title": "Paper & Live Trading",
        "subtitle": "Deploy validated rules into paper or live workflows while keeping research and execution clearly separated.",
    },
    "System Health": {
        "step": "14",
        "group": "Operations",
        "title": "System Health",
        "subtitle": "Verify storage, market data, AI, GitHub Actions, and cloud workers before trusting a long-running research job.",
    },
}

WORKSPACE_NAV_GROUPS = [
    ("START HERE", ["Stock Strategy Finder"]),
    ("RESEARCH", ["Overview", "Knowledge Sources", "AI Research Autopilot"]),
    (
        "STRATEGY DEVELOPMENT",
        ["Strategy Library", "Strategy Integrity", "Retrospective Learning", "Strategy DNA", "Make Strategy Testable", "Strategy Lab", "Validation"],
    ),
    (
        "MARKET RESEARCH",
        ["Universe Research", "Market Discovery", "Pattern Validation", "Catalyst Intelligence", "Stock Analyzer"],
    ),
    ("EXECUTION", ["Live / Paper"]),
    ("OPERATIONS", ["System Health"]),
]

WORKSPACE_NAV_ICONS = {
    "Stock Strategy Finder": "◆",
    "Overview": "⌁",
    "Knowledge Sources": "◇",
    "AI Research Autopilot": "✦",
    "Strategy Library": "▤",
    "Strategy Integrity": "⚖",
    "Retrospective Learning": "↺",
    "Strategy DNA": "◇",
    "Make Strategy Testable": "≣",
    "Strategy Lab": "⬡",
    "Validation": "✓",
    "Universe Research": "◎",
    "Market Discovery": "⌖",
    "Pattern Validation": "▦",
    "Catalyst Intelligence": "ϟ",
    "Stock Analyzer": "⌕",
    "Live / Paper": "↗",
    "System Health": "⚙",
}


def _nav_key(section: str, *, active: bool = False) -> str:
    prefix = "til_nav_active_" if active else "til_nav_"
    return prefix + "".join(
        char.lower() if char.isalnum() else "_"
        for char in section
    ).strip("_")


def _route_loading_label(section: str) -> str:
    meta = WORKSPACE_PAGE_META.get(section) or {}
    title = str(meta.get("title") or section or "workspace").strip()
    return f"Loading {title}…"


def navigate_to_workspace(
    section: str,
    *,
    pending: bool = False,
) -> None:
    if pending:
        st.session_state["til_navigate_to"] = section
    else:
        st.session_state["til_workspace_section"] = section
    st.session_state["_trading_app_boot_message"] = _route_loading_label(section)
    st.rerun()


def prime_action_feedback(message: str) -> None:
    """Show feedback before Streamlit begins an expensive full rerun."""
    st.session_state["_trading_app_boot_message"] = str(message or "Working…")


def queue_workspace_navigation(section: str) -> None:
    """Route from a button callback so loading feedback appears immediately."""
    if section == "Stock Analyzer":
        # A normal sidebar visit means "analyze freely", not "continue a Step-1 run".
        st.session_state.pop("til_guided_strategy_id", None)
        st.session_state.pop("til_guided_finder_run_id", None)
        st.session_state.pop("til_analyzer_strategy_id", None)
    if section == "Strategy Lab":
        st.session_state.pop("til_strategy_lab_candidate_payload", None)
        st.session_state.pop("til_guided_validation_mode", None)
    st.session_state["til_navigate_to"] = section
    prime_action_feedback(_route_loading_label(section))


def queue_strategy_validation_from_analyzer(
    symbol: str,
    strategy: dict[str, Any] | None,
) -> None:
    ticker = str(symbol or "").strip().upper()
    payload = dict(strategy or {})
    if ticker:
        st.session_state["til_strategy_lab_ticker"] = ticker
    st.session_state["til_guided_validation_mode"] = True
    finder_days = int(safe_float(payload.get("_finder_history_days"), 0) or 0)
    if finder_days:
        st.session_state["til_strategy_lab_history_days"] = max(7, min(180, finder_days))
    finder_timeframe = str(payload.get("_finder_timeframe") or "")
    if finder_timeframe in {"1Min", "5Min", "15Min"}:
        st.session_state["til_strategy_lab_timeframe"] = finder_timeframe
    if payload.get("id"):
        st.session_state["til_strategy_lab_candidate_payload"] = payload
        st.session_state["til_selected_strategy_id"] = str(payload.get("id") or "")
    st.session_state["til_navigate_to"] = "Strategy Lab"
    prime_action_feedback(f"Opening strict validation for {ticker or 'this stock'}…")


def queue_paper_test_from_analyzer(symbol: str, strategy_id: str = "") -> None:
    ticker = str(symbol or "").strip().upper()
    if strategy_id:
        st.session_state["til_selected_strategy_id"] = str(strategy_id)
    if ticker:
        st.session_state["til_analyzer_ticker"] = ticker
    st.session_state["til_navigate_to"] = "Live / Paper"
    prime_action_feedback(f"Opening paper testing for {ticker or 'this stock'}…")


def queue_stock_analyzer_from_finder(
    symbol: str,
    strategy_id: str = "",
    finder_run_id: str = "",
) -> None:
    ticker = str(symbol or "").strip().upper()
    guided_strategy_id = str(strategy_id or "").strip()
    guided_run_id = str(finder_run_id or "").strip()
    if ticker:
        st.session_state["til_analyzer_ticker"] = ticker
    if guided_strategy_id:
        st.session_state["til_guided_strategy_id"] = guided_strategy_id
    else:
        st.session_state.pop("til_guided_strategy_id", None)
    if guided_run_id:
        st.session_state["til_guided_finder_run_id"] = guided_run_id
    else:
        st.session_state.pop("til_guided_finder_run_id", None)
    st.session_state.pop("til_analyzer_strategy_id", None)
    st.session_state["til_navigate_to"] = "Stock Analyzer"
    prime_action_feedback(f"Checking {ticker or 'stock'} current signal…")


def render_guided_strategy_flow(active_step: int = 1) -> None:
    steps = [
        (1, "Search", "Test strategy families historically for this stock."),
        (2, "Compare", "Review the ranked candidates from the search."),
        (3, "Validate", "Try to break the selected candidate on unseen data."),
        (4, "Current Setup", "Check whether a validated setup is present now."),
        (5, "Paper Test", "Track real-time results before risking real money."),
    ]
    cards = []
    current = max(1, min(5, int(active_step or 1)))
    for number, title, detail in steps:
        state = "complete" if number < current else "active" if number == current else ""
        cards.append(
            f'<div class="til-guided-step {state}">'
            f'<span class="til-guided-step-num">{"✓" if number < current else number}</span>'
            f'<strong>{html.escape(title)}</strong>'
            f'<small>{html.escape(detail)}</small>'
            '</div>'
        )
    st.markdown(
        '<div class="til-guided-flow">'
        '<div class="til-guided-flow-title">YOUR STRATEGY WORKFLOW</div>'
        '<div class="til-guided-flow-grid">' + "".join(cards) + '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


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

try:
    remembered_workspace = str(st.query_params.get("workspace") or "").strip()
except (AttributeError, KeyError, RuntimeError, TypeError):
    remembered_workspace = ""
remembered_workspace = WORKSPACE_DISPLAY_TO_INTERNAL.get(
    remembered_workspace,
    remembered_workspace,
)

if requested_workspace in WORKSPACE_SECTIONS:
    st.session_state["til_workspace_section"] = requested_workspace
elif (
    "til_workspace_section" not in st.session_state
    and remembered_workspace in WORKSPACE_SECTIONS
):
    st.session_state["til_workspace_section"] = remembered_workspace

module = str(st.session_state.get("til_workspace_section") or "Overview")
if module not in WORKSPACE_SECTIONS:
    module = "Overview"
    st.session_state["til_workspace_section"] = module

try:
    if str(st.query_params.get("workspace") or "") != module:
        st.query_params["workspace"] = module
except (AttributeError, KeyError, RuntimeError, TypeError):
    pass

library: dict[str, Any] = {}
library_load_error: AppError | None = None
_library_cold_start = not isinstance(
    st.session_state.get(_LIBRARY_RENDER_CACHE_KEY),
    dict,
)
_library_status_factory = getattr(st, "status", None)
_library_load_status = (
    _boot_status
    if _library_cold_start and _boot_status is not None
    else (
        _library_status_factory("Loading research library…", expanded=False)
        if _library_cold_start and callable(_library_status_factory)
        else None
    )
)
if _library_load_status is not None and _library_cold_start:
    _library_load_status.update(
        label="Loading research library…",
        expanded=False,
    )
elif _library_cold_start:
    # Lightweight/test Streamlit shims may not implement st.status.
    st.info("Loading research library…")
try:
    # Refresh durable storage before deriving health state so a stale local
    # cloud_backup_status.json error cannot keep cloud research disabled after
    # a newer worker/save has already repaired the private GitHub library.
    library = load_library()
    if _library_load_status is not None:
        _library_load_status.update(
            label="Trading Intelligence Lab ready",
            state="complete",
            expanded=False,
        )
    st.session_state.pop("_trading_app_boot_message", None)
except AppError as exc:
    library_load_error = exc
    if _library_load_status is not None:
        _library_load_status.update(
            label="Research library could not load",
            state="error",
            expanded=True,
        )

system_config_checks = configuration_checks(
    setting,
    backup_repository=resolved_backup_repository(),
)
system_config_summary = overall_system_state(system_config_checks)
stock_cloud_ready, stock_cloud_blockers = subsystem_ready(
    system_config_checks,
    "stock_finder",
)
actions_token_setting = setting("GITHUB_ACTIONS_TOKEN")
actions_repository_setting = setting(
    "GITHUB_ACTIONS_REPOSITORY",
    "derektshaffer/youtube-trading-strategy-lab-public",
)
actions_ref_setting = setting("GITHUB_ACTIONS_REF", "main")
system_status_word = str(system_config_summary.get("state") or "DEGRADED")
persistence_snapshot = intelligence_store().persistence_status(verify=False)
known_persistence_error = str(persistence_snapshot.get("last_error") or "").strip()
if known_persistence_error:
    system_status_word = "DEGRADED"
    stock_cloud_ready = False
    stock_cloud_blockers = [
        *stock_cloud_blockers,
        "Private durable storage has a recorded error: " + known_persistence_error,
    ]

with st.sidebar:
    st.markdown(
        """
        <div class="til-sidebrand">
          <div class="til-sidebrand-mark til-crystal-logo">
            <svg viewBox="0 0 64 64" aria-hidden="true">
              <defs>
                <linearGradient id="tilCrystalA" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0%" stop-color="#55dcff"/>
                  <stop offset="100%" stop-color="#42eca0"/>
                </linearGradient>
                <linearGradient id="tilCrystalB" x1="1" y1="0" x2="0" y2="1">
                  <stop offset="0%" stop-color="#30aeea"/>
                  <stop offset="100%" stop-color="#59f0bf"/>
                </linearGradient>
              </defs>
              <polygon points="32,3 54,17 59,42 39,60 14,54 5,30 15,10" fill="rgba(22,110,145,.13)" stroke="url(#tilCrystalA)" stroke-width="1.2"/>
              <polygon points="32,3 39,60 5,30 54,17 14,54 15,10 59,42 32,3" fill="none" stroke="url(#tilCrystalB)" stroke-width=".9" opacity=".88"/>
              <polyline points="15,10 32,31 54,17 39,60 32,31 5,30 32,3 32,31 59,42 14,54 32,31" fill="none" stroke="#67e7ff" stroke-width=".75" opacity=".74"/>
              <g fill="#6cf2c0">
                <circle cx="32" cy="3" r="1.8"/><circle cx="54" cy="17" r="1.6"/><circle cx="59" cy="42" r="1.6"/>
                <circle cx="39" cy="60" r="1.6"/><circle cx="14" cy="54" r="1.6"/><circle cx="5" cy="30" r="1.6"/>
                <circle cx="15" cy="10" r="1.6"/><circle cx="32" cy="31" r="2"/>
              </g>
            </svg>
          </div>
          <div>
            <div class="til-sidebrand-name">Trading<br>Intelligence<br>Lab</div>
          </div>
        </div>
        <div class="til-workspace-label">WHAT DO YOU WANT TO DO?</div>
        """,
        unsafe_allow_html=True,
    )

    primary_navigation = [
        ("Overview", "⌂  Home"),
        ("Stock Strategy Finder", "①  Find & test a strategy"),
        ("Market Discovery", "◎  Find stocks worth watching"),
        ("Knowledge Sources", "◇  Add research material"),
        ("AI Research Autopilot", "✦  AI discoveries & research"),
        ("Live / Paper", "↗  Paper & live trading"),
    ]
    for section, label in primary_navigation:
        is_active = section == module
        if is_active:
            st.button(
                label,
                width="stretch",
                type="primary",
                key="til_simple_nav_" + _nav_key(section, active=True),
                disabled=True,
            )
        else:
            st.button(
                label,
                width="stretch",
                type="secondary",
                key="til_simple_nav_" + _nav_key(section),
                on_click=queue_workspace_navigation,
                args=(section,),
            )

    st.caption("Steps 2–5 appear automatically after Step 1 finds a strategy.")

    advanced_sections = [
        "Stock Analyzer",
        "Strategy Library",
        "Strategy Integrity",
        "Retrospective Learning",
        "Strategy DNA",
        "Make Strategy Testable",
        "Strategy Lab",
        "Validation",
        "Universe Research",
        "Pattern Validation",
        "Catalyst Intelligence",
        "System Health",
    ]
    with st.expander(
        "Advanced / Research Details",
        expanded=module in advanced_sections,
    ):
        st.caption("These pages explain or audit what the AI pipeline is doing. You usually do not need them.")
        for section in advanced_sections:
            meta = WORKSPACE_PAGE_META.get(section) or {}
            is_active = section == module
            advanced_label = (
                "⌕  Standalone Stock Analyzer"
                if section == "Stock Analyzer"
                else WORKSPACE_NAV_ICONS.get(section, "◇") + "  " + str(meta.get("title") or section)
            )
            if is_active:
                st.button(
                    advanced_label,
                    width="stretch",
                    type="primary",
                    key="til_advanced_nav_" + _nav_key(section, active=True),
                    disabled=True,
                )
            else:
                st.button(
                    advanced_label,
                    width="stretch",
                    type="secondary",
                    key="til_advanced_nav_" + _nav_key(section),
                    on_click=queue_workspace_navigation,
                    args=(section,),
                )

    system_status_color = "#47dda0" if system_status_word == "READY" else "#f3bd58"
    st.markdown(
        f"""
        <div class="til-side-status">
          <div class="til-side-status-row">
            <div>
              <div class="til-side-status-label">AI RESEARCH SYSTEM</div>
              <div class="til-side-status-value"><span class="til-live-dot" style="background:{system_status_color};"></span> {html.escape(system_status_word)}</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


if library_load_error is not None:
    st.error(str(library_load_error))
    st.stop()

if st.session_state.pop("_til_cloud_conflict_recovered", False):
    st.warning(
        "Storage conflict recovered safely. The previous local working copy was preserved "
        "in an automatic backup, and the durable private GitHub library was restored as "
        "the active copy so the workspace could finish loading."
    )

strategies = list(library.get("strategies") or [])
sources = list(library.get("knowledge_sources") or [])
source_strategies = [item for item in strategies if is_family_source_strategy(item)]
canonical_strategies = [
    item
    for item in strategies
    if str(item.get("source_type") or "").lower() == "canonical_family"
]
managed_strategies = canonical_strategies or source_strategies
stock_specific_strategies = [
    item
    for item in strategies
    if str(item.get("source_type") or "").strip().casefold()
    == "stock_specific_finder"
]
downstream_strategies = [*stock_specific_strategies, *managed_strategies]

_integrity_signature = (
    str(library.get("updated_at") or ""),
    tuple(
        str(item.get("id") or item.get("name") or "")
        for item in downstream_strategies
    ),
)
_integrity_cache = st.session_state.get("_til_strategy_integrity_cache")
if (
    isinstance(_integrity_cache, dict)
    and _integrity_cache.get("signature") == _integrity_signature
    and isinstance(_integrity_cache.get("reports"), dict)
):
    downstream_integrity_reports = _integrity_cache["reports"]
else:
    downstream_integrity_reports = {
        str(item.get("id") or item.get("name") or ""): strategy_integrity_report(item)
        for item in downstream_strategies
    }
    st.session_state["_til_strategy_integrity_cache"] = {
        "signature": _integrity_signature,
        "reports": downstream_integrity_reports,
    }

managed_integrity_reports = {
    str(item.get("id") or item.get("name") or ""): downstream_integrity_reports.get(
        str(item.get("id") or item.get("name") or ""),
        {},
    )
    for item in managed_strategies
}
integrity_safe_strategies = [
    item
    for item in downstream_strategies
    if str(
        (
            downstream_integrity_reports.get(
                str(item.get("id") or item.get("name") or "")
            )
            or {}
        ).get("status")
        or ""
    ) != "blocked"
]
integrity_blocked_count = sum(
    1
    for report in managed_integrity_reports.values()
    if str((report or {}).get("status") or "") == "blocked"
)

top_gap, top_search, top_actions = st.columns([2.55, 1.35, .72], vertical_alignment="center")
with top_gap:
    top_status_label = "ONLINE" if system_status_word == "READY" else "DEGRADED"
    st.markdown(
        '<div class="til-top-status"><span class="til-top-status-dot"></span>'
        f'AI RESEARCH SYSTEM <strong>{html.escape(top_status_label)}</strong></div>',
        unsafe_allow_html=True,
    )
with top_search:
    workspace_search_query = st.text_input(
        "Search workspace",
        placeholder="⌕  Search workspace…",
        label_visibility="collapsed",
        key="til_workspace_search",
    ).strip()
with top_actions:
    st.markdown(
        """
        <div class="til-top-actions">
          <div class="til-top-icon til-notify">♢<span></span></div>
          <div class="til-top-icon">?</div>
          <div class="til-avatar">AI</div>
          <div class="til-top-chevron">⌄</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

render_workspace_page_header(module)

if workspace_search_query:
    query = workspace_search_query.casefold()
    page_matches = [
        section
        for section in WORKSPACE_SECTIONS
        if query in WORKSPACE_DISPLAY_LABELS.get(section, section).casefold()
        or query in str((WORKSPACE_PAGE_META.get(section) or {}).get("subtitle") or "").casefold()
    ]
    source_matches = [
        item
        for item in sources
        if query in str(item.get("title") or "").casefold()
        or query in str(item.get("author") or "").casefold()
        or query in str(item.get("summary") or "").casefold()
    ][:5]
    strategy_matches = [
        item
        for item in managed_strategies
        if query in str(item.get("name") or "").casefold()
        or query in str(item.get("summary") or "").casefold()
    ][:5]
    match_count = len(page_matches) + len(source_matches) + len(strategy_matches)

    with st.expander(f"⌕ Search results · {match_count}", expanded=True):
        if page_matches:
            st.caption("WORKSPACE")
            cols = st.columns(min(4, len(page_matches)))
            for index, section in enumerate(page_matches):
                if cols[index % len(cols)].button(
                    WORKSPACE_DISPLAY_LABELS.get(section, section),
                    key=f"til_search_page_{_nav_key(section)}",
                    width="stretch",
                ):
                    st.session_state["til_workspace_search"] = ""
                    navigate_to_workspace(section)
        if source_matches:
            st.caption("SOURCES")
            for item in source_matches:
                if st.button(
                    f"◇ {item.get('title') or 'Untitled source'}"
                    + (f" · {item.get('author')}" if item.get("author") else ""),
                    key=f"til_search_source_{str(item.get('id') or item.get('ingest_id') or '')}",
                    width="stretch",
                ):
                    st.session_state["til_workspace_search"] = ""
                    navigate_to_workspace("Knowledge Sources")
        if strategy_matches:
            st.caption("STRATEGY FAMILIES")
            for item in strategy_matches:
                if st.button(
                    f"✦ {item.get('name') or 'Unnamed strategy'}",
                    key=f"til_search_strategy_{str(item.get('id') or '')}",
                    width="stretch",
                ):
                    st.session_state["til_selected_strategy_id"] = str(item.get("id") or "")
                    st.session_state["til_workspace_search"] = ""
                    navigate_to_workspace("Strategy Library")
        if not match_count:
            st.caption("No workspace pages, sources, or strategy families match that search.")


if module == "Stock Strategy Finder":
    st.markdown(
        """
        <div class="til-finder-intro">
          <div class="til-finder-intro-icon">◆</div>
          <div>
            <div class="til-finder-intro-title">Find and test a strategy — one stock at a time</div>
            <div class="til-finder-intro-copy">
              You choose the stock. The Lab searches strategy families, optimizes the strongest candidates,
              and automatically tests the winner on unseen data. The technical research stays underneath;
              the workflow below tells you what to do next.
            </div>
          </div>
          <div class="til-finder-policy"><span>AI VETO</span><strong>OFF</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_guided_strategy_flow(active_step=1)
    st.markdown("### Step 1 — Find & test a strategy")
    st.caption("Choose the ticker and search depth. Deep is the normal thorough search; Recent Behavior is faster and focuses on roughly the latest month.")

    def render_recent_completed_cloud_runs(fresh_library: dict[str, Any]) -> None:
        recent_cloud_runs = [
            item
            for item in fresh_library.get("stock_strategy_finder_runs") or []
            if isinstance(item, dict)
            and (
                bool((item.get("distributed") or {}).get("enabled"))
                or str(item.get("parallelized_by") or "")
                == "distributed_strategy_family_timeframe"
            )
        ]
        recent_cloud_runs.sort(
            key=lambda item: str(item.get("generated_at") or ""),
            reverse=True,
        )
        if recent_cloud_runs:
            st.markdown("### Recent completed cloud research")
            for summary in recent_cloud_runs[:3]:
                completed_symbol = str(summary.get("symbol") or "Stock").strip().upper()
                completed_profile = str(summary.get("profile") or "Research").strip()
                completed_configs = int(summary.get("unique_configurations_tested") or 0)
                distributed = dict(summary.get("distributed") or {})
                completed_shards = int(distributed.get("shard_count") or 0)
                completed_at = str(summary.get("generated_at") or "").strip()
                st.markdown(
                    (
                        '<div class="til-finder-notice til-finder-complete-note">'
                        f'<div class="til-finder-notice-title">☁ {html.escape(completed_symbol)} · '
                        f'{html.escape(completed_profile)} · CLOUD COMPLETE</div>'
                        '<div class="til-finder-notice-body">'
                        f'{completed_configs:,} configurations tested'
                        + (f' · {completed_shards} distributed shards' if completed_shards else '')
                        + (f' · completed {html.escape(completed_at)}' if completed_at else '')
                        + '. This result stays visible even when the controls below are set to another depth.'
                        '</div></div>'
                    ),
                    unsafe_allow_html=True,
                )
                if st.button(
                    f"Open {completed_symbol} {completed_profile} result",
                    key=(
                        "til_open_completed_cloud_"
                        + "".join(
                            char.lower() if char.isalnum() else "_"
                            for char in f"{completed_symbol}_{completed_profile}_{completed_at}"
                        ).strip("_")
                    ),
                    width="stretch",
                ):
                    st.session_state["til_pending_finder_symbol"] = completed_symbol
                    if completed_profile in SEARCH_PROFILES:
                        st.session_state["til_pending_finder_profile"] = completed_profile
                    st.session_state["_trading_app_boot_message"] = (
                        f"Loading {completed_symbol} {completed_profile} Finder result…"
                    )
                    st.rerun()



    @st.fragment(run_every="60s")
    def render_global_cloud_finder_activity() -> None:
        """Auto-refresh cloud Finder status only while a job is active."""
        try:
            last_refresh = float(
                st.session_state.get(_LIBRARY_LAST_CLOUD_REFRESH_KEY) or 0.0
            )
            prepared_cache = st.session_state.get(_LIBRARY_RENDER_CACHE_KEY)
            prepared_data = (
                prepared_cache.get("data")
                if isinstance(prepared_cache, dict)
                else None
            )
            if (
                isinstance(prepared_data, dict)
                and time.monotonic() - last_refresh < 10.0
            ):
                # The parent page just loaded the durable library. Reuse it for
                # the first Finder paint instead of immediately downloading the
                # same large library a second time.
                fresh_library = prepared_data
            else:
                # Later fragment-only refreshes want raw queue/result state and
                # intentionally avoid rebuilding strategy families.
                fresh_library = load_cloud_status_library()
        except AppError as exc:
            st.error(f"Cloud research status could not refresh: {exc}")
            return

        render_recent_completed_cloud_runs(fresh_library)

        active_jobs = [
            item
            for item in fresh_library.get("research_queue") or []
            if isinstance(item, dict)
            and str(item.get("type") or "") == "stock_finder"
            and str(item.get("status") or "") in {"queued", "running", "retry"}
        ]
        if not active_jobs:
            st.rerun()

        active_jobs.sort(
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )
        st.markdown("### Active cloud research")
        actions_configured = bool(actions_token_setting)

        for index, job in enumerate(active_jobs[:4]):
            payload = dict(job.get("payload") or {})
            symbol = str(payload.get("symbol") or "Stock").strip().upper()
            profile_name = str(payload.get("profile") or "Research").strip()
            durable_status = str(job.get("status") or "queued").replace("_", " ").upper()
            job_id = str(job.get("id") or "")
            is_waiting_for_worker = (
                durable_status == "QUEUED"
                and not payload.get("distributed_run_id")
            )
            auto_attempt_key = f"til_auto_cloud_launch_attempted_{job_id}"
            auto_result_key = f"til_auto_cloud_launch_result_{job_id}"

            # Rendering this page is intentionally side-effect free. New cloud
            # jobs are launched by the queue action itself; recovery launches
            # happen only when the user explicitly presses Retry.
            auto_result = st.session_state.get(auto_result_key) or {}
            operational = cloud_job_display_state(
                job,
                actions_configured=actions_configured,
                launch_result=auto_result,
            )
            operational_state = str(operational.get("state") or durable_status)
            operational_detail = str(operational.get("detail") or "")
            total_shards = int(payload.get("distributed_shards_total") or 0)
            completed_shards = len(
                {
                    int(value)
                    for value in payload.get("distributed_shards_completed") or []
                    if str(value).lstrip("-").isdigit()
                }
            )
            stage = str(payload.get("distributed_stage") or operational_state).replace("_", " ").title()
            message = str(payload.get("distributed_message") or "").strip()
            failed_step = str(
                payload.get("distributed_failed_stage")
                or job.get("failure_step")
                or ""
            ).replace("_", " ").strip()
            last_error = str(
                payload.get("distributed_last_error")
                or job.get("last_error")
                or ""
            ).strip()
            next_attempt = str(job.get("next_attempt_at") or "").strip()
            if durable_status == "RETRY" and last_error:
                message = (
                    f"Retrying after {failed_step or 'the current step'} failed: {last_error}"
                    + (f" Next attempt: {next_attempt}." if next_attempt else "")
                )
            elif not message:
                message = str(job.get("status_message") or "").strip()
            progress_value = safe_float(payload.get("distributed_progress"), None)
            if progress_value is None:
                if is_waiting_for_worker:
                    progress_value = 0.0
                elif total_shards > 0:
                    progress_value = 0.10 + 0.75 * min(1.0, completed_shards / total_shards)
                else:
                    progress_value = 0.06
            progress_value = max(0.0, min(0.99, float(progress_value)))
            shard_text = (
                f" · {completed_shards}/{total_shards} shards complete"
                if total_shards > 0
                else ""
            )

            if operational_state in {"RUNNING", "STARTING"}:
                close_note = "SAFE TO CLOSE YOUR COMPUTER"
            elif operational_state == "STALLED":
                close_note = "ACTION REQUIRED"
            else:
                close_note = "CLOUD QUEUE SAVED"

            st.markdown(
                (
                    '<div class="til-finder-notice til-finder-cloud-note">'
                    f'<div class="til-finder-notice-title">☁ {html.escape(symbol)} · {html.escape(profile_name)} '
                    f'· CLOUD {html.escape(operational_state)} · {html.escape(close_note)}</div>'
                    '<div class="til-finder-notice-body">'
                    'This cloud run is independent of the stock/search-depth controls below. '
                    'Changing those controls or leaving this page does not delete the saved job.'
                    f'{html.escape(shard_text)}'
                    '</div></div>'
                ),
                unsafe_allow_html=True,
            )

            if is_waiting_for_worker:
                progress_title = f"{symbol} {profile_name} · {operational_state}"
                progress_detail = operational_detail
                bar_html = '<div class="til-cloud-progress-queued"></div>'
                progress_right = operational_state
            else:
                progress_title = (
                    f"{symbol} {profile_name} · {progress_value * 100:.0f}% · {stage}{shard_text}"
                )
                progress_detail = message or operational_detail or "Cloud compute is active."
                bar_html = (
                    f'<div class="til-cloud-progress-fill" '
                    f'style="width:{progress_value * 100:.2f}%"></div>'
                )
                progress_right = f"{progress_value * 100:.0f}%"

            st.markdown(
                (
                    '<div class="til-cloud-progress-wrap">'
                    '<div class="til-cloud-progress-meta">'
                    f'<span>{html.escape(progress_title)}</span>'
                    f'<span>{html.escape(progress_right)}</span>'
                    '</div>'
                    '<div class="til-cloud-progress-track">'
                    f'{bar_html}'
                    '</div>'
                    f'<div class="til-cloud-progress-sub">{html.escape(progress_detail)}</div>'
                    '</div>'
                ),
                unsafe_allow_html=True,
            )

            if operational_state == "STALLED":
                st.error(operational_detail)
            elif is_waiting_for_worker and auto_result and not auto_result.get("ok"):
                st.warning(operational_detail)
            elif is_waiting_for_worker and not actions_configured:
                st.warning(
                    "Immediate cloud launch is not configured. Open **14. System Health** before relying on this queued job."
                )
            elif is_waiting_for_worker and auto_result.get("ok"):
                st.info(operational_detail)

            if is_waiting_for_worker:
                launch_key = f"til_launch_cloud_now_{job_id}"
                retry_launch_slot = st.empty()
                retry_launch = retry_launch_slot.button(
                    "☁ Retry cloud launch now",
                    key=launch_key,
                    width="stretch",
                    disabled=not actions_configured,
                    help=(
                        "Retries the immediate GitHub Actions launch for this exact queued job. "
                        "System Health must show the Actions launcher as configured."
                    ),
                )
                if retry_launch:
                    retry_launch_slot.button(
                        "☁ Starting cloud worker…",
                        key=f"{launch_key}_busy",
                        width="stretch",
                        disabled=True,
                    )
                    launch_ok, launch_detail = dispatch_github_workflow(
                        actions_repository_setting,
                        actions_token_setting,
                        workflow="distributed-stock-finder.yml",
                        ref=actions_ref_setting,
                        inputs={"job_id": job_id},
                    )
                    st.session_state[auto_result_key] = {
                        "ok": bool(launch_ok),
                        "detail": str(launch_detail),
                    }
                    if launch_ok:
                        st.success(
                            "Immediate cloud launch requested. The status monitor will confirm when a worker actually claims the job."
                        )
                    else:
                        st.warning(launch_detail)

            if index < min(3, len(active_jobs) - 1):
                st.caption("")

        if len(active_jobs) > 4:
            st.caption(f"{len(active_jobs) - 4} additional cloud Finder job(s) are also queued.")

    _initial_active_cloud_finders = [
        item
        for item in library.get("research_queue") or []
        if isinstance(item, dict)
        and str(item.get("type") or "") == "stock_finder"
        and str(item.get("status") or "") in {"queued", "running", "retry"}
    ]
    if _initial_active_cloud_finders:
        render_global_cloud_finder_activity()
    else:
        render_recent_completed_cloud_runs(library)

    pending_finder_profile = str(
        st.session_state.pop("til_pending_finder_profile", "") or ""
    )
    if pending_finder_profile in SEARCH_PROFILES:
        # Apply requested values before their widgets are instantiated.
        st.session_state["til_finder_profile"] = pending_finder_profile
        st.session_state["til_finder_profile_persisted"] = pending_finder_profile
    pending_finder_symbol = str(
        st.session_state.pop("til_pending_finder_symbol", "") or ""
    ).strip().upper()
    if pending_finder_symbol:
        st.session_state["til_finder_symbol"] = pending_finder_symbol
        st.session_state["til_finder_symbol_persisted"] = pending_finder_symbol

    finder_profile_options = list(SEARCH_PROFILES)
    finder_profile_default = str(
        st.session_state.get("til_finder_profile_persisted") or "Deep"
    )
    if finder_profile_default not in SEARCH_PROFILES:
        finder_profile_default = "Deep"
    if "til_finder_symbol" not in st.session_state:
        st.session_state["til_finder_symbol"] = str(
            st.session_state.get("til_finder_symbol_persisted") or "SDOT"
        )
    if "til_finder_profile" not in st.session_state:
        st.session_state["til_finder_profile"] = finder_profile_default

    finder_a, finder_b = st.columns([1.15, 1.0])
    with finder_a:
        finder_symbol = st.text_input(
            "Ticker",
            placeholder="SDOT",
            max_chars=10,
            key="til_finder_symbol",
            help="Enter one stock. The strategy search is optimized and validated specifically for this ticker.",
        ).strip().upper()
    with finder_b:
        finder_profile_name = st.selectbox(
            "Search depth",
            finder_profile_options,
            key="til_finder_profile",
            format_func=lambda value: (
                "Recent Behavior (faster)"
                if str(value) == "Current Regime"
                else "Deep (recommended)"
                if str(value) == "Deep"
                else "Very Deep (slowest)"
                if str(value) == "Very Deep"
                else str(value)
            ),
            help=(
                "Deep uses a longer history. Recent Behavior focuses on roughly the latest month "
                "for stocks whose behavior changes quickly. Very Deep can take substantially longer."
            ),
        )

    st.session_state["til_finder_symbol_persisted"] = finder_symbol
    st.session_state["til_finder_profile_persisted"] = finder_profile_name

    finder_profile = search_profile(finder_profile_name)
    finder_profile_display = (
        "Recent Behavior"
        if str(finder_profile.name) == "Current Regime"
        else str(finder_profile.name)
    )
    finder_family_strategies = stock_finder_strategy_families(strategies)
    finder_candidates, finder_skips = selected_strategies_for_profile(
        finder_family_strategies,
        finder_symbol or "UNKNOWN",
        finder_profile,
        integrity_reports=managed_integrity_reports,
    )
    finder_work = estimate_search_work(finder_profile, len(finder_candidates))

    st.markdown(
        (
            '<div class="til-finder-stats">'
            f'<div><span>STRATEGY FAMILIES</span><strong>{len(finder_candidates):,}</strong><em>{"all eligible" if finder_profile.quick_family_limit is None else "diversity sample"}</em></div>'
            f'<div><span>TIMEFRAMES</span><strong>{len(finder_profile.timeframes)}</strong><em>{" · ".join(finder_profile.timeframes)}</em></div>'
            f'<div><span>MIN. SEARCH SIMULATIONS</span><strong>{int(finder_work.get("minimum_estimated_simulations") or 0):,}</strong><em>before walk-forward + stability</em></div>'
            f'<div><span>HISTORY</span><strong>{finder_profile.history_days}</strong><em>calendar days</em></div>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )
    st.caption(finder_profile.description)
    if finder_profile.quick_family_limit is None:
        st.markdown(
            (
                '<div class="til-finder-notice til-finder-policy-note">'
                '<div class="til-finder-notice-title">SEARCH POLICY · FULL FAMILY COVERAGE</div>'
                '<div class="til-finder-notice-body">'
                'Every technically executable long strategy family is included. AI may prioritize search order, '
                'but it cannot reject a valid family or combination because it looks unconventional.'
                '</div></div>'
            ),
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            (
                '<div class="til-finder-notice til-finder-policy-note">'
                '<div class="til-finder-notice-title">SEARCH POLICY · QUICK MODE</div>'
                '<div class="til-finder-notice-body">'
                'Quick mode limits the family count for speed but samples round-robin across behavior buckets. '
                'Use Deep when you do not want the family cap.'
                '</div></div>'
            ),
            unsafe_allow_html=True,
        )

    with st.expander("What gets tested", expanded=False):
        st.write(
            "Rule thresholds, VWAP/EMA behavior, pullback and breakout conditions, volume requirements, "
            "session timing, stops, reward/risk, holding time, execution behavior, position sizing, extended-hours "
            "behavior, source-supported alternatives, AI research assumptions, and local refinements around promising regions."
        )
        st.write(
            "**Only technical impossibilities are skipped** — for example short-only rules in the current long-only "
            "backtester, a strategy explicitly locked to another ticker, or a strategy with no machine-testable rules."
        )
        if finder_skips:
            st.caption(f"{len(finder_skips)} technically ineligible library record(s) will be skipped.")

    saved_finder_checkpoint = latest_finder_checkpoint(
        library,
        finder_symbol,
        finder_profile.name,
    )
    session_finder_result = st.session_state.get("til_stock_strategy_finder_result") or {}
    session_result_matches = (
        str(session_finder_result.get("symbol") or "").upper() == finder_symbol
        and str((session_finder_result.get("profile") or {}).get("name") or "") == finder_profile.name
    )
    saved_finder_result = latest_completed_finder_report(
        library,
        finder_symbol,
        finder_profile.name,
    )
    latest_symbol_finder_result = latest_completed_finder_report(
        library,
        finder_symbol,
    )
    finder_result = newest_matching_finder_report(
        session_finder_result if session_result_matches else {},
        saved_finder_result,
        finder_symbol,
        finder_profile.name,
    )

    checkpoint_status = str((saved_finder_checkpoint or {}).get("status") or "").lower()
    checkpoint_engine_state = dict((saved_finder_checkpoint or {}).get("engine_state") or {})
    checkpoint_timeframes = dict(checkpoint_engine_state.get("timeframes") or {})
    checkpoint_family_passes = sum(
        len((state or {}).get("rankings") or [])
        for state in checkpoint_timeframes.values()
        if isinstance(state, dict)
    )
    checkpoint_resumable = (
        _finder_supports_resume
        and checkpoint_status in {"running", "failed", "interrupted"}
        and bool(checkpoint_timeframes)
        and checkpoint_family_passes > 0
    )

    if checkpoint_resumable:
        st.markdown(
            (
                '<div class="til-finder-notice til-finder-checkpoint-note">'
                '<div class="til-finder-notice-title">SAVED LOCAL CHECKPOINT</div>'
                f'<div class="til-finder-notice-body">{html.escape(finder_symbol)} {html.escape(finder_profile_display)} '
                f'has {checkpoint_family_passes:,} completed strategy-family/timeframe passes. '
                'Resume Locally will reuse them instead of starting those completed passes over.'
                '</div></div>'
            ),
            unsafe_allow_html=True,
        )
    elif checkpoint_timeframes and not _finder_supports_resume:
        st.warning(
            "A saved local Finder checkpoint is intact, but this Streamlit process still has the "
            "pre-resume helper loaded from before the deploy. Refresh after the app process restarts; "
            "the checkpoint has not been discarded."
        )
    elif checkpoint_status in {"running", "interrupted"}:
        st.warning(
            f"The previous {finder_symbol} {finder_profile_display} run did not finish before its first "
            "reusable optimizer checkpoint. The next Research click will restart that run from the beginning."
        )
    elif checkpoint_status == "failed":
        last_error = str((saved_finder_checkpoint or {}).get("last_error") or "").strip()
        st.error(
            f"The previous {finder_symbol} run stopped before a resumable optimizer checkpoint was available."
            + (f" Last error: {last_error}" if last_error else "")
        )
    elif saved_finder_result and not session_result_matches:
        st.info(
            f"Loaded the last completed {finder_symbol} {finder_profile_display} research result from durable storage."
        )
    elif not finder_result:
        latest_profile_name = str(
            (latest_symbol_finder_result.get("profile") or {}).get("name")
            or ""
        ).strip()
        latest_profile_display = (
            "Recent Behavior"
            if latest_profile_name == "Current Regime"
            else latest_profile_name
        )
        if latest_symbol_finder_result and latest_profile_name:
            st.info(
                f"**No {finder_profile_display} result exists yet for {finder_symbol}.** "
                f"Run this search below to create one. Your latest saved {finder_symbol} result "
                f"is from **{latest_profile_display}**."
            )
        else:
            st.info(
                f"**No {finder_profile_display} result exists yet for {finder_symbol}.** "
                "Run this search below to create one."
            )

    active_cloud_finders = [
        item
        for item in library.get("research_queue") or []
        if isinstance(item, dict)
        and str(item.get("type") or "") == "stock_finder"
        and str(item.get("status") or "") in {"queued", "running", "retry"}
    ]

    active_cloud_finder = next(
        (
            item
            for item in library.get("research_queue") or []
            if isinstance(item, dict)
            and str(item.get("type") or "") == "stock_finder"
            and str(item.get("status") or "") in {"queued", "running", "retry"}
            and str((item.get("payload") or {}).get("symbol") or "").upper() == finder_symbol
            and str((item.get("payload") or {}).get("profile") or "") == finder_profile.name
        ),
        None,
    )
    if active_cloud_finder:
        st.markdown(
            (
                '<div class="til-finder-notice til-finder-cloud-note">'
                '<div class="til-finder-notice-title">☁ THIS SELECTION MATCHES THE ACTIVE CLOUD RUN</div>'
                f'<div class="til-finder-notice-body">{html.escape(finder_symbol)} {html.escape(finder_profile_display)} '
                'is already running in the cloud. '
                'The controls below are locked while that job is active.'
                '</div></div>'
            ),
            unsafe_allow_html=True,
        )
    elif active_cloud_finders:
        other = dict(active_cloud_finders[0] or {})
        other_payload = dict(other.get("payload") or {})
        other_symbol = str(other_payload.get("symbol") or "Stock").upper()
        other_profile = str(other_payload.get("profile") or "Research")
        other_profile_display = (
            "Recent Behavior" if other_profile == "Current Regime" else other_profile
        )
        st.caption(
            f"Other cloud research is running in the background: "
            f"{other_symbol} — {other_profile_display}. It does not affect this {finder_symbol} search."
        )
    else:
        st.markdown(
            (
                '<div class="til-finder-notice til-finder-local-note">'
                '<div class="til-finder-notice-title">◆ LOCAL SESSION MODE</div>'
                '<div class="til-finder-notice-body">'
                'A local Research/Resume run uses the Streamlit session, so keep this browser open while it is computing. '
                'For a run that continues with your Mac closed, use the ☁ Queue Distributed button.'
                '</div></div>'
            ),
            unsafe_allow_html=True,
        )

    if stock_cloud_ready:
        st.caption("Cloud runner: ready.")
    else:
        st.warning(
            "Cloud configuration preflight: **NOT READY**. "
            + " ".join(stock_cloud_blockers)
            + " Open **14. System Health** to verify the complete worker path."
        )

    st.markdown("### Run the strategy search")
    st.caption(
        "You do not need to run a separate validation page. The Finder automatically tests the winner on "
        "untouched holdout data, walk-forward periods, higher execution costs, and nearby parameter settings."
    )

    cloud_col, local_col = st.columns([1.0, 1.35])
    with cloud_col:
        cloud_finder_slot = st.empty()
        queue_cloud_finder = cloud_finder_slot.button(
            f"☁ Run {finder_symbol or 'Stock'} — {finder_profile_display} in cloud",
            width="stretch",
            disabled=(
                not bool(finder_symbol)
                or not bool(finder_candidates)
                or active_cloud_finder is not None
                or not stock_cloud_ready
            ),
            key="til_queue_stock_strategy_finder_cloud",
            on_click=prime_action_feedback,
            args=(f"Starting {finder_symbol or 'stock'} {finder_profile_display} cloud strategy search…",),
            help=(
                "Queues the same stock-specific research for distributed cloud execution. "
                "Strategy-family/timeframe shards run independently, then one final holdout, "
                "walk-forward, and stability pass chooses the result. You can close this browser."
            ),
        )
    with local_col:
        st.caption(
            (
                (
                    "Recent Behavior is short enough to run here in the browser. "
                    "Use cloud if you want to close the browser while it runs."
                )
                if finder_profile.name == "Current Regime"
                else
                "Cloud is recommended for Deep/Very Deep runs because it can continue after you close the browser."
            )
            + " While cloud research is active, status refreshes automatically; when no cloud job is active, "
            "automatic refresh stops so the page stays still."
        )

    if queue_cloud_finder and finder_symbol:
        cloud_finder_slot.button(
            f"☁ Queuing {finder_symbol} — {finder_profile_display}…",
            width="stretch",
            disabled=True,
            key="til_queue_stock_strategy_finder_cloud_busy",
        )
        cloud_queue_status = st.status(
            f"Saving {finder_symbol} cloud research job and starting worker…",
            expanded=True,
        )
        queue_payload = {
            "symbol": finder_symbol,
            "profile": finder_profile.name,
            "requested_from": "Stock Strategy Finder",
        }
        queued_job = None
        queue_error = ""
        try:
            try:
                queued_library, queued_job = enqueue_research_job(
                    load_library(force_cloud_refresh=True, mutable=True),
                    "stock_finder",
                    queue_payload,
                    priority=90 if finder_profile.name == "Very Deep" else 75,
                    dedupe_key=f"stock-finder:{finder_symbol}:{finder_profile.name}",
                    max_attempts=2,
                )
            except AppError as exc:
                # Streamlit can hot-deploy this page while an older helper remains
                # cached. Load the current source under a private versioned name;
                # never replace a shared sys.modules entry during a page rerun.
                if "Unsupported research job type" not in str(exc):
                    raise
                _research_orchestrator = load_current_source_module(
                    "trading_research_orchestrator"
                )
                queued_library, queued_job = _research_orchestrator.enqueue_research_job(
                    load_library(force_cloud_refresh=True, mutable=True),
                    "stock_finder",
                    queue_payload,
                    priority=90 if finder_profile.name == "Very Deep" else 75,
                    dedupe_key=f"stock-finder:{finder_symbol}:{finder_profile.name}",
                    max_attempts=2,
                )
            if queued_job:
                intelligence_store().save(queued_library)
        except AppError as exc:
            queue_error = str(exc)

        if queue_error:
            cloud_queue_status.update(
                label="Cloud Finder queue failed",
                state="error",
                expanded=True,
            )
            st.error(
                "Cloud Finder could not confirm a durable queue update. "
                f"No automatic retry was started: {queue_error}"
            )
        elif queued_job:
            actions_token = setting("GITHUB_ACTIONS_TOKEN")
            actions_repository = setting(
                "GITHUB_ACTIONS_REPOSITORY",
                "derektshaffer/youtube-trading-strategy-lab-public",
            )
            actions_ref = setting("GITHUB_ACTIONS_REF", "main")
            launch_ok, launch_detail = dispatch_github_workflow(
                actions_repository,
                actions_token,
                workflow="distributed-stock-finder.yml",
                ref=actions_ref,
                inputs={"job_id": str(queued_job.get("id") or "")},
            )
            if launch_ok:
                cloud_queue_status.update(
                    label=f"{finder_symbol} cloud research launched",
                    state="complete",
                    expanded=False,
                )
                st.success(
                    f"{finder_symbol} {finder_profile_display} was queued **and the cloud worker was launched immediately**. "
                    "You can close your Mac or browser; the shards will run independently and the final result "
                    "will be saved back into the Finder when complete."
                )
            else:
                cloud_queue_status.update(
                    label=f"{finder_symbol} cloud job saved · worker launch needs attention",
                    state="error",
                    expanded=True,
                )
                st.warning(
                    f"{finder_symbol} {finder_profile_display} is safely queued, but instant launch was not available. "
                    f"{launch_detail} The scheduled worker is only a fallback; do not assume compute started "
                    "until this job changes to STARTING/RUNNING or System Health confirms the worker path."
                )
            st.rerun()
        else:
            cloud_queue_status.update(
                label="Cloud Finder job already queued or running",
                state="complete",
                expanded=False,
            )
            st.info("That cloud Finder job is already queued or running.")

    finder_slot = st.empty()
    finder_action = "Resume" if checkpoint_resumable else "Run"
    run_finder = finder_slot.button(
        f"◆ {finder_action} {finder_symbol or 'Stock'} — {finder_profile_display} here",
        type="primary",
        width="stretch",
        disabled=(
            not bool(finder_symbol)
            or not bool(finder_candidates)
            or active_cloud_finder is not None
        ),
        key="til_run_stock_strategy_finder",
        on_click=prime_action_feedback,
        args=(
            f"{'Resuming' if checkpoint_resumable else 'Starting'} {finder_symbol or 'stock'} {finder_profile_display} strategy search…",
        ),
    )

    if run_finder and finder_symbol:
        st.warning(
            "◆ **LOCAL SESSION RUNNING — KEEP THIS BROWSER OPEN**\n\n"
            "This run is executing in the current Streamlit session. "
            "If the session stops, the saved checkpoint will protect completed work, "
            "but computation will not continue until you resume it."
        )
        resume_engine_state = checkpoint_engine_state if checkpoint_resumable else {}
        now_iso = utc_now().isoformat()
        checkpoint_record = {
            "id": (
                str((saved_finder_checkpoint or {}).get("id") or "")
                if checkpoint_resumable
                else hashlib.sha256(
                    f"{finder_symbol}|{finder_profile.name}|{now_iso}".encode("utf-8")
                ).hexdigest()[:24]
            ),
            "symbol": finder_symbol,
            "profile": finder_profile.name,
            "status": "running",
            "started_at": (
                str((saved_finder_checkpoint or {}).get("started_at") or now_iso)
                if checkpoint_resumable else now_iso
            ),
            "updated_at": now_iso,
            "progress": safe_float((saved_finder_checkpoint or {}).get("progress"), 0.0) or 0.0,
            "message": (
                f"Resuming {finder_symbol} from durable optimizer checkpoint"
                if checkpoint_resumable
                else f"Preparing {finder_symbol} stock-specific research"
            ),
            "engine_state": resume_engine_state,
            "research_start": (
                (saved_finder_checkpoint or {}).get("research_start")
                if checkpoint_resumable else None
            ),
            "research_end": (
                (saved_finder_checkpoint or {}).get("research_end")
                if checkpoint_resumable else None
            ),
            "last_error": None,
        }

        finder_slot.button(
            f"◆ {'Resuming' if checkpoint_resumable else 'Researching'} {finder_symbol}…",
            type="primary",
            width="stretch",
            disabled=True,
            key="til_run_stock_strategy_finder_busy",
        )
        finder_monitor = long_task_monitor("stock_strategy_finder")
        finder_bar = st.progress(
            max(0.01, min(0.99, float(checkpoint_record["progress"]) or 0.01)),
            text=finder_monitor.text(
                max(0.01, min(0.99, float(checkpoint_record["progress"]) or 0.01)),
                str(checkpoint_record["message"]),
            ),
        )
        finder_status = st.status(
            (
                f"Resuming {finder_symbol} saved research…"
                if checkpoint_resumable
                else f"Building {finder_symbol} historical research set…"
            ),
            expanded=True,
        )
        checkpoint_store = intelligence_store()
        checkpoint_counter = [0]
        checkpoint_last_save = [time.monotonic()]
        checkpoint_save_warning = [False]

        def persist_finder_checkpoint(*, force: bool = False) -> None:
            if not force:
                checkpoint_counter[0] += 1
                if checkpoint_counter[0] % 4 != 0 and time.monotonic() - checkpoint_last_save[0] < 90:
                    return
            checkpoint_record["updated_at"] = utc_now().isoformat()
            checkpoint_data = checkpoint_store.load_latest()
            checkpoint_data = merge_finder_checkpoint_into_library(
                checkpoint_data,
                checkpoint_record,
            )
            checkpoint_store.save(checkpoint_data)
            checkpoint_last_save[0] = time.monotonic()

        try:
            market = market_client()
            saved_research_start = str(checkpoint_record.get("research_start") or "").strip()
            saved_research_end = str(checkpoint_record.get("research_end") or "").strip()
            if checkpoint_resumable and saved_research_start and saved_research_end:
                try:
                    finder_start = datetime.fromisoformat(saved_research_start.replace("Z", "+00:00"))
                    finder_end = datetime.fromisoformat(saved_research_end.replace("Z", "+00:00"))
                except ValueError:
                    finder_end = utc_now()
                    if market.historical_feed == "sip" and market.live_feed != "sip":
                        finder_end -= timedelta(minutes=16)
                    finder_start = finder_end - timedelta(days=finder_profile.history_days)
            else:
                finder_end = utc_now()
                if market.historical_feed == "sip" and market.live_feed != "sip":
                    finder_end -= timedelta(minutes=16)
                finder_start = finder_end - timedelta(days=finder_profile.history_days)
            checkpoint_record["research_start"] = finder_start.isoformat()
            checkpoint_record["research_end"] = finder_end.isoformat()

            # Freeze the exact research window before spending a long Deep run.
            # A later resume downloads the same bars, so optimizer fingerprints still match.
            persist_finder_checkpoint(force=True)

            def finder_history_progress(page: int) -> None:
                fraction = 0.03 + 0.12 * min(1.0, page / 100.0)
                checkpoint_record["progress"] = fraction
                checkpoint_record["message"] = f"Downloading {finder_symbol} 1-minute history · page {page}"
                update_task_bar(
                    finder_bar,
                    finder_monitor,
                    fraction,
                    str(checkpoint_record["message"]),
                )

            rows_by_symbol = market.bars(
                [finder_symbol],
                start=finder_start,
                end=finder_end,
                timeframe="1Min",
                adjustment="raw",
                max_pages=300,
                progress=finder_history_progress,
            )
            finder_rows = list(rows_by_symbol.get(finder_symbol) or [])
            if not finder_rows:
                raise AppError(f"No usable historical bars were returned for {finder_symbol}.")

            split_actions = market.research_reset_actions(
                [finder_symbol],
                start=finder_start,
                end=finder_end,
            )
            finder_rows, split_guard = split_safe_raw_research_rows(
                finder_rows,
                split_actions,
                finder_symbol,
            )
            if not finder_rows:
                raise AppError(
                    f"No split-safe raw-price history remained for {finder_symbol}."
                )
            checkpoint_record["market_data_integrity"] = split_guard
            if split_guard.get("corporate_action_reset_detected"):
                finder_status.write(
                    "Corporate-action integrity guard · raw prices preserved · "
                    f"research restarted at {split_guard.get('latest_split_date')} "
                    f"after discarding {int(split_guard.get('discarded_pre_split_rows') or 0):,} "
                    "pre-split candles"
                )

            finder_status.write(
                f"Historical bars ready · {len(finder_rows):,} split-safe raw-price one-minute candles · "
                f"{len(finder_candidates)} strategy families queued"
            )
            checkpoint_record["progress"] = max(float(checkpoint_record.get("progress") or 0.0), 0.16)
            checkpoint_record["message"] = f"Historical bars ready · {len(finder_rows):,} candles"
            update_task_bar(
                finder_bar,
                finder_monitor,
                0.16,
                str(checkpoint_record["message"]),
            )

            needs_catalyst_history = any(
                bool(normalize_machine_rules(item.get("machine_rules")).get("catalyst_required"))
                for item in finder_candidates
            )
            if needs_catalyst_history:
                finder_status.write("Loading point-in-time catalyst history for catalyst-aware strategies…")
                articles = historical_news(
                    market,
                    [finder_symbol],
                    start=finder_start - timedelta(hours=24),
                    end=finder_end,
                    max_pages=100,
                )
                finder_rows, catalyst_summary = enrich_bars_with_point_in_time_catalysts(
                    finder_rows,
                    articles,
                    lookback_hours=24.0,
                )
                finder_status.write(
                    f"Catalyst history attached · {len(articles):,} timestamped news item(s)"
                )

            def on_finder_progress(completed: int, total: int, message: str) -> None:
                portion = min(1.0, max(0.0, completed / max(1, total)))
                overall = 0.18 + 0.79 * portion
                checkpoint_record["progress"] = overall
                checkpoint_record["message"] = message
                update_task_bar(
                    finder_bar,
                    finder_monitor,
                    overall,
                    message,
                )
                if message and (
                    "Walk-forward" in message
                    or "Parameter stability" in message
                    or "search:" in message
                    or "Resuming optimizer" in message
                ):
                    finder_status.write(message)
                if message and ("Walk-forward" in message or "Parameter stability" in message):
                    try:
                        persist_finder_checkpoint(force=True)
                    except AppError as checkpoint_exc:
                        if not checkpoint_save_warning[0]:
                            finder_status.write(f"Checkpoint warning: {checkpoint_exc}")
                            checkpoint_save_warning[0] = True

            def on_finder_engine_checkpoint(engine_state: dict[str, Any]) -> None:
                checkpoint_record["engine_state"] = engine_state
                try:
                    persist_finder_checkpoint()
                except AppError as checkpoint_exc:
                    if not checkpoint_save_warning[0]:
                        finder_status.write(f"Checkpoint warning: {checkpoint_exc}")
                        checkpoint_save_warning[0] = True

            finder_run_kwargs: dict[str, Any] = {
                "profile_name": finder_profile.name,
                "progress": on_finder_progress,
            }
            if _finder_supports_resume:
                finder_run_kwargs.update(
                    {
                        "resume_state": resume_engine_state or None,
                        "checkpoint": on_finder_engine_checkpoint,
                    }
                )
            finder_report = run_stock_strategy_finder(
                finder_rows,
                finder_family_strategies,
                finder_symbol,
                **finder_run_kwargs,
            )
            finder_report["market_data_integrity"] = split_guard

            # Post-selection execution audit: sample actual historical SIP/IEX quotes
            # only at the frozen winner's holdout entries. This keeps quote volume
            # bounded while checking whether our modeled spread envelope was too mild.
            optimization_for_spread = finder_report.get("optimization") or {}
            winner_for_spread = optimization_for_spread.get("winner") or {}
            winning_backtest_for_spread = optimization_for_spread.get("winning_backtest") or {}
            optimized_settings_for_spread = (
                winner_for_spread.get("optimized_backtest_settings") or {}
            )
            optimizer_settings_for_spread = (
                optimization_for_spread.get("optimization_settings") or {}
            )
            sensitivity_multipliers = [
                safe_float(value)
                for value in (
                    optimizer_settings_for_spread.get("execution_sensitivity_multipliers")
                    or (1.25, 1.5, 1.75, 2.0)
                )
            ]
            maximum_stress_multiplier = max(
                [value for value in sensitivity_multipliers if value is not None]
                or [2.0]
            )
            spread_audit = historical_entry_spread_audit(
                market,
                finder_symbol,
                list(winning_backtest_for_spread.get("trades") or []),
                list(optimization_for_spread.get("holdout_sessions") or []),
                modeled_spread_bps=(
                    safe_float(optimized_settings_for_spread.get("spread_bps"), 12.0)
                    or 12.0
                ),
                maximum_stress_multiplier=maximum_stress_multiplier,
            )
            finder_report = apply_historical_spread_integrity_guard(
                finder_report,
                spread_audit,
            )
            if spread_audit.get("status") == "UNDERMODELED":
                finder_status.write(
                    "Execution integrity warning · real holdout spreads exceeded the "
                    "largest spread assumption in the tested sensitivity curve."
                )
            elif spread_audit.get("status") == "COVERED":
                finder_status.write(
                    "Execution integrity check · sampled real holdout spreads were "
                    "covered by the modeled sensitivity range."
                )

            # Flush the newest optimizer state/loser ledger before writing the final result.
            checkpoint_record["status"] = "complete"
            checkpoint_record["progress"] = 1.0
            checkpoint_record["message"] = f"{finder_symbol} strategy research complete"
            checkpoint_record["completed_at"] = str(finder_report.get("generated_at") or utc_now().isoformat())
            checkpoint_record["last_error"] = None

            data = checkpoint_store.load_latest()
            data = merge_finder_checkpoint_into_library(data, checkpoint_record)
            data = merge_finder_report_into_library(data, finder_report)
            checkpoint_store.save(data)

            ui_report = (
                latest_completed_finder_report(
                    data,
                    finder_symbol,
                    finder_profile.name,
                )
                or dict(finder_report)
            )
            ui_report["configuration_history"] = []
            if isinstance(ui_report.get("optimization"), dict):
                ui_report["optimization"] = dict(ui_report["optimization"])
                ui_report["optimization"]["configuration_history"] = []
            st.session_state["til_stock_strategy_finder_result"] = ui_report

            complete_task_bar(
                finder_bar,
                finder_monitor,
                f"{finder_symbol} strategy research complete",
            )
            verdict = finder_report.get("verdict") or {}
            finder_status.update(
                label=(
                    f"{finder_symbol} · {verdict.get('label') or 'Research complete'} · "
                    f"{int(finder_report.get('unique_configurations_tested') or 0):,} unique configurations tested"
                ),
                state="complete",
                expanded=False,
            )
            st.rerun()
        except AppError as exc:
            checkpoint_record["status"] = "failed"
            checkpoint_record["last_error"] = str(exc)
            checkpoint_record["message"] = "Stock Strategy Finder stopped safely"
            try:
                persist_finder_checkpoint(force=True)
            except Exception:
                pass
            finder_status.update(label="Stock Strategy Finder stopped safely", state="error", expanded=False)
            st.error(str(exc))
        except Exception as exc:
            checkpoint_record["status"] = "failed"
            checkpoint_record["last_error"] = str(exc)
            checkpoint_record["message"] = "Stock Strategy Finder encountered an error"
            try:
                persist_finder_checkpoint(force=True)
            except Exception:
                pass
            finder_status.update(label="Stock Strategy Finder encountered an error", state="error", expanded=False)
            st.error(f"Stock Strategy Finder failed: {exc}")

    if finder_result and str(finder_result.get("symbol") or "").upper() == finder_symbol:
        verdict = finder_result.get("verdict") or {}
        robustness = finder_result.get("robustness") or {}
        stability = finder_result.get("parameter_stability") or {}
        walk = (finder_result.get("walk_forward") or {}).get("summary") or {}
        optimization = finder_result.get("optimization") or {}
        winner = optimization.get("winner") or {}
        holdout = winner.get("holdout_metrics") or {}

        if int(finder_result.get("strategy_fidelity_engine_version") or 0) < 1:
            st.warning(
                "**Legacy research result — predates the Strategy Integrity gate.** "
                "This saved run may have tested a simplified machine version of the source strategy. "
                "Keep it for historical comparison, but do not treat its verdict as equivalent to a new "
                "Finder run that first verifies source-to-backtester fidelity."
            )

        verdict_class = {
            "ready_for_paper": "ready",
            "promising": "promising",
            "historical_candidate": "promising",
            "historically_robust_execution_gap": "promising",
            "no_robust_strategy": "reject",
        }.get(str(verdict.get("code") or ""), "promising")
        st.markdown(
            (
                f'<div class="til-finder-verdict {verdict_class}">'
                '<div><span>STOCK-SPECIFIC RESEARCH VERDICT</span>'
                f'<strong>{html.escape(str(verdict.get("label") or "Research complete"))}</strong>'
                f'<p>{html.escape(str(verdict.get("reason") or ""))}</p></div>'
                f'<div class="til-finder-score">{safe_float(robustness.get("score"), 0.0):.0f}<small>/100</small></div>'
                '</div>'
            ),
            unsafe_allow_html=True,
        )

        result_cols = st.columns(6)
        result_cols[0].metric(
            "Configurations tested",
            f"{int(finder_result.get('unique_configurations_tested') or 0):,}",
        )
        result_cols[1].metric("Winning family", str(finder_result.get("winner_strategy_name") or "—"))
        result_cols[2].metric("Timeframe", str(finder_result.get("timeframe") or "—"))
        result_cols[3].metric("Holdout P/L", f"${safe_float(holdout.get('net_pnl'), 0.0):,.2f}")
        result_cols[4].metric("Walk-forward profitable", f"{safe_float(walk.get('profitable_fold_pct'), 0.0):.0f}%")
        result_cols[5].metric("Nearby settings profitable", f"{safe_float(stability.get('positive_pct'), 0.0):.0f}%")

        st.success(
            f"Step 1 complete: {finder_symbol} now has ranked historical strategy candidates. Next, compare them and choose one to validate."
        )
        render_guided_strategy_flow(active_step=2)
        st.markdown("### Step 2 — Compare the tested strategies")
        st.caption(
            "The next page shows only strategies that Step 1 actually tested for this stock, ranked by their historical search results."
        )
        guided_strategy_id = str(
            finder_result.get("stock_specific_strategy_id")
            or finder_result.get("winner_source_strategy_id")
            or ""
        )
        st.button(
            f"② Compare {finder_symbol} tested strategies →",
            type="primary",
            width="stretch",
            key="til_finder_continue_current_signal",
            on_click=queue_stock_analyzer_from_finder,
            args=(
                finder_symbol,
                guided_strategy_id,
                str(finder_result.get("run_id") or ""),
            ),
        )
        st.markdown("### Historical validation details")
        st.caption("These details explain how hard the winning strategy was tested. You can ignore them until you want the deeper evidence.")

        if walk.get("embargo_sessions") is not None:
            st.caption(
                f"Walk-forward boundary protection: {int(walk.get('embargo_sessions') or 0)} full session(s) "
                "are omitted between each fold's optimization history and external test block."
            )

        finder_spread_audit = finder_result.get("historical_spread_audit") or {}
        finder_reuse_audit = finder_result.get("holdout_reuse_audit") or {}
        finder_market_integrity = finder_result.get("market_data_integrity") or {}
        integrity_cols = st.columns(3)
        integrity_cols[0].metric(
            "Holdout freshness",
            "Pristine" if finder_reuse_audit.get("pristine", True) else "Reused",
        )
        integrity_cols[1].metric(
            "Real spread audit",
            str(finder_spread_audit.get("status") or "Not sampled").replace("_", " ").title(),
        )
        integrity_cols[2].metric(
            "Price-history contract",
            (
                "Raw · post-action"
                if finder_market_integrity.get("corporate_action_reset_detected")
                else "Raw"
            ),
        )
        if not finder_reuse_audit.get("pristine", True):
            st.warning(str(finder_reuse_audit.get("note") or "Final holdout has been reused."))
        if str(finder_spread_audit.get("status") or "") == "UNDERMODELED":
            st.error(
                "Observed bid/ask spreads at sampled untouched-holdout entries exceeded "
                "the largest spread assumption in the tested cost curve."
            )
        elif str(finder_spread_audit.get("status") or "") in {"LIMITED", "LIMITED_FEED"}:
            st.warning(
                "Historical quote coverage at holdout entries was limited, so real-spread "
                "confirmation remains incomplete."
            )

        distributed_details = dict(finder_result.get("distributed") or {})
        if distributed_details.get("enabled"):
            st.caption(
                f"Distributed cloud execution · {int(distributed_details.get('shard_count') or 0)} shards "
                f"across {len(distributed_details.get('timeframes') or [])} timeframes · "
                "all shards were merged before the final untouched holdout, walk-forward, and stability checks."
            )

        st.markdown("### Evidence by period")
        development_execution_sensitivity = winner.get("execution_sensitivity") or {}
        holdout_execution_sensitivity = winner.get("holdout_execution_sensitivity") or {}
        execution_sensitivity = (
            holdout_execution_sensitivity
            or development_execution_sensitivity
        )
        execution_sensitivity_scope = (
            "Untouched holdout"
            if holdout_execution_sensitivity
            else "Validation"
        )
        sensitivity_points = [
            item
            for item in execution_sensitivity.get("points") or []
            if isinstance(item, dict)
        ]
        evidence_periods = [
            ("Training", winner.get("training_metrics") or {}),
            ("Validation", winner.get("validation_metrics") or {}),
            ("Untouched holdout", holdout),
        ]
        if not sensitivity_points and not holdout_execution_sensitivity:
            evidence_periods.append(("Higher-cost stress", winner.get("stress_metrics") or {}))
        evidence_rows = []
        for period_name, metrics in evidence_periods:
            evidence_rows.append(
                {
                    "Period": period_name,
                    "Trades": int(safe_float(metrics.get("trade_count"), 0) or 0),
                    "Net P/L": safe_float(metrics.get("net_pnl"), 0.0) or 0.0,
                    "Return %": safe_float(metrics.get("return_pct"), 0.0) or 0.0,
                    "Win rate %": safe_float(metrics.get("win_rate_pct"), 0.0) or 0.0,
                    "Profit factor": metrics.get("profit_factor"),
                    "Max drawdown %": safe_float(metrics.get("max_drawdown_pct"), 0.0) or 0.0,
                }
            )
        st.dataframe(pd.DataFrame(evidence_rows), width="stretch", hide_index=True)

        if sensitivity_points:
            st.markdown(f"### {execution_sensitivity_scope} execution-cost sensitivity")
            sensitivity_cols = st.columns(4)
            sensitivity_cols[0].metric(
                "Cost-curve grade",
                str(execution_sensitivity.get("label") or "—").title(),
            )
            sensitivity_cols[1].metric(
                "Sensitivity score",
                f"{safe_float(execution_sensitivity.get('score'), 0.0):.1f}/100",
            )
            sensitivity_cols[2].metric(
                "Stress points profitable",
                f"{safe_float(execution_sensitivity.get('profitable_multiplier_pct'), 0.0):.0f}%",
            )
            median_retention = execution_sensitivity.get("median_pnl_retention_pct")
            sensitivity_cols[3].metric(
                "Median P/L retained",
                f"{safe_float(median_retention, 0.0):.0f}%" if median_retention is not None else "—",
            )
            sensitivity_rows = []
            for point in sensitivity_points:
                metrics = point.get("metrics") or {}
                retention = point.get("pnl_retention_pct")
                sensitivity_rows.append(
                    {
                        "Execution cost": f"{safe_float(point.get('multiplier'), 0.0):.2f}×",
                        "Spread bps": round(safe_float(point.get("spread_bps"), 0.0) or 0.0, 2),
                        "Slippage bps": round(safe_float(point.get("slippage_bps"), 0.0) or 0.0, 2),
                        "Net P/L": safe_float(metrics.get("net_pnl"), 0.0) or 0.0,
                        "P/L retained %": (
                            safe_float(retention, 0.0) if retention is not None else None
                        ),
                        "Profit factor": metrics.get("profit_factor"),
                        "Profitable": bool(point.get("profitable")),
                    }
                )
            st.dataframe(pd.DataFrame(sensitivity_rows), width="stretch", hide_index=True)
            first_break = execution_sensitivity.get("first_unprofitable_multiplier")
            if first_break is not None:
                st.caption(
                    f"First tested cost level with non-positive P/L: {safe_float(first_break, 0.0):.2f}×."
                )
            st.caption(str(execution_sensitivity.get("note") or ""))
        elif holdout_execution_sensitivity:
            st.caption(
                "Untouched-holdout execution sensitivity is unavailable because the "
                "baseline holdout did not have positive simulated P/L and at least one trade."
            )

        regime_report = finder_result.get("regime_diagnostics") or {}
        regime_windows = [
            item
            for item in regime_report.get("windows") or []
            if isinstance(item, dict)
        ]
        if regime_windows:
            st.markdown("### Same winning rules across different recent time periods")
            regime_rows = []
            for regime in regime_windows:
                metrics = regime.get("metrics") or {}
                regime_rows.append(
                    {
                        "Time period": str(regime.get("label") or "").replace("regime", "period"),
                        "Sessions": int(regime.get("session_count") or 0),
                        "Period": f"{regime.get('start_session')} → {regime.get('end_session')}",
                        "Trades": int(safe_float(metrics.get("trade_count"), 0) or 0),
                        "Net P/L": safe_float(metrics.get("net_pnl"), 0.0) or 0.0,
                        "Return %": safe_float(metrics.get("return_pct"), 0.0) or 0.0,
                        "Win rate %": safe_float(metrics.get("win_rate_pct"), 0.0) or 0.0,
                        "Profit factor": metrics.get("profit_factor"),
                    }
                )
            st.dataframe(
                pd.DataFrame(regime_rows),
                width="stretch",
                hide_index=True,
            )
            st.caption(
                str(regime_report.get("note") or "")
                + " This is especially useful for stocks whose behavior changes materially over time."
            )

        if str(verdict.get("code") or "") in {"historical_candidate", "promising"}:
            st.info(
                "The best candidate is intentionally shown even though it is **not validated**. "
                "This lets you inspect a setup that may work in the stock's recent behavior without pretending it has proven durable."
            )

        current_profile_name = str((finder_result.get("profile") or {}).get("name") or "")
        if (
            str(verdict.get("code") or "") != "ready_for_paper"
            and current_profile_name != "Current Regime"
        ):
            regime_switch_slot = st.empty()
            switch_regime = regime_switch_slot.button(
                "↻ Switch to Recent Behavior search",
                key="til_finder_try_current_regime",
                help=(
                    "Search roughly the latest month of behavior for stock-specific setups "
                    "that may be hidden by a much longer mixed historical history."
                ),
            )
            if switch_regime:
                regime_switch_slot.button(
                    "↻ Loading Recent Behavior search…",
                    disabled=True,
                    key="til_finder_try_current_regime_busy",
                )
                st.session_state["til_pending_finder_profile"] = "Current Regime"
                st.session_state["_trading_app_boot_message"] = (
                    "Loading Recent Behavior Finder…"
                )
                st.rerun()

        with st.expander("Winning optimized rules", expanded=False):
            st.json(winner.get("optimized_rules") or {})
        with st.expander("Walk-forward + parameter-stability details", expanded=False):
            st.write(
                f"Walk-forward score: **{safe_float(walk.get('score'), 0.0):.1f}/100** · "
                f"profitable active folds: **{safe_float(walk.get('profitable_fold_pct'), 0.0):.1f}%**"
            )
            st.write(
                f"Nearby holdout variants: **{int(stability.get('active') or 0)} active** · "
                f"**{safe_float(stability.get('positive_pct'), 0.0):.1f}% profitable** · "
                f"median P/L **${safe_float(stability.get('median_net_pnl'), 0.0):,.2f}**"
            )
            st.caption(str(stability.get("note") or ""))

        stage_timings = dict(finder_result.get("stage_timings_seconds") or {})
        if stage_timings:
            with st.expander("Research speed profile", expanded=False):
                timing_rows = []
                total_timing = safe_float(stage_timings.get("total"), 0.0) or 0.0
                for stage_key, stage_label in (
                    ("optimization", "Strategy optimization"),
                    ("walk_forward", "Walk-forward validation"),
                    ("parameter_stability", "Parameter stability"),
                ):
                    seconds = safe_float(stage_timings.get(stage_key), 0.0) or 0.0
                    timing_rows.append(
                        {
                            "Stage": stage_label,
                            "Minutes": round(seconds / 60.0, 2),
                            "Share of measured runtime": (
                                round(seconds / total_timing * 100.0, 1)
                                if total_timing > 0 else 0.0
                            ),
                        }
                    )
                st.dataframe(
                    pd.DataFrame(timing_rows),
                    width="stretch",
                    hide_index=True,
                )
                st.caption(
                    f"Measured Finder runtime: {total_timing / 60.0:.1f} minutes. "
                    "This profile helps us target the real bottleneck instead of reducing search breadth."
                )

        st.success(
            f"The exact configuration ledger for this run was saved to the research library, including losing combinations. "
            f"That ledger contains {int(finder_result.get('unique_configurations_tested') or 0):,} unique tested configurations."
        )

        candidate_strategy_id = str(
            finder_result.get("stock_specific_strategy_id")
            or finder_result.get("winner_source_strategy_id")
            or ""
        )
        actions = st.columns(3)
        if actions[0].button(
            "↗ Open Paper & Live Trading",
            width="stretch",
            disabled=str(verdict.get("code") or "") != "ready_for_paper",
            key="til_finder_open_paper",
        ):
            st.session_state["til_selected_strategy_id"] = candidate_strategy_id
            navigate_to_workspace("Live / Paper", pending=True)
        if actions[1].button(
            "⌖ Scan market for this setup",
            width="stretch",
            key="til_finder_open_discovery",
        ):
            st.session_state["til_market_discovery_strategy_id"] = candidate_strategy_id
            navigate_to_workspace("Market Discovery", pending=True)
        if actions[2].button(
            "⌬ Open advanced Strategy Lab",
            width="stretch",
            key="til_finder_open_lab",
        ):
            st.session_state["til_selected_strategy_id"] = candidate_strategy_id
            navigate_to_workspace("Strategy Lab", pending=True)


elif module == "Overview":
    overview_validated = sum(
        1
        for item in canonical_strategies
        if str(item.get("validation_status") or "").lower() == "validated"
    )
    overview_queue = research_queue_status(library)
    overview_research_system = dict(library.get("research_system") or {})

    st.markdown("### What do you want to do?")
    st.caption(
        "Choose a goal. You do not need to work through the research pages in order."
    )

    action_row_one = st.columns(3)
    action_row_two = st.columns(2)

    home_actions = [
        (
            action_row_one[0],
            "◆ Find the best strategy for a stock",
            "Enter a ticker and let the Lab search historical strategy/rule combinations for that specific stock.",
            "Stock Strategy Finder",
            "Find strategy",
            "til_home_find_strategy",
        ),
        (
            action_row_one[1],
            "⌕ Analyze a stock right now",
            "Compare one ticker against everything the Lab currently knows and see which setup fits the current market.",
            "Stock Analyzer",
            "Analyze stock",
            "til_home_analyze_stock",
        ),
        (
            action_row_one[2],
            "◎ Find stocks worth watching",
            "Scan current market leaders against the entire usable strategy library and rank the best stock/strategy matches.",
            "Market Discovery",
            "Scan market",
            "til_home_market_discovery",
        ),
        (
            action_row_two[0],
            "◇ Add research material",
            "Add books, PDFs, or videos. AI extracts the ideas and folds them into the research library.",
            "Knowledge Sources",
            "Add sources",
            "til_home_add_sources",
        ),
        (
            action_row_two[1],
            "✦ See what AI has discovered",
            "See the automatic research queue, grounded research, hypotheses, and what the AI is working on.",
            "AI Research Autopilot",
            "View AI research",
            "til_home_ai_research",
        ),
    ]

    for col, title, body, target, button_label, key in home_actions:
        with col:
            st.markdown(
                f'<div class="til-card"><strong>{title}</strong><p class="muted">{body}</p></div>',
                unsafe_allow_html=True,
            )
            if st.button(button_label, key=key, width="stretch", type="primary"):
                navigate_to_workspace(target)

    st.markdown("### AI research at a glance")
    status_cols = st.columns(5)
    status_cols[0].metric(
        "Automatic research",
        "ACTIVE" if overview_research_system.get("last_worker_at") else "WAITING",
        str(overview_research_system.get("last_worker_at") or "No worker heartbeat yet"),
        delta_color="off",
    )
    status_cols[1].metric(
        "Research queue",
        int(overview_queue.get("active") or 0),
        "jobs waiting / running",
        delta_color="off",
    )
    status_cols[2].metric(
        "Strategy families",
        len(canonical_strategies),
        f"{len(source_strategies)} raw source ideas",
        delta_color="off",
    )
    status_cols[3].metric(
        "Validated",
        overview_validated,
        "passed current validation gate",
        delta_color="off",
    )
    status_cols[4].metric(
        "Cloud system",
        system_status_word,
        "open Advanced → System Health for details",
        delta_color="off",
    )

    last_cycle = str(
        overview_research_system.get("last_seeded_at")
        or overview_research_system.get("last_worker_at")
        or "No cycle recorded yet"
    )
    st.info(
        "**You normally do not need to manage the pipeline manually.** "
        "The hourly cloud worker keeps processing research jobs, while the daily research cycle adds fresh topics. "
        f"Latest recorded research activity: {last_cycle}."
    )

    if integrity_blocked_count:
        integrity_home_col, integrity_button_col = st.columns([3.2, 1.0], vertical_alignment="center")
        integrity_home_col.warning(
            f"**Strategy fidelity check:** {integrity_blocked_count} strategy "
            f"{'family has' if integrity_blocked_count == 1 else 'families have'} important source logic "
            "the current backtester cannot faithfully reproduce. Those families are excluded from new "
            "Finder, Strategy Lab, Market Discovery, Stock Analyzer, and cross-stock research runs."
        )
        if integrity_button_col.button(
            "Review integrity gaps",
            key="til_home_integrity_audit",
            width="stretch",
        ):
            navigate_to_workspace("Strategy Integrity")

    with st.expander("How the Lab works behind the scenes", expanded=False):
        st.write(
            "1. **Learn** — books, videos, and grounded web research produce trading ideas.\\n"
            "2. **Consolidate** — similar ideas are merged into strategy families instead of becoming endless duplicates.\\n"
            "3. **Test & validate** — historical optimization, holdout data, walk-forward tests, and robustness checks try to break the idea.\\n"
            "4. **Apply** — Find Strategy, Market Discovery, and Stock Analyzer use what survived the research process."
        )
        st.caption(
            "Strategy Library, Blueprint, Rule Builder, Strategy Lab, Validation, Market Universe, "
            "Catalyst Intelligence, and System Health remain available under Advanced / Research Details."
        )


elif module == "Retrospective Learning":
    st.caption(
        "The teacher is allowed to look into the future **only to assign labels** such as "
        "\"this became a meaningful swing low\" or \"this breakout later followed through.\" "
        "The learner's features are frozen at the event timestamp, so future bars can never leak "
        "into the prediction or backtest."
    )

    teacher_cols = st.columns([1.0, 1.0, 1.0])
    teacher_symbol = teacher_cols[0].text_input(
        "Ticker",
        value="SDOT",
        key="til_retrospective_symbol",
    ).strip().upper()
    teacher_timeframe = teacher_cols[1].selectbox(
        "Timeframe",
        ["1Min", "5Min", "15Min"],
        index=1,
        key="til_retrospective_timeframe",
    )
    teacher_days = int(
        teacher_cols[2].slider(
            "History days",
            2,
            30,
            10,
            1,
            key="til_retrospective_days",
        )
    )

    st.markdown("#### Teacher definitions")
    teacher_settings = st.columns(5)
    swing_confirm = int(
        teacher_settings[0].number_input(
            "Swing confirmation bars",
            min_value=1,
            max_value=10,
            value=3,
            step=1,
            key="til_teacher_swing_confirm",
        )
    )
    swing_move = float(
        teacher_settings[1].number_input(
            "Minimum swing move %",
            min_value=0.1,
            max_value=25.0,
            value=1.0,
            step=0.1,
            key="til_teacher_swing_move",
        )
    )
    breakout_lookback = int(
        teacher_settings[2].number_input(
            "Breakout lookback bars",
            min_value=5,
            max_value=100,
            value=20,
            step=1,
            key="til_teacher_breakout_lookback",
        )
    )
    breakout_outcome = int(
        teacher_settings[3].number_input(
            "Outcome bars",
            min_value=2,
            max_value=100,
            value=12,
            step=1,
            key="til_teacher_breakout_outcome",
        )
    )
    breakout_move = float(
        teacher_settings[4].number_input(
            "Follow-through move %",
            min_value=0.1,
            max_value=25.0,
            value=2.0,
            step=0.1,
            key="til_teacher_breakout_move",
        )
    )

    st.info(
        "**Causality rule:** hindsight may decide the label and when an anchor becomes confirmed. "
        "It may never change the feature snapshot that existed at the event. A confirmed historical "
        "anchor can only become available to trading logic from its known_at timestamp forward."
    )

    teacher_button_slot = st.empty()
    build_teacher = teacher_button_slot.button(
        "↺ Build retrospective teaching examples",
        type="primary",
        width="stretch",
        key="til_build_retrospective_teacher",
    )
    if build_teacher:
        teacher_button_slot.button(
            "↺ Building teaching examples…",
            type="primary",
            width="stretch",
            disabled=True,
            key="til_build_retrospective_teacher_busy",
        )
        if not teacher_symbol:
            st.error("Enter a ticker first.")
        else:
            with st.status(
                f"Building causal teaching examples for {teacher_symbol}…",
                expanded=True,
            ) as teacher_status:
                try:
                    market = market_client()
                    teacher_end = utc_now()
                    if market.historical_feed == "sip" and market.live_feed != "sip":
                        teacher_end -= timedelta(minutes=16)
                    teacher_start = teacher_end - timedelta(days=teacher_days)
                    teacher_status.write(
                        f"Downloading {teacher_timeframe} bars from {teacher_start.date()} "
                        f"through {teacher_end.date()}…"
                    )
                    rows_by_symbol = market.bars(
                        [teacher_symbol],
                        start=teacher_start,
                        end=teacher_end,
                        timeframe=teacher_timeframe,
                        adjustment="raw",
                        max_pages=80,
                    )
                    teacher_rows = list(rows_by_symbol.get(teacher_symbol) or [])
                    if not teacher_rows:
                        raise AppError(
                            f"No historical bars were returned for {teacher_symbol}."
                        )
                    split_actions = market.research_reset_actions(
                        [teacher_symbol],
                        start=teacher_start,
                        end=teacher_end,
                    )
                    teacher_rows, teacher_market_data_integrity = split_safe_raw_research_rows(
                        teacher_rows,
                        split_actions,
                        teacher_symbol,
                    )
                    if not teacher_rows:
                        raise AppError(
                            f"No split-safe raw-price history remained for {teacher_symbol}."
                        )
                    if teacher_market_data_integrity.get("corporate_action_reset_detected"):
                        teacher_status.write(
                            "Corporate-action integrity guard · teaching history restarted at "
                            f"{teacher_market_data_integrity.get('latest_split_date')}."
                        )
                    teacher_status.write(
                        "Assigning hindsight labels, then rebuilding every feature snapshot "
                        "using only information available at the event…"
                    )
                    teacher_run = build_retrospective_teacher_run(
                        teacher_rows,
                        symbol=teacher_symbol,
                        timeframe=teacher_timeframe,
                        swing_confirmation_bars=swing_confirm,
                        swing_minimum_move_pct=swing_move,
                        breakout_lookback_bars=breakout_lookback,
                        breakout_outcome_bars=breakout_outcome,
                        breakout_success_move_pct=breakout_move,
                    )
                    teacher_run["market_data_integrity_contract"] = "split_safe_raw_v1"
                    teacher_run["market_data_integrity"] = teacher_market_data_integrity
                    fresh_library = load_library(force_cloud_refresh=True, mutable=True)
                    updated_library = merge_retrospective_teacher_run(
                        fresh_library,
                        teacher_run,
                    )
                    intelligence_store().save(updated_library)
                    st.session_state["til_last_retrospective_run"] = teacher_run
                    teacher_status.update(
                        label=(
                            f"Teacher run saved · {sum((teacher_run.get('label_counts') or {}).values())} "
                            "causal examples"
                        ),
                        state="complete",
                        expanded=False,
                    )
                    st.rerun()
                except (AppError, ValueError) as exc:
                    teacher_status.update(
                        label="Retrospective teaching run failed",
                        state="error",
                        expanded=True,
                    )
                    st.error(str(exc))

    retrospective_runs = [
        dict(item)
        for item in library.get("retrospective_learning_runs") or []
        if isinstance(item, dict)
    ]
    session_teacher_run = st.session_state.get("til_last_retrospective_run")
    if isinstance(session_teacher_run, dict):
        retrospective_runs = [
            dict(session_teacher_run),
            *[
                item
                for item in retrospective_runs
                if str(item.get("generated_at") or "")
                != str(session_teacher_run.get("generated_at") or "")
            ],
        ]

    if retrospective_runs:
        st.divider()
        st.markdown("### What the teacher has labeled")
        run_labels = {
            (
                f"{item.get('symbol')} · {item.get('timeframe')} · "
                f"{item.get('start','')[:10]} → {item.get('end','')[:10]}"
            ): item
            for item in retrospective_runs
        }
        selected_teacher_run = run_labels[
            st.selectbox(
                "Saved teaching run",
                list(run_labels),
                key="til_retrospective_saved_run",
            )
        ]
        counts = dict(selected_teacher_run.get("label_counts") or {})
        count_cols = st.columns(max(1, min(4, len(counts) or 1)))
        if counts:
            for index, (label, value) in enumerate(sorted(counts.items())):
                count_cols[index % len(count_cols)].metric(
                    label.replace("_", " ").title(),
                    int(value or 0),
                )
        else:
            st.warning("This run did not find any events that met the current teacher definitions.")

        feature_layers = dict(selected_teacher_run.get("feature_layers") or {})
        indicator_check = dict(
            selected_teacher_run.get("indicator_cross_validation") or {}
        )
        if feature_layers:
            st.markdown("### Learning layers used")
            st.caption(
                "Every layer below is computed causally at the event timestamp. "
                "Future bars are used only to decide the retrospective outcome label."
            )
            layer_rows = [
                {
                    "Feature layer": str(name).replace("_", " ").title(),
                    "How it is used": description,
                }
                for name, description in feature_layers.items()
            ]
            st.dataframe(pd.DataFrame(layer_rows), width="stretch", hide_index=True)

        if indicator_check:
            indicator_passed = bool(indicator_check.get("passed"))
            if indicator_passed:
                st.success(
                    "Indicator consistency check passed: the Lab's equivalent EMA, ATR, "
                    "and session-VWAP calculations matched their independent references."
                )
            else:
                st.error(
                    "Indicator consistency check found a mismatch. Treat this teaching run "
                    "as diagnostic until the calculation difference is resolved."
                )
            with st.expander("Indicator cross-validation details", expanded=not indicator_passed):
                validation_rows = []
                for indicator_name, detail in (
                    indicator_check.get("checks") or {}
                ).items():
                    validation_rows.append(
                        {
                            "Indicator": str(indicator_name).replace("_", " ").upper(),
                            "Reference": detail.get("external_reference"),
                            "Definition": detail.get("definition"),
                            "Passed": bool(detail.get("passed")),
                            "Max absolute difference": detail.get("max_abs_difference"),
                            "Note": detail.get("note") or "",
                        }
                    )
                if validation_rows:
                    st.dataframe(
                        pd.DataFrame(validation_rows),
                        width="stretch",
                        hide_index=True,
                    )
                st.caption(str(indicator_check.get("policy") or ""))

        st.markdown("### Causal precursor summaries")
        st.caption(
            "These are descriptive medians of features measured **at the event**, before the future "
            "outcome label was known. They are learning clues, not validated trading rules."
        )
        precursor = dict(selected_teacher_run.get("precursor_feature_medians") or {})
        precursor_rows = []
        for label, values in precursor.items():
            for feature, value in (values or {}).items():
                precursor_rows.append(
                    {
                        "Outcome label": str(label).replace("_", " ").title(),
                        "Causal feature": str(feature).replace("_", " ").title(),
                        "Median at event": value,
                    }
                )
        if precursor_rows:
            st.dataframe(pd.DataFrame(precursor_rows), width="stretch", hide_index=True)

        with st.expander("Audit the hindsight / causality boundary", expanded=False):
            policy = dict(selected_teacher_run.get("causality_policy") or {})
            st.json(policy, expanded=True)
            examples = list(selected_teacher_run.get("examples") or [])
            if examples:
                audit_rows = [
                    {
                        "Label": str(item.get("label") or "").replace("_", " ").title(),
                        "Event / feature cutoff": item.get("feature_cutoff"),
                        "Label known at": item.get("known_at"),
                        "Outcome window end": item.get("outcome_window_end"),
                    }
                    for item in examples[-40:]
                ]
                st.dataframe(pd.DataFrame(audit_rows), width="stretch", hide_index=True)
    else:
        st.caption(
            "No retrospective teaching runs are saved yet. Run one above; results are stored in the "
            "durable Trading Intelligence library so later learning work can build on them."
        )



    st.divider()
    st.markdown("### Open-source implementation references")
    st.caption(
        "These repositories are **implementation/reference evidence**, not proof that a trading "
        "strategy works. The Lab can use them to discover missing capabilities, cross-check formulas, "
        "and design independent tests. Copyleft, Commons-Clause, or unlicensed code stays reference-only "
        "unless its licensing is explicitly reviewed."
    )
    reference_frame = pd.DataFrame(reference_rows())
    if not reference_frame.empty:
        st.dataframe(
            reference_frame[
                [
                    "repository",
                    "category",
                    "license",
                    "posture",
                    "usefulness",
                    "why",
                ]
            ],
            width="stretch",
            hide_index=True,
        )
    st.info(
        "**How the AI should use these:** compare implementations, extract concepts, build independent "
        "causal versions, and use permissively licensed libraries as possible test or dependency candidates "
        "only after review. A GitHub repository is never treated as profitability evidence."
    )


elif module == "System Health":
    st.caption(
        "This page separates **configured** from **proven working**. A saved queue entry is not treated as proof that compute started."
    )

    summary_cols = st.columns(4)
    summary_cols[0].metric(
        "Configuration",
        system_status_word,
        f"{int(system_config_summary.get('ready') or 0)}/{int(system_config_summary.get('total') or 0)} required checks ready",
        delta_color="off",
    )
    summary_cols[1].metric(
        "Cloud launcher",
        "CONFIGURED" if actions_token_setting else "MISSING",
        "dedicated Actions token" if actions_token_setting else "GITHUB_ACTIONS_TOKEN",
        delta_color="off",
    )
    research_system = dict(library.get("research_system") or {})
    summary_cols[2].metric(
        "Worker heartbeat",
        "SEEN" if research_system.get("last_worker_at") else "NOT SEEN",
        str(research_system.get("last_worker_at") or "No completed worker heartbeat saved"),
        delta_color="off",
    )
    active_health_jobs = [
        item
        for item in library.get("research_queue") or []
        if isinstance(item, dict)
        and str(item.get("status") or "") in {"queued", "running", "retry"}
    ]
    summary_cols[3].metric(
        "Active research jobs",
        len(active_health_jobs),
        "queued / running / retry",
        delta_color="off",
    )

    st.markdown("### Configuration checks")
    for check in system_config_checks:
        status = str(check.get("status") or "blocked").upper()
        detail = str(check.get("detail") or "")
        if status == "READY":
            st.success(f"**{check.get('name')} · READY** — {detail}")
        else:
            st.error(f"**{check.get('name')} · {status}** — {detail}")

    st.markdown("### Durable storage live probe")
    with st.spinner("Verifying private backup reachability…"):
        persistence_live = intelligence_store().persistence_status(verify=True)
    persistence_error = str(
        persistence_live.get("verification_error")
        or persistence_live.get("last_error")
        or ""
    ).strip()
    if (
        persistence_live.get("verified")
        and persistence_live.get("write_verified")
        and not persistence_error
    ):
        st.success(
            "**Private durable storage · HEALTHY** — The Trading Intelligence backup is reachable "
            "and this app has a recorded successful cloud write."
        )
    elif persistence_live.get("verified") and not persistence_error:
        st.warning(
            "**Private durable storage · REACHABLE** — The backup can be read, but this app has not "
            "yet recorded a successful write in the current deployment. The end-to-end smoke test below "
            "will prove write access from the cloud worker."
        )
    else:
        st.error(
            "**Private durable storage · BLOCKED** — "
            + (persistence_error or "The private backup could not be verified.")
        )
    st.caption(
        "Destination: "
        + str(persistence_live.get("repository") or "not configured")
        + " · "
        + str(persistence_live.get("path") or "no path")
        + " · Last successful write: "
        + str(persistence_live.get("last_write_at") or "not recorded")
    )

    st.markdown("### GitHub Actions live probe")
    if actions_token_setting:
        with st.spinner("Checking workflow access…"):
            workflow_probe = probe_github_workflow(
                actions_repository_setting,
                actions_token_setting,
                workflow="distributed-stock-finder.yml",
            )
        probe_state = str(workflow_probe.get("state") or "UNKNOWN")
        probe_detail = str(workflow_probe.get("detail") or "")
        if probe_state == "READY":
            st.success(f"**Actions workflow · READY** — {probe_detail}")
        elif probe_state == "BLOCKED":
            st.error(f"**Actions workflow · BLOCKED** — {probe_detail}")
        else:
            st.warning(f"**Actions workflow · {probe_state}** — {probe_detail}")
    else:
        st.error(
            "**Actions workflow · BLOCKED** — Add the Streamlit secret GITHUB_ACTIONS_TOKEN "
            "before cloud launches can be tested."
        )

    st.markdown("### End-to-end cloud smoke test")
    st.write(
        "This launches a tiny GitHub Actions diagnostic. It verifies the runner can start, "
        "read **and write** the private backup, authenticate to Alpaca, and authenticate to Gemini. "
        "It does not place a trade or run a strategy search."
    )
    smoke_button_slot = st.empty()
    smoke_clicked = smoke_button_slot.button(
        "☁ Run end-to-end cloud smoke test",
        type="primary",
        width="stretch",
        disabled=not bool(actions_token_setting),
        key="til_run_cloud_smoke_test",
    )
    if smoke_clicked:
        smoke_button_slot.button(
            "☁ Starting smoke test…",
            type="primary",
            width="stretch",
            disabled=True,
            key="til_run_cloud_smoke_test_busy",
        )
        smoke_ok, smoke_detail = dispatch_github_workflow(
            actions_repository_setting,
            actions_token_setting,
            workflow=CLOUD_SMOKE_WORKFLOW,
            ref=actions_ref_setting,
            inputs={"requested_from": "streamlit-system-health"},
        )
        if smoke_ok:
            st.session_state["til_cloud_smoke_requested"] = True
            st.success(
                "Smoke test launch accepted. The live status below refreshes automatically; "
                "do not treat it as passed until it explicitly says PASS."
            )
        else:
            st.error(smoke_detail)

    @st.fragment(run_every="15s")
    def render_smoke_status() -> None:
        if not actions_token_setting:
            return
        latest = latest_workflow_run(
            actions_repository_setting,
            actions_token_setting,
            workflow=CLOUD_SMOKE_WORKFLOW,
        )
        display = workflow_run_display_state(latest)
        state = str(display.get("state") or "UNKNOWN")
        detail = str(display.get("detail") or "")
        if state == "PASS":
            st.success(f"**Latest smoke test · PASS** — {detail}")
        elif state == "FAIL":
            st.error(f"**Latest smoke test · FAIL** — {detail}")
        elif state in {"RUNNING", "QUEUED"}:
            st.info(f"**Latest smoke test · {state}** — {detail}")
        else:
            st.warning(f"**Latest smoke test · {state}** — {detail}")
        if latest and latest.get("html_url"):
            st.link_button(
                "Open smoke-test run details",
                str(latest.get("html_url")),
                width="stretch",
            )

    render_smoke_status()

    st.markdown("### Reliability rules now enforced")
    st.write(
        "• The app no longer displays a hard-coded READY/ONLINE status.\n"
        "• Distributed Finder jobs older than 15 minutes without a worker claim are labeled STALLED.\n"
        "• New distributed jobs are disabled when required cloud configuration is missing.\n"
        "• The cloud worker and distributed Finder use the Trading Intelligence durable queue, not the legacy library path.\n"
        "• The live smoke test proves the external worker path instead of assuming it works."
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
            width="stretch",
            disabled=not can_analyze,
            key="til_analyze_source",
        )

        if analyze and uploaded is not None:
            analyze_slot.button(
                "🧠 Analyzing…",
                type="primary",
                width="stretch",
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
                        setting("GEMINI_RULE_COMPILER_MODEL", DEFAULT_GEMINI_SPECIALIST_MODEL),
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
                data = load_library(mutable=True)

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
            width="stretch",
            key="til_library_run_ai_manager",
        ):
            navigate_to_workspace("AI Research Autopilot", pending=True)
        if action_cols[1].button(
            "🔎 Find stocks matching validated families",
            width="stretch",
            disabled=not bool(validated_families),
            key="til_library_find_validated",
        ):
            st.session_state["til_market_discovery_include_research"] = False
            navigate_to_workspace("Market Discovery", pending=True)

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
                    width="stretch",
                    key="til_family_manual_lab",
                ):
                    st.session_state["til_selected_strategy_id"] = str(family.get("id") or "")
                    navigate_to_workspace("Strategy Lab", pending=True)
                if advanced[1].button(
                    "Inspect / improve testable rules",
                    width="stretch",
                    key="til_family_manual_compiler",
                ):
                    st.session_state["til_selected_strategy_id"] = str(family.get("id") or "")
                    navigate_to_workspace("Make Strategy Testable", pending=True)


elif module == "Strategy Integrity":
    st.caption(
        "This audit asks a different question from validation: **is the backtester actually testing the strategy the source described?** "
        "A profitable backtest is not meaningful if important entry, stock-selection, execution, risk, or exit logic was silently dropped."
    )

    def integrity_area_label(value: str) -> str:
        labels = {
            "universe": "Stock selection",
            "entry": "Entry",
            "structure": "Price / setup structure",
            "execution": "Execution",
            "risk": "Risk",
            "exit": "Exit / trade management",
            "other": "Other",
        }
        key = str(value or "other").strip().casefold()
        return labels.get(key, key.replace("_", " ").title())

    integrity_reports: dict[str, dict[str, Any]] = {}
    integrity_rows: list[dict[str, Any]] = []
    for strategy in managed_strategies:
        report = strategy_integrity_report(strategy)
        strategy_id = str(strategy.get("id") or strategy.get("name") or "")
        integrity_reports[strategy_id] = report
        dimensions = report.get("dimension_summary") or {}
        requirement_count = int(report.get("requirement_count") or 0)
        modeled_count = int(report.get("modeled_count") or 0)
        coverage_value = safe_float(report.get("coverage_pct"), 0.0) or 0.0
        coverage_measurable = bool(report.get("coverage_measurable")) and requirement_count > 0
        integrity_rows.append(
            {
                "Strategy family": strategy.get("name") or "Unnamed strategy",
                "Backtester match": report.get("label"),
                "Rules modeled": (
                    "N/A — no rules detected"
                    if requirement_count == 0
                    else f"{modeled_count} of {requirement_count} ({coverage_value or 0.0:.1f}%)"
                ),
                "Coverage sort": coverage_value if coverage_measurable else None,
                "Rules detected count": requirement_count,
                "Important rules missing": int(report.get("critical_missing_count") or 0),
                "Missing exit rules": int((dimensions.get("exit") or {}).get("missing") or 0),
                "Missing stock-selection rules": int((dimensions.get("universe") or {}).get("missing") or 0),
                "Missing execution rules": int((dimensions.get("execution") or {}).get("missing") or 0),
                "Validation status": strategy.get("validation_status") or "unvalidated",
            }
        )

    if not integrity_rows:
        st.info("No strategy families are available to audit yet.")
    else:
        faithful = sum(1 for row in integrity_rows if row["Backtester match"] == "FULLY MODELED FOR CURRENT REQUIREMENTS")
        partial = sum(1 for row in integrity_rows if row["Backtester match"] == "PARTIALLY MODELED")
        unknown = sum(1 for row in integrity_rows if row["Backtester match"] == "NO RULES DETECTED")
        blocked = sum(1 for row in integrity_rows if row["Backtester match"] == "IMPORTANT LOGIC NOT MODELED")
        measurable_coverages = [
            float(row["Coverage sort"])
            for row in integrity_rows
            if row.get("Coverage sort") is not None
        ]
        average_coverage = (
            sum(measurable_coverages) / len(measurable_coverages)
            if measurable_coverages
            else None
        )

        audit_cols = st.columns(5)
        audit_cols[0].metric("Strategies fully represented", faithful)
        audit_cols[1].metric("Strategies partly represented", partial)
        audit_cols[2].metric("No rules detected", unknown)
        audit_cols[3].metric("Strategies with critical gaps", blocked)
        audit_cols[4].metric(
            "Avg. strategy rules reproduced",
            "N/A" if average_coverage is None else f"{average_coverage:.1f}%",
        )
        if average_coverage is None:
            st.caption(
                "Average coverage is N/A because no strategies currently have measurable detected-rule coverage."
            )
        else:
            st.caption(
                f"{average_coverage:.1f}% means the backtester can reproduce about "
                f"{average_coverage:.0f}% of detected strategy rules on average, excluding strategies "
                "where no rules were detected. This is not a profitability, accuracy, confidence, or win-rate score."
            )

        if blocked:
            st.error(
                f"{blocked} strategy {'family has' if blocked == 1 else 'families have'} important source logic "
                "the deterministic backtester cannot currently reproduce. Finder searches now exclude those "
                "families instead of testing a misleading simplified imitation."
            )
        elif partial:
            st.warning(
                "Some strategies are only partially represented. Review the gaps before treating historical "
                "results as evidence about the original source strategy."
            )
        else:
            st.success("The currently detected defining requirements are represented by the backtester.")

        audit_frame = pd.DataFrame(integrity_rows).sort_values(
            by=["Important rules missing", "Rules detected count", "Coverage sort"],
            ascending=[False, True, True],
            na_position="last",
        )
        audit_frame = audit_frame.drop(
            columns=["Coverage sort", "Rules detected count"],
            errors="ignore",
        )
        st.dataframe(audit_frame, width="stretch", hide_index=True)

        # Audit raw source variations too. A minority exit/selection variation can
        # disappear from a family's "core" DNA by design, but it still matters for
        # deciding which machine capabilities the Lab should implement next.
        source_gap_buckets: dict[str, dict[str, Any]] = {}
        source_reports_with_gaps = 0
        for raw_source_strategy in source_strategies:
            source_report = strategy_integrity_report(raw_source_strategy)
            missing_items = [
                item
                for item in source_report.get("requirements") or []
                if not item.get("modeled") and item.get("critical")
            ]
            if missing_items:
                source_reports_with_gaps += 1
            source_name = str(raw_source_strategy.get("name") or "Unnamed strategy")
            source_title = str(
                raw_source_strategy.get("source_title")
                or raw_source_strategy.get("source_type")
                or "Unknown source"
            )
            for gap in missing_items:
                label = str(gap.get("label") or "Unmodeled requirement")
                bucket = source_gap_buckets.setdefault(
                    label,
                    {
                        "Missing capability": label,
                        "Area": integrity_area_label(str(gap.get("dimension") or "other")),
                        "Source strategies affected": 0,
                        "Sources affected": set(),
                        "Examples": [],
                        "Why it is missing": str(gap.get("limitation") or ""),
                    },
                )
                bucket["Source strategies affected"] += 1
                bucket["Sources affected"].add(source_title)
                if len(bucket["Examples"]) < 4 and source_name not in bucket["Examples"]:
                    bucket["Examples"].append(source_name)

        if source_gap_buckets:
            st.markdown("### Missing capabilities across the original research material")
            st.caption(
                "This looks at every original extracted strategy, including variations that were later "
                "consolidated into the same family. It is the backlog for expanding the backtester's vocabulary."
            )
            source_gap_rows = []
            for bucket in source_gap_buckets.values():
                source_gap_rows.append(
                    {
                        "Missing capability": bucket["Missing capability"],
                        "Area": bucket["Area"],
                        "Source strategies affected": int(bucket["Source strategies affected"]),
                        "Independent sources affected": len(bucket["Sources affected"]),
                        "Example strategies": "; ".join(bucket["Examples"]),
                        "Why it is missing": bucket["Why it is missing"],
                    }
                )
            source_gap_rows.sort(
                key=lambda row: (
                    -int(row["Source strategies affected"]),
                    -int(row["Independent sources affected"]),
                    str(row["Missing capability"]),
                )
            )
            st.dataframe(pd.DataFrame(source_gap_rows), width="stretch", hide_index=True)
            st.warning(
                f"{source_reports_with_gaps} original extracted strategy "
                f"{'variation contains' if source_reports_with_gaps == 1 else 'variations contain'} "
                "at least one defining requirement the current deterministic model cannot reproduce. "
                "The most common gaps at the top of this table should drive the next engine upgrades."
            )
        else:
            st.success(
                "No critical vocabulary gaps were detected across the currently extracted source strategies."
            )

        strategy_options = {
            f"{row['Strategy family']} · {row['Backtester match']} · {row['Rules modeled']}": row
            for row in integrity_rows
        }
        selected_label = st.selectbox(
            "Inspect how well a strategy is represented",
            list(strategy_options),
            key="til_integrity_strategy",
        )
        selected_row = strategy_options[selected_label]
        selected_strategy = next(
            (
                item
                for item in managed_strategies
                if str(item.get("name") or "Unnamed strategy") == str(selected_row["Strategy family"])
            ),
            managed_strategies[0],
        )
        selected_id = str(selected_strategy.get("id") or selected_strategy.get("name") or "")
        report = integrity_reports.get(selected_id) or strategy_integrity_report(selected_strategy)

        st.markdown(f"### {selected_strategy.get('name') or 'Unnamed strategy'}")
        fidelity_cols = st.columns(4)
        detail_requirement_count = int(report.get("requirement_count") or 0)
        detail_coverage = safe_float(report.get("coverage_pct"), 0.0) or 0.0
        fidelity_cols[0].metric("Backtester match", report.get("label"))
        fidelity_cols[1].metric(
            "Rules represented",
            "N/A" if detail_requirement_count == 0 else f"{detail_coverage or 0.0:.1f}%",
        )
        fidelity_cols[2].metric(
            "Rules modeled",
            (
                "N/A"
                if detail_requirement_count == 0
                else f"{int(report.get('modeled_count') or 0)}/{detail_requirement_count}"
            ),
        )
        fidelity_cols[3].metric("Important rules missing", int(report.get("critical_missing_count") or 0))
        if detail_requirement_count == 0:
            st.caption(
                "No source rules were detected for this strategy, so fidelity cannot be scored yet."
            )

        source_ids = {
            str(value)
            for value in selected_strategy.get("source_strategy_ids") or []
            if str(value).strip()
        }
        contributing = [
            item for item in source_strategies
            if str(item.get("id") or "") in source_ids
        ]
        if contributing:
            with st.expander(f"Source strategies consolidated into this family · {len(contributing)}", expanded=False):
                for item in contributing:
                    st.write(
                        f"**{item.get('name') or 'Unnamed'}** · "
                        f"{item.get('source_title') or item.get('source_type') or 'Unknown source'}"
                    )

        source_col, machine_col = st.columns(2)
        with source_col:
            st.markdown("#### Source-described behavior")
            if selected_strategy.get("entry_conditions"):
                st.write("**Entry**")
                for value in selected_strategy.get("entry_conditions") or []:
                    st.write("• " + str(value))
            if selected_strategy.get("risk_rules"):
                st.write("**Risk**")
                for value in selected_strategy.get("risk_rules") or []:
                    st.write("• " + str(value))
            if selected_strategy.get("exit_conditions"):
                st.write("**Exit / trade management**")
                for value in selected_strategy.get("exit_conditions") or []:
                    st.write("• " + str(value))
        with machine_col:
            st.markdown("#### What the backtester executes")
            effective_rules = {
                key: value
                for key, value in normalize_machine_rules(
                    effective_strategy_for_research(selected_strategy).get("machine_rules")
                ).items()
                if value is not None
            }
            if effective_rules:
                st.json(effective_rules, expanded=False)
            else:
                st.warning("No executable machine rules are currently available.")

        requirements = list(report.get("requirements") or [])
        if requirements:
            st.markdown("#### Rule-by-rule backtester check")
            st.caption("This shows which parts of the strategy description the backtester can reproduce and which parts are still missing.")
            requirement_rows = []
            for item in requirements:
                requirement_rows.append(
                    {
                        "Area": integrity_area_label(str(item.get("dimension") or "entry")),
                        "Strategy rule": item.get("label"),
                        "Backtester can reproduce it?": "YES" if item.get("modeled") else "NO",
                        "Backtester rule(s)": ", ".join(str(value) for value in item.get("rule_keys") or []) or "No supported rule",
                        "Why it cannot be reproduced": item.get("limitation") or "",
                    }
                )
            st.dataframe(pd.DataFrame(requirement_rows), width="stretch", hide_index=True)

        missing = list(report.get("critical_missing_requirements") or [])
        if missing:
            st.error(
                "**Do not interpret optimization results for this family as a faithful test of the original strategy yet.** "
                "Important missing components: " + "; ".join(str(item) for item in missing)
            )
        else:
            st.info(str(report.get("note") or ""))


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
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

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
                st.dataframe(pd.DataFrame(concept_rows), width="stretch", hide_index=True)

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
            st.dataframe(pd.DataFrame(family_rows), width="stretch", hide_index=True)
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
                st.dataframe(pd.DataFrame(candidate_rows), width="stretch", hide_index=True)
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
                save_candidate_slot = action_cols[0].empty()
                save_candidate = save_candidate_slot.button(
                    "💾 Save / refresh research candidate",
                    width="stretch",
                    key=f"save_synth_{candidate.get('id')}",
                )
                synth_run_slot = action_cols[1].empty()
                run_candidate = synth_run_slot.button(
                    "🧪 Run full historical research pipeline",
                    type="primary",
                    width="stretch",
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
                    save_candidate_slot.button(
                        "💾 Saving research candidate…",
                        width="stretch",
                        disabled=True,
                        key=f"save_synth_busy_{candidate.get('id')}",
                    )
                    data = load_library(mutable=True)
                    executable["research_readiness"] = readiness
                    data = upsert_strategy_record(data, executable)
                    intelligence_store().save(data)
                    st.success("Saved the synthesized research candidate to the unified Strategy Library.")
                    st.rerun()

                if run_candidate:
                    synth_run_slot.button(
                        "🧪 Researching…",
                        type="primary",
                        width="stretch",
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
                                setting("GEMINI_RULE_COMPILER_MODEL", DEFAULT_GEMINI_SPECIALIST_MODEL),
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

                        data = load_library(mutable=True)
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
                        data = load_library(mutable=True)
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
            width="stretch",
            key="til_compile_rule_suggestions",
        )
        if compile_rules:
            compiler_slot.button(
                "🧩 Translating…",
                type="primary",
                width="stretch",
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
                        setting("GEMINI_RULE_COMPILER_MODEL", DEFAULT_GEMINI_SPECIALIST_MODEL),
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
                    data = load_library(mutable=True)
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
                    width="stretch",
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

        remove_assumptions_slot = st.empty()
        remove_assumptions = (
            remove_assumptions_slot.button(
                "Remove all AI test assumptions",
                width="stretch",
                key="til_remove_ai_test_assumptions",
            )
            if accepted_overrides
            else False
        )
        if remove_assumptions:
            remove_assumptions_slot.button(
                "Removing AI test assumptions…",
                width="stretch",
                disabled=True,
                key="til_remove_ai_test_assumptions_busy",
            )
            data = load_library(mutable=True)
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
        "This is the AI research manager. It now combines your curated books/videos with a persistent grounded-web "
        "research queue. Gemini Flash handles high-volume research, Gemini Pro reviews difficult or conflicting "
        "hypotheses, and deterministic historical validation—not AI opinion—decides what advances."
    )

    cloud_queue = research_queue_status(library)
    research_system = dict(library.get("research_system") or {})
    grounded_runs = list(library.get("external_research_runs") or [])
    research_hypotheses = list(library.get("research_hypotheses") or [])
    worker_runs = list(library.get("research_worker_runs") or [])

    st.markdown("### Continuous Research System")
    continuous_metrics = st.columns(4)
    continuous_metrics[0].metric("Research queue", int(cloud_queue.get("active") or 0))
    continuous_metrics[1].metric("Grounded web runs", len(grounded_runs))
    continuous_metrics[2].metric(
        "Hypotheses",
        len(research_hypotheses),
        delta=(
            f"{sum(1 for item in research_hypotheses if str(item.get('status') or '') == 'queued_for_validation')} queued for validation"
        ),
        delta_color="off",
    )
    continuous_metrics[3].metric(
        "Cloud worker",
        "ACTIVE" if research_system.get("last_worker_at") else "READY TO CONNECT",
        delta=str(research_system.get("last_worker_at") or "No cloud worker run saved yet"),
        delta_color="off",
    )

    bulk_model = setting("GEMINI_RESEARCH_BULK_MODEL", DEFAULT_GEMINI_BULK_RESEARCH_MODEL)
    specialist_model = setting("GEMINI_RESEARCH_SPECIALIST_MODEL", DEFAULT_GEMINI_SPECIALIST_MODEL)
    st.info(
        f"Model routing: **{bulk_model}** handles broad grounded research → "
        f"**{specialist_model}** handles adversarial specialist review → "
        "**Trading Lab validation** handles historical proof. "
        "The cloud worker is separate from Streamlit, so once its GitHub Actions secrets are connected, "
        "this queue can continue while your Mac is off."
    )

    if not research_system.get("last_worker_at"):
        with st.expander("Finish cloud worker connection", expanded=False):
            st.write(
                "The worker code and hourly schedule are already installed. GitHub Actions still needs "
                "its own copies of the credentials that currently live in Streamlit Secrets."
            )
            st.code(
                "\n".join(
                    (
                        "GEMINI_API_KEY",
                        "GEMINI_PAID_API_KEY   # optional fallback",
                        "TRADING_LAB_BACKUP_REPOSITORY   # same value as Streamlit GITHUB_BACKUP_REPOSITORY",
                        "TRADING_LAB_BACKUP_TOKEN        # same value as Streamlit GITHUB_BACKUP_TOKEN",
                        "ALPACA_API_KEY",
                        "ALPACA_SECRET_KEY",
                    )
                ),
                language="text",
            )
            st.caption(
                "Add these as GitHub repository Actions secrets. Never paste the actual secret values into app code."
            )

    queue_cycle_col, queue_refresh_col = st.columns([1.4, 1.0])
    with queue_cycle_col:
        cycle_button_slot = st.empty()
        seed_cycle = cycle_button_slot.button(
            "Run today's research cycle now",
            type="primary",
            width="stretch",
            key="til_seed_continuous_research",
        )
        if seed_cycle:
            cycle_button_slot.button(
                "Queuing today's research…",
                type="primary",
                width="stretch",
                disabled=True,
                key="til_seed_continuous_research_busy",
            )
            queued_library, added_jobs = seed_continuous_research_cycle(load_library(force_cloud_refresh=True, mutable=True))
            if added_jobs:
                intelligence_store().save(queued_library)
                st.success(
                    f"Queued {added_jobs} grounded research topics for the cloud worker. "
                    "Flash will research them first; Pro reviews the resulting hypotheses."
                )
                st.rerun()
            else:
                st.info("Today's continuous research cycle is already queued.")
    with queue_refresh_col:
        st.caption(
            "Runs automatically once per UTC day. The hourly cloud worker keeps processing the durable queue. "
            "Use the button only when you want to seed today's cycle immediately."
        )

    recent_queue = [
        item for item in library.get("research_queue") or []
        if isinstance(item, dict)
    ][:12]
    if recent_queue:
        with st.expander("Recent cloud research jobs", expanded=False):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Type": str(item.get("type") or "").replace("_", " ").title(),
                            "Status": str(item.get("status") or "").replace("_", " ").title(),
                            "Priority": item.get("priority"),
                            "Attempts": item.get("attempts"),
                            "Updated": item.get("updated_at"),
                            "Result": item.get("result_ref") or "",
                        }
                        for item in recent_queue
                    ]
                ),
                width="stretch",
                hide_index=True,
            )

    recent_external = grounded_runs[:3]
    if recent_external:
        with st.expander("Latest grounded research", expanded=False):
            for run in recent_external:
                st.markdown(
                    f"**{run.get('title') or run.get('topic') or 'Grounded research'}**  "
                    f"· {run.get('model') or 'Gemini'}"
                )
                st.write(str(run.get("summary") or "")[:1400])
                sources = [
                    item for item in run.get("sources") or []
                    if isinstance(item, dict)
                ]
                if sources:
                    st.caption(
                        "Source quality: "
                        + " · ".join(
                            f"{str(item.get('source_type') or 'unknown').replace('_', ' ')} "
                            f"{int(item.get('source_quality_score') or 0)}/100"
                            for item in sources[:5]
                        )
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
        width="stretch",
        disabled=not managed_strategies,
        key="til_run_full_autonomous_research",
    )
    if run_auto:
        auto_button_slot.button(
            "🤖 Researching…",
            type="primary",
            width="stretch",
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
                    setting("GEMINI_RULE_COMPILER_MODEL", DEFAULT_GEMINI_SPECIALIST_MODEL),
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
                prepared_library = load_library(mutable=True)
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
            data = merge_autonomous_research_into_library(load_library(mutable=True), report)
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
                width="stretch",
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
                        width="stretch",
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
    guided_validation_mode = bool(st.session_state.get("til_guided_validation_mode"))
    if guided_validation_mode:
        st.success(
            "Guided validation mode: the strategy and ticker from Step 2 are preloaded. "
            "Walk-forward testing is required and already turned on. Keep the defaults unless you "
            "have a specific reason to change them, then click Optimize + validate strategy."
        )
    else:
        st.caption(
            "Choose a strategy from any source, download historical Alpaca candles, optimize only on "
            "earlier sessions, then evaluate separate validation and untouched holdout periods."
        )

    if integrity_blocked_count:
        st.warning(
            f"{integrity_blocked_count} strategy "
            f"{'family is' if integrity_blocked_count == 1 else 'families are'} excluded from backtesting "
            "because the historical engine cannot yet reproduce important source logic. "
            "Review Advanced → Strategy Integrity Audit."
        )

    if not integrity_safe_strategies:
        st.info(
            "No strategy is currently faithful enough for Strategy Lab testing. "
            "Expand the missing machine rules before treating a backtest as meaningful."
        )
    else:
        strategy_pool = list(integrity_safe_strategies)
        handed_candidate = st.session_state.get("til_strategy_lab_candidate_payload")
        if isinstance(handed_candidate, dict) and handed_candidate.get("id"):
            strategy_pool = [
                dict(handed_candidate),
                *[
                    item
                    for item in strategy_pool
                    if str(item.get("id") or "") != str(handed_candidate.get("id") or "")
                ],
            ]

        strategy_labels: dict[str, dict[str, Any]] = {}
        for item in strategy_pool:
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
            [effective_strategy_for_research(item) for item in integrity_safe_strategies]
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
            if key not in {
                "stop_loss_pct",
                "reward_risk",
                "max_hold_minutes",
                "trailing_stop_pct",
                "move_stop_to_breakeven_at_r",
                "scale_out_fraction_pct",
                "scale_out_at_r",
                "exit_below_vwap",
                "exit_below_fast_ema",
            } and value is not None
        )
        if entry_rule_count == 0 and not compare_all:
            st.warning(
                "This strategy currently has no objective entry/filter rule that the backtester can "
                "enforce. The AI may have extracted only qualitative conditions. It should be translated "
                "into measurable rules before treating a backtest as meaningful."
            )

        top = st.columns(4)
        ticker = top[0].text_input(
            "Stock ticker",
            value=str(st.session_state.get("til_strategy_lab_ticker") or "SDOT"),
            max_chars=10,
        ).strip().upper()
        st.session_state["til_strategy_lab_ticker"] = ticker
        guided_history_days = int(
            safe_float(st.session_state.get("til_strategy_lab_history_days"), 30) or 30
        )
        history_days = top[1].slider(
            "Historical calendar days",
            7,
            180,
            max(7, min(180, guided_history_days)),
            1,
        )
        timeframe_options = ["1Min", "5Min", "15Min"]
        guided_timeframe = str(st.session_state.get("til_strategy_lab_timeframe") or "5Min")
        timeframe = top[2].selectbox(
            "Candle size",
            timeframe_options,
            index=(
                timeframe_options.index(guided_timeframe)
                if guided_timeframe in timeframe_options
                else 1
            ),
        )
        search_depth = top[3].selectbox(
            "Optimization depth",
            [12, 36, 96, 160],
            index=2 if guided_validation_mode else 1,
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
        max_drawdown = float(
            risk_cols[3].number_input(
                "Validation drawdown ceiling (%)",
                1.0,
                20.0,
                15.0,
                1.0,
                help="The shared strict validation protocol never permits a drawdown ceiling above 20%.",
            )
        )

        with st.expander("Advanced validation settings", expanded=False):
            v1, v2, v3, v4 = st.columns(4)
            training_fraction = v1.slider("Training share", 0.40, 0.75, 0.60, 0.05)
            validation_fraction = v2.slider("Validation share", 0.10, 0.35, 0.20, 0.05)
            minimum_training_trades = v3.number_input("Minimum training trades", 1, 50, 5, 1)
            minimum_validation_trades = v4.number_input("Minimum validation/holdout trades", 1, 25, 2, 1)
            run_walk_forward = st.checkbox(
                "Run rolling walk-forward re-optimization",
                value=True if guided_validation_mode else False,
                disabled=guided_validation_mode,
                help=(
                    "Required before a manual Strategy Lab result can be saved as validated. "
                    "Each fold re-optimizes using only earlier sessions, freezes the winner, "
                    "and tests it on the next unseen block."
                ),
            )
            if run_walk_forward:
                w1, w2, w3 = st.columns(3)
                wf_history_sessions = int(w1.number_input("Minimum prior sessions per fold", 5, 60, 8, 1))
                wf_test_sessions = int(w2.number_input("Unseen sessions per fold", 1, 10, 2, 1))
                wf_folds = int(w3.number_input("Walk-forward folds", 2, 6, 3, 1))
            else:
                wf_history_sessions, wf_test_sessions, wf_folds = 8, 2, 3

        split_ok = training_fraction + validation_fraction <= 0.90
        if not split_ok:
            st.error("Training + validation must leave at least 10% of sessions untouched for final holdout.")

        strategy_lab_slot = st.empty()
        run_lab = strategy_lab_slot.button(
            "🧪 Optimize + validate strategy",
            type="primary",
            width="stretch",
            disabled=not ticker or not split_ok or (entry_rule_count == 0 and not compare_all),
            key="til_optimize_validate_strategy",
        )

        if run_lab:
            strategy_lab_slot.button(
                "🧪 Optimizing…",
                type="primary",
                width="stretch",
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
                    adjustment="raw",
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

                split_actions = market.research_reset_actions(
                    [ticker],
                    start=start_time,
                    end=end_time,
                )
                rows, lab_market_data_integrity = split_safe_raw_research_rows(
                    rows,
                    split_actions,
                    ticker,
                )
                if not rows:
                    raise AppError(
                        f"No split-safe raw-price history remained for {ticker}."
                    )
                if lab_market_data_integrity.get("corporate_action_reset_detected"):
                    update_task_bar(
                        task_bar,
                        lab_monitor,
                        0.26,
                        "Corporate-action integrity guard · raw-price research restarted at "
                        f"{lab_market_data_integrity.get('latest_split_date')}",
                    )

                blocked_spread_candidates = [
                    item
                    for item in candidates
                    if normalize_machine_rules(item.get("machine_rules")).get("max_spread_pct")
                    is not None
                ]
                if blocked_spread_candidates:
                    if compare_all:
                        blocked_ids = {
                            str(item.get("id") or "")
                            for item in blocked_spread_candidates
                        }
                        candidates = [
                            item
                            for item in candidates
                            if str(item.get("id") or "") not in blocked_ids
                        ]
                        if not candidates:
                            raise AppError(
                                "Every selected strategy requires a historical max-spread rule, "
                                "which remains fail-closed until quote history is fully integrated "
                                "as an entry filter."
                            )
                    else:
                        raise AppError(
                            "This strategy requires max_spread_pct. Strategy Lab will not validate "
                            "that rule using a fixed spread/slippage proxy; use it only after "
                            "point-in-time quote filtering is implemented."
                        )

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
                    stress_cost_multiplier=1.75,
                    automatic_slippage=True,
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
                winner = report.get("winner") or {}
                winner_source_id = str(winner.get("source_strategy_id") or "")
                winner_source = next(
                    (
                        item
                        for item in candidates
                        if str(item.get("id") or "") == winner_source_id
                    ),
                    None,
                )
                stability_report = {}
                if walk_report and winner_source is not None:
                    update_task_bar(
                        task_bar,
                        lab_monitor,
                        0.97,
                        "Testing nearby parameter stability on untouched holdout",
                    )
                    stability_report = parameter_stability_test(
                        rows,
                        winner_source,
                        report,
                        maximum=min(24, max(12, int(search_depth) // 4)),
                    )

                winner = report.get("winner") or {}
                optimized_settings_for_spread = (
                    winner.get("optimized_backtest_settings") or {}
                )
                sensitivity_multipliers = [
                    safe_float(value)
                    for value in (
                        (report.get("optimization_settings") or {}).get(
                            "execution_sensitivity_multipliers"
                        )
                        or (1.25, 1.5, 1.75, 2.0)
                    )
                ]
                lab_spread_audit = historical_entry_spread_audit(
                    market,
                    ticker,
                    list((report.get("winning_backtest") or {}).get("trades") or []),
                    list(report.get("holdout_sessions") or []),
                    modeled_spread_bps=(
                        safe_float(optimized_settings_for_spread.get("spread_bps"), 12.0)
                        or 12.0
                    ),
                    maximum_stress_multiplier=max(
                        [value for value in sensitivity_multipliers if value is not None]
                        or [2.0]
                    ),
                )
                integrity_wrapper = apply_historical_spread_integrity_guard(
                    {
                        "symbol": ticker,
                        "timeframe": timeframe,
                        "optimization": report,
                        "robustness": strength,
                    },
                    lab_spread_audit,
                )
                current_integrity_library = load_library(
                    force_cloud_refresh=True,
                    mutable=True,
                )
                integrity_wrapper = apply_holdout_reuse_guard(
                    current_integrity_library,
                    integrity_wrapper,
                )
                exposure_library = record_holdout_exposure(
                    current_integrity_library,
                    integrity_wrapper,
                    source="manual_strategy_lab",
                    generated_at=str(report.get("generated_at") or ""),
                )
                intelligence_store().save(exposure_library)

                report = integrity_wrapper.get("optimization") or report
                strength = integrity_wrapper.get("robustness") or strength
                winner = report.get("winner") or winner
                lab_holdout_reuse_audit = (
                    integrity_wrapper.get("holdout_reuse_audit") or {}
                )

                evidence_verdict = finder_evidence_verdict(
                    strength,
                    stability_report,
                    walk_report or {},
                    report,
                )
                paper_fidelity = {}
                if winner_source is not None:
                    paper_fidelity = paper_execution_fidelity(
                        {
                            **winner_source,
                            "validation_status": "research_only",
                            "validated_rules": None,
                            "machine_rules": (
                                winner.get("optimized_rules")
                                or winner_source.get("machine_rules")
                                or {}
                            ),
                        }
                    )
                evidence_verdict = apply_paper_fidelity_to_verdict(
                    evidence_verdict,
                    paper_fidelity,
                )

                st.session_state["til_strategy_lab_result"] = {
                    "ticker": ticker,
                    "timeframe": timeframe,
                    "history_days": history_days,
                    "report": report,
                    "walk_forward": walk_report,
                    "strength": strength,
                    "parameter_stability": stability_report,
                    "evidence_verdict": evidence_verdict,
                    "paper_execution_fidelity": paper_fidelity,
                    "historical_spread_audit": lab_spread_audit,
                    "holdout_reuse_audit": lab_holdout_reuse_audit,
                    "market_data_integrity": lab_market_data_integrity,
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
            stability_report = lab_result.get("parameter_stability") or {}
            evidence_verdict = lab_result.get("evidence_verdict") or finder_evidence_verdict(
                strength,
                stability_report,
                walk_report or {},
                report,
            )
            paper_fidelity = lab_result.get("paper_execution_fidelity") or {}
            training = winner.get("training_metrics") or {}
            validation = winner.get("validation_metrics") or {}
            holdout = winner.get("holdout_metrics") or {}
            stress = winner.get("stress_metrics") or {}

            st.divider()
            st.markdown(f"## Result · {lab_result.get('ticker')}")
            headline = st.columns(6)
            headline[0].metric("Robustness score", f"{safe_float(strength.get('score'), 0.0):.1f}/100")
            headline[1].metric("Grade", strength.get("label") or "—")
            headline[2].metric("Evidence tier", str(evidence_verdict.get("research_tier") or "research").replace("_", " ").title())
            headline[3].metric("Selected strategy", winner.get("strategy_name") or "—")
            headline[4].metric("Optimizer status", winner.get("status") or "—")
            headline[5].metric("Variants tested", f"{int(report.get('variants_tested') or 0):,}")
            st.caption(strength.get("note") or "")
            lab_spread_display = lab_result.get("historical_spread_audit") or {}
            lab_reuse_display = lab_result.get("holdout_reuse_audit") or {}
            lab_market_display = lab_result.get("market_data_integrity") or {}
            integrity_cols = st.columns(3)
            integrity_cols[0].metric(
                "Holdout freshness",
                "Pristine" if lab_reuse_display.get("pristine", True) else "Reused",
            )
            integrity_cols[1].metric(
                "Real spread audit",
                str(lab_spread_display.get("status") or "Not sampled").replace("_", " ").title(),
            )
            integrity_cols[2].metric(
                "Price-history contract",
                "Raw · post-action" if lab_market_display.get("corporate_action_reset_detected") else "Raw",
            )
            if not lab_reuse_display.get("pristine", True):
                st.warning(str(lab_reuse_display.get("note") or "Final holdout has been reused."))
            if str(lab_spread_display.get("status") or "") == "UNDERMODELED":
                st.error(
                    "Observed bid/ask spreads at sampled untouched-holdout entries exceeded "
                    "the largest spread assumption in the tested cost curve."
                )
            elif str(lab_spread_display.get("status") or "") in {"LIMITED", "LIMITED_FEED"}:
                st.warning(
                    "Historical quote confirmation at holdout entries was limited by coverage or "
                    "a non-consolidated feed, so SIP/NBBO execution confirmation remains incomplete."
                )

            if not walk_report:
                st.info(
                    "This manual result is exploratory. Walk-forward was not run, so it cannot be saved as validated even if the selected historical period is profitable."
                )
            elif str(evidence_verdict.get("code") or "") != "ready_for_paper":
                st.warning(
                    f"**{evidence_verdict.get('label') or 'Research-only result'}** — "
                    f"{evidence_verdict.get('reason') or 'One or more validation gates were not met.'}"
                )

            catalyst_summary = lab_result.get("catalyst_summary")
            if catalyst_summary:
                st.success(
                    "Point-in-time catalyst filter applied: "
                    f"{int(catalyst_summary.get('specific_catalysts') or 0)} classified catalyst events "
                    f"({int(catalyst_summary.get('positive_catalysts') or 0)} positive, "
                    f"{int(catalyst_summary.get('negative_catalysts') or 0)} negative). "
                    "News published after a bar is not visible to that bar."
                )

            development_execution_sensitivity = winner.get("execution_sensitivity") or {}
            holdout_execution_sensitivity = winner.get("holdout_execution_sensitivity") or {}
            execution_sensitivity = (
                holdout_execution_sensitivity
                or development_execution_sensitivity
            )
            execution_sensitivity_scope = (
                "Untouched holdout"
                if holdout_execution_sensitivity
                else "Validation"
            )
            sensitivity_points = [
                item
                for item in execution_sensitivity.get("points") or []
                if isinstance(item, dict)
            ]
            period_inputs = [
                ("Training", training),
                ("Validation", validation),
                ("Untouched holdout", holdout),
            ]
            if not sensitivity_points and not holdout_execution_sensitivity:
                period_inputs.append(("Higher-cost stress", stress))
            period_rows = []
            for name, metrics in period_inputs:
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
            st.dataframe(pd.DataFrame(period_rows), width="stretch", hide_index=True)

            if sensitivity_points:
                st.markdown(f"### {execution_sensitivity_scope} execution-cost sensitivity")
                sensitivity_cols = st.columns(4)
                sensitivity_cols[0].metric(
                    "Cost-curve grade",
                    str(execution_sensitivity.get("label") or "—").title(),
                )
                sensitivity_cols[1].metric(
                    "Sensitivity score",
                    f"{safe_float(execution_sensitivity.get('score'), 0.0):.1f}/100",
                )
                sensitivity_cols[2].metric(
                    "Stress points profitable",
                    f"{safe_float(execution_sensitivity.get('profitable_multiplier_pct'), 0.0):.0f}%",
                )
                median_retention = execution_sensitivity.get("median_pnl_retention_pct")
                sensitivity_cols[3].metric(
                    "Median P/L retained",
                    f"{safe_float(median_retention, 0.0):.0f}%" if median_retention is not None else "—",
                )
                sensitivity_rows = []
                for point in sensitivity_points:
                    metrics = point.get("metrics") or {}
                    retention = point.get("pnl_retention_pct")
                    sensitivity_rows.append(
                        {
                            "Execution cost": f"{safe_float(point.get('multiplier'), 0.0):.2f}×",
                            "Spread bps": round(safe_float(point.get("spread_bps"), 0.0) or 0.0, 2),
                            "Slippage bps": round(safe_float(point.get("slippage_bps"), 0.0) or 0.0, 2),
                            "Net P/L": safe_float(metrics.get("net_pnl"), 0.0) or 0.0,
                            "P/L retained %": (
                                safe_float(retention, 0.0) if retention is not None else None
                            ),
                            "Profit factor": metrics.get("profit_factor"),
                            "Profitable": bool(point.get("profitable")),
                        }
                    )
                st.dataframe(pd.DataFrame(sensitivity_rows), width="stretch", hide_index=True)
                first_break = execution_sensitivity.get("first_unprofitable_multiplier")
                if first_break is not None:
                    st.caption(
                        f"First tested cost level with non-positive P/L: {safe_float(first_break, 0.0):.2f}×."
                    )
                st.caption(str(execution_sensitivity.get("note") or ""))
            elif holdout_execution_sensitivity:
                st.caption(
                    "Untouched-holdout execution sensitivity is unavailable because the "
                    "baseline holdout did not have positive simulated P/L and at least one trade."
                )

            if strength.get("reasons"):
                with st.expander("Why the robustness score was reduced", expanded=False):
                    for reason in strength.get("reasons") or []:
                        st.write("• " + str(reason))

            if walk_report:
                summary = walk_report.get("summary") or {}
                st.markdown("### Rolling walk-forward")
                wf_cols = st.columns(6)
                wf_cols[0].metric("Walk-forward score", f"{safe_float(summary.get('score'), 0.0):.1f}/100")
                wf_cols[1].metric("Profitable folds", f"{safe_float(summary.get('profitable_fold_pct'), 0.0):.0f}%")
                wf_cols[2].metric("External trades", int(summary.get("external_trade_count") or 0))
                wf_cols[3].metric("External net P/L", f"${safe_float(summary.get('external_net_pnl'), 0.0):,.2f}")
                pf = summary.get("external_profit_factor")
                wf_cols[4].metric("External profit factor", f"{safe_float(pf, 0.0):.2f}" if pf is not None else "—")
                wf_cols[5].metric("Embargo", f"{int(summary.get('embargo_sessions') or 0)} session")

                fold_rows = []
                for fold in walk_report.get("folds") or []:
                    metrics = fold.get("external_metrics") or {}
                    fold_rows.append(
                        {
                            "Fold": fold.get("fold"),
                            "Optimized through": fold.get("history_end"),
                            "Embargo": (
                                f"{fold.get('embargo_start')} → {fold.get('embargo_end')}"
                                if fold.get("embargo_start")
                                else "None"
                            ),
                            "Unseen test": f"{fold.get('external_test_start')} → {fold.get('external_test_end')}",
                            "Strategy": fold.get("selected_strategy_name"),
                            "Trades": int(safe_float(metrics.get("trade_count"), 0) or 0),
                            "Net P/L": safe_float(metrics.get("net_pnl"), 0.0) or 0.0,
                            "Return %": safe_float(metrics.get("return_pct"), 0.0) or 0.0,
                            "Profit factor": metrics.get("profit_factor"),
                        }
                    )
                if fold_rows:
                    st.dataframe(pd.DataFrame(fold_rows), width="stretch", hide_index=True)
                for warning in walk_report.get("warnings") or []:
                    st.warning(str(warning))

            for warning in report.get("warnings") or []:
                st.warning(str(warning))

            winner_id = str(winner.get("source_strategy_id") or "")
            can_mark_validated = validated_status_ready(
                evidence_verdict,
                paper_fidelity,
                walk_report,
            )
            save_validation_slot = st.empty()
            save_validation = save_validation_slot.button(
                "💾 Save this validation result to the strategy library",
                width="stretch",
                key="til_save_strategy_validation",
            )
            if save_validation:
                save_validation_slot.button(
                    "💾 Saving validation result…",
                    width="stretch",
                    disabled=True,
                    key="til_save_strategy_validation_busy",
                )
                data = load_library(mutable=True)
                validation_status = "validated" if can_mark_validated else "research_only"
                saved_strategy_found = False
                for item in data.get("strategies") or []:
                    if str(item.get("id") or "") == winner_id:
                        saved_strategy_found = True
                        item["validation_status"] = validation_status
                        if validation_status == "validated":
                            item["validated_rules"] = winner.get("optimized_rules") or {}
                            item["validated_backtest_settings"] = winner.get("optimized_backtest_settings") or {}
                            item["validated_at"] = report.get("generated_at")
                        else:
                            item.pop("validated_rules", None)
                            item.pop("validated_backtest_settings", None)
                            item.pop("validated_at", None)
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
                            "execution_sensitivity": development_execution_sensitivity,
                            "holdout_execution_sensitivity": holdout_execution_sensitivity,
                            "walk_forward_summary": (walk_report or {}).get("summary"),
                            "parameter_stability": stability_report,
                            "evidence_verdict": evidence_verdict,
                            "paper_execution_fidelity": paper_fidelity,
                            "historical_spread_audit": lab_result.get("historical_spread_audit") or {},
                            "holdout_reuse_audit": lab_result.get("holdout_reuse_audit") or {},
                            "market_data_integrity": lab_result.get("market_data_integrity") or {},
                            "holdout_sessions": list(report.get("holdout_sessions") or []),
                        }
                        break

                if not saved_strategy_found:
                    handed_candidate = st.session_state.get("til_strategy_lab_candidate_payload")
                    if (
                        isinstance(handed_candidate, dict)
                        and str(handed_candidate.get("id") or "") == winner_id
                    ):
                        new_item = dict(handed_candidate)
                        new_item["source_type"] = "stock_specific_finder"
                        new_item["optimized_for_symbol"] = str(report.get("symbol") or "").upper()
                        new_item["validation_status"] = validation_status
                        if validation_status == "validated":
                            new_item["validated_rules"] = winner.get("optimized_rules") or {}
                            new_item["validated_backtest_settings"] = (
                                winner.get("optimized_backtest_settings") or {}
                            )
                            new_item["validated_at"] = report.get("generated_at")
                        else:
                            new_item.pop("validated_rules", None)
                            new_item.pop("validated_backtest_settings", None)
                            new_item.pop("validated_at", None)
                        new_item["last_validation"] = {
                            "symbol": report.get("symbol"),
                            "generated_at": report.get("generated_at"),
                            "robustness_score": strength.get("score"),
                            "robustness_label": strength.get("label"),
                            "optimizer_status": winner.get("status"),
                            "training_metrics": training,
                            "validation_metrics": validation,
                            "holdout_metrics": holdout,
                            "stress_metrics": stress,
                            "execution_sensitivity": development_execution_sensitivity,
                            "holdout_execution_sensitivity": holdout_execution_sensitivity,
                            "walk_forward_summary": (walk_report or {}).get("summary"),
                            "parameter_stability": stability_report,
                            "evidence_verdict": evidence_verdict,
                            "paper_execution_fidelity": paper_fidelity,
                            "historical_spread_audit": lab_result.get("historical_spread_audit") or {},
                            "holdout_reuse_audit": lab_result.get("holdout_reuse_audit") or {},
                            "market_data_integrity": lab_result.get("market_data_integrity") or {},
                            "holdout_sessions": list(report.get("holdout_sessions") or []),
                        }
                        data["strategies"] = [new_item, *(data.get("strategies") or [])]

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
                    "execution_sensitivity": development_execution_sensitivity,
                    "holdout_execution_sensitivity": holdout_execution_sensitivity,
                    "walk_forward_summary": (walk_report or {}).get("summary"),
                    "parameter_stability": stability_report,
                    "evidence_verdict": evidence_verdict,
                    "paper_execution_fidelity": paper_fidelity,
                    "historical_spread_audit": lab_result.get("historical_spread_audit") or {},
                    "holdout_reuse_audit": lab_result.get("holdout_reuse_audit") or {},
                    "market_data_integrity": lab_result.get("market_data_integrity") or {},
                    "holdout_sessions": list(report.get("holdout_sessions") or []),
                    "holdout_fingerprint": (
                        (lab_result.get("holdout_reuse_audit") or {}).get("holdout_fingerprint")
                    ),
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
                        "This candidate met the same strict validation gate used by Stock Strategy Finder."
                        if validation_status == "validated"
                        else (
                            f"It remains research-only as {str(evidence_verdict.get('research_tier') or 'research').replace('_', ' ')}. "
                            "Historical profitability is still saved and visible; it is simply not labeled validated."
                        )
                    )
                )


elif module == "Universe Research":
    st.caption(
        "Run one frozen strategy unchanged across several stocks. This is designed to expose ticker-specific "
        "overfitting: a strategy that only works on one symbol should look narrow here."
    )

    if integrity_blocked_count:
        st.warning(
            f"{integrity_blocked_count} low-fidelity strategy "
            f"{'family is' if integrity_blocked_count == 1 else 'families are'} excluded from cross-stock research."
        )

    if not integrity_safe_strategies:
        st.info(
            "No strategy family is currently faithful enough for cross-stock research. "
            "Review Advanced → Strategy Integrity Audit first."
        )
    else:
        universe_choices = {}
        for item in sorted(
            integrity_safe_strategies,
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
            width="stretch",
            disabled=len(universe_symbols) < 2,
            key="til_test_strategy_across_stocks",
        )
        if run_universe:
            universe_slot.button(
                "🧬 Testing…",
                type="primary",
                width="stretch",
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
                    adjustment="raw",
                    max_pages=40,
                    progress=universe_history_progress,
                )
                split_actions = market.research_reset_actions(
                    universe_symbols,
                    start=start_time,
                    end=end_time,
                )
                universe_integrity_by_symbol: dict[str, dict[str, Any]] = {}
                for symbol in universe_symbols:
                    safe_rows, integrity = split_safe_raw_research_rows(
                        list(rows_by_symbol.get(symbol) or []),
                        split_actions,
                        symbol,
                    )
                    rows_by_symbol[symbol] = safe_rows
                    universe_integrity_by_symbol[symbol] = integrity
                update_task_bar(
                    universe_bar,
                    universe_monitor,
                    0.50,
                    "Split-safe raw historical candles ready",
                )

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
                report["market_data_integrity_by_symbol"] = universe_integrity_by_symbol
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
                st.dataframe(pd.DataFrame(table_rows), width="stretch", hide_index=True)
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
            reuse = run.get("holdout_reuse_audit") or {}
            spread_audit = run.get("historical_spread_audit") or {}
            market_integrity = run.get("market_data_integrity") or {}
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
                    "Holdout freshness": (
                        "Pristine" if reuse.get("pristine", True) else "Reused"
                    ),
                    "Real spread audit": spread_audit.get("status") or "—",
                    "Price history": (
                        "Raw · post-action"
                        if market_integrity.get("corporate_action_reset_detected")
                        else ("Raw" if market_integrity else "Legacy/unknown")
                    ),
                }
            )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        st.caption(
            "Validation history is evidence tracking, not a leaderboard. Large historical P/L with weak "
            "holdout or walk-forward behavior should rank below a smaller but more stable result."
        )



elif module == "Pattern Validation":
    st.caption(
        "Advanced detector audit. The Lab replays historical candles one at a time so every "
        "VWAP, breakout, bounce, pullback, and stair-step event is recorded only when it was "
        "actually knowable. These results remain observational and do not change live scores."
    )

    pv_cols = st.columns([1.8, 1.0, 1.0])
    pattern_symbols_text = pv_cols[0].text_input(
        "Stocks to validate",
        value=str(st.session_state.get("til_pattern_validation_symbols") or "SDOT REAX"),
        help="Keep this targeted here; broad multi-stock validation can use the same backend in cloud batches.",
        key="til_pattern_validation_symbols_input",
    )
    pattern_days = int(
        pv_cols[1].slider(
            "Trading days",
            1,
            10,
            3,
            1,
            key="til_pattern_validation_days",
        )
    )
    pv_cols[2].metric("Resolution", "1 minute")
    pattern_symbols = [
        token.strip().upper()
        for token in pattern_symbols_text.replace(",", " ").split()
        if token.strip()
    ][:5]

    detector_labels = {
        str(spec.get("label") or key): key
        for key, spec in DETECTOR_SPECS.items()
    }
    default_detector_labels = list(detector_labels)[:]
    with st.expander("Choose detectors", expanded=False):
        selected_detector_labels = st.multiselect(
            "Historical detector scorecards",
            list(detector_labels),
            default=default_detector_labels,
            key="til_pattern_validation_detectors",
            label_visibility="collapsed",
        )
    selected_detectors = [
        detector_labels[label]
        for label in selected_detector_labels
        if label in detector_labels
    ]

    st.info(
        "This view is deliberately bounded to 5 stocks and 10 trading days because causal replay is much "
        "more rigorous than a normal indicator calculation: each historical candle is processed "
        "as if later candles do not exist. Weekends and market holidays do not count toward the selected days."
    )

    pattern_slot = st.empty()
    run_pattern_validation = pattern_slot.button(
        "▦ Run historical pattern validation",
        type="primary",
        width="stretch",
        disabled=not pattern_symbols or not selected_detectors,
        key="til_run_pattern_validation",
    )
    if run_pattern_validation:
        pattern_slot.button(
            "▦ Validating patterns…",
            type="primary",
            width="stretch",
            disabled=True,
            key="til_run_pattern_validation_busy",
        )
        st.session_state["til_pattern_validation_symbols"] = " ".join(pattern_symbols)
        pattern_monitor = long_task_monitor("pattern_detector_validation")
        pattern_bar = st.progress(
            0.03,
            text=pattern_monitor.text(0.03, "Preparing causal detector replay…"),
        )
        try:
            status_box = st.status("Preparing historical detector replay…", expanded=True)
            progress_state = {"replayed": 0}

            def pattern_validation_progress(message: str) -> None:
                status_box.write(message)
                text = str(message or "")
                fraction = 0.12
                if text.startswith("Loading historical"):
                    fraction = 0.18
                elif text.startswith("Replaying detector history"):
                    progress_state["replayed"] += 1
                    fraction = min(
                        0.92,
                        0.22
                        + 0.70
                        * progress_state["replayed"]
                        / max(1, len(pattern_symbols)),
                    )
                update_task_bar(
                    pattern_bar,
                    pattern_monitor,
                    fraction,
                    text or "Validating detector history",
                )

            validation_end = utc_now()
            # Fetch a conservative calendar buffer, then trim the downloaded bars to
            # the requested number of actual America/New_York market sessions.
            calendar_lookback_days = max(7, pattern_days * 2 + 3)
            validation_start = validation_end - timedelta(days=calendar_lookback_days)
            scorecards = run_detector_scorecards(
                market_client(),
                pattern_symbols,
                start=validation_start,
                end=validation_end,
                timeframe="1Min",
                horizons=(ml_horizon,),
                swing_radius=3,
                detectors=selected_detectors,
                max_pages=80,
                session_limit=pattern_days,
                progress=pattern_validation_progress,
            )
            evidence_gate = evaluate_scorecard_report(scorecards)
            st.session_state["til_pattern_validation_result"] = {
                "symbols": pattern_symbols,
                "days": pattern_days,
                "trading_days": pattern_days,
                "detectors": selected_detectors,
                "report": scorecards,
                "evidence_gate": evidence_gate,
            }
            status_box.update(
                label=(
                    f"Pattern validation complete · "
                    f"{scorecards.get('symbols_with_data', 0)} stocks with data"
                ),
                state="complete",
                expanded=False,
            )
            complete_task_bar(
                pattern_bar,
                pattern_monitor,
                "Historical pattern validation complete",
            )
            st.rerun()
        except AppError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Pattern validation failed: {exc}")

    stored_pattern_result = st.session_state.get("til_pattern_validation_result") or {}
    pattern_report = stored_pattern_result.get("report") or {}
    pattern_gate = stored_pattern_result.get("evidence_gate") or (
        evaluate_scorecard_report(pattern_report) if pattern_report else {}
    )
    if pattern_report:
        st.divider()
        st.markdown("### Detector scorecards")
        total_events = sum(
            int((item or {}).get("event_count") or 0)
            for item in (pattern_report.get("summary") or {}).values()
        )
        evidence_counts = Counter(
            str((item or {}).get("sample_quality") or "SPARSE")
            for item in (pattern_report.get("summary") or {}).values()
        )
        score_cols = st.columns(4)
        score_cols[0].metric("Stocks with data", int(pattern_report.get("symbols_with_data") or 0))
        score_cols[1].metric(
            "Market days loaded",
            int(pattern_report.get("market_sessions_observed") or 0),
            help="Unique U.S. market-session dates actually present in the downloaded data.",
        )
        score_cols[2].metric("Pattern events", total_events)
        eligible_detectors = list(pattern_gate.get("eligible_detectors") or [])
        score_cols[3].metric(
            "Scoring candidates",
            len(eligible_detectors),
        )

        score_rows = []
        for detector, item in (pattern_report.get("summary") or {}).items():
            gate = (pattern_gate.get("detectors") or {}).get(detector) or {}
            horizons = item.get("horizons") or {}
            h5 = horizons.get("5") or {}
            h15 = horizons.get("15") or {}
            h30 = horizons.get("30") or {}
            score_rows.append(
                {
                    "Detector": item.get("label") or detector,
                    "Evidence gate": str(gate.get("status") or "—").replace("_", " ").title(),
                    "Sample": item.get("sample_quality"),
                    "Events": int(item.get("event_count") or 0),
                    "Stocks": int(item.get("symbols_with_events") or 0),
                    "Market days": int(item.get("unique_market_days") or 0),
                    "Max one-stock share %": safe_float(item.get("max_symbol_event_share_pct")),
                    "5m avg return %": safe_float(h5.get("avg_return_pct")),
                    "15m avg return %": safe_float(h15.get("avg_return_pct")),
                    "30m avg return %": safe_float(h30.get("avg_return_pct")),
                    "15m directional hit %": safe_float(h15.get("directional_hit_pct")),
                    "15m directional return %": safe_float(h15.get("avg_directional_return_pct")),
                    "Avg MFE %": safe_float(item.get("avg_max_favorable_excursion_pct")),
                    "Avg MAE %": safe_float(item.get("avg_max_adverse_excursion_pct")),
                }
            )

        if score_rows:
            st.dataframe(
                pd.DataFrame(score_rows),
                width="stretch",
                hide_index=True,
            )
            st.caption(
                "SPARSE / LIMITED / MODERATE / BROAD describes sample breadth only. "
                "The evidence gate also requires multi-stock/time breadth, low concentration, a conservative "
                "95% hit-rate confidence bound, positive multi-horizon behavior, and favorable excursion quality. "
                "A 'Candidate For Scoring' still does not automatically become a trading rule."
            )
        else:
            st.info(
                "No selected detector produced a completed causal event in this historical window. "
                "Increase the history window or use stocks that actually exhibited the pattern."
            )

        gates = pattern_gate.get("detectors") or {}
        if gates:
            with st.expander("Why detectors passed or failed the evidence gate", expanded=False):
                for detector, gate in gates.items():
                    label = str((DETECTOR_SPECS.get(detector) or {}).get("label") or detector)
                    status = str(gate.get("status") or "unknown").replace("_", " ").title()
                    st.markdown(f"**{label} — {status}**")
                    for reason in gate.get("reasons") or []:
                        st.write("• " + str(reason))

        by_symbol = list(pattern_report.get("by_symbol") or [])
        if by_symbol:
            with st.expander("Coverage by stock", expanded=False):
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Stock": item.get("symbol"),
                                "Bars replayed": int(item.get("bars") or 0),
                                "Sessions": int(item.get("sessions") or 0),
                                "Pattern events": int(item.get("event_count") or 0),
                            }
                            for item in by_symbol
                        ]
                    ),
                    width="stretch",
                    hide_index=True,
                )


    st.divider()
    st.markdown("### Predictive ML Research")
    st.caption(
        "Build a cross-stock causal training set and test the Lab's first probability-model baseline. "
        "Every reported model metric is out-of-sample. This research does not change scanner rankings, "
        "strategy approval, Paper Auto, or live trading."
    )
    ml_backfill_status = (
        (library.get("research_system") or {}).get("predictive_ml_backfill_status")
        if isinstance(library.get("research_system"), dict)
        else {}
    ) or {}
    ml_backfill_state = str(ml_backfill_status.get("status") or "").strip().lower()
    if ml_backfill_state in {"queued", "running"}:
        state_label = "Queued" if ml_backfill_state == "queued" else "Running"
        st.info(
            f"🧠 Automatic ML backfill: {state_label}. "
            "The cloud worker is building historical labeled examples and retraining the "
            "research-only probability model without requiring this page to stay open."
        )
    elif ml_backfill_state == "complete":
        backfill_model_ready = bool(ml_backfill_status.get("shadow_scoring_enabled"))
        ready_model_count = int(ml_backfill_status.get("ready_model_count") or 0)
        learned_horizons = [
            int(value)
            for value in ml_backfill_status.get("horizons") or []
            if value is not None
        ]
        similarity_status = str(
            ml_backfill_status.get("similarity_status") or ""
        ).replace("_", " ").title()
        st.caption(
            "🧠 Automatic ML backfill complete · "
            f"{int(ml_backfill_status.get('symbols_with_data') or 0)} stocks · "
            f"{int(ml_backfill_status.get('trading_days') or 0)} trading days · "
            f"{int(ml_backfill_status.get('labeled_rows') or 0):,} labeled rows · "
            + (
                "horizons " + "/".join(str(value) for value in learned_horizons) + " min · "
                if learned_horizons
                else ""
            )
            + (
                f"{ready_model_count or 1} shadow model(s) passed historical gates."
                if backfill_model_ready
                else "all current candidates remain validation-gated."
            )
        )
        if similarity_status:
            similarity_count = len(ml_backfill_status.get("similarity_symbols") or [])
            st.caption(
                f"🔗 Automatic stock-similarity validation: {similarity_status}"
                + (
                    f" · {similarity_count} representative held-out stocks"
                    if similarity_count
                    else ""
                )
                + "."
            )
        learning_router_status = str(
            ml_backfill_status.get("learning_router_status") or ""
        ).replace("_", " ").title()
        if learning_router_status:
            router_compared = int(
                ml_backfill_status.get("learning_router_symbols_compared") or 0
            )
            router_clear = int(
                ml_backfill_status.get("learning_router_clear_routes") or 0
            )
            route_counts = dict(
                ml_backfill_status.get("learning_router_route_counts") or {}
            )
            route_bits = []
            for route_key, route_label in (
                ("same_ticker_history", "own-history"),
                ("similarity_weighted_transfer", "similar-stock"),
                ("broad_cross_stock_transfer", "broad-transfer"),
            ):
                count = int(route_counts.get(route_key) or 0)
                if count:
                    route_bits.append(f"{route_label} {count}")
            st.caption(
                f"🧭 Stock learning router: {learning_router_status}"
                + (
                    f" · {router_compared} stocks compared on identical unseen rows"
                    if router_compared
                    else ""
                )
                + (
                    f" · {router_clear} clear provisional route(s)"
                    if router_clear
                    else ""
                )
                + (
                    " · leaders: " + ", ".join(route_bits)
                    if route_bits
                    else ""
                )
                + "."
            )

            backfill_run_id = str(ml_backfill_status.get("run_id") or "").strip()
            predictive_runs = [
                item
                for item in library.get("predictive_ml_runs") or []
                if isinstance(item, dict)
            ]
            active_backfill_run = next(
                (
                    item
                    for item in predictive_runs
                    if str(item.get("id") or "").strip() == backfill_run_id
                ),
                predictive_runs[0] if predictive_runs else {},
            )
            learning_router = (
                dict(active_backfill_run.get("stock_learning_router") or {})
                if isinstance(active_backfill_run, dict)
                else {}
            )
            learning_route_rows = [
                item
                for item in learning_router.get("by_symbol") or []
                if isinstance(item, dict)
            ]
            if learning_route_rows:
                with st.expander(
                    "Per-stock learning route comparison",
                    expanded=False,
                ):
                    st.caption(
                        "Fair comparison: each learning method is scored on the same stock, "
                        "session, timestamp, and realized outcome. A winner is shown only when "
                        "its out-of-sample edge clears the router's materiality threshold."
                    )
                    route_table = []
                    for item in learning_route_rows:
                        routes = (
                            dict(item.get("routes") or {})
                            if isinstance(item.get("routes"), dict)
                            else {}
                        )
                        own_history = dict(routes.get("same_ticker_history") or {})
                        similar_stock = dict(
                            routes.get("similarity_weighted_transfer") or {}
                        )
                        broad_transfer = dict(
                            routes.get("broad_cross_stock_transfer") or {}
                        )
                        recommended_route = str(
                            item.get("recommended_route") or ""
                        ).strip()
                        best_label = (
                            str(item.get("recommended_route_label") or "").strip()
                            if recommended_route
                            else str(
                                item.get("provisional_lowest_brier_route_label")
                                or "No clear route"
                            ).strip()
                        )
                        evidence_label = (
                            "Clear provisional leader"
                            if recommended_route
                            else (
                                "No clear edge"
                                if item.get("status") == "EVALUATED"
                                else "More history needed"
                            )
                        )
                        route_table.append(
                            {
                                "Stock": item.get("symbol"),
                                "Evidence": evidence_label,
                                "Best learning source": best_label,
                                "Paired OOS rows": int(item.get("paired_oos_rows") or 0),
                                "Own-history Brier": safe_float(
                                    own_history.get("brier_score")
                                ),
                                "Similar-stock Brier": safe_float(
                                    similar_stock.get("brier_score")
                                ),
                                "Broad-transfer Brier": safe_float(
                                    broad_transfer.get("brier_score")
                                ),
                                "Own-history AUC": safe_float(
                                    own_history.get("roc_auc")
                                ),
                                "Similar-stock AUC": safe_float(
                                    similar_stock.get("roc_auc")
                                ),
                                "Broad-transfer AUC": safe_float(
                                    broad_transfer.get("roc_auc")
                                ),
                                "Why": item.get("reason") or "",
                            }
                        )
                    st.dataframe(
                        pd.DataFrame(route_table),
                        width="stretch",
                        hide_index=True,
                        column_config={
                            "Own-history Brier": st.column_config.NumberColumn(
                                format="%.4f"
                            ),
                            "Similar-stock Brier": st.column_config.NumberColumn(
                                format="%.4f"
                            ),
                            "Broad-transfer Brier": st.column_config.NumberColumn(
                                format="%.4f"
                            ),
                            "Own-history AUC": st.column_config.NumberColumn(
                                format="%.3f"
                            ),
                            "Similar-stock AUC": st.column_config.NumberColumn(
                                format="%.3f"
                            ),
                            "Broad-transfer AUC": st.column_config.NumberColumn(
                                format="%.3f"
                            ),
                        },
                    )
                    st.caption(
                        "Lower Brier is better probability accuracy; higher AUC is better "
                        "ranking/discrimination. These results are still research-only and "
                        "do not change live scanner ranking or execution."
                    )
    elif ml_backfill_state == "failed":
        st.warning(
            "Automatic ML backfill hit an error and will use the durable retry path: "
            + str(ml_backfill_status.get("last_error") or "unknown worker error")
        )

    historical_head_to_head = historical_shadow_head_to_head(library)
    historical_status = str(historical_head_to_head.get("status") or "")
    historical_models = list(historical_head_to_head.get("models") or [])
    if historical_status == "PROVISIONAL_HISTORICAL_LEADER":
        leader_family = str(
            historical_head_to_head.get("leader_model_family") or "Model"
        )
        st.success(
            "⚡ Historical head-to-head: "
            f"**{leader_family}** is the provisional historical leader. "
            + str(historical_head_to_head.get("reason") or "")
            + " Live shadow results will confirm or overturn this."
        )
    elif historical_status == "NO_CLEAR_HISTORICAL_LEADER":
        st.info(
            "⚖️ Historical head-to-head: no clear leader yet. "
            + str(historical_head_to_head.get("reason") or "")
            + " Live shadow evidence will break the tie."
        )

    if len(historical_models) >= 2:
        with st.expander("Historical model head-to-head", expanded=True):
            st.caption(
                "Fast path: this reuses the OOS results already produced by the completed "
                "historical backfill. No retraining is required. Models are compared only "
                "when they used the exact same target, training snapshot, and chronological "
                "untouched test folds."
            )
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Model": item.get("model_family"),
                            "OOS predictions": int(item.get("oos_rows") or 0),
                            "Walk-forward folds": int(item.get("fold_count") or 0),
                            "ROC AUC": safe_float(item.get("roc_auc")),
                            "Brier skill vs naive": (
                                None
                                if safe_float(item.get("brier_skill_vs_naive")) is None
                                else safe_float(item.get("brier_skill_vs_naive")) * 100.0
                            ),
                            "Brier score": safe_float(item.get("brier_score")),
                            "Held-out stock AUC": safe_float(item.get("held_out_roc_auc")),
                            "Held-out stock Brier skill": (
                                None
                                if safe_float(item.get("held_out_brier_skill_vs_naive")) is None
                                else safe_float(item.get("held_out_brier_skill_vs_naive")) * 100.0
                            ),
                        }
                        for item in historical_models
                    ]
                ),
                width="stretch",
                hide_index=True,
                column_config={
                    "ROC AUC": st.column_config.NumberColumn(format="%.3f"),
                    "Brier skill vs naive": st.column_config.NumberColumn(format="%.2f%%"),
                    "Brier score": st.column_config.NumberColumn(format="%.4f"),
                    "Held-out stock AUC": st.column_config.NumberColumn(format="%.3f"),
                    "Held-out stock Brier skill": st.column_config.NumberColumn(format="%.2f%%"),
                },
            )
            st.caption(
                "Historical leader = provisional champion. Live results remain the confirmation "
                "layer and can replace it once enough real matured decisions accumulate."
            )

    shadow_monitor = (
        (library.get("research_system") or {}).get("predictive_model_monitor")
        if isinstance(library.get("research_system"), dict)
        else {}
    ) or {}
    shadow_registry = (
        (library.get("research_system") or {}).get("predictive_model_registry")
        if isinstance(library.get("research_system"), dict)
        else {}
    ) or {}
    shadow_models_by_id = {
        str(item.get("model_id") or ""): item
        for item in shadow_monitor.get("models") or []
        if isinstance(item, dict) and str(item.get("model_id") or "").strip()
    }
    shadow_champion_id = active_shadow_champion_id(library)
    shadow_latest = (
        shadow_models_by_id.get(shadow_champion_id)
        or (
            shadow_monitor.get("latest_model")
            if isinstance(shadow_monitor.get("latest_model"), dict)
            else {}
        )
    )
    if shadow_registry.get("champion_model_id"):
        registry_status = str(
            shadow_registry.get("status") or "CHAMPION_PROVISIONAL"
        ).replace("_", " ").title()
        challenger_count = len(shadow_registry.get("challenger_model_ids") or [])
        champion_model = next(
            (
                item for item in shadow_probability_models(library)
                if str(item.get("id") or "") == shadow_champion_id
            ),
            {},
        )
        champion_family = str(
            champion_model.get("model_family")
            or champion_model.get("model_type")
            or "model"
        ).replace("_", " ")
        registry_reason = str(shadow_registry.get("decision_reason") or "")
        if (
            historical_status == "PROVISIONAL_HISTORICAL_LEADER"
            and registry_status == "Champion Provisional"
        ):
            registry_reason = (
                "Historical OOS leader is being used provisionally while live outcomes collect."
            )
        st.caption(
            "🏆 Shadow model registry · "
            f"{registry_status} · champion: {champion_family} · "
            f"{challenger_count} compatible challenger(s) · "
            + registry_reason
        )
    if shadow_latest:
        shadow_status = str(shadow_latest.get("status") or "COLLECTING").upper()
        monitor_icon = {
            "HEALTHY": "✅",
            "WATCH": "⚠️",
            "DRIFT_ALERT": "🚨",
            "COLLECTING": "🧪",
        }.get(shadow_status, "🧪")
        with st.expander(
            f"{monitor_icon} Shadow model health · {shadow_status.replace('_', ' ').title()}",
            expanded=shadow_status in {"WATCH", "DRIFT_ALERT"},
        ):
            monitor_cols = st.columns(5)
            monitor_cols[0].metric(
                "Matured decisions",
                f"{int(shadow_latest.get('evaluated_decisions') or 0):,}",
            )
            monitor_cols[1].metric(
                "Stocks",
                int(shadow_latest.get("symbol_count") or 0),
            )
            monitor_cols[2].metric(
                "Sessions",
                int(shadow_latest.get("session_count") or 0),
            )
            live_skill = safe_float(shadow_latest.get("brier_skill_vs_naive"))
            monitor_cols[3].metric(
                "Live Brier skill",
                "—" if live_skill is None else f"{live_skill * 100:.1f}%",
            )
            live_ece = safe_float(shadow_latest.get("expected_calibration_error"))
            monitor_cols[4].metric(
                "Calibration error",
                "—" if live_ece is None else f"{live_ece * 100:.1f}%",
            )
            st.caption(
                "Live shadow predictions are deduplicated into 30-minute stock decision points "
                "before evaluation, so repeated scanner refreshes do not masquerade as independent evidence. "
                "Validated challengers are scored on those same decisions and cannot replace the champion "
                "until they show enough breadth and a material live advantage."
            )
            for reason in list(shadow_latest.get("reasons") or [])[:3]:
                st.write("• " + str(reason))
            reliability_rows = list(shadow_latest.get("reliability_bins") or [])
            if reliability_rows:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Predicted range": (
                                    f"{float(item.get('bin_low') or 0) * 100:.0f}–"
                                    f"{float(item.get('bin_high') or 0) * 100:.0f}%"
                                ),
                                "Decisions": int(item.get("rows") or 0),
                                "Avg predicted %": (
                                    safe_float(item.get("mean_probability"), 0.0) * 100.0
                                ),
                                "Actual success %": (
                                    safe_float(item.get("observed_rate"), 0.0) * 100.0
                                ),
                            }
                            for item in reliability_rows
                        ]
                    ),
                    width="stretch",
                    hide_index=True,
                )
            st.caption(
                "This monitor is diagnostic only. It cannot change scanner ranking, strategy approval, "
                "position sizing, Paper Auto, or execution."
            )
    elif latest_shadow_probability_model(library):
        st.caption(
            "🧪 Shadow model health: collecting live matured outcomes. "
            "No performance verdict is shown until real shadow predictions have enough breadth."
        )

    ml_preset_cols = st.columns([1.35, 2.65])
    ml_benchmark_slot = ml_preset_cols[0].empty()
    load_ml_benchmark = ml_benchmark_slot.button(
        "Load broader 5-stock benchmark",
        width="stretch",
        key="til_load_broader_ml_benchmark",
    )
    if load_ml_benchmark:
        ml_benchmark_slot.button(
            "Loading benchmark preset…",
            width="stretch",
            disabled=True,
            key="til_load_broader_ml_benchmark_busy",
        )
        broader_symbols = "SDOT RR KULR FCEL ACHR"
        st.session_state["til_ml_symbols"] = broader_symbols
        st.session_state["til_ml_symbols_input"] = broader_symbols
        st.session_state["til_ml_history_days"] = 30
        st.session_state["til_ml_target_horizon"] = 15
        st.session_state["til_ml_target_mode_choice"] = "Trade-quality move"
        st.session_state["til_ml_profit_target_pct"] = 1.0
        st.session_state["til_ml_stop_loss_pct"] = 0.75
        st.session_state["til_ml_session_mode_choice"] = "Regular session"
        st.rerun()
    ml_preset_cols[1].caption(
        "Broader benchmark preset: SDOT · RR · KULR · FCEL · ACHR · "
        "30 trading days · 15-minute horizon · +1.00% before -0.75%."
    )

    if "til_ml_symbols_input" not in st.session_state:
        st.session_state["til_ml_symbols_input"] = str(
            st.session_state.get("til_ml_symbols")
            or pattern_symbols_text
            or "SDOT REAX"
        )

    ml_cols = st.columns([1.7, 0.9, 0.9, 1.0])
    ml_symbols_text = ml_cols[0].text_input(
        "ML research stocks",
        help="Start with a few related stocks. The backend supports broader cloud batches later.",
        key="til_ml_symbols_input",
    )
    ml_days = int(
        ml_cols[1].slider(
            "Trading days",
            12,
            30,
            20,
            1,
            key="til_ml_history_days",
        )
    )
    ml_horizon = int(
        ml_cols[2].selectbox(
            "Prediction horizon",
            options=[5, 15, 30],
            index=1,
            format_func=lambda value: f"{value} min",
            key="til_ml_target_horizon",
        )
    )
    ml_cols[3].metric("Model", "Logistic baseline")
    ml_symbols = [
        token.strip().upper()
        for token in ml_symbols_text.replace(",", " ").split()
        if token.strip()
    ][:5]

    with st.expander("ML target settings", expanded=False):
        ml_target_cols = st.columns([1.25, 1.2, 0.9, 0.9])
        ml_target_choice = ml_target_cols[0].selectbox(
            "Prediction target",
            options=["Trade-quality move", "Simply higher later"],
            index=0,
            help=(
                "Trade-quality move asks whether a meaningful upside target is reached before "
                "a downside limit. Simply higher later keeps the original benchmark."
            ),
            key="til_ml_target_mode_choice",
        )
        ml_target_mode = (
            "target_before_stop"
            if ml_target_choice == "Trade-quality move"
            else "positive_return"
        )
        ml_session_choice = ml_target_cols[1].selectbox(
            "Market hours",
            options=["Regular session", "Premarket", "After-hours"],
            index=0,
            help=(
                "Regular session uses 9:30 AM-4:00 PM ET, Premarket uses 4:00-9:30 AM ET, "
                "and After-hours uses 4:00-8:00 PM ET. Each run uses exactly one regime."
            ),
            key="til_ml_session_mode_choice",
        )
        ml_session_mode = {
            "Regular session": "regular",
            "Premarket": "premarket",
            "After-hours": "afterhours",
        }[ml_session_choice]
        ml_profit_target_pct = float(
            ml_target_cols[2].slider(
                "Upside target",
                min_value=0.25,
                max_value=5.0,
                value=1.0,
                step=0.25,
                format="%.2f%%",
                key="til_ml_profit_target_pct",
            )
        )
        ml_stop_loss_pct = float(
            ml_target_cols[3].slider(
                "Downside limit",
                min_value=0.25,
                max_value=3.0,
                value=0.75,
                step=0.25,
                format="%.2f%%",
                key="til_ml_stop_loss_pct",
            )
        )

    ml_run_similarity_validation = st.checkbox(
        "Also run continuous stock-similarity validation",
        value=True,
        help=(
            "Recommended research test. Each stock is held out completely, then historical rows "
            "with more similar prior-session VWAP, breakout, bounce, pullback, stair-step, volume, "
            "price, range, and liquidity behavior receive more influence without discarding other stocks."
        ),
        key="til_ml_run_similarity_validation",
    )
    # Retain the old archetype validator in code for research reproducibility, but
    # do not surface it in the main workflow now that hard-bucket transfer underperformed.
    ml_run_archetype_transfer = False

    if ml_target_mode == "target_before_stop":
        st.info(
            f"The baseline will predict whether price reaches +{ml_profit_target_pct:.2f}% "
            f"before -{ml_stop_loss_pct:.2f}% within the selected horizon. If both levels are "
            "touched in the same 1-minute candle, the downside level wins conservatively because "
            f"intrabar ordering is unknown. Market-hours regime: {ml_session_choice}. "
            "Trading days means actual U.S. market sessions; weekends and market holidays do not count. "
            "For interactive ML runs, predictions are sampled every 5 one-minute bars to reduce memory, "
            "while every underlying 1-minute candle still feeds the causal feature calculations."
        )
    else:
        st.info(
            "The baseline will use the original benchmark: whether price is simply higher at the "
            "end of the selected horizon. This is useful as a control, but it is less trade-relevant "
            "than the target-before-stop label."
        )
    ml_slot = st.empty()
    run_ml_baseline = ml_slot.button(
        "◈ Build dataset & run ML baseline",
        type="primary",
        width="stretch",
        disabled=not ml_symbols,
        key="til_run_predictive_ml_baseline",
    )
    if run_ml_baseline:
        ml_slot.button(
            "◈ Building causal ML dataset…",
            type="primary",
            width="stretch",
            disabled=True,
            key="til_run_predictive_ml_baseline_busy",
        )
        st.session_state["til_ml_symbols"] = " ".join(ml_symbols)
        ml_monitor = long_task_monitor("predictive_ml_baseline")
        ml_bar = st.progress(
            0.03,
            text=ml_monitor.text(0.03, "Preparing predictive ML research…"),
        )
        try:
            ml_status = st.status("Preparing causal ML dataset…", expanded=True)
            progress_state = {"built": 0}

            def ml_research_progress(message: str) -> None:
                ml_status.write(message)
                text = str(message or "")
                fraction = 0.10
                if text.startswith("Loading historical"):
                    fraction = 0.18
                elif text.startswith("ML stock "):
                    try:
                        position = text.split()[2]
                        stock_index_text, stock_total_text = position.split("/", 1)
                        stock_index = max(1, int(stock_index_text))
                        stock_total = max(1, int(stock_total_text))
                    except (IndexError, TypeError, ValueError):
                        stock_index = max(1, progress_state.get("built", 0) + 1)
                        stock_total = max(1, len(ml_symbols))

                    phase = 0.0
                    if "adding causal context" in text:
                        phase = 0.50
                    elif "finished " in text:
                        phase = 1.0
                        progress_state["built"] = max(
                            progress_state.get("built", 0),
                            stock_index,
                        )
                    completed_units = max(0.0, (stock_index - 1) + phase)
                    fraction = min(
                        0.78,
                        0.20 + 0.58 * completed_units / stock_total,
                    )
                update_task_bar(
                    ml_bar,
                    ml_monitor,
                    fraction,
                    text or "Building ML research dataset",
                )

            ml_market = market_client()
            ml_end = utc_now()
            if str(getattr(ml_market, "historical_feed", "sip")).lower() == "sip":
                ml_end -= timedelta(minutes=16)
                ml_status.write(
                    "Using a 16-minute SIP historical cutoff required by the current Alpaca entitlement."
                )
            else:
                ml_end -= timedelta(minutes=1)
            ml_calendar_lookback_days = max(20, ml_days * 2 + 5)
            ml_start = ml_end - timedelta(days=ml_calendar_lookback_days)
            ml_dataset = build_cross_stock_training_dataset(
                ml_market,
                ml_symbols,
                start=ml_start,
                end=ml_end,
                timeframe="1Min",
                horizons=(ml_horizon,),
                swing_radius=3,
                max_pages=120,
                require_full_horizon=True,
                session_limit=ml_days,
                profit_target_pct=ml_profit_target_pct,
                stop_loss_pct=ml_stop_loss_pct,
                session_mode=ml_session_mode,
                observation_stride_bars=5,
                progress=ml_research_progress,
            )
            update_task_bar(
                ml_bar,
                ml_monitor,
                0.84,
                "Running chronological walk-forward probability model…",
            )
            ml_evaluation = walk_forward_logistic_baseline(
                ml_dataset,
                target_horizon=ml_horizon,
                target_mode=ml_target_mode,
                min_train_sessions=8,
                test_sessions_per_fold=2,
                embargo_sessions=1,
                min_train_rows=250,
            )
            compact_ml_evaluation = {
                key: value
                for key, value in ml_evaluation.items()
                if key != "predictions"
            }
            completed_ml_result = {
                "symbols": ml_symbols,
                "days": ml_days,
                "trading_days": ml_days,
                "horizon": ml_horizon,
                "target_mode": ml_target_mode,
                "profit_target_pct": ml_profit_target_pct,
                "stop_loss_pct": ml_stop_loss_pct,
                "session_mode": ml_session_mode,
                "session_choice": ml_session_choice,
                "dataset_summary": {
                    key: value
                    for key, value in ml_dataset.items()
                    if key not in {"records"}
                },
                "evaluation": compact_ml_evaluation,
                "generalization": {
                    "status": "PENDING",
                    "reason": "Baseline saved; held-out-stock validation has not finished yet.",
                },
                "probability_model": {
                    "status": "PENDING",
                    "shadow_scoring_enabled": False,
                    "research_only": True,
                    "affects_live_ranking": False,
                    "reason": "Waiting for held-out-stock validation before training the portable model.",
                },
                "ticker_specific": {
                    "status": "PENDING",
                    "reason": "Ticker-specific validation has not finished yet.",
                },
                "archetype_validation": {},
                "completed_at": utc_now().isoformat(),
                "checkpoint_stage": "baseline_complete",
            }
            st.session_state["til_predictive_ml_result"] = completed_ml_result
            try:
                persist_predictive_ml_result(completed_ml_result)
                st.session_state["til_predictive_ml_persist_error"] = ""
                ml_status.write(
                    "Baseline result saved durably. Running stricter held-out-stock validation…"
                )
            except AppError as exc:
                st.session_state["til_predictive_ml_persist_error"] = str(exc)
                ml_status.write(
                    "Baseline finished, but the durable checkpoint could not be saved: "
                    + str(exc)
                )

            update_task_bar(
                ml_bar,
                ml_monitor,
                0.92,
                "Running held-out-stock walk-forward generalization test…",
            )
            ml_generalization = leave_one_symbol_out_walk_forward_logistic_baseline(
                ml_dataset,
                target_horizon=ml_horizon,
                target_mode=ml_target_mode,
                min_train_sessions=8,
                test_sessions_per_fold=2,
                embargo_sessions=1,
                min_train_rows=250,
                min_test_rows=25,
            )
            compact_ml_generalization = {
                key: value
                for key, value in ml_generalization.items()
                if key != "predictions"
            }
            completed_ml_result["generalization"] = compact_ml_generalization
            update_task_bar(
                ml_bar,
                ml_monitor,
                0.935,
                "Training portable calibrated shadow probability model…",
            )
            ml_probability_model = build_portable_probability_model(
                ml_dataset,
                target_horizon=ml_horizon,
                target_mode=ml_target_mode,
                generalization=ml_generalization,
                min_train_sessions=8,
                test_sessions_per_fold=2,
                embargo_sessions=1,
                min_train_rows=250,
            )
            completed_ml_result["probability_model"] = ml_probability_model
            if ml_probability_model.get("shadow_scoring_enabled"):
                ml_status.write(
                    "Portable probability model passed the shadow-scoring gates. "
                    "It remains research-only and cannot change rankings or execution."
                )
            else:
                ml_status.write(
                    "Portable probability candidate trained, but it remains gated off from "
                    "live shadow scoring until validation is strong enough."
                )
            completed_ml_result["checkpoint_stage"] = "generalization_complete"
            completed_ml_result["completed_at"] = utc_now().isoformat()
            st.session_state["til_predictive_ml_result"] = completed_ml_result
            try:
                persist_predictive_ml_result(completed_ml_result)
                st.session_state["til_predictive_ml_persist_error"] = ""
            except AppError as exc:
                st.session_state["til_predictive_ml_persist_error"] = str(exc)

            update_task_bar(
                ml_bar,
                ml_monitor,
                0.945,
                "Testing ticker-specific walk-forward models…",
            )
            ml_ticker_specific = ticker_specific_walk_forward_logistic_baseline(
                ml_dataset,
                target_horizon=ml_horizon,
                target_mode=ml_target_mode,
                min_train_sessions=8,
                test_sessions_per_fold=2,
                embargo_sessions=1,
                min_train_rows=150,
            )
            compact_ml_ticker_specific = {
                key: value
                for key, value in ml_ticker_specific.items()
                if key != "predictions"
            }
            completed_ml_result["ticker_specific"] = compact_ml_ticker_specific
            completed_ml_result["checkpoint_stage"] = "ticker_specific_complete"
            completed_ml_result["completed_at"] = utc_now().isoformat()
            st.session_state["til_predictive_ml_result"] = completed_ml_result
            try:
                persist_predictive_ml_result(completed_ml_result)
                st.session_state["til_predictive_ml_persist_error"] = ""
            except AppError as exc:
                st.session_state["til_predictive_ml_persist_error"] = str(exc)

            ml_similarity_validation = {}
            if ml_run_similarity_validation:
                update_task_bar(
                    ml_bar,
                    ml_monitor,
                    0.96,
                    "Testing continuous behavioral similarity weighting…",
                )
                ml_similarity_validation = (
                    similarity_weighted_leave_one_symbol_out_walk_forward_logistic_baseline(
                        ml_dataset,
                        target_horizon=ml_horizon,
                        target_mode=ml_target_mode,
                        min_train_sessions=8,
                        test_sessions_per_fold=2,
                        embargo_sessions=1,
                        min_train_rows=250,
                        min_test_rows=20,
                    )
                )
                compact_ml_similarity_validation = {
                    key: value
                    for key, value in ml_similarity_validation.items()
                    if key != "predictions"
                }
                completed_ml_result["similarity_validation"] = compact_ml_similarity_validation
                completed_ml_result["checkpoint_stage"] = "similarity_complete"
                completed_ml_result["completed_at"] = utc_now().isoformat()
                st.session_state["til_predictive_ml_result"] = completed_ml_result
                try:
                    persist_predictive_ml_result(completed_ml_result)
                    st.session_state["til_predictive_ml_persist_error"] = ""
                except AppError as exc:
                    st.session_state["til_predictive_ml_persist_error"] = str(exc)

            ml_archetype_validation = {}
            if ml_run_archetype_transfer:
                update_task_bar(
                    ml_bar,
                    ml_monitor,
                    0.96,
                    "Comparing within-archetype vs across-archetype transfer…",
                )
                ml_archetype_validation = archetype_transfer_walk_forward_logistic_baseline(
                    ml_dataset,
                    target_horizon=ml_horizon,
                    target_mode=ml_target_mode,
                    min_train_sessions=8,
                    test_sessions_per_fold=2,
                    embargo_sessions=1,
                    min_train_rows=200,
                    min_test_rows=20,
                )
                compact_ml_archetype_validation = {
                    key: value
                    for key, value in ml_archetype_validation.items()
                    if key != "predictions"
                }
                completed_ml_result["archetype_validation"] = compact_ml_archetype_validation
                completed_ml_result["checkpoint_stage"] = "archetype_complete"
                completed_ml_result["completed_at"] = utc_now().isoformat()
                st.session_state["til_predictive_ml_result"] = completed_ml_result
                try:
                    persist_predictive_ml_result(completed_ml_result)
                    st.session_state["til_predictive_ml_persist_error"] = ""
                except AppError as exc:
                    st.session_state["til_predictive_ml_persist_error"] = str(exc)

            ml_status.update(
                label=(
                    f"Predictive ML research complete · "
                    f"{ml_dataset.get('row_count', 0):,} labeled rows"
                ),
                state="complete",
                expanded=False,
            )
            complete_task_bar(
                ml_bar,
                ml_monitor,
                "Predictive ML baseline evaluation complete",
            )
            st.success("Predictive ML results are ready below.")
        except AppError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Predictive ML research failed: {exc}")

    stored_ml_result = st.session_state.get("til_predictive_ml_result") or {}
    ml_result_source = "session"
    if not stored_ml_result:
        durable_ml_runs = [
            item
            for item in library.get("predictive_ml_runs") or []
            if isinstance(item, dict) and item.get("evaluation")
        ]
        if durable_ml_runs:
            durable_ml_runs.sort(
                key=lambda item: str(item.get("completed_at") or ""),
                reverse=True,
            )
            stored_ml_result = deepcopy(durable_ml_runs[0])
            ml_result_source = "durable"
            st.session_state["til_predictive_ml_result"] = stored_ml_result

    ml_dataset_summary = stored_ml_result.get("dataset_summary") or {}
    ml_evaluation = stored_ml_result.get("evaluation") or {}
    ml_generalization = stored_ml_result.get("generalization") or {}
    ml_probability_model = stored_ml_result.get("probability_model") or {}
    ml_ticker_specific = stored_ml_result.get("ticker_specific") or {}
    ml_similarity_validation = stored_ml_result.get("similarity_validation") or {}
    ml_archetype_validation = stored_ml_result.get("archetype_validation") or {}
    if ml_evaluation:
        st.divider()
        completed_at = str(stored_ml_result.get("completed_at") or "").strip()
        result_symbols = " · ".join(str(symbol) for symbol in stored_ml_result.get("symbols") or [])
        result_days = int(stored_ml_result.get("trading_days") or stored_ml_result.get("days") or 0)
        result_horizon = int(stored_ml_result.get("horizon") or 0)
        source_note = "restored from durable storage" if ml_result_source == "durable" else "completed in this session"
        st.caption(
            f"Latest completed ML result · {result_symbols or 'saved benchmark'} · "
            f"{result_days} trading days · {result_horizon}-minute horizon · {source_note}"
            + (f" · {completed_at}" if completed_at else "")
        )
        persist_error = str(st.session_state.get("til_predictive_ml_persist_error") or "").strip()
        if persist_error:
            st.warning(
                "These results are visible now, but their durable cloud save failed: " + persist_error
            )
        if str(ml_evaluation.get("status")) != "EVALUATED":
            st.warning(
                "The baseline could not be evaluated yet: "
                + str(ml_evaluation.get("reason") or "not enough usable history.")
            )
        else:
            ml_metric_cols = st.columns(6)
            ml_metric_cols[0].metric("Training rows", f"{int(ml_dataset_summary.get('row_count') or 0):,}")
            ml_metric_cols[1].metric("Stocks", int(ml_dataset_summary.get("symbols_with_data") or 0))
            ml_metric_cols[2].metric("OOS predictions", f"{int(ml_evaluation.get('oos_rows') or 0):,}")
            ml_metric_cols[3].metric("Walk-forward folds", int(ml_evaluation.get("fold_count") or 0))
            auc_value = safe_float(ml_evaluation.get("roc_auc"))
            ml_metric_cols[4].metric("ROC AUC", "—" if auc_value is None else f"{auc_value:.3f}")
            skill_value = safe_float(ml_evaluation.get("brier_skill_vs_naive"))
            ml_metric_cols[5].metric(
                "Brier skill vs naive",
                "—" if skill_value is None else f"{skill_value * 100:.1f}%",
            )
            target_description = str(ml_evaluation.get("target_description") or "").strip()
            if target_description:
                st.caption("Prediction target: " + target_description)

            if ml_probability_model:
                portable_validation = ml_probability_model.get("validation") or {}
                portable_auc = safe_float(portable_validation.get("roc_auc"))
                portable_skill = safe_float(portable_validation.get("brier_skill_vs_naive"))
                if ml_probability_model.get("shadow_scoring_enabled"):
                    st.success(
                        "Shadow probability model ready · "
                        f"{int(ml_probability_model.get('feature_count') or 0)} live-compatible features · "
                        f"OOS AUC {portable_auc:.3f}" if portable_auc is not None else
                        "Shadow probability model ready for research-only scoring."
                    )
                    st.caption(
                        "These probabilities are now eligible to appear in Market Discovery and Stock Analyzer. "
                        "They still cannot change ranking, strategy approval, position sizing, or execution."
                    )
                elif str(ml_probability_model.get("status") or "") not in {"", "PENDING"}:
                    reasons = list(ml_probability_model.get("gate_reasons") or [])
                    st.info(
                        "Portable probability candidate trained but is still gated off from live shadow scoring"
                        + (": " + " ".join(str(reason) for reason in reasons[:3]) if reasons else ".")
                    )

            archetype_distribution = list(ml_dataset_summary.get("archetype_distribution") or [])
            context_feature_count = len(ml_dataset_summary.get("context_feature_columns") or [])
            if archetype_distribution:
                st.markdown("#### Causal stock context")
                st.caption(
                    f"{context_feature_count} context features are attached to each ML row using only "
                    "current/past bars and completed prior sessions. Historical float and catalyst-profile "
                    "features are intentionally excluded until point-in-time coverage is trustworthy."
                )
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Archetype": str(item.get("archetype") or "unknown").replace("_", " ").title(),
                                "Rows": int(item.get("rows") or 0),
                                "Stocks": int(item.get("symbol_count") or 0),
                                "Symbols": ", ".join(item.get("symbols") or []),
                            }
                            for item in archetype_distribution
                        ]
                    ),
                    width="stretch",
                    hide_index=True,
                )

            if skill_value is not None and skill_value > 0 and auc_value is not None and auc_value > 0.5:
                st.success(
                    "This baseline beat the naive probability benchmark on the combined out-of-sample rows. "
                    "That is encouraging evidence, not proof of a tradable edge; fold stability and broader "
                    "cross-stock/time testing still matter."
                )
            else:
                st.warning(
                    "This run did not demonstrate a reliable advantage over the naive probability benchmark. "
                    "Do not promote it to live scoring; use the result to improve features, labels, or the model."
                )

            fold_rows = []
            for fold in ml_evaluation.get("folds") or []:
                fold_rows.append(
                    {
                        "Fold": fold.get("fold"),
                        "Train sessions": fold.get("train_sessions"),
                        "Train rows": fold.get("train_rows"),
                        "Test sessions": ", ".join(fold.get("test_sessions") or []),
                        "Test rows": fold.get("test_rows"),
                        "ROC AUC": safe_float(fold.get("roc_auc")),
                        "Brier": safe_float(fold.get("brier_score")),
                        "Naive Brier": safe_float(fold.get("naive_brier_score")),
                        "Brier skill": safe_float(fold.get("brier_skill_vs_naive")),
                        "Accuracy": safe_float(fold.get("accuracy")),
                    }
                )
            if fold_rows:
                st.dataframe(pd.DataFrame(fold_rows), width="stretch", hide_index=True)

            st.markdown("#### Held-out-stock generalization")
            if str(ml_generalization.get("status") or "") != "EVALUATED":
                st.warning(
                    "Held-out-stock validation could not be evaluated yet: "
                    + str(
                        ml_generalization.get("reason")
                        or "not enough stocks or usable future sessions."
                    )
                )
            else:
                gen_cols = st.columns(4)
                gen_auc = safe_float(ml_generalization.get("roc_auc"))
                gen_skill = safe_float(ml_generalization.get("brier_skill_vs_naive"))
                gen_cols[0].metric(
                    "Held-out ROC AUC",
                    "—" if gen_auc is None else f"{gen_auc:.3f}",
                )
                gen_cols[1].metric(
                    "Held-out Brier skill",
                    "—" if gen_skill is None else f"{gen_skill * 100:.1f}%",
                )
                gen_cols[2].metric(
                    "Held-out predictions",
                    f"{int(ml_generalization.get('oos_rows') or 0):,}",
                )
                gen_cols[3].metric(
                    "Stocks rotated",
                    int(ml_generalization.get("symbol_count") or 0),
                )
                held_out_rows = []
                for item in ml_generalization.get("by_symbol") or []:
                    held_out_rows.append(
                        {
                            "Held-out stock": item.get("symbol"),
                            "Status": item.get("status"),
                            "OOS predictions": int(item.get("oos_rows") or 0),
                            "ROC AUC": safe_float(item.get("roc_auc")),
                            "Brier skill": safe_float(item.get("brier_skill_vs_naive")),
                            "Positive rate": safe_float(item.get("oos_positive_rate")),
                            "Folds": int(item.get("fold_count") or 0),
                        }
                    )
                if held_out_rows:
                    st.dataframe(
                        pd.DataFrame(held_out_rows),
                        width="stretch",
                        hide_index=True,
                    )
                st.caption(
                    "For each row above, that stock was excluded from every training row. "
                    "The model also trained only on earlier market sessions from the other stocks, "
                    "so this tests cross-stock transfer without ticker or future-session leakage."
                )

            st.markdown("#### Model architecture comparison")
            if str(ml_ticker_specific.get("status") or "") != "EVALUATED":
                st.warning(
                    "Ticker-specific validation could not be evaluated yet: "
                    + str(
                        ml_ticker_specific.get("reason")
                        or "not enough same-stock history for chronological training."
                    )
                )
            else:
                pooled_auc = safe_float(ml_evaluation.get("roc_auc"))
                pooled_skill = safe_float(ml_evaluation.get("brier_skill_vs_naive"))
                ticker_auc = safe_float(ml_ticker_specific.get("roc_auc"))
                ticker_macro_auc = safe_float(ml_ticker_specific.get("macro_roc_auc"))
                ticker_skill = safe_float(ml_ticker_specific.get("brier_skill_vs_naive"))
                held_auc = safe_float(ml_generalization.get("roc_auc"))
                held_skill = safe_float(ml_generalization.get("brier_skill_vs_naive"))

                architecture_rows = [
                    {
                        "Architecture": "Pooled chronological",
                        "Training source": "Earlier rows from all included stocks",
                        "ROC AUC": pooled_auc,
                        "Brier skill": pooled_skill,
                        "OOS predictions": int(ml_evaluation.get("oos_rows") or 0),
                    },
                    {
                        "Architecture": "Ticker-specific",
                        "Training source": "Only that ticker's own earlier sessions",
                        "ROC AUC": ticker_auc,
                        "Brier skill": ticker_skill,
                        "OOS predictions": int(ml_ticker_specific.get("oos_rows") or 0),
                    },
                    {
                        "Architecture": "Held-out stock",
                        "Training source": "Earlier sessions from other stocks only",
                        "ROC AUC": held_auc,
                        "Brier skill": held_skill,
                        "OOS predictions": int(ml_generalization.get("oos_rows") or 0),
                    },
                ]
                st.dataframe(
                    pd.DataFrame(architecture_rows),
                    width="stretch",
                    hide_index=True,
                )
                if ticker_macro_auc is not None:
                    st.caption(
                        f"Ticker-specific macro AUC across evaluated stocks: {ticker_macro_auc:.3f}. "
                        "Macro AUC gives each stock equal weight rather than letting the largest ticker dominate."
                    )

                if (
                    ticker_auc is not None
                    and held_auc is not None
                    and pooled_auc is not None
                    and ticker_auc > held_auc + 0.03
                    and ticker_auc >= pooled_auc - 0.01
                    and ticker_skill is not None
                    and ticker_skill > 0
                ):
                    st.success(
                        "Ticker-specific history is carrying meaningful predictive information. "
                        "That supports a shared causal feature engine with stock-specific predictive models "
                        "rather than forcing one universal cross-stock model."
                    )
                elif (
                    ticker_auc is not None
                    and pooled_auc is not None
                    and pooled_auc > ticker_auc + 0.03
                ):
                    st.info(
                        "The pooled chronological model still ranks outcomes better than the ticker-specific "
                        "models. Stock-specific modeling is not yet clearly superior."
                    )
                else:
                    st.info(
                        "The architecture comparison is mixed. Use the per-stock comparison below before "
                        "choosing between pooled and stock-specific models."
                    )

                held_by_symbol = {
                    str(item.get("symbol") or ""): item
                    for item in ml_generalization.get("by_symbol") or []
                }
                ticker_rows = []
                for item in ml_ticker_specific.get("by_symbol") or []:
                    symbol = str(item.get("symbol") or "")
                    held_item = held_by_symbol.get(symbol) or {}
                    own_auc = safe_float(item.get("roc_auc"))
                    other_auc = safe_float(held_item.get("roc_auc"))
                    ticker_rows.append(
                        {
                            "Stock": symbol,
                            "Status": item.get("status"),
                            "Own-history AUC": own_auc,
                            "Own-history Brier skill": safe_float(
                                item.get("brier_skill_vs_naive")
                            ),
                            "Other-stocks AUC": other_auc,
                            "Other-stocks Brier skill": safe_float(
                                held_item.get("brier_skill_vs_naive")
                            ),
                            "Own minus other AUC": (
                                None
                                if own_auc is None or other_auc is None
                                else own_auc - other_auc
                            ),
                            "Own-history OOS": int(item.get("oos_rows") or 0),
                        }
                    )
                if ticker_rows:
                    st.dataframe(
                        pd.DataFrame(ticker_rows),
                        width="stretch",
                        hide_index=True,
                    )
                st.caption(
                    "Ticker-specific models never train on another stock. They train only on earlier "
                    "sessions of the same ticker and are scored on later unseen sessions with the same "
                    "walk-forward embargo used elsewhere."
                )

            if ml_similarity_validation:
                st.markdown("#### Continuous behavioral similarity")
                if str(ml_similarity_validation.get("status") or "") != "EVALUATED":
                    st.warning(
                        "Similarity-weighted validation could not be evaluated: "
                        + str(
                            ml_similarity_validation.get("reason")
                            or "not enough continuous context or held-out history."
                        )
                    )
                else:
                    sim_cols = st.columns(4)
                    base_auc = safe_float(ml_similarity_validation.get("baseline_roc_auc"))
                    sim_auc = safe_float(ml_similarity_validation.get("similarity_roc_auc"))
                    auc_delta = safe_float(
                        ml_similarity_validation.get("similarity_minus_baseline_auc")
                    )
                    sim_skill = safe_float(
                        ml_similarity_validation.get("similarity_brier_skill_vs_naive")
                    )
                    sim_cols[0].metric(
                        "Unweighted AUC",
                        "—" if base_auc is None else f"{base_auc:.3f}",
                    )
                    sim_cols[1].metric(
                        "Similarity AUC",
                        "—" if sim_auc is None else f"{sim_auc:.3f}",
                    )
                    sim_cols[2].metric(
                        "AUC improvement",
                        "—" if auc_delta is None else f"{auc_delta:+.3f}",
                    )
                    sim_cols[3].metric(
                        "Similarity Brier skill",
                        "—" if sim_skill is None else f"{sim_skill * 100:.1f}%",
                    )
                    similarity_rows = []
                    for item in ml_similarity_validation.get("by_symbol") or []:
                        similarity_rows.append(
                            {
                                "Held-out stock": item.get("symbol"),
                                "Rows": int(item.get("oos_rows") or 0),
                                "Unweighted AUC": safe_float(item.get("baseline_roc_auc")),
                                "Similarity AUC": safe_float(item.get("similarity_roc_auc")),
                                "AUC improvement": safe_float(
                                    item.get("similarity_minus_baseline_auc")
                                ),
                                "Similarity Brier skill": safe_float(
                                    item.get("similarity_brier_skill_vs_naive")
                                ),
                            }
                        )
                    if similarity_rows:
                        st.dataframe(
                            pd.DataFrame(similarity_rows),
                            width="stretch",
                            hide_index=True,
                        )
                    st.caption(
                        "This test does not put stocks into fixed families. Every eligible historical "
                        "training row remains available, but rows with more similar completed-session VWAP, "
                        "breakout, bounce, pullback, stair-step, volume-acceleration, price, range, and "
                        "liquidity behavior receive more influence. The held-out stock is never used in "
                        "training, and the similarity fingerprint never uses the current session's future."
                    )

            if ml_archetype_validation:
                st.markdown("#### Within-archetype vs across-archetype transfer")
                if str(ml_archetype_validation.get("status") or "") != "EVALUATED":
                    st.warning(
                        "Archetype transfer comparison could not be evaluated: "
                        + str(ml_archetype_validation.get("reason") or "not enough family coverage.")
                    )
                else:
                    archetype_cols = st.columns(4)
                    within_auc = safe_float(ml_archetype_validation.get("within_roc_auc"))
                    across_auc = safe_float(ml_archetype_validation.get("across_roc_auc"))
                    auc_delta = safe_float(ml_archetype_validation.get("within_minus_across_auc"))
                    paired_rows = int(ml_archetype_validation.get("paired_oos_rows") or 0)
                    archetype_cols[0].metric(
                        "Same-family AUC",
                        "—" if within_auc is None else f"{within_auc:.3f}",
                    )
                    archetype_cols[1].metric(
                        "Other-family AUC",
                        "—" if across_auc is None else f"{across_auc:.3f}",
                    )
                    archetype_cols[2].metric(
                        "AUC advantage",
                        "—" if auc_delta is None else f"{auc_delta:+.3f}",
                    )
                    archetype_cols[3].metric("Paired OOS rows", f"{paired_rows:,}")
                    archetype_rows = []
                    for item in ml_archetype_validation.get("by_archetype") or []:
                        archetype_rows.append(
                            {
                                "Archetype": str(item.get("archetype") or "").replace("_", " ").title(),
                                "Rows": int(item.get("oos_rows") or 0),
                                "Same-family AUC": safe_float(item.get("within_roc_auc")),
                                "Other-family AUC": safe_float(item.get("across_roc_auc")),
                                "AUC advantage": safe_float(item.get("within_minus_across_auc")),
                                "Same-family Brier skill": safe_float(item.get("within_brier_skill_vs_naive")),
                                "Other-family Brier skill": safe_float(item.get("across_brier_skill_vs_naive")),
                            }
                        )
                    if archetype_rows:
                        st.dataframe(pd.DataFrame(archetype_rows), width="stretch", hide_index=True)
                    st.caption(
                        "Both models are scored on the exact same held-out-stock rows. The archetype label "
                        "is used to choose the training cohort but is removed from model inputs for this comparison."
                    )

            st.caption(
                "ROC AUC measures ranking ability (0.5 is random). Brier score measures probability accuracy "
                "(lower is better). Positive Brier skill means the model improved on a constant probability "
                "equal to the training-set positive rate. No model from this panel is used live."
            )


elif module == "Catalyst Intelligence":
    st.caption(
        "Combine timestamped market news with primary-source SEC EDGAR filings. "
        "The system keeps source timestamps and filing evidence visible, distinguishes fresh vs stale items, "
        "and flags dilution/offering risk conservatively without claiming a filing caused the price move."
    )

    sec_user_agent = setting("SEC_USER_AGENT")
    if not sec_user_agent:
        st.warning(
            "SEC filing intelligence is built but not enabled yet because SEC_USER_AGENT is missing from Streamlit Secrets. "
            "News intelligence will still work. To enable EDGAR, add a descriptive app/company name plus a real contact email "
            "to SEC_USER_AGENT so requests comply with SEC fair-access guidance."
        )

    cat_cols = st.columns([1.0, 1.0, 2.1])
    catalyst_ticker = cat_cols[0].text_input(
        "Catalyst ticker",
        value=str(st.session_state.get("til_catalyst_ticker") or "SDOT"),
        max_chars=10,
    ).strip().upper()
    catalyst_days = int(cat_cols[1].slider("History", 7, 180, 30, 1, key="til_catalyst_days"))
    cat_cols[2].caption(
        "News uses a deterministic event taxonomy. SEC evidence uses form + 8-K item semantics. "
        "Primary documents remain linked so ambiguous filings can be inspected instead of guessed."
    )

    catalyst_slot = st.empty()
    load_catalysts = catalyst_slot.button(
        "📰 Load news + SEC catalyst intelligence",
        type="primary",
        width="stretch",
        disabled=not catalyst_ticker,
        key="til_load_classify_catalysts",
    )
    if load_catalysts:
        catalyst_slot.button(
            "📰 Researching catalyst evidence…",
            type="primary",
            width="stretch",
            disabled=True,
            key="til_load_classify_catalysts_busy",
        )
        catalyst_monitor = long_task_monitor("historical_catalyst_research")
        catalyst_bar = st.progress(
            0.03,
            text=catalyst_monitor.text(0.03, f"Preparing {catalyst_ticker} catalyst intelligence…"),
        )
        try:
            st.session_state["til_catalyst_ticker"] = catalyst_ticker
            market = market_client()
            cat_end = utc_now()
            cat_start = cat_end - timedelta(days=catalyst_days)
            status_box = st.status(f"Loading {catalyst_ticker} market news…", expanded=True)

            def catalyst_page_progress(page: int) -> None:
                status_box.write(f"Historical news page {page}…")
                update_task_bar(
                    catalyst_bar,
                    catalyst_monitor,
                    0.05 + 0.48 * min(1.0, page / 60.0),
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
                0.56,
                f"Classifying {len(raw_articles)} news items",
            )
            classified_news = [classify_catalyst(item) for item in raw_articles]

            classified_sec: list[dict[str, Any]] = []
            sec_payload: dict[str, Any] = {}
            sec_error = ""
            if sec_user_agent:
                status_box.write("Loading primary-source SEC filing history…")
                update_task_bar(
                    catalyst_bar,
                    catalyst_monitor,
                    0.68,
                    "Loading SEC EDGAR filing evidence",
                )
                try:
                    sec_payload = SecEdgarClient(sec_user_agent).recent_filings(
                        catalyst_ticker,
                        days=catalyst_days,
                        limit=250,
                        as_of=cat_end,
                    )
                    classified_sec = classify_recent_sec_filings(sec_payload)
                except AppError as exc:
                    sec_error = str(exc)
                    status_box.write("SEC evidence unavailable: " + sec_error)

            update_task_bar(
                catalyst_bar,
                catalyst_monitor,
                0.84,
                "Ranking fresh, novel, and primary-source evidence",
            )
            ranked_evidence = rank_catalyst_evidence(
                classified_news,
                classified_sec,
                as_of=cat_end,
            )
            evidence_summary = catalyst_intelligence_summary(ranked_evidence)
            sec_summary = sec_filing_summary(classified_sec)

            st.session_state["til_catalyst_result"] = {
                "symbol": catalyst_ticker,
                "days": catalyst_days,
                "news_articles": classified_news,
                "sec_filings": classified_sec,
                "evidence": ranked_evidence,
                "summary": evidence_summary,
                "sec_summary": sec_summary,
                "sec_company": {
                    "name": sec_payload.get("company_name"),
                    "cik": sec_payload.get("cik"),
                } if sec_payload else {},
                "sec_error": sec_error,
                "sec_enabled": bool(sec_user_agent),
            }
            status_box.update(
                label=(
                    f"Catalyst intelligence complete · {len(classified_news)} news · "
                    f"{len(classified_sec)} SEC filings"
                ),
                state="complete",
                expanded=False,
            )
            complete_task_bar(
                catalyst_bar,
                catalyst_monitor,
                "Catalyst intelligence complete",
            )
            st.rerun()
        except AppError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Catalyst intelligence failed: {exc}")

    catalyst_result = st.session_state.get("til_catalyst_result") or {}
    if catalyst_result and catalyst_result.get("symbol") == catalyst_ticker:
        evidence = list(catalyst_result.get("evidence") or [])
        summary = catalyst_result.get("summary") or {}
        sec_summary = catalyst_result.get("sec_summary") or {}
        sec_company = catalyst_result.get("sec_company") or {}

        st.divider()
        if sec_company.get("name"):
            st.caption(
                f"SEC entity: {sec_company.get('name')} · CIK {sec_company.get('cik') or '—'}"
            )
        if catalyst_result.get("sec_error"):
            st.warning("SEC evidence could not be loaded for this run: " + str(catalyst_result.get("sec_error")))

        summary_cols = st.columns(5)
        summary_cols[0].metric("Evidence items", int(summary.get("evidence_items") or 0))
        summary_cols[1].metric("Fresh catalysts", int(summary.get("fresh_specific_catalysts") or 0))
        summary_cols[2].metric("SEC filings", int(sec_summary.get("filings") or 0))
        summary_cols[3].metric("Dilution flags", int(summary.get("dilution_risks") or 0))
        summary_cols[4].metric("High-severity SEC", int(sec_summary.get("high_severity_filings") or 0))

        filter_mode = st.radio(
            "Show",
            [
                "Highest relevance",
                "Fresh only",
                "SEC filings",
                "News only",
                "Dilution / offering risk",
                "All evidence",
            ],
            horizontal=True,
            key="til_catalyst_evidence_filter",
        )
        if filter_mode == "Fresh only":
            visible = [
                item for item in evidence
                if str(item.get("freshness") or "") in {"breaking", "fresh"}
                and item.get("is_specific_catalyst")
            ]
        elif filter_mode == "SEC filings":
            visible = [item for item in evidence if item.get("evidence_type") == "sec_filing"]
        elif filter_mode == "News only":
            visible = [item for item in evidence if item.get("evidence_type") == "news"]
        elif filter_mode == "Dilution / offering risk":
            visible = [item for item in evidence if item.get("is_dilution_risk")]
        elif filter_mode == "All evidence":
            visible = evidence
        else:
            visible = [
                item for item in evidence
                if item.get("is_specific_catalyst")
            ][:40]

        table_rows = []
        for item in visible:
            evidence_type = "SEC" if item.get("evidence_type") == "sec_filing" else "News"
            table_rows.append(
                {
                    "Type": evidence_type,
                    "Published (UTC)": item.get("published_at"),
                    "Freshness": str(item.get("freshness") or "unknown").title(),
                    "Novelty": str(item.get("novelty") or "—").title(),
                    "Category": item.get("category"),
                    "Base score": safe_float(item.get("score"), 0.0) or 0.0,
                    "Effective score": safe_float(item.get("effective_score"), 0.0) or 0.0,
                    "Form": item.get("form") if evidence_type == "SEC" else None,
                    "8-K items": ", ".join(item.get("items_list") or []) if evidence_type == "SEC" else None,
                    "Headline / filing": item.get("headline"),
                    "Source": item.get("source"),
                }
            )

        if table_rows:
            st.dataframe(pd.DataFrame(table_rows), width="stretch", hide_index=True)
            inspect_labels = {}
            for index, item in enumerate(visible):
                label = (
                    f"{'SEC' if item.get('evidence_type') == 'sec_filing' else 'News'} · "
                    f"{item.get('published_at') or 'Unknown time'} · {item.get('category')} · "
                    f"{str(item.get('headline') or '')[:60]}"
                )
                inspect_labels[f"{label} · #{index + 1}"] = item
            selected_item = inspect_labels[
                st.selectbox(
                    "Inspect catalyst evidence",
                    list(inspect_labels),
                    key="til_catalyst_inspect_evidence",
                )
            ]

            st.markdown(f"### {selected_item.get('category')}")
            st.write(selected_item.get("headline") or "No headline / filing description")
            if selected_item.get("summary"):
                st.write(selected_item.get("summary"))
            if selected_item.get("rationale"):
                st.info(str(selected_item.get("rationale")))

            detail_bits = [
                f"Published: {selected_item.get('published_at') or '—'}",
                f"Freshness: {str(selected_item.get('freshness') or 'unknown').title()}",
                f"Novelty: {str(selected_item.get('novelty') or '—').title()}",
                f"Source: {selected_item.get('source') or '—'}",
                f"Base score: {safe_float(selected_item.get('score'), 0.0):+.1f}",
                f"Freshness-adjusted evidence: {safe_float(selected_item.get('effective_score'), 0.0):+.1f}",
            ]
            st.caption(" · ".join(detail_bits))

            if selected_item.get("evidence_type") == "sec_filing":
                sec_details = []
                if selected_item.get("form"):
                    sec_details.append(f"Form {selected_item.get('form')}")
                if selected_item.get("items_list"):
                    sec_details.append("8-K items " + ", ".join(selected_item.get("items_list") or []))
                if selected_item.get("accessionNumber"):
                    sec_details.append("Accession " + str(selected_item.get("accessionNumber")))
                if sec_details:
                    st.write("SEC evidence: " + " · ".join(sec_details))
            elif selected_item.get("keywords"):
                st.write(
                    "Matched terms: "
                    + ", ".join(str(x) for x in selected_item.get("keywords") or [])
                )

            source_url = str(selected_item.get("url") or "").strip()
            if source_url:
                st.link_button("Open source evidence", source_url)
        else:
            st.info("No catalyst evidence matches this filter in the selected period.")


elif module == "Market Discovery":
    st.caption(
        "Scan a live stock universe against the usable strategy library. "
        "Validated status and current setup quality are shown separately so research candidates remain visible."
    )

    validated_strategies = [
        item for item in integrity_safe_strategies
        if str(item.get("validation_status") or "").lower() == "validated"
    ]
    requested_strategy_id = str(
        st.session_state.get("til_market_discovery_strategy_id") or ""
    )
    requested_strategy = next(
        (
            item
            for item in integrity_safe_strategies
            if str(item.get("id") or "") == requested_strategy_id
        ),
        None,
    )

    include_research = st.checkbox(
        "Include research-only strategy families",
        value=True,
        key="til_market_discovery_include_research",
        help=(
            "On by default so promising and historically profitable stock-specific candidates "
            "remain visible. Turn it off for fully validated strategies only."
        ),
    )
    discovery_strategies = integrity_safe_strategies if include_research else validated_strategies

    if requested_strategy is not None:
        discovery_strategies = [requested_strategy]
        st.info(
            f"Scanning the exact Finder setup: **{requested_strategy.get('name') or 'stock-specific candidate'}**. "
            "This may be research-only; its validation status will stay visible in the scan results."
        )
        clear_discovery_slot = st.empty()
        clear_discovery = clear_discovery_slot.button(
            "Clear setup filter and scan the full strategy library",
            key="til_clear_market_discovery_strategy_filter",
        )
        if clear_discovery:
            clear_discovery_slot.button(
                "Loading full strategy library…",
                disabled=True,
                key="til_clear_market_discovery_strategy_filter_busy",
            )
            st.session_state.pop("til_market_discovery_strategy_id", None)
            st.session_state["_trading_app_boot_message"] = (
                "Loading full Market Discovery strategy library…"
            )
            st.rerun()

    if integrity_blocked_count:
        st.warning(
            f"{integrity_blocked_count} strategy "
            f"{'family is' if integrity_blocked_count == 1 else 'families are'} excluded because "
            "important source logic is not yet faithfully modeled. See Advanced → Strategy Integrity Audit."
        )

    if not discovery_strategies:
        st.info(
            "No validated strategies are available yet. Turn on research-only strategy families "
            "or let the research pipeline produce validated families first."
        )
    else:
        st.markdown(
            f"**Automatic strategy coverage:** {len(discovery_strategies)} strategy "
            f"{'family' if len(discovery_strategies) == 1 else 'families'} will be checked against every stock."
        )
        if requested_strategy is not None:
            requested_status = str(
                requested_strategy.get("validation_status") or "research_only"
            ).replace("_", " ").title()
            st.caption(
                f"Setup status: {requested_status}. A research-only match is a lead, not a proven edge."
            )
        elif include_research:
            st.caption(
                f"{len(validated_strategies)} validated · "
                f"{max(0, len(discovery_strategies) - len(validated_strategies))} research-only. "
                "Research-only matches are useful leads, not proven edges."
            )

        scan_cols = st.columns([1.25, 1.0, 1.9])
        universe_mode = scan_cols[0].selectbox(
            "Stocks to scan",
            ["Momentum universe", "Top gainers", "Most active", "Custom watchlist"],
            key="til_market_discovery_universe",
            help=(
                "Momentum universe blends Alpaca's ranked gainers and most-active lists, then "
                "removes duplicates before the batched strategy scan."
            ),
        )
        universe_limits = {
            "Momentum universe": 150,
            "Top gainers": 50,
            "Most active": 100,
            "Custom watchlist": MAX_LIVE_SCAN_SYMBOLS,
        }
        universe_defaults = {
            "Momentum universe": 50,
            "Top gainers": 30,
            "Most active": 50,
            "Custom watchlist": 50,
        }
        candidate_limit = int(universe_limits[universe_mode])
        candidate_default = min(candidate_limit, int(universe_defaults[universe_mode]))
        candidate_key = (
            "til_market_discovery_candidates_"
            + universe_mode.casefold().replace(" ", "_")
        )
        candidate_count = int(
            scan_cols[1].slider(
                "How many stocks",
                5,
                candidate_limit,
                candidate_default,
                5,
                key=candidate_key,
            )
        )
        custom_symbols = scan_cols[2].text_input(
            "Custom tickers",
            placeholder="SDOT LUCY REAX ...",
            disabled=universe_mode != "Custom watchlist",
            key="til_market_discovery_custom",
            help=f"Custom live scans support up to {MAX_LIVE_SCAN_SYMBOLS} unique symbols per run.",
        )
        estimated_batches = max(
            1,
            (candidate_count + LIVE_SCAN_BATCH_SIZE - 1) // LIVE_SCAN_BATCH_SIZE,
        )
        st.caption(
            f"Up to {candidate_count} candidates · processed in about {estimated_batches} "
            f"batch{'es' if estimated_batches != 1 else ''} of {LIVE_SCAN_BATCH_SIZE} stocks. "
            "Each batch shares market-data downloads across all strategies."
        )

        scan_slot = st.empty()
        scan_now = scan_slot.button(
            "🔎 Find the best opportunities now",
            type="primary",
            width="stretch",
            key="til_scan_current_market",
        )
        if scan_now:
            scan_slot.button(
                "🔎 Scanning every strategy…",
                type="primary",
                width="stretch",
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
                if universe_mode == "Momentum universe":
                    gainers = market.movers(top=min(50, candidate_count))
                    active = market.most_active(top=min(100, candidate_count))
                    symbols = merge_momentum_candidate_universe(
                        gainers,
                        active,
                        limit=candidate_count,
                    )
                elif universe_mode == "Top gainers":
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
                    0.16,
                    f"Comparing {len(discovery_strategies)} strategies across {len(symbols)} stocks",
                )
                status_box.write(
                    f"Comparing {len(discovery_strategies)} strategy families across "
                    f"{len(symbols)} stocks…"
                )

                def market_scan_progress(message: str) -> None:
                    status_box.write(message)
                    text = str(message or "")
                    lower = text.casefold()
                    batch_index = 1
                    batch_total = max(
                        1,
                        (len(symbols) + LIVE_SCAN_BATCH_SIZE - 1) // LIVE_SCAN_BATCH_SIZE,
                    )
                    if lower.startswith("batch ") and " · " in text:
                        try:
                            batch_token = text.split(" · ", 1)[0].split(" ", 1)[1]
                            batch_index, batch_total = [
                                int(value) for value in batch_token.split("/", 1)
                            ]
                        except (IndexError, TypeError, ValueError):
                            batch_index = 1
                    stage_fraction = 0.08
                    if "relative-volume" in lower:
                        stage_fraction = 0.35
                    elif "catalyst" in lower:
                        stage_fraction = 0.58
                    elif "intraday" in lower:
                        stage_fraction = 0.80
                    completed_before = max(0, batch_index - 1)
                    fraction = 0.18 + 0.76 * min(
                        1.0,
                        (completed_before + stage_fraction) / max(1, batch_total),
                    )
                    update_task_bar(scan_bar, scan_monitor, fraction, text)

                results = scan_market_strategies(
                    market,
                    symbols,
                    discovery_strategies,
                    progress=market_scan_progress,
                )
                results = apply_shadow_probability_scores(
                    results,
                    shadow_probability_models(library),
                    champion_model_id=active_shadow_champion_id(library),
                )
                try:
                    st.session_state["til_live_learning_market_discovery_status"] = (
                        persist_live_learning_cycle(
                            market,
                            results,
                            source="market_discovery",
                        )
                    )
                except AppError as exc:
                    st.session_state["til_live_learning_market_discovery_status"] = {
                        "error": str(exc),
                        "research_only": True,
                    }
                st.session_state["til_market_discovery_result"] = {
                    "universe_mode": universe_mode,
                    "requested_candidates": candidate_count,
                    "candidate_symbols": list(symbols),
                    "batch_size": LIVE_SCAN_BATCH_SIZE,
                    "strategy_count": len(discovery_strategies),
                    "include_research": include_research,
                    "results": results,
                }
                status_box.update(
                    label=(
                        f"Scan complete · {len(results)} stocks × "
                        f"{len(discovery_strategies)} strategies"
                    ),
                    state="complete",
                    expanded=False,
                )
                complete_task_bar(scan_bar, scan_monitor, "Market-wide strategy scan complete")
                st.rerun()
            except AppError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Market scan failed: {exc}")

        discovery_result = st.session_state.get("til_market_discovery_result") or {}
        live_results = list(discovery_result.get("results") or [])
        if live_results:
            st.divider()
            st.markdown("### Best opportunities found")
            live_learning_status = (
                st.session_state.get("til_live_learning_market_discovery_status") or {}
            )
            if live_learning_status.get("error"):
                st.caption(
                    "Live learning is research-only and did not affect this scan. "
                    "Its durable save/outcome update was unavailable: "
                    + str(live_learning_status.get("error"))
                )
            elif live_learning_status.get("logged"):
                st.caption(
                    "🧠 Live learning (research only) · "
                    f"logged {int(live_learning_status.get('logged') or 0)} shadow observations · "
                    f"matured {int(live_learning_status.get('matured') or 0)} prior outcomes · "
                    f"{int(live_learning_status.get('total') or 0)} durable observations total. "
                    "This does not affect live rankings."
                )
            scanned_candidates = len(discovery_result.get("candidate_symbols") or [])
            scan_batch_size = int(discovery_result.get("batch_size") or LIVE_SCAN_BATCH_SIZE)
            scanned_batches = max(
                1,
                (scanned_candidates + scan_batch_size - 1) // scan_batch_size,
            )
            st.caption(
                "Each stock is paired with its highest-ranked strategy. Validated strategies rank ahead "
                "of research-only strategies, then current setup quality, robustness, and rule match are considered. "
                f"Candidate universe: {scanned_candidates} stocks across {scanned_batches} "
                f"batch{'es' if scanned_batches != 1 else ''}."
            )

            table_rows = []
            for item in live_results:
                metrics = item.get("metrics") or {}
                validation = str(item.get("validation_status") or "unvalidated").replace("_", " ").title()
                table_rows.append(
                    {
                        "Stock": item.get("symbol"),
                        "Best strategy": item.get("best_strategy_name"),
                        "Strategy status": validation,
                        "Current setup": item.get("status"),
                        "Rule match %": safe_float(item.get("score"), 0.0) or 0.0,
                        "Robustness": item.get("robustness_score"),
                        "Other matching strategies": max(
                            0,
                            int(item.get("matching_strategy_count") or 0) - 1,
                        ),
                        "Price": safe_float(metrics.get("price")),
                        "Day move %": safe_float(metrics.get("day_change_pct")),
                        "RVOL": safe_float(metrics.get("relative_volume")),
                        "Spread %": safe_float(metrics.get("spread_pct")),
                        "ML probability": (
                            safe_float((item.get("ml_prediction") or {}).get("probability")) * 100.0
                            if safe_float((item.get("ml_prediction") or {}).get("probability")) is not None
                            else None
                        ),
                    }
                )
            st.dataframe(pd.DataFrame(table_rows), width="stretch", hide_index=True)

            matches = [
                item for item in live_results
                if str(item.get("status") or "").upper() == "MATCH"
            ]
            validated_matches = [
                item for item in matches
                if str(item.get("validation_status") or "").lower() == "validated"
            ]
            summary_cols = st.columns(4)
            summary_cols[0].metric("Strong matches", len(matches))
            summary_cols[1].metric("Validated matches", len(validated_matches))
            summary_cols[2].metric("Stocks evaluated", len(live_results))
            summary_cols[3].metric(
                "Strategies checked",
                int(discovery_result.get("strategy_count") or 0),
            )

            inspect_labels = {
                (
                    f"{item.get('symbol')} · {item.get('best_strategy_name')} · "
                    f"{item.get('status')} · {safe_float(item.get('score'), 0.0):.0f}%"
                ): item
                for item in live_results
            }
            inspected = inspect_labels[
                st.selectbox(
                    "Inspect an opportunity",
                    list(inspect_labels),
                    key="til_market_discovery_inspect",
                )
            ]
            metrics = inspected.get("metrics") or {}
            signal = inspected.get("signal") or {}
            validation = str(
                inspected.get("validation_status") or "unvalidated"
            ).replace("_", " ").title()

            st.markdown(
                f"**{inspected.get('symbol')} → {inspected.get('best_strategy_name')}**  "
                f"· {validation} · {inspected.get('status')} · "
                f"{safe_float(inspected.get('score'), 0.0):.0f}% rule match"
            )
            if validation.lower() != "validated":
                st.warning(
                    "The best match for this stock is still a research-only strategy. "
                    "Treat it as a lead for further research, not a proven trade signal."
                )

            detail_cols = st.columns(5)
            detail_cols[0].metric(
                "Price",
                "USD " + f"{safe_float(metrics.get('price'), 0.0):,.4f}",
            )
            detail_cols[1].metric(
                "Day move",
                f"{safe_float(metrics.get('day_change_pct'), 0.0):+.2f}%",
            )
            rvol = safe_float(metrics.get("relative_volume"))
            detail_cols[2].metric(
                "Relative volume",
                f"{rvol:.2f}×" if rvol is not None else "—",
            )
            detail_cols[3].metric(
                "Rule match",
                f"{safe_float(signal.get('score'), 0.0):.0f}%",
            )
            inspected_ml = inspected.get("ml_prediction") or {}
            inspected_probability = safe_float(inspected_ml.get("probability"))
            detail_cols[4].metric(
                "ML probability",
                "—" if inspected_probability is None else f"{inspected_probability * 100:.1f}%",
            )
            if inspected_probability is not None:
                st.caption(
                    "ML probability is research-only and predicts: "
                    + str(inspected_ml.get("target_description") or "the configured ML target")
                    + ". It does not affect this ranking."
                )

            checks = signal.get("checks") or []
            if checks:
                with st.expander("Why the best strategy matched", expanded=True):
                    for check in checks:
                        state = str(check.get("status") or "").upper()
                        icon = "✅" if state == "PASS" else "❓" if state == "UNKNOWN" else "❌"
                        st.write(
                            f"{icon} **{check.get('label') or 'Rule'}** — "
                            f"current: {check.get('actual')} · required: {check.get('required')}"
                        )

            alternate_matches = list(inspected.get("strategy_matches") or [])[1:]
            if alternate_matches:
                with st.expander("Other strategies that fit this stock", expanded=False):
                    for alternate in alternate_matches:
                        alt_validation = str(
                            alternate.get("validation_status") or "unvalidated"
                        ).replace("_", " ").title()
                        st.write(
                            f"**{alternate.get('strategy_name')}** · {alt_validation} · "
                            f"{alternate.get('status')} · "
                            f"{safe_float(alternate.get('score'), 0.0):.0f}% rule match"
                        )


elif module == "Stock Analyzer":
    render_guided_strategy_flow(active_step=2)
    st.caption(
        "Use this page in order: compare the Step-1 candidates, validate the one you want to trust, "
        "then check whether that validated setup is active right now."
    )

    if not integrity_safe_strategies:
        st.info(
            "No strategy families are currently safe to analyze. Open Advanced → Strategy Integrity Audit "
            "to see which defining rules are missing from the backtester."
        )
    else:
        analyzer_cols = st.columns([1.2, 1.0, 2.0])
        analyzer_ticker = analyzer_cols[0].text_input(
            "Ticker to analyze",
            value=str(st.session_state.get("til_analyzer_ticker") or "SDOT"),
            max_chars=10,
        ).strip().upper()
        validated_only = analyzer_cols[1].checkbox(
            "Only fully validated",
            value=False,
            help=(
                "Off by default so stock-specific historical/promising candidates remain visible. "
                "Turn it on when you want only strategies that cleared every strict validation gate."
            ),
        )
        analyzer_cols[2].caption(
            "The analyzer ranks setup fit separately from historical robustness. "
            "A 100% rule match is not the same thing as a 100% chance of profit."
        )

        guided_strategy_id = str(st.session_state.get("til_guided_strategy_id") or "").strip()
        guided_finder_run_id = str(
            st.session_state.get("til_guided_finder_run_id") or ""
        ).strip()
        guided_strategy = next(
            (
                item
                for item in integrity_safe_strategies
                if str(item.get("id") or "") == guided_strategy_id
            ),
            None,
        )

        finder_run_summary = next(
            (
                item
                for item in library.get("stock_strategy_finder_runs") or []
                if isinstance(item, dict)
                and guided_finder_run_id
                and str(item.get("id") or "") == guided_finder_run_id
                and str(item.get("symbol") or "").strip().upper() == analyzer_ticker
            ),
            None,
        )
        if finder_run_summary is None and guided_strategy_id:
            # Compatibility for a handoff created before run IDs were carried.
            # Still stay strictly on this ticker.
            finder_run_summary = next(
                (
                    item
                    for item in library.get("stock_strategy_finder_runs") or []
                    if isinstance(item, dict)
                    and str(item.get("symbol") or "").strip().upper() == analyzer_ticker
                ),
                None,
            )

        tested_rankings = [
            dict(item)
            for item in (finder_run_summary or {}).get("tested_strategy_rankings") or []
            if isinstance(item, dict)
        ]
        ranking_note = ""

        # Older completed runs may predate compact ranked-candidate persistence.
        # Reconstruct only from the saved checkpoint for this exact ticker/profile.
        if finder_run_summary is not None and not tested_rankings:
            finder_profile_name = str((finder_run_summary or {}).get("profile") or "")
            matching_checkpoint = next(
                (
                    item
                    for item in library.get("stock_strategy_finder_checkpoints") or []
                    if isinstance(item, dict)
                    and str(item.get("symbol") or "").strip().upper() == analyzer_ticker
                    and (
                        not finder_profile_name
                        or str(item.get("profile") or "") == finder_profile_name
                    )
                ),
                None,
            )
            engine_state = dict((matching_checkpoint or {}).get("engine_state") or {})
            reconstructed: list[dict[str, Any]] = []
            for timeframe_name, timeframe_state in dict(
                engine_state.get("timeframes") or {}
            ).items():
                if not isinstance(timeframe_state, dict):
                    continue
                for raw_candidate in timeframe_state.get("rankings") or []:
                    if not isinstance(raw_candidate, dict):
                        continue
                    candidate = dict(raw_candidate)
                    candidate.setdefault("timeframe", timeframe_name)
                    reconstructed.append(candidate)
            for raw_candidate in engine_state.get("rankings") or []:
                if isinstance(raw_candidate, dict):
                    reconstructed.append(dict(raw_candidate))

            reconstructed.sort(
                key=lambda item: (
                    str(item.get("status") or "").upper() == "VALIDATED",
                    bool(item.get("adequate_sample")),
                    (
                        safe_float(
                            (item.get("validation_metrics") or {}).get("net_pnl"),
                            0.0,
                        )
                        or 0.0
                    )
                    > 0,
                    safe_float(item.get("score"), 0.0) or 0.0,
                ),
                reverse=True,
            )
            seen_source_ids: set[str] = set()
            for candidate in reconstructed:
                source_id = str(candidate.get("source_strategy_id") or "").strip()
                if not source_id or source_id in seen_source_ids:
                    continue
                seen_source_ids.add(source_id)
                tested_rankings.append(
                    {
                        "rank": len(tested_rankings) + 1,
                        "source_strategy_id": source_id,
                        "strategy_name": candidate.get("strategy_name"),
                        "timeframe": candidate.get("timeframe"),
                        "status": candidate.get("status"),
                        "score": candidate.get("score"),
                        "adequate_sample": bool(candidate.get("adequate_sample")),
                        "validation_metrics": candidate.get("validation_metrics") or {},
                        "stress_metrics": candidate.get("stress_metrics") or {},
                        "optimized_rules": candidate.get("optimized_rules") or {},
                        "optimized_backtest_settings": candidate.get("optimized_backtest_settings") or {},
                    }
                )
            if tested_rankings:
                ranking_note = (
                    "This older saved run was reconstructed from its own optimizer checkpoint. "
                    "The list is still restricted to strategies tested for this ticker."
                )

        source_strategy_by_id = {
            str(item.get("id") or ""): item
            for item in integrity_safe_strategies
            if str(item.get("id") or "").strip()
        }
        winner_source_strategy_id = str(
            (finder_run_summary or {}).get("winner_source_strategy_id") or ""
        ).strip()

        strategy_by_id: dict[str, dict[str, Any]] = {}
        ranking_by_id: dict[str, dict[str, Any]] = {}
        for candidate in tested_rankings:
            source_id = str(candidate.get("source_strategy_id") or "").strip()
            base_strategy = source_strategy_by_id.get(source_id)
            if not source_id or not isinstance(base_strategy, dict):
                continue

            is_step1_winner = source_id == winner_source_strategy_id
            if (
                is_step1_winner
                and isinstance(guided_strategy, dict)
                and str(guided_strategy.get("optimized_for_symbol") or "").strip().upper()
                == analyzer_ticker
            ):
                candidate_strategy = dict(guided_strategy)
            else:
                candidate_strategy = dict(base_strategy)
                candidate_strategy["id"] = (
                    "finder-tested-"
                    + hashlib.sha1(
                        (
                            str((finder_run_summary or {}).get("id") or "")
                            + "|"
                            + analyzer_ticker
                            + "|"
                            + source_id
                        ).encode("utf-8")
                    ).hexdigest()[:18]
                )
                candidate_strategy["name"] = str(
                    candidate.get("strategy_name")
                    or base_strategy.get("name")
                    or "Strategy"
                )
                candidate_strategy["source_type"] = "stock_specific_finder_tested"
                candidate_strategy["optimized_for_symbol"] = analyzer_ticker
                candidate_strategy["parent_strategy_id"] = source_id
                candidate_strategy["machine_rules"] = (
                    candidate.get("optimized_rules")
                    or base_strategy.get("machine_rules")
                    or {}
                )
                candidate_strategy["optimized_backtest_settings"] = (
                    candidate.get("optimized_backtest_settings") or {}
                )
                # Only the final Finder winner received the strict post-selection
                # holdout/walk-forward/stability verdict.
                candidate_strategy["validation_status"] = "research_only"
                candidate_strategy.pop("validated_rules", None)
                candidate_strategy.pop("validated_backtest_settings", None)
                candidate_strategy.pop("validated_at", None)

            candidate_strategy["_finder_timeframe"] = candidate.get("timeframe")
            candidate_strategy["_finder_history_days"] = int(
                safe_float(
                    ((finder_run_summary or {}).get("profile_details") or {}).get("history_days"),
                    0,
                )
                or 0
            )
            option_id = str(candidate_strategy.get("id") or "")
            if not option_id:
                continue
            strategy_by_id[option_id] = candidate_strategy
            ranking_by_id[option_id] = candidate

        strategy_option_ids = list(strategy_by_id)
        if validated_only:
            strategy_option_ids = [
                option_id
                for option_id in strategy_option_ids
                if str(
                    (strategy_by_id.get(option_id) or {}).get("validation_status")
                    or ""
                ).lower()
                == "validated"
            ]

        if finder_run_summary is None and guided_strategy is not None:
            # A very old handoff with no recoverable run data: show only the
            # actual Step-1 winner rather than contaminating this ticker with
            # unrelated library strategies.
            strategy_by_id = {str(guided_strategy.get("id") or ""): guided_strategy}
            ranking_by_id = {
                str(guided_strategy.get("id") or ""): {
                    "rank": 1,
                    "source_strategy_id": winner_source_strategy_id,
                    "strategy_name": guided_strategy.get("name"),
                    "validation_metrics": {},
                }
            }
            strategy_option_ids = list(strategy_by_id)
            ranking_note = (
                "This older result does not contain its tested-strategy ranking. "
                "Only the Step-1 winner is shown; rerun Step 1 once to populate ranked alternatives."
            )

        if finder_run_summary is not None and not strategy_option_ids and guided_strategy is not None:
            option_id = str(guided_strategy.get("id") or "")
            strategy_by_id = {option_id: guided_strategy}
            ranking_by_id = {
                option_id: {
                    "rank": 1,
                    "source_strategy_id": winner_source_strategy_id,
                    "strategy_name": guided_strategy.get("name"),
                    "validation_metrics": {},
                }
            }
            strategy_option_ids = [option_id]
            ranking_note = (
                ranking_note
                or "This older Step-1 run does not contain a recoverable ranked alternative list. "
                "Only its winner is shown; a new Step-1 run will save all tested candidates."
            )

        current_strategy_id = str(
            st.session_state.get("til_analyzer_strategy_id") or ""
        ).strip()
        winner_option_id = next(
            (
                option_id
                for option_id, candidate in ranking_by_id.items()
                if str(candidate.get("source_strategy_id") or "")
                == winner_source_strategy_id
            ),
            "",
        )
        preferred_strategy_id = (
            winner_option_id
            if winner_option_id in strategy_option_ids
            else current_strategy_id
            if current_strategy_id in strategy_option_ids
            else strategy_option_ids[0]
            if strategy_option_ids
            else ""
        )
        if (
            "til_analyzer_strategy_id" not in st.session_state
            or current_strategy_id not in strategy_option_ids
        ):
            st.session_state["til_analyzer_strategy_id"] = preferred_strategy_id

        if finder_run_summary is not None:
            st.info(
                f"Showing only strategies Step 1 tested for **{analyzer_ticker}**. "
                "They are ordered by the Step-1 Finder ranking; #1 is the strategy it selected."
            )
        elif guided_strategy is not None:
            st.info(
                "Continuing the Step-1 winner. This older saved result does not include "
                "its full tested-strategy ranking."
            )
        if ranking_note:
            st.caption(ranking_note)

        def analyzer_strategy_label(option_id: str) -> str:
            strategy = strategy_by_id.get(str(option_id)) or {}
            ranking = ranking_by_id.get(str(option_id)) or {}
            rank = int(safe_float(ranking.get("rank"), 0) or 0)
            metrics = ranking.get("validation_metrics") or {}
            validation_return = safe_float(metrics.get("return_pct"))
            trade_count = int(safe_float(metrics.get("trade_count"), 0) or 0)
            validation_text = (
                f"Validation {validation_return:+.1f}%"
                if validation_return is not None
                else "Validation return —"
            )
            sample_text = f"{trade_count} trades" if trade_count else "sample —"
            return (
                (f"#{rank} · " if rank else "")
                + str(strategy.get("name") or "Unnamed strategy")
                + f" · {validation_text} · {sample_text}"
            )

        if strategy_option_ids:
            selected_strategy_id = st.selectbox(
                "Strategy tested for this stock",
                strategy_option_ids,
                key="til_analyzer_strategy_id",
                format_func=analyzer_strategy_label,
                help=(
                    "Only strategies actually tested in this stock's Step-1 run appear here. "
                    "The rank is Step 1's historical ranking. Validation return is the strategy's "
                    "return in the validation portion of that historical test; it is not a future-profit probability."
                ),
            )
            selected_strategy = strategy_by_id.get(str(selected_strategy_id))
            analyzer_strategies = [selected_strategy] if selected_strategy is not None else []
            selected_ranking = ranking_by_id.get(str(selected_strategy_id)) or {}
            selected_validation = str(
                (selected_strategy or {}).get("validation_status") or "research_only"
            ).replace("_", " ").title()
            selected_meta_cols = st.columns([1.6, 1.0, 1.0])
            selected_meta_cols[0].caption(
                "Selected: " + analyzer_strategy_label(str(selected_strategy_id))
            )
            selected_meta_cols[1].caption(
                "Timeframe: " + str(selected_ranking.get("timeframe") or "—")
            )
            selected_meta_cols[2].caption("Strict status: " + selected_validation)
        else:
            selected_strategy_id = ""
            selected_strategy = None
            analyzer_strategies = []

        if validated_only and not analyzer_strategies:
            st.info(
                "None of the strategies tested for this stock are fully validated yet. "
                "Turn off Only fully validated to compare the research candidates from Step 1."
            )

        st.markdown("### What should I do next?")
        selected_is_validated = (
            str((selected_strategy or {}).get("validation_status") or "").lower()
            == "validated"
        )
        if selected_strategy is not None and not selected_is_validated:
            st.warning(
                "This strategy is still research-only. Its current setup can be previewed, but "
                "you should not use that preview as evidence that the strategy is profitable. "
                "The next real step is strict historical validation."
            )
            st.button(
                "③ Validate this strategy →",
                type="primary",
                width="stretch",
                key="til_validate_selected_analyzer_strategy",
                on_click=queue_strategy_validation_from_analyzer,
                args=(analyzer_ticker, dict(selected_strategy)),
            )
            st.caption(
                "Validation uses separate unseen data, untouched holdout data, higher-cost stress tests, "
                "parameter stability, and walk-forward evidence."
            )
        elif selected_strategy is not None:
            st.success(
                "This strategy has passed strict historical validation. Next, check whether its setup is active now."
            )

        analyzer_slot = st.empty()
        analyze_stock = analyzer_slot.button(
            "④ Check current setup" if selected_is_validated else "Preview current setup (optional)",
            type="primary",
            width="stretch",
            disabled=not analyzer_ticker or not analyzer_strategies,
            key="til_analyze_stock_strategies",
            on_click=prime_action_feedback,
            args=(f"Checking {analyzer_ticker or 'stock'} current signal…",),
        )
        if analyze_stock:
            analyzer_slot.button(
                "🧭 Analyzing…",
                type="primary",
                width="stretch",
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

                analyzer_market = market_client()
                analysis = analyze_stock_strategies(
                    analyzer_market,
                    analyzer_ticker,
                    analyzer_strategies,
                    progress=stock_analysis_progress,
                )
                analysis = apply_shadow_probability_scores(
                    [analysis],
                    shadow_probability_models(library),
                    champion_model_id=active_shadow_champion_id(library),
                )[0]
                analysis["_selected_strategy_id"] = str(selected_strategy_id or "")

                analyzer_as_of = utc_now()
                analyzer_news = [
                    classify_catalyst(item)
                    for item in (analysis.get("news_items") or [])
                    if isinstance(item, dict)
                ]
                analyzer_sec_filings: list[dict[str, Any]] = []
                analyzer_sec_error = ""
                analyzer_sec_user_agent = setting("SEC_USER_AGENT")
                if analyzer_sec_user_agent:
                    status_box.write("Checking SEC EDGAR for recent filing risk and catalysts…")
                    update_task_bar(
                        analyzer_bar,
                        analyzer_monitor,
                        0.86,
                        "Checking SEC filing evidence",
                    )
                    try:
                        analyzer_sec_payload = SecEdgarClient(
                            analyzer_sec_user_agent
                        ).recent_filings(
                            analyzer_ticker,
                            days=30,
                            limit=100,
                            as_of=analyzer_as_of,
                        )
                        analyzer_sec_filings = classify_recent_sec_filings(analyzer_sec_payload)
                    except AppError as exc:
                        analyzer_sec_error = str(exc)
                        status_box.write("SEC evidence unavailable: " + analyzer_sec_error)

                analyzer_evidence = rank_catalyst_evidence(
                    analyzer_news,
                    analyzer_sec_filings,
                    as_of=analyzer_as_of,
                )
                analysis["catalyst_evidence"] = analyzer_evidence
                analysis["catalyst_summary"] = catalyst_intelligence_summary(analyzer_evidence)
                analysis["sec_summary"] = sec_filing_summary(analyzer_sec_filings)
                analysis["sec_error"] = analyzer_sec_error
                analysis["sec_enabled"] = bool(analyzer_sec_user_agent)
                try:
                    st.session_state["til_live_learning_stock_analyzer_status"] = (
                        persist_live_learning_cycle(
                            analyzer_market,
                            [analysis],
                            source="stock_analyzer",
                            max_new=1,
                        )
                    )
                except AppError as exc:
                    st.session_state["til_live_learning_stock_analyzer_status"] = {
                        "error": str(exc),
                        "research_only": True,
                    }
                st.session_state["til_stock_analysis"] = analysis
                status_box.update(label=f"{analyzer_ticker} analysis complete", state="complete", expanded=False)
                complete_task_bar(analyzer_bar, analyzer_monitor, f"{analyzer_ticker} analysis complete")
                st.rerun()
            except AppError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Stock analysis failed: {exc}")

        stock_result = st.session_state.get("til_stock_analysis") or {}
        if (
            stock_result
            and stock_result.get("symbol") == analyzer_ticker
            and str(stock_result.get("_selected_strategy_id") or "")
            == str(selected_strategy_id or "")
        ):
            metrics = stock_result.get("metrics") or {}
            comparisons = list(stock_result.get("comparisons") or [])
            st.divider()
            st.markdown(f"### Step 4 — Current Setup · {analyzer_ticker}")
            analyzer_learning_status = (
                st.session_state.get("til_live_learning_stock_analyzer_status") or {}
            )
            if analyzer_learning_status.get("error"):
                st.caption(
                    "Live learning is research-only and did not affect this analysis. "
                    "Its durable save/outcome update was unavailable: "
                    + str(analyzer_learning_status.get("error"))
                )
            elif analyzer_learning_status.get("logged"):
                st.caption(
                    "🧠 Live learning (research only) · "
                    f"logged {int(analyzer_learning_status.get('logged') or 0)} observation · "
                    f"matured {int(analyzer_learning_status.get('matured') or 0)} prior outcomes · "
                    f"{int(analyzer_learning_status.get('total') or 0)} durable observations total. "
                    "This does not affect strategy ranking."
                )

            market_cols = st.columns(6)
            market_cols[0].metric("Price", f"${safe_float(metrics.get('price'), 0.0):,.4f}")
            market_cols[1].metric("Day move", f"{safe_float(metrics.get('day_change_pct'), 0.0):+.2f}%")
            rvol = safe_float(metrics.get("relative_volume"))
            market_cols[2].metric("RVOL", f"{rvol:.2f}×" if rvol is not None else "—")
            market_cols[3].metric("Spread", f"{safe_float(metrics.get('spread_pct'), 0.0):.2f}%")
            catalyst_summary = stock_result.get("catalyst_summary") or {}
            sec_summary = stock_result.get("sec_summary") or {}
            market_cols[4].metric(
                "Fresh catalysts",
                int(catalyst_summary.get("fresh_specific_catalysts") or 0),
            )
            analyzer_ml = stock_result.get("ml_prediction") or {}
            analyzer_probability = safe_float(analyzer_ml.get("probability"))
            market_cols[5].metric(
                "ML probability",
                "—" if analyzer_probability is None else f"{analyzer_probability * 100:.1f}%",
            )
            if analyzer_probability is not None:
                st.caption(
                    "Research-only ML probability · "
                    + str(analyzer_ml.get("target_description") or "configured ML target")
                    + " · does not affect strategy ranking or execution."
                )

            catalyst_evidence = list(stock_result.get("catalyst_evidence") or [])
            specific_evidence = [
                item for item in catalyst_evidence if item.get("is_specific_catalyst")
            ]
            dilution_evidence = [
                item for item in specific_evidence if item.get("is_dilution_risk")
            ]
            fresh_dilution = [
                item for item in dilution_evidence
                if str(item.get("freshness") or "") in {"breaking", "fresh", "recent"}
            ]

            if fresh_dilution:
                strongest_dilution = max(
                    fresh_dilution,
                    key=lambda item: abs(safe_float(item.get("effective_score"), 0.0) or 0.0),
                )
                st.warning(
                    "Fresh dilution / offering evidence detected: "
                    f"{strongest_dilution.get('category')} · "
                    f"{strongest_dilution.get('headline') or 'source evidence available'}"
                )

            if specific_evidence or stock_result.get("sec_error"):
                with st.expander("Catalyst + SEC evidence", expanded=bool(fresh_dilution)):
                    st.caption(
                        f"{len(specific_evidence)} specific catalyst items · "
                        f"{int(sec_summary.get('filings') or 0)} SEC filings reviewed · "
                        f"{len(dilution_evidence)} dilution-risk flags."
                    )
                    if stock_result.get("sec_error"):
                        st.caption("SEC evidence unavailable for this run: " + str(stock_result.get("sec_error")))
                    for item in specific_evidence[:8]:
                        icon = "⚠️" if item.get("is_dilution_risk") else (
                            "▲" if item.get("is_positive") else "▼" if item.get("is_negative") else "•"
                        )
                        source_type = "SEC" if item.get("evidence_type") == "sec_filing" else "News"
                        st.write(
                            f"{icon} **{source_type} · {item.get('category')}** — "
                            f"{item.get('headline') or 'Evidence'} "
                            f"({str(item.get('freshness') or 'unknown').title()})"
                        )

            if comparisons:
                best = comparisons[0]
                best_validation_raw = str(best.get("validation_status") or "unvalidated").strip().lower()
                best_validation = best_validation_raw.replace("_", " ").title()
                st.markdown(
                    f"**Best current strategy fit:** {best.get('strategy_name')} · "
                    f"{best.get('status')} · {safe_float(best.get('score'), 0.0):.0f}% rule match"
                )
                st.caption(
                    "Rule match tells you how many setup conditions are present right now. It is not a probability of profit."
                )

                st.markdown("### Why the current setup matched")
                inspect_options = {
                    f"{item.get('strategy_name')} · {item.get('status')} · {safe_float(item.get('score'), 0.0):.0f}%": item
                    for item in comparisons
                }
                chosen = inspect_options[st.selectbox("Strategy to inspect", list(inspect_options))]
                signal = chosen.get("signal") or {}
                checks = signal.get("checks") or []
                if checks:
                    for check in checks:
                        state = str(check.get("status") or "").upper()
                        icon = "✅" if state == "PASS" else "❓" if state == "UNKNOWN" else "❌"
                        st.write(
                            f"{icon} **{check.get('label') or 'Rule'}** — "
                            f"current: {check.get('actual')} · required: {check.get('required')}"
                        )
                else:
                    st.caption("No rule-by-rule checks were returned for this strategy match.")

                st.markdown("### Historical validation status")
                chosen_validation_raw = str(chosen.get("validation_status") or "unvalidated").strip().lower()
                chosen_validation = chosen_validation_raw.replace("_", " ").title()
                robustness_score = safe_float(chosen.get("robustness_score"))
                validation_cols = st.columns(2)
                validation_cols[0].metric("Validation status", chosen_validation)
                validation_cols[1].metric(
                    "Historical robustness",
                    "—" if robustness_score is None else f"{robustness_score:.1f}/100",
                )
                if chosen_validation_raw == "validated":
                    st.success("This strategy cleared the app's strict historical validation gate.")
                else:
                    st.warning(
                        "This is still a research candidate. It may match the stock right now, but it has not cleared every strict validation gate."
                    )

                st.markdown("### Evidence summary")
                confidence_cols = st.columns(2)
                confidence_cols[0].metric(
                    "Evidence confidence",
                    "Validated" if chosen_validation_raw == "validated" else "Research-only",
                )
                confidence_cols[1].metric(
                    "Current rule match",
                    f"{safe_float(chosen.get('score'), 0.0):.0f}%",
                )
                st.caption(
                    "This combines historical evidence and today's rule fit. It is not a guaranteed win rate or probability of profit."
                )

                chosen_status = str(chosen.get("status") or "").upper()
                if chosen_validation_raw == "validated" and chosen_status == "MATCH":
                    st.success(
                        "This strategy passed historical validation and its setup is active now. "
                        "The next step is paper testing—not real-money trading."
                    )
                    st.button(
                        "⑤ Open paper testing →",
                        type="primary",
                        width="stretch",
                        key="til_open_paper_from_analyzer",
                        on_click=queue_paper_test_from_analyzer,
                        args=(analyzer_ticker, str(selected_strategy_id or "")),
                    )
                elif chosen_validation_raw == "validated":
                    st.info(
                        "The strategy is historically validated, but its full setup is not active right now. "
                        "Wait for the setup or compare another validated candidate."
                    )
                else:
                    st.warning(
                        "Do not move to paper/live trading yet. Validate this candidate first using the button above."
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
                with st.expander("Other strategy matches (optional)", expanded=False):
                    st.dataframe(pd.DataFrame(comparison_rows), width="stretch", hide_index=True)


elif module == "Live / Paper":
    st.caption(
        "Research and validation remain separate from execution. Existing safety checks and Alpaca "
        "paper-trading controls stay in place."
    )
    live_runner_slot = st.empty()
    open_live_runner = live_runner_slot.button(
        "Open existing Live Strategy Runner",
        width="stretch",
        key="til_open_live_strategy_runner",
    )
    if open_live_runner:
        live_runner_slot.button(
            "Loading Live Strategy Runner…",
            width="stretch",
            disabled=True,
            key="til_open_live_strategy_runner_busy",
        )
        st.switch_page("pages/Live_Strategy_Runner.py")