from __future__ import annotations

import ast
import json
from pathlib import Path

from hybrid_sidecar_entry import configure_tls_certificate_bundle


ROOT = Path(__file__).resolve().parent


def test_frozen_sidecar_uses_certifi_bundle_without_overriding_operator_choice(tmp_path):
    bundle = tmp_path / "cacert.pem"
    bundle.write_text("test certificate bundle", encoding="utf-8")
    environment: dict[str, str] = {}

    selected = configure_tls_certificate_bundle(
        environ=environment,
        certificate_bundle=bundle,
    )

    assert selected == str(bundle)
    assert environment["SSL_CERT_FILE"] == str(bundle)

    environment["SSL_CERT_FILE"] = "/operator/managed/ca.pem"
    preserved = configure_tls_certificate_bundle(
        environ=environment,
        certificate_bundle=bundle,
    )
    assert preserved == "/operator/managed/ca.pem"
    assert environment["SSL_CERT_FILE"] == "/operator/managed/ca.pem"


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
    assert "--gui-smoke" in workflow
    assert '"full_gui": True' in (
        ROOT / "desktop" / "pyside6_spike" / "pyside_gui.py"
    ).read_text(encoding="utf-8")
    assert "actions/upload-artifact@v4" in workflow


def test_desktop_sidecar_build_collects_the_tls_certificate_bundle():
    build = (ROOT / "scripts" / "build_desktop_sidecar.py").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements-desktop.txt").read_text(encoding="utf-8")

    assert '"--collect-data"' in build
    assert '"certifi"' in build
    assert "certifi>=" in requirements


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


def test_tauri_icon_is_present_and_valid_png():
    icon = ROOT / "desktop" / "tauri_spike" / "src-tauri" / "icons" / "icon.png"
    data = icon.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(data) > 256


def test_both_frameworks_render_interactive_candles_from_the_same_local_job():
    html = (ROOT / "desktop" / "tauri_spike" / "src" / "index.html").read_text(
        encoding="utf-8"
    )
    javascript = (ROOT / "desktop" / "tauri_spike" / "src" / "main.js").read_text(
        encoding="utf-8"
    )
    pyside_app = (ROOT / "desktop" / "pyside6_spike" / "app.py").read_text(
        encoding="utf-8"
    )
    pyside_chart = (
        ROOT / "desktop" / "pyside6_spike" / "pyside_chart.py"
    ).read_text(encoding="utf-8")
    pyside_gui = (
        ROOT / "desktop" / "pyside6_spike" / "pyside_gui.py"
    ).read_text(encoding="utf-8")
    adapter = (ROOT / "hybrid_runtime" / "engine_adapter.py").read_text(
        encoding="utf-8"
    )
    router = (ROOT / "hybrid_runtime" / "router.py").read_text(encoding="utf-8")
    smoke = (ROOT / "scripts" / "smoke_tauri_bundle.py").read_text(encoding="utf-8")

    assert '<canvas id="chart"' in html
    assert "Scroll to zoom" in html
    assert "chart.framework_fixture" in javascript
    assert "tauri-ui-ready-" in javascript
    assert "class CandleChart" in pyside_chart
    assert "--gui-smoke" in pyside_app
    assert "pyside-ui" in pyside_gui
    assert "chart_framework_fixture_handler" in adapter
    assert '"chart.framework_fixture": chart_framework_fixture_handler' in adapter
    assert '"chart.framework_fixture"' in router
    assert "wait_for_tauri_ui" in smoke


def test_desktop_build_and_smoke_scripts_parse():
    paths = [
        ROOT / "scripts" / "build_desktop_sidecar.py",
        ROOT / "scripts" / "build_pyside_spike.py",
        ROOT / "scripts" / "desktop_sidecar_smoke.py",
        ROOT / "scripts" / "smoke_tauri_bundle.py",
        ROOT / "desktop" / "pyside6_spike" / "app.py",
        ROOT / "desktop" / "pyside6_spike" / "pyside_chart.py",
        ROOT / "desktop" / "pyside6_spike" / "pyside_gui.py",
        ROOT / "hybrid_runtime" / "engine_adapter.py",
    ]
    for path in paths:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
