from copy import deepcopy
from dataclasses import asdict
from datetime import datetime,timezone
from decimal import Decimal
import pytest
from systematic_trader.events import ContractError,digest,timestamp_ns
from systematic_trader.features import Session,MINUTE
from systematic_trader.sparse_observations import (minute_state,TapeCoverage,trade_rule,identity_bound,SparsePanel)
from systematic_trader.sparse_discovery import snapshot,profile,rank
from systematic_trader.momentum import MomentumPolicy,rank_movers
from systematic_trader.bounded_discovery import prefix_snapshot
from systematic_trader.features import Universe

OPEN=timestamp_ns('2025-01-02T14:30:00Z')
SESSION=Session('2025-01-02',OPEN,OPEN+390*MINUTE,'c'*64)
COVERAGE=TapeCoverage('TEST',OPEN,SESSION.close_ns,OPEN+1000*MINUTE,'a'*64)


def stamp(t):return datetime.fromtimestamp(t//10**9,timezone.utc).isoformat()


def bar(i,price='10',volume=100000):
    p=dict(open=price,high=price,low=price,close=price,vwap=price,volume_shares=volume,trade_count=1)
    return dict(stamp=OPEN+i*MINUTE,segment='regular',payload=p,lineage=dict(normalized_payload_hash=digest(p),raw_page_sha256='b'*64))


def trade(i,price='10',volume=100000,conditions=None):
    return dict(t=stamp(OPEN+i*MINUTE),p=price,s=volume,z='C',c=conditions or ['@'])


def states(n=30,spacing=3):
    return [minute_state('TEST',OPEN+i*MINUTE,bar=bar(i,str(10+Decimal(i)/10)) if i%spacing==0 else None,
        trades=[trade(i,str(10+Decimal(i)/10))] if i%spacing==0 else [],coverage=COVERAGE) for i in range(n)]


def prior(n=14):
    return [dict(session_id=str(i),close_ns=OPEN-(n-i)*1440*MINUTE,known_ns=OPEN-(n-i)*1440*MINUTE,
        minute_volumes=[100]*390,close='10',high='10',low='10',source_hash='d'*64) for i in range(n)]


def snap(rows,history=None,elapsed=30,**kwargs):
    return snapshot('TEST',rows,prior() if history is None else history,SESSION,cutoff=OPEN+elapsed*MINUTE,decision=OPEN+elapsed*MINUTE,**kwargs)


def test_sparse_every_three_minutes_surfaces_without_synthetic_bars():
    rows=states();s=snap(rows)
    assert not s['missing']
    assert s['values']['emitted_bar_count']==10
    assert s['values']['documented_no_price_minutes']==20
    assert s['values']['last_observed_bar_start_ns']==OPEN+27*MINUTE
    assert s['values']['staleness_upper_bound_ns']==3*MINUTE
    assert s['values']['continuation_observation_separation_ns']==3*MINUTE
    assert rank([s])['candidates'][0]['symbol']=='TEST'
    assert sum(r['bar'] is not None for r in rows)==10
    assert all(r['fill_allowed'] is False for r in rows)


@pytest.mark.parametrize('tape,code', [('C','I'),('C','W'),('C','4'),('A','B'),('C','P'),('C','Z')])
def test_non_price_forming_minutes_preserve_print_volume(tape,code):
    t=trade(0,conditions=[code]);t['z']=tape
    s=minute_state('TEST',OPEN,trades=[t],coverage=COVERAGE)
    assert s['activity']=='documented_non_price_forming_trades'
    assert s['bar'] is None and s['native_print_volume']==100000
    assert s['emitted_bar_volume']==0 and s['native_eligible_volume']==100000


def test_unknown_condition_does_not_approve_absence():
    t=trade(0,conditions=['?'])
    s=minute_state('TEST',OPEN,trades=[t],coverage=COVERAGE)
    assert s['coverage']=='unresolved'
    assert trade_rule(dict(z='UNKNOWN',c=['I']))==(None,None)
    assert trade_rule(dict(z='C',c=['?','I']))==(False,None)


def test_quote_only_does_not_reset_price_or_certify_trade_coverage():
    q=dict(t=stamp(OPEN),bp=10,ap=11)
    s=minute_state('TEST',OPEN,quotes=[q])
    assert s['activity']=='quote_only_observation' and s['coverage']=='unresolved'
    assert s['bar'] is None and s['provider_timestamp_ns'] is None
    rows=states();rows[-1]=minute_state('TEST',OPEN+29*MINUTE,quotes=[dict(t=stamp(OPEN+29*MINUTE))],coverage=COVERAGE)
    assert snap(rows)['values']['last_observed_bar_start_ns']==OPEN+27*MINUTE


def test_no_tape_coverage_remains_unknown_and_blocks_discovery():
    rows=states();rows[1]=minute_state('TEST',OPEN+MINUTE)
    assert 'unresolved_trade_or_bar_coverage' in snap(rows)['missing']
    assert not rank([snap(rows)])['candidates']
    assert minute_state('TEST',OPEN,trades=[],coverage=None)['emitted_bar_volume'] is None


def test_eligible_print_without_bar_is_missing_not_synthetic_price():
    row=minute_state('TEST',OPEN,trades=[trade(0)],coverage=COVERAGE)
    assert row['coverage']=='unresolved' and row['bar'] is None
    assert 'eligible_trades_without_native_bar' in row['issues']


def test_long_no_trade_period_preserves_stale_price_and_refuses_signal():
    rows=states(3)+[minute_state('TEST',OPEN+i*MINUTE,coverage=COVERAGE) for i in range(3,30)]
    s=snap(rows)
    assert s['values']['last_close']=='10'
    assert s['values']['staleness_upper_bound_ns']==30*MINUTE
    assert 'stale_last_price_at_decision' in s['missing'] and not rank([s])['candidates']


def test_cold_start_positive_activity_can_surface_without_invented_rvol():
    rows=states(spacing=1)
    for i in range(27,30):
        p=str(10+Decimal(i)/10)
        rows[i]=minute_state('TEST',OPEN+i*MINUTE,bar=bar(i,p,300000),trades=[trade(i,p,300000)],coverage=COVERAGE)
    s=snap(rows,history=[])
    assert s['values']['relative_volume_at_time'] is None
    assert s['lane']=='cold_start_same_session' and 'history_unavailable' in s['limitations']
    assert rank([s])['candidates'][0]['lane']=='cold_start_same_session'
    # Flat same-session volume is not automatically unusual.
    assert not rank([snap(states(spacing=1),history=[])])['candidates']


def test_recent_listing_excludes_prelisting_and_no_silent_symbol_mapping():
    records=[dict(symbol='TEST',evidence_kind='listed_from',effective_date='2025-01-02')]
    assert identity_bound('TEST','2025-01-01',records)[0]=='prelisting'
    assert identity_bound('TEST','2025-01-02',records)[0]=='archive_series_unverified'
    rows=states();rows[0]['identity']='prelisting'
    assert 'identity_exclusion' in snap(rows)['missing']


@pytest.mark.parametrize('action',['reverse_split','forward_split','name_change'])
def test_action_discontinuities_cannot_become_momentum(action):
    rows=states();rows[-1]['action_sources']=[action]
    assert not rank([snap(rows)])['candidates']
    assert not rank([snap(states(),action_boundary=True)])['candidates']


def test_halt_and_resumption_never_grant_tradability():
    for halt in [True,False,None]:
        s=minute_state('TEST',OPEN,bar=bar(0),trades=[trade(0)],coverage=COVERAGE,halted=halt)
        assert s['tradability']==('HALTED' if halt else 'UNKNOWN')
        assert not s['fill_allowed']


def test_late_out_of_order_and_future_inputs_do_not_leak():
    rows=states(60);base=snap(rows[:30])
    assert snap(list(reversed(rows)))==base
    future=prior()+[dict(close_ns=SESSION.close_ns,known_ns=SESSION.close_ns,session_id='future')]
    assert snap(rows,history=future)==base
    late=deepcopy(rows);late[0]['assumed_available_ns']=OPEN+31*MINUTE
    s=snap(late)
    assert 'unresolved_missing_minute_state_or_late_arrival' in s['missing']
    assert s['feature_hash']!=base['feature_hash']


def test_tape_bar_disagreements_fail_closed():
    s=minute_state('TEST',OPEN,bar=bar(0),trades=[trade(0,'11')],coverage=COVERAGE)
    assert s['coverage']=='unresolved' and 'bar_tape_price_range_disagreement' in s['issues']
    with pytest.raises(ContractError,match='outside_minute'):
        minute_state('TEST',OPEN,trades=[trade(1)],coverage=COVERAGE)
    with pytest.raises(ContractError,match='symbol_mismatch'):
        minute_state('OTHER',OPEN,coverage=COVERAGE)


@pytest.mark.parametrize('status', ['canceled', 'incorrect'])
def test_final_export_invalidated_print_does_not_change_bar_range_or_volume(status):
    valid=trade(0,'237',170)
    odd=trade(0,'236.98',169,['@','I'])
    invalid=dict(trade(0,'236.93',125),u=status)
    native=bar(0,'237',339)
    before=deepcopy([valid,odd,invalid])
    result=minute_state('TEST',OPEN,bar=native,trades=before,coverage=COVERAGE)
    assert result['coverage']=='native_bar_observed' and result['issues']==[]
    assert result['native_print_volume']==464 and result['native_eligible_volume']==339
    assert result['trade_count']==3 and result['eligible_price_prints']==1
    assert result['invalidated_prints']==1 and result['trade_update_counts'][status]==1
    assert before==[valid,odd,invalid] and result['original_available_ns'] is None
    assert result['bar']==native and result['fill_allowed'] is False
    # Reversing input receipt order preserves deterministic final-export output.
    assert result==minute_state('TEST',OPEN,bar=native,trades=list(reversed(before)),coverage=COVERAGE)


def test_final_export_corrected_row_uses_existing_conditions_without_inventing_chain():
    old=dict(trade(0,'11',100),u='incorrect')
    corrected=dict(trade(0,'10',100),u='corrected')
    result=minute_state('TEST',OPEN,bar=bar(0,'10',100),trades=[old,corrected],coverage=COVERAGE)
    assert result['issues']==[] and result['native_eligible_volume']==100
    assert result['invalidated_prints']==1
    assert trade_rule(dict(corrected,c=['@','I']))==(False,True)
    absent=minute_state('TEST',OPEN,trades=[old],coverage=COVERAGE)
    assert absent['bar'] is None and absent['native_eligible_volume']==0


@pytest.mark.parametrize('status', [None,'', 'unknown', [], 1])
def test_unknown_final_export_update_cannot_certify_absence_or_agreement(status):
    row=dict(trade(0,'10',100),u=status)
    assert trade_rule(row)==(None,None)
    result=minute_state('TEST',OPEN,trades=[row],coverage=COVERAGE)
    assert result['coverage']=='unresolved' and result['bar'] is None
    assert 'unresolved_trade_conditions' in result['issues']
    present=minute_state('TEST',OPEN,bar=bar(0,'10',100),trades=[row],coverage=COVERAGE)
    assert present['coverage']=='unresolved'


def test_full_sparse_profile_preserves_documented_volume_measure():
    rows=states(390);h=profile(rows,SESSION)
    assert h and len(h['minute_volumes'])==390
    assert sum(h['minute_volumes'])==130*100000
    rows[1]=minute_state('TEST',OPEN+MINUTE)
    assert profile(rows,SESSION) is None


def test_holdout_and_validation_read_guard_precedes_file_access():
    p=object.__new__(SparsePanel);p.root=None
    for day in ['2025-03-03','2025-04-01','2025-05-01','2025-06-30']:
        with pytest.raises(ContractError):p.session(day)


def test_dense_arithmetic_ranking_order_remains_legacy_compatible():
    rows=states(spacing=1);raw=[r['bar'] for r in rows]
    old=prefix_snapshot('TEST',raw,prior(),SESSION,elapsed=30)
    new=snap(rows)
    for key in ['last_close','return_3m','relative_volume_at_time','session_dollar_volume']:
        assert old['values'][key]==new['values'][key]
    u=Universe();u.observe([dict(instrument_id='archive-series:TEST',symbol='TEST',active=True,asset_type='common_stock')],known_ns=OPEN,effective_ns=OPEN,source_hash='e'*64)
    original=rank_movers([old],u,SESSION,as_of_ns=OPEN+30*MINUTE)
    assert [c['symbol'] for c in original['candidates']]==[c['symbol'] for c in rank([new])['candidates']]
    assert rank([new])==rank([new])


def test_state_clock_and_reference_overlay_retain_raw_observations():
    from systematic_trader.sparse_observations import reference_overlay
    rows=states(6);original=deepcopy(rows)
    actions=[dict(type='reverse_splits',record=dict(symbol='TEST',ex_date='2025-01-02'),source_sha256='a'*64)]
    halts=[dict(symbol='TEST',halt_ns=OPEN+MINUTE,trade_resumption_ns=OPEN+3*MINUTE,record_hash='b'*64)]
    result=reference_overlay(rows,'2025-01-02',actions,halts)
    assert rows==original
    assert result[1]['last_observation']['provider_bar_start_ns']==OPEN
    assert result[1]['price_age_upper_bound_ns']==2*MINUTE
    assert result[1]['price_knowledge']=='stale_valid_last_observation'
    assert result[1]['tradability']=='HALTED' and result[3]['tradability']=='UNKNOWN'
    assert all(s['action_sources'] and not s['fill_allowed'] for s in result)
    missing=minute_state('TEST',OPEN+6*MINUTE)
    result=reference_overlay(rows+[missing],'2025-01-02',[],[])
    assert result[-1]['price_knowledge']=='unknown_current_price' and result[-1]['last_observation'] is not None


def test_unknown_label_signals_are_not_counted_as_true_or_false_positives():
    from systematic_trader.sparse_benchmark import summarize_fixed
    labels=[dict(symbol='TEST',delay_minutes='2',day=str(i),label=dict(state=state,reason='fixture')) for i,state in enumerate(['positive','negative','unknown'])]
    signals={('2',str(i),'TEST'):{'present':True} for i in range(3)}
    result=summarize_fixed(labels,signals,'all')
    assert result['precision_known_labels']==.5 and result['precision_bounds']==[1/3,2/3]
    assert result['counts']['surfaced_unknown']==1 and result['counts']['surfaced_negative']==1


def test_calendar_selects_comparable_slots_without_skipping_unresolved_profiles():
    from systematic_trader.sparse_calendar import comparable_indices
    days=[f'2024-12-{i:02}' for i in range(1,22)]
    calendar={d:dict(open=OPEN,close=OPEN+(210 if i==18 else 390)*MINUTE) for i,d in enumerate(days)}
    morning=comparable_indices(days,calendar,before_day='2025-01-02',elapsed=210)
    afternoon=comparable_indices(days,calendar,before_day='2025-01-02',elapsed=211)
    assert morning==list(range(7,21))
    assert afternoon==[i for i in range(6,21) if i!=18]
    profiles=[object() for d in days];profiles[15]=None
    chosen=[profiles[i] for i in afternoon]
    assert len(chosen)==14 and chosen.count(None)==1
    assert afternoon==comparable_indices(days,calendar,before_day='2025-01-02',elapsed=211)


def test_calendar_selection_never_reads_future_price_profiles():
    from systematic_trader.sparse_calendar import comparable_indices
    days=['2024-12-31','2025-01-02','2025-01-03']
    c={d:dict(open=OPEN,close=OPEN+390*MINUTE) for d in days}
    assert comparable_indices(days,c,before_day='2025-01-02',elapsed=300)==[0]
    with pytest.raises(ContractError):comparable_indices(list(reversed(days)),c,before_day='2025-01-02',elapsed=300)


def test_saved_watchlist_chain_detects_tamper_and_truncation(tmp_path):
    from systematic_trader.sparse_review import verify_watchlists
    from systematic_trader.events import canonical_json
    from systematic_trader.research_panel import file_hash
    ranking=dict(candidates=[],excluded=[]);ranking['ranking_hash']=digest(ranking)
    row=dict(day='2025-01-02',delay_minutes=2,decision_ns=OPEN+10*MINUTE,ranking=ranking,previous_hash='0'*64)
    row['record_hash']=digest(row);p=tmp_path/'watchlists.jsonl';p.write_text(canonical_json(row)+'\n')
    expected=dict(watchlist_chain_head=row['record_hash'],scan_count=1,watchlist_sha256=file_hash(p))
    assert verify_watchlists(p,expected)['scans']==1
    p.write_text('')
    with pytest.raises(ContractError):verify_watchlists(p,expected)
    row['decision_ns']+=MINUTE;p.write_text(canonical_json(row)+'\n')
    with pytest.raises(ContractError):verify_watchlists(p,expected)
