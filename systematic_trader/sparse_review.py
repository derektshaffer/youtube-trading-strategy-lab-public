"""Read-only integrity and error analysis for a frozen sparse discovery run."""
from collections import Counter
from decimal import Decimal
from pathlib import Path
import json
from .events import ContractError,digest
from .research_panel import file_hash
from .sparse_benchmark import verified_json


def verify_watchlists(path, expected):
    previous='0'*64;seen=set();count=0
    with Path(path).open() as handle:
        for line in handle:
            r=json.loads(line);key=(r['day'],r['delay_minutes'],r['decision_ns'])
            if key in seen:raise ContractError('duplicate_sparse_scan')
            seen.add(key)
            if r['previous_hash']!=previous or r['record_hash']!=digest({k:v for k,v in r.items() if k!='record_hash'}):
                raise ContractError('sparse_watchlist_chain_integrity')
            ranking=r['ranking']
            if ranking['ranking_hash']!=digest({k:v for k,v in ranking.items() if k!='ranking_hash'}):
                raise ContractError('sparse_ranking_integrity')
            previous=r['record_hash'];count+=1
    if previous!=expected['watchlist_chain_head'] or count!=expected['scan_count'] or file_hash(path)!=expected['watchlist_sha256']:
        raise ContractError('sparse_watchlist_manifest_integrity')
    return dict(scans=count,chain_head=previous,file_sha256=file_hash(path))


def review(directory):
    root=Path(directory);result=verified_json(root/'comparison.json','result_hash');pred=verified_json(root/'frozen-predictions.json','result_hash')
    if result['predictions_hash']!=pred['result_hash']:raise ContractError('sparse_prediction_result_link')
    verified=verify_watchlists(root/'watchlists.jsonl',pred);cases=[];traces={}
    for day,sha in pred['trace_hashes'].items():
        r=verified_json(root/(day+'-traces.json'),'result_hash')
        if r['result_hash']!=sha:raise ContractError('sparse_trace_integrity')
        traces[day]=r['traces']
    # Verify every saved first detection against its complete sequence of scan predicates.
    expected={}
    for day,groups in traces.items():
        for delay,symbols in groups.items():
            for symbol,rows in symbols.items():
                first=next((r for r in rows if r['rank'] is not None),None)
                if first:expected[(day,delay,symbol)]=first
    for det in pred['detections']:
        key=(det['day'],det['delay_minutes'],det['symbol']);d=det['detection'];first=expected.pop(key,None)
        if first is None or any(d[k]!=v for k,v in first.items()) or first['reasons'] or first['missing'] or first['fill_allowed']:
            raise ContractError('sparse_first_detection_trace_mismatch')
    if expected:raise ContractError('sparse_first_detection_omitted')
    for m in result['missed_extreme_movers']:
        rows=traces[m['day']][m['delay_minutes']][m['symbol']]
        if any(t['rank'] is not None for t in rows):raise ContractError('detected_extreme_mover_called_missed')
        usable=[t for t in rows if not t['missing']]
        liquid=[t for t in usable if Decimal(t['session_dollar_volume'])>=Decimal(result['protocol']['minimum_dollar_volume'])]
        volume=[t for t in liquid if not set(t['reasons'])&{'relative_volume_filter','same_session_volume_acceleration_filter','same_session_volume_comparison_unavailable'}]
        reason=('unresolved_input' if not usable else 'liquidity_not_met_with_usable_fresh_inputs' if not liquid else
                'unusual_volume_not_met_with_liquidity_and_fresh_inputs' if not volume else 'positive_continuation_not_jointly_met')
        taxonomy=set()
        for key in m['reason_counts']:
            taxonomy.add('missing_input' if 'coverage' in key else 'stale_data' if 'stale' in key else 'scan_timing' if 'six_completed' in key else
                'feature_threshold' if key.endswith('filter') or key=='positive_continuation_required' else key)
        dollars=[Decimal(t['session_dollar_volume']) for t in rows if t['session_dollar_volume'] is not None]
        rv=[Decimal(t['relative_volume']) for t in rows if t['relative_volume'] is not None]
        cases.append(dict(**m,taxonomy=sorted(taxonomy),joint_failure=reason,usable_scans=len(usable),liquid_usable_scans=len(liquid),volume_eligible_scans=len(volume),
            max_emitted_bar_dollar_proxy=str(max(dollars)) if dollars else None,max_rvol=str(max(rv)) if rv else None))
    body=dict(version='sparse-frozen-study-review-v1',result_hash_reviewed=result['result_hash'],verified_watchlists=verified,
        verified_first_detections=len(pred['detections']),cases=cases,
        unique_extreme_missed_symbol_days=len({(r['day'],r['symbol']) for r in cases}),
        no_missed_observed_20pct=not any(r['threshold']=='0.20' for r in cases),
        no_policy_change=True,execution_authority='none')
    return {**body,'review_hash':digest(body)}
