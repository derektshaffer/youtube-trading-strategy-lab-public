from copy import deepcopy
import json

import pytest

from hybrid_runtime.api import create_app
from hybrid_runtime.cloud_bridge import CloudBridgeWorker, DesktopCloudSettings
from hybrid_runtime.cloud_link_store import CloudLinkStore
from hybrid_runtime.contracts import JobStatus, transition_allowed
from hybrid_runtime.github_library import GitHubLibraryConfig, GitHubLibraryError
from hybrid_runtime.service import HybridService
from hybrid_runtime.storage import HybridStore, HybridStoreError, InvalidJobTransition
from test_desktop_stock_finder_bridge import FakeClient


@pytest.fixture
def recovery(tmp_path):
    service = HybridService(HybridStore(tmp_path / "jobs.sqlite3"))
    links = CloudLinkStore(tmp_path / "links.sqlite3")
    client = FakeClient()
    settings = DesktopCloudSettings(github=GitHubLibraryConfig(repository="owner/private-data"))
    worker = CloudBridgeWorker(service, links, data_dir=tmp_path,
        settings_loader=lambda _: settings, token_loader=lambda _: "test-token",
        client_factory=lambda *_: client)
    job, _ = service.submit({"job_type": "strategy.stock_finder",
        "payload": {"symbol": "SDOT", "profile": "Deep"}, "requested_target": "cloud"})
    worker.run_once()
    remote = client.document["research_queue"][0]
    remote.update(status="failed", last_error="Previous authentication failure")
    worker.run_once()
    assert service.get(job.id).status == JobStatus.FAILED
    remote.update(status="running")
    remote["payload"].update(distributed_stage="distributed_optimization", distributed_progress=0.45)
    return worker, service, links, client, job, remote


def test_reconnect_preserves_failure_and_tracks_same_job_after_restart(recovery):
    worker, service, links, client, job, remote = recovery
    original = service.get(job.id)
    events = service.events(job.id)
    document = deepcopy(client.document)
    writes, dispatches = client.write_count, deepcopy(client.dispatches)
    result = worker.reconnect_failed_finder(job.id)
    assert result.id == job.id and result.status == JobStatus.OPTIMIZING
    assert result.progress == 0.45 and result.error is None and result.completed_at is None
    assert result.attempt == original.attempt
    assert service.events(job.id)[:len(events)] == events
    with service.store._reader() as connection:
        rows = connection.execute("SELECT * FROM cloud_job_recoveries").fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0]["previous_state_json"])["error"] == original.error
    assert links.get(job.id)["remote_job_id"] == remote["id"]
    assert client.write_count == writes and client.dispatches == dispatches
    assert client.document == document and len(service.list()) == 1
    # A new bridge/store instance must retain the read-only exact binding.
    worker.service = HybridService(HybridStore(service.store.path))
    remote["payload"]["distributed_progress"] = 0.7
    worker.run_once()
    assert worker.service.get(job.id).progress == 0.7
    with pytest.raises(HybridStoreError):
        worker.reconnect_failed_finder(job.id)
    assert len(service.list()) == 1 and client.write_count == writes


@pytest.mark.parametrize("change", ["missing_link", "different_repo", "different_branch", "different_path",
    "missing_job", "duplicate_job", "wrong_type", "wrong_symbol", "wrong_owner", "still_failed",
    "cancelled", "cancel_requested", "complete_no_report", "network_failure", "stale_local",
    "malformed_payload", "malformed_marker", "stale_link"])
def test_reconnect_fails_closed_without_mutation(recovery, change):
    worker, service, links, client, job, remote = recovery
    if change == "missing_link":
        links.delete(job.id)
    elif change.startswith("different_"):
        key = {"different_repo": "repository", "different_branch": "branch", "different_path": "path"}[change]
        args = {key: "other/value"}
        worker.settings_loader = lambda _: DesktopCloudSettings(github=GitHubLibraryConfig(
            **{"repository": "owner/private-data", **args}))
    elif change == "missing_job":
        remote["id"] = "different-id-with-same-dedupe"
    elif change == "duplicate_job":
        client.document["research_queue"].append(deepcopy(remote))
    elif change == "wrong_type":
        remote["type"] = "autonomous_validation"
    elif change == "wrong_symbol":
        remote["payload"]["symbol"] = "AAPL"
    elif change == "wrong_owner":
        remote["payload"]["hybrid_cloud_bridge"]["local_job_id"] = "another-local-job"
    elif change in {"still_failed", "cancelled", "complete_no_report"}:
        remote["status"] = {"still_failed": "failed", "cancelled": "cancelled", "complete_no_report": "complete"}[change]
    elif change == "cancel_requested":
        remote["cancel_requested"] = True
    elif change == "malformed_payload":
        remote["payload"] = "invalid"
    elif change == "malformed_marker":
        remote["payload"]["hybrid_cloud_bridge"] = "invalid"
    elif change == "network_failure":
        def fail():
            raise GitHubLibraryError("network unavailable")
        client.read = fail
    elif change == "stale_local":
        original_read = client.read
        def changed_while_reading():
            service.store.transition_job(job.id, JobStatus.FAILED, message="New failure evidence")
            return original_read()
        client.read = changed_while_reading
    elif change == "stale_link":
        original_read = client.read
        def changed_link():
            settings = worker.settings_loader(None)
            links.upsert(local_job_id=job.id, remote_job_id="changed-id",
                repository=settings.github.repository, branch=settings.github.branch, path=settings.github.path)
            return original_read()
        client.read = changed_link
    before = service.get(job.id)
    writes, dispatches = client.write_count, deepcopy(client.dispatches)
    with pytest.raises((HybridStoreError, GitHubLibraryError)):
        worker.reconnect_failed_finder(job.id)
    current = service.get(job.id)
    assert current.status == before.status == JobStatus.FAILED
    assert current.error == before.error
    assert service.store.cloud_recovery(job.id) is None
    assert client.write_count == writes and client.dispatches == dispatches


def test_recovered_attachment_never_republishes_missing_or_replaced_remote(recovery):
    worker, service, links, client, job, remote = recovery
    worker.reconnect_failed_finder(job.id)
    writes, dispatches = client.write_count, deepcopy(client.dispatches)
    remote["id"] = "same-request-but-another-job"
    worker.run_once()
    assert client.write_count == writes and client.dispatches == dispatches
    assert "missing or duplicated" in links.get(job.id)["dispatch_error"]
    assert len(client.document["research_queue"]) == 1
    # Losing the shortcut DB must not permit re-publication either.
    links.delete(job.id)
    worker.run_once()
    assert client.write_count == writes


def test_repeated_recovery_uses_immutable_audited_repository(recovery):
    worker, service, links, client, job, remote = recovery
    worker.reconnect_failed_finder(job.id)
    remote["status"] = "failed"
    worker.run_once()
    remote["status"] = "running"
    links.upsert(local_job_id=job.id, remote_job_id=remote["id"],
        repository="other/repository", branch=worker.settings_loader(None).github.branch,
        path=worker.settings_loader(None).github.path)
    worker.settings_loader = lambda _: DesktopCloudSettings(github=GitHubLibraryConfig(repository="other/repository"))
    with pytest.raises(HybridStoreError, match="connection changed"):
        worker.reconnect_failed_finder(job.id)
    assert service.get(job.id).status == JobStatus.FAILED


def test_additive_schema_upgrade_preserves_existing_job_history(recovery):
    worker, service, links, client, job, remote = recovery
    before = service.get(job.id).as_dict()
    events = service.events(job.id)
    with service.store._transaction(immediate=True) as connection:
        connection.execute("DROP TABLE cloud_job_recoveries")
        connection.execute("DELETE FROM schema_migrations")
        connection.execute("INSERT INTO schema_migrations VALUES (1, 'old')")
    upgraded = HybridStore(service.store.path)
    assert upgraded.get_job(job.id).as_dict() == before
    assert upgraded.list_events(job.id) == events
    assert upgraded.cloud_recovery(job.id) is None
    with upgraded._reader() as connection:
        assert [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2]


def test_completed_recovery_requires_and_returns_exact_report(recovery):
    worker, service, links, client, job, remote = recovery
    stamp = "2026-09-03T12:00:00+00:00"
    remote.update(status="complete", result_ref=f"distributed-finder:SDOT:Deep:{stamp}")
    client.document["stock_strategy_finder_runs"] = [{"id": "report", "generated_at": stamp,
        "symbol": "SDOT", "profile": "Deep", "winner_strategy_name": "Momentum",
        "unique_configurations_tested": 12, "verdict": {"code": "research_only"}}]
    result = worker.reconnect_failed_finder(job.id)
    assert result.status == JobStatus.COMPLETE
    assert result.result["outcome"] == "stock_finder_complete"
    assert result.result["remote_job_id"] == remote["id"]
    assert result.result["finder_report"]
    assert len(client.dispatches) == 1 and client.write_count == 1


def test_terminal_transition_gate_is_unchanged(recovery):
    worker, service, links, client, job, remote = recovery
    for terminal in (JobStatus.FAILED, JobStatus.COMPLETE, JobStatus.CANCELLED):
        assert not transition_allowed(terminal, JobStatus.CLAIMED)
    with pytest.raises(InvalidJobTransition):
        service.store.transition_job(job.id, JobStatus.CLAIMED)


@pytest.mark.parametrize("change", ["cancel_requested", "complete", "cancelled", "result", "local"])
def test_local_terminal_protection(recovery, change):
    worker, service, links, client, job, remote = recovery
    # Simulate protected existing durable records; no network read may occur.
    with service.store._transaction(immediate=True) as connection:
        field, value = {
            "cancel_requested": ("cancel_requested", 1), "complete": ("status", "complete"),
            "cancelled": ("status", "cancelled"), "result": ("result_json", '{"saved":true}'),
            "local": ("execution_target", "local"),
        }[change]
        connection.execute(f"UPDATE jobs SET {field} = ? WHERE id = ?", (value, job.id))
    def forbidden_read():
        pytest.fail("Protected local record triggered a remote read")
    client.read = forbidden_read
    before = service.get(job.id).as_dict()
    with pytest.raises(HybridStoreError):
        worker.reconnect_failed_finder(job.id)
    assert service.get(job.id).as_dict() == before


def test_authenticated_endpoint_is_explicit_and_checks_result(recovery):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    worker, service, links, client, job, remote = recovery
    api = TestClient(create_app(service, expected_token="x" * 48,
        cloud_reconnect=worker.reconnect_failed_finder))
    url = f"/v1/jobs/{job.id}/reconnect-cloud"
    assert api.post(url).status_code == 401
    auth = {"Authorization": "Bearer " + "x" * 48}
    assert api.post(url, headers=auth).json()["status"] == "optimizing"
    assert api.post(url, headers=auth).status_code == 409
    assert api.post("/v1/jobs/missing/reconnect-cloud", headers=auth).status_code == 404
