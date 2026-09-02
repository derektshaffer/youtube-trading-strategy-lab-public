from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hybrid_runtime.contracts import ExecutionTarget, JobRequest
from hybrid_runtime.desktop_settings import (
    DesktopSettings,
    DesktopSettingsError,
    load_desktop_settings,
    save_desktop_settings,
)
from hybrid_runtime.engine_adapter import profit_first_plan_handler
from hybrid_runtime.library_source import LibrarySourceError, load_library_for_job
from hybrid_runtime.router import RoutingPolicy


def empty_library() -> dict:
    return {
        "strategies": [],
        "validation_runs": [],
        "research_queue": [],
        "predictive_ml_runs": [],
        "knowledge_sources": [],
    }


def test_desktop_settings_round_trip_without_secret_fields(tmp_path: Path):
    path = save_desktop_settings(
        DesktopSettings(
            library_source="local_file",
            local_library_path=str(tmp_path / "library.json"),
            github_repository="owner/private-library",
            github_branch="main",
            github_path="trading-intelligence-lab/intelligence_library.json",
        ),
        tmp_path,
    )
    loaded = load_desktop_settings(tmp_path)

    assert loaded.library_source == "local_file"
    assert loaded.github_repository == "owner/private-library"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert not any(
        "token" in key.lower() or "secret" in key.lower()
        for key in saved
    )
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_desktop_settings_reject_escape_paths_and_bad_repositories():
    with pytest.raises(DesktopSettingsError):
        DesktopSettings(github_path="../secret.json")
    with pytest.raises(DesktopSettingsError):
        DesktopSettings(github_repository="not-a-full-name")
    with pytest.raises(DesktopSettingsError):
        DesktopSettings(library_source="unknown")


def test_configured_local_library_loads_with_safe_summary(tmp_path: Path):
    library_path = tmp_path / "library.json"
    library = empty_library()
    library["strategies"] = [{"id": "alpha"}, {"id": "beta"}]
    library["validation_runs"] = [{"strategy_id": "alpha"}]
    library_path.write_text(json.dumps(library), encoding="utf-8")
    save_desktop_settings(
        DesktopSettings(
            library_source="local_file",
            local_library_path=str(library_path),
        ),
        tmp_path,
    )

    loaded = load_library_for_job({}, data_dir=tmp_path)

    assert loaded.metadata["source"] == "configured_local_file"
    assert loaded.metadata["strategies"] == 2
    assert loaded.metadata["validation_runs"] == 1
    assert loaded.metadata["cloud_refreshed"] is False
    assert "token" not in json.dumps(loaded.metadata).lower()


def test_inline_fixture_load_and_missing_source_failure(tmp_path: Path):
    loaded = load_library_for_job({"library": empty_library()}, data_dir=tmp_path)
    assert loaded.metadata["source"] == "inline_fixture"
    assert loaded.metadata["strategies"] == 0

    save_desktop_settings(
        DesktopSettings(
            library_source="local_file",
            local_library_path=str(tmp_path / "missing.json"),
        ),
        tmp_path,
    )
    with pytest.raises(LibrarySourceError):
        load_library_for_job({}, data_dir=tmp_path)


def test_profit_first_handler_returns_library_provenance_for_empty_fixture():
    progress: list[tuple[float, str, str]] = []
    result = profit_first_plan_handler(
        {"library": empty_library(), "maximum_candidates": 3},
        lambda fraction, stage, message: progress.append(
            (fraction, stage, message)
        ),
        lambda: False,
    )

    assert result["queue_status"] == "no-eligible-candidates"
    assert result["candidates"] == []
    assert result["library"]["source"] == "inline_fixture"
    assert progress[-1][1] == "saving"


def test_library_and_profit_first_requests_default_to_local_execution():
    policy = RoutingPolicy()
    for job_type in (
        "library.configuration",
        "library.summary",
        "strategy.profit_first_plan",
    ):
        decision = policy.decide(
            JobRequest(job_type=job_type, requested_target=ExecutionTarget.AUTO)
        )
        assert decision.target == ExecutionTarget.LOCAL
        assert decision.automatic is True
