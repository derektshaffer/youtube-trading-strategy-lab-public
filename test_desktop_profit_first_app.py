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
        "hybrid_runtime/desktop_settings.py",
        "hybrid_runtime/library_source.py",
        "hybrid_runtime/market_cache.py",
        "hybrid_runtime/cloud_bridge.py",
        "hybrid_runtime/cloud_link_store.py",
        "hybrid_runtime/server.py",
        "scripts/build_trading_intelligence_desktop.py",
    ]
    for path in paths:
        ast.parse(read(path), filename=path)


def test_production_shell_exposes_profit_first_jobs_and_secure_connection():
    ui = (
        read("desktop/trading_intelligence/pages.py")
        + read("desktop/trading_intelligence/window.py")
        + read("desktop/trading_intelligence/enhanced_window.py")
        + read("desktop/trading_intelligence/analysis_page.py")
    )
    settings = read("hybrid_runtime/desktop_settings.py")
    source = read("hybrid_runtime/library_source.py")

    assert "Profit First" in ui
    assert "Durable Jobs" in ui
    assert "Connection Settings" in ui
    assert "Quick Analysis" in ui
    assert "strategy.profit_first_plan" in ui
    assert "strategy.profit_first_validation" in ui
    assert "Run strict cloud validation" in ui
    assert "Attach to active validation" in ui
    assert "continue_after_app_exit" in ui
    assert "remote_dedupe_key" in ui
    assert "analysis.stock" in ui
    assert "MacOSKeychain().set_secret" in ui
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


def test_production_sidecar_activates_cloud_bridge_and_link_lookup():
    server = read("hybrid_runtime/server.py")
    bridge = read("hybrid_runtime/cloud_bridge.py")
    api = read("hybrid_runtime/api.py")

    assert "CloudBridgeWorker(" in server
    assert "CloudLinkStore(" in server
    assert 'name="trading-intelligence-cloud-bridge"' in server
    assert "cloud_link_lookup=cloud_links.get" in server
    assert 'SUPPORTED_CLOUD_JOB_TYPES = frozenset({"strategy.profit_first_validation"})' in bridge
    assert "/v1/jobs/{job_id}/cloud-link" in api


def test_production_build_keeps_trading_engines_in_sidecar():
    build = read("scripts/build_trading_intelligence_desktop.py")
    runtime = read("desktop/trading_intelligence/runtime.py")
    market_cache = read("hybrid_runtime/market_cache.py")

    assert 'APP_NAME = "Trading Intelligence"' in build
    assert '"--paths"' in build
    assert "build_desktop_sidecar.py" in build
    assert '"--add-binary"' in build
    assert '"profit_first_queue"' not in build
    assert '"youtube_strategy_engine"' not in build
    assert "from youtube_strategy_engine import AlpacaMarketData" in market_cache
    app = read("desktop/trading_intelligence/app.py")
    assert "from desktop.trading_intelligence.runtime" in app
    assert "TRADING_INTELLIGENCE_LOCAL_TOKEN" in runtime
    assert "127.0.0.1" in runtime


def test_market_cache_is_persistent_incremental_and_explicit_about_price_age():
    cache = read("hybrid_runtime/market_cache.py")
    analysis = read("desktop/trading_intelligence/analysis_page.py")
    ui = read("desktop/trading_intelligence/ui.py")

    assert "CACHE_DIRECTORY = \"market-cache-v1\"" in cache
    assert "overlap = timedelta" in cache
    assert "network_request" in cache
    assert "data_age_seconds" in cache
    assert "Latest completed Alpaca candle close" in cache
    assert "macOS Keychain" in analysis or "persistent local cache" in analysis
    assert "from .enhanced_window import MainWindow" in ui


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
