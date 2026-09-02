from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from hybrid_runtime.support_snapshot import build_support_snapshot, snapshot_json


ROOT = Path(__file__).resolve().parent


def fixture_health() -> dict:
    return {
        "status": "attention",
        "checks": {
            "runtime_service": True,
            "runtime_storage": True,
            "library_connection": True,
            "library_readable": True,
            "alpaca_credentials": True,
            "github_library_credential": False,
            "setup_not_pending": False,
        },
        "required_checks": [
            "runtime_service",
            "runtime_storage",
            "library_connection",
            "library_readable",
            "alpaca_credentials",
        ],
        "setup": {
            "verification": "pending",
            "launch_ready": False,
            "capabilities": {"library": True, "cloud": False, "market": True},
        },
        "runtime": {"status": "ok", "authenticated_loopback": True},
        "library": {
            "source": "configured_local_file",
            "cloud_refreshed": False,
            "strategies": 123,
            "validation_runs": 45,
            "research_queue": 3,
            "predictive_ml_runs": 7,
            "knowledge_sources": 88,
            "warning": (
                "Provider failed at /Users/Derek/private/library.json with "
                "Authorization: Bearer SECRET_BEARER_VALUE and "
                "token=ghp_SUPERSECRET012345678901234567890. "
                "See https://example.com/problem?token=ALSO_SECRET&code=123"
            ),
            "raw_strategy_payload": {"never": "include-me"},
        },
        "connection": {
            "library_source_preference": "local_file",
            "library_mode": "local",
            "github_required_for_library": False,
            "github_repository": "private-owner/private-research-repo",
            "github_branch": "main",
            "github_path": "trading-intelligence-lab/intelligence_library.json",
            "market_feed": "sip",
            "local_library_path": "/Users/Derek/Documents/private/intelligence_library.json",
        },
        "jobs": {
            "active": 2,
            "sample_size": 42,
            "status_counts": {"complete": 30, "failed": 2, "running": 2},
            "type_counts": {"strategy.stock_finder": 5, "strategy.strategy_lab": 3},
            "payload": {"giant": "NEVER_INCLUDE" * 10_000},
            "recent_failures": [
                {
                    "id": "job-12345678901234567890",
                    "job_type": "strategy.stock_finder",
                    "stage": "cloud_dispatch",
                    "message": (
                        "api_key=ALPACA_SECRET_VALUE password=PASSWORD_VALUE "
                        "github_pat_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890 "
                        "/Users/AnotherPerson/secrets/file.txt "
                        + ("x" * 10_000)
                    ),
                    "updated_at": "2026-09-02T22:00:00Z",
                }
            ],
        },
        "storage": {
            "market": {"exists": True, "bytes": 123456, "files": 4, "latest_mtime": 1.0},
            "job_database": {"exists": True, "bytes": 9000, "files": 1},
            "cloud_links": {"exists": True, "bytes": 5000, "files": 1},
        },
        "data_dir": "/Users/Derek/Library/Application Support/Trading Intelligence Lab",
        "some_model_coefficients": [0.1] * 50_000,
    }


def test_support_snapshot_is_bounded_selected_and_redacted():
    snapshot = build_support_snapshot(
        fixture_health(),
        created_at=datetime(2026, 9, 2, 23, 0, tzinfo=timezone.utc),
    )
    encoded = snapshot_json(snapshot)

    assert snapshot["kind"] == "trading-intelligence-redacted-support-snapshot"
    assert snapshot["created_at"] == "2026-09-02T23:00:00Z"
    assert snapshot["setup"]["capabilities"] == {
        "library": True,
        "cloud": False,
        "market": True,
    }
    assert snapshot["library"]["counts"]["strategies"] == 123
    assert snapshot["connection"]["github_repository_name"] == "private-research-repo"
    assert snapshot["connection"]["local_library_filename"] == "intelligence_library.json"
    assert snapshot["jobs"]["recent_failures"][0]["job_id_suffix"] == "901234567890"

    for forbidden in (
        "SECRET_BEARER_VALUE",
        "ghp_SUPERSECRET",
        "ALSO_SECRET",
        "ALPACA_SECRET_VALUE",
        "PASSWORD_VALUE",
        "github_pat_ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "/Users/Derek",
        "/Users/AnotherPerson",
        "private-owner",
        "NEVER_INCLUDE",
        "some_model_coefficients",
        "raw_strategy_payload",
        "data_dir",
    ):
        assert forbidden not in encoded

    assert "<redacted>" in encoded
    assert "https://example.com/problem" in encoded
    assert "?token=" not in encoded
    assert len(encoded.encode("utf-8")) < 32_000


def test_support_snapshot_never_serializes_full_paths_or_library_payloads():
    health = fixture_health()
    health["library"]["strategies"] = [{"name": "PROPRIETARY_STRATEGY_PAYLOAD"}]
    health["library"]["validation_runs"] = [{"raw": "PRIVATE_VALIDATION_PAYLOAD"}]
    health["jobs"]["recent_failures"] = []
    encoded = json.dumps(build_support_snapshot(health), sort_keys=True)
    assert "PROPRIETARY_STRATEGY_PAYLOAD" not in encoded
    assert "PRIVATE_VALIDATION_PAYLOAD" not in encoded
    assert "Documents/private" not in encoded


def test_system_health_ui_exposes_copy_and_owner_only_save_actions_without_network_calls():
    page = (ROOT / "desktop/trading_intelligence/system_health_page.py").read_text(
        encoding="utf-8"
    )
    window = (ROOT / "desktop/trading_intelligence/system_health_window.py").read_text(
        encoding="utf-8"
    )
    snapshot = (ROOT / "hybrid_runtime/support_snapshot.py").read_text(encoding="utf-8")

    assert "Copy Support Snapshot" in page
    assert "Save Snapshot…" in page
    assert "copy_snapshot_requested" in page
    assert "save_snapshot_requested" in page
    assert "QApplication.clipboard()" in window
    assert "QFileDialog.getSaveFileName" in window
    assert "write_private_text_file(destination, text)" in window
    assert "last_system_health_result" in window
    assert "build_support_snapshot" in window
    assert "Full paths, credentials, research/strategy/model content" in snapshot

    copy_body = window.split("def copy_support_snapshot", 1)[1].split(
        "def save_support_snapshot", 1
    )[0]
    save_body = window.split("def save_support_snapshot", 1)[1].split(
        "__all__", 1
    )[0]
    assert "request_json" not in copy_body
    assert "request_json" not in save_body
    assert "urlopen" not in copy_body
    assert "urlopen" not in save_body
