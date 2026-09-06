"""Offline checkpoint throughput and terminal-detail regression coverage."""
from copy import deepcopy
import json
import time
from unittest.mock import Mock

import pytest

import cloud_strategy_lab_worker as worker
import strategy_lab_jobs as jobs
from hybrid_runtime.cloud_bridge import CloudBridgeWorker, DesktopCloudSettings
from hybrid_runtime.cloud_link_store import CloudLinkStore
from hybrid_runtime.contracts import JobStatus
from hybrid_runtime.github_library import GitHubLibraryConfig
from hybrid_runtime.service import HybridService
from hybrid_runtime.storage import HybridStore, InvalidJobTransition
from hybrid_runtime.strategy_lab_bridge import (
    STRATEGY_LAB_CHECKPOINT_PATH, overlay_strategy_lab_checkpoint,
)
from strategy_lab_persistence import save_strategy_lab_checkpoint, load_latest_strategy_lab_checkpoint
from strategy_lab_progress import PROGRESS_FORMAT, progress_store, save_progress
from strategy_lab_telemetry import CheckpointTelemetry, instrument_checkpoint_store, timed_checkpoint_io
from test_strategy_lab_cloud_bridge import FakeGitHub
from test_strategy_lab_diagnostic_budget import BOUNDS, SID, queue, request
from youtube_strategy_engine import StrategyStore


def bridge_fixture(tmp_path):
    service = HybridService(HybridStore(tmp_path / "jobs.sqlite3"))
    links = CloudLinkStore(tmp_path / "links.sqlite3")
    fake = FakeGitHub()
    settings = DesktopCloudSettings(github=GitHubLibraryConfig(
        repository="fixture/private", path="main-library.json", action_repository="fixture/app"))
    bridge = CloudBridgeWorker(service, links, data_dir=tmp_path,
        settings_loader=lambda _: settings, token_loader=lambda _: "fixture-token", client_factory=fake.client)
    job, _ = service.submit(request().as_dict())
    bridge.run_once()
    remote = fake.documents["main-library.json"]["research_queue"][0]
    remote.update(status="running", attempts=1)
    remote["payload"].update(diagnostic_deadline_at="2026-09-05T23:03:27Z",
                             diagnostic_attempt_started_at="2026-09-05T22:43:27Z")
    checkpoint = {"id": request().payload["run_id"], "record_type": "strategy_lab_checkpoint",
        "ticker": "SPY", "attempt": 1, "status": "failed", "stage": "optimization", "progress": .4835,
        "saved_at": "2026-09-05T23:03:33Z", "execution_error": {
            "kind": "execution_timeout", "category": "infrastructure", "last_execution_stage": "optimization"}}
    fake.documents[STRATEGY_LAB_CHECKPOINT_PATH]["validation_runs"] = [checkpoint]
    return service, bridge, fake, job, remote, checkpoint


@pytest.mark.parametrize("queue_status", ["running", "failed"])
def test_timeout_details_reach_serialized_api_and_sqlite_even_when_queue_lags(tmp_path, queue_status):
    service, bridge, fake, job, remote, checkpoint = bridge_fixture(tmp_path)
    remote["status"] = queue_status
    bridge.run_once()
    saved = service.get(job.id)
    api = json.loads(json.dumps(saved.as_dict()))
    assert api["status"] == "failed" and api["result"] is None
    assert api["progress"] == .4835
    error = api["error"]
    assert error["kind"] == error["terminal_reason"] == "execution_timeout"
    assert error["category"] == "infrastructure" and error["last_execution_stage"] == "optimization"
    assert error["diagnostic_timeout_minutes"] == 20 and error["diagnostic_max_attempts"] == 1
    assert error["diagnostic_deadline_at"] == remote["payload"]["diagnostic_deadline_at"]
    assert error["last_checkpoint_saved_at"] == checkpoint["saved_at"]
    assert "no strategy validation verdict" in error["message"]
    assert service.store.get_job(job.id).error == saved.error
    bridge.run_once()
    assert len(fake.dispatches) == 1


def test_generic_terminal_failure_can_gain_details_without_reopening_or_redispatch(tmp_path):
    service, bridge, fake, job, remote, checkpoint = bridge_fixture(tmp_path)
    failed = service.store.transition_job(job.id, JobStatus.FAILED, stage="failed", progress=.48,
        error={"type": "CloudStrategyLabError", "message": "Cloud Strategy Validation failed"})
    bridge.run_once()
    enriched = service.get(job.id)
    assert enriched.error["kind"] == "execution_timeout"
    assert enriched.error["last_execution_stage"] == "optimization"
    assert enriched.progress == .4835 and enriched.status == JobStatus.FAILED
    for name in ("attempt", "completed_at", "heartbeat_at", "updated_at", "result", "payload", "stage"):
        assert getattr(enriched, name) == getattr(failed, name)
    bridge.run_once()
    assert len(fake.dispatches) == 1 and len(fake.documents["main-library.json"]["research_queue"]) == 1


def test_missing_queue_for_terminal_detail_repair_never_publishes(tmp_path):
    service, bridge, fake, job, remote, checkpoint = bridge_fixture(tmp_path)
    service.store.transition_job(job.id, JobStatus.FAILED,
        error={"type": "CloudStrategyLabError", "message": "previous generic failure"})
    fake.documents["main-library.json"]["research_queue"] = []
    bridge.run_once()
    assert fake.documents["main-library.json"]["research_queue"] == []
    assert len(fake.dispatches) == 1
    assert service.get(job.id).status == JobStatus.FAILED


@pytest.mark.parametrize("changes", [{"ticker": "QQQ"}, {"id": "different-run"}, {"attempt": 2}])
def test_other_run_ticker_or_attempt_cannot_supply_timeout_details(tmp_path, changes):
    _, _, _, _, remote, checkpoint = bridge_fixture(tmp_path)
    result = overlay_strategy_lab_checkpoint(remote, {**checkpoint, **changes})
    assert not result.get("error")


def test_metadata_enrichment_refuses_live_or_completed_jobs(tmp_path):
    service, _, _, job, _, _ = bridge_fixture(tmp_path)
    current = service.get(job.id)
    with pytest.raises(InvalidJobTransition):
        service.store.enrich_failed_strategy_lab(job.id, error={"kind": "execution_timeout"},
                                                 expected_updated_at=current.updated_at)


def test_repeated_timeout_cleanup_preserves_original_stage_progress_budget_and_resume_state(queue, tmp_path, monkeypatch):
    full = StrategyStore(tmp_path / "checkpoint")
    monkeypatch.setattr(worker, "build_checkpoint_store", lambda: full)
    claimed = worker._claim("remote")
    payload = claimed["payload"]
    state = {"fingerprint": "existing-integrity-signature", "symbol": "SPY",
             "completed_strategy_ids": [SID], "rankings": [{"source_strategy_id": SID,
             "folds": [{"train": [1, 2], "oos": [3], "metrics": {"pnl": -2}}]}],
             "configuration_history": [{"signature": "unchanged", "rules": {"pivot": 5}}]}
    save_strategy_lab_checkpoint(full, run_id=payload["run_id"], ticker="SPY", status="running",
        job=payload, optimizer_state=state, attempt=1, started_at="2026-09-05T22:42:16Z",
        progress=.4, stage="optimization", progress_storage=PROGRESS_FORMAT)
    save_progress(progress_store(full, payload["run_id"]), run_id=payload["run_id"], ticker="SPY",
        attempt=1, started_at="2026-09-05T22:42:16Z", fraction=.4835, stage="optimization", message="last real trial")
    worker._diagnostic_terminal(claimed, "execution_timeout")
    first = load_latest_strategy_lab_checkpoint(full, run_id=payload["run_id"])
    worker.finalize_diagnostic("remote")
    second = load_latest_strategy_lab_checkpoint(full, run_id=payload["run_id"])
    assert second["execution_error"] == first["execution_error"]
    assert second["stage"] == second["execution_error"]["last_execution_stage"] == "optimization"
    assert second["terminal_reason"] == "execution_timeout" and second["status"] == "failed"
    assert second["progress"] == .4835 and second["optimizer_state"] == state
    assert second["job"] == payload and second["job"]["strategy_ids"] == [SID]
    assert second["diagnostic_deadline_at"] == payload["diagnostic_deadline_at"]
    row = queue["research_queue"][0]
    assert row["execution_error"] == second["execution_error"] and row["progress"] == .4835
    assert row["attempts"] == 1 and row["next_attempt_at"] is None
    assert "result" not in second
    executor = Mock()
    result = jobs.execute_strategy_lab_job_once(run_id=payload["run_id"], job=payload,
        checkpoint_store=full, market=object(), main_store=object(), executor=executor)
    assert result["status"] == "failed"
    executor.assert_not_called()


def test_normal_resume_keeps_fold_and_optimizer_payload_after_many_heartbeats(tmp_path, monkeypatch):
    full = StrategyStore(tmp_path / "checkpoint")
    payload = {"ticker": "SPY", "candidates": [{"id": SID}], "search_depth": 12}
    state = {"fingerprint": "same-data-and-config", "completed_strategy_ids": [SID],
             "rankings": [{"source_strategy_id": SID, "completed_folds": [{"oos": "untouched"}]}]}
    save_strategy_lab_checkpoint(full, run_id="resume", ticker="SPY", status="running",
        job=payload, optimizer_state=state, attempt=1, progress=.4, stage="optimization")
    writes = []; original = StrategyStore.save
    def observed(store, data):
        writes.append(store is full)
        return original(store, data)
    monkeypatch.setattr(StrategyStore, "save", observed)
    clock = [100.0]
    monkeypatch.setattr(jobs, "time", type("Clock", (), {"monotonic": staticmethod(lambda: clock[0])}))
    def execute(job, **callbacks):
        assert job == payload and callbacks["optimizer_resume_state"] == state
        for index in range(10):
            clock[0] += 11
            callbacks["progress"](.4 + index / 100, "optimization", "trial")
        current = load_latest_strategy_lab_checkpoint(full, run_id="resume")
        assert current["status"] == "running" and "result" not in current
        assert current["optimizer_state"] == state and current["job"] == payload
        raise RuntimeError("controlled interruption")
    outcome = jobs.execute_strategy_lab_job_once(run_id="resume", job=payload, checkpoint_store=full,
        main_store=object(), market=object(), executor=execute)
    assert outcome["status"] == "failed"
    assert writes.count(True) == 2 and writes.count(False) == 10
    terminal = load_latest_strategy_lab_checkpoint(full, run_id="resume")
    assert terminal["attempt"] == 2 and terminal["optimizer_state"] == state


def test_instrumentation_records_split_costs_and_does_not_log_payloads(tmp_path):
    full = StrategyStore(tmp_path / "checkpoint")
    rows = []
    metric = instrument_checkpoint_store(full, "timing")
    metric.emit = rows.append
    class RemoteProbe:
        _checkpoint_telemetry = metric
        @timed_checkpoint_io("remote_seconds")
        def read(self):
            time.sleep(.002)
    time.sleep(.002)
    with metric.operation("probe", "optimization"):
        full._write_local({"sensitive_fixture": "not-for-logs"})
        RemoteProbe().read()
    row = rows[-1]
    assert row["serialization_seconds"] > 0 and row["local_write_seconds"] > 0
    assert row["remote_seconds"] >= .002 and row["non_checkpoint_wall_seconds"] > 0
    assert row["checkpoint_bytes"] == full.path.stat().st_size
    assert "not-for-logs" not in json.dumps(rows) and "sensitive_fixture" not in json.dumps(rows)
    assert row["cumulative"]["persistence_seconds"] == row["persistence_seconds"]


def test_failed_telemetry_sink_cannot_break_durable_storage(tmp_path):
    full = StrategyStore(tmp_path / "checkpoint")
    def broken(_):
        raise OSError("log sink unavailable")
    instrument_checkpoint_store(full, "safe").emit = broken
    save_strategy_lab_checkpoint(full, run_id="safe", ticker="SPY", status="running", job={"strategy_ids": [SID]})
    assert full.load()["validation_runs"][0]["job"]["strategy_ids"] == [SID]
