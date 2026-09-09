import pytest
from systematic_trader.events import ContractError, timestamp_ns
from systematic_trader.historical_execution import AvailabilityClock, CausalQuoteIndex, load_export

T=timestamp_ns('2025-01-02T14:30:00Z')
def quote(seconds=0,**changes):
    q=dict(t=f'2025-01-02T14:30:{seconds:02d}Z',bp=10,ap=11,bs=1,**{'as':2},bx='Q',ax='Q',c=['R'],z='C')
    q.update(changes);return q

def test_export_receipt_is_not_original_arrival():
    q=quote(retrieved_ns=T+1)
    index=CausalQuoteIndex([q],AvailabilityClock('actual_application_receipt'))
    c=index.context(T+10**9,max_age_ns=30*10**9)
    assert not c['available'] and c['unknown_arrivals']==1 and not c['actual_arrival_verified']

def test_future_and_equal_arrivals_never_match():
    index=CausalQuoteIndex([quote()],AvailabilityClock('assumed_source_delay',10**9))
    assert not index.context(T+10**9,max_age_ns=30*10**9)['available']
    c=index.context(T+10**9+1,max_age_ns=30*10**9)
    assert c['available'] and not c['actual_arrival_verified'] and not c['fill_allowed']
    assert c['quote_available_ns']<T+10**9+1

def test_reordered_receipts_do_not_regress_newer_source_state():
    qs=[quote(0,original_application_receipt_ns=T+10*10**9),quote(1,bp=10.5,original_application_receipt_ns=T+2*10**9)]
    index=CausalQuoteIndex(qs,AvailabilityClock('actual_application_receipt'))
    c=index.context(T+11*10**9,max_age_ns=30*10**9)
    assert c['bid']=='10.5' and c['actual_arrival_verified']

def test_ambiguous_same_source_time_remains_unknown_until_new_event():
    index=CausalQuoteIndex([quote(),quote(bp=9),quote(1)],AvailabilityClock('assumed_source_delay'))
    assert index.context(T+1,max_age_ns=30*10**9)['reason']=='ambiguous_quote_state'
    assert index.context(T+10**9+1,max_age_ns=30*10**9)['available']

def test_duplicates_do_not_create_freshness_or_size():
    index=CausalQuoteIndex([quote(),quote()],AvailabilityClock('assumed_source_delay'))
    assert index.context(T+1,max_age_ns=30*10**9)['bid_size_native']==1
    assert index.context(T+31*10**9,max_age_ns=30*10**9)['reason']=='stale_quote'

def test_crossed_quote_and_native_order_regression_fail():
    index=CausalQuoteIndex([quote(bp=12)],AvailabilityClock('assumed_source_delay'))
    assert not index.context(T+1,max_age_ns=30*10**9)['available']
    with pytest.raises(ContractError):CausalQuoteIndex([quote(1),quote()],AvailabilityClock('assumed_source_delay'))

def test_protected_period_rejected_before_source_open(tmp_path):
    for day in ['2025-03-03','2025-04-01','2025-05-01','2025-06-02']:
        with pytest.raises(ContractError):load_export(tmp_path/'nonexistent',kind='quotes',day=day,symbols=['ASPI'])

@pytest.mark.parametrize('mode,delay',[('implicit',0),('assumed_source_delay',-1),('actual_application_receipt',1),('assumed_source_delay',True)])
def test_clock_cannot_hide_assumptions(mode,delay):
    with pytest.raises(ContractError):AvailabilityClock(mode,delay)
