"""Independent watchlist-based verification of saved persistence experiments."""
from collections import Counter
from decimal import Decimal
from pathlib import Path
import json
from .events import ContractError,digest
from .features import MINUTE
from .research_check import save
from .sparse_benchmark import verified_json
from .sparse_review import verify_watchlists
from .selectivity_study import allowed_day


def verify(history_root, directory):
    h=Path(history_root);root=Path(directory);b=h/'sparse-v1/calendar-benchmark'
    old=verified_json(b/'frozen-predictions.json','result_hash');verify_watchlists(b/'watchlists.jsonl',old)
    pred=verified_json(root/'study/frozen-predictions.json','result_hash')
    replay=verified_json(root/'replay/frozen-predictions.json','result_hash')
    result=verified_json(root/'study/comparison.json','result_hash')
    if pred!=replay or result!=verified_json(root/'replay/comparison.json','result_hash'):
        raise ContractError('selectivity_independent_replay_mismatch')
    traces={}
    for day,sha in old['trace_hashes'].items():
        allowed_day(day);r=verified_json(b/(day+'-traces.json'),'result_hash')
        if r['result_hash']!=sha:raise ContractError('selectivity_verifier_trace_hash')
        for delay,ss in r['traces'].items():
            for symbol,rows in ss.items():
                for row in rows:traces[(day,int(delay),symbol,row['decision_ns'])]=row
    previous={};count=0;head='0'*64;found={name:{} for name in pred['detections']}
    with (b/'watchlists.jsonl').open() as source,(root/'study/candidate-scans.jsonl').open() as candidates:
        for line in source:
            native=json.loads(line);current=json.loads(next(candidates));key=(native['day'],native['delay_minutes']);clock=native['decision_ns']
            prior=previous.get(key);expected={}
            for name in pred['detections']:
                selected=[]
                if prior and clock-prior['decision_ns']==5*MINUTE:
                    prev={r['symbol']:r for r in prior['ranking']['candidates']}
                    for rank,entry in enumerate(native['ranking']['candidates'],1):
                        symbol=entry['symbol']
                        if symbol not in prev:continue
                        now=traces[(*key,symbol,clock)];before=traces[(*key,symbol,prior['decision_ns'])]
                        if (now['rank']!=rank or entry['feature_hash']!=now['feature_hash']):raise ContractError('native_trace_rank_mismatch')
                        ok=(entry['lane']==prev[symbol]['lane'] and now['last_observed_bar_start_ns']>before['last_observed_bar_start_ns'])
                        if name=='persistence-price-2':ok=ok and Decimal(now['last_close'])>Decimal(before['last_close'])
                        if ok:
                            selected.append(dict(symbol=symbol,rank=rank,feature_hash=now['feature_hash'],previous_feature_hash=before['feature_hash']))
                            found[name].setdefault((*key,symbol),clock)
                expected[name]=selected
            body=dict(day=key[0],delay_minutes=key[1],decision_ns=clock,candidates=expected,previous_hash=head);head=digest(body)
            if current!={**body,'record_hash':head}:raise ContractError('independent_candidate_scan_mismatch')
            previous[key]=native;count+=1
        if next(candidates,None) is not None:raise ContractError('extra_candidate_scan')
    for name,rows in pred['detections'].items():
        actual={(r['day'],int(r['delay_minutes']),r['symbol']):r['detection']['decision_ns'] for r in rows}
        if actual!=found[name]:raise ContractError('independent_candidate_first_detection_mismatch')
    body=dict(version='independent-selectivity-verification-v1',scans_checked=count,first_detections={k:len(v) for k,v in found.items()},
        watchlist_based_reimplementation_equal=True,full_replay_equal=True,prediction_hash=pred['result_hash'],result_hash_verified=result['result_hash'],scan_head=head,
        no_candidate_decision_function_reused=True,execution_authority='none')
    report={**body,'verification_hash':digest(body)};save(root/'replay-verification.json',report);return report
