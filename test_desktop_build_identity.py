from __future__ import annotations

import ast
from pathlib import Path
import plistlib
import subprocess
import sys

from hybrid_runtime.build_identity import (
    CHANNEL_KEY,
    SOURCE_COMMIT_KEY,
    VERSION_LABEL_KEY,
    bundle_info_path_from_executable,
    read_build_identity,
    stamp_bundle_identity,
)
from hybrid_runtime.support_snapshot import build_support_snapshot
from scripts.package_desktop_beta import update_bundle_version


ROOT = Path(__file__).resolve().parent


def fake_app(tmp_path: Path) -> Path:
    app = tmp_path / "Trading Intelligence.app"
    contents = app / "Contents"
    executable = contents / "MacOS" / "Trading Intelligence"
    executable.parent.mkdir(parents=True)
    executable.write_text("binary-placeholder", encoding="utf-8")
    info = contents / "Info.plist"
    with info.open("wb") as handle:
        plistlib.dump(
            {
                "CFBundleName": "Trading Intelligence",
                "CFBundleDisplayName": "Trading Intelligence",
                "CFBundleShortVersionString": "0.0.0",
                "CFBundleVersion": "1",
            },
            handle,
        )
    return app


def test_bundle_identity_stamp_and_installed_app_readback(tmp_path, monkeypatch):
    app = fake_app(tmp_path)
    commit = "a" * 40
    stamped = stamp_bundle_identity(
        app,
        version_label="0.2.0-beta.47",
        channel="internal_beta",
        commit=commit,
        bundle_short_version="0.2.0",
        bundle_build="47",
        display_name="Trading Intelligence",
    )
    assert stamped == {
        "version_label": "0.2.0-beta.47",
        "channel": "internal_beta",
        "commit": commit,
        "bundle_short_version": "0.2.0",
        "build_number": "47",
    }

    info = app / "Contents" / "Info.plist"
    with info.open("rb") as handle:
        plist = plistlib.load(handle)
    assert plist[VERSION_LABEL_KEY] == "0.2.0-beta.47"
    assert plist[CHANNEL_KEY] == "internal_beta"
    assert plist[SOURCE_COMMIT_KEY] == commit
    assert plist["CFBundleShortVersionString"] == "0.2.0"
    assert plist["CFBundleVersion"] == "47"
    assert plist["CFBundleDisplayName"] == "Trading Intelligence"

    executable = app / "Contents" / "MacOS" / "Trading Intelligence"
    assert bundle_info_path_from_executable(executable) == info
    identity = read_build_identity(executable=executable, environment={})
    assert identity == {
        "version": "0.2.0-beta.47",
        "bundle_short_version": "0.2.0",
        "build_number": "47",
        "channel": "internal_beta",
        "commit": commit,
        "commit_short": "a" * 12,
        "packaged": True,
    }


def test_stamp_uses_ci_sha_when_explicit_commit_is_empty(tmp_path, monkeypatch):
    app = fake_app(tmp_path)
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    stamped = stamp_bundle_identity(
        app,
        version_label="0.3.0-rc.1",
        channel="notarized_candidate",
    )
    assert stamped["commit"] == "b" * 40
    assert read_build_identity(
        info_plist=app / "Contents" / "Info.plist",
        environment={},
    )["commit_short"] == "b" * 12


def test_source_mode_build_identity_is_safe_and_path_free(tmp_path):
    identity = read_build_identity(
        executable=tmp_path / "not-an-app" / "python",
        environment={
            "TRADING_INTELLIGENCE_BUILD_VERSION": "0.4.0-dev",
            "TRADING_INTELLIGENCE_BUILD_NUMBER": "123",
            "TRADING_INTELLIGENCE_BUILD_CHANNEL": "development_candidate",
            "TRADING_INTELLIGENCE_BUILD_COMMIT": "c" * 40,
        },
    )
    assert identity["packaged"] is False
    assert identity["version"] == "0.4.0-dev"
    assert identity["build_number"] == "123"
    assert identity["channel"] == "development_candidate"
    assert identity["commit_short"] == "c" * 12
    assert str(tmp_path) not in str(identity)


def test_internal_beta_packager_embeds_full_version_channel_build_and_commit(tmp_path):
    app = fake_app(tmp_path)
    commit = "d" * 40
    identity = update_bundle_version(
        app,
        "0.5.0-beta.88",
        "run-0088",
        commit=commit,
    )
    assert identity["version_label"] == "0.5.0-beta.88"
    assert identity["bundle_short_version"] == "0.5.0"
    assert identity["build_number"] == "0088"
    assert identity["channel"] == "internal_beta"
    assert identity["commit"] == commit


def test_support_snapshot_carries_only_safe_build_identity():
    snapshot = build_support_snapshot(
        {
            "build": {
                "version": "0.6.0-beta.9",
                "bundle_short_version": "0.6.0",
                "build_number": "9",
                "channel": "internal_beta",
                "commit": "e" * 40,
                "commit_short": "e" * 12,
                "packaged": True,
                "app_path": "/Users/Derek/Applications/Trading Intelligence.app",
            }
        }
    )
    assert snapshot["build"] == {
        "version": "0.6.0-beta.9",
        "bundle_short_version": "0.6.0",
        "build_number": "9",
        "channel": "internal_beta",
        "commit": "e" * 40,
        "commit_short": "e" * 12,
        "packaged": True,
    }
    assert "/Users/Derek" not in str(snapshot)
    assert "app_path" not in str(snapshot)


def test_release_scripts_can_run_directly_from_repo_root():
    for script in (
        "scripts/build_trading_intelligence_desktop.py",
        "scripts/package_desktop_beta.py",
        "scripts/stamp_desktop_bundle.py",
    ):
        completed = subprocess.run(
            [sys.executable, script, "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, (script, completed.stdout, completed.stderr)
        assert "usage:" in completed.stdout.lower()


def test_build_identity_is_wired_before_final_signing_and_into_system_health():
    build_script = (ROOT / "scripts/build_trading_intelligence_desktop.py").read_text(
        encoding="utf-8"
    )
    beta_script = (ROOT / "scripts/package_desktop_beta.py").read_text(encoding="utf-8")
    stamp_script = (ROOT / "scripts/stamp_desktop_bundle.py").read_text(encoding="utf-8")
    health_window = (ROOT / "desktop/trading_intelligence/system_health_window.py").read_text(
        encoding="utf-8"
    )
    health_page = (ROOT / "desktop/trading_intelligence/system_health_page.py").read_text(
        encoding="utf-8"
    )

    for path in (
        "hybrid_runtime/build_identity.py",
        "scripts/build_trading_intelligence_desktop.py",
        "scripts/package_desktop_beta.py",
        "scripts/stamp_desktop_bundle.py",
    ):
        ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)

    for script_source in (build_script, beta_script, stamp_script):
        assert "sys.path.insert(0, str(REPO_ROOT))" in script_source
    assert build_script.rfind("stamp_bundle_identity(") < build_script.rfind('["codesign", "--force"')
    assert 'DEFAULT_BUILD_CHANNEL = "development_candidate"' in build_script
    assert "embedded_identity = read_build_identity(" in beta_script
    assert 'channel: str = "notarized_candidate"' in stamp_script
    assert "read_build_identity()" in health_window
    assert 'result["build"] = read_build_identity()' in health_window
    assert 'self.build_status.setText("Build · "' in health_page
