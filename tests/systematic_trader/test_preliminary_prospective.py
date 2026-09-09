from copy import deepcopy
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import pytest

from systematic_trader.events import ContractError,canonical_json,digest
from systematic_trader.preliminary import PreliminaryResearch,STATE,WARNING,summary as preliminary_summary
from systematic_trader.prospective import ProspectiveRecorder,summary,ResearchView
from systematic_trader.prospective_fixtures import OPEN,CLOSE,ID,complete,entering,coverage,fact,raw
from systematic_trader.evidence_store import EvidenceStore


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    def denied(*a,**kw):raise AssertionError('no network in offline tests')
    monkeypatch.setattr(socket,'create_connection',denied)
    monkeypatch.setattr(socket.socket,'connect',denied)


def request(scenario='positive'):
    return dict(dataset='fixture-'+scenario,symbols=['FIXTURE'],sessions=['2025-01-08'],hypotheses=[dict(id='baseline',
        strategy=dict(id='baseline',name='Synthetic mechanics',direction='long',machine_rules={'min_price':1000 if scenario=='no-trade' else 1,'stop_loss_pct':5,'reward_risk':1},unresolved_rules=[]))])


@pytest.mark.parametrize('scenario,sign',[('positive',1),('losing',-1),('no-trade',0)])
def test_preliminary_results_and_restart_provenance(tmp_path,scenario,sign):
    p=PreliminaryResearch(tmp_path);result=p.run(request(scenario));pnl=result['performance']['research_rankings'][0]['rough_net_pnl']
    assert (pnl>0)-(pnl<0)==sign
    assert result['state']==STATE and result['warning']==WARNING
    assert not any(result[k] for k in ['certified','tier1_satisfied','runner_authorized','paper_eligible','live_eligible','production_eligible','orders_enabled'])
    assert 'out_of_sample' not in canonical_json(result)
    assert p.replay(result['run_id'])['exact_match']
    code='from systematic_trader.preliminary import PreliminaryResearch; import json,sys; print(json.dumps(PreliminaryResearch(sys.argv[1]).replay(sys.argv[2])))'
    out=json.loads(subprocess.check_output([sys.executable,'-c',code,str(tmp_path),result['run_id']],text=True))
    assert out['exact_match'] and out['result_hash']==result['result_hash']
    assert preliminary_summary(tmp_path)['runs']==1


def test_preliminary_cannot_be_certificate_runner_or_consumer(tmp_path):
    from systematic_trader.certification import CertificateAuthority
    from systematic_trader.certified_runner import RunnerVerifier
    from systematic_trader.performance_consumer import consume
    from systematic_trader.performance_results import load_result
    p=PreliminaryResearch(tmp_path/'preliminary');r=p.run(request())
    authority=CertificateAuthority.initialize(tmp_path/'trusted',domain='synthetic-fixture')
    with pytest.raises(ContractError):authority.issue(r)
    with pytest.raises(ContractError):RunnerVerifier(authority.audit.root).run(r,{},p.audit.root,[])
    with pytest.raises(ContractError):consume(r)
    with pytest.raises((ContractError,FileNotFoundError)):load_result(p.audit.root,r['run_id'])
    with pytest.raises(ContractError,match='domain'):EvidenceStore(p.audit.root,'prospective-evidence-v1')


@pytest.mark.parametrize('day',['2025-03-03','2025-04-01','2025-05-01','2025-06-02','2026-09-08'])
def test_preliminary_date_gate_precedes_data_reads(tmp_path,day):
    r=request();r['sessions']=[day]
    p=PreliminaryResearch(tmp_path)
    with pytest.raises(ContractError):p.run(r)
    assert [e['kind'] for e in p.audit.replay()]==['domain','preliminary_attempt','preliminary_failed']


@pytest.mark.parametrize('dataset',['prospective','HOLDOUT_LOCKED','certified','../prospective-evidence','fixture-invalid'])
def test_no_path_or_future_dataset_override(tmp_path,dataset):
    r=request();r['dataset']=dataset
    with pytest.raises(ContractError):PreliminaryResearch(tmp_path).run(r)


def test_comparison_is_predeclared_and_never_promotion(tmp_path):
    r=request();other=deepcopy(r['hypotheses'][0]);other['id']='reject-no-trigger';other['strategy']['machine_rules']['min_price']=1000;r['hypotheses'].append(other)
    p=PreliminaryResearch(tmp_path);result=p.run(r)
    assert len(result['performance']['research_rankings'])==2
    assert all(x['state']==STATE for x in result['performance']['research_rankings'])
    assert [e['kind'] for e in p.audit.replay()]==['domain','preliminary_attempt','preliminary_registered','preliminary_inputs','preliminary_result']


def test_preliminary_review_disagreement_and_ai_agreement_no_authority(tmp_path):
    from ai_review.fixtures import adapters,validator,response
    from ai_review.gate import ReviewGate
    from ai_review.storage import ReviewStorage
    from ai_review.contracts import ReviewError
    from hybrid_runtime.storage import HybridStore
    p=PreliminaryResearch(tmp_path/'p');result=p.run(request());packet=p.review_packet(result['run_id'])
    primary,reviewer=adapters();g=ReviewGate(ReviewStorage(HybridStore(tmp_path/'review.sqlite3')),primary,reviewer,validator)
    g.submit(packet);assert g.run(packet['artifact_id'])['snapshot']['state']=='CLEARED_FOR_NEXT_VALIDATION_STAGE'
    for target in ['paper','live','trusted_runner','certification']:
        with pytest.raises(ReviewError):g.advance(packet['artifact_id'],packet_hash=digest(packet),checkpoint=packet['checkpoint'],target=target)
    def material(req):return response(dict(request_hash=req['request_hash'],classification='material',summary='Preliminary is not certification',objections=[dict(severity='material',primary_claim='Profitability',objection='Admin coverage missing',disputed_evidence=['preliminary-result'],unresolved_question='Status history?',required_resolution='Authoritative evidence')]))
    _,g.reviewer=adapters(review=material,configuration_version='different-offline-review')
    assert g.run(packet['artifact_id'])['snapshot']['state']=='AI_REVIEW_DISAGREEMENT'
    assert result['state']==STATE and not result['runner_authorized']


def test_raw_first_recovery_and_replay(tmp_path,monkeypatch):
    r=ProspectiveRecorder(tmp_path);r.register('s',OPEN,CLOSE,[ID]);original=r._normalize
    def crash(*args):raise KeyboardInterrupt('after durable raw')
    monkeypatch.setattr(r,'_normalize',crash)
    with pytest.raises(KeyboardInterrupt):r.ingest('s','fixture',raw(entering()),received_ns=999)
    assert r.audit.replay()[-1]['kind']=='raw_receipt'
    assert 'pending_raw_normalization' in r.state()['sessions']['s']['errors']
    monkeypatch.setattr(r,'_normalize',original);r.recover();before=r.state()
    assert ProspectiveRecorder(tmp_path).recover()==before
    code='from systematic_trader.prospective import ProspectiveRecorder;import json,sys;print(json.dumps(ProspectiveRecorder(sys.argv[1]).state()))'
    assert json.loads(subprocess.check_output([sys.executable,'-c',code,str(tmp_path)],text=True))==before


def test_raw_hash_duplicates_are_idempotent(tmp_path):
    r=ProspectiveRecorder(tmp_path);r.register('s',OPEN,CLOSE,[ID]);payload=raw(entering())
    r.ingest('s','fixture',payload,received_ns=998);r.ingest('s','fixture',payload,received_ns=999)
    receipts=[x['body'] for x in r.audit.replay() if x['kind']=='raw_receipt']
    assert receipts[0]['sha256']==receipts[1]['sha256']
    s=r.state()['sessions']['s'];assert len(s['facts'])==10 and len(s['interpreted'])==5 and not s['errors']


@pytest.mark.parametrize('missing',['identity','corporate_actions','round_lot','trading_status','luld'])
def test_missing_entering_state_never_complete(tmp_path,missing):
    r=ProspectiveRecorder(tmp_path);r.register('s',OPEN,CLOSE,[ID]);r.ingest('s','fixture',raw([f for f in entering() if f['kind']!=missing]),received_ns=999)
    r.ingest('s','fixture',raw(coverage()),received_ns=2001)
    s=r.state()['sessions']['s'];assert s['completeness'][ID][missing]=='Incomplete' and s['completeness_state']=='Incomplete'


def test_silence_and_late_entering_records_do_not_establish_state(tmp_path):
    r=ProspectiveRecorder(tmp_path);r.register('s',OPEN,CLOSE,[ID]);r.ingest('s','fixture',raw(entering()),received_ns=1001)
    assert r.state()['sessions']['s']['completeness_state']=='Incomplete'
    r.ingest('s','fixture',raw(coverage()),received_ns=2001)
    assert r.state()['sessions']['s']['completeness'][ID]['trading_status']=='Incomplete'


def test_complete_fixture_is_still_collection_only_not_certifiable(tmp_path):
    r=ProspectiveRecorder(tmp_path);s=complete(r);state=r.state()['sessions'][s]
    assert state['completeness_state']=='Complete' and not state['certifiable']
    with pytest.raises(ContractError,match='access_denied'):r.read_for_research(s)


def test_same_ticker_different_identifiers_do_not_join(tmp_path):
    r=ProspectiveRecorder(tmp_path);r.register('s',OPEN,CLOSE,[ID,'fixture:listing-B'])
    r.ingest('s','fixture',raw(entering()),received_ns=999);r.ingest('s','fixture',raw(coverage()),received_ns=2001)
    state=r.state()['sessions']['s'];assert state['completeness'][ID]['identity']=='Complete'
    assert state['completeness']['fixture:listing-B']['identity']=='Incomplete'


def test_round_lot_change_effective_intervals_and_original_knowledge(tmp_path):
    r=ProspectiveRecorder(tmp_path);r.register('s',OPEN,CLOSE,[ID]);r.ingest('s','fixture',raw([fact('round_lot',values={'shares_per_round_lot':100})]),received_ns=999)
    change=[fact('round_lot',values={'shares_per_round_lot':100},end=1500,operation='revise'),fact('round_lot','new-lot',{'shares_per_round_lot':10},start=1500)]
    r.ingest('s','fixture',raw(change),received_ns=1490)
    assert r.fact_at('s',ID,'round_lot',effective_ns=1400,as_of_ns=1400)['values']['shares_per_round_lot']==100
    assert r.fact_at('s',ID,'round_lot',effective_ns=1600,as_of_ns=1600)['values']['shares_per_round_lot']==10
    with pytest.raises(ContractError):r.fact_at('s',ID,'round_lot',effective_ns=3000,as_of_ns=3000)


def test_minimum_order_size_is_not_round_lot(tmp_path):
    r=ProspectiveRecorder(tmp_path);r.register('s',OPEN,CLOSE,[ID]);facts=entering()
    next(f for f in facts if f['kind']=='round_lot')['values']={'minimum_order_size':1}
    r.ingest('s','fixture',raw(facts),received_ns=999);r.ingest('s','fixture',raw(coverage()),received_ns=2001)
    assert r.state()['sessions']['s']['completeness'][ID]['round_lot']=='Incomplete'


def test_cancels_corrections_and_voided_actions_preserve_originals(tmp_path):
    r=ProspectiveRecorder(tmp_path);r.register('s',OPEN,CLOSE,[ID])
    r.ingest('s','fixture',raw([fact('trade','t1',{'price':'100','size':5}),fact('corporate_actions','ca1',{'type':'split','ratio':'2'})]),received_ns=1000)
    r.ingest('s','fixture',raw([fact('trade','t1',{'price':'101','size':6},operation='revise'),fact('corporate_actions','ca1',{'type':'split','ratio':'3'},operation='revise')]),received_ns=1100)
    assert r.fact_at('s',ID,'trade',effective_ns=1200,as_of_ns=1200)['values']['price']=='101'
    r.ingest('s','fixture',raw([fact('trade','t1',operation='void'),fact('corporate_actions','ca1',operation='void')]),received_ns=1200)
    state=r.state()['sessions']['s'];assert len(state['facts'])==6
    assert all(f['voided'] for f in state['interpreted'])
    assert ProspectiveRecorder(tmp_path).state()==r.state()


def test_unknown_amendment_target_and_unknown_source_fail_safe(tmp_path):
    r=ProspectiveRecorder(tmp_path);r.register('s',OPEN,CLOSE,[ID]);r.ingest('s','fixture',raw([fact('trade','missing',operation='revise')]),received_ns=1000)
    assert 'unresolved_correction_or_void_target' in r.state()['sessions']['s']['errors']
    with pytest.raises(ContractError):r.ingest('s','paid-new-provider',b'{}')


@pytest.mark.parametrize('target',['VALIDATION_LOCKED','HOLDOUT_LOCKED'])
def test_locked_data_not_in_summaries_or_preliminary_api(tmp_path,target):
    r=ProspectiveRecorder(tmp_path/'r');s=complete(r);revision=r.state()['sessions'][s]['revision']
    r.transition(s,target,expected_revision=revision,reason='Explicit fixture partition')
    with pytest.raises(ContractError):r.read_for_research(s)
    public=canonical_json(summary(r.audit.root));assert '110' not in public and ID not in public and 'source_row' not in public
    with pytest.raises(ContractError):r.transition(s,'RESEARCH_VISIBLE',expected_revision=r.state()['sessions'][s]['revision'],reason='cannot unlock')
    req=request();req['dataset']=str(r.audit.root)
    with pytest.raises(ContractError):PreliminaryResearch(tmp_path/'p').run(req)


def test_lifecycle_and_new_receipt_invalidate_old_eligibility(tmp_path):
    r=ProspectiveRecorder(tmp_path);s=complete(r);rev=r.state()['sessions'][s]['revision']
    r.transition(s,'RESEARCH_VISIBLE',expected_revision=rev,reason='Explicit fixture development choice');view=r.read_for_research(s)
    r.transition(s,'DEVELOPMENT_ELIGIBLE',expected_revision=view.revision,reason='Explicit fixture decision')
    with pytest.raises(ContractError,match='stale'):r.validate_view(view)
    view=r.read_for_research(s);r.source_gap(s,'test','new uncertainty')
    with pytest.raises(ContractError,match='stale'):r.validate_view(view)
    with pytest.raises(ContractError):r.transition(s,'HOLDOUT_LOCKED',expected_revision=r.state()['sessions'][s]['revision'],reason='cannot unsee research')


def test_source_unavailable_and_clock_regression(tmp_path):
    r=ProspectiveRecorder(tmp_path);r.register('s',OPEN,CLOSE,[ID]);r.ingest('s','fixture',raw(entering()),received_ns=999)
    r.ingest('s','fixture',raw(entering()),received_ns=998)
    assert 'receipt_clock_regression' in r.state()['sessions']['s']['errors']
    r.source_gap('s','alpaca_sip','entitlement blocked')
    assert set(r.state()['sessions']['s']['completeness'][ID].values())=={'Source unavailable'}


def test_current_directory_without_identifier_never_joins_asset(tmp_path):
    r=ProspectiveRecorder(tmp_path);r.register('s',OPEN,CLOSE,['alpaca-asset:a'])
    data='Symbol|Security Name|Round Lot Size\nSAME|Company|100\nFile Creation Time: 09072026|\n'
    r.ingest('s','nasdaq_directory',data,received_ns=999)
    state=r.state()['sessions']['s'];assert state['facts'][0]['security_id'] is None
    assert state['completeness_state']=='Incomplete'


def test_prospective_audit_tampering_and_append_only(tmp_path):
    r=ProspectiveRecorder(tmp_path);complete(r);db=sqlite3.connect(tmp_path/'audit.sqlite3')
    with pytest.raises(sqlite3.IntegrityError):db.execute("UPDATE audit SET body='{}'")
    db.close();p=tmp_path/'head.json';p.write_text('{}')
    with pytest.raises(ContractError):r.state()


def test_ui_labels_no_prices_or_authority(tmp_path):
    from PySide6.QtWidgets import QApplication
    from desktop.trading_intelligence.evidence_tracks_section import EvidenceTracksSection
    app=QApplication.instance() or QApplication([]);widget=EvidenceTracksSection()
    widget.render({'preliminary':{'runs':2},'prospective':{'state':'Prospective evidence incomplete','complete':0,'incomplete':1}})
    assert WARNING in widget.status.text() and 'incomplete' in widget.status.text()
    assert 'No paper or live orders' in widget.detail.text()
    widget.render({});assert 'not loaded' in widget.status.text()
    widget.close()


def test_evidence_jobs_local_only_and_status_does_not_initialize(tmp_path,monkeypatch):
    from hybrid_runtime.router import RoutingPolicy
    from hybrid_runtime.contracts import JobRequest,ExecutionTarget
    from hybrid_runtime.engine_adapter import evidence_status_handler,preliminary_research_handler
    monkeypatch.setenv('TRADING_INTELLIGENCE_DESKTOP_DATA_DIR',str(tmp_path))
    for kind in ['research.preliminary','research.evidence_status']:
        assert RoutingPolicy().decide(JobRequest(kind,{},requested_target='auto')).target==ExecutionTarget.LOCAL
        with pytest.raises(ValueError):RoutingPolicy().decide(JobRequest(kind,{},requested_target='cloud'))
    status=evidence_status_handler({},lambda *a:None,lambda:False)
    assert status['preliminary']['runs']==0 and not list(tmp_path.iterdir())
    result=preliminary_research_handler(request(),lambda *a:None,lambda:False)
    assert result['state']==STATE
    assert evidence_status_handler({},lambda *a:None,lambda:False)['preliminary']['runs']==1


def test_current_collector_uses_only_existing_read_endpoints(tmp_path,monkeypatch):
    import systematic_trader.prospective_sources as module
    import systematic_trader.__main__ as main
    import systematic_trader.data_acquisition as acquisition
    calls=[]
    monkeypatch.setattr(module,'public_get',lambda url:b'Symbol|Security Name|Round Lot Size\nAAPL|Apple|100\n')
    monkeypatch.setattr(main,'get_credentials',lambda source:('fixture-key','fixture-secret'))
    class Access:
        def __init__(self,*a):pass
        def get(self,kind,params):
            calls.append(kind)
            if kind=='assets':return json.dumps([{'symbol':s,'id':s+'-id','class':'us_equity','exchange':'NASDAQ'} for s in module.SYMBOLS]).encode()
            if kind=='calendar':
                from datetime import datetime,timedelta
                from zoneinfo import ZoneInfo
                day=(datetime.now(ZoneInfo('America/New_York')).date()+timedelta(days=1)).isoformat()
                return json.dumps([{'date':day,'open':'09:30','close':'16:00'}]).encode()
            if kind=='actions':return b'{"corporate_actions":{},"next_page_token":null}'
            raise AssertionError('order/network endpoint forbidden')
    monkeypatch.setattr(acquisition,'HistoricalAccess',Access)
    result=module.collect_once(tmp_path)
    assert calls==['assets','calendar','actions'] and result['successful_raw_requests']==5
    assert result['monitored_session'] and not result['certification']
    state=ProspectiveRecorder(tmp_path).state()['sessions'][result['monitored_session']]
    assert state['completeness_state']=='Incomplete' and state['lifecycle']=='COLLECTION_ONLY'
    assert all(s.startswith('alpaca-asset:') for s in state['security_ids'])
    assert summary(tmp_path)['collecting'] is False


@pytest.mark.parametrize('change',['missing_band','invalid_band','gap','void_entering'])
def test_incomplete_transition_evidence_cannot_be_complete(tmp_path,change):
    r=ProspectiveRecorder(tmp_path);r.register('s',OPEN,CLOSE,[ID]);facts=entering()
    if change=='missing_band':next(f for f in facts if f['kind']=='luld')['values'].pop('upper')
    if change=='invalid_band':next(f for f in facts if f['kind']=='luld')['values']['upper']='NaN'
    r.ingest('s','fixture',raw(facts),received_ns=999)
    if change=='gap':r.ingest('s','fixture',raw([fact('gap','missing',{'reason':'missing sequence'})]),received_ns=1500)
    if change=='void_entering':r.ingest('s','fixture',raw([fact('luld',operation='void')]),received_ns=1500)
    r.ingest('s','fixture',raw(coverage()),received_ns=2001)
    assert r.state()['sessions']['s']['completeness_state']=='Incomplete'


def test_ui_certified_fixture_never_looks_historical():
    from PySide6.QtWidgets import QApplication
    from desktop.trading_intelligence.evidence_tracks_section import EvidenceTracksSection
    app=QApplication.instance() or QApplication([]);w=EvidenceTracksSection()
    assert 'Certification blocked' in w.status.text()
    w.render({'certificate':{'dataset_certification':'Certified','experiment':'Experiment frozen','trust_domain':'synthetic-fixture'}})
    assert 'Certified fixture experiment' in w.status.text() and 'not historical evidence' in w.status.text()
    w.render({});assert 'Certification blocked' in w.status.text();w.close()
