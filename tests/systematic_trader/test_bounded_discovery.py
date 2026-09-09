from copy import deepcopy
from unittest.mock import Mock
from decimal import Decimal
import pytest
from systematic_trader.events import ContractError,digest,timestamp_ns
from systematic_trader.features import MINUTE,Session
from systematic_trader.bounded_discovery import prefix_snapshot,evaluation_labels,summarize
from systematic_trader.research_panel import ResearchPanel,admission,SOURCE_HASH,bar_findings,independent_calendar_check
from systematic_trader.research_split import ResearchSplit,HOLDOUT_START
from systematic_trader.corporate_action_basis import split_basis

OPEN=timestamp_ns('2025-01-02T14:30:00Z')
SESSION=Session('2025-01-02',OPEN,OPEN+390*MINUTE,digest('calendar-fixture'))


def rows():
    return [dict(stamp=OPEN+i*MINUTE,segment='regular',payload=dict(open='10',high='11',low='9',close=str(Decimal(10)+Decimal(i)/100),vwap='10',volume_shares=1000,trade_count=10)) for i in range(20)]


def prior():
    return [dict(session_id=str(i),close_ns=OPEN-(i+1)*1440*MINUTE,known_ns=OPEN-(i+1)*1440*MINUTE,
        high='11',low='9',close='10',volume=39000,minute_volumes=[100]*390,source_hash=digest(i)) for i in range(14)]


def test_future_bars_or_future_history_cannot_change_prefix_features():
    original=rows();before=prefix_snapshot('TEST',original,prior(),SESSION,elapsed=10)
    changed=deepcopy(original)
    for r in changed[10:]:r['payload'].update(high='1000',close='999',volume_shares=9000000)
    future=dict(prior()[0],close_ns=OPEN+1440*MINUTE,known_ns=OPEN+1440*MINUTE)
    assert prefix_snapshot('TEST',changed,[*prior(),future],SESSION,elapsed=10)==before


def test_missing_prefix_never_imputed_and_actions_are_exclusionary():
    r=rows();del r[2]
    s=prefix_snapshot('TEST',r,prior(),SESSION,elapsed=10,action_boundary=True)
    assert 'intraday_bar_coverage_incomplete' in s['missing'] and 'corporate_action_lookback_excluded' in s['missing']
    assert not s['values']


def test_early_close_profiles_are_not_extrapolated():
    p=prior()
    for h in p:h['minute_volumes']=[100]*210
    s=prefix_snapshot('TEST',rows(),p,SESSION,elapsed=215)
    assert 'historical_time_bucket_unavailable' in s['missing']


def test_holdout_reader_refuses_before_database_query():
    panel=ResearchPanel.__new__(ResearchPanel);panel.connection=Mock()
    with pytest.raises(ContractError,match='holdout'):panel.session('2025-05-01')
    with pytest.raises(ContractError,match='period'):panel.session('2025-03-03')
    panel.connection.execute.assert_not_called()


def test_unknown_labels_are_not_negative_examples_or_false_zero_precision():
    rows=[dict(day='2025-01-02',labels=None,first_detection={'decision_ns':OPEN})]
    result=summarize(rows)['0.05']
    assert result['precision'] is None and result['recall'] is None
    assert result['false_positives']==0 and result['surfaced_unknown_labels']==1
    assert result['precision_bounds_including_unknown']==[0,1]


def test_tier1_assumptions_and_edited_flags_cannot_authorize_unknown_halt_fills():
    m=dict(source_manifest_hash=SOURCE_HASH,split=ResearchSplit().manifest(),calendar_check=dict(regular_passed=True),validation_admissible=True)
    m['manifest_hash']=digest(m)
    d=admission(m,experiment='diagnostic',purpose='conditional_discovery')
    assert not d['admitted']
    assert admission(m,experiment='diagnostic',purpose='conditional_discovery',explicit_export_assumptions=True)['admitted']
    for tier in (1,2):
        result=admission(m,experiment='orb',purpose='strategy',tier=tier,conservative_tier1=True)
        assert not result['admitted'] and 'historical_tradability_not_established' in result['reasons']


def test_reverse_split_preserves_dollars_and_never_uses_future_knowledge():
    raw=rows()[0]['payload'];unchanged=deepcopy(raw)
    action=dict(effective_ns=OPEN,verified_publication_ns=OPEN-MINUTE,new_shares=1,old_shares=100,source_hash=digest('fixture'))
    result=split_basis(raw,[action],bar_ns=OPEN-MINUTE,as_of_ns=OPEN)
    assert result['adjusted']['close']=='1000' and result['adjusted']['volume_shares']=='10.00'
    assert Decimal(result['adjusted']['close'])*Decimal(result['adjusted']['volume_shares'])==Decimal(raw['close'])*raw['volume_shares']
    assert raw==unchanged
    with pytest.raises(ContractError,match='future_split'):split_basis(raw,[action],bar_ns=OPEN-MINUTE,as_of_ns=OPEN-2*MINUTE)
    with pytest.raises(ContractError,match='publication'):split_basis(raw,[dict(action,verified_publication_ns=None)],bar_ns=OPEN-MINUTE,as_of_ns=OPEN)


def test_zero_volume_is_exclusionary_and_vwap_is_never_clipped():
    p=rows()[0]['payload'];p.update(volume_shares=0,vwap='20')
    findings=bar_findings(p)
    assert ('zero_volume_price_bar','exclusionary') in findings
    assert any(r.startswith('vwap_outside') for r,c in findings) and p['vwap']=='20'


def test_discovery_run_registers_and_persists_without_accessing_later_periods(tmp_path,monkeypatch):
    import json
    from dataclasses import asdict
    from systematic_trader import bounded_discovery as module
    from systematic_trader.momentum import MomentumPolicy
    from systematic_trader.research_check import save
    m=dict(source_manifest_hash=SOURCE_HASH,split=ResearchSplit().manifest(),calendar_check=dict(regular_passed=True),
        symbols=['TEST'],calendar={'2025-01-02':dict(open=OPEN,close=OPEN+20*MINUTE,source_hash=digest('fixture'))},corporate_actions=[],limitations=['fixture_only'])
    m['manifest_hash']=digest(m)
    class Panel:
        manifest=m
        def __init__(self,path):pass
        def close(self):pass
        def session(self,day):
            assert day=='2025-01-02'
            return {'TEST':rows()}
    monkeypatch.setattr(module,'ResearchPanel',Panel)
    protocol=dict(version='fixture-diagnostic-protocol',phase='development_only',policy=asdict(MomentumPolicy()),delay_minutes=[0,2,5],scan_every_minutes=5)
    protocol['protocol_hash']=digest(protocol);save(tmp_path/'protocol.json',protocol)
    result=module.run('fixture',tmp_path/'protocol.json',tmp_path/'result')
    assert result['scan_count']==8 and result['performance_experiments_run']==0
    assert result['result_hash']==digest({k:v for k,v in result.items() if k!='result_hash'})
    assert json.loads((tmp_path/'result/experiment-audit.json').read_text())['records']==3


def test_calendar_dst_early_close_and_unexpected_holiday_are_explicit():
    from systematic_trader.dataset_audit import calendar_sessions
    def cal(day,close='16:00'):
        return dict(date=day,open='09:30',close=close,session_open='0400',session_close='1700' if close=='13:00' else '2000')
    s=calendar_sessions([cal('2025-03-07'),cal('2025-03-10'),cal('2024-12-24','13:00')])
    assert s['2025-03-07']['open']==timestamp_ns('2025-03-07T14:30:00Z')
    assert s['2025-03-10']['open']==timestamp_ns('2025-03-10T13:30:00Z')
    assert (s['2024-12-24']['close']-s['2024-12-24']['open'])//MINUTE==210
    s.update(calendar_sessions([cal('2025-01-09')]))
    assert not independent_calendar_check(s)['regular_passed']


def test_forward_and_reverse_split_composition_and_invalid_ratios():
    raw=rows()[0]['payload']
    a=dict(effective_ns=OPEN-2*MINUTE,verified_publication_ns=OPEN-3*MINUTE,new_shares=10,old_shares=1,source_hash=digest('forward'))
    b=dict(a,effective_ns=OPEN,new_shares=1,old_shares=100,source_hash=digest('reverse'))
    r=split_basis(raw,[b,a],bar_ns=OPEN-4*MINUTE,as_of_ns=OPEN)
    assert Decimal(r['adjusted']['close'])==100 and Decimal(r['adjusted']['volume_shares'])==100
    with pytest.raises(ContractError,match='ratio'):split_basis(raw,[dict(a,old_shares=0)],bar_ns=OPEN-4*MINUTE,as_of_ns=OPEN)
