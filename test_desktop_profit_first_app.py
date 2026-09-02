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
        "hybrid_runtime/desktop_settings.py",
        "hybrid_runtime/library_source.py",
        "scripts/build_trading_intelligence_desktop.py",
    ]
    for path in paths:
        ast.parse(read(path), filename=path)


def test_production_shell_exposes_profit_first_jobs_and_secure_connection():
    ui = read("desktop/trading_intelligence/pages.py") + read("desktop/trading_intelligence/window.py")
    settings = read("hybrid_runtime/desktop_settings.py")
    source = read("hybrid_runtime/library_source.py")

    assert "Profit First" in ui
    assert "Durable Jobs" in ui
    assert "Connection Settings" in ui
    assert "strategy.profit_first_plan" in ui
    assert "Cloud validation bridge pending" in ui
    assert "MacOSKeychain().set_secret" in ui
    assert "GITHUB_BACKUP_TOKEN_ACCOUNT" in settings
    assert "token" not in "".join(
        line.strip()
        for line in settings.splitlines()
        if "as_dict" in line
    ).lower()
    assert "GitHubCloudBackup" in source
    assert "StrategyStore" in source
    assert "redact_text(exc, (token,))" in source


def test_production_build_keeps_trading_engines_in_sidecar():
    build = read("scripts/build_trading_intelligence_desktop.py")
    runtime = read("desktop/trading_intelligence/runtime.py")

    assert 'APP_NAME = "Trading Intelligence"' in build
    assert '"--paths"' in build
    assert "build_desktop_sidecar.py" in build
    assert '"--add-binary"' in build
    assert '"profit_first_queue"' not in build
    assert '"youtube_strategy_engine"' not in build
    app = read("desktop/trading_intelligence/app.py")
    assert "from desktop.trading_intelligence.runtime" in app
    assert "TRADING_INTELLIGENCE_LOCAL_TOKEN" in runtime
    assert "127.0.0.1" in runtime


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
