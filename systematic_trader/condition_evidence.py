"""Narrow documented exclusion evidence; never an execution eligibility policy.

Alpaca Market Data FAQ, minute aggregation: strictest condition wins. These
rules diagnose the final export; historical rule-publication timing is unknown.
"""
from collections import Counter,defaultdict
import json
from pathlib import Path
from .data_acquisition import verified_pages
from .events import ContractError,digest,timestamp_ns
from .features import MINUTE
from .research_panel import ResearchPanel

SOURCE='https://docs.alpaca.markets/us/docs/market-data-faq'


def excludes_minute_price(row):
    tape=row.get('z');conditions=set(row.get('c') or [])
    return (tape in {'A','B','C','O'} and 'I' in conditions or
            tape in {'A','B','C'} and '4' in conditions or tape in {'C','O'} and 'W' in conditions)


def excludes_volume(row):
    return row.get('z') in {'A','B','C'} and bool(set(row.get('c') or [])&{'M','Q','9'})


def reconcile(panel_directory,export_directory,*,day='2025-01-02'):
    panel=ResearchPanel(panel_directory);data=panel.session(day);c=panel.manifest['calendar'][day]
    root=Path(export_directory);trades=defaultdict(list);symbols=set()
    try:
        manifest=json.loads((root/'trades/complete.json').read_text())
        params=manifest['params'];start,end=timestamp_ns(params['start']),timestamp_ns(params['end'])
        if start>c['pre'] or end<c['post']-1:raise ContractError('condition_evidence_export_window_incomplete')
        symbols=set(params['symbols'].split(','))
        for page,meta in verified_pages(root/'trades'):
            for symbol,rows in page['trades'].items():
                if symbol not in symbols:raise ContractError('condition_evidence_unrequested_symbol')
                for r in rows:
                    stamp=timestamp_ns(r['t'])
                    if not start<=stamp<=end:raise ContractError('condition_evidence_time_outside_export_query')
                    trades[(symbol,stamp//MINUTE*MINUTE)].append(r)
        result={}
        for symbol in sorted(symbols):
            bars={r['stamp']:r for r in data.get(symbol,[]) if r['segment']=='regular'}
            minutes=[];counts=Counter()
            for t in range(c['open'],c['close'],MINUTE):
                rows=trades[(symbol,t)];bar=bars.get(t)
                reason='price_bar_present' if bar else 'no_trades_in_complete_final_export' if not rows else 'documented_non_price_forming_activity' if all(excludes_minute_price(r) for r in rows) else 'unresolved_missing_price_bar'
                counts[reason]+=1
                total=sum(r['s'] for r in rows);excluded=sum(r['s'] for r in rows if excludes_volume(r))
                match=bar is not None and total-excluded==bar['payload']['volume_shares']
                if bar and total!=bar['payload']['volume_shares']:
                    counts['volume_difference_explained_by_documented_exclusions' if match else 'unresolved_volume_difference']+=1
                minutes.append(dict(start_ns=t,reason=reason,source_print_count=len(rows),source_total_volume=total,
                    documented_excluded_volume=excluded,native_bar_volume=bar['payload']['volume_shares'] if bar else None,
                    native_bar_volume_matches_after_exclusions=match if bar else None,
                    conditions={','.join(k):v for k,v in Counter(tuple(r['c']) for r in rows).items()},
                    source_trade_rows_hash=digest(rows),native_bar_hash=bar['lineage']['normalized_payload_hash'] if bar else None))
            result[symbol]=dict(counts=dict(counts),minutes=minutes)
        body=dict(version='minute-condition-evidence-v1',day=day,source_documentation=SOURCE,trade_export=manifest,
            panel_manifest_hash=panel.manifest['manifest_hash'],symbols=result,
            raw_data_changed=False,imputed_ohlc_bars=0,production_condition_policy_approved=False,
            historical_rule_publication='unverified',execution_authority='none')
        return {**body,'result_hash':digest(body)}
    finally:panel.close()
