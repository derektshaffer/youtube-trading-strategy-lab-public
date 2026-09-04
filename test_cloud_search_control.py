from copy import deepcopy
import pytest

from cloud_search_control import bind_claim, supports_stop, verify_worker_run
from trading_research_orchestrator import (
    claim_next_research_job, claim_research_job_by_id,
    recover_stale_research_jobs, finish_research_job, fail_research_job,
)


def environment(monkeypatch, workflow="cloud-strategy-lab.yml"):
    for key, value in {
        "GITHUB_REPOSITORY": "owner/code", "GITHUB_RUN_ID": "1234",
        "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SHA": "a" * 40,
        "GITHUB_WORKFLOW_REF": f"owner/code/.github/workflows/{workflow}@refs/heads/main",
    }.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("exact", [False, True])
@pytest.mark.parametrize("kind, workflow", [
    ("strategy_lab", "cloud-strategy-lab.yml"),
    ("stock_finder", "distributed-stock-finder.yml"),
])
def test_claim_binds_exact_dedicated_worker(monkeypatch, exact, kind, workflow):
    environment(monkeypatch, workflow)
    data = {"research_queue": [{"id": "search", "type": kind, "status": "queued", "payload": {}}]}
    updated, job = (claim_research_job_by_id(data, "worker", "search") if exact
                    else claim_next_research_job(data, "worker"))
    assert supports_stop(job, "owner/code")
    assert updated["research_queue"][0]["cloud_worker"] == job["cloud_worker"]
    assert job["cloud_worker"]["run_id"] == "1234"


def test_unknown_or_old_worker_never_inherits_stop_capability(monkeypatch):
    environment(monkeypatch, "continuous-trading-research.yml")
    job = {"type": "stock_finder", "cloud_worker": {"run_id": "previous"}}
    bind_claim(job)
    assert "cloud_worker" not in job
    assert not supports_stop(job, "owner/code")


@pytest.mark.parametrize("exact", [True, False])
def test_rerun_of_cancelled_workflow_cannot_claim_other_work(monkeypatch, exact):
    environment(monkeypatch)
    old = {"id": "old", "type": "strategy_lab", "status": "cancelling", "cancel_requested": True}
    bind_claim(old)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "3")
    data = {"research_queue": [old, {"id": "new", "type": "strategy_lab", "status": "queued"}]}
    with pytest.raises(ValueError, match="saved stop request"):
        if exact:
            claim_research_job_by_id(data, "worker", "new")
        else:
            claim_next_research_job(data, "worker")


@pytest.mark.parametrize("status", ["cancelling", "cancelled"])
def test_cancellation_cannot_be_restarted_or_overwritten(status):
    job = {"id": "search", "type": "stock_finder", "status": status, "cancel_requested": True,
           "updated_at": "2020-01-01T00:00:00Z", "started_at": "2020-01-01T00:00:00Z"}
    data = {"research_queue": [job], "saved_results": ["keep"]}
    original = deepcopy(data)
    for operation in [lambda d: finish_research_job(d, "search"),
                      lambda d: fail_research_job(d, "search", "late exception"),
                      lambda d: recover_stale_research_jobs(d)[0],
                      lambda d: claim_next_research_job(d, "worker")[0],
                      lambda d: claim_research_job_by_id(d, "worker", "search")[0]]:
        result = operation(deepcopy(data))
        assert result["research_queue"] == original["research_queue"]
        assert result["saved_results"] == ["keep"]


@pytest.mark.parametrize("key, value", [
    ("id", 999), ("run_attempt", 3), ("head_sha", "b" * 40),
    ("repository", {"full_name": "other/code"}), ("path", ".github/workflows/other.yml"),
])
def test_verification_rejects_different_worker(monkeypatch, key, value):
    environment(monkeypatch)
    job = {"type": "strategy_lab"}
    bind_claim(job)
    run = {"id": 1234, "run_attempt": 2, "head_sha": "a" * 40,
           "repository": {"full_name": "owner/code"}, "path": ".github/workflows/cloud-strategy-lab.yml"}
    assert verify_worker_run(job["cloud_worker"], run) == run
    run[key] = value
    with pytest.raises(ValueError):
        verify_worker_run(job["cloud_worker"], run)
