"""Native Finder signals + authenticated local API + durable bridge, no cloud writes."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
from copy import deepcopy

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from fastapi.testclient import TestClient

from desktop.trading_intelligence.finder_window import MainWindow
from hybrid_runtime.api import create_app
from hybrid_runtime.cloud_bridge import CloudBridgeWorker, DesktopCloudSettings
from hybrid_runtime.cloud_link_store import CloudLinkStore
from hybrid_runtime.contracts import JobStatus
from hybrid_runtime.github_library import GitHubLibraryConfig, GitHubLibraryConflict, GitHubLibraryError
from hybrid_runtime.service import HybridService
from hybrid_runtime.storage import HybridStore
from test_desktop_stock_finder_bridge import FakeClient


PUSH_ERROR = "Large cloud-library Git push failed. Check GitHub connectivity and repository access."


class Runtime:
    def __init__(self, directory):
        self.data_dir = directory
        self.service = HybridService(HybridStore(directory / "jobs.sqlite3"))
        self.links = CloudLinkStore(directory / "links.sqlite3")
        self.cloud = FakeClient()
        self.worker = CloudBridgeWorker(
            self.service, self.links, data_dir=directory,
            settings_loader=lambda _: DesktopCloudSettings(github=GitHubLibraryConfig(repository="owner/private-data")),
            token_loader=lambda _: "fixture-token",
            client_factory=lambda *_: self.cloud,
        )
        self.api = TestClient(create_app(
            self.service, expected_token="local-fixture-token", cloud_link_lookup=self.links.get,
            cloud_submission_retry=self.worker.retry_finder_submission,
        ))
        self.calls = []
        self.submission_error = None

    def request_json(self, method, path, payload=None, **kwargs):
        self.calls.append((method, path, deepcopy(payload)))
        if method == "POST" and path in {"/v1/route", "/v1/jobs"} and self.submission_error:
            raise self.submission_error
        response = self.api.request(method, path, json=payload,
                                    headers={"Authorization": "Bearer local-fixture-token"})
        if response.status_code >= 400:
            raise RuntimeError(response.json().get("detail"))
        return response.json()


@pytest.fixture(scope="module")
def app():
    from PySide6.QtCore import QLibraryInfo
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    monkeypatch.setattr(QTimer, "singleShot", lambda *args: None)
    runtime = Runtime(tmp_path)
    w = MainWindow(runtime, smoke=True)
    for timer in w.findChildren(QTimer):
        timer.stop()
    w.finder.symbol.setText("ABDT")
    w.finder.profile.setCurrentText("Very Deep")
    yield w
    w.close()
    runtime.api.close()


def assert_retry_available(window):
    page = window.finder
    assert window.finder_job_id == ""
    assert window.finder_route == {}
    assert page.banner.property("state") == "error"
    assert page.status.text() == "Cloud Finder · Submission failed — retry"
    assert page.progress.format() == "Submission failed — retry available"
    assert page.run.isEnabled() and page.symbol.isEnabled() and page.profile.isEnabled()
    assert page.symbol.text() == "ABDT" and page.profile.currentData() == "Very Deep"
    assert "click Run Finder in Cloud to retry" in page.detail.text()


def fail_push(window, monkeypatch, message=PUSH_ERROR):
    window.finder.run.click()
    job_id = window.finder_job_id
    with monkeypatch.context() as patch:
        patch.setattr(window.runtime.cloud, "write", lambda *a, **kw: (_ for _ in ()).throw(GitHubLibraryError(message)))
        window.runtime.worker.run_once()
    window._poll_stock_finder()
    return job_id


def finish_remote(runtime):
    remote = runtime.cloud.document["research_queue"][0]
    generated = "2026-09-04T19:00:00+00:00"
    remote.update(status="complete", result_ref=f"distributed-finder:ABDT:Very Deep:{generated}")
    remote["payload"].update(distributed_stage="complete", distributed_progress=1.0)
    runtime.cloud.document["stock_strategy_finder_runs"] = [{
        "id": "fixture-report", "generated_at": generated, "symbol": "ABDT", "profile": "Very Deep",
        "winner_strategy_name": "Fixture strategy", "verdict": {"code": "research_only"},
    }]


def test_push_failure_releases_native_controls_and_retry_reuses_exact_job(window, monkeypatch):
    runtime = window.runtime
    job_id = fail_push(window, monkeypatch)
    assert_retry_available(window)
    assert PUSH_ERROR in window.finder.detail.text()
    saved = runtime.service.get(job_id)
    assert saved.status == JobStatus.RETRY_WAIT and saved.stage == "cloud_submission_failed"
    assert saved.error["type"] == "CloudFinderSubmissionError"
    assert not runtime.worker.run_once()  # no silent retry
    assert runtime.cloud.write_count == 0 and runtime.cloud.dispatches == []

    window.finder.run.click()  # actual signal -> retry endpoint -> same durable request
    assert window.finder_job_id == job_id
    assert not window.finder.run.isEnabled()
    assert runtime.service.get(job_id).status == JobStatus.QUEUED
    assert runtime.service.get(job_id).error == {}
    assert runtime.service.get(job_id).payload == saved.payload
    runtime.worker.run_once()
    window._poll_stock_finder()
    assert window.finder.status.text() == "ABDT queued"
    assert runtime.cloud.write_count == len(runtime.cloud.dispatches) == 1
    assert len(runtime.service.list()) == 1
    assert sum(method == "POST" and path == "/v1/jobs" for method, path, _ in runtime.calls) == 1
    assert "explicit retry required" in " ".join(event["message"] for event in runtime.service.events(job_id))
    runtime.worker.run_once()
    assert runtime.cloud.write_count == len(runtime.cloud.dispatches) == 1


def test_classified_git_error_reaches_saved_request_and_native_retry_panel(window, monkeypatch, tmp_path):
    import subprocess
    from hybrid_runtime import github_git_upload as upload
    private = "fixture-secret-never-display"
    with monkeypatch.context() as patch:
        patch.setattr(upload.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
            [], 128, b"", f"remote: Write access to repository not granted\nAuthorization: Bearer {private}".encode()))
        with pytest.raises(GitHubLibraryError) as caught:
            upload._run_git("git", ["push"], root=tmp_path, environment={}, operation="push")
    message = str(caught.value)
    job_id = fail_push(window, monkeypatch, message)
    assert_retry_available(window)
    assert "[repository_access; exit 128]" in window.finder.detail.text()
    assert "Contents write permission" in window.finder.detail.text()
    assert window.runtime.service.get(job_id).error["message"] == message
    assert private not in window.finder.detail.text()
    assert not window.runtime.worker.run_once()  # explicit retry only


@pytest.mark.parametrize("phase", ["read", "write", "conflict", "settings", "token", "plan"])
def test_all_prepublication_errors_pause_and_preserve_inputs(window, monkeypatch, phase):
    runtime = window.runtime
    window.finder.run.click()
    job_id = window.finder_job_id
    def fail(*args, **kwargs):
        raise GitHubLibraryConflict("Branch moved") if phase == "conflict" else GitHubLibraryError(PUSH_ERROR)
    if phase == "settings":
        monkeypatch.setattr(runtime.worker, "settings_loader", lambda _: None)
    elif phase == "token":
        monkeypatch.setattr(runtime.worker, "token_loader", fail)
    elif phase == "plan":
        monkeypatch.setattr(runtime.worker, "_publication_for", fail)
    else:
        monkeypatch.setattr(runtime.cloud, "read" if phase == "read" else "write", fail)
    runtime.worker.run_once()
    window._poll_stock_finder()
    assert_retry_available(window)
    assert runtime.service.get(job_id).stage == "cloud_submission_failed"
    assert not runtime.worker.run_once()


def test_synchronous_submission_error_and_retry(window):
    runtime = window.runtime
    runtime.submission_error = RuntimeError("Local service unavailable")
    window.finder.run.click()
    assert_retry_available(window)
    runtime.submission_error = None
    window.finder.run.click()
    assert window.finder_job_id and not window.finder.run.isEnabled()
    runtime.worker.run_once()
    window._poll_stock_finder()
    assert runtime.cloud.write_count == 1


def test_paused_submission_restores_without_restart_or_automatic_publication(window, monkeypatch):
    job_id = fail_push(window, monkeypatch)
    window.finder.symbol.clear()
    window.finder.profile.setCurrentText("Quick")
    window._restore_background_cloud_jobs()
    window._poll_stock_finder()
    assert_retry_available(window)
    assert not window.runtime.worker.run_once()
    window.finder.run.click()
    assert window.finder_job_id == job_id


def test_ambiguous_push_landed_then_completed_retry_attaches_exact_run(window, monkeypatch):
    runtime = window.runtime
    window.finder.run.click()
    job_id = window.finder_job_id
    original_write = runtime.cloud.write
    def landed(*args, **kwargs):
        original_write(*args, **kwargs)
        raise GitHubLibraryError("Push response lost")
    with monkeypatch.context() as patch:
        patch.setattr(runtime.cloud, "write", landed)
        runtime.worker.run_once()
    window._poll_stock_finder()
    remote = runtime.cloud.document["research_queue"][0]
    finish_remote(runtime)
    window.finder.run.click()
    runtime.worker.run_once()
    window._poll_stock_finder()
    link = runtime.links.get(job_id)
    assert link["remote_job_id"] == remote["id"]
    assert runtime.cloud.write_count == 1 and runtime.cloud.dispatches == []
    assert len(runtime.cloud.document["research_queue"]) == 1
    assert runtime.service.get(job_id).status == JobStatus.COMPLETE
    assert "Finder complete" in window.finder.status.text()


def test_successful_submission_running_and_complete_release_controls(window):
    runtime = window.runtime
    window.finder.run.click()
    job_id = window.finder_job_id
    runtime.worker.run_once()
    remote = runtime.cloud.document["research_queue"][0]
    remote["status"] = "running"
    remote["payload"].update(distributed_stage="distributed_optimization", distributed_progress=.5)
    runtime.worker.run_once()
    window._poll_stock_finder()
    assert window.finder.progress.value() == 500 and not window.finder.run.isEnabled()
    finish_remote(runtime)
    runtime.worker.run_once()
    window._poll_stock_finder()
    assert window.finder_job_id == "" and window.finder_route == {}
    assert window.finder.run.isEnabled() and window.finder.profile.isEnabled()
    assert "Finder complete" in window.finder.status.text()
    assert runtime.cloud.write_count == len(runtime.cloud.dispatches) == 1


@pytest.mark.parametrize("change", ["symbol", "profile"])
def test_editing_inputs_after_failure_does_not_mutate_or_retry_old_request(window, monkeypatch, change):
    runtime = window.runtime
    paused_id = fail_push(window, monkeypatch)
    original = runtime.service.get(paused_id).payload
    if change == "symbol":
        window.finder.symbol.setText("SLS")
    else:
        window.finder.profile.setCurrentText("Quick")
    window.finder.run.click()
    assert window.finder_job_id != paused_id
    assert runtime.service.get(paused_id).stage == "cloud_submission_failed"
    assert runtime.service.get(paused_id).payload == original
    runtime.worker.run_once()
    assert runtime.cloud.write_count == len(runtime.cloud.dispatches) == 1


def test_lost_local_submission_response_reattaches_without_duplicate(window, monkeypatch):
    runtime = window.runtime
    original_request = runtime.request_json
    def lost_response(method, path, payload=None, **kwargs):
        response = original_request(method, path, payload, **kwargs)
        if method == "POST" and path == "/v1/jobs":
            raise TimeoutError("Submission response lost")
        return response
    with monkeypatch.context() as patch:
        patch.setattr(runtime, "request_json", lost_response)
        window.finder.run.click()
    assert_retry_available(window)
    saved_id = runtime.service.list()[0].id
    window.finder.run.click()
    assert window.finder_job_id == saved_id and len(runtime.service.list()) == 1
    runtime.worker.run_once()
    assert runtime.cloud.write_count == len(runtime.cloud.dispatches) == 1


def test_published_dispatch_failure_and_read_outage_do_not_resubmit(window, monkeypatch):
    runtime = window.runtime
    window.finder.run.click()
    job_id = window.finder_job_id
    monkeypatch.setattr(runtime.cloud, "dispatch_workflow", lambda *a, **kw: False)
    runtime.worker.run_once()
    window._poll_stock_finder()
    assert window.finder_job_id == job_id and not window.finder.run.isEnabled()
    assert "published cloud job remains attached" in window.finder.detail.text()
    with monkeypatch.context() as patch:
        patch.setattr(runtime.cloud, "read", lambda: (_ for _ in ()).throw(GitHubLibraryError("Offline")))
        runtime.worker.run_once()
    window._poll_stock_finder()
    assert runtime.service.get(job_id).status == JobStatus.CLAIMED
    assert window.finder_job_id == job_id
    runtime.worker.run_once()
    assert runtime.cloud.write_count == 1
    response = runtime.api.post(f"/v1/jobs/{job_id}/retry-cloud-submission",
                                headers={"Authorization": "Bearer local-fixture-token"})
    assert response.status_code == 409


def test_retry_api_requires_auth_and_busy_sync_accepts_saved_retry(window, monkeypatch):
    runtime = window.runtime
    job_id = fail_push(window, monkeypatch)
    path = f"/v1/jobs/{job_id}/retry-cloud-submission"
    assert runtime.api.post(path).status_code == 401
    held, release = threading.Event(), threading.Event()
    def hold():
        with runtime.worker._reconciliation_lock:
            held.set()
            release.wait(5)
    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert held.wait(2)
        window.finder.run.click()
        assert window.finder_job_id == job_id
        assert runtime.service.get(job_id).status == JobStatus.QUEUED
        window._poll_stock_finder()
        assert window.finder.status.text() == "Cloud Finder · Retry Saved"
        assert "waiting for cloud sync" in window.finder.detail.text()
        assert "Cloud connection required" not in window.finder.detail.text()
    finally:
        release.set()
        thread.join(2)
    assert window.finder_job_id == job_id
