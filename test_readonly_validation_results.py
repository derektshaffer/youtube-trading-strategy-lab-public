"""Read-only endpoint identity and side-effect boundary, with saved SPY evidence."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hybrid_runtime.contracts import JobStatus
from hybrid_runtime.github_library import GitHubLibraryConfig
from hybrid_runtime.saved_validation_reader import read_saved_validation
from hybrid_runtime.search_monitor import binding, identity
from hybrid_runtime.strategy_lab_result_projection import project_strategy_lab_result

SID = "webresearch-c238a2839213bb33d9"
JID = "d5e0aeae9c7c4556809fab89370b425f"


@pytest.fixture
def evidence():
    saved = json.loads((Path(__file__).parent / "fixtures/strategy_lab_spy_completed.json").read_text())
    return project_strategy_lab_result(saved["result"], run_id=saved["checkpoint"]["id"], saved_at=saved["checkpoint"]["saved_at"])


@pytest.fixture
def setup(evidence, tmp_path):
    payload = {"ticker":"SPY", "strategy_ids":[SID], "run_id":evidence["run_id"]}
    job = SimpleNamespace(id=JID, job_type="strategy.strategy_lab", payload=payload,
                          status=JobStatus.COMPLETE, stage="complete", progress=1,
                          updated_at="2026-09-06T01:37:50.779484Z", result=evidence,
                          error=None, request_fingerprint="saved-request")
    settings = SimpleNamespace(github=GitHubLibraryConfig(repository="owner/private-data"))
    remote = {"id":"cloud-spy", "type":"strategy_lab", "status":"complete",
              "created_at":"2026-09-06T01:29:43Z", "payload":deepcopy(payload)}
    library = {"research_queue":[remote], "strategies":[{"id":SID,"machine_rules":{"anchor":"swing_high"}}],
               "brokerage_state":{"orders":[]}}
    prohibited = {name:Mock(side_effect=AssertionError("Viewing cannot call "+name)) for name in
                  ("submit","cancel","retry","write","dispatch_workflow","run_once","_publication_for")}
    def get(job_id):
        if job_id != job.id:
            raise ValueError("Unknown job")
        return job
    service = SimpleNamespace(get=get, list=lambda **kw:[job], submit=prohibited["submit"], cancel=prohibited["cancel"])
    link = {**binding(settings), "local_job_id":JID, "remote_job_id":"cloud-spy"}
    client = SimpleNamespace(read=lambda:SimpleNamespace(data=library), write=prohibited["write"],
                             dispatch_workflow=prohibited["dispatch_workflow"])
    worker = SimpleNamespace(service=service, link_store=SimpleNamespace(get=lambda _:link),
                             settings_loader=lambda _:settings, token_loader=lambda _:"fixture",
                             client_factory=lambda *_:client, data_dir=tmp_path,
                             run_once=prohibited["run_once"], _publication_for=prohibited["_publication_for"])
    request = {"key":"local:"+JID,"id":JID,"identity":job.request_fingerprint}
    cloud_request = {"key":"cloud:cloud-spy","id":"cloud-spy","identity":identity(remote),"binding":binding(settings)}
    return SimpleNamespace(worker=worker, job=job, remote=remote, library=library, request=request,
                           cloud_request=cloud_request, prohibited=prohibited, link=link)


@pytest.mark.parametrize("cloud", [False,True])
def test_saved_spy_repeated_read_is_canonical_and_has_no_side_effects(setup, cloud):
    s=setup
    before_job=deepcopy(vars(s.job)); before_library=deepcopy(s.library); before_link=deepcopy(s.link)
    for _ in range(3):
        result=read_saved_validation(s.worker, s.cloud_request if cloud else s.request)
        assert result["job_id"]==JID and result["cloud_job_id"]=="cloud-spy"
        assert result["ticker"]=="SPY" and result["strategy_ids"]==[SID]
        p=result["result"]
        assert p==s.job.result
        assert p["winner_strategy_id"]==SID
        assert p["evidence_verdict"]["code"]=="no_robust_strategy"
        assert p["evidence_verdict"]["label"]=="NO RELIABLE EDGE FOUND"
        assert p["strength"]["score"]==13 and p["strength"]["label"]=="WEAK"
        assert p["parameter_stability"]["label"]=="BRITTLE" and p["parameter_stability"]["tested"]==12
        assert p["walk_forward_summary"]["fold_count"]==3
        assert [p[k]["trade_count"] for k in ("training_metrics","validation_metrics","holdout_metrics")]==[0,0,0]
        assert p["holdout_metrics"]["profit_factor"] is None
        assert p["paper_execution_fidelity"]["status"]=="blocked"
        assert p["holdout_reuse_audit"]["pristine"] is True
        assert len(json.dumps(result))<40000
    assert vars(s.job)==before_job and s.library==before_library and s.link==before_link
    for method in s.prohibited.values():
        method.assert_not_called()


@pytest.mark.parametrize("cloud", [False,True])
def test_timeout_is_terminal_execution_failure_not_strategy_failure(setup, cloud):
    s=setup;s.job.status=JobStatus.FAILED;s.job.stage="failed";s.job.result=None
    s.job.error={"category":"infrastructure","kind":"execution_timeout","message":"VALIDATION EXECUTION TIMED OUT; no strategy validation verdict was produced.","last_progress":.4835}
    s.remote["status"]="failed"
    result=read_saved_validation(s.worker,s.cloud_request if cloud else s.request)
    assert result["status"]=="failed" and result["result"] is None
    assert result["outcome"]=="execution_failure" and result["error"]["kind"]=="execution_timeout"
    assert "evidence_verdict" not in result
    for method in s.prohibited.values():method.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("ticker","QQQ"),("winner_strategy_id","other"),("run_id","other"),
    ("job_id","other"),("remote_job_id","other"),
])
def test_result_identity_mismatch_is_rejected(setup,field,value):
    setup.job.result[field]=value
    with pytest.raises(ValueError,match="identity"):
        read_saved_validation(setup.worker,setup.request)


@pytest.mark.parametrize("change", ["fingerprint","id","kind","nonterminal","missing","dump","large","version"])
def test_fail_closed_instead_of_substituting_or_executing(setup,change):
    s=setup
    if change=="fingerprint":s.request["identity"]="other"
    elif change=="id":s.request["id"]="other"
    elif change=="kind":s.job.job_type="backtest.saved_strategy"
    elif change=="nonterminal":s.job.status=JobStatus.OPTIMIZING
    elif change=="missing":s.job.result={}
    elif change=="dump":s.job.result["optimizer_state"]={"private":"do not expose"}
    elif change=="large":s.job.result["strength"]["reasons"]=["x"*130000]
    elif change=="version":s.job.result["projection_version"]=999
    with pytest.raises(ValueError):read_saved_validation(s.worker,s.request)
    for method in s.prohibited.values():method.assert_not_called()


@pytest.mark.parametrize("change", ["binding","fingerprint","duplicate","terminal","payload"])
def test_cloud_identity_and_terminal_agreement(setup,change):
    s=setup
    if change=="binding":s.cloud_request["binding"]={**s.cloud_request["binding"],"branch":"wrong"}
    elif change=="fingerprint":s.cloud_request["identity"]="wrong"
    elif change=="duplicate":s.library["research_queue"].append(deepcopy(s.remote))
    elif change=="terminal":s.remote["status"]="failed"
    elif change=="payload":
        s.remote["payload"]["ticker"]="QQQ";s.cloud_request["identity"]=identity(s.remote)
    with pytest.raises(ValueError):read_saved_validation(s.worker,s.cloud_request)


def test_legacy_full_result_uses_existing_projection(setup):
    raw=json.loads((Path(__file__).parent/"fixtures/strategy_lab_spy_completed.json").read_text())["result"]
    setup.job.result=raw
    result=read_saved_validation(setup.worker,setup.request)
    assert result["result"]==project_strategy_lab_result(raw,run_id=setup.job.payload["run_id"],saved_at="")
    assert "report" not in result["result"]


def test_authenticated_endpoint_is_read_only(setup):
    pytest.importorskip("fastapi");pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from hybrid_runtime.api import create_app
    s=setup
    with TestClient(create_app(s.worker.service,expected_token="fixture",search_monitor=SimpleNamespace(worker=s.worker))) as client:
        assert client.post("/v1/saved-validations/result",json=s.request).status_code==401
        for _ in range(3):
            response=client.post("/v1/saved-validations/result",json=s.request,headers={"Authorization":"Bearer fixture"})
            assert response.status_code==200 and response.json()["job_id"]==JID
        rejected=client.post("/v1/saved-validations/result",json={**s.request,"identity":"wrong"},headers={"Authorization":"Bearer fixture"})
        assert rejected.status_code==409
    for method in s.prohibited.values():method.assert_not_called()
