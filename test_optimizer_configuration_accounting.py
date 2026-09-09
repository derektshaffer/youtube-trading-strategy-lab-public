"""Accounting contracts only; stubbed P/L is never calibration evidence."""
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
import json
import pytest
import youtube_strategy_engine as e
from datetime import datetime, timedelta, timezone

ROWS = [{'t': (datetime(2026, 8, 17, 14, 0, tzinfo=timezone.utc) + timedelta(days=d, minutes=m)).isoformat(),
         'o': 3., 'h': 3.2, 'l': 2.9, 'c': 3.1, 'v': 1000} for d in range(5) for m in range(4)]
CONTROL = {'id': 'accounting-test', 'name': 'Accounting only', 'direction': 'long',
           'machine_rules': {'stop_loss_pct': 7.5, 'reward_risk': 2.}}
GOLD = {'current_result': {'metrics': {'trade_count': 10, 'net_pnl': 100., 'return_pct': 5.,
        'win_rate_pct': 60., 'profit_factor': 2., 'max_drawdown_pct': 1.},
        'trades': [], 'equity_curve': [], 'sessions': []}}


def signature(strategy, settings):
    return sha256(json.dumps({'strategy_id': strategy['id'],
        'rules': e.normalize_machine_rules(strategy['machine_rules']),
        'settings': asdict(settings)}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]


@pytest.mark.parametrize('finalize', [False, True])
def test_every_executed_configuration_is_counted_once_including_final_refinements(monkeypatch, finalize):
    observed = set()
    def execute(rows, strategy, symbol, settings, **kwargs):
        observed.add(signature(strategy, settings))
        result = deepcopy(GOLD['current_result'])
        # Rank the newly generated final rule so A/B exercises its legacy variant.
        result['metrics']['net_pnl'] += 100 * float(strategy['machine_rules'].get('stop_loss_pct') or 0)
        return result
    monkeypatch.setattr(e, 'run_backtest', execute)
    monkeypatch.setattr(e, '_period_metrics', lambda result, *a: deepcopy(result['metrics']))
    monkeypatch.setattr(e, 'generate_local_strategy_refinements',
        lambda rules, settings, maximum, stage: [e.normalize_machine_rules({**rules, 'stop_loss_pct': 9.5})] if stage == 'final' else [])
    monkeypatch.setattr(e, 'generate_local_execution_refinements',
        lambda settings, base, **kwargs: [replace(settings, risk_per_trade_pct=8.5)] * 2)
    settings=e.BacktestSettings(allow_price_extension_after_qualification=True)
    opt=e.OptimizationSettings(max_variants_per_strategy=2, finalists_per_strategy=2,
        optimize_position_sizing=False, automatic_slippage=False, max_execution_variants_per_finalist=1)
    report=e.optimize_stock_strategies(ROWS, [CONTROL], 'SDOT', settings, opt, finalize_holdout=finalize)
    history=report['configuration_history']
    assert {r['signature'] for r in history} == observed
    assert report['unique_configurations_tested'] == len(history) == len(observed)
    phases={p for r in history for p in r['phases']}
    assert {'final_rule_refinement', 'final_execution_refinement'} <= phases
    if finalize:
        assert {'behavior_comparison_legacy', 'behavior_comparison_optimized', 'final_holdout'} <= phases
        before=deepcopy(history)
        e.finalize_stock_optimization(report, ROWS, [CONTROL])
        assert report['configuration_history'] == before


def test_finalization_preserves_separate_interval_counts(monkeypatch):
    monkeypatch.setattr(e, 'run_backtest', lambda *a, **k: deepcopy(GOLD['current_result']))
    settings=e.legacy_behavior_settings(e.BacktestSettings())
    report={'symbol':'SDOT', 'timeframe':'5Min', 'holdout_sessions':[],
            'winner':{'source_strategy_id':CONTROL['id'], 'optimized_rules':CONTROL['machine_rules'],
                      'optimized_backtest_settings':asdict(settings), 'status':'LIMITED DATA'}}
    e.finalize_stock_optimization(report, ROWS, [CONTROL])
    record=deepcopy(report['configuration_history'][0]);record['timeframe']='1Min'
    report['configuration_history'].append(record);report['unique_configurations_tested']+=1
    e.finalize_stock_optimization(report, ROWS, [CONTROL])
    assert report['unique_configurations_tested'] == 2
    assert {r['timeframe'] for r in report['configuration_history']} == {'1Min','5Min'}
