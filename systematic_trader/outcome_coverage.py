"""Outcome-only certification of saved evidence; never calls discovery or providers."""
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
from statistics import median
from datetime import datetime
from zoneinfo import ZoneInfo

from .events import ContractError, digest, timestamp_ns
from .features import MINUTE
from .research_check import save
from .research_panel import ResearchPanel, file_hash
from .sparse_observations import SparsePanel, read_trade_day, trade_rule
from .sparse_benchmark import verified_json
from .sparse_review import verify_watchlists
from .discovery_bounds import recall_bounds

THRESHOLDS = ('0.05', '0.10', '0.20')
DENSE = {'AAPL', 'NVDA', 'AMD', 'TSLA', 'AMZN', 'META', 'MSFT'}


def audit_day(symbol, states, tape, coverage, opened, closed):
    """Validate exact full-session accounting and independently corroborate prices.

    tape is a minute-indexed mapping from an exhausted, hash-verified query.
    No synthesized OHLC is produced. Native opening/high observations are used.
    """
    expected = list(range(opened, closed, MINUTE))
    if [s['minute_start_ns'] for s in states] != expected:
        raise ContractError('outcome_minute_inventory_incomplete')
    if any(s['symbol'] != symbol for s in states):
        raise ContractError('outcome_symbol_mismatch')
    complete = bool(coverage and coverage.symbol == symbol and
                    coverage.start_ns <= opened and coverage.end_ns >= closed)
    problems = Counter()
    witnessed = []
    opening_price = None
    opening_corroborated = False
    observed = []
    for s in states:
        t = s['minute_start_ns']
        rows = sorted(tape.get(t, ()), key=lambda r: (timestamp_ns(r['t']), digest(r)))
        if complete:
            if s['source_hash'] != coverage.source_hash or s['trade_rows_hash'] != digest(rows):
                raise ContractError('outcome_tape_observation_link_mismatch')
            if any(not t <= timestamp_ns(r['t']) < t + MINUTE for r in rows):
                raise ContractError('outcome_print_outside_minute')
        rules = [trade_rule(r)[0] for r in rows]
        eligible = [r for r, rule in zip(rows, rules) if rule is True]
        if any(rule is None for rule in rules):
            problems['unknown_price_conditions'] += 1
        if s['coverage'] == 'unresolved':
            problems['unresolved_minute'] += 1
        problems.update(s['issues'])
        if s['action_sources']:
            problems['corporate_action_on_session'] += 1
        bar = s['bar']
        if bar:
            observed.append((t, Decimal(bar['payload']['high'])))
            if t == opened:
                opening_price = Decimal(bar['payload']['open'])
                if complete and eligible and not s['issues']:
                    first = min(timestamp_ns(r['t']) for r in eligible)
                    opening_corroborated = all(Decimal(str(r['p'])) == opening_price
                        for r in eligible if timestamp_ns(r['t']) == first)
            if complete and not s['issues'] and not any(rule is None for rule in rules):
                for r in eligible:
                    if Decimal(str(r['p'])) <= Decimal(bar['payload']['high']):
                        witnessed.append((timestamp_ns(r['t']), Decimal(str(r['p']))))
    if not complete:
        problems['full_regular_session_tape_unavailable'] += 1
    if opening_price is None:
        problems['exact_opening_bar_unavailable'] += 1
    elif not opening_corroborated:
        problems['opening_tape_agreement_unverified'] += 1
    full = complete and opening_corroborated and not problems
    peak = max((p for _, p in observed), default=None)
    peak_at = next((t for t, p in observed if p == peak), None)
    labels = {}
    for threshold in THRESHOLDS:
        barrier = opening_price * (1 + Decimal(threshold)) if opening_price is not None else None
        crossing = min((t for t, price in witnessed if barrier is not None and price >= barrier), default=None)
        observed_crossing = next((t for t, price in observed if barrier is not None and price >= barrier), None)
        labels[threshold] = dict(corroborated_crossing_ns=crossing,
                                 observed_crossing_minute_ns=observed_crossing,
                                 barrier=str(barrier) if barrier is not None else None)
    evidence = dict(symbol=symbol,full_price_coverage=full,complete_query=complete,
        opening_corroborated=opening_corroborated,opening_price=str(opening_price) if opening_price is not None else None,
        expected_minutes=len(expected),problems=dict(problems),thresholds=labels,
        documented_absent_minutes=sum(s['coverage']=='documented_absence' for s in states),
        observed_peak=str(peak) if peak is not None else None,observed_peak_minute_ns=peak_at,
        observations_hash=digest(states),tape_source_hash=coverage.source_hash if coverage else None,
        expected_identity_absence=all(s['identity'] in {'prelisting','post_delisting','wrong_era_literal_symbol'}
            and s['identity_source'] for s in states) and not observed,
        original_available_ns=None,execution_authority='none')
    return {**evidence,'evidence_hash':digest(evidence)}


def classify(original, evidence, threshold):
    """Prior benchmark labels remain immutable and explicitly distinguishable."""
    if threshold not in THRESHOLDS:
        raise ContractError('outcome_threshold_not_preregistered')
    state = original['state']
    if state != 'unknown':
        return dict(state=state,reason='inherited_frozen_label',original_reason=original['reason'],
                    basis='original_complete_session' if original['reason']=='complete_session_label' else 'original_observed_lower_bound_or_absence',
                    newly_certified=False)
    if original['reason'] == 'corporate_action_quality_exclusion':
        return dict(state='unknown',reason='preserved_corporate_action_quality_exclusion',newly_certified=False)
    if evidence['expected_identity_absence']:
        return dict(state='expected_absent',reason='sourced_historical_identity_absence',newly_certified=True)
    if not evidence['opening_corroborated']:
        return dict(state='unknown',reason='exact_opening_price_not_corroborated',newly_certified=False)
    crossing = evidence['thresholds'][threshold]['corroborated_crossing_ns']
    if crossing is not None and not evidence['problems'].get('corporate_action_on_session'):
        return dict(state='positive',reason='corroborated_price_forming_crossing',crossing_ns=crossing,
                    full_price_coverage=evidence['full_price_coverage'],newly_certified=True)
    if evidence['full_price_coverage']:
        return dict(state='negative',reason='complete_tape_price_forming_interval_without_crossing',newly_certified=True)
    return dict(state='unknown',reason='price_coverage_insufficient_to_exclude_crossing',newly_certified=False)


def summarize(rows, detections, *, cohort='all', lane=None, sessions=39):
    counts = Counter()
    timing = []
    hours = Counter()
    lane_days = Counter()
    for row in rows:
        dense = row['symbol'] in DENSE
        if cohort == 'sparse' and dense or cohort == 'dense_control' and not dense:
            continue
        det = detections.get((row['day'], row['symbol']))
        if lane is not None and det and det['lane'] != lane:
            det = None
        state = row['label']['state']
        counts[state] += 1
        if not det:
            counts['not_surfaced_'+state] += 1
            continue
        counts['surfaced_'+state] += 1
        lane_days[det['lane']] += 1
        clock = datetime.fromtimestamp(det['decision_ns']//10**9, ZoneInfo('America/New_York'))
        hours[f'{clock.hour:02d}:00'] += 1
        if state == 'positive':
            e = row['evidence'];cross = e['thresholds'][row['threshold']]['observed_crossing_minute_ns']
            if cross is not None:
                peak, opened, ref = Decimal(e['observed_peak']), Decimal(e['opening_price']), Decimal(det['reference_bar_close'])
                remaining = (float((peak-ref)/(peak-opened)*100) if peak>opened and
                             det['decision_ns']<=e['observed_peak_minute_ns'] else 0)
                timing.append(dict(day=row['day'],symbol=row['symbol'],rank=det['rank'],
                    lead_minutes=(cross-det['decision_ns'])/MINUTE,observed_move_remaining_pct=remaining,
                    source_age_upper_bound_ns=det['staleness_upper_bound_ns'],
                    full_price_coverage=e['full_price_coverage'],reference_is_not_fill=True))
    tp, fp, us = (counts['surfaced_'+s] for s in ('positive','negative','unknown'))
    alerts = tp+fp+us+counts['surfaced_expected_absent']
    positives, unknown = counts['positive'],counts['unknown']
    return dict(counts=dict(counts),certified_positive_recall=tp/positives if positives else None,
        recall_bounds=recall_bounds(positives,tp,unknown,us),
        precision_known_labels=tp/(tp+fp) if tp+fp else None,
        precision_if_identified=tp/alerts if alerts and not us else None,
        precision_bounds=[tp/alerts,(tp+us)/alerts] if alerts else None,
        signaled_symbol_days=alerts,alerts_per_session=alerts/sessions,false_positives_per_session=fp/sessions,
        lane_first_detections=dict(lane_days),first_detection_hours_et=dict(hours),
        median_lead_minutes=median(r['lead_minutes'] for r in timing) if timing else None,
        median_observed_move_remaining_pct=median(r['observed_move_remaining_pct'] for r in timing) if timing else None,
        median_first_rank=median(r['rank'] for r in timing) if timing else None,timing_details=timing,
        lane_recall_denominator='all_cohort_positives' if lane else None)


def run(history_root, protocol_path, directory):
    r = Path(history_root);old=r/'certification-v2';sparse=r/'sparse-v1'
    protocol=verified_json(protocol_path,'protocol_hash')
    if protocol['version']!='outcome-coverage-protocol-v1' or protocol['thresholds']!=list(THRESHOLDS):
        raise ContractError('outcome_protocol_mismatch')
    if file_hash(Path(__file__).with_name('sparse_observations.py'))!=protocol['condition_implementation_sha256']:
        raise ContractError('outcome_condition_implementation_changed')
    baseline=verified_json(sparse/'calendar-benchmark/comparison.json','result_hash')
    pred=verified_json(sparse/'calendar-benchmark/frozen-predictions.json','result_hash')
    labels=verified_json(old/'discovery-coverage-bounds.json','result_hash')
    panel=ResearchPanel(old/'panel');observations=SparsePanel(sparse/'observations-with-state')
    actual=dict(baseline=baseline['result_hash'],predictions=pred['result_hash'],observations=observations.manifest['manifest_hash'],
                panel=panel.manifest['manifest_hash'],frozen_labels=labels['result_hash'],discovery_protocol=baseline['protocol']['protocol_hash'])
    if actual!=protocol['inputs']:
        panel.close();raise ContractError('outcome_frozen_input_changed')
    verify_watchlists(sparse/'calendar-benchmark/watchlists.jsonl',pred)
    root=Path(directory);root.mkdir(parents=True,exist_ok=False,mode=0o700)
    save(root/'registration.json',dict(protocol=protocol,status='registered_before_classification',source_sha256=file_hash(__file__)))
    original={};decisions={str(d):{} for d in protocol['delays']}
    for row in labels['labels']:
        key=(row['day'],row['symbol'],row['threshold'])
        if key in original and original[key]!=row['label']:
            raise ContractError('outcome_label_depends_on_delay')
        original[key]=row['label']
    for row in pred['detections']:
        decisions[row['delay_minutes']][(row['day'],row['symbol'])]=row['detection']
    new=[];evidence_heads={}
    try:
        for day in sorted({k[0] for k in original}):
            calendar=panel.manifest['calendar'];c=calendar[day];states=observations.session(day)
            grouped,coverage,manifest=read_trade_day(sparse/'tape'/day/'trades',day,calendar)
            if manifest!=observations.manifest['tape_sources'][day]:
                raise ContractError('outcome_tape_manifest_changed')
            daily=[]
            for symbol in panel.manifest['symbols']:
                tape={t:rows for (s,t),rows in grouped.items() if s==symbol}
                evidence=audit_day(symbol,states[symbol],tape,coverage.get(symbol),c['open'],c['close'])
                for threshold in THRESHOLDS:
                    prior=original[(day,symbol,threshold)]
                    daily.append(dict(day=day,symbol=symbol,threshold=threshold,original=prior,
                                      label=classify(prior,evidence,threshold),evidence=evidence))
            body=dict(day=day,rows=daily,tape_manifest_hash=digest(manifest))
            result={**body,'result_hash':digest(body)};save(root/(day+'-outcomes.json'),result)
            evidence_heads[day]=result['result_hash'];new.extend(daily)
            print(json.dumps(dict(day=day,outcomes_classified=len(daily))),flush=True)
        summary={};unknown_transitions={}
        for delay,dets in decisions.items():
            summary[delay]={};unknown_transitions[delay]={}
            for threshold in THRESHOLDS:
                subset=[row for row in new if row['threshold']==threshold]
                groups={}
                for cohort in ('all','sparse','dense_control'):
                    groups[cohort]=summarize(subset,dets,cohort=cohort)
                    for lane in ('cold_start_same_session','full_history'):
                        groups[cohort+'/'+lane]=summarize(subset,dets,cohort=cohort,lane=lane)
                summary[delay][threshold]=groups
                unknown_transitions[delay][threshold]=dict(Counter(row['label']['state'] for row in subset
                    if row['original']['state']=='unknown' and (row['day'],row['symbol']) in dets))
        body=dict(version='frozen-discovery-outcome-certification-v1',protocol=protocol,
            evidence_heads=evidence_heads,summary=summary,unknown_signaled_transitions=unknown_transitions,
            source_prediction_hash=pred['result_hash'],source_watchlist_sha256=pred['watchlist_sha256'],
            baseline_result_hash=baseline['result_hash'],classification_count=len(new),
            scope='CONDITIONAL_ON_FIXED_PANEL_FINAL_EXPORTS',discovery_rerun=False,threshold_changes=False,
            performance_experiments=0,parameter_searches=0,holdout='LOCKED_UNREQUESTED',execution_authority='none')
        result={**body,'result_hash':digest(body)};save(root/'selectivity.json',result);return result
    finally:
        panel.close()
