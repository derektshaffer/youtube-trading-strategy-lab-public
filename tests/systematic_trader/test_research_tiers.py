from copy import deepcopy
import json
import pytest
from systematic_trader.events import ContractError, digest, timestamp_ns
from systematic_trader.research_tiers import decide, POLICY


def fixture():
    policy=json.loads(POLICY.read_text())
    request=dict(experiment='no_trade',requested_symbols=['ASPI'],requested_window=[timestamp_ns('2025-01-02T14:30:00Z'),timestamp_ns('2025-01-02T21:00:00Z')])
    # Synthetic policy facts test the rubric, not real dataset certification.
    facts={k:dict(state='verified',source_hashes=[digest('fixture only')]) for k in policy['requirements_all_performance_tiers']}
    facts['explicit_final_export_conditioning']=dict(state='conditional',source_hashes=[digest(policy)])
    facts['source_availability_and_revision_chain']=dict(state='unknown',source_hashes=[digest('fixture only')])
    e=dict(request_hash=digest(request),facts=facts)
    return request,{**e,'evidence_hash':digest(e)}


def rehash(e):return {**{k:v for k,v in e.items() if k!='evidence_hash'},'evidence_hash':digest({k:v for k,v in e.items() if k!='evidence_hash'})}


def test_tier1_conditioning_does_not_grant_high_fidelity_or_orders():
    r,e=fixture();one=decide(r,e,tier=1);two=decide(r,e,tier=2)
    assert one['admitted'] and not one['actual_arrival_required']
    assert not one['production_qualified'] and not one['runner_certificate_issued'] and one['execution_authority']=='none'
    assert not two['admitted'] and 'source_availability_and_revision_chain' in two['missing_requirements']


@pytest.mark.parametrize('requirement',['positive_status','security_identity','action_basis','native_quote_units','complete_quote_trade_scope'])
def test_no_trade_gets_no_exemption_from_unknown_evidence(requirement):
    r,e=fixture();e['facts'][requirement]['state']='unknown'
    assert requirement in decide(r,rehash(e),tier=1)['missing_requirements']


def test_scope_changes_or_unhashed_flags_cannot_reuse_review():
    r,e=fixture();bad=deepcopy(r);bad['requested_symbols']=['NVDA']
    with pytest.raises(ContractError):decide(bad,e,tier=1)
    e['facts']['positive_status']['state']='unknown'
    with pytest.raises(ContractError):decide(r,e,tier=1)


def test_missing_source_or_implicit_timing_cannot_admit():
    r,e=fixture();e['facts']['explicit_final_export_conditioning']['source_hashes']=[]
    assert not decide(r,rehash(e),tier=1)['admitted']


def test_review_never_authorizes_held_out_window():
    r,e=fixture();r['requested_window']=[timestamp_ns('2025-04-01T14:30:00Z'),timestamp_ns('2025-04-01T21:00:00Z')];e['request_hash']=digest(r)
    with pytest.raises(ContractError):decide(r,rehash(e),tier=1)
