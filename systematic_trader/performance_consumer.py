"""Context-only performance integration. No data paths, booleans or callbacks."""
from decimal import Decimal

from .events import ContractError, digest
from .features import Session
from .performance_engine import evaluate_session
from .performance_scope import _activate, _TOKEN
from .research_engine import ExecutionPolicy
from .research_split import ResearchSplit
from .validation import session_statistics

D=Decimal
_COMPUTE_TOKEN=object()


def _compute(provenance, *, _token=None):
    """Internal replay calculation; never a public historical execution API."""
    if _token is not _COMPUTE_TOKEN:raise ContractError('verified_runner_performance_context_required')
    data=provenance['data'];experiment=provenance['experiment'];spec=experiment['specification']
    cells=provenance['certificate']['body']['cells']
    if spec['cells']!=cells or data['cells']!=cells or not cells:
        raise ContractError('performance_scope_mismatch')
    if data['normalized_hash']!=digest(data['normalized']):raise ContractError('performance_replay_hash_mismatch')
    if spec['consumer']!='performance-consumer-v1':raise ContractError('wrong_performance_consumer')
    # Future provider intake must produce this same bundle contract. Its admission
    # remains separate; a filename or user flag cannot enable historical intake.
    expected={(c['session'],c['symbol'],c['security_id']) for c in cells}
    if len(expected)!=len(cells):raise ContractError('duplicate_performance_cell')
    seen=set();outputs=[];statistics=[];skips=[];exposure=[]
    for bundle in data['normalized']:
        if bundle['version']!='performance-bundle-v1':raise ContractError('performance_bundle_version')
        session=Session(**bundle['session'])
        ResearchSplit().authorize(session.open_ns,session.close_ns,purpose='strategy_development')
        keys=[tuple(k) for k in bundle['keys']]
        entries=[e for u in bundle['universe'] for e in u['entries']]
        symbol_map={e['instrument_id']:e['symbol'] for e in entries}
        scoped={(session.session_id,symbol_map[k[3]],k[3]) for k in keys}
        if not scoped or not scoped<=expected or seen & scoped:raise ContractError('performance_bundle_scope_mismatch')
        if {e['instrument_id'] for e in entries}!={k[3] for k in keys}:raise ContractError('performance_universe_expansion')
        for c in cells:
            if (c['session'],c['symbol'],c['security_id']) in scoped and (c['start_ns']!=session.open_ns or c['end_ns']!=session.close_ns):
                raise ContractError('performance_session_bounds_mismatch')
        seen|=scoped
        for h in bundle['history']:
            if (h['provider'],h['feed'],h['origin'],h['instrument_id']) not in keys or h['known_ns']>=session.open_ns:
                raise ContractError('performance_history_scope_or_leakage')
        for event in bundle['events']:
            if event['event_type'].startswith('market.'):
                key=(event['provider'],event['feed'],event['origin'],event['instrument_id'])
                if (key not in keys or event['symbol']!=symbol_map[key[3]] or
                    not session.open_ns<=event['source_time_ns']<session.close_ns or
                    not session.open_ns<=event['received_ns']<session.close_ns):
                    raise ContractError('performance_event_scope_mismatch')
            elif event['event_type']=='reference.instrument':
                if event['payload']['instrument_id'] not in symbol_map:raise ContractError('performance_reference_expansion')
            else:raise ContractError('unsupported_performance_control_event')
        with _activate(bundle['events'],_TOKEN):
            output=evaluate_session(bundle['events'],session=session,daily_history=bundle['history'],
                universe_snapshots=bundle['universe'],keys=bundle['keys'],execution_policy=ExecutionPolicy(**spec['execution_assumptions']))
        if not output['discovery']:raise ContractError('performance_discovery_inputs_incomplete')
        simulation=output['simulation']
        if simulation['critical_gaps'] or simulation['ambiguities'] or simulation['residual_positions'] or simulation['pending_entries']:
            raise ContractError('incomplete_performance_exposure_or_ambiguity')
        exposure.append(dict(session=session.session_id,residual_positions=simulation['residual_positions'],
            pending_entries=simulation['pending_entries'],risk_audit=output['risk']))
        completed=simulation['completed']
        statistics.append(dict(session_id=session.session_id,net_r=str(sum((D(t['net_r']) for t in completed),D(0)))))
        for decision in output['decisions']:
            reasons=next((r['rejections'] for r in output['risk'] if r.get('decision_id')==decision['decision_id']),decision['rejections'])
            if not reasons and not any(f.get('decision_id')==decision['decision_id'] for f in completed):
                reasons=['no_completed_trade_see_fill_and_cancel_ledger']
            if reasons:skips.append(dict(session=session.session_id,symbol=decision['symbol'],reason=reasons,decision_id=decision['decision_id']))
        if not output['decisions']:skips.append(dict(session=session.session_id,reason=['no_eligible_breakout_intent'],symbols=sorted(symbol_map.values())))
        outputs.append(dict(session=session.session_id,output=output))
    if seen!=expected:raise ContractError('performance_missing_authorized_cell')
    trades=[dict(session=o['session'],**t) for o in outputs for t in o['output']['simulation']['completed']]
    fills=[dict(session=o['session'],**f) for o in outputs for f in o['output']['execution_audit']]
    net=sum((D(t['net_pnl']) for t in trades),D(0))
    fees=sum((D(f['quantity'])*D(spec['execution_assumptions']['fee_per_share']) for f in fills
        if f['type'] in {'simulated_entry_fill','simulated_exit_fill'}),D(0))
    result=dict(version='performance-output-v1',sessions=outputs,trade_ledger=trades,fill_ledger=fills,
        metrics=dict(gross_pnl=str(net+fees),net_pnl=str(net),modeled_fees=str(fees),trades=len(trades),
            wins=sum(D(t['net_pnl'])>0 for t in trades),losses=sum(D(t['net_pnl'])<0 for t in trades),
            flat=sum(D(t['net_pnl'])==0 for t in trades),
            session_statistics=session_statistics(statistics,seed=spec['statistics']['seed'],draws=spec['statistics']['draws']),
            percentage_return=None,percentage_drawdown=None),
        exposure=exposure,rejections_and_skips=skips,
        warnings=data['limitations']+['Gross P&L includes modeled slippage; net deducts modeled per-share fees',
            'No percentage-return or percentage-drawdown series is supported by this engine',
            'Synthetic outcomes carry no evidence of historical profitability'],
        production_eligible=False,execution_authority='none',orders_enabled=False)
    return result


def consume(handoff):
    from .certified_runner import _take_performance_handoff
    provenance=_take_performance_handoff(handoff)
    output=_compute(provenance,_token=_COMPUTE_TOKEN)
    artifact=dict(version='performance-result-v1',state='PERFORMANCE_COMPLETE',
        experiment_id=provenance['experiment']['experiment_hash'],certificate_id=provenance['certificate']['certificate_id'],
        frozen_specification_hash=digest(provenance['experiment']['specification']),
        evidence_replay_hash=provenance['data']['normalized_hash'],
        code_version=provenance['data']['context'],run_timestamp=provenance['run_timestamp'],
        authorized_cells=provenance['data']['cells'],execution_model_version=ExecutionPolicy().version,
        provenance=provenance,performance=output,performance_calculated=True,
        review_routing=dict(state='INDEPENDENT_REVIEW_REQUIRED',policy=provenance['experiment']['specification']['result_review_policy'],
            reasons=['all_completed_results_reviewed']+(['positive_net_pnl'] if D(output['metrics']['net_pnl'])>0 else [])),
        execution_authority='none',orders_enabled=False,production_eligible=False)
    return {**artifact,'result_hash':digest(artifact)}
