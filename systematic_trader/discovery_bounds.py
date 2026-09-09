"""Positive mover bounds from sparse observed bars; never invent negatives."""
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
from .events import ContractError,digest
from .research_panel import ResearchPanel


def observed_label(rows,opening_ns,threshold,*,complete_label=None,excluded=False):
    if excluded:return dict(state='unknown',reason='corporate_action_quality_exclusion')
    if complete_label is not None:return dict(state='positive' if complete_label else 'negative',reason='complete_session_label')
    opening=next((r for r in rows if r['stamp']==opening_ns and r['segment']=='regular'),None)
    if opening is None:return dict(state='unknown',reason='exact_opening_bar_unavailable')
    barrier=Decimal(opening['payload']['open'])*(1+Decimal(threshold))
    crossing=next((r['stamp'] for r in rows if r['segment']=='regular' and Decimal(r['payload']['high'])>=barrier),None)
    if crossing is None:return dict(state='unknown',reason='no_observed_crossing_in_incomplete_session')
    return dict(state='positive',reason='observed_positive_lower_bound',first_observed_crossing_ns=crossing,
        original_first_crossing_time='unknown_due_to_missing_intervals')


def recall_bounds(positive,detected_positive,unknown,surfaced_unknown):
    # Worst case: all undetected unknowns positive; surfaced unknowns negative.
    # Best case reverses those assignments. No unobserved price is generated.
    lower_denominator=positive+unknown-surfaced_unknown
    upper_denominator=positive+surfaced_unknown
    return [detected_positive/lower_denominator if lower_denominator else None,
            (detected_positive+surfaced_unknown)/upper_denominator if upper_denominator else None]


def evaluate(panel_directory,discovery_path,protocol_path):
    protocol=json.loads(Path(protocol_path).read_text());discovery=json.loads(Path(discovery_path).read_text())
    if protocol['protocol_hash']!=digest({k:v for k,v in protocol.items() if k!='protocol_hash'}):raise ContractError('coverage_bounds_protocol_integrity')
    if discovery['result_hash']!=digest({k:v for k,v in discovery.items() if k!='result_hash'}):raise ContractError('frozen_discovery_report_integrity')
    if protocol['parent_discovery_protocol']!=discovery['protocol']['protocol_hash']:raise ContractError('bounds_discovery_protocol_mismatch')
    panel=ResearchPanel(panel_directory);rows=[];summary={}
    try:
        for delay,episodes in discovery['episodes'].items():
            counts={t:{cohort:Counter() for cohort in ('all','catalog_sample','infrastructure_common_stock')} for t in protocol['unchanged_thresholds']}
            day=None;data={}
            for episode in episodes:
                if episode['day']!=day:day=episode['day'];data=panel.session(day)
                symbol=episode['symbol'];opened=panel.manifest['calendar'][day]['open']
                for threshold in protocol['unchanged_thresholds']:
                    if symbol in protocol['expected_absent_symbols']:
                        label=dict(state='expected_absent',reason='historical_delisting_or_later_ticker_name')
                    else:
                        known=episode['labels']['thresholds'][threshold]['positive'] if episode['labels'] else None
                        label=observed_label(data.get(symbol,[]),opened,threshold,complete_label=known,
                            excluded=episode['label_exclusion']=='corporate_action_lookback')
                    detected=episode['first_detection'] is not None
                    rows.append(dict(day=day,symbol=symbol,delay_minutes=delay,threshold=threshold,label=label,detected=detected))
                    for cohort in ('all',episode['cohort']):
                        counter=counts[threshold][cohort];counter[label['state']]+=1
                        if detected:counter['surfaced_'+label['state']]+=1
                        if label['reason']=='observed_positive_lower_bound':counter['sparse_observed_positive']+=1
            summary[delay]={}
            for threshold,cohorts in counts.items():
                summary[delay][threshold]={}
                for cohort,c in cohorts.items():
                    summary[delay][threshold][cohort]=dict(counts=dict(c),recall_bounds=recall_bounds(c['positive'],c['surfaced_positive'],c['unknown'],c['surfaced_unknown']),
                        precision_known_labels=c['surfaced_positive']/(c['surfaced_positive']+c['surfaced_negative']) if c['surfaced_positive']+c['surfaced_negative'] else None)
        body=dict(version='discovery-coverage-bounds-v1',protocol=protocol,parent_discovery_result_hash=discovery['result_hash'],
            summary=summary,labels=rows,scope='same_frozen_archive_panel_and_signals_additional_missing_label_bounds',
            discovery_policy_changed=False,performance_experiments_run=0,holdout='LOCKED_UNTOUCHED',execution_authority='none')
        return {**body,'result_hash':digest(body)}
    finally:panel.close()
