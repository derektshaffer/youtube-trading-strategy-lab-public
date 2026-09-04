"""Search inventory is read-only; cancellation is exact, confirmed and fail-closed."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from hybrid_runtime.api import create_app
from hybrid_runtime.cloud_bridge import CloudBridgeWorker, DesktopCloudSettings
from hybrid_runtime.cloud_link_store import CloudLinkStore
from hybrid_runtime.contracts import JobStatus
from hybrid_runtime.github_library import GitHubLibraryConfig, GitHubLibraryConflict, GitHubLibraryError
from hybrid_runtime.search_monitor import SearchMonitor, remote_snapshot
from hybrid_runtime.service import HybridService
from hybrid_runtime.storage import HybridStore
from test_desktop_stock_finder_bridge import FakeClient


def remote(job_id="cloud-lab", status="running", **changes):
    return {"id": job_id, "type": "strategy_lab", "status": status, "stage": "optimizing", "progress": .58,
            "updated_at": "2026-09-04T17:00:00Z", "created_at": "2026-09-04T10:00:00Z",
            "payload": {"run_id": job_id, "ticker": "SDOT", "search_depth": 160, "timeframe": "5Min"}, **changes}


@pytest.fixture
def setup(tmp_path):
    service = HybridService(HybridStore(tmp_path / "jobs.sqlite3"))
    links = CloudLinkStore(tmp_path / "links.sqlite3")
    cloud = FakeClient()
    settings = DesktopCloudSettings(github=GitHubLibraryConfig(repository="owner/private-data"))
    worker = CloudBridgeWorker(service, links, data_dir=tmp_path,
        settings_loader=lambda _: settings, token_loader=lambda _: "fixture-token",
        client_factory=lambda *_: cloud)
    monitor = SearchMonitor(worker)
    return SimpleNamespace(service=service, links=links, cloud=cloud, settings=settings, worker=worker, monitor=monitor)


def test_inventory_finds_remote_only_searches_and_never_dispatches_or_creates_jobs(setup):
    s = setup
    s.cloud.document["research_queue"] = [remote(), remote("queued", "queued"), remote("done", "complete")]
    original = deepcopy(s.cloud.document)
    result = s.monitor.snapshot()
    assert result["active_count"] == 2 and not result["stale"]
    assert len(result["rows"]) == 3
    running = next(row for row in result["rows"] if row["id"] == "cloud-lab")
    assert running["progress"] == .58 and not running["can_cancel"]
    assert s.cloud.document == original and s.cloud.write_count == 0 and s.cloud.dispatches == []
    assert s.service.list() == []


def stoppable(s):
    item = remote()
    item["cloud_worker"] = {
        "version": 1, "repository": s.settings.github.action_repository,
        "run_id": "123", "run_attempt": 1, "head_sha": "a" * 40, "workflow": "cloud-strategy-lab.yml",
    }
    s.cloud.document["research_queue"] = [item]
    run = {"id": 123, "run_attempt": 1, "head_sha": "a" * 40,
           "repository": {"full_name": s.settings.github.action_repository},
           "path": ".github/workflows/cloud-strategy-lab.yml", "status": "in_progress"}
    s.cloud.workflow_run = lambda _: deepcopy(run)
    calls = []
    s.cloud.cancel_workflow_run = lambda run_id: calls.append(run_id)
    return run, calls


def test_running_stop_is_pending_until_actions_confirms_exit(setup):
    s = setup
    run, calls = stoppable(s)
    s.cloud.document["saved_results"] = ["keep"]
    row = s.monitor.snapshot()["rows"][0]
    assert row["can_cancel"]
    assert s.monitor.cancel(row)["status"] == "cancelling"
    assert calls == ["123"]
    assert s.cloud.document["research_queue"][0]["status"] == "cancelling"
    assert s.cloud.document["saved_results"] == ["keep"]
    assert s.monitor.snapshot(force=True)["rows"][0]["status"] == "cancelling"
    run.update(status="completed", conclusion="cancelled")
    count = s.cloud.write_count
    snapshot = s.monitor.snapshot(force=True)
    assert snapshot["rows"][0]["status"] == "cancelled"
    assert snapshot["active_count"] == 0
    assert s.cloud.write_count == count  # monitoring itself stays read-only
    assert not snapshot["rows"][0]["can_cancel"]


@pytest.mark.parametrize("failure", ["different_attempt", "permission", "conflict", "timeout"])
def test_running_stop_failures_never_claim_confirmed_cancellation(setup, failure):
    s = setup
    run, calls = stoppable(s)
    row = s.monitor.snapshot()["rows"][0]
    def fail(*args, **kwargs):
        raise GitHubLibraryError("Permission denied or response unavailable")
    if failure == "different_attempt":
        run["run_attempt"] = 2
    elif failure == "permission":
        s.cloud.workflow_run = fail
    elif failure == "conflict":
        s.cloud.write = fail
    else:
        s.cloud.cancel_workflow_run = fail
    with pytest.raises((ValueError, GitHubLibraryError)):
        s.monitor.cancel(row)
    assert not calls
    assert s.cloud.document["research_queue"][0]["status"] != "cancelled"
    assert s.cloud.write_count == (1 if failure == "timeout" else 0)


def test_worker_binding_changes_require_reselection(setup):
    s = setup
    run, calls = stoppable(s)
    row = s.monitor.snapshot()["rows"][0]
    s.cloud.document["research_queue"][0]["cloud_worker"]["run_id"] = "456"
    with pytest.raises(ValueError):
        s.monitor.cancel(row)
    assert not calls and s.cloud.write_count == 0


def test_progress_updates_do_not_invalidate_exact_selected_worker(setup):
    s = setup
    _, calls = stoppable(s)
    row = s.monitor.snapshot()["rows"][0]
    s.cloud.document["research_queue"][0]["payload"]["distributed_progress"] = .7
    assert s.monitor.cancel(row)["status"] == "cancelling"
    assert calls == ["123"]


def test_confirmed_worker_stop_releases_attached_desktop_job(setup):
    s = setup
    job, _ = s.service.submit({"job_type": "strategy.stock_finder", "payload": {"symbol": "ABDT", "profile": "Quick"}})
    s.worker.run_once()
    item = s.cloud.document["research_queue"][0]
    item.update(status="cancelling", cancel_requested=True, cloud_worker={
        "version": 1, "repository": s.settings.github.action_repository, "run_id": "123",
        "run_attempt": 1, "head_sha": "a" * 40, "workflow": "distributed-stock-finder.yml",
    })
    run = {"id": 123, "run_attempt": 1, "head_sha": "a" * 40,
           "repository": {"full_name": s.settings.github.action_repository},
           "path": ".github/workflows/distributed-stock-finder.yml", "status": "in_progress"}
    s.cloud.workflow_run = lambda _: run
    s.worker.run_once()
    assert not s.service.get(job.id).terminal
    run.update(status="completed", conclusion="cancelled")
    s.worker.run_once()
    assert s.service.get(job.id).status == JobStatus.CANCELLED
    assert s.cloud.document["research_queue"][0]["status"] == "cancelled"
    assert len(s.cloud.dispatches) == 1


def test_old_running_checkpoint_does_not_claim_requeued_search_is_running(setup):
    s = setup
    item = remote(status="queued", stage="requeued_after_stale_lease")
    checkpoints = {"validation_runs": [{"id": "cloud-lab", "record_type": "strategy_lab_checkpoint", "status": "running",
        "progress": .578, "stage": "walk_forward", "saved_at": "2026-09-03T01:00:00Z"}]}
    from hybrid_runtime.strategy_lab_bridge import STRATEGY_LAB_RECORD_TYPE
    checkpoints["validation_runs"][0]["record_type"] = STRATEGY_LAB_RECORD_TYPE
    row = remote_snapshot({"research_queue": [item]}, s.settings, "r1", checkpoints)["rows"][0]
    assert row["status"] == "queued" and row["stage"] == "requeued_after_stale_lease"
    assert row["progress"] == .578 and row["checkpoint_at"] == "2026-09-03T01:00:00Z"


def test_selected_queue_cancellation_preserves_other_searches_and_history(setup):
    s = setup
    s.cloud.document["research_queue"] = [remote("one", "queued"), remote("two"), remote("done", "complete")]
    s.cloud.document["saved_results"] = {"preserve": [1, 2, 3]}
    original = deepcopy(s.cloud.document)
    row = next(row for row in s.monitor.snapshot()["rows"] if row["id"] == "one")
    assert s.monitor.cancel(row)["status"] == "cancelled"
    assert s.cloud.document["research_queue"][0]["cancel_requested"]
    assert s.cloud.document["research_queue"][1:] == original["research_queue"][1:]
    assert s.cloud.document["saved_results"] == original["saved_results"]
    assert s.cloud.dispatches == [] and s.cloud.write_count == 1


@pytest.mark.parametrize("change", ["running", "identity", "duplicate", "binding", "conflict", "uncertain"])
def test_cancel_rechecks_identity_state_and_cas_without_retry(setup, monkeypatch, change):
    s = setup
    s.cloud.document["research_queue"] = [remote(status="queued")]
    row = s.monitor.snapshot()["rows"][0]
    if change == "running":
        s.cloud.document["research_queue"][0]["status"] = "running"
    elif change == "identity":
        s.cloud.document["research_queue"][0]["payload"]["ticker"] = "OTHER"
    elif change == "duplicate":
        s.cloud.document["research_queue"] *= 2
    elif change == "binding":
        row["binding"]["repository"] = "other/private-data"
    else:
        calls = []
        def fail(*args, **kwargs):
            calls.append(1)
            raise GitHubLibraryConflict("Branch moved") if change == "conflict" else GitHubLibraryError("Response lost")
        monkeypatch.setattr(s.cloud, "write", fail)
    with pytest.raises((ValueError, GitHubLibraryError)):
        s.monitor.cancel(row)
    assert s.cloud.write_count == 0 and s.cloud.dispatches == []
    if change in {"conflict", "uncertain"}:
        assert len(calls) == 1


def test_snapshot_read_failure_preserves_rows_and_marks_unknown_not_zero(setup, monkeypatch):
    s = setup
    s.cloud.document["research_queue"] = [remote(status="queued")]
    first = s.monitor.snapshot()
    def fail():
        raise GitHubLibraryError("Cloud unavailable")
    monkeypatch.setattr(s.cloud, "read", fail)
    latest = s.monitor.snapshot(force=True)
    assert latest["stale"] and latest["checked_at"] == first["checked_at"]
    assert latest["rows"][0]["id"] == "cloud-lab" and not latest["rows"][0]["can_cancel"]


def test_bridge_cached_snapshot_avoids_repeated_large_reads(setup, monkeypatch):
    s = setup
    s.worker.search_snapshot_cache = remote_snapshot({"research_queue": [remote()]}, s.settings, "revision")
    monkeypatch.setattr(s.cloud, "read", lambda: pytest.fail("should use existing bridge snapshot"))
    assert s.monitor.snapshot()["active_count"] == 1
    assert s.monitor.snapshot()["active_count"] == 1


def test_local_link_deduplication_and_local_cancel_without_cloud_dispatch(setup):
    s = setup
    job, _ = s.service.submit({"job_type": "strategy.stock_finder", "payload": {"symbol": "ABDT", "profile": "Quick"}})
    row = s.monitor.snapshot()["rows"][0]
    assert row["id"] == job.id and s.monitor.cancel(row)["status"] == "cancelled"
    assert s.service.get(job.id).status == JobStatus.CANCELLED
    assert s.cloud.write_count == 0 and s.cloud.dispatches == []


def test_api_auth_and_running_cancellation_is_rejected(setup):
    s = setup
    s.cloud.document["research_queue"] = [remote()]
    app = create_app(s.service, expected_token="fixture-token", search_monitor=s.monitor)
    with TestClient(app) as api:
        assert api.get("/v1/searches").status_code == 401
        assert api.post("/v1/searches/cancel", json={}).status_code == 401
        headers = {"Authorization": "Bearer fixture-token"}
        row = api.get("/v1/searches", headers=headers).json()["rows"][0]
        assert api.post("/v1/searches/cancel", headers=headers, json=row).status_code == 409
        assert s.cloud.write_count == 0


def test_linked_local_and_remote_search_show_one_row_and_cancel_reconciles(setup):
    s = setup
    job, _ = s.service.submit({"job_type": "strategy.stock_finder", "payload": {"symbol": "ABDT", "profile": "Quick"}})
    s.worker.run_once()
    result = s.monitor.snapshot(force=True)
    assert result["active_count"] == 1 and len(result["rows"]) == 1
    assert result["rows"][0]["key"].startswith("cloud:")
    assert s.monitor.cancel(result["rows"][0])["status"] == "cancelled"
    s.worker.run_once()
    assert s.service.get(job.id).status == JobStatus.CANCELLED
    assert len(s.cloud.dispatches) == 1  # only the original submission


def test_ambiguous_published_submission_cannot_be_cancelled_only_locally(setup, monkeypatch):
    s = setup
    job, _ = s.service.submit({"job_type": "strategy.stock_finder", "payload": {"symbol": "ABDT", "profile": "Quick"}})
    write = s.cloud.write
    def landed(*args, **kwargs):
        write(*args, **kwargs)
        raise GitHubLibraryError("Response lost")
    with monkeypatch.context() as patch:
        patch.setattr(s.cloud, "write", landed)
        s.worker.run_once()
    assert s.service.get(job.id).stage == "cloud_submission_failed"
    with pytest.raises(ValueError, match="exists in the cloud"):
        s.monitor.cancel({"key": "local:" + job.id, "identity": job.request_fingerprint})
    assert s.service.get(job.id).status == JobStatus.RETRY_WAIT
    assert s.cloud.document["research_queue"][0]["status"] == "queued"


@pytest.mark.parametrize("queue", [{"bad": "format"}, [remote(), remote()]])
def test_malformed_cloud_queue_does_not_report_no_searches(setup, queue):
    s = setup
    s.cloud.document["research_queue"] = queue
    result = s.monitor.snapshot(force=True)
    assert result["stale"] and result["warning"]
