from copy import deepcopy
from datetime import datetime, timedelta
from fractions import Fraction
import json
from pathlib import Path
import sqlite3
import pytest

from systematic_trader import rvol_feasibility as r
from systematic_trader.events import ContractError, timestamp_ns, digest
from systematic_trader.features import MINUTE


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k: pytest.fail('network forbidden'))


def session(day, close='16:00', offset='-05:00'):
    return dict(open=timestamp_ns(day+'T09:30:00'+offset),close=timestamp_ns(day+'T'+close+':00'+offset))


def fixture(day):
    c=session(day)
    return dict(origin='fixture',day=day,open_ns=c['open'],close_ns=c['close'],
                available_ns=c['close'],volumes={i:10 for i in range(15)})


@pytest.fixture
def arithmetic():
    days=['2025-01-02','2025-01-03','2025-01-06','2025-01-07','2025-01-08','2025-01-10',
          '2025-01-13','2025-01-14','2025-01-15','2025-01-16','2025-01-17','2025-01-21','2025-01-22','2025-01-23']
    history=[fixture(d) for d in days]
    current=fixture('2025-01-24');current['volumes'][14]=150
    decision=current['open_ns']+15*MINUTE+1_000_000_000;current['available_ns']=decision
    return current,history,decision


def test_exact_continuous_formulas_and_deterministic_order(arithmetic):
    current,history,decision=arithmetic
    # Prior B13=130, B14=140, B15=150. Current C13=130,C14=140,C15=290.
    expected=dict(L='29/15',S='14/15',A='14/15')
    assert r.fixture_terms(current,history,decision_ns=decision)==expected
    assert r.fixture_terms(current,list(reversed(history)),decision_ns=decision)==expected


@pytest.mark.parametrize('mutation,error',[
    (lambda c,h,d:h[0].update(day=c['day']),'future_history'),
    (lambda c,h,d:h[0].update(available_ns=d+1),'future_history'),
    (lambda c,h,d:h[0].update(close_ns=c['open_ns']),'future_history'),
    (lambda c,h,d:h[0].update(day='2024-12-31'),'not_authorized'),
    (lambda c,h,d:h[0].update(day='2025-05-01'),'holdout'),
    (lambda c,h,d:c.update(day='2025-05-01'),'holdout'),
    (lambda c,h,d:h[0].update(origin='owned_historical_export'),'not_authorized'),
    (lambda c,h,d:c.update(origin='owned_historical_export'),'not_authorized'),
    (lambda c,h,d:h[0]['volumes'].pop(7),'not_zero_filled'),
    (lambda c,h,d:c['volumes'].update({7:0}),'not_zero_filled'),
    (lambda c,h,d:h[0].update(open_ns=h[0]['open_ns']+60*MINUTE),'same_clock'),
    (lambda c,h,d:c.update(available_ns=d+1),'future_row'),
    (lambda c,h,d:h[0].update(available_ns=h[0]['open_ns']),'too_early'),
])
def test_chronology_warmup_and_real_data_denial(arithmetic,mutation,error):
    c,h,d=arithmetic;mutation(c,h,d)
    with pytest.raises(ContractError,match=error):r.fixture_terms(c,h,decision_ns=d)


def test_exact_history_count_not_shortened(arithmetic):
    c,h,d=arithmetic
    with pytest.raises(ContractError,match='fourteen'):r.fixture_terms(c,h[:-1],decision_ns=d)
    with pytest.raises(ContractError,match='fourteen'):r.fixture_terms(c,h[:-1]+[h[0]],decision_ns=d)


def test_other_decision_time_rejected(arithmetic):
    c,h,d=arithmetic
    with pytest.raises(ContractError,match='boundary'):r.fixture_terms(c,h,decision_ns=d+MINUTE)


@pytest.mark.parametrize('day',['2024-12-31','2025-01-09','2025-01-17','2025-03-03','2025-04-01','2025-05-01','2025-06-30'])
def test_unauthorized_scope_before_any_query(day):
    class NoQuery:
        def execute(self,*_):pytest.fail('Unauthorized SQL')
    with pytest.raises(ContractError):r.project_cell(NoQuery(),'ASPI',day)


def test_other_symbols_rejected_before_query():
    class NoQuery:
        def execute(self,*_):pytest.fail('Unauthorized SQL')
    with pytest.raises(ContractError):r.project_cell(NoQuery(),'AAPL',r.DAYS[0])


@pytest.mark.parametrize('mutate',[
    lambda s:s.update(history_sessions=5),lambda s:s.update(history_sessions=14.0),
    lambda s:s.update(threshold=3.7),lambda s:s['decision'].update(offset_minutes=20),
    lambda s:s['target'].update(end_offset_minutes=60),lambda s:s['variants'][1]['features'].append('price'),
    lambda s:s['symbols'].append('POWL'),lambda s:s.update(execution_authorized=True),
    lambda s:s['accounting'].update(targets=2),lambda s:s.update(orders_enabled=True),
])
def test_frozen_single_family_rejects_unregistered_options(mutate):
    s=r.specification();mutate(s)
    with pytest.raises(ContractError,match='frozen'):r.validate_spec(s)


def test_calendar_dst_early_close_holiday_and_target_boundary():
    early={'2024-12-24':session('2024-12-24','13:00')}
    assert r.schedule(early,'2024-12-24')['target_end']<early['2024-12-24']['close']
    with pytest.raises(ContractError,match='holiday'):r.schedule(early,'2024-12-25')
    march={'2025-03-10':session('2025-03-10',offset='-04:00')}
    assert r.schedule(march,'2025-03-10')['decision']==timestamp_ns('2025-03-10T13:45:01Z')
    bad={'2025-01-02':session('2025-01-02','10:00')}
    with pytest.raises(ContractError,match='early_close'):r.schedule(bad,'2025-01-02')


def test_required_prior_dates_do_not_skip_unauthorized_or_use_future():
    calendar={d:session(d) for d in ['2024-12-31',*r.DAYS,'2025-01-17']}
    assert r.prior_days(calendar,'2025-01-03')==['2024-12-31','2025-01-02']
    assert '2025-01-17' not in r.prior_days(calendar,'2025-01-16')


@pytest.fixture
def database():
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE bars(symbol,stamp,day,segment,payload,lineage)')
    day=r.DAYS[0];opening=session(day)['open']
    payload=dict(open='987654321',high='987654321',low='987654321',close='987654321',vwap='987654321',
                 volume_shares=123456,trade_count=2,interval_ns=MINUTE,revision=False)
    lineage=dict(raw_page_sha256='a'*64,native_row_hash='b'*64,normalized_payload_hash='c'*64)
    for minute in range(46):
        if minute==7:continue
        p=dict(payload)
        if minute==8:p['volume_shares']=0
        conn.execute('INSERT INTO bars VALUES(?,?,?,?,?,?)',('ASPI',opening+minute*MINUTE,day,'regular',json.dumps(p),json.dumps(lineage)))
    yield conn
    conn.close()


def test_projection_cannot_return_price_volume_or_target_values(database):
    rows=r.project_cell(database,'ASPI',r.DAYS[0]);text=json.dumps(rows)
    assert '987654321' not in text and '123456' not in text
    assert all('payload' not in row and 'close' not in row and 'volume_shares' not in row for row in rows)
    assert 'json_extract(payload,\'$.high\')' not in r.PROJECTION
    assert 'json_extract(payload,\'$.close\')' not in r.PROJECTION


def test_missing_native_minute_is_distinct_from_explicit_zero(database):
    day=r.DAYS[0];rows=r.project_cell(database,'ASPI',day)
    result=r.coverage(rows,day,{day:session(day)})
    assert result['prefix']['missing_minutes']==['09:37']
    assert result['prefix']['native_zero_minutes']==['09:38']
    assert not result['prefix']['complete']
    assert result['target_metadata']['complete']


def test_duplicate_session_or_variant_specific_eligibility_denied():
    cells=[dict(symbol=s,day=d,eligible=(s=='ASPI' and d==r.DAYS[-1])) for d in r.DAYS for s in r.SYMBOLS]
    cohorts=r.common_cells(cells)
    assert cohorts['A']==cohorts['B']==cohorts['C']==['ASPI|2025-01-16']
    with pytest.raises(ContractError,match='overlapping'):r.common_cells(cells+[cells[0]])
    with pytest.raises(ContractError,match='scope'):r.common_cells(cells[:-1])


def test_missing_source_lineage_and_session_classification_reject(database):
    day=r.DAYS[0];rows=r.project_cell(database,'ASPI',day)
    rows[0]['segment']='post';rows[0]['raw_page_sha256']=None
    cov=r.coverage(rows,day,{day:session(day)})
    assert 'session_classification_mismatch' in cov['anomalies']
    assert 'native_lineage_missing' in cov['prefix']['invalid_minutes'][0]['reasons']


def test_preserved_v4_and_no_order_or_certificate_authority():
    import hashlib
    root=Path(__file__).resolve().parents[2]
    assert hashlib.sha256((root/'systematic_trader/protocols/bounded-admission-v4.json').read_bytes()).hexdigest()=='66518232c324faf0291695b656b0d4a78942ee9ba372e28d88bca6844fdb280f'
    s=r.specification()
    assert not s['orders_enabled'] and not s['certified'] and not s['execution_authorized']
    assert s['accounting']['performance_jobs']==0 and s['accounting']['variants']==3
    assert s['state']=='PRELIMINARY_DESIGN_ONLY'


def test_metadata_audit_replay_idempotency_and_review_contract(database,tmp_path,monkeypatch):
    calendar={d:session(d) for d in [*r.DAYS,'2024-12-24','2024-12-26','2024-12-27','2024-12-30','2024-12-31']}
    class FakePanel:
        def __init__(self,*_):
            self.connection=database
            self.manifest=dict(calendar=calendar,manifest_hash='a'*64,normalized_database_sha256='b'*64,
                raw_adjustment='raw',series_identity='literal archive',availability='unverified',admission_scope='conditional')
        def close(self):pass
    monkeypatch.setattr(r,'ResearchPanel',FakePanel)
    result=r.run(tmp_path/'audit')
    assert result['verdict']=='NOT FEASIBLE' and len(result['cells'])==50
    assert result['variant_cells']==dict(A=[],B=[],C=[])
    assert result['journal_records']==4 and not result['outcomes_inspected']
    assert result==r.replay(tmp_path/'audit')
    monkeypatch.setattr(r,'ResearchPanel',lambda *_:pytest.fail('repeat audit reread prices or metadata'))
    assert r.run(tmp_path/'audit')==result
    from ai_review.contracts import validate_packet
    packet=r.review_packet(tmp_path/'audit','2026-09-07T00:00:00Z');validate_packet(packet)
    assert packet['deterministic_context']['review_status']=='NOT_SUBMITTED'
    assert not packet['deterministic_context']['runner_authorized']
    (tmp_path/'audit'/'head.json').write_text('{}')
    with pytest.raises(ContractError):r.replay(tmp_path/'audit')


def test_saved_protocol_matches_frozen_requirements():
    path=Path(__file__).resolve().parents[2]/'systematic_trader/protocols/rvol-feasibility-v1.json'
    r.validate_spec(json.loads(path.read_text()))
    source=json.loads((path.parent/'target-domain-preliminary-v1.json').read_text())
    assert source['request']['symbols']==list(r.SYMBOLS)
    assert source['request']['sessions']==list(r.DAYS)


def test_interrupted_audit_resumes_committed_metadata_without_requery(database,tmp_path,monkeypatch):
    calendar={d:session(d) for d in r.DAYS}
    class FakePanel:
        def __init__(self,*_):
            self.connection=database
            self.manifest=dict(calendar=calendar,manifest_hash='a'*64,normalized_database_sha256='b'*64,
                raw_adjustment='raw',series_identity='literal',availability='unknown',admission_scope='conditional')
        def close(self):pass
    monkeypatch.setattr(r,'ResearchPanel',FakePanel)
    append=r.EvidenceStore.append
    def crash(self,journal,kind,key,body):
        if kind=='feasibility':raise RuntimeError('simulated interruption')
        return append(self,journal,kind,key,body)
    monkeypatch.setattr(r.EvidenceStore,'append',crash)
    with pytest.raises(RuntimeError,match='interruption'):r.run(tmp_path/'audit')
    monkeypatch.setattr(r.EvidenceStore,'append',append)
    monkeypatch.setattr(r,'ResearchPanel',lambda *_:pytest.fail('archive must not be queried again'))
    resumed=r.run(tmp_path/'audit')
    assert resumed['journal_records']==4 and resumed['verdict']=='NOT FEASIBLE'
    assert resumed==r.replay(tmp_path/'audit')
