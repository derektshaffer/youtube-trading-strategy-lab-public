from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from systematic_trader.events import ContractError, digest
from systematic_trader.ledger import Ledger
from systematic_trader.market_state import MarketState
from systematic_trader.research_fixture import populate, evaluate, KEY, OPEN, SESSION
from systematic_trader.research_engine import Simulator, ExecutionPolicy, RiskBook, RiskPolicy
from systematic_trader.features import MINUTE


@pytest.fixture
def events(tmp_path):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        populate(ledger)
        return list(ledger.replay(through_seq=ledger.watermark()))


def state(events):
    m=MarketState()
    for e in events:m.apply(e)
    return m


def resign(decision):
    decision['decision_id']=digest({k:v for k,v in decision.items() if k!='decision_id'})
    return decision


def test_alpha_cannot_bypass_independent_market_risk_with_empty_rejections(events):
    d=evaluate(events)['decisions'][0]
    assert RiskBook().reserve(d)['quantity']==0
    m=state(events[:9]);m.gaps.add('unresolved-source-gap')
    result=RiskBook().reserve(d,market=m)
    assert result['quantity']==0 and 'risk_unresolved_data_gap' in result['rejections']
    m=state(events[:9]);m.instruments[KEY].halted=None
    assert 'risk_market_state_unsafe' in RiskBook().reserve(d,market=m)['rejections']


@pytest.mark.parametrize('field,value,reason',[
    ('ask_size_shares',10000000,'risk_quote_liquidity_or_cap_mismatch'),
    ('preceding_minute_volume',999999999,'risk_volume_evidence_mismatch'),
    ('created_ns',OPEN+6*MINUTE,'risk_market_watermark_mismatch'),
])
def test_strategy_cannot_invent_market_liquidity_or_clock(events,field,value,reason):
    d=deepcopy(evaluate(events)['decisions'][0]);d[field]=value;resign(d)
    result=RiskBook().reserve(d,market=state(events[:9]))
    assert result['quantity']==0 and reason in result['rejections']


def test_modified_intent_rejected_and_submitted_intent_is_frozen(events):
    d=deepcopy(evaluate(events)['decisions'][0]);d['cap']='10000'
    assert 'decision_hash_mismatch' in RiskBook().reserve(d,market=state(events[:9]))['rejections']
    d=evaluate(events)['decisions'][0];sim=Simulator();assert sim.submit(d,market=state(events[:9]))
    original=sim.pending[KEY[3]]['decision']['cap'];d['cap']='100000'
    assert sim.pending[KEY[3]]['decision']['cap']==original


def test_feed_gap_does_not_ack_cancel_release_risk_or_forge_later_fill(events):
    d=evaluate(events)['decisions'][0];sim=Simulator();m=state(events[:9]);sim.submit(d,market=m)
    reservation=deepcopy(sim.risk.reservations)
    gap=deepcopy(events[9]);gap.update(event_type='recorder.gap',payload=dict(reason='disconnect',unresolved=True),
        source_time_ns=None,instrument_id=None,symbol=None,content_hash=digest('gap'))
    m.apply(gap);sim.advance(gap,m)
    assert sim.pending and sim.risk.reservations==reservation
    assert sim.ambiguities==['data_gap_execution_state_unknown']
    m.apply(events[10]);sim.advance(events[10],m)
    assert not sim.positions and sim.pending
    assert not any(a['type']=='simulated_cancel_ack' for a in sim.audit)


def test_zero_latency_stop_waits_for_subsequent_quote(events):
    d=evaluate(events)['decisions'][0];sim=Simulator(policy=ExecutionPolicy(arrival_latency_ns=0))
    m=state(events[:9]);sim.submit(d,market=m);m.apply(events[9]);sim.advance(events[9],m)
    assert sim.positions
    q=deepcopy(events[10]);q['payload'].update(bid='99',ask='99.01');q['content_hash']=digest('stop-quote')
    m.apply(q);sim.advance(q,m)
    assert sim.positions and not any(a['type']=='simulated_exit_fill' for a in sim.audit)
    later=deepcopy(q);later.update(ledger_seq=12,event_id='later-stop-quote',content_hash=digest('later-stop'),
        source_time_ns=q['source_time_ns']+1,received_ns=q['received_ns']+1,normalized_ns=q['received_ns']+1)
    m.apply(later);sim.advance(later,m)
    assert not sim.positions and sim.completed


def test_unknown_status_revokes_previously_tradeable_state(events):
    m=state(events[:9]);unknown=deepcopy(events[1]);unknown.update(ledger_seq=10,event_id='unknown-status',
        received_ns=OPEN+6*MINUTE,normalized_ns=OPEN+6*MINUTE,source_time_ns=OPEN+6*MINUTE,content_hash=digest('unknown-status'))
    unknown['payload']['status_code']='UNDOCUMENTED'
    m.apply(unknown)
    assert m.instruments[KEY].halted is None


def test_peak_to_trough_limit_and_repeated_execution_failures_are_independent(events):
    d=evaluate(events)['decisions'][0];book=RiskBook(RiskPolicy(maximum_drawdown='5'))
    book.mark_pnl('10');book.mark_pnl('4')
    assert book.killed and 'drawdown_limit' in book.reserve(d,market=state(events[:9]))['rejections']
    book=RiskBook()
    for _ in range(3):book.record_execution_failure('rejection')
    assert book.killed and book.reserve(d,market=state(events[:9]))['quantity']==0
    book=RiskBook();book.record_execution_failure('reconciliation_unknown');assert book.killed


def test_unknown_correlations_share_conservative_independent_budget(events):
    d=evaluate(events)['decisions'][0]
    book=RiskBook(RiskPolicy(correlated_notional='1100'))
    # Existing separate-name reservation consumes the same unknown group. The
    # strategy cannot grant itself a different group by editing its decision.
    book.reservations['other:name']=dict(qty=10,risk=Decimal('4'),notional=Decimal('1050'))
    result=book.reserve(d,market=state(events[:9]))
    assert result['quantity']==0
    grouped=RiskBook(RiskPolicy(correlated_notional='1100'),correlation_groups={'other:name':'other-sector',KEY[3]:'technology'})
    grouped.reservations=deepcopy(book.reservations)
    assert grouped.reserve(d,market=state(events[:9]))['quantity']==10


def test_skipped_event_or_future_market_state_cannot_drive_simulator(events):
    sim=Simulator();m=state(events[:9]);sim.advance(events[8],m)
    m=state(events[:11])
    with pytest.raises(ContractError,match='missing_ledger'):sim.advance(events[10],m)
    with pytest.raises(ContractError,match='watermark_mismatch'):Simulator().advance(events[9],m)


def test_post_session_quote_cannot_liquidate_residual_at_impossible_price(events):
    d=evaluate(events)['decisions'][0];sim=Simulator();m=state(events[:9]);sim.submit(d,market=m)
    m.apply(events[9]);sim.advance(events[9],m)
    after=deepcopy(events[10]);after.update(received_ns=SESSION.close_ns,normalized_ns=SESSION.close_ns,
        source_time_ns=SESSION.close_ns,content_hash=digest('after-close'))
    m.apply(after);sim.advance(after,m)
    assert sim.positions and not sim.completed
    assert sim.ambiguities==['session_closed_with_exposure']


def test_invalid_order_deadlines_are_independently_rejected(events):
    d=deepcopy(evaluate(events)['decisions'][0]);d['expires_ns']=d['session_close_ns']+1;resign(d)
    result=RiskBook().reserve(d,market=state(events[:9]))
    assert 'invalid_order_session_window' in result['rejections'] and not result['quantity']
