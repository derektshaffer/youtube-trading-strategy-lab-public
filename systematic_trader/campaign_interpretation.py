"""Append-only reporting correction; never recomputes trades, prices or P&L.

The first frozen campaign compared timestamp spellings in its quality diagnostic.
Its original code/result remain reproducible. This separate interpretation compares
instants without rounding, filling, changing thresholds or changing observations.
"""
from datetime import datetime,timezone
from pathlib import Path
import hashlib
import json

from .events import ContractError,digest,timestamp_ns
from .evidence_store import read_records
from .bounded_campaign import BoundedCampaign,classifications,campaign_hash

VERSION='campaign-instant-quality-v1'


def equivalent_timestamps(data):
    normalized=json.loads(json.dumps(data))
    for key,rows in normalized['cells'].items():
        for row in rows:
            original=row['t'];ns=timestamp_ns(original)
            text=datetime.fromtimestamp(ns//10**9,tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
            fraction=ns%10**9
            row['t']=text+('.'+f'{fraction:09d}' if fraction else '')+'Z'
            if timestamp_ns(row['t'])!=ns:raise ContractError('timestamp_instant_changed')
    return normalized


def interpret(campaign):
    records=campaign.audit.replay()
    original=next(r['body'] for r in records if r['kind']=='campaign_result')
    if original['code_hash']!=campaign_hash():raise ContractError('frozen_campaign_changed')
    rows=campaign.research.audit.replay()
    result=next(r['body'] for r in rows if r['key']=='result:'+original['run_id'])
    data=next(r['body']['data'] for r in rows if r['key']=='inputs:'+original['run_id'])
    if digest(data)!=result['input_hash']:raise ContractError('campaign_input_changed')
    computed=classifications(result,equivalent_timestamps(data),original['review'])
    if any(a['metrics']!=b['metrics'] for a,b in zip(computed['candidates'],original['candidates'])):
        raise ContractError('interpretation_cannot_change_performance')
    body=dict(version=VERSION,original_report_hash=original['report_hash'],result_hash=result['result_hash'],
        input_hash=result['input_hash'],correction_code_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        reason='Equivalent ISO-8601 timestamp spellings compared as exact instants; original string comparison falsely marked opening bars missing.',
        performance_recomputed=False,original_raw_and_results_preserved=True,report=computed)
    body['interpretation_hash']=digest(body)
    return body


def append_interpretation(campaign):
    computed=interpret(campaign)
    with campaign.audit.locked() as store:
        prior=[r['body'] for r in campaign.audit.records(store) if r['kind']=='interpretation_corrected']
        if prior:
            if prior!=[computed]:raise ContractError('interpretation_changed_new_explicit_revision_required')
            return prior[0]
        campaign.audit.append(store,'interpretation_corrected','instant-quality-correction',computed)
    return computed


def replay_interpretation(campaign):
    computed=interpret(campaign)
    saved=[r['body'] for r in campaign.audit.replay() if r['kind']=='interpretation_corrected']
    if saved!=[computed]:raise ContractError('interpretation_replay_mismatch')
    return dict(exact_match=True,interpretation_hash=computed['interpretation_hash'],performance_recomputed=False,orders_enabled=False)


def summary(directory):
    records=read_records(Path(directory)/'campaign','bounded-campaign-v1')
    corrections=[r['body'] for r in records if r['kind']=='interpretation_corrected']
    results=[r['body'] for r in records if r['kind']=='campaign_result']
    report=corrections[-1]['report'] if corrections else results[-1] if results else {}
    return dict(state='PRELIMINARY_RESEARCH_ONLY',completed_campaigns=len(results),
        candidates=[{'hypothesis_id':c['hypothesis_id'],'classification':c['classification']} for c in report.get('candidates',[])],
        interpretation_corrected=bool(corrections),certified=False,orders_enabled=False)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('directory');parser.add_argument('--replay',action='store_true');args=parser.parse_args()
    campaign=BoundedCampaign(args.directory)
    print(json.dumps(replay_interpretation(campaign) if args.replay else append_interpretation(campaign),indent=2))
