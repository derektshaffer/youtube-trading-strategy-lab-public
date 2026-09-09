"""Conditional prefix-causal diagnostics, using the existing momentum policy.

This is not a reconstruction of original arrivals or a trading backtester.
Final-export and retrospective-panel assumptions are explicit in every report.
Future labels are computed only after each session's watchlists are frozen.
"""
from collections import Counter,defaultdict
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
from statistics import median
from .events import ContractError,canonical_json,digest
from .features import MINUTE,Session,Universe
from .momentum import MomentumPolicy,intraday_values,rank_movers
from .research_check import save
from .research_panel import ResearchPanel,admission,bar_findings,file_hash
from .validation import ExperimentStore

D=Decimal


def prefix_snapshot(symbol,rows,prior,session,*,elapsed,policy=MomentumPolicy(),action_boundary=False):
    """Only the completed prefix and past sessions enter shared arithmetic."""
    cutoff=session.open_ns+elapsed*MINUTE
    by_time={r['stamp']:r for r in rows if session.open_ns<=r['stamp']<cutoff}
    prefix=[by_time.get(t) for t in range(session.open_ns,cutoff,MINUTE)]
    missing=[]
    if elapsed<6:missing.append('six_completed_minutes_required')
    if any(r is None for r in prefix):missing.append('intraday_bar_coverage_incomplete')
    selected=[deepcopy(h) for h in prior if h is not None and h['close_ns']<session.open_ns and h['known_ns']<=cutoff]
    selected=selected[-policy.minimum_history_sessions:]
    if len(selected)<policy.minimum_history_sessions:missing.append('insufficient_complete_prior_sessions')
    if any(len(h['minute_volumes'])<elapsed for h in selected):missing.append('historical_time_bucket_unavailable')
    if action_boundary:missing.append('corporate_action_lookback_excluded')
    p=[r['payload'] for r in prefix] if prefix and all(r is not None for r in prefix) else []
    if any(bar_findings(x) for x in p):missing.append('bar_quality_requires_reconciliation')
    values,arithmetic_missing=intraday_values(p,selected,elapsed)
    missing.extend(arithmetic_missing)
    body=dict(version='conditional-archive-prefix-v1',policy=asdict(policy),key=['alpaca','sip','import','archive-series:'+symbol],
        symbol=symbol,session=asdict(session),as_of_ns=cutoff,values=values,missing=sorted(set(missing)),
        prefix_hash=digest([(r['stamp'],r['payload']) for r in prefix if r]),history_hash=digest(selected),
        execution_missing=['historical_identity_unverified','market_status_unknown_or_halted','original_arrival_unverified','quotes_not_connected'],
        evidence='conditional_final_export_archive_panel',production_eligible=False,execution_authority='none')
    return {**body,'feature_hash':digest(body)}


def daily_profile(rows,session):
    by_time={r['stamp']:r for r in rows if r['segment']=='regular'}
    selected=[by_time.get(t) for t in range(session.open_ns,session.close_ns,MINUTE)]
    if not selected or any(r is None or bar_findings(r['payload']) for r in selected):return None
    p=[r['payload'] for r in selected]
    return dict(session_id=session.session_id,close_ns=session.close_ns,known_ns=session.close_ns,
        high=str(max(D(x['high']) for x in p)),low=str(min(D(x['low']) for x in p)),close=p[-1]['close'],open=p[0]['open'],
        minute_volumes=[x['volume_shares'] for x in p],volume=sum(x['volume_shares'] for x in p),
        dollar_volume=str(sum(D(x['vwap'])*x['volume_shares'] for x in p)),source_hash=digest([(r['stamp'],r['payload']) for r in selected]))


def evaluation_labels(rows,profile,prior,session):
    """Future outcome labels; never passed to prefix_snapshot or rank_movers."""
    if profile is None:return None
    p=[r for r in rows if r['segment']=='regular'];opened=D(profile['open']);peak=max(D(r['payload']['high']) for r in p)
    peak_start=next(r['stamp'] for r in p if D(r['payload']['high'])==peak)
    result=dict(open=str(opened),peak=str(peak),peak_start_ns=peak_start,maximum_up_from_open=str(peak/opened-1),
        range_fraction=str((D(profile['high'])-D(profile['low']))/opened),thresholds={})
    result['move_begin_ns']=next((r['stamp'] for r in p if D(r['payload']['high'])>=opened*D('1.01')),None)
    for threshold in ('0.05','0.10','0.20'):
        crossing=next((r['stamp'] for r in p if D(r['payload']['high'])>=opened*(1+D(threshold))),None)
        result['thresholds'][threshold]=dict(positive=crossing is not None,crossing_start_ns=crossing,
            crossing_end_ns=crossing+MINUTE if crossing is not None else None)
    valid=[h for h in prior if h]
    expected=sum(D(h['volume']) for h in valid)/len(valid) if len(valid)==14 else None
    rv=D(profile['volume'])/expected if expected else None
    result.update(daily_relative_volume=str(rv) if rv is not None else None,extreme_relative_volume=rv>=4 if rv is not None else None,
                  large_range=D(result['range_fraction'])>=D('0.20'))
    return result


def summarize(episodes):
    output={}
    for threshold in ('0.05','0.10','0.20','large_range','extreme_relative_volume'):
        known=[];unknown=[]
        for e in episodes:
            label=e['labels'];value=(label['thresholds'][threshold]['positive'] if label and threshold in label['thresholds'] else label.get(threshold) if label else None)
            (unknown if value is None else known).append((e,value))
        positives=[e for e,v in known if v];surfaced=[e for e,v in known if e['first_detection']]
        true=[e for e,v in known if v and e['first_detection']];false=[e for e,v in known if not v and e['first_detection']]
        unknown_signals=sum(bool(e['first_detection']) for e,v in unknown)
        early=[];leads=[];remaining=[];already=[]
        for e in true:
            d=e['first_detection'];label=e['labels'];cross=label['thresholds'].get(threshold,{}).get('crossing_start_ns')
            if cross is not None:
                leads.append((cross-d['decision_ns'])/MINUTE)
                if d['decision_ns']<cross:early.append(e)
            denom=D(label['peak'])-D(label['open'])
            if denom>0:
                already.append(float((D(d['reference_bar_close'])-D(label['open']))/denom*100))
                remaining.append(float((D(label['peak'])-D(d['reference_bar_close']))/denom*100) if d['decision_ns']<=label['peak_start_ns'] else 0.0)
        total_signals=len(surfaced)+unknown_signals
        output[threshold]=dict(known_label_symbol_sessions=len(known),unknown_label_symbol_sessions=len(unknown),meaningful_movers=len(positives),
            surfaced_known_labels=len(surfaced),true_positives=len(true),false_positives=len(false),surfaced_unknown_labels=unknown_signals,
            recall=len(true)/len(positives) if positives else None,
            precision=len(true)/len(surfaced) if surfaced else None,
            precision_bounds_including_unknown=[len(true)/total_signals,(len(true)+unknown_signals)/total_signals] if total_signals else None,
            early_recall=len(early)/len(positives) if positives and threshold.startswith('0.') else None,
            lead_time_minutes_median_lower_bound=median(leads) if leads else None,
            eventual_move_already_completed_pct_median=median(already) if already else None,
            eventual_move_remaining_pct_median=median(remaining) if remaining else None,
            false_positive_burden_per_session=len(false)/len({e['day'] for e in episodes}) if episodes else None)
    return output


def run(panel_directory,protocol_path,output_directory):
    protocol=json.loads(Path(protocol_path).read_text())
    if protocol['protocol_hash']!=digest({k:v for k,v in protocol.items() if k!='protocol_hash'}):raise ContractError('discovery_protocol_integrity')
    if protocol['policy']!=asdict(MomentumPolicy()) or protocol['phase']!='development_only':raise ContractError('unfrozen_discovery_policy_or_phase')
    panel=ResearchPanel(panel_directory);m=panel.manifest
    admission_result=admission(m,experiment='frozen_momentum_diagnostic',purpose='conditional_discovery',explicit_export_assumptions=True)
    if not admission_result['admitted']:raise ContractError('conditional_discovery_admission_failed')
    root=Path(output_directory);root.mkdir(parents=True,exist_ok=False,mode=0o700)
    store=ExperimentStore(root/'experiments.sqlite3');policy=MomentumPolicy()
    registered={**protocol,'policies':[policy.version],'holdout_sessions':['reserved-period:2025-05-01/2025-06-30']}
    experiment=store.register(registered,dict(origin='import',sha256=m['manifest_hash']))
    store.append('dataset_admission',experiment+':admission',admission_result)
    history=defaultdict(list);episodes={str(d):[] for d in protocol['delay_minutes']};exclusions=Counter();chain='0'*64;scans=0
    days=[d for d in sorted(m['calendar']) if d<='2025-02-28'];actions=m['corporate_actions']
    log=root/'frozen-watchlists.jsonl'
    try:
        with log.open('x') as handle:
            log.chmod(0o600)
            for day in days:
                c=m['calendar'][day];session=Session(day,c['open'],c['close'],c['source_hash']);data=panel.session(day)
                prior={s:deepcopy(history[s][-14:]) for s in m['symbols']}
                boundaries={s:False for s in m['symbols']}
                for s in m['symbols']:
                    first=next((h['session_id'] for h in prior[s] if h),day)
                    for action in actions:
                        r=action['record'];effective=r.get('ex_date') or r.get('effective_date') or r.get('process_date')
                        if effective and first<=effective<=day and s in r.values():boundaries[s]=True
                day_detections={str(d):{} for d in protocol['delay_minutes']}
                if day>='2025-01-02':
                    for delay in protocol['delay_minutes']:
                        for minute in range(protocol['scan_every_minutes'],(session.close_ns-session.open_ns)//MINUTE,protocol['scan_every_minutes']):
                            elapsed=minute-delay
                            if elapsed<1:continue
                            cutoff=session.open_ns+elapsed*MINUTE;decision=session.open_ns+minute*MINUTE
                            snapshots=[prefix_snapshot(s,data.get(s,[]),prior[s],session,elapsed=elapsed,policy=policy,action_boundary=boundaries[s]) for s in m['symbols']]
                            universe=Universe();universe.observe([dict(instrument_id='archive-series:'+s,symbol=s,active=True,asset_type='common_stock') for s in m['symbols']],
                                known_ns=session.open_ns,effective_ns=session.open_ns,source_hash=digest(['fixed_archive_panel_not_market_membership',m['symbols']]))
                            ranking=rank_movers(snapshots,universe,session,as_of_ns=cutoff,policy=policy)
                            for item in ranking['excluded']:
                                for reason in item['reasons']:exclusions[reason]+=1
                            by_symbol={s['symbol']:s for s in snapshots}
                            for rank,candidate in enumerate(ranking['candidates'],1):
                                symbol=candidate['symbol'];v=by_symbol[symbol]['values']
                                if symbol not in day_detections[str(delay)]:
                                    day_detections[str(delay)][symbol]=dict(decision_ns=decision,feature_cutoff_ns=cutoff,
                                        reference_bar_close=v['last_close'],reference_is_not_executable_price=True,rank=rank,
                                        signals=candidate['signals'],relative_volume=v['relative_volume_at_time'],gap=v.get('gap_from_prior_close'),
                                        session_dollar_volume=v['session_dollar_volume'],feature_hash=candidate['feature_hash'])
                            entry=dict(day=day,delay_minutes=delay,decision_ns=decision,ranking=ranking,previous_hash=chain)
                            chain=digest(entry);handle.write(canonical_json({**entry,'record_hash':chain})+'\n');scans+=1
                    handle.flush();os.fsync(handle.fileno())  # Freeze predictions before computing future outcome labels.
                profiles={s:daily_profile(data.get(s,[]),session) for s in [*m['symbols'],'SPY']}
                if day>='2025-01-02':
                    spy=[h for h in history['SPY'][-14:] if h]
                    regime='unknown' if len(spy)<14 else 'prior_SPY_14session_up' if D(spy[-1]['close'])>=D(spy[0]['close']) else 'prior_SPY_14session_down'
                    for symbol in m['symbols']:
                        labels=None if boundaries[symbol] else evaluation_labels(data.get(symbol,[]),profiles[symbol],prior[symbol],session)
                        for delay in protocol['delay_minutes']:
                            detection=day_detections[str(delay)].get(symbol)
                            first_bar=next((r for r in data.get(symbol,[]) if r['stamp']==session.open_ns),None)
                            px=D(first_bar['payload']['open']) if first_bar else None
                            bucket='unknown' if px is None else 'under_5' if px<5 else '5_to_20' if px<20 else '20_to_100' if px<100 else '100_plus'
                            tod=(detection['decision_ns']-session.open_ns)/MINUTE if detection else None
                            gap=D(detection['gap']) if detection and detection['gap'] is not None else None
                            rv=D(detection['relative_volume']) if detection else None
                            prior_dollars=[D(h['dollar_volume']) for h in prior[symbol] if h]
                            liquid=median(prior_dollars) if len(prior_dollars)==14 else None
                            episodes[str(delay)].append(dict(day=day,symbol=symbol,first_detection=detection,labels=labels,regime=regime,price_bucket=bucket,
                                detection_time_bucket='not_surfaced' if tod is None else 'first_30_minutes' if tod<30 else '30_to_120_minutes' if tod<120 else '120_to_270_minutes' if tod<270 else 'last_120_minutes',
                                detection_gap_bucket='unknown_or_not_surfaced' if gap is None else 'down_5pct_plus' if gap<=D('-.05') else 'up_5pct_plus' if gap>=D('.05') else 'within_5pct',
                                detection_rvol_bucket='not_surfaced' if rv is None else '2_to_4' if rv<4 else '4_plus',
                                prior_dollar_liquidity='unknown' if liquid is None else 'under_1m' if liquid<1_000_000 else '1m_to_10m' if liquid<10_000_000 else '10m_plus',
                                cohort='infrastructure_common_stock' if symbol in {'AAPL','NVDA','AMD','TSLA','AMZN','META','MSFT'} else 'catalog_sample',
                                market_cap='unavailable',session_segment='regular',label_exclusion='corporate_action_lookback' if boundaries[symbol] else 'incomplete_or_anomalous_session' if labels is None else None))
                for symbol,profile in profiles.items():history[symbol].append(profile)
                if day>='2025-01-02':print(json.dumps(dict(day=day,scans=scans,detections={d:len(v) for d,v in day_detections.items()})),flush=True)
        summaries={d:summarize(rows) for d,rows in episodes.items()};breakdowns={}
        for delay,rows in episodes.items():
            groups={}
            for dimension in ('cohort','regime','price_bucket','symbol','detection_time_bucket','detection_gap_bucket','detection_rvol_bucket','prior_dollar_liquidity'):
                groups[dimension]={value:summarize([e for e in rows if e[dimension]==value]) for value in sorted({e[dimension] for e in rows})}
            breakdowns[delay]=groups
        result=dict(version='bounded-discovery-results-v1',protocol=protocol,panel_manifest_hash=m['manifest_hash'],admission=admission_result,
            discovery_result='CONDITIONAL_PREFIX_CAUSAL_DIAGNOSTIC_NOT_VERIFIED_LIVE_ARRIVAL',summaries=summaries,breakdowns=breakdowns,
            episodes=episodes,scan_count=scans,scan_exclusion_counts=dict(exclusions),watchlist_chain_head=chain,watchlist_file_sha256=file_hash(log),
            performance_experiments_run=0,parameter_combinations_searched=0,holdout_result='LOCKED_NOT_REQUESTED_NOT_EVALUATED',
            premarket_result='NOT_EVALUATED_SPARSE_PROFILE_AND_MEMBERSHIP_UNRESOLVED',
            execution_authority='none',production_eligible=False,limitations=m['limitations'])
        result['result_hash']=digest(result)
        store.append('conditional_discovery_result',experiment+':result',dict(result_hash=result['result_hash'],scans=scans,performance_experiments_run=0))
        save(root/'experiment-audit.json',store.verify());save(root/'discovery-results.json',result)
        return result
    finally:store.close();panel.close()
