from __future__ import annotations

import ast
from pathlib import Path

from scripts.package_desktop_beta import build_number, bundle_short_version, clean_version


ROOT = Path(__file__).resolve().parent


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_beta_packaging_sources_parse_and_version_helpers_are_bounded():
    for path in (
        "scripts/package_desktop_beta.py",
        "scripts/check_desktop_release_readiness.py",
    ):
        ast.parse(read(path), filename=path)
    assert clean_version(" 0.2.0 beta / unsafe ") == "0.2.0betaunsafe"
    assert clean_version("") == "0.1.0-beta"
    assert bundle_short_version("0.2.0-beta.47") == "0.2.0"
    assert bundle_short_version("2.4") == "2.4.0"
    assert bundle_short_version("beta") == "0.1.0"
    assert build_number("run-001234") == "001234"
    assert build_number("") == "1"


def test_internal_beta_workflow_is_structurally_gated_and_never_publishes_a_release():
    workflow = read(".github/workflows/desktop-beta-package.yml")
    # Keep this regression dependency-free because the normal repository test
    # environment intentionally installs requirements.txt rather than PyYAML.
    assert workflow.startswith("name: Trading Intelligence Desktop Beta Package\n")
    assert "jobs:\n  internal-beta:" in workflow
    assert "runs-on: macos-14" in workflow
    assert "package_desktop_beta.py" in workflow
    assert "check_desktop_release_readiness.py" in workflow
    assert "hdiutil attach" in workflow
    assert "codesign --verify --deep --strict" in workflow
    assert "retention-days: 30" in workflow
    assert "internal_beta_only" in workflow
    assert "public_distribution_ready" in workflow
    assert "gh release" not in workflow
    assert "create-release" not in workflow.lower()
    assert "APPLE_ID" not in workflow
    assert "APPLE_APP_SPECIFIC_PASSWORD" not in workflow
    assert "APPLE_DEVELOPER_ID" not in workflow


def test_packager_records_exact_distribution_state_and_checksums():
    source = read("scripts/package_desktop_beta.py")
    assert '"channel": "internal_beta"' in source
    assert '"bundle_short_version": short_version' in source
    assert '"public_distribution_ready"' in source
    assert '"developer_id_signed"' in source
    assert '"stapled_ticket_valid"' in source
    assert '"gatekeeper_accepted"' in source
    assert "SHA256SUMS.txt" in source
    assert "hdiutil" in source
    assert '"--sign", "-"' in source
    assert "Developer ID signing, Apple notarization" in source


def test_release_readiness_requires_all_public_distribution_gates():
    source = read("scripts/check_desktop_release_readiness.py")
    assert "codesign.returncode == 0" in source
    assert "developer_id" in source
    assert "hardened_runtime" in source
    assert "secure_timestamp" in source
    assert "stapler.returncode == 0" in source
    assert "gatekeeper.returncode == 0" in source
    assert '"public_release_ready" if ready else "internal_beta_only"' in source
    assert "--require-public-ready" in source
