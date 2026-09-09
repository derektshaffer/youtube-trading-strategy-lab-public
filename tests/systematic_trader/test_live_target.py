from copy import deepcopy
from pathlib import Path
import base64
import json
import threading
import pytest
from systematic_trader.events import ContractError,timestamp_ns,digest
from systematic_trader.collection_operations import POOL,POLICY,select_pool,freeze_session,phase,preparation_ns,active_sessions
from systematic_trader.collection_service import CollectionService,status
from systematic_trader.source_coverage import matrix
from systematic_trader.target_campaign import TargetCampaign,specification,SOURCE_IDS,concentrations,review_flags

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k: (_ for _ in ()).throw(AssertionError('offline')))

def reg(day='2026-09-08',close='16:00'):
    return dict(open_ns=timestamp_ns(day+'T09:30:00-04:00'),close_ns=timestamp_ns(day+'T'+close+':00-04:00'))

def assets():
    return [dict(symbol=s,security_id='alpaca-asset:'+s,values=dict(status='active',exchange='NASDAQ',**{'class':'us_equity'})) for s in reversed(POOL)]

@pytest.mark.parametrize('time,expected',[('03:59','Awaiting session'),('04:00','Premarket'),('09:30','RTH'),('16:00','Post-close'),('16:30','Seal due')])
def test_calendar_phases(time,expected):assert phase(reg(),timestamp_ns('2026-09-08T'+time+':00-04:00'))==expected

def test_early_close():
    r=reg(close='13:00')
    assert phase(r,timestamp_ns('2026-09-08T13:30:00-04:00'))=='Seal due'
    assert preparation_ns(r)==timestamp_ns('2026-09-08T04:00:00-04:00')

def test_freeze_before_observation_no_late_replacement(tmp_path):
    s=CollectionService(tmp_path);r=reg();a=assets()
    sid=freeze_session(s,'2026-09-08',r,a,r['open_ns']-6*3600*10**9)
    assert list(s.registrations()[sid]['identities'])==list(POOL[:5])
    freeze_session(s,'2026-09-08',r,a[:-1],r['open_ns']+1)
    assert list(s.registrations()[sid]['identities'])==list(POOL[:5])
    assert any(x['kind']=='gap' and 'identity' in x['body']['reason'] for x in s.records())
    assert s.replay()['state']['sessions'][sid]['lifecycle']=='COLLECTION_ONLY'

def test_late_start_records_premarket_gap(tmp_path):
    s=CollectionService(tmp_path);r=reg();sid=freeze_session(s,'2026-09-08',r,assets(),r['open_ns'])
    assert any(g['reason']=='premarket_observation_started_late' for g in s.replay()['checkpoints'][sid]['gaps'])

def test_ambiguous_identity_denied():
    a=assets()
    with pytest.raises(ContractError):select_pool(a+[a[0]])
    with pytest.raises(ContractError):select_pool([])

def test_retired_registration_keeps_raw(tmp_path):
    s=CollectionService(tmp_path);r=reg();s.register('legacy',**r,identities={'SPY':'alpaca-asset:SPY'},provenance={'source':'fixture'})
    s.ingest('legacy','alpaca_assets',b'[]',received_ns=r['open_ns']-1,connection_id='fixture')
    before=s.replay('legacy')['evidence_hash']
    sid=freeze_session(s,'2026-09-08',r,assets(),r['open_ns']-6*3600*10**9)
    assert set(active_sessions(s))=={sid}
    assert s.replay('legacy')['evidence_hash']==before

def test_partial_coverage_never_claims_status():
    m=matrix(iex_subscription=True,iex_market_events=3,iex_event_types={'market.quote':3})
    assert m['fields']['quotes']['classification']=='Live available'
    assert m['fields']['positive trading status']['classification']=='Entitlement blocked'
    assert matrix(iex_subscription=True)['fields']['quotes']['classification']=='Not currently sourced'
    assert m['tradier']=='Credential blocked' and not m['certified']

def test_iex_limits():
    from systematic_trader.iex_collection import IEXConfig
    assert set(IEXConfig(('ASPI',)).subscription())=={'action','trades','quotes'}
    with pytest.raises(ContractError):IEXConfig(('ASPI',),max_frame_bytes=1)


def test_iex_raw_ack_market_revisions_and_ordering(tmp_path):
    from systematic_trader.collection_transport import stream
    s=CollectionService(tmp_path,segment_receipts=2);r=reg();s.register('day',**r,identities={'ASPI':'alpaca-asset:ASPI'},provenance={'source':'fixture'})
    ack=dict(T='subscription',**{k:['ASPI'] for k in ['trades','quotes','corrections','cancelErrors']})
    trade=dict(T='t',S='ASPI',i=123,x='V',p=10,s=100,c=['@'],t='2026-09-08T13:30:01.123456789Z',z='C')
    correction=dict(T='c',S='ASPI',x='V',oi=123,op=10,os=100,oc=['@'],ci=124,cp=10.1,cs=100,cc=['@'],t='2026-09-08T13:30:02Z',z='C')
    cancel=dict(T='x',S='ASPI',i=124,x='V',p=10.1,s=100,a='C',t='2026-09-08T13:30:03Z',z='C')
    payloads=[dict(T='success',msg='connected'),dict(T='success',msg='authenticated'),ack,trade,trade,correction,cancel]
    frames=[json.dumps([m]).encode() for m in payloads];queue=list(frames);stop=threading.Event()
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def send(self,x):assert 'order' not in x
        def recv(self,timeout):
            if len(queue)==1:stop.set()
            return queue.pop(0)
    stream(s,'day','alpaca_iex',('fixture-key','fixture-secret'),['ASPI'],stop,connect=lambda _:Socket(),clock=lambda:r['open_ns'])
    rows=s._replay_segments()[0];raws=[x for x in rows if x['kind']=='raw_receipt']
    assert [base64.b64decode(x['body']['raw_base64']) for x in raws]==frames
    replay=s.replay();assert replay['checkpoints']['day']['duplicates']==1
    assert replay['state']['sessions']['day']['completeness_state']=='Incomplete'
    assert all(v!='Complete' for c in replay['state']['sessions']['day']['completeness'].values() for v in c.values())
    assert not status(tmp_path,r['open_ns'])['orders_enabled']
    assert CollectionService(tmp_path,segment_receipts=2).replay()['evidence_hash']==replay['evidence_hash']
    facts=replay['state']['sessions']['day']['facts'];assert len(facts)==7
    assert any(f['values'].get('event_type')=='market.trade_correction' for f in facts)

@pytest.mark.parametrize('change',['symbols','sessions','hypotheses','bounds','current_fundamentals'])
def test_research_scope_locked(tmp_path,change):
    spec=specification()
    if change=='symbols':spec['request']['symbols'].append('NVDA')
    elif change=='sessions':spec['request']['sessions']=['2025-05-01']
    elif change=='hypotheses':spec['request']['hypotheses']*=2
    elif change=='bounds':spec['bounds']={**spec['bounds'],'reruns':1}
    else:spec['current_market_cap']=100
    with pytest.raises(ContractError):TargetCampaign(tmp_path).register({},spec)

def test_concentration_and_strong_review():
    trades=[dict(symbol='ASPI',entry_time='2025-01-08T10:00:00Z',pnl=90),dict(symbol='MDXH',entry_time='2025-01-10T10:00:00Z',pnl=-10)]
    c=concentrations(trades)
    assert c['largest_trade_share_absolute_pnl']==.9 and c['largest_symbol_share_absolute_pnl']==.9
    assert len(review_flags(100))==5 and len(review_flags(-100))==1
    assert 'Unclassified' in c['market_regime']

def test_target_fixture_bounded_replay_no_promotion(tmp_path,monkeypatch):
    import systematic_trader.preliminary as p
    def data(request):
        cells={}
        for s in request['symbols']:
            for day in request['sessions']:
                cells[s+'|'+day]=[dict(t=day+f'T14:{m}:00Z',o=10,h=10.1,l=9.9,c=10,v=1000) for m in range(30,36)]
        return dict(cells=cells,lineage={},warnings=['Synthetic fixture only'],source_kind='fixture')
    monkeypatch.setattr(p,'_data',data)
    c=TargetCampaign(tmp_path);a=dict(strategies=[dict(strategy=dict(id=i)) for i in SOURCE_IDS])
    c.register(a);r=c.run();assert len(r['candidates'])==6 and r['state']=='PRELIMINARY_RESEARCH_ONLY'
    assert not r['certified'] and not r['orders_enabled'] and c.replay()['exact_match']
    with pytest.raises(ContractError,match='budget'):c.run()
    with pytest.raises(ContractError):c.register(a)


def test_market_closed_and_next_session_not_fake_sealed(tmp_path):
    from systematic_trader.collection_runtime import serve
    now=timestamp_ns('2026-09-07T12:00:00-04:00') # Labor Day; only calendar-provided Tuesday exists.
    def cycle(s,now_ns):freeze_session(s,'2026-09-08',reg(),assets(),now_ns)
    s=serve(tmp_path,once=True,cycle=cycle,clock=lambda:now)
    assert set(s.registrations())=={'2026-09-08|target-v1'}
    assert not any(x['kind']=='seal' for x in s.records())
    assert status(tmp_path,now)['phase']=='Awaiting session'


def test_scheduler_premarket_rth_postclose_and_seal(tmp_path,monkeypatch):
    from systematic_trader.collection_runtime import serve
    import systematic_trader.__main__ as main
    import systematic_trader.tradier as tradier
    import systematic_trader.collection_transport as transport
    values=[timestamp_ns('2026-09-08T'+t+':00-04:00') for t in ['04:00','09:30','16:00','16:30']]
    class Stop:
        n=0
        def is_set(self):return self.n>=len(values)
        def wait(self,x):self.n+=1
    stop=Stop();calls=[]
    monkeypatch.setattr('systematic_trader.collection_runtime.time.monotonic_ns',lambda:values[min(stop.n,3)])
    monkeypatch.setattr(main,'get_credentials',lambda _:('fixture-key','fixture-secret'))
    monkeypatch.setattr(tradier,'credentials',lambda _: (_ for _ in ()).throw(ContractError('missing')))
    monkeypatch.setattr(transport,'reconnecting_stream',lambda *a,**kw:calls.append(a[2]))
    def cycle(s,now_ns):freeze_session(s,'2026-09-08',reg(),assets(),now_ns)
    s=serve(tmp_path,cycle=cycle,stop=stop,clock=lambda:values[min(stop.n,3)])
    assert calls==['alpaca_iex']
    assert [r['body']['phase'] for r in s.records() if r['kind']=='session_phase']==['Premarket','RTH','Post-close','Seal due']
    seals=[r['body'] for r in s.records() if r['kind']=='seal'];assert len(seals)==1 and not seals[0]['certified']
    assert status(tmp_path,values[-1])['seal']=='Sealed'


def test_failed_input_does_not_run_engine_or_retry(tmp_path,monkeypatch):
    import systematic_trader.preliminary as p
    from systematic_trader.target_status import summary
    monkeypatch.setattr(p,'_data',lambda _: (_ for _ in ()).throw(ContractError('preliminary_requested_cell_has_no_observations')))
    monkeypatch.setattr(p,'_calculate',lambda *a: (_ for _ in ()).throw(AssertionError('must not calculate')))
    c=TargetCampaign(tmp_path);c.register(dict(strategies=[dict(strategy=dict(id=i)) for i in SOURCE_IDS]))
    with pytest.raises(ContractError):c.run()
    r=summary(tmp_path);assert r['failed_before_inputs'] and r['engine_evaluations']==0 and len(r['candidates'])==6
    assert all(x['metrics'] is None and x['classification']=='Blocked by data' for x in r['candidates'])
    with pytest.raises(ContractError,match='budget'):c.run()


def test_trade_observation_does_not_claim_quotes_or_corrections():
    m=matrix(iex_subscription=True,iex_market_events=1,iex_event_types={'market.trade':1})['fields']
    assert m['trades']['classification']=='Live available'
    assert m['quotes']['classification']=='Not currently sourced'
    assert m['corrections/cancellations']['classification']=='Not currently sourced'
