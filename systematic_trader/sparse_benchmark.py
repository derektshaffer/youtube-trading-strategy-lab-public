"""Frozen sparse repair evaluation. Freeze ALL watchlists before opening labels."""
from collections import Counter,defaultdict
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
import json
import os
from pathlib import Path
from statistics import median
from .bounded_discovery import daily_profile,prefix_snapshot
from .discovery_bounds import recall_bounds
from .events import ContractError,canonical_json,digest
from .features import MINUTE,Session
from .halt_history import parse_halts,status_bound
from .momentum import MomentumPolicy
from .research_check import save
from .research_panel import ResearchPanel,admission,file_hash
from .sparse_discovery import SparsePolicy,profile,snapshot,rank
from .sparse_calendar import comparable_indices
from .sparse_observations import SparsePanel,reference_overlay


def verified_json(path, hash_key):
    row=json.loads(Path(path).read_text())
    if row[hash_key]!=digest({k:v for k,v in row.items() if k!=hash_key}):raise ContractError('sparse_study_artifact_integrity')
    return row


def load_halts(directory):
    root=Path(directory);m=verified_json(root/'complete.json','manifest_hash');records=[]
    for day,sha in sorted(m['heads'].items()):
        if file_hash(root/(day+'.raw.xml'))!=sha:raise ContractError('sparse_halt_source_integrity')
        records.extend(parse_halts((root/(day+'.raw.xml')).read_bytes(),requested_date=day))
    return records


def summarize_fixed(labels, detections, cohort):
    c=Counter();details=[]
    for row in labels:
        s=row['symbol'];group='infrastructure_common_stock' if s in {'AAPL','NVDA','AMD','TSLA','AMZN','META','MSFT'} else 'catalog_sample'
        if cohort!='all' and group!=cohort:continue
        det=detections.get((row['delay_minutes'],row['day'],s));state=row['label']['state'];c[state]+=1
        if det:c['surfaced_'+state]+=1
        if row['label']['reason']=='observed_positive_lower_bound':c['sparse_observed_positive']+=1
        details.append((row,det))
    positives=c['positive'];unknown=c['unknown'];tp=c['surfaced_positive'];fp=c['surfaced_negative'];us=c['surfaced_unknown'];total=tp+fp+us+c['surfaced_expected_absent']
    return dict(counts=dict(c),observed_positive_recall=tp/positives if positives else None,
        recall_bounds=recall_bounds(positives,tp,unknown,us),precision_known_labels=tp/(tp+fp) if tp+fp else None,
        precision_bounds=[tp/total,(tp+us)/total] if total else None,
        false_positive_burden_per_session=fp/39,total_signaled_symbol_days=total)


def run(panel_directory,observation_directory,old_directory,protocol_path,directory):
    p=verified_json(protocol_path,'protocol_hash');old=Path(old_directory)
    parent=verified_json(old/'discovery-protocol.json','protocol_hash')
    if p['parent_discovery_protocol']!=parent['protocol_hash'] or p['delays']!=parent['delay_minutes'] or p['scan_minutes']!=parent['scan_every_minutes']:
        raise ContractError('sparse_parent_protocol_changed')
    policy=MomentumPolicy();sp=SparsePolicy();calendar_correction=p.get('calendar_comparable_history',False)
    if calendar_correction and p['version']!='sparse-repair-calendar-protocol-v2':raise ContractError('unregistered_calendar_correction')
    if (p['max_anchor_age_minutes']!=sp.max_anchor_age_minutes or p['max_decision_age_minutes']!=sp.max_decision_age_minutes or
        p['cold_start_ratio']!=sp.cold_start_ratio or p['minimum_rvol']!=policy.minimum_relative_volume or
        p['minimum_price']!=policy.minimum_price or p['minimum_dollar_volume']!=policy.minimum_dollar_volume or
        p['full_history_sessions']!=policy.minimum_history_sessions or p['limit']!=policy.limit or
        p['thresholds']!=parent['labels']['up_from_regular_open'] or p['parameter_search_budget']!=0 or p['label_changes']):
        raise ContractError('sparse_implementation_policy_not_frozen')
    panel=ResearchPanel(panel_directory);sparse=SparsePanel(observation_directory);m=panel.manifest
    if p['panel_manifest_hash']!=m['manifest_hash'] or sparse.manifest['panel_manifest_hash']!=m['manifest_hash']:
        raise ContractError('sparse_panel_source_mismatch')
    root=Path(directory);root.mkdir(parents=True,exist_ok=False,mode=0o700)
    admitted=admission(m,experiment='sparse_repair_v1',purpose='conditional_discovery',explicit_export_assumptions=True)
    if not admitted['admitted']:raise ContractError('sparse_discovery_admission_rejected')
    save(root/'registration.json',dict(protocol=p,admission=admitted,observations_hash=sparse.manifest['manifest_hash'],
        code_hash=digest({f.name:file_hash(f) for f in sorted(Path(__file__).parent.glob('*.py'))}),status='registered_before_predictions'))
    halts=load_halts(old/'halts-development');by_halt=defaultdict(list)
    for r in halts:by_halt[r['symbol']].append(r)
    legacy_history=defaultdict(list);sparse_history=defaultdict(list);past_days=[];detections={};daily_trace={};chain='0'*64;scans=0;allreasons=Counter();routes=Counter()
    log=root/'watchlists.jsonl'
    try:
        with log.open('x') as h:
            log.chmod(0o600)
            for day,c in sorted(m['calendar'].items()):
                if day>'2025-02-28':continue
                session=Session(day,c['open'],c['close'],c['source_hash']);data=panel.session(day);raw_states=sparse.session(day)
                if not sparse.manifest.get('reference_overlay_included'):raise ContractError('sparse_reference_overlay_required')
                states=raw_states
                earlier={s:deepcopy(legacy_history[s][-14:]) for s in m['symbols']};hist={s:deepcopy(sparse_history[s][-14:]) for s in m['symbols']}
                first=past_days[-14] if len(past_days)>=14 else past_days[0] if past_days else day
                boundaries={s:[] for s in m['symbols']}
                for a in m['corporate_actions']:
                    r=a['record'];effective=r.get('ex_date') or r.get('effective_date') or r.get('process_date')
                    if effective and first<=effective<=day:
                        for s in m['symbols']:
                            if s in r.values():boundaries[s].append(digest(a))
                traces={str(delay):{s:[] for s in m['symbols']} for delay in p['delays']}
                if day>='2025-01-02':
                    for delay in p['delays']:
                        for minute in range(p['scan_minutes'],(c['close']-c['open'])//MINUTE,p['scan_minutes']):
                            elapsed=minute-delay
                            if elapsed<1:continue
                            cutoff=c['open']+elapsed*MINUTE;decision=c['open']+minute*MINUTE;snapshots=[]
                            scan_hist=hist;scan_boundaries=boundaries
                            if calendar_correction:
                                indices=comparable_indices(past_days,m['calendar'],before_day=day,elapsed=elapsed)
                                scan_hist={s:[sparse_history[s][i] for i in indices] for s in m['symbols']}
                                earliest=past_days[indices[0]] if indices else day
                                scan_boundaries={s:[] for s in m['symbols']}
                                for a in m['corporate_actions']:
                                    ar=a['record'];effective=ar.get('ex_date') or ar.get('effective_date') or ar.get('process_date')
                                    if effective and earliest<=effective<=day:
                                        for s in m['symbols']:
                                            if s in ar.values():scan_boundaries[s].append(digest(a))
                            for s in m['symbols']:
                                original=prefix_snapshot(s,data.get(s,[]),earlier[s],session,elapsed=elapsed,action_boundary=bool(scan_boundaries[s]))
                                # Preserve the exact original dense full-history path wherever it was valid.
                                if not original['missing']:
                                    f=original;f.update(decision_ns=decision,lane='full_history');f['feature_hash']=digest({k:v for k,v in f.items() if k!='feature_hash'});route='legacy_dense'
                                else:
                                    f=snapshot(s,states[s],scan_hist[s],session,cutoff=cutoff,decision=decision,action_boundary=bool(scan_boundaries[s]));route=f['lane']
                                routes[route]+=1;snapshots.append(f)
                            ranking=rank(snapshots);by_s={s['symbol']:s for s in snapshots};ranks={r['symbol']:i for i,r in enumerate(ranking['candidates'],1)}
                            reasons={x['symbol']:x['reasons'] for x in ranking['excluded']}
                            for s,f in by_s.items():
                                v=f['values'];found=s in ranks;why=reasons.get(s,[])
                                for reason in why:allreasons[reason]+=1
                                status=status_bound(by_halt[s],s,decision)
                                trace=dict(decision_ns=decision,feature_cutoff_ns=cutoff,rank=ranks.get(s),reasons=why,lane=f['lane'],
                                    history_sessions=f.get('history_sessions',14),last_close=v.get('last_close'),
                                    last_observed_bar_start_ns=v.get('last_observed_bar_start_ns',cutoff-MINUTE if v.get('last_close') else None),
                                    staleness_upper_bound_ns=v.get('staleness_upper_bound_ns',decision-cutoff+MINUTE if v.get('last_close') else None),
                                    relative_volume=v.get('relative_volume_at_time'),same_session_volume_acceleration=v.get('volume_acceleration'),
                                    continuation=v.get('return_3m'),session_dollar_volume=v.get('session_dollar_volume'),
                                    missing=f['missing'],limitations=f.get('limitations',[]),action_sources=scan_boundaries[s],
                                    tradability='HALTED' if status['halted'] else 'UNKNOWN',halt_sources=status['sources'],fill_allowed=False,feature_hash=f['feature_hash'])
                                traces[str(delay)][s].append(trace)
                                key=(str(delay),day,s)
                                if found and key not in detections:
                                    detections[key]=dict(**trace,reference_bar_close=v['last_close'],reference_is_not_executable_price=True)
                            entry=dict(day=day,delay_minutes=delay,decision_ns=decision,ranking=ranking,previous_hash=chain)
                            chain=digest(entry);h.write(canonical_json({**entry,'record_hash':chain})+'\n');scans+=1
                    h.flush();os.fsync(h.fileno())
                    body=dict(day=day,traces=traces);result={**body,'result_hash':digest(body)};save(root/(day+'-traces.json'),result);daily_trace[day]=result['result_hash']
                    print(json.dumps(dict(day=day,scans=scans,detected=sum(k[1]==day for k in detections))),flush=True)
                for s in m['symbols']:
                    legacy_history[s].append(daily_profile(data.get(s,[]),session));sparse_history[s].append(profile(states[s],session))
                past_days.append(day)
        frozen=dict(scan_count=scans,watchlist_chain_head=chain,watchlist_sha256=file_hash(log),trace_hashes=daily_trace,
            detections=[dict(delay_minutes=k[0],day=k[1],symbol=k[2],detection=v) for k,v in sorted(detections.items())])
        save(root/'frozen-predictions.json',{**frozen,'result_hash':digest(frozen)})
        # Only now open outcome artifacts, after EVERY development prediction is durable.
        labels=verified_json(old/'discovery-coverage-bounds.json','result_hash');legacy=verified_json(old/'discovery-development-v2/discovery-results.json','result_hash')
        if labels['protocol']['protocol_hash']!=p['parent_bounds_protocol'] or labels['parent_discovery_result_hash']!=legacy['result_hash']:
            raise ContractError('sparse_frozen_labels_changed')
        summary={};comparison={};timings={};misses=[]
        old_detections={(d,e['day'],e['symbol']):e['first_detection'] for d,rows in legacy['episodes'].items() for e in rows if e['first_detection']}
        original_episodes={(d,e['day'],e['symbol']):e for d,rows in legacy['episodes'].items() for e in rows}
        for delay in map(str,p['delays']):
            summary[delay]={};comparison[delay]={};timings[delay]={}
            for threshold in p['thresholds']:
                subset=[r for r in labels['labels'] if r['delay_minutes']==delay and r['threshold']==threshold]
                summary[delay][threshold]={cohort:summarize_fixed(subset,detections,cohort) for cohort in ('all','catalog_sample','infrastructure_common_stock')}
                comparison[delay][threshold]={cohort:summarize_fixed(subset,old_detections,cohort) for cohort in ('all','catalog_sample','infrastructure_common_stock')}
                timing=[]
                for r in subset:
                    if r['label']['state']!='positive':continue
                    key=(delay,r['day'],r['symbol']);det=detections.get(key);episode=original_episodes[key];known=episode['labels']
                    crossing=known['thresholds'][threshold]['crossing_start_ns'] if known else r['label']['first_observed_crossing_ns']
                    if det:
                        data=panel.session(r['day']);bars=[x for x in data[r['symbol']] if x['segment']=='regular'];opened=next(x for x in bars if x['stamp']==m['calendar'][r['day']]['open'])
                        peak=max(Decimal(x['payload']['high']) for x in bars);open_price=Decimal(opened['payload']['open']);ref=Decimal(det['reference_bar_close']);peak_at=next(x['stamp'] for x in bars if Decimal(x['payload']['high'])==peak)
                        timing.append(dict(day=r['day'],symbol=r['symbol'],first_detection=det,
                            observed_crossing_ns=crossing,lead_to_observed_crossing_minutes=(crossing-det['decision_ns'])/MINUTE,
                            timing_evidence='complete_session_bar_time' if known else 'observed_crossing_only_true_first_crossing_unknown',
                            observed_move_remaining_pct=float((peak-ref)/(peak-open_price)*100) if peak>open_price and det['decision_ns']<=peak_at else 0,
                            remaining_is_not_capture_or_true_eventual_peak=not bool(known),peak_observed=str(peak)))
                    elif threshold in {'0.10','0.20'}:
                        trace=verified_json(root/(r['day']+'-traces.json'),'result_hash')['traces'][delay][r['symbol']]
                        counts=Counter(reason for t in trace for reason in t['reasons']);before=[t for t in trace if t['decision_ns']<=crossing]
                        misses.append(dict(day=r['day'],symbol=r['symbol'],threshold=threshold,delay_minutes=delay,
                            label=r['label'],scan_count=len(trace),before_observed_crossing=len(before),reason_counts=dict(counts),
                            pre_crossing_reason_counts=dict(Counter(reason for t in before for reason in t['reasons'])),
                            constraints_explained='all_saved_scan_predicates; overlapping_reasons_not_exclusive_causes',
                            trace_hash=daily_trace[r['day']],identity='unverified_archive_series',tradability='UNKNOWN',fill_allowed=False))
                timings[delay][threshold]=dict(detections=timing,median_lead_to_observed_crossing_minutes=median(x['lead_to_observed_crossing_minutes'] for x in timing) if timing else None,
                    median_observed_move_remaining_pct=median(x['observed_move_remaining_pct'] for x in timing) if timing else None,
                    median_first_rank=median(x['first_detection']['rank'] for x in timing) if timing else None)
        body=dict(version='sparse-discovery-comparison-v2' if calendar_correction else 'sparse-discovery-comparison-v1',protocol=p,observation_manifest_hash=sparse.manifest['manifest_hash'],
            frozen_label_hash=labels['result_hash'],old_discovery_hash=legacy['result_hash'],predictions_hash=digest(frozen),
            scan_count=scans,watchlist_chain_head=chain,summary=summary,old_summary=comparison,timings=timings,missed_extreme_movers=misses,
            route_counts=dict(routes),exclusion_counts=dict(allreasons),performance_experiments=0,parameter_search_budget=0,
            labels_changed=False,holdout='LOCKED_UNREQUESTED',scope='conditional_fixed_panel_final_export_diagnostic',
            original_arrival_verified=False,production_eligible=False,execution_authority='none')
        result={**body,'result_hash':digest(body)};save(root/'comparison.json',result);return result
    finally:panel.close()
