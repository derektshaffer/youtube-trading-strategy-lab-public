from copy import deepcopy
import pytest
from systematic_trader.events import ContractError
from systematic_trader.features import MINUTE
from systematic_trader.selectivity_study import confirm, allowed_day


def row(t=10,price='10'):
    return dict(decision_ns=t*MINUTE,feature_cutoff_ns=(t-2)*MINUTE,rank=1,reasons=[],missing=[],
                last_observed_bar_start_ns=(t-3)*MINUTE,last_close=price,lane='cold_start_same_session')


def test_persistence_requires_new_observation_and_joint_rule_requires_response():
    a,b=row(),row(15)
    assert confirm(a,b)
    assert not confirm(a,b,strict_price_increase=True)
    b['last_close']='10.01';assert confirm(a,b,strict_price_increase=True)
    b['last_close']='9.99';assert confirm(a,b) and not confirm(a,b,strict_price_increase=True)
    b['last_observed_bar_start_ns']=a['last_observed_bar_start_ns'];assert not confirm(a,b)


@pytest.mark.parametrize('change',[{'rank':None},{'missing':['stale']},{'reasons':['volume']},{'lane':'full_history'},{'decision_ns':16*MINUTE}])
def test_resets_on_exclusion_lane_change_or_missed_scan(change):
    b=row(15);b.update(change);assert not confirm(row(),b)


def test_never_accepts_first_scan_and_rejects_future_inputs():
    assert not confirm(None,row())
    b=row(15);b['feature_cutoff_ns']=b['decision_ns']+1
    with pytest.raises(ContractError):confirm(row(),b)
    b=row(15);b['last_observed_bar_start_ns']=b['feature_cutoff_ns']
    with pytest.raises(ContractError):confirm(row(),b)


@pytest.mark.parametrize('day',['2025-03-03','2025-04-01','2025-05-01','2025-06-30','2024-12-31'])
def test_non_development_dates_blocked(day):
    with pytest.raises(ContractError):allowed_day(day)


def test_future_outcomes_cannot_change_decision_or_mutate_inputs():
    a,b=row(),row(15,'10.1');before=deepcopy((a,b))
    expected=confirm(a,b,strict_price_increase=True)
    assert (a,b)==before
    b['future_label']='negative';b['future_peak']='999'
    assert confirm(a,b,strict_price_increase=True)==expected


@pytest.mark.parametrize('price',['NaN','Infinity','0','-1'])
def test_bad_price_rejected(price):
    with pytest.raises(ContractError):confirm(row(),row(15,price))

from systematic_trader.selectivity_diagnostics import quote_context, QuoteIndex
from systematic_trader.events import timestamp_ns


def quote(t='2025-01-02T15:00:00Z',bid='10',ask='11'):
    return dict(t=t,bp=bid,ap=ask,bs=2,**{'as':3},c=['R'])


def test_quote_context_is_strictly_prior_and_preserves_units_and_uncertainty():
    q=quote();t=timestamp_ns(q['t'])
    assert not quote_context([q],t)['available']
    c=quote_context([q],t+1)
    assert c['available'] and c['age_ns']==1 and c['bid_size_native']==2
    assert c['original_arrival_ns'] is None and not c['fill_allowed']
    assert c['size_unit']=='provider_round_lots_not_verified_shares'


def test_all_tied_quote_records_checked_even_when_tail_duplicates():
    q=quote();a=quote(bid='9')
    assert not quote_context([a,q,q],timestamp_ns(q['t'])+1)['available']
    assert quote_context([q,q],timestamp_ns(q['t'])+1)['available']


@pytest.mark.parametrize('bid,ask',[('0','10'),('10','10'),('11','10'),('NaN','10')])
def test_invalid_quote_cannot_supply_context(bid,ask):
    q=quote(bid=bid,ask=ask);assert not quote_context([q],timestamp_ns(q['t'])+1)['available']


def test_quote_order_not_silently_sorted():
    with pytest.raises(ContractError):QuoteIndex([quote('2025-01-02T15:01:00Z'),quote()])

from systematic_trader.selectivity_diagnostics import prefix_features


def minute_states(source=None):
    return [dict(minute_start_ns=t*MINUTE,minute_end_ns=(t+1)*MINUTE,coverage='native_bar_observed',issues=[],
        source_hash=source,emitted_bar_volume=100,eligible_price_prints=0,state_hash=str(t),action_sources=[],
        bar=dict(stamp=t*MINUTE,payload=dict(open='10',close='10',high='11',low='9',vwap='10',volume_shares=100))) for t in range(8)]


def diagnostic_detection():
    return dict(**row(),relative_volume=None,same_session_volume_acceleration='2',continuation='0.01',
                session_dollar_volume='8000',staleness_upper_bound_ns=3*MINUTE,action_sources=[])


def test_missing_tape_is_unknown_density_not_zero():
    assert prefix_features(minute_states(),diagnostic_detection())['price_forming_prints_last_6m'] is None
    assert prefix_features(minute_states('verified-source'),diagnostic_detection())['price_forming_prints_last_6m']==0


def test_diagnostic_future_suffix_cannot_change_prefix_features():
    states=minute_states('verified-source');d=diagnostic_detection();before=prefix_features(states,d)
    future=deepcopy(states[-1]);future.update(minute_start_ns=9*MINUTE,minute_end_ns=10*MINUTE,state_hash='future')
    future['bar']['payload']['high']='999999'
    assert prefix_features(states+[future],d)==before

from systematic_trader.selectivity_study import criteria, load_protocol
from pathlib import Path


def test_precision_alone_cannot_clear_preregistered_joint_success():
    p=load_protocol(Path(__file__).parents[2]/'systematic_trader/protocols/discovery-selectivity-v1.json')
    def summary(tp,fp,unknown=0):
        total=tp+fp+unknown
        return {'counts':{'surfaced_positive':tp,'surfaced_negative':fp},'precision_bounds':[tp/total,(tp+unknown)/total]}
    baseline={d:{'0.05':{'all':summary(19,65,2)}} for d in ('0','2','5')}
    candidate={d:{'0.05':{'all':summary(1,0)},'0.10':{'all':summary(3,0)},'0.20':{'all':summary(1,0)}} for d in baseline}
    result=criteria(candidate,baseline,p['success'])
    assert not result['development_success']
    assert result['checks']['2']['precision_bound_improved']
    for d in candidate:candidate[d]['0.05']['all']=summary(16,45)
    assert criteria(candidate,baseline,p['success'])['development_success']
