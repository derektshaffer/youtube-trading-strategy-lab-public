from copy import deepcopy

import pytest

from systematic_trader.events import ContractError, digest
from systematic_trader.qualification import QualificationProtocol, qualification_diagnostics

PROTOCOL=QualificationProtocol(registered_policies=('orb','no_trade','neighbor'),neighbors=('neighbor',),
    minimum_trades=6,minimum_sessions=6,minimum_instruments=3,minimum_regimes=2,minimum_periods=3)


def rows():
    return [dict(outcome_id=f'outcome-{i}',instrument_id=f'stable:{i%3}',session_id=f'day-{i}',
        start_ns=i*1000+100,end_ns=i*1000+200,label_end_ns=i*1000+250,available_ns=i*1000+300,
        selection_cutoff_ns=i*1000+99,split='out_of_sample',fold_id=f'fold-{i}',input_hash=digest(i),
        evidence='fixture',regime='trend' if i%2 else 'range',regime_known_ns=i*1000+90,period=f'period-{i//2}',
        policy_net_r={'orb':'1','no_trade':'0','neighbor':'0.8'},policy_trade_counts={'orb':1,'no_trade':0,'neighbor':1},
        execution_net_r={'double_costs':'0.5','one_second_arrival':'0.2'}) for i in range(6)]


def test_diagnostics_report_counts_sensitivity_baseline_and_never_promote():
    r=qualification_diagnostics(rows(),protocol=PROTOCOL)
    assert r['research_checks_passed']
    assert r['trade_count']==6 and r['session_count']==6 and r['policy_trials']==3
    assert not r['qualified'] and not r['production_eligible'] and r['execution_authority']=='none'
    assert 'fixture_not_performance_evidence' in r['promotion_blockers']
    assert 'multiple_testing_adjustment_unverified' in r['promotion_blockers']
    assert r==qualification_diagnostics(list(reversed(rows())),protocol=PROTOCOL)


def test_one_ticker_and_narrow_regime_cannot_pass_robustness():
    data=rows()
    for row in data:row.update(instrument_id='stable:only',regime='only',period='only')
    r=qualification_diagnostics(data,protocol=PROTOCOL)
    assert not r['research_checks_passed']
    assert {'minimum_instruments','ticker_trade_concentration','ticker_profit_concentration','regime_coverage','period_coverage'}<=set(r['failed_checks'])


def test_high_base_return_does_not_hide_stress_or_neighbor_failure():
    data=rows()
    for row in data:row['execution_net_r']['double_costs']='-1';row['policy_net_r']['neighbor']='-0.1'
    r=qualification_diagnostics(data,protocol=PROTOCOL)
    assert 'execution_sensitivity' in r['failed_checks']
    assert 'parameter_neighborhood_positive' in r['failed_checks']
    assert r['checks']['positive_candidate_return']


@pytest.mark.parametrize('change,reason',[
    (lambda r:r.update(split='final_holdout'),'oos_lineage'),
    (lambda r:r.update(split='training'),'oos_lineage'),
    (lambda r:r.update(selection_cutoff_ns=r['start_ns']),'not_frozen'),
    (lambda r:r.update(regime_known_ns=r['end_ns']),'asof_regime'),
    (lambda r:r.update(residual_exposure=True),'incomplete_simulation'),
    (lambda r:r['execution_net_r'].pop('double_costs'),'execution_coverage'),
    (lambda r:r['policy_net_r'].update(orb='NaN'),'invalid_qualification'),
    (lambda r:r['policy_trade_counts'].update(orb=0),'profit_without_trades'),
])
def test_leakage_missing_scenarios_or_invalid_outcomes_rejected(change,reason):
    data=rows();change(data[0])
    with pytest.raises(ContractError,match=reason):qualification_diagnostics(data,protocol=PROTOCOL)


def test_duplicate_renamed_or_shifted_window_cannot_inflate_samples():
    data=rows();data.append(deepcopy(data[0]))
    with pytest.raises(ContractError,match='outcome_id'):qualification_diagnostics(data,protocol=PROTOCOL)
    data[-1]['outcome_id']='renamed';data[-1]['start_ns']+=1
    with pytest.raises(ContractError,match='economic_window'):qualification_diagnostics(data,protocol=PROTOCOL)


def test_defaults_require_realistic_sample_size_and_declared_neighborhood():
    data=rows()
    for row in data:row['policy_net_r'].pop('neighbor');row['policy_trade_counts'].pop('neighbor')
    r=qualification_diagnostics(data)
    assert {'minimum_trades','minimum_sessions','minimum_instruments','parameter_neighborhood_declared'}<=set(r['failed_checks'])


def test_mixed_fixture_and_research_results_do_not_pool():
    data=rows();data[0]['evidence']='research_only'
    with pytest.raises(ContractError,match='mixed_qualification'):qualification_diagnostics(data,protocol=PROTOCOL)
