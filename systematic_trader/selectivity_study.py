"""Preregistered scan-persistence experiments; immutable baseline, no execution."""
from collections import Counter
from decimal import Decimal
from pathlib import Path
import json
import os
from .events import ContractError, canonical_json, digest
from .features import MINUTE
from .research_check import save
from .research_panel import file_hash
from .sparse_benchmark import verified_json
from .sparse_review import verify_watchlists
from .outcome_coverage import summarize, THRESHOLDS


def allowed_day(day):
    if not '2025-01-02' <= day <= '2025-02-28':
        raise ContractError('selectivity_development_date_required')


def confirm(previous, current, *, strict_price_increase=False):
    """Pure prefix rule: no outcome, later scan, or earlier session state."""
    if not previous or current['decision_ns']-previous['decision_ns'] != 5*MINUTE:
        return False
    for row in (previous, current):
        if row['rank'] is None or row['reasons'] or row['missing']:
            return False
        if row['feature_cutoff_ns'] > row['decision_ns']:
            raise ContractError('selectivity_future_feature')
        if row['last_observed_bar_start_ns'] is None or row['last_close'] is None:
            return False
        if row['last_observed_bar_start_ns']+MINUTE > row['feature_cutoff_ns']:
            raise ContractError('selectivity_future_price')
        price=Decimal(row['last_close'])
        if not price.is_finite() or price<=0:
            raise ContractError('selectivity_invalid_price')
    return (current['lane']==previous['lane'] and
            current['last_observed_bar_start_ns']>previous['last_observed_bar_start_ns'] and
            (not strict_price_increase or Decimal(current['last_close'])>Decimal(previous['last_close'])))


def load_protocol(path):
    p=verified_json(path,'protocol_hash')
    expected=[('persistence-2',False),('persistence-price-2',True)]
    if (p['version']!='discovery-selectivity-protocol-v1' or p['candidate_budget']!=2 or
        p['parameter_combinations']!=2 or p['scan_minutes']!=5 or p['delays']!=[0,2,5] or
        p['labels']!=list(THRESHOLDS) or
        [(c['id'],c['strict_price_increase']) for c in p['candidates']]!=expected or
        any(c['consecutive_scans']!=2 or not c['same_lane'] or not c['new_price_observation'] for c in p['candidates'])):
        raise ContractError('selectivity_unregistered_setting')
    for day in p['dates']: allowed_day(day)
    return p


def freeze(history_root, protocol_path, directory):
    """Finish every candidate prediction before any outcome file is opened."""
    h=Path(history_root);b=h/'sparse-v1/calendar-benchmark';p=load_protocol(protocol_path)
    pred=verified_json(b/'frozen-predictions.json','result_hash')
    if pred['result_hash']!=p['inputs']['predictions'] or sorted(pred['trace_hashes'])!=p['dates']:
        raise ContractError('selectivity_baseline_changed')
    verify_watchlists(b/'watchlists.jsonl',pred)
    root=Path(directory);root.mkdir(parents=True,exist_ok=False,mode=0o700)
    save(root/'registration.json',dict(protocol=p,source_sha256=file_hash(__file__),status='before_predictions_and_labels'))
    detections={c['id']:[] for c in p['candidates']};counts=Counter();head='0'*64
    with (root/'candidate-scans.jsonl').open('x') as log:
        for day,sha in sorted(pred['trace_hashes'].items()):
            allowed_day(day)
            daily=verified_json(b/(day+'-traces.json'),'result_hash')
            if daily['result_hash']!=sha:raise ContractError('selectivity_trace_changed')
            for delay in p['delays']:
                traces=daily['traces'][str(delay)]
                if set(traces)!=set(p['cohort']):raise ContractError('selectivity_cohort_changed')
                clocks=[r['decision_ns'] for r in next(iter(traces.values()))]
                if clocks!=sorted(set(clocks)) or any([r['decision_ns'] for r in rows]!=clocks for rows in traces.values()):
                    raise ContractError('selectivity_scan_inventory_mismatch')
                seen={c['id']:set() for c in p['candidates']}
                for i,clock in enumerate(clocks):
                    decisions={}
                    for candidate in p['candidates']:
                        selected=[]
                        for symbol,rows in traces.items():
                            current=rows[i];previous=rows[i-1] if i else None
                            if confirm(previous,current,strict_price_increase=candidate['strict_price_increase']):
                                selected.append(dict(symbol=symbol,rank=current['rank'],feature_hash=current['feature_hash'],previous_feature_hash=previous['feature_hash']))
                                if symbol not in seen[candidate['id']]:
                                    seen[candidate['id']].add(symbol)
                                    detections[candidate['id']].append(dict(day=day,symbol=symbol,delay_minutes=str(delay),
                                        detection=dict(**current,reference_bar_close=current['last_close'],reference_is_not_executable_price=True),
                                        previous_feature_hash=previous['feature_hash']))
                        decisions[candidate['id']]=sorted(selected,key=lambda r:r['rank'])
                        counts[candidate['id']]+=len(selected)
                    body=dict(day=day,delay_minutes=delay,decision_ns=clock,candidates=decisions,previous_hash=head)
                    head=digest(body);log.write(canonical_json({**body,'record_hash':head})+'\n')
        log.flush();os.fsync(log.fileno())
    body=dict(version='selectivity-predictions-v1',protocol_hash=p['protocol_hash'],baseline_prediction_hash=pred['result_hash'],
        detections=detections,scan_head=head,scan_sha256=file_hash(root/'candidate-scans.jsonl'),eligible_scan_counts=dict(counts),
        scope='fixed-panel development final-export scenario',execution_authority='none')
    result={**body,'result_hash':digest(body)};save(root/'frozen-predictions.json',result)
    return result


def criteria(candidate, baseline, success):
    checks={}
    for delay in ('0','2','5'):
        c=candidate[delay];b=baseline[delay];a=c['0.05']['all'];old=b['0.05']['all']
        tp=a['counts'].get('surfaced_positive',0);fp=a['counts'].get('surfaced_negative',0)
        checks[delay]=dict(recall_retained=Decimal(tp)>=Decimal(success['all_delays_min_true_positive_retention'])*old['counts'].get('surfaced_positive',0),
            false_positives_reduced=Decimal(fp)<=Decimal(success['all_delays_max_false_positive_retention'])*old['counts'].get('surfaced_negative',0),
            precision_bound_improved=bool(a['precision_bounds'] and a['precision_bounds'][0]>old['precision_bounds'][1]),
            ten_pct_retained=c['0.10']['all']['counts'].get('surfaced_positive',0)>=success['all_delays_min_10pct_true_positives'],
            twenty_pct_retained=c['0.20']['all']['counts'].get('surfaced_positive',0)>=success['all_delays_min_20pct_true_positives'])
    c=candidate['2']['0.05']['all']['counts']
    checks['two_minute_absolute']=dict(true_positives=c.get('surfaced_positive',0)>=success['two_minute_min_5pct_true_positives'],
        false_positives=c.get('surfaced_negative',0)<=success['two_minute_max_5pct_false_positives'])
    return dict(checks=checks,development_success=all(v for group in checks.values() for v in group.values()),
                validated_improvement=False,march_validation_used=False)


def evaluate(history_root, protocol_path, directory):
    h=Path(history_root);root=Path(directory);p=load_protocol(protocol_path)
    pred=verified_json(root/'frozen-predictions.json','result_hash')
    if pred['protocol_hash']!=p['protocol_hash'] or pred['scan_sha256']!=file_hash(root/'candidate-scans.jsonl'):
        raise ContractError('selectivity_predictions_changed')
    original=verified_json(h/'outcome-v1/certification/selectivity.json','result_hash')
    if original['result_hash']!=p['inputs']['outcomes']:raise ContractError('selectivity_outcomes_changed')
    rows=[]
    for day,sha in sorted(original['evidence_heads'].items()):
        allowed_day(day);d=verified_json(h/'outcome-v1/certification'/(day+'-outcomes.json'),'result_hash')
        if d['result_hash']!=sha:raise ContractError('selectivity_daily_outcomes_changed')
        rows.extend(d['rows'])
    results={};decisions={}
    for name,ds in pred['detections'].items():
        results[name]={}
        for delay in ('0','2','5'):
            dets={(r['day'],r['symbol']):r['detection'] for r in ds if r['delay_minutes']==delay}
            results[name][delay]={}
            for threshold in THRESHOLDS:
                subset=[r for r in rows if r['threshold']==threshold];groups={}
                for cohort in ('all','sparse','dense_control'):
                    groups[cohort]=summarize(subset,dets,cohort=cohort,sessions=len(p['dates']))
                    for lane in ('cold_start_same_session','full_history'):
                        groups[cohort+'/'+lane]=summarize(subset,dets,cohort=cohort,lane=lane,sessions=len(p['dates']))
                results[name][delay][threshold]=groups
        decisions[name]=criteria(results[name],original['summary'],p['success'])
    body=dict(version='selectivity-comparison-v1',protocol_hash=p['protocol_hash'],prediction_hash=pred['result_hash'],
        baseline_outcome_hash=original['result_hash'],baseline=original['summary'],candidates=results,decisions=decisions,
        discovery_versions_tested=2,parameter_combinations=2,performance_experiments=0,ml=False,
        march_validation_used=False,april_oos_used=False,may_june_holdout_used=False,production_eligible=False,execution_authority='none')
    result={**body,'result_hash':digest(body)};save(root/'comparison.json',result);return result
