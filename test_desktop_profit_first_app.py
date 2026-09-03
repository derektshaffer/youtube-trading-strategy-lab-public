from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_production_desktop_python_sources_parse():
    paths = [
        "desktop/__init__.py",
        "desktop/trading_intelligence/app.py",
        "desktop/trading_intelligence/runtime.py",
        "desktop/trading_intelligence/ui.py",
        "desktop/trading_intelligence/pages.py",
        "desktop/trading_intelligence/theme.py",
        "desktop/trading_intelligence/window.py",
        "desktop/trading_intelligence/enhanced_window.py",
        "desktop/trading_intelligence/analysis_page.py",
        "desktop/trading_intelligence/chart.py",
        "desktop/trading_intelligence/finder_page.py",
        "desktop/trading_intelligence/finder_window.py",
        "desktop/trading_intelligence/results_page.py",
        "desktop/trading_intelligence/results_window.py",
        "desktop/trading_intelligence/strategy_lab_page.py",
        "desktop/trading_intelligence/strategy_lab_window.py",
        "desktop/trading_intelligence/research_ml_page.py",
        "desktop/trading_intelligence/research_ml_window.py",
        "desktop/trading_intelligence/system_health_page.py",
        "desktop/trading_intelligence/system_health_window.py",
        "desktop/trading_intelligence/onboarding_page.py",
        "desktop/trading_intelligence/onboarding_window.py",
        "desktop/trading_intelligence/market_discovery_page.py",
        "desktop/trading_intelligence/scanner_launcher_page.py",
        "desktop/trading_intelligence/parity_window.py",
        "hybrid_runtime/market_discovery_job.py",
        "hybrid_runtime/scanner_launcher.py",
        "hybrid_runtime/desktop_settings.py",
        "hybrid_runtime/library_source.py",
        "hybrid_runtime/market_cache.py",
        "hybrid_runtime/cloud_bridge.py",
        "hybrid_runtime/cloud_link_store.py",
        "hybrid_runtime/stock_finder_bridge.py",
        "hybrid_runtime/results_summary.py",
        "hybrid_runtime/strategy_lab_bridge.py",
        "hybrid_runtime/strategy_lab_options.py",
        "hybrid_runtime/research_ml_summary.py",
        "hybrid_runtime/system_health_summary.py",
        "hybrid_runtime/onboarding.py",
        "hybrid_runtime/server.py",
        "scripts/build_trading_intelligence_desktop.py",
    ]
    for path in paths:
        ast.parse(read(path), filename=path)


def test_production_shell_exposes_setup_profit_first_finder_results_research_ml_health_jobs_and_secure_connection():
    ui = (
        read("desktop/trading_intelligence/pages.py")
        + read("desktop/trading_intelligence/window.py")
        + read("desktop/trading_intelligence/enhanced_window.py")
        + read("desktop/trading_intelligence/analysis_page.py")
        + read("desktop/trading_intelligence/finder_page.py")
        + read("desktop/trading_intelligence/finder_window.py")
        + read("desktop/trading_intelligence/results_page.py")
        + read("desktop/trading_intelligence/results_window.py")
        + read("desktop/trading_intelligence/strategy_lab_page.py")
        + read("desktop/trading_intelligence/strategy_lab_window.py")
        + read("desktop/trading_intelligence/research_ml_page.py")
        + read("desktop/trading_intelligence/research_ml_window.py")
        + read("desktop/trading_intelligence/system_health_page.py")
        + read("desktop/trading_intelligence/system_health_window.py")
        + read("desktop/trading_intelligence/onboarding_page.py")
        + read("desktop/trading_intelligence/onboarding_window.py")
        + read("desktop/trading_intelligence/market_discovery_page.py")
        + read("desktop/trading_intelligence/scanner_launcher_page.py")
        + read("desktop/trading_intelligence/parity_window.py")
    )
    settings = read("hybrid_runtime/desktop_settings.py")
    source = read("hybrid_runtime/library_source.py")

    assert "FIRST-RUN SETUP" in ui
    assert "Save securely + verify" in ui
    assert "Start Trading Intelligence" in ui
    assert "system.onboarding_probe" in ui
    assert "Profit First" in ui
    assert "Durable Jobs" in ui
    assert "Connection Settings" in ui
    assert "Quick Analysis" in ui
    assert "Stock Strategy Finder" in ui
    assert "Run Finder in Cloud" in ui
    assert "Find Stocks" in ui
    assert "Find stocks worth watching" in ui
    assert "market.discovery" in ui
    assert "Open Momentum Scanner" in ui
    assert "Results" in ui
    assert "Refresh Results" in ui
    assert "library.results_summary" in ui
    assert "Strategy Lab" in ui
    assert "Run Strategy Lab in Cloud" in ui
    assert "Research + ML" in ui
    assert "Refresh Research + ML" in ui
    assert "library.research_ml_summary" in ui
    assert "System Health" in ui
    assert "Refresh Health" in ui
    assert "runtime_health=runtime_health" in ui
    assert "strategy.profit_first_plan" in ui
    assert "strategy.profit_first_validation" in ui
    assert "strategy.stock_finder" in ui
    assert "strategy.strategy_lab" in ui
    assert "Run strict cloud validation" in ui
    assert "Attach to active validation" in ui
    assert "continue_after_app_exit" in ui
    assert "remote_dedupe_key" in ui
    assert "distributed_shards_completed" in ui
    assert "analysis.stock" in ui
    assert "finder_job_id" in ui
    assert "profit_validation_job_id" in ui
    assert "strategy_lab_job_id" in ui
    assert "MacOSKeychain().set_secret" in ui
    assert "ALPACA_API_KEY_ACCOUNT" in ui
    assert "ALPACA_SECRET_KEY_ACCOUNT" in ui
    assert "ALPACA_API_KEY_ACCOUNT" in settings
    assert "ALPACA_SECRET_KEY_ACCOUNT" in settings
    assert "GITHUB_BACKUP_TOKEN_ACCOUNT" in settings
    assert "token" not in "".join(
        line.strip()
        for line in settings.splitlines()
        if "as_dict" in line
    ).lower()
    assert "GitHubCloudBackup" in source
    assert "StrategyStore" in source
    assert "redact_text(exc, (token,))" in source


def test_production_sidecar_activates_cloud_bridge_finder_and_link_lookup():
    server = read("hybrid_runtime/server.py")
    bridge = read("hybrid_runtime/cloud_bridge.py")
    finder_bridge = read("hybrid_runtime/stock_finder_bridge.py")
    strategy_lab_bridge = read("hybrid_runtime/strategy_lab_bridge.py")
    api = read("hybrid_runtime/api.py")

    assert "CloudBridgeWorker(" in server
    assert "CloudLinkStore(" in server
    assert 'name="trading-intelligence-cloud-bridge"' in server
    assert "cloud_link_lookup=cloud_links.get" in server
    assert '"strategy.profit_first_validation"' in bridge
    assert '"strategy.stock_finder"' in bridge
    assert '"strategy.strategy_lab"' in bridge
    assert 'DISTRIBUTED_STOCK_FINDER_WORKFLOW = "distributed-stock-finder.yml"' in finder_bridge
    assert 'REMOTE_STOCK_FINDER_TYPE = "stock_finder"' in finder_bridge
    assert "finder_report_for_remote" in finder_bridge
    assert 'CLOUD_STRATEGY_LAB_WORKFLOW = "cloud-strategy-lab.yml"' in strategy_lab_bridge
    assert 'REMOTE_STRATEGY_LAB_TYPE = "strategy_lab"' in strategy_lab_bridge
    assert "/v1/jobs/{job_id}/cloud-link" in api


def test_production_build_keeps_trading_engines_in_sidecar():
    build = read("scripts/build_trading_intelligence_desktop.py")
    runtime = read("desktop/trading_intelligence/runtime.py")
    market_cache = read("hybrid_runtime/market_cache.py")
    adapter = read("hybrid_runtime/engine_adapter.py")
    router = read("hybrid_runtime/router.py")

    assert 'APP_NAME = "Trading Intelligence"' in build
    assert '"--paths"' in build
    assert "build_desktop_sidecar.py" in build
    assert '"--add-binary"' in build
    assert '"profit_first_queue"' not in build
    assert '"youtube_strategy_engine"' not in build
    assert "from youtube_strategy_engine import AlpacaMarketData" in market_cache
    assert '"system.onboarding_probe": onboarding_probe_handler' in adapter
    assert '"system.onboarding_probe"' in router
    assert '"library.results_summary": results_summary_handler' in adapter
    assert '"library.strategy_lab_options": strategy_lab_options_handler' in adapter
    assert '"library.research_ml_summary": research_ml_summary_handler' in adapter
    app = read("desktop/trading_intelligence/app.py")
    assert "from desktop.trading_intelligence.runtime" in app
    assert "TRADING_INTELLIGENCE_LOCAL_TOKEN" in runtime
    assert "127.0.0.1" in runtime


def test_market_cache_is_persistent_incremental_and_explicit_about_price_age():
    cache = read("hybrid_runtime/market_cache.py")
    analysis = read("desktop/trading_intelligence/analysis_page.py")
    ui = read("desktop/trading_intelligence/ui.py")
    onboarding_window = read("desktop/trading_intelligence/onboarding_window.py")
    health_window = read("desktop/trading_intelligence/system_health_window.py")

    assert "CACHE_DIRECTORY = \"market-cache-v1\"" in cache
    assert "overlap = timedelta" in cache
    assert "network_request" in cache
    assert "data_age_seconds" in cache
    assert "Latest completed Alpaca candle close" in cache
    assert "macOS Keychain" in analysis or "persistent local cache" in analysis
    assert "from .parity_window import MainWindow" in ui
    parity = read("desktop/trading_intelligence/parity_window.py")
    assert "from .beta_recovery_window import MainWindow as RecoveryMainWindow" in parity
    assert "from .system_health_window import MainWindow as SystemHealthMainWindow" in onboarding_window
    assert "from .research_ml_window import MainWindow as ResearchMLMainWindow" in health_window


def test_finder_cloud_jobs_are_background_and_reconnect_safe():
    window = read("desktop/trading_intelligence/finder_window.py")

    assert "finder_job_id" in window
    assert "profit_validation_job_id" in window
    assert "_restore_background_cloud_jobs" in window
    assert "_submit_background_cloud_job" in window
    assert "_poll_stock_finder" in window
    assert "_poll_background_profit_validation" in window
    assert "You can continue using Quick Analysis while it runs" in window
    assert "Cloud cancellation is available before the remote worker starts" in window


def test_results_are_bounded_read_only_and_keep_cloud_jobs_reconciling():
    page = read("desktop/trading_intelligence/results_page.py")
    window = read("desktop/trading_intelligence/results_window.py")
    summary = read("hybrid_runtime/results_summary.py")
    router = read("hybrid_runtime/router.py")

    assert "full evidence remains in durable storage" in page
    assert "Only explicit validated status" in page
    assert "library.results_summary" in window
    assert "_poll_stock_finder" in window
    assert "_poll_background_profit_validation" in window
    assert '"bounded": True' in summary
    assert '"validated_strategies"' in summary
    assert '"library.results_summary"' in router


def test_research_ml_is_bounded_read_only_and_preserves_cloud_reconciliation():
    page = read("desktop/trading_intelligence/research_ml_page.py")
    window = read("desktop/trading_intelligence/research_ml_window.py")
    summary = read("hybrid_runtime/research_ml_summary.py")
    router = read("hybrid_runtime/router.py")

    assert "never launches compute or changes trading decisions" in page
    assert "shadow models and do not place trades" in page
    assert "library.research_ml_summary" in window
    assert "_poll_strategy_lab" in window
    assert "_poll_stock_finder" in window
    assert "_poll_background_profit_validation" in window
    assert '"bounded": True' in summary
    assert '"affects_live_ranking": False' in summary
    assert '"affects_execution": False' in summary
    assert '"library.research_ml_summary"' in router


def test_system_health_is_read_only_source_aware_and_live():
    page = read("desktop/trading_intelligence/system_health_page.py")
    window = read("desktop/trading_intelligence/system_health_window.py")
    summary = read("hybrid_runtime/system_health_summary.py")

    assert "read-only diagnostic" in page
    assert 'request_json("GET", "/health")' in window
    assert "build_system_health_summary" in window
    assert '"runtime_service"' in summary
    assert '"library_connection"' in summary
    assert "using_local_library" in summary
    assert "github_required_for_library" in summary
    assert '"affects_execution": False' in summary


def test_onboarding_is_first_run_only_in_normal_gui_and_never_persists_secrets_to_jobs():
    page = read("desktop/trading_intelligence/onboarding_page.py")
    window = read("desktop/trading_intelligence/onboarding_window.py")
    probe = read("hybrid_runtime/onboarding.py")

    assert "if self.smoke:" in window
    assert "super().wait_for_health()" in window
    assert "configuration_status" in window
    assert "MacOSKeychain" in window
    assert "save_desktop_settings" in window
    assert '"job_type": "system.onboarding_probe"' in window
    assert '"payload": {}' in window
    assert "_github_token" in page
    assert "_alpaca_api_key" in page
    assert "_alpaca_secret_key" in page
    assert "save_and_verify_requested.emit(self.settings_payload())" in page
    assert '"research_only": True' in probe
    assert '"affects_execution": False' in probe
    assert "Authorization" in probe
    assert "provider.bars(" in probe


def test_apple_silicon_workflow_launches_full_profit_first_gui():
    workflow = read(".github/workflows/desktop-production.yml")

    assert "runs-on: macos-14" in workflow
    assert 'test "$(uname -m)" = "arm64"' in workflow
    assert "codesign --verify --deep --strict" in workflow
    assert "build_trading_intelligence_desktop.py" in workflow
    assert "--smoke" in workflow
    assert "--library-fixture" in workflow
    assert 'metrics["queue_status"] == "no-eligible-candidates"' in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "ALPACA_API_KEY" not in workflow
    assert "GEMINI_API_KEY" not in workflow
    assert "GITHUB_BACKUP_TOKEN:" not in workflow
