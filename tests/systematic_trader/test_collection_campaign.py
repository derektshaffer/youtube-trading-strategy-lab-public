from copy import deepcopy
from pathlib import Path
import base64
import json
import subprocess
import sys
import threading

import pytest

from systematic_trader.events import ContractError,digest
from systematic_trader.collection_service import CollectionService,status,capabilities
from systematic_trader.prospective_fixtures import entering,coverage,fact,raw,ID,OPEN,CLOSE
from systematic_trader.bounded_campaign import BoundedCampaign,specification,BOUNDS,classifications


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k: (_ for _ in ()).throw(AssertionError('offline tests only')))


def registered(tmp_path,limit=2):
    service=CollectionService(tmp_path,segment_receipts=limit)
    service.register('day',OPEN,CLOSE,{'FIXTURE':ID},provenance={'source':'fixture-calendar'})
    return service


def ingest(service,facts,when=999,conn='c'):
    return service.ingest('day','fixture',raw(facts),received_ns=when,connection_id=conn)


def complete(service):
    ingest(service,entering());ingest(service,coverage(),2001)


def test_raw_segments_replay_and_seal(tmp_path):
    s=registered(tmp_path);complete(s)
    seal=s.seal('day',2002)
    assert seal['classification']=='Complete' and not seal['certified'] and not seal['orders_enabled']
    assert seal['reasons']==[] and status(tmp_path,2003)['seal']=='Sealed'
    before=s.replay()['evidence_hash']
    code='from systematic_trader.collection_service import CollectionService; import sys; print(CollectionService(sys.argv[1],segment_receipts=2).replay()["evidence_hash"])'
    assert subprocess.check_output([sys.executable,'-c',code,str(tmp_path)],text=True).strip()==before
    assert s.replay()['state']['sessions']['day']['lifecycle']=='COLLECTION_ONLY'


def test_late_revision_opens_new_segment_invalidates_seal(tmp_path):
    s=registered(tmp_path);complete(s);s.seal('day',2002)
    ingest(s,[fact('corporate_actions',values={'state':'changed'},operation='revise')],2004)
    assert status(tmp_path,2005)['seal']=='Unsealed or revised'
    seal=s.seal('day',2006)
    assert seal['revision']==2 and seal['classification']=='Incomplete'
    state=s.replay()['state']['sessions']['day']
    assert len([f for f in state['facts'] if f['kind']=='corporate_actions'])==2


def test_cancel_trade_across_rotation(tmp_path):
    s=registered(tmp_path,1)
    ingest(s,[fact('trade','trade1',{'price':'10','sequence':1})])
    ingest(s,[fact('trade','trade1',{'price':'10','sequence':2},operation='void')],1001)
    assert s.replay()['state']['sessions']['day']['interpreted'][0]['voided']
    assert len(s.segments('day'))==2


def test_restart_adds_gap_even_clean_stop(tmp_path):
    s=registered(tmp_path);s.start(900);ingest(s,entering());s.heartbeat(1100,1100);s.stop(1200)
    restarted=CollectionService(tmp_path,segment_receipts=2);restarted.start(1300)
    reasons=[g['reason'] for g in restarted.replay()['checkpoints']['day']['gaps']]
    assert 'startup_or_restart_coverage_unknown' in reasons and 'stopped_during_session' in reasons
    assert status(tmp_path,1301)['running'];restarted.stop(1302);assert not status(tmp_path,1303)['running']


def test_unknown_no_bytes_and_unavailable(tmp_path):
    s=registered(tmp_path)
    assert s.seal('day',2001)['classification']=='Unknown'
    s.gap('day','tradier','credentials_unavailable',1000)
    assert s.seal('day',2002)['classification']=='Source unavailable'


def test_duplicate_and_sequence_reorder(tmp_path):
    s=registered(tmp_path,1)
    one=[fact('trade','one',{'price':'10','sequence':1},start=1001)]
    ingest(s,one,1001);ingest(s,one,1002)
    ingest(s,[fact('trade','three',{'price':'10','sequence':3},start=1000)],1003)
    check=s.replay()['checkpoints']['day']
    assert check['duplicates']==1 and check['out_of_order']==1
    assert any(g['reason']=='sequence_gap_or_reorder' for g in check['gaps'])
    assert len([r for r in s._replay_segments()[0] if r['kind']=='raw_receipt'])==3


@pytest.mark.parametrize('category',['identity','corporate_actions','round_lot','trading_status','luld'])
def test_missing_entering_category_incomplete(tmp_path,category):
    s=registered(tmp_path);ingest(s,[f for f in entering() if f['kind']!=category]);ingest(s,coverage(),2001)
    assert s.seal('day',2002)['classification']=='Incomplete'


def test_late_entering_and_silence_not_complete(tmp_path):
    s=registered(tmp_path);ingest(s,entering(),1001);ingest(s,coverage(),2001)
    assert s.seal('day',2002)['classification']=='Incomplete'


def test_clock_discontinuity(tmp_path):
    s=registered(tmp_path);s.heartbeat(10**12,100);s.heartbeat(2*10**12,200)
    # Register a relevant future session to test forward/backward clock jumps.
    s.register('future',10**12,3*10**12,{'FIXTURE':ID},provenance={'source':'fixture'})
    s.heartbeat(10**12,300)
    assert any(r['kind']=='gap' and r['body']['reason']=='clock_discontinuity' for r in s.records())


def test_corrupted_segment_fails_closed(tmp_path):
    s=registered(tmp_path);complete(s);s.seal('day',2002)
    entry=s.segments('day')[0];path=s.root/'segments'/entry['name']/'head.json';path.write_text('{}')
    with pytest.raises(ContractError):s.recover(2100)
    assert status(tmp_path,2100)['state']=='Degraded'


def test_rotation_intent_interruption_recovers(tmp_path):
    s=registered(tmp_path);entry=dict(session_id='day',name='interrupted-intent')
    s.append('segment_open',entry);s.recover(950)
    ingest(s,entering());assert len(s.replay()['state']['sessions']['day']['facts'])==5


def test_raw_commit_normalization_interruption(tmp_path,monkeypatch):
    from systematic_trader.prospective import ProspectiveRecorder
    s=registered(tmp_path);original=ProspectiveRecorder._normalize
    monkeypatch.setattr(ProspectiveRecorder,'_normalize',lambda *a: (_ for _ in ()).throw(RuntimeError('crash')))
    with pytest.raises(RuntimeError):ingest(s,entering())
    monkeypatch.setattr(ProspectiveRecorder,'_normalize',original)
    s.recover(1001)
    assert len(s.replay()['state']['sessions']['day']['facts'])==5
    assert sum(r['kind']=='raw_receipt' for r in s._replay_segments()[0])==1


def test_identity_and_round_lot_intervals_unchanged(tmp_path):
    s=registered(tmp_path)
    with pytest.raises(ContractError):s.register('day',OPEN,CLOSE,{'FIXTURE':'fixture:other'},provenance={'source':'fixture-calendar'})
    ingest(s,[fact('round_lot','old',{'shares_per_round_lot':100},end=1200),fact('round_lot','new',{'shares_per_round_lot':50},start=1200)])
    segment=s.segment(s.segments('day')[0])
    assert segment.fact_at('day',ID,'round_lot',effective_ns=1100,as_of_ns=1000)['values']['shares_per_round_lot']==100
    assert segment.fact_at('day',ID,'round_lot',effective_ns=1300,as_of_ns=1000)['values']['shares_per_round_lot']==50
    with pytest.raises(ContractError):segment.read_for_research('day')


def test_dashboard_no_payloads_and_stale_health(tmp_path):
    s=registered(tmp_path);ingest(s,[fact('trade','private',{'price':'987654.321','sequence':1})]);s.health('day','fixture','Healthy','frame_received',1000)
    text=json.dumps(status(tmp_path,1001))
    assert '987654' not in text and ID not in text and 'raw_base64' not in text
    assert status(tmp_path,1001)['sources']['fixture']['state']=='Degraded'


def test_seal_before_close_denied_and_capability_gaps():
    caps=capabilities()
    assert caps['alpaca_sip']['access'].startswith('BLOCKED')
    assert not caps['tradier']['luld'] and not caps['tradier']['trading_status']
    assert caps['tradier']['sequence'].endswith('unverified')


def fixture_spec(scenario='positive'):
    spec=specification();spec['request']=dict(dataset='fixture-'+scenario,symbols=['FIXTURE'],sessions=['2025-01-08'],hypotheses=[dict(id='fixture',strategy=dict(id='fixture',name='Synthetic',direction='long',machine_rules={'min_price':1000 if scenario=='no-trade' else 1,'stop_loss_pct':5,'reward_risk':1},unresolved_rules=[]))])
    return spec


@pytest.mark.parametrize('scenario,expected',[('positive','Weak'),('losing','Reject'),('no-trade','Weak')])
def test_campaign_bounded_results_and_replay(tmp_path,scenario,expected):
    c=BoundedCampaign(tmp_path);c.register(fixture_spec(scenario));r=c.run()
    assert r['state']=='PRELIMINARY_RESEARCH_ONLY' and r['candidates'][0]['classification']==expected
    assert not r['certified'] and r['candidates'][0]['review_pending'] and not r['candidates'][0]['promoted']
    assert c.replay()['exact_match']
    with pytest.raises(ContractError,match='budget'):c.run()
    with pytest.raises(ContractError,match='registered'):c.register(fixture_spec(scenario))


@pytest.mark.parametrize('change',['bounds','scope','holdout','prospective'])
def test_campaign_rejects_expansion_and_locked_data(tmp_path,change):
    spec=specification()
    if change=='bounds':spec['bounds']={**BOUNDS,'reruns':1}
    if change=='scope':spec['request']['symbols']=['TSLA']
    if change=='holdout':spec['request']['sessions']=['2025-05-01']
    if change=='prospective':spec['request']['dataset']='prospective'
    with pytest.raises(ContractError):BoundedCampaign(tmp_path).register(spec)


def test_campaign_data_failure_claim_cannot_rerun(tmp_path,monkeypatch):
    c=BoundedCampaign(tmp_path);c.register(fixture_spec())
    monkeypatch.setattr(c.research,'run',lambda *a: (_ for _ in ()).throw(ContractError('source_data_missing')))
    with pytest.raises(ContractError,match='source_data_missing'):c.run()
    assert c.audit.replay()[-1]['kind']=='campaign_failed'
    with pytest.raises(ContractError,match='budget'):c.run()


def test_disagreement_durable_blocks_research_transition(tmp_path):
    c=BoundedCampaign(tmp_path);c.register(fixture_spec());r=c.run()
    result=next(x['body'] for x in c.research.audit.replay() if x['kind']=='preliminary_result')
    data=next(x['body']['data'] for x in c.research.audit.replay() if x['kind']=='preliminary_inputs')
    disagreement={'campaign:fixture':{'state':'AI_REVIEW_DISAGREEMENT'}}
    report=classifications(result,data,disagreement)
    assert report['candidates'][0]['classification']=='Blocked by AI disagreement'
    assert not report['candidates'][0]['promoted']
    data['cells']['FIXTURE|2025-01-08'].pop()
    assert classifications(result,data,disagreement)['candidates'][0]['classification']=='Blocked by data quality'


def test_reconnecting_disconnect_budget_and_integrity_failure(tmp_path):
    from systematic_trader.collection_transport import reconnecting_stream
    s=registered(tmp_path);calls=[]
    class Stop:
        def is_set(self):return False
        def wait(self,n):return False
    def disconnected(*a):calls.append(1);raise ConnectionError('offline')
    reconnecting_stream(s,'day','tradier',['fixture-secret'],['FIXTURE'],Stop(),runner=disconnected,clock=lambda:1000)
    assert len(calls)==6
    def corrupt(*a):raise ContractError('corruption')
    with pytest.raises(ContractError):reconnecting_stream(s,'day','tradier',['fixture-secret'],['FIXTURE'],Stop(),runner=corrupt)


def test_runtime_single_cycle_and_stop(tmp_path):
    from systematic_trader.collection_runtime import serve
    def cycle(service,now_ns):
        service.register('day',OPEN,CLOSE,{'FIXTURE':ID},provenance={'source':'fixture-calendar'})
        complete(service)
    s=serve(tmp_path,once=True,cycle=cycle,clock=lambda:2100)
    assert s.records()[-1]['kind']=='stopped' and s.seal('day',2100)['classification']=='Complete'


def test_receipt_clock_regression_across_segments(tmp_path):
    s=registered(tmp_path,1);ingest(s,entering(),999);ingest(s,coverage(),998)
    assert any(g['reason']=='receipt_clock_regression_across_segments' for g in s.replay()['checkpoints']['day']['gaps'])


def test_unknown_correction_target_is_incomplete(tmp_path):
    s=registered(tmp_path);complete(s)
    ingest(s,[fact('trade','absent',{'price':'10'},operation='revise')],2002)
    assert 'unresolved_correction_or_void_target' in s.seal('day',2003)['reasons']


def test_seal_cannot_precede_close(tmp_path):
    s=registered(tmp_path)
    with pytest.raises(ContractError,match='before_close'):s.seal('day',1999)


def test_unfinished_write_head_refused_not_repaired(tmp_path):
    from systematic_trader.validation import ExperimentStore
    s=registered(tmp_path);ingest(s,entering())
    entry=s.segments('day')[0];store=ExperimentStore(s.root/'segments'/entry['name']/'audit.sqlite3')
    store.append('unknown','torn',{});store.close()
    with pytest.raises(ContractError,match='incomplete_or_truncated'):s.recover(1001)


def test_closed_segment_append_attempt_detected(tmp_path):
    s=registered(tmp_path);complete(s);s.seal('day',2002)
    rec=s.segment(s.segments('day')[0]);rec.ingest('day','fixture',raw(coverage()),received_ns=2100)
    with pytest.raises(ContractError,match='closed_segment_changed'):s.replay()


def test_owner_lock_refuses_second_process(tmp_path):
    import fcntl
    registered(tmp_path,128)
    with (tmp_path/'owner.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        code='from systematic_trader.collection_runtime import serve; import sys; serve(sys.argv[1],once=True)'
        result=subprocess.run([sys.executable,'-c',code,str(tmp_path)],capture_output=True,text=True)
    assert result.returncode!=0 and 'collector_already_running' in result.stderr


def test_stop_request_prevents_source_access(tmp_path):
    from systematic_trader.collection_runtime import serve
    tmp_path.mkdir(exist_ok=True);(tmp_path/'stop.request').write_text('{}')
    def forbidden(*a,**kw):raise AssertionError('stop precedes network')
    s=serve(tmp_path,once=True,cycle=forbidden,clock=lambda:2100)
    assert s.records()[-1]['kind']=='stopped'


def test_persisted_reference_cooldown_survives_restart(tmp_path):
    from systematic_trader.collection_runtime import serve
    called=[]
    def cycle(service,now_ns):called.append(now_ns)
    serve(tmp_path,once=True,cycle=cycle,clock=lambda:10**15)
    serve(tmp_path,once=True,cycle=cycle,clock=lambda:10**15+10**9)
    assert len(called)==1


def test_throttling_is_not_infinite_immediate_retry(tmp_path):
    from systematic_trader.collection_transport import reconnecting_stream
    from systematic_trader.service import CaptureFailure
    s=registered(tmp_path);calls=[]
    def rejected(*args):calls.append(1);raise CaptureFailure('429')
    reconnecting_stream(s,'day','tradier',['fixture-secret'],['FIXTURE'],threading.Event(),runner=rejected)
    assert len(calls)==1
    assert s.records()[-1]['body']['state']=='Source unavailable'


def test_tradier_raw_first_and_unverified_sequence(tmp_path):
    from systematic_trader.collection_transport import stream
    s=registered(tmp_path);stop=threading.Event()
    payload={'type':'timesale','symbol':'FIXTURE','date':'1735828200000','seq':41,'cancel':False,'correction':False,
        'flag':'','session':'normal','bid':'9.99','ask':'10.01','last':'10','size':'100','exch':'Q'}
    content=json.dumps(payload).encode();sent=[]
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def send(self,x):sent.append(json.loads(x))
        def recv(self,timeout):stop.set();return content
    stream(s,'day','tradier',['fixture-token-never-persist'],['FIXTURE'],stop,connect=lambda _:Socket(),session_factory=lambda _:'fixture-session-secret',clock=lambda:1001)
    records=s._replay_segments()[0]
    receipt=next(r for r in records if r['kind']=='raw_receipt')
    assert base64.b64decode(receipt['body']['raw_base64'])==content
    assert records.index(receipt)<next(i for i,r in enumerate(records) if r['kind']=='normalized')
    assert not sent[0]['validOnly'] and sent[0]['advancedDetails']
    assert 'fixture-token-never-persist' not in json.dumps(records)
    check=s.replay()['checkpoints']['day']
    assert not any(g['reason']=='sequence_gap_or_reorder' for g in check['gaps'])


def test_transport_reflected_secret_is_redacted_and_stops(tmp_path):
    from systematic_trader.collection_transport import stream
    from systematic_trader.service import CaptureFailure
    s=registered(tmp_path);stop=threading.Event()
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def send(self,x):pass
        def recv(self,timeout):return b'{"error":"fixture-private-token"}'
    with pytest.raises(CaptureFailure,match='redacted'):
        stream(s,'day','tradier',['fixture-private-token'],['FIXTURE'],stop,connect=lambda _:Socket(),session_factory=lambda _:'fixture-stream-secret',clock=lambda:1001)
    records=s._replay_segments()[0];r=next(r for r in records if r['kind']=='raw_receipt')
    assert b'fixture-private-token' not in base64.b64decode(r['body']['raw_base64'])


def test_positive_campaign_cannot_issue_or_run_certificate(tmp_path):
    from systematic_trader.certification import CertificateAuthority
    from systematic_trader.performance_consumer import consume
    c=BoundedCampaign(tmp_path/'c');c.register(fixture_spec());r=c.run()
    with pytest.raises(ContractError):CertificateAuthority.initialize(tmp_path/'a',domain='synthetic-fixture').issue(r)
    with pytest.raises(ContractError):consume(r)


def test_service_ui_has_health_and_no_price_payload():
    from PySide6.QtWidgets import QApplication
    app=QApplication.instance() or QApplication([])
    from desktop.trading_intelligence.evidence_tracks_section import EvidenceTracksSection
    w=EvidenceTracksSection();w.render({'collection_service':{'state':'Collecting','session':'fixture','symbols':['AAPL'],'completeness':'Incomplete','coverage':{'luld':['Incomplete']},'detected_gaps':3,'seal':'Unsealed','sources':{'tradier':{'state':'Degraded','reason':'missing'}}},'campaign':{'candidates':[{'hypothesis_id':'h','classification':'Weak'}]}})
    assert 'Collecting' in w.status.text() and 'LULD: Incomplete' in w.detail.text() and 'h — Weak' in w.detail.text()
    w.render({});assert 'Collecting' not in w.status.text()


def test_process_crash_restart_keeps_raw_and_gap(tmp_path):
    code='''from systematic_trader.collection_service import CollectionService
from systematic_trader.prospective_fixtures import entering,raw,ID
import sys,time
s=CollectionService(sys.argv[1]);n=time.time_ns();s.register('day',n-10**9,n+60*10**9,{'FIXTURE':ID},provenance={'source':'fixture'})
s.start(n);s.ingest('day','fixture',raw(entering()),received_ns=n,connection_id='before-crash')
print('durable',flush=True)
time.sleep(30)
'''
    child=subprocess.Popen([sys.executable,'-c',code,str(tmp_path)],stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='durable'
        child.kill();child.wait(timeout=5)
        s=CollectionService(tmp_path);before=s.replay()['evidence_hash'];s.start(__import__('time').time_ns())
        assert len(s.replay()['state']['sessions']['day']['facts'])==5
        assert any(g['reason']=='startup_or_restart_coverage_unknown' for g in s.replay()['checkpoints']['day']['gaps'])
        assert before!=s.replay()['evidence_hash']
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)


def test_segment_lifecycle_cannot_silently_expose_forward_data(tmp_path):
    s=registered(tmp_path);ingest(s,entering());rec=s.segment(s.segments('day')[0]);session=rec.state()['sessions']['day']
    rec.transition('day','RESEARCH_VISIBLE',expected_revision=session['revision'],reason='Test unsupported segmented promotion')
    with pytest.raises(ContractError,match='governed_session_intake'):s.replay()


def test_current_reference_cycle_uses_existing_raw_intake(tmp_path):
    from systematic_trader.collection_runtime import current_reference_cycle
    from systematic_trader.prospective import ProspectiveRecorder
    from systematic_trader.collection_operations import POOL as SYMBOLS
    def collector(directory,credentials_source):
        assert credentials_source=='lab-dev-keychain'
        p=ProspectiveRecorder(directory);p.register('day',OPEN,CLOSE,['alpaca-asset:'+s for s in SYMBOLS])
        assets=[{'symbol':s,'id':s,'class':'us_equity','exchange':'fixture','status':'active'} for s in SYMBOLS]
        p.ingest('day','alpaca_assets',json.dumps(assets),received_ns=999,provenance={'source':'fixture-assets'})
        p.source_gap('day','status','entering_status_unavailable')
        return dict(monitored_session='day',successful_raw_requests=1)
    s=CollectionService(tmp_path)
    assert current_reference_cycle(s,collector=collector,now_ns=999)=='day|target-v1'
    assert len(s.registrations()['day|target-v1']['identities'])==5
    assert s.seal('day|target-v1',2001)['classification']=='Incomplete'
    assert all(f['kind']=='identity_observation' for f in s.replay()['state']['sessions']['day|target-v1']['facts'])


@pytest.mark.parametrize('spelling',['2025-01-08T14:30:00Z','2025-01-08T14:30:00.000000000Z','2025-01-08T09:30:00-05:00'])
def test_opening_quality_uses_equal_instants(spelling):
    from systematic_trader.campaign_interpretation import equivalent_timestamps
    from systematic_trader.bounded_campaign import quality
    data={'cells':{'FIXTURE|2025-01-08':[{'t':spelling},*[{'t':f'2025-01-08T14:{i}:00.000000000Z'} for i in range(31,35)]]}}
    original=deepcopy(data);assert quality(equivalent_timestamps(data))['problems']==[]
    assert original==data


def test_timestamp_correction_does_not_fill_or_round():
    from systematic_trader.campaign_interpretation import equivalent_timestamps
    from systematic_trader.bounded_campaign import quality
    data={'cells':{'FIXTURE|2025-01-08':[{'t':f'2025-01-08T14:{i}:00.000000001Z'} for i in range(30,35)]}}
    assert quality(equivalent_timestamps(data))['problems']
    assert len(equivalent_timestamps(data)['cells']['FIXTURE|2025-01-08'])==5


def test_append_only_interpretation_preserves_performance(tmp_path):
    from systematic_trader.campaign_interpretation import append_interpretation,replay_interpretation
    c=BoundedCampaign(tmp_path);c.register(fixture_spec());r=c.run()
    interpreted=append_interpretation(c)
    assert interpreted['report']['candidates'][0]['metrics']==r['candidates'][0]['metrics']
    assert replay_interpretation(c)['exact_match']
    assert c.replay()['exact_match']
    with pytest.raises(ContractError,match='budget'):c.run()
