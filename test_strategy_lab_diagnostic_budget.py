"""Offline bounds tests. No test dispatches to GitHub or calls a data provider."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
from pathlib import Path
import re
import time
from unittest.mock import Mock

import pytest

import cloud_strategy_lab_worker as worker
from hybrid_runtime.cloud_bridge import CloudBridgeWorker, DesktopCloudSettings
from hybrid_runtime.cloud_link_store import CloudLinkStore
from hybrid_runtime.contracts import JobRequest
from hybrid_runtime.diagnostic_budget import (
    diagnostic_budget, remaining_diagnostic_seconds, stamp_diagnostic_deadline,
    strategy_lab_dispatch_inputs, workflow_execution_minutes,
)
from hybrid_runtime.github_library import GitHubLibraryConfig
from hybrid_runtime.service import HybridService
from hybrid_runtime.storage import HybridStore
from hybrid_runtime.strategy_lab_bridge import (
    normalized_strategy_lab_payload, overlay_strategy_lab_checkpoint,
    prepare_strategy_lab_publication,
)
from strategy_lab_jobs import execute_strategy_lab_job_once, MAX_AUTOMATIC_ATTEMPTS
from strategy_lab_persistence import load_latest_strategy_lab_checkpoint, save_strategy_lab_checkpoint
from test_strategy_lab_cloud_bridge import FakeGitHub
from youtube_strategy_engine import StrategyStore, AppError

SID = "webresearch-c238a2839213bb33d9"
TEST_STRATEGY = {"id": SID, "name": "Stored test definition", "machine_rules": {"avwap_pivot_confirm_bars": 5}}
BOUNDS = {"diagnostic_mode": True, "diagnostic_max_attempts": 1, "diagnostic_timeout_minutes": 20}


def request(diagnostic=True, *, pinned=False):
    from hybrid_runtime.strategy_lab_bridge import strategy_revision
    return JobRequest("strategy.strategy_lab", {
        "run_id": "diagnostic-offline-test", "ticker": "SPY", "strategy_ids": [SID],
        **({"strategy_revisions": {SID: strategy_revision(TEST_STRATEGY)}} if pinned else {}),
        "compared_all": False, "search_depth": 12, "history_days": 30,
        "run_walk_forward": True, "training_fraction": 0.6, "validation_fraction": 0.2,
        **(BOUNDS if diagnostic else {}),
    }, idempotency_key="diagnostic-offline-test")


def remote(diagnostic=True):
    return {"id": "remote", "type": "strategy_lab", "status": "queued", "attempts": 0,
            "max_attempts": 1 if diagnostic else 3, "priority": 90,
            "created_at": "2026-09-05T00:00:00Z", "payload": deepcopy(request(diagnostic).payload)}


@pytest.fixture
def queue(monkeypatch):
    state = {"research_queue": [remote()]}
    def mutate(fn):
        updated = fn(deepcopy(state))
        if updated is not None:
            state.clear()
            state.update(deepcopy(updated))
    monkeypatch.setattr(worker, "mutate_remote_library", mutate)
    monkeypatch.delenv("DIAGNOSTIC_MODE", raising=False)
    return state


@pytest.mark.parametrize("diagnostic", [False, True])
def test_backend_serialization_normalization_publication_dispatch_and_worker(tmp_path, monkeypatch, diagnostic):
    service = HybridService(HybridStore(tmp_path / "jobs.sqlite3"))
    record, created = service.submit(request(diagnostic, pinned=True).as_dict())
    assert created
    reloaded = service.get(record.id)
    assert reloaded.payload == record.payload
    assert JobRequest.from_mapping(json.loads(json.dumps(request(diagnostic, pinned=True).as_dict()))).payload == record.payload
    normalized = normalized_strategy_lab_payload(reloaded)
    fake = FakeGitHub()
    settings = DesktopCloudSettings(github=GitHubLibraryConfig(
        repository="owner/private", path="main-library.json", action_repository="owner/app"))
    bridge = CloudBridgeWorker(service, CloudLinkStore(tmp_path / "links.sqlite3"), data_dir=tmp_path,
        settings_loader=lambda _: settings, token_loader=lambda _: "test-token", client_factory=fake.client)
    assert bridge.run_once()
    item = json.loads(json.dumps(fake.documents["main-library.json"]["research_queue"][0]))
    assert item["max_attempts"] == (1 if diagnostic else 3)
    assert item["payload"]["ticker"] == "SPY"
    assert item["payload"]["strategy_ids"] == [SID]
    monkeypatch.setattr(worker, "strategy_integrity_report", lambda _: {"status": "faithful"})
    monkeypatch.setattr(worker, "effective_strategy_for_research", deepcopy)
    strategy = deepcopy(TEST_STRATEGY)
    spec = worker._job_spec(item, {"strategies": [strategy, {"id": "not-selected"}]})
    assert spec["candidates"] == [strategy]
    assert spec["ticker"] == "SPY" and spec["compared_all"] is False
    assert spec["run_walk_forward"] is True
    assert (spec["training_fraction"], spec["validation_fraction"]) == (0.6, 0.2)
    assert spec["wf_history_sessions"] == 8 and spec["wf_test_sessions"] == 2 and spec["wf_folds"] == 3
    assert spec["minimum_training_trades"] == 5 and spec["minimum_validation_trades"] == 2
    assert fake.dispatches[0]["inputs"] == strategy_lab_dispatch_inputs(item)
    if diagnostic:
        for key, value in BOUNDS.items():
            assert normalized[key] == item["payload"][key] == spec[key] == value
            assert fake.dispatches[0]["inputs"][key] == ("true" if value is True else str(value))
    else:
        assert diagnostic_budget(spec) == {}
        assert fake.dispatches[0]["inputs"] == {"job_id": item["id"]}
        assert MAX_AUTOMATIC_ATTEMPTS == 3
        assert workflow_execution_minutes(False) == 330


@pytest.mark.parametrize("bad", [
    {"diagnostic_mode": "true"}, {"diagnostic_timeout_minutes": 20},
    {**BOUNDS, "diagnostic_max_attempts": 2}, {**BOUNDS, "diagnostic_max_attempts": True},
    {**BOUNDS, "diagnostic_timeout_minutes": 4}, {**BOUNDS, "diagnostic_timeout_minutes": 31},
    {**BOUNDS, "diagnostic_timeout_minutes": 20.5},
])
def test_invalid_or_ignored_budget_is_rejected(bad):
    with pytest.raises(ValueError):
        diagnostic_budget(bad)


@pytest.mark.parametrize("preferred", ["remote", ""])
def test_claim_persists_deadline_and_cannot_claim_attempt_two(queue, monkeypatch, preferred):
    queue["research_queue"][0]["max_attempts"] = 3
    import hashlib, json
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    request = {k: v for k, v in queue["research_queue"][0]["payload"].items()
               if k != "hybrid_cloud_bridge"}
    claimed = worker._claim(preferred)
    proof = claimed["worker_provenance"]
    assert proof["source_revision"] == "a" * 40 and proof["workflow_run_id"] == "123"
    assert proof["request_sha256"] == hashlib.sha256(json.dumps(request, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert proof["source_sha256"]["cloud_strategy_lab_worker.py"] == hashlib.sha256(Path(worker.__file__).read_bytes()).hexdigest()
    assert queue["research_queue"][0]["worker_provenance"] == proof
    assert claimed["attempts"] == 1 and claimed["max_attempts"] == 1
    assert 1190 < remaining_diagnostic_seconds(claimed["payload"]) <= 1200
    assert queue["research_queue"][0]["payload"] == claimed["payload"]
    queue["research_queue"][0].update(status="retry", max_attempts=3)
    terminal = Mock()
    monkeypatch.setattr(worker, "_diagnostic_terminal", terminal)
    assert worker._claim(preferred) is None
    item = queue["research_queue"][0]
    assert item["attempts"] == 1 and item["status"] == "failed" and item["next_attempt_at"] is None
    terminal.assert_called_once()


def test_normal_failure_keeps_retry_and_diagnostic_failure_does_not(queue):
    item = queue["research_queue"][0]
    item.update(status="running", attempts=1, max_attempts=3)
    assert worker._fail_queue("remote", "provider error") == "failed"
    assert queue["research_queue"][0]["next_attempt_at"] is None
    queue["research_queue"] = [{**remote(False), "status": "running", "attempts": 1}]
    assert worker._fail_queue("remote", "provider error") == "retry"
    assert queue["research_queue"][0]["next_attempt_at"] is not None


def test_checkpoint_retains_budget_on_completion_and_refuses_second_execution(tmp_path):
    store = StrategyStore(tmp_path / "checkpoint")
    job = deepcopy(request().payload)
    stamp_diagnostic_deadline(job)
    save_strategy_lab_checkpoint(store, run_id=job["run_id"], ticker="SPY", status="running", job=job, attempt=1)
    executor = Mock()
    outcome = execute_strategy_lab_job_once(run_id=job["run_id"], job=job,
        checkpoint_store=store, market=object(), main_store=object(), executor=executor)
    assert outcome["status"] == "failed"
    executor.assert_not_called()
    save_strategy_lab_checkpoint(store, run_id=job["run_id"], ticker="SPY", status="complete",
                                 result={"evidence_verdict": {"code": "research_only"}}, attempt=1)
    checkpoint = load_latest_strategy_lab_checkpoint(store, run_id=job["run_id"])
    assert "job" not in checkpoint
    for key, value in BOUNDS.items():
        assert checkpoint[key] == value
    assert checkpoint["diagnostic_deadline_at"] == job["diagnostic_deadline_at"]


def test_real_process_timeout_is_terminal_preserves_progress_and_never_retries(tmp_path, queue, monkeypatch):
    store = StrategyStore(tmp_path / "checkpoint")
    monkeypatch.setattr(worker, "build_checkpoint_store", lambda: store)
    claimed = worker._claim("remote")
    def blocked(job):
        save_strategy_lab_checkpoint(store, run_id=job["payload"]["run_id"], ticker="SPY", status="running",
            job=job["payload"], attempt=1, stage="optimization", progress=0.37,
            optimizer_state={"completed_strategy_ids": [SID]})
        time.sleep(60)
        raise AssertionError("The diagnostic process was not interrupted")
    monkeypatch.setattr(worker, "_run_claimed_job", blocked)
    # Accelerate the deadline only, exercising the real fork/terminate/join path.
    monkeypatch.setattr(worker, "remaining_diagnostic_seconds", lambda _: 0.4)
    before = {p.pid for p in multiprocessing.active_children()}
    started = time.monotonic()
    outcome = worker._run_diagnostic(claimed)
    assert time.monotonic() - started < 5
    assert {p.pid for p in multiprocessing.active_children()} == before
    assert outcome["status"] == "failed" and outcome["failure_kind"] == "execution_timeout"
    item = queue["research_queue"][0]
    assert item["status"] == "failed" and item["attempts"] == 1 and item["next_attempt_at"] is None
    checkpoint = load_latest_strategy_lab_checkpoint(store, run_id=claimed["payload"]["run_id"])
    assert checkpoint["status"] == "failed" and checkpoint["stage"] == "optimization"
    assert checkpoint["terminal_reason"] == "execution_timeout"
    assert checkpoint["execution_error"]["category"] == "infrastructure"
    assert checkpoint["execution_error"]["kind"] == "execution_timeout"
    assert checkpoint["execution_error"]["last_execution_stage"] == "optimization"
    assert checkpoint["execution_error"]["diagnostic_timeout_minutes"] == 20
    assert checkpoint["progress"] == 0.37
    assert checkpoint["optimizer_state"]["completed_strategy_ids"] == [SID]
    assert "result" not in checkpoint and "evidence_verdict" not in checkpoint
    assert "VALIDATION EXECUTION TIMED OUT" in checkpoint["message"]
    assert overlay_strategy_lab_checkpoint({**item, "status": "running"}, checkpoint)["status"] == "failed"
    assert overlay_strategy_lab_checkpoint(item, {**checkpoint, "status": "running"})["status"] == "failed"
    save_strategy_lab_checkpoint(store, run_id=claimed["payload"]["run_id"], ticker="SPY", status="running", attempt=1)
    assert load_latest_strategy_lab_checkpoint(store)["status"] == "failed"
    assert worker._claim("remote") is None


def test_expired_running_job_is_reaped_without_second_attempt(queue, monkeypatch):
    item = queue["research_queue"][0]
    item.update(status="running", attempts=1)
    item["payload"]["diagnostic_deadline_at"] = "2020-01-01T00:00:00+00:00"
    monkeypatch.setattr(worker, "_diagnostic_terminal", Mock())
    assert worker._claim() is None
    assert queue["research_queue"][0]["status"] == "failed"
    assert queue["research_queue"][0]["failure_kind"] == "execution_timeout"


def test_deadline_is_not_reset_by_repeated_stamping():
    payload = dict(BOUNDS)
    stamp_diagnostic_deadline(payload)
    before = deepcopy(payload)
    stamp_diagnostic_deadline(payload)
    assert payload == before


def test_dispatch_mismatch_fails_before_claim(queue, monkeypatch):
    monkeypatch.setenv("DIAGNOSTIC_MODE", "true")
    monkeypatch.setenv("DIAGNOSTIC_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("DIAGNOSTIC_TIMEOUT_MINUTES", "10")
    with pytest.raises(AppError, match="do not match"):
        worker._claim("remote")
    assert queue["research_queue"][0]["attempts"] == 0


def test_workflow_has_executable_timeout_and_exact_finalization():
    path = Path(__file__).parent / ".github/workflows/cloud-strategy-lab.yml"
    workflow = path.read_text()
    # Assert this workflow's scoped contract using only the standard library.
    # GitHub validates YAML syntax; no undeclared YAML parser is needed in CI.
    for name, default in (("diagnostic_mode", "false"), ("diagnostic_max_attempts", '"1"'),
                          ("diagnostic_timeout_minutes", '"20"')):
        block = re.search(rf"(?m)^      {name}:\n((?:        [^\n]*\n)+)", workflow)
        assert block is not None, name
        assert f"        default: {default}\n" in block.group(1)
    assert "    timeout-minutes: ${{ inputs.diagnostic_mode && 45 || 330 }}\n" in workflow

    def step(name):
        block = re.search(rf"(?m)^      - name: {re.escape(name)}\n((?:        [^\n]*(?:\n|$))+)", workflow)
        assert block is not None, name
        return block.group(1)

    budget = step("Resolve execution budget")
    assert "        id: budget\n" in budget
    assert "        run: python -m hybrid_runtime.diagnostic_budget\n" in budget
    validation = step("Run queued Strategy Lab job")
    assert "        id: validation\n" in validation
    assert "        timeout-minutes: ${{ fromJSON(steps.budget.outputs.execution_timeout_minutes) }}\n" in validation
    assert '--job-id "$EXACT_STRATEGY_LAB_JOB_ID"' in validation
    assert "${{ inputs.job_id }}" not in validation
    assert "inputs.finalize_only && '--finalize-diagnostic'" in validation
    finalizer = step("Finalize interrupted diagnostic without retry")
    assert "always() && inputs.diagnostic_mode && !inputs.finalize_only && steps.validation.outcome != 'success'" in finalizer
    assert "--finalize-diagnostic" in finalizer
    assert workflow_execution_minutes(True, 1, 20) == 22
    with pytest.raises(ValueError):
        workflow_execution_minutes(True, 2, 20)


def test_exact_missing_strategy_never_falls_back(monkeypatch):
    monkeypatch.setattr(worker, "strategy_integrity_report", lambda _: {"status": "faithful"})
    with pytest.raises(AppError, match="no longer fully modeled"):
        worker._job_spec(remote(), {"strategies": [{"id": "unrelated"}]})


def test_finalizer_does_not_claim_or_retry(queue, tmp_path, monkeypatch):
    store = StrategyStore(tmp_path / "checkpoint")
    monkeypatch.setattr(worker, "build_checkpoint_store", lambda: store)
    claimed = worker._claim("remote")
    queue["research_queue"][0]["payload"]["diagnostic_deadline_at"] = "2020-01-01T00:00:00+00:00"
    outcome = worker.finalize_diagnostic("remote")
    assert outcome["status"] == "failed" and outcome["failure_kind"] == "execution_timeout"
    assert queue["research_queue"][0]["attempts"] == claimed["attempts"] == 1
    assert worker.finalize_diagnostic("remote")["status"] == "failed"


def test_finalizer_preserves_real_admission_failure_without_another_attempt(queue, tmp_path, monkeypatch):
    store = StrategyStore(tmp_path / 'checkpoint')
    monkeypatch.setattr(worker, 'build_checkpoint_store', lambda: store)
    job = worker._claim('remote')
    message = 'Selected strategy is not fully modeled; execution/admission incomplete; no strategy validation verdict was produced.'
    worker._fail_queue('remote', message)
    # Model metadata left by the old finalizer, which must not replace the real error.
    queue['research_queue'][0]['execution_error'] = {'category': 'infrastructure', 'kind': 'execution_error',
                                                    'message': 'VALIDATION EXECUTION INTERRUPTED'}
    monkeypatch.setattr(worker, 'execute_strategy_lab_job_once', Mock(side_effect=AssertionError('must not execute')))
    for _ in range(2):
        outcome = worker.finalize_diagnostic('remote')
        item = queue['research_queue'][0]
        assert outcome['preserved_terminal_failure'] and outcome['new_execution_attempts'] == 0
        assert item['status'] == 'failed' and item['attempts'] == 1
        assert item['last_error'] == item['execution_error']['message'] == message
        assert item['execution_error']['category'] == 'execution'
        assert not item.get('result') and not item.get('next_attempt_at')
        cp = load_latest_strategy_lab_checkpoint(store, run_id=job['payload']['run_id'])
        assert cp['status'] == 'failed' and cp['attempt'] == 1
        assert cp['execution_error']['message'] == message and 'result' not in cp
