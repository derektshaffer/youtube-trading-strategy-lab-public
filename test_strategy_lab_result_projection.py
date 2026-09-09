"""Saved SPY replay: no market, optimizer, cloud dispatch or result regeneration."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hybrid_runtime.strategy_lab_bridge import (
    STRATEGY_LAB_CHECKPOINT_PATH, strategy_lab_result_summary,
    strategy_lab_result_from_checkpoint,
)
from hybrid_runtime.strategy_lab_result_projection import PROJECTION_VERSION
from hybrid_runtime.contracts import JobStatus
from hybrid_runtime.storage import InvalidJobTransition
from test_strategy_lab_checkpoint_repair import bridge_fixture

SID = "webresearch-c238a2839213bb33d9"


@pytest.fixture
def saved():
    return json.loads((Path(__file__).parent / "fixtures/strategy_lab_spy_completed.json").read_text())


def projected(saved):
    return strategy_lab_result_summary(saved["result"], run_id=saved["checkpoint"]["id"],
                                       saved_at=saved["checkpoint"]["saved_at"])


def test_actual_saved_zero_trade_verdict_and_identity(saved):
    p = projected(saved)
    assert p["walk_forward_summary"]["fold_count"] == 3
    assert p["walk_forward_summary"]["recorded_fold_count"] == 3
    assert p["walk_forward_summary"]["active_fold_count"] == 0
    assert p["walk_forward_summary"]["profitable_fold_count"] == 0
    assert p["walk_forward_summary"]["executed"] is True
    assert len(p["walk_forward"]["folds"]) == 3
    assert p["parameter_stability"]["tested"] == 12
    assert p["parameter_stability"]["executed"] is True
    assert p["parameter_stability"]["label"] == p["parameter_stability"]["classification"] == "BRITTLE"
    assert p["parameter_stability"]["active"] == p["parameter_stability"]["positive"] == 0
    assert p["evidence_verdict"]["code"] == "calibration_failed"
    assert p["evidence_verdict"]["label"] == "DISCOVERY / BACKTESTER CALIBRATION FAILED"
    assert p["evidence_verdict"]["candidate_test_verdict"]["code"] == "no_robust_strategy"
    assert p["evidence_verdict"]["paper_ready"] is False
    assert p["strength"]["score"] == 13 and p["strength"]["label"] == "WEAK"
    assert p["strength"]["independently_positive"] is False
    assert p["strength"]["reasons"] == saved["result"]["strength"]["reasons"]
    assert p["ticker"] == "SPY" and p["winner_strategy_id"] == SID
    assert p["run_id"] == saved["checkpoint"]["id"]
    assert p["saved_at"] == saved["checkpoint"]["saved_at"]


@pytest.mark.parametrize("block", ["training_metrics", "validation_metrics", "holdout_metrics", "stress_metrics", "full_metrics"])
def test_zero_and_explicit_null_metrics_are_not_missing(saved, block):
    p = projected(saved)
    assert p[block]["trade_count"] == 0 and p[block]["net_pnl"] == 0.0
    assert "profit_factor" in p[block] and p[block]["profit_factor"] is None
    assert p["evidence_availability"][block] is True
    del saved["result"]["report"]["winner"][block]
    missing = projected(saved)
    assert missing[block] == {} and missing["evidence_availability"][block] is False


def test_integrity_readiness_cost_evidence_and_warnings_survive(saved):
    p = projected(saved)
    assert p["paper_execution_fidelity"]["status"] == "blocked"
    assert p["paper_execution_fidelity"]["paper_runner_persistent_manager"] is False
    assert p["holdout_reuse_audit"]["pristine"] is True
    assert p["holdout_reuse_audit"]["prior_material_exposure_count"] == 0
    assert p["historical_spread_audit"]["status"] == "NO_TRADES"
    assert p["historical_spread_audit"]["quote_feeds"] == []
    assert p["historical_spread_audit"]["provider_error"] is None
    assert p["holdout_execution_sensitivity"]["passes_validation_gate"] is False
    assert p["optimizer_summary"]["adequate_sample"] is False
    assert p["optimizer_summary"]["warnings"]
    assert p["source_fidelity_recorded"] is False  # Not fabricated from paper fidelity.
    assert p["backtest_limitations"]
    assert p["validation_settings"]["minimum_training_trades"] == 5


@pytest.mark.parametrize("value", [0, None, False, []])
def test_canonical_fields_override_legacy_even_when_falsy(saved, value):
    saved["result"]["walk_forward"]["summary"].update(fold_count=value, folds=99)
    saved["result"]["parameter_stability"].update(tested=value, tested_neighbor_count=99)
    p = projected(saved)
    assert p["walk_forward_summary"]["fold_count"] == value
    assert p["parameter_stability"]["tested"] == value


def test_narrow_legacy_aliases_only_fill_absent_fields(saved):
    saved["result"]["walk_forward"]["summary"] = {"folds": 3, "profitable_folds": 0, "total_pnl": 0.0}
    saved["result"]["parameter_stability"] = {"tested_neighbor_count": 12, "label": "BRITTLE"}
    p = projected(saved)
    assert p["walk_forward_summary"]["fold_count"] == 3
    assert p["walk_forward_summary"]["profitable_fold_count"] == 0
    assert p["walk_forward_summary"]["external_net_pnl"] == 0.0
    assert p["parameter_stability"]["tested"] == 12


def test_missing_and_present_empty_checks_are_distinct(saved):
    saved["result"].pop("walk_forward")
    missing = projected(saved)
    assert missing["walk_forward_summary"] == {}
    assert missing["evidence_availability"]["walk_forward"] is False
    saved["result"]["walk_forward"] = {"summary": {}, "folds": []}
    empty = projected(saved)
    assert empty["evidence_availability"]["walk_forward"] is True
    assert empty["walk_forward_summary"]["fold_count"] == 0
    assert empty["walk_forward"]["folds"] == []


def test_compact_mapping_excludes_large_internal_payloads_and_does_not_mutate(saved):
    raw = saved["result"]
    raw["report"]["configuration_history"] = ["DO_NOT_EXPOSE" * 100000]
    raw["report"]["winner"]["optimized_rules"] = {"internal": "DO_NOT_EXPOSE" * 100000}
    raw["walk_forward"]["folds"][0]["optimized_rules"] = raw["report"]["winner"]["optimized_rules"]
    before = deepcopy(raw)
    encoded = json.dumps(projected(saved))
    assert len(encoded.encode()) < 40000 and "DO_NOT_EXPOSE" not in encoded
    assert raw == before


def test_archived_completed_result_projects_identically(saved):
    from strategy_lab_persistence import archive_strategy_lab_result
    cp = {**saved["checkpoint"], "result_archive": archive_strategy_lab_result(saved["result"])}
    assert strategy_lab_result_from_checkpoint(cp) == projected(saved)


def completed_fixture(tmp_path, saved):
    service, bridge, fake, job, remote, _ = bridge_fixture(tmp_path)
    cp = {**saved["checkpoint"], "id": job.payload["run_id"], "result": saved["result"], "progress": 1, "stage": "complete"}
    remote.update(status="complete", stage="complete", progress=1, result_ref="strategy-lab-checkpoint:"+cp["id"])
    remote["payload"]["strategy_ids"] = [SID]
    fake.documents[STRATEGY_LAB_CHECKPOINT_PATH] = {"validation_runs": [cp]}
    # Fixture local request uses the same exact canonical SID and SPY.
    assert job.payload["strategy_ids"] == [SID]
    bridge.run_once()
    complete = service.get(job.id)
    assert complete.status == JobStatus.COMPLETE
    # Simulate the previously persisted v0 compact representation, not execution.
    legacy = deepcopy(complete.result)
    legacy.pop("projection_version")
    legacy["walk_forward_summary"] = {}
    legacy["parameter_stability"] = {"status": "complete"}
    with service.store._transaction(immediate=True) as connection:
        connection.execute("UPDATE jobs SET result_json=? WHERE id=?", (json.dumps(legacy), job.id))
    return service, bridge, fake, service.get(job.id), remote


def test_historical_refresh_and_serialized_api_contract_no_execution(tmp_path, saved, monkeypatch):
    service, bridge, fake, before, remote = completed_fixture(tmp_path, saved)
    initial_dispatches = deepcopy(fake.dispatches)
    initial_remote = deepcopy(fake.documents)
    monkeypatch.setattr(bridge, "_publication_for", lambda *a: pytest.fail("historical replay must not publish"))
    bridge.run_once()
    after = service.get(before.id)
    assert after.result["projection_version"] == PROJECTION_VERSION
    assert after.result["job_id"] == before.id
    for key in ("status", "stage", "progress", "attempt", "completed_at", "heartbeat_at", "updated_at", "payload", "error"):
        assert getattr(after, key) == getattr(before, key)
    assert fake.documents == initial_remote and fake.dispatches == initial_dispatches
    # The baseline GET /v1/jobs handler returns this exact serialized JobRecord.
    # Local-only saved-result endpoint/desktop adapter coverage runs separately
    # in the canonical checkout; those unrelated files are not published here.
    api = json.loads(json.dumps(after.as_dict()))
    assert api["id"] == before.id and api["result"]["job_id"] == before.id
    assert api["result"]["walk_forward"]["summary"]["fold_count"] == 3
    assert api["result"]["parameter_stability"]["classification"] == "BRITTLE"
    assert api["result"]["parameter_stability"]["tested"] == 12
    assert api["result"]["paper_execution_fidelity"]["status"] == "blocked"
    bridge.run_once()
    assert fake.dispatches == initial_dispatches


@pytest.mark.parametrize("field,value", [("ticker","QQQ"),("run_id","wrong"),("winner_strategy_id","wrong"),("job_id","wrong"),("remote_job_id","wrong")])
def test_completed_enrichment_refuses_identity_mismatch(tmp_path, saved, field, value):
    service, _, _, before, _ = completed_fixture(tmp_path, saved)
    p = {**before.result, "projection_version": PROJECTION_VERSION, field: value}
    with pytest.raises(InvalidJobTransition):
        service.store.enrich_completed_strategy_lab(before.id, result=p, expected_result=before.result)
    assert service.get(before.id).result == before.result


def test_completed_enrichment_refuses_scoring_changes(tmp_path, saved):
    service, _, _, before, _ = completed_fixture(tmp_path, saved)
    p = {**before.result, "projection_version": PROJECTION_VERSION, "strength":{"score":99,"label":"STRONG"}}
    with pytest.raises(InvalidJobTransition):
        service.store.enrich_completed_strategy_lab(before.id, result=p, expected_result=before.result)


def test_missing_historical_checkpoint_cannot_publish_or_poll_forever(tmp_path, saved):
    service, bridge, fake, before, _ = completed_fixture(tmp_path, saved)
    fake.documents[STRATEGY_LAB_CHECKPOINT_PATH] = {"validation_runs":[]}
    fake.documents["main-library.json"]["research_queue"] = []
    dispatches = deepcopy(fake.dispatches)
    bridge.run_once()
    assert before.id not in [j.id for j in bridge._jobs()]
    assert fake.dispatches == dispatches and service.get(before.id).result == before.result
