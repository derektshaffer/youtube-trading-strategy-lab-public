from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_desktop_build_workflow_uses_native_arm_runner_and_both_candidates():
    workflow = (ROOT / ".github" / "workflows" / "desktop-framework-spikes.yml").read_text(
        encoding="utf-8"
    )
    assert workflow.count("runs-on: macos-14") == 2
    assert "tauri-apple-silicon:" in workflow
    assert "pyside-apple-silicon:" in workflow
    assert 'test "$(uname -m)" = "arm64"' in workflow
    assert "codesign --verify --deep --strict" in workflow
    assert "smoke_tauri_bundle.py" in workflow
    assert "--headless-smoke" in workflow
    assert "actions/upload-artifact@v4" in workflow


def test_desktop_tokens_are_environment_only_not_command_line_arguments():
    server = (ROOT / "hybrid_runtime" / "server.py").read_text(encoding="utf-8")
    tauri = (ROOT / "desktop" / "tauri_spike" / "src" / "main.js").read_text(
        encoding="utf-8"
    )
    pyside = (ROOT / "desktop" / "pyside6_spike" / "app.py").read_text(
        encoding="utf-8"
    )
    assert 'add_argument("--token"' not in server
    assert "TRADING_INTELLIGENCE_LOCAL_TOKEN" in server
    assert "TRADING_INTELLIGENCE_LOCAL_TOKEN" in tauri
    assert "TRADING_INTELLIGENCE_LOCAL_TOKEN" in pyside
    assert '["--token"' not in tauri
    assert '"--token",' not in pyside


def test_tauri_bundle_is_ad_hoc_signed_loopback_scoped_and_vanilla_safe():
    config = json.loads(
        (ROOT / "desktop" / "tauri_spike" / "src-tauri" / "tauri.conf.json").read_text(
            encoding="utf-8"
        )
    )
    capability = json.loads(
        (
            ROOT
            / "desktop"
            / "tauri_spike"
            / "src-tauri"
            / "capabilities"
            / "default.json"
        ).read_text(encoding="utf-8")
    )
    javascript = (ROOT / "desktop" / "tauri_spike" / "src" / "main.js").read_text(
        encoding="utf-8"
    )
    assert config["bundle"]["macOS"]["signingIdentity"] == "-"
    assert config["bundle"]["macOS"]["minimumSystemVersion"] == "12.0"
    assert config["app"]["withGlobalTauri"] is True
    assert "http://127.0.0.1:*" in config["app"]["security"]["csp"]
    assert "shell:allow-kill" in capability["permissions"]
    scoped_permissions = [
        item for item in capability["permissions"] if isinstance(item, dict)
    ]
    assert len(scoped_permissions) == 1
    allowed = scoped_permissions[0]["allow"][0]
    assert allowed["sidecar"] is True
    assert allowed["args"][:3] == ["--host", "127.0.0.1", "--port"]
    assert "window.__TAURI__.path" in javascript
    assert "window.__TAURI__.shell" in javascript
    assert 'from "@tauri-apps/' not in javascript


def test_desktop_build_and_smoke_scripts_parse():
    paths = [
        ROOT / "scripts" / "build_desktop_sidecar.py",
        ROOT / "scripts" / "build_pyside_spike.py",
        ROOT / "scripts" / "desktop_sidecar_smoke.py",
        ROOT / "scripts" / "smoke_tauri_bundle.py",
        ROOT / "desktop" / "pyside6_spike" / "app.py",
    ]
    for path in paths:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
