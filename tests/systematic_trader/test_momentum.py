from copy import deepcopy
from decimal import Decimal

import pytest

from systematic_trader.events import ContractError, digest
from systematic_trader.features import MINUTE, Session, Universe
from systematic_trader.historical import HistoricalImporter, historical_view
from systematic_trader.events import canonical_json, timestamp_ns
from systematic_trader.momentum import MomentumPolicy, intraday_snapshot, rank_movers
from systematic_trader.ledger import Ledger
from systematic_trader.research_fixture import stamp

OPEN=timestamp_ns('2026-03-09T13:30:00Z')
KEY=('vendor','minutes','import','id:mover')
POLICY=MomentumPolicy(minimum_history_sessions=3,minimum_dollar_volume='100',minimum_relative_volume='2')
SESSION=Session('momentum-day',OPEN,OPEN+390*MINUTE,digest('fixture-calendar'))


def dataset():
    records=[
        dict(kind='instrument',symbol='MOVER',instrument_id=KEY[3],effective_from=stamp(OPEN-MINUTE),
             effective_to=stamp(OPEN+1440*MINUTE),known_at=stamp(OPEN-MINUTE)),
        dict(kind='calendar',session_id=SESSION.session_id,exchange='TEST',pre_open=stamp(OPEN),open=stamp(OPEN),
             close=stamp(SESSION.close_ns),post_close=stamp(SESSION.close_ns),known_at=stamp(OPEN-MINUTE)),
        dict(kind='coverage',instrument_id=KEY[3],session_id=SESSION.session_id,segments=['regular'],known_at=stamp(OPEN-MINUTE)),
    ]
    for i in range(9):
        price=Decimal(10)+Decimal(i)/10
        records.append(dict(kind='bar',symbol='MOVER',instrument_id=KEY[3],start=stamp(OPEN+i*MINUTE),
                            known_at=stamp(OPEN+(i+1)*MINUTE),revision=0,open=str(price),close=str(price+Decimal('.05')),
                            high=str(price+Decimal('.06')),low=str(price-Decimal('.02')),vwap=str(price),
                            volume=100*(i+1),trade_count=10))
    return dict(version='historical-bundle-v1',dataset_id='momentum-fixture',provider=KEY[0],feed=KEY[1],
                source='synthetic',evidence='fixture',adjustment='unadjusted',
                availability=dict(mode='vendor_timestamp',delay_ns=0),universe_coverage='point_in_time',records=records)


def history():
    return [dict(instrument_id=KEY[3],provider=KEY[0],feed=KEY[1],origin=KEY[2],session_id=f'prior-{i}',
                 close_ns=OPEN-(4-i)*1440*MINUTE,known_ns=OPEN-(4-i)*1440*MINUTE+MINUTE,source_hash=digest(i),
                 high='10',low='8',close='9',minute_volumes=[100]*390,adjustment='unadjusted') for i in range(3)]


def universe():
    u=Universe();u.observe([dict(instrument_id=KEY[3],symbol='MOVER',active=True,asset_type='common_stock')],
                          known_ns=OPEN-MINUTE,effective_ns=OPEN-MINUTE,source_hash=digest('all-names'))
    return u


def snapshot(ledger,when=OPEN+6*MINUTE,h=None):
    v=historical_view(ledger,through_seq=ledger.watermark(),as_of_ns=when,dataset_id='momentum-fixture')
    return intraday_snapshot(v['market'],KEY,SESSION,history() if h is None else h,as_of_ns=when,policy=POLICY)


@pytest.fixture
def ledger(tmp_path):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        HistoricalImporter(ledger,clock=lambda:OPEN+1440*MINUTE).ingest(canonical_json(dataset()).encode())
        yield ledger


def test_interpretable_intraday_values_use_only_completed_prefix(ledger):
    s=snapshot(ledger);v=s['values']
    assert s['missing']==[]
    assert v['session_volume']==2100 and v['relative_volume_at_time']=='3.5'
    assert v['last_close']=='10.55' and v['session_high']=='10.56'
    assert v['volume_acceleration']=='2.5'
    assert v['breakout'] and v['higher_lows']
    assert Decimal(v['gap_from_prior_close'])==Decimal(10)/9-1
    assert 'fresh_quote_unavailable' in s['execution_missing']
    assert s['execution_authority']=='none'


def test_future_day_values_late_history_and_late_correction_cannot_leak(ledger):
    before=snapshot(ledger)
    h=history();future=deepcopy(h[0]);future.update(close_ns=SESSION.close_ns,known_ns=SESSION.close_ns,
        high='9999',close='9999',minute_volumes=[9999999]*390)
    late=deepcopy(h[1]);late.update(known_ns=SESSION.close_ns,minute_volumes=[999999]*390)
    assert snapshot(ledger,h=h+[future,late])==before
    b=dataset();revision=deepcopy(b['records'][3]);revision.update(revision=1,known_at=stamp(OPEN+8*MINUTE),high='1000')
    b['records']=[revision];HistoricalImporter(ledger,clock=lambda:OPEN+1440*MINUTE).ingest(canonical_json(b).encode())
    assert snapshot(ledger)==before
    assert snapshot(ledger,OPEN+9*MINUTE)['values']['session_high']=='1000'


def test_dynamic_discovery_uses_supplied_universe_and_preserves_execution_missingness(ledger):
    s=snapshot(ledger);ranking=rank_movers([s],universe(),SESSION,as_of_ns=s['as_of_ns'],policy=POLICY)
    assert ranking['candidates'][0]['symbol']=='MOVER'  # Outside infrastructure ten symbols.
    assert 'breakout' in ranking['candidates'][0]['signals']
    assert 'fresh_quote_unavailable' in ranking['candidates'][0]['execution_missing']
    assert not ranking['production_eligible']
    s['values']['last_close']='999'
    assert ranking['candidates'][0]['relative_volume']=='3.5'
    with pytest.raises(ContractError,match='hash_mismatch'):
        rank_movers([s],universe(),SESSION,as_of_ns=s['as_of_ns'],policy=POLICY)


def test_missing_whole_universe_not_silently_reduced(ledger):
    s=snapshot(ledger)
    with pytest.raises(ContractError,match='coverage_incomplete'):
        rank_movers([],universe(),SESSION,as_of_ns=s['as_of_ns'],policy=POLICY)


@pytest.mark.parametrize('alter',[
    lambda s:s.update(as_of_ns=s['as_of_ns']-MINUTE),
    lambda s:s['session'].update(session_id='previous-day'),
    lambda s:s['policy'].update(minimum_history_sessions=1),
])
def test_stale_wrong_session_or_wrong_policy_features_rejected(ledger,alter):
    s=snapshot(ledger);when=s['as_of_ns'];alter(s)
    with pytest.raises(ContractError,match='context_mismatch'):
        rank_movers([s],universe(),SESSION,as_of_ns=when,policy=POLICY)


def test_shortened_prior_session_is_not_extrapolated(ledger):
    h=history();h[0]['minute_volumes']=[100]*5
    s=snapshot(ledger,h=h)
    assert 'historical_time_bucket_unavailable' in s['missing']
    assert rank_movers([s],universe(),SESSION,as_of_ns=s['as_of_ns'],policy=POLICY)['candidates']==[]


def test_old_effective_universe_receipt_does_not_roll_back_new_membership():
    u=universe()
    u.observe([],known_ns=OPEN+MINUTE,effective_ns=OPEN+MINUTE,source_hash=digest('delist'))
    u.observe([dict(instrument_id=KEY[3],symbol='MOVER',active=True,asset_type='common_stock')],
              known_ns=OPEN+2*MINUTE,effective_ns=OPEN-MINUTE,source_hash=digest('late-old-snapshot'))
    assert u.as_of(OPEN+3*MINUTE)[0]=={}
    assert KEY[3] in u.as_of(OPEN)[0]


def test_gap_blocks_watchlist(ledger):
    b=dataset();revision=deepcopy(b['records'][3]);revision.update(revision=0,known_at=stamp(OPEN+5*MINUTE),high='999')
    b['records']=[revision];HistoricalImporter(ledger,clock=lambda:OPEN+1440*MINUTE).ingest(canonical_json(b).encode())
    s=snapshot(ledger)
    assert 'conflicting_bar_revision' in s['missing']
    assert not rank_movers([s],universe(),SESSION,as_of_ns=s['as_of_ns'],policy=POLICY)['candidates']


def test_known_corporate_action_crossing_history_blocks_unadjusted_returns(ledger):
    b=dataset();b['records']=[dict(kind='corporate_action',instrument_id=KEY[3],action_id='split',action_type='split',
        effective_at=stamp(OPEN),known_at=stamp(OPEN-MINUTE),details={'ratio':'2'})]
    HistoricalImporter(ledger,clock=lambda:OPEN+1440*MINUTE).ingest(canonical_json(b).encode())
    assert 'corporate_action_boundary_unadjusted' in snapshot(ledger)['missing']


def test_history_scan_connects_archived_prior_profiles_universe_features_and_ranking(tmp_path):
    from systematic_trader.momentum import historical_scan
    b=dataset();current=deepcopy(b['records']);rows=[]
    current[0]['effective_from']=stamp(OPEN-5*1440*MINUTE)
    current[0]['known_at']=stamp(OPEN-5*1440*MINUTE)
    rows.append(current[0])
    for day in range(3,0,-1):
        shift=day*1440*MINUTE
        cal=deepcopy(current[1]);cal.update(session_id=f'prior-{day}')
        for field in ['known_at','pre_open','open','close','post_close']:
            cal[field]=stamp(timestamp_ns(cal[field])-shift)
        cal['close']=stamp(OPEN-shift+9*MINUTE);cal['post_close']=cal['close']
        rows.append(cal)
        cov=deepcopy(current[2]);cov.update(session_id=cal['session_id'],known_at=stamp(OPEN-shift-MINUTE));rows.append(cov)
        for r in current[3:]:
            prior=deepcopy(r)
            for field in ['start','known_at']:prior[field]=stamp(timestamp_ns(prior[field])-shift)
            prior['volume']=100
            rows.append(prior)
    rows+=current[1:]
    rows.append(dict(kind='universe',known_at=stamp(OPEN-MINUTE),effective_at=stamp(OPEN-MINUTE),entries=[
        dict(instrument_id=KEY[3],symbol='MOVER',active=True,asset_type='common_stock')]))
    b['records']=rows
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        HistoricalImporter(ledger,clock=lambda:OPEN+1440*MINUTE).ingest(canonical_json(b).encode())
        v=historical_view(ledger,through_seq=ledger.watermark(),as_of_ns=OPEN+6*MINUTE,dataset_id='momentum-fixture')
        result=historical_scan(v,session_id=SESSION.session_id,policy=POLICY)
        assert result['features'][0]['values']['relative_volume_at_time']=='3.5'
        assert result['ranking']['candidates'][0]['symbol']=='MOVER'
        assert not result['production_eligible']
