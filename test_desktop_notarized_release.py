from __future__ import annotations

import ast
from pathlib import Path
import plistlib

from scripts.stamp_desktop_bundle import stamp


ROOT = Path(__file__).resolve().parent


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_distribution_helpers_parse_and_stamp_numeric_bundle_metadata(tmp_path):
    for path in (
        "scripts/stamp_desktop_bundle.py",
        "scripts/sign_desktop_distribution.py",
        "scripts/package_signed_desktop.py",
        "scripts/finalize_desktop_distribution.py",
    ):
        ast.parse(read(path), filename=path)

    app = tmp_path / "Trading Intelligence.app"
    contents = app / "Contents"
    contents.mkdir(parents=True)
    info = contents / "Info.plist"
    with info.open("wb") as handle:
        plistlib.dump({"CFBundleIdentifier": "com.derektshaffer.trading-intelligence"}, handle)
    result = stamp(app, version="0.3.0-beta.8", build="run-0042")
    with info.open("rb") as handle:
        data = plistlib.load(handle)
    assert result == {"bundle_short_version": "0.3.0", "bundle_build": "0042"}
    assert data["CFBundleShortVersionString"] == "0.3.0"
    assert data["CFBundleVersion"] == "0042"


def test_notarized_candidate_workflow_is_manual_only_and_never_publishes():
    workflow = read(".github/workflows/desktop-notarized-candidate.yml")
    assert workflow.startswith("name: Trading Intelligence Notarized Candidate\n")
    event_section = workflow.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in event_section
    assert "pull_request:" not in event_section
    assert "push:" not in event_section
    assert "runs-on: macos-14" in workflow
    assert "ref: main" in workflow
    assert "APPLE_DEVELOPER_ID_P12_BASE64" in workflow
    assert "APPLE_DEVELOPER_ID_P12_PASSWORD" in workflow
    assert "APPLE_NOTARY_APPLE_ID" in workflow
    assert "APPLE_NOTARY_PASSWORD" in workflow
    assert "APPLE_TEAM_ID" in workflow
    assert "security create-keychain" in workflow
    assert "security delete-keychain" in workflow
    assert "notarytool submit" in workflow
    assert "stapler staple" in workflow
    assert "--require-public-ready" in workflow
    assert "spctl -a -t open --context context:primary-signature" in workflow
    assert "gh release" not in workflow
    assert "create-release" not in workflow.lower()


def test_distribution_signer_requires_developer_id_hardened_runtime_and_timestamp():
    source = read("scripts/sign_desktop_distribution.py")
    assert 'startswith("Developer ID Application:")' in source
    assert '"--options",\n            "runtime"' in source
    assert '"--timestamp"' in source
    assert '"--deep"' in source
    assert "Authority=Developer ID Application:" in source
    assert '"runtime" not in text.lower()' in source


def test_signed_packager_never_mutates_or_resigns_app():
    source = read("scripts/package_signed_desktop.py")
    assert "public_distribution_ready" in source
    assert "Refusing distribution packaging" in source
    assert "plistlib" not in source
    assert '"--sign"' not in source
    assert "ditto" in source
    assert "hdiutil" in source


def test_final_manifest_requires_app_and_dmg_notarization_and_keeps_updates_disabled():
    source = read("scripts/finalize_desktop_distribution.py")
    assert "inspect_app(app)" in source
    assert "inspect_dmg(dmg)" in source
    assert "stapled_ticket_valid" in source
    assert "gatekeeper_accepted" in source
    assert '"public_distribution_ready": True' in source
    assert '"automatic_updates_ready": False' in source
    assert "SHA256SUMS.txt" in source
