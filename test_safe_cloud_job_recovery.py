from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import cloud_profit_first_worker as profit
import cloud_strategy_lab_worker as lab
import distributed_stock_finder as distributed
from youtube_strategy_engine import AppError, StrategyStore


class Cloud:
    repository = "owner/private"
    path = "library.json"

    def __init__(self):
        self.library = StrategyStore.blank()
        self.library["updated_at"] = "2026-09-03T10:00:00Z"
        self.reads = 0

    def read_library(self):
        self.reads += 1
        return {"library": deepcopy(self.library), "sha": "a" * 40}


def test_cold_restore_is_only_a_one_call_optimization(tmp_path):
    cloud = Cloud()
    store = StrategyStore(tmp_path, cloud_backup=cloud)
    store.load_latest()
    assert cloud.reads == 1
    assert store.restored_on_startup
    cloud.library["updated_at"] = "2026-09-03T11:00:00Z"
    cloud.library["research_queue"] = [{"id": "finder", "status": "running"}]
    assert store.load_latest()["research_queue"] == cloud.library["research_queue"]
    assert cloud.reads == 2
    cloud.library["updated_at"] = "2026-09-03T12:00:00Z"
    cloud.library["research_queue"][0]["stage"] = "walk_forward"
    assert store.load_latest()["research_queue"][0]["stage"] == "walk_forward"
    assert cloud.reads == 3


def test_restore_flag_does_not_hide_unsynced_divergence(tmp_path):
    cloud = Cloud()
    store = StrategyStore(tmp_path, cloud_backup=cloud)
    local = store.load_latest()
    local["updated_at"] = "2026-09-03T11:00:00Z"
    local["strategies"] = [{"id": "local"}]
    store._write_local(local)
    cloud.library["updated_at"] = "2026-09-03T12:00:00Z"
    with pytest.raises(AppError, match="Both the local"):
        store.load_latest()
    assert store.load()["strategies"] == [{"id": "local"}]


def test_cloud_exposure_retry_reapplies_guard_and_preserves_new_records(tmp_path):
    snapshots = [
        {"revision": 1, "research_queue": [{"id": "finder", "stage": "search"}]},
        {"revision": 2, "research_queue": [{"id": "finder", "stage": "walk_forward"}]},
    ]
    writes = []

    def mutate(callback):
        for snapshot in snapshots:
            writes.append(callback(deepcopy(snapshot)))
        return writes[-1]

    def guard(library, wrapper):
        wrapper["holdout_reuse_audit"] = {"blocked": library["revision"] == 2}
        return wrapper

    def exposure(library, wrapper, **kwargs):
        library["exposure"] = deepcopy(wrapper)
        return library

    original = {"optimization": {"id": "result"}}
    with patch.object(lab, "mutate_remote_library", side_effect=mutate), \
         patch.object(lab, "apply_holdout_reuse_guard", side_effect=guard) as guarded, \
         patch.object(lab, "record_holdout_exposure", side_effect=exposure):
        result = lab.CloudStrategyLabStore(tmp_path).commit_holdout_exposure(original, generated_at="now")
    assert guarded.call_count == 2
    assert result["holdout_reuse_audit"]["blocked"] is True
    assert writes[-1]["research_queue"] == snapshots[-1]["research_queue"]
    assert "holdout_reuse_audit" not in original


def test_real_cas_retry_reads_concurrent_change_before_committing_exposure(tmp_path):
    class RacingCloud(Cloud):
        writes = 0

        def save_library(self, data, *, previous_updated_at):
            self.writes += 1
            if self.writes == 1:
                self.library["updated_at"] = "2026-09-03T13:00:00Z"
                self.library["research_queue"] = [{"id": "finder", "stage": "walk_forward"}]
                self.library["concurrent_exposure"] = True
                raise AppError("The private GitHub backup contains a different or newer saved library.")
            assert previous_updated_at == self.library["updated_at"]
            self.library = deepcopy(data)

    cloud = RacingCloud()
    def guard(data, wrapper):
        wrapper["blocked"] = bool(data.get("concurrent_exposure"))
        return wrapper
    def exposure(data, wrapper, **kwargs):
        data["own_exposure"] = deepcopy(wrapper)
        return data
    with patch.object(distributed, "build_cloud_backup", return_value=cloud), \
         patch.object(distributed.time, "sleep"), \
         patch.object(lab, "apply_holdout_reuse_guard", side_effect=guard), \
         patch.object(lab, "record_holdout_exposure", side_effect=exposure):
        result = lab.CloudStrategyLabStore(tmp_path).commit_holdout_exposure({}, generated_at="now")
    assert cloud.writes == 2
    assert result["blocked"] is True
    assert cloud.library["research_queue"] == [{"id": "finder", "stage": "walk_forward"}]
    assert cloud.library["concurrent_exposure"] is True


def queue_item(**overrides):
    return {"id": "exact", "type": "autonomous_validation", "source": "trading_intelligence_desktop",
            "status": "queued", "attempts": 0, "max_attempts": 3,
            "payload": {"strategy_ids": ["strategy-one"]}, **overrides}


def run_profit(item, execute=None):
    unrelated = {"id": "finder", "type": "stock_finder", "status": "running",
                 "updated_at": "2020-01-01T00:00:00Z", "attempts": 1}
    library = {"research_queue": [deepcopy(unrelated), deepcopy(item)]}
    writes = []

    def mutate(callback):
        updated = callback(deepcopy(library))
        if updated is not None:
            library.update(updated)
            writes.append(deepcopy(library))
        return deepcopy(library)

    executor = execute or Mock(return_value="validation-result")
    with patch.object(profit, "mutate_remote_library", side_effect=mutate), \
         patch.object(profit, "build_store", return_value=object()), \
         patch.object(profit, "execute_job", executor):
        result = profit.run_once("exact")
    assert library["research_queue"][0] == unrelated
    return result, executor, writes


def test_exact_profit_first_runs_one_existing_item_and_no_stale_finder_recovery():
    result, executor, writes = run_profit(queue_item())
    assert result["status"] == "complete"
    assert len(writes) == 1
    executor.assert_called_once()
    assert executor.call_args.args[1] is None
    assert executor.call_args.args[2]["id"] == "exact"
    assert writes[0]["research_queue"][1]["attempts"] == 1


@pytest.mark.parametrize("status", ["running", "complete", "failed", "cancelled"])
def test_exact_profit_first_never_restarts_active_or_terminal_jobs(status):
    result, executor, writes = run_profit(queue_item(status=status))
    assert result["status"] == "idle"
    executor.assert_not_called()
    assert writes == []


@pytest.mark.parametrize("overrides", [
    {"type": "stock_finder"}, {"source": "other"}, {"payload": {}},
    {"attempts": 3}, {"id": "another"}, {"payload": {"strategy_ids": ["", " "]}},
    {"payload": {"strategy_ids": {"wrong": "type"}}},
])
def test_exact_profit_first_rejects_wrong_or_unbounded_targets(overrides):
    with pytest.raises(AppError):
        run_profit(queue_item(**overrides))


def test_exact_profit_first_respects_retry_delay():
    result, executor, writes = run_profit(queue_item(status="retry", next_attempt_at="2999-01-01T00:00:00Z"))
    assert result["status"] == "idle"
    executor.assert_not_called()
    assert not writes


def test_exact_profit_first_missing_id_does_not_build_worker():
    with patch.object(profit, "mutate_remote_library") as mutate:
        with pytest.raises(AppError, match="exact existing"):
            profit.run_once("")
        mutate.assert_not_called()


@pytest.mark.parametrize("saved_result", [None, {}, {"different": True}])
def test_cloud_lab_refuses_completion_without_verified_saved_result(saved_result):
    checkpoint = SimpleNamespace(cloud_backup=SimpleNamespace(read_library=Mock(side_effect=[
        {"library": {}},
        {"library": {"validation_runs": [{"id": "run", "ticker": "", "record_type": "strategy_lab_checkpoint", "status": "complete", "result": saved_result}]}},
    ])))
    with patch.object(lab, "_claim", return_value={"id": "remote", "payload": {"run_id": "run"}}), \
         patch.object(lab, "build_main_store", return_value=Mock()), \
         patch.object(lab, "build_checkpoint_store", return_value=checkpoint), \
         patch.object(lab, "_job_spec", return_value={}), \
         patch.object(lab, "build_market", return_value=Mock()), \
         patch.object(lab, "execute_strategy_lab_job_once", return_value={"status": "complete", "result": {"real": True}}), \
         patch.object(lab, "_fail_queue", return_value="failed"), \
         patch.object(lab, "_complete_queue") as complete:
        result = lab.run_once("remote")
    assert result["status"] == "failed"
    assert "not verified" in result["message"] or "no verifiable result" in result["message"]
    complete.assert_not_called()


def test_cloud_lab_failed_terminal_outcome_is_nonzero():
    with patch.object(lab, "run_once", return_value={"status": "failed"}):
        assert lab.main(["--job-id", "remote"]) == 1


def test_cloud_lab_completed_checkpoint_finishes_queue_without_computation():
    with patch.object(lab, "_claim", return_value={"id": "remote", "payload": {"run_id": "run", "ticker": "ABC"}}), \
         patch.object(lab, "build_main_store"), patch.object(lab, "build_checkpoint_store"), \
         patch.object(lab, "_saved_cloud_result", return_value={"report": {"winner": {"name": "result"}}}), \
         patch.object(lab, "build_market") as market, \
         patch.object(lab, "execute_strategy_lab_job_once") as execute, \
         patch.object(lab, "_complete_queue") as complete:
        result = lab.run_once("remote")
    assert result["recovered_from_checkpoint"] is True
    complete.assert_called_once()
    execute.assert_not_called()
    market.assert_not_called()


def test_failed_checkpoint_allows_new_attempt_but_not_stale_running(tmp_path):
    from strategy_lab_persistence import save_strategy_lab_checkpoint, load_latest_strategy_lab_checkpoint
    store = StrategyStore(tmp_path)
    def save(status, attempt, **kwargs):
        return save_strategy_lab_checkpoint(store, run_id="run", ticker="ABC", status=status, attempt=attempt, **kwargs)
    save("running", 1, job={"ticker": "ABC"}, optimizer_state={"completed_strategy_ids": ["one"]})
    save("failed", 1)
    failed = load_latest_strategy_lab_checkpoint(store)
    assert failed["optimizer_state"]["completed_strategy_ids"] == ["one"]
    save("running", 1)
    assert load_latest_strategy_lab_checkpoint(store)["status"] == "failed"
    save("running", 2, progress=0.01)
    assert load_latest_strategy_lab_checkpoint(store)["status"] == "running"
    save("complete", 2, result={"saved": True})
    save("failed", 3)
    assert load_latest_strategy_lab_checkpoint(store)["result"] == {"saved": True}


def test_exact_strategy_lab_claim_preserves_unrelated_stale_finder():
    finder = {"id": "finder", "type": "stock_finder", "status": "running", "updated_at": "2020-01-01T00:00:00Z"}
    library = {"research_queue": [finder, queue_item(type="strategy_lab")]}
    written = []
    with patch.object(lab, "mutate_remote_library", side_effect=lambda fn: written.append(fn(deepcopy(library)))):
        job = lab._claim("exact")
    assert job["id"] == "exact"
    assert written[0]["research_queue"][0] == finder


def test_exact_profit_failure_only_marks_owned_job():
    error = AppError("validation failed")
    with pytest.raises(AppError, match="validation failed"):
        run_profit(queue_item(), execute=Mock(side_effect=error))


def test_workflow_exact_job_input_is_data_not_shell_source_and_broad_worker_is_conditional():
    text = (Path(__file__).parent / ".github/workflows/continuous-trading-research.yml").read_text()
    assert 'EXACT_PROFIT_FIRST_JOB_ID: ${{ inputs.job_id }}' in text
    assert 'if: ${{ !inputs.job_id }}\n        run: python cloud_research_worker.py' in text
    assert 'if: ${{ inputs.job_id != \'\' }}' in text
    assert 'run: python cloud_profit_first_worker.py --job-id "$EXACT_PROFIT_FIRST_JOB_ID"' in text
    assert '--job-id "${{ inputs.job_id }}"' not in text


def test_verified_cloud_lab_result_completes_queue_after_normal_execution():
    result = {"report": {"winner": {"name": "result"}}}
    with patch.object(lab, "_claim", return_value={"id": "remote", "payload": {"run_id": "run", "ticker": "ABC"}}), \
         patch.object(lab, "build_main_store"), patch.object(lab, "build_checkpoint_store"), \
         patch.object(lab, "_saved_cloud_result", side_effect=[{}, result]), \
         patch.object(lab, "_job_spec", return_value={}), patch.object(lab, "build_market"), \
         patch.object(lab, "execute_strategy_lab_job_once", return_value={"status": "complete", "result": result}), \
         patch.object(lab, "_complete_queue") as complete:
        outcome = lab.run_once("remote")
    assert outcome["status"] == "complete"
    complete.assert_called_once()


def test_checkpoint_readback_refuses_another_ticker():
    cloud = SimpleNamespace(read_library=lambda: {"library": {"validation_runs": [{
        "id": "run", "ticker": "WRONG", "record_type": "strategy_lab_checkpoint", "status": "complete", "result": {"saved": True}
    }]}})
    with pytest.raises(AppError, match="ticker"):
        lab._saved_cloud_result(SimpleNamespace(cloud_backup=cloud), "run", "ABC")


@pytest.mark.parametrize("status", ["cancelled", "failed", "complete"])
def test_queue_finalization_does_not_overwrite_a_terminal_state(status):
    library = {"research_queue": [{"id": "remote", "type": "strategy_lab", "status": status, "payload": {"run_id": "run"}}]}
    with patch.object(lab, "mutate_remote_library", side_effect=lambda fn: fn(deepcopy(library))):
        with pytest.raises(AppError, match="no longer active"):
            lab._complete_queue("remote", run_id="run", result_summary={"saved": True})
