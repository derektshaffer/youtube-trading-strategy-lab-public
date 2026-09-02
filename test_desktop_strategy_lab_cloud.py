from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_strategy_lab_cloud_sources_parse():
    for path in (
        "cloud_strategy_lab_worker.py",
        "hybrid_runtime/strategy_lab_bridge.py",
        "hybrid_runtime/strategy_lab_options.py",
        "desktop/trading_intelligence/strategy_lab_page.py",
        "desktop/trading_intelligence/strategy_lab_window.py",
        "desktop/trading_intelligence/beta_recovery_window.py",
    ):
        ast.parse(read(path), filename=path)


def test_production_ui_retains_strategy_lab_cloud_chain_under_later_wrappers():
    ui = read("desktop/trading_intelligence/ui.py")
    recovery_window = read("desktop/trading_intelligence/beta_recovery_window.py")
    onboarding_window = read("desktop/trading_intelligence/onboarding_window.py")
    health_window = read("desktop/trading_intelligence/system_health_window.py")
    research_window = read("desktop/trading_intelligence/research_ml_window.py")
    page = read("desktop/trading_intelligence/strategy_lab_page.py")
    window = read("desktop/trading_intelligence/strategy_lab_window.py")
    router = read("hybrid_runtime/router.py")
    adapter = read("hybrid_runtime/engine_adapter.py")

    assert "from .beta_recovery_window import MainWindow" in ui
    assert "from .onboarding_window import MainWindow as OnboardingMainWindow" in recovery_window
    assert "class MainWindow(OnboardingMainWindow):" in recovery_window
    assert "from .system_health_window import MainWindow as SystemHealthMainWindow" in onboarding_window
    assert "class MainWindow(SystemHealthMainWindow):" in onboarding_window
    assert "from .research_ml_window import MainWindow as ResearchMLMainWindow" in health_window
    assert "class MainWindow(ResearchMLMainWindow):" in health_window
    assert "from .strategy_lab_window import MainWindow as StrategyLabMainWindow" in research_window
    assert "class MainWindow(StrategyLabMainWindow):" in research_window
    assert "Run Strategy Lab in Cloud" in page
    assert "can continue after this app or your Mac closes" in page
    assert '"strategy.strategy_lab"' in window
    assert '"library.strategy_lab_options"' in window
    assert "continue_after_app_exit" in window
    assert "strategy_lab_job_id" in window
    assert "_restore_background_cloud_jobs" in window
    assert 'self._require_capabilities(("library", "cloud"), "Strategy Lab")' in recovery_window
    assert '"strategy.strategy_lab"' in router
    assert '"library.strategy_lab_options"' in router
    assert '"library.strategy_lab_options": strategy_lab_options_handler' in adapter


def test_strategy_lab_cloud_worker_reuses_existing_executor_and_fidelity_gate():
    worker = read("cloud_strategy_lab_worker.py")
    jobs = read("strategy_lab_jobs.py")
    core = read("trading_intelligence_core.py")

    assert "execute_strategy_lab_job_once" in worker
    assert "strategy_integrity_report" in worker
    assert 'status == "faithful"' in worker
    assert "effective_strategy_for_research" in worker
    assert "def execute_strategy_lab_job_once(" in jobs
    assert "return _run_job(" in jobs
    assert "def strategy_integrity_report(" in core


def test_strategy_lab_queue_is_dedicated_and_continuous_worker_will_not_claim_it():
    orchestrator = read("trading_research_orchestrator.py")
    continuous = read("cloud_research_worker.py")
    bridge = read("hybrid_runtime/cloud_bridge.py")

    assert '"strategy_lab"' in orchestrator.split("SUPPORTED_RESEARCH_JOB_TYPES", 1)[1].split(")", 1)[0]
    assert 'SUPPORTED_RESEARCH_JOB_TYPES - {"stock_finder", "strategy_lab"}' in continuous
    assert '"strategy.strategy_lab"' in bridge.split("SUPPORTED_CLOUD_JOB_TYPES", 1)[1].split("DEFAULT_LIBRARY_PATH", 1)[0]
    assert "strategy_lab_checkpoint_config" in bridge
    assert "overlay_strategy_lab_checkpoint" in bridge
    assert "CLOUD_STRATEGY_LAB_WORKFLOW" in bridge


def test_cloud_strategy_lab_workflow_has_durable_recovery_without_long_global_lock():
    workflow = read(".github/workflows/cloud-strategy-lab.yml")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "timeout-minutes: 330" in workflow
    assert "group: strategy-lab-cloud-worker" in workflow
    assert "group: trading-intelligence-library-writer" not in workflow
    assert "cloud_strategy_lab_worker.py" in workflow
    assert "TRADING_LAB_BACKUP_TOKEN" in workflow
    assert "ALPACA_API_KEY" in workflow
    assert "ALPACA_SECRET_KEY" in workflow


def test_results_reads_small_strategy_lab_checkpoint_without_persisting_secrets():
    source = read("hybrid_runtime/library_source.py")
    adapter = read("hybrid_runtime/engine_adapter.py")
    bridge = read("hybrid_runtime/strategy_lab_bridge.py")

    assert "def load_strategy_lab_checkpoint_library(" in source
    assert "load_strategy_lab_checkpoint_library" in adapter
    assert 'STRATEGY_LAB_CHECKPOINT_PATH = "trading-intelligence-lab/strategy_lab_latest.json"' in bridge
    assert "strategy-lab-checkpoint-cache" in source
    assert "MacOSKeychain" in source
