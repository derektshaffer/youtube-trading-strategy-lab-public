"""Assess actual quote/trade coverage without synthesizing fills or status."""
from collections import Counter,defaultdict
from decimal import Decimal
import json
from pathlib import Path
from .data_acquisition import verified_pages
from .events import ContractError,digest,normalize_market,timestamp_ns
from .features import MINUTE
from .research_panel import ResearchPanel


def audit(panel_directory,export_directory,*,day='2025-01-02'):
    panel=ResearchPanel(panel_directory);c=panel.manifest['calendar'][day];data=panel.session(day)
    root=Path(export_directory);sources={};raw=defaultdict(list);summary={}
    try:
        for kind in ('quotes','trades'):
            manifest=json.loads((root/kind/'complete.json').read_text());params=manifest['params']
            if timestamp_ns(params['start'])>c['pre'] or timestamp_ns(params['end'])<c['post']-1:raise ContractError('execution_export_not_full_declared_session')
            sources[kind]=manifest
            for page,meta in verified_pages(root/kind):
                for symbol,rows in page[kind].items():
                    raw[(symbol,kind)].extend(rows)
        symbols=sorted({s for s,k in raw})
        for symbol in symbols:
            result={};clocks={};trades=defaultdict(list);bars={r['stamp']:r for r in data.get(symbol,[]) if r['segment']=='regular'}
            for kind in ('quotes','trades'):
                rows=raw[(symbol,kind)];fields=set();conditions=Counter();bad=0;stamps=[];spreads=[];sizes=[];previous=None;out_of_order=0
                for row in rows:
                    fields.update(row);conditions.update(row.get('c') or [])
                    try:
                        _,p,t,_=normalize_market(dict(T='q' if kind=='quotes' else 't',S=symbol,**row))
                        if previous is not None and t<previous:out_of_order+=1
                        previous=t;stamps.append(t)
                        if kind=='quotes':
                            bid,ask=Decimal(p['bid']),Decimal(p['ask'])
                            if 0<bid<ask:spreads.append((ask-bid)/((ask+bid)/2)*10000)
                            else:bad+=1
                            sizes.extend([p['bid_size'],p['ask_size']])
                        else:
                            trades[t//MINUTE*MINUTE].append(row);sizes.append(p['size_shares'])
                    except (ContractError,KeyError,TypeError):bad+=1
                clocks[kind]=set(stamps);spreads.sort();sizes.sort()
                result[kind]=dict(records=len(rows),fields=sorted(fields),conditions=dict(conditions),invalid_or_unusable=bad,out_of_order=out_of_order,
                    size_unit='provider_round_lots_unresolved_historical_lot_size' if kind=='quotes' else 'shares',
                    min_size=sizes[0] if sizes else None,max_size=sizes[-1] if sizes else None,
                    median_quoted_spread_bps=str(spreads[len(spreads)//2]) if spreads else None,
                    first_source_ns=min(stamps) if stamps else None,last_source_ns=max(stamps) if stamps else None)
            absence=Counter();volume_disagreement=0;missing=[]
            for minute in range(c['open'],c['close'],MINUTE):
                t=trades[minute];bar=bars.get(minute)
                if bar:
                    if sum(r['s'] for r in t)!=bar['payload']['volume_shares']:volume_disagreement+=1
                    continue
                reason='no_trades_in_complete_final_export' if not t else 'only_odd_lot_trades' if all('I' in r['c'] for r in t) else 'unresolved_condition_or_revision_gap'
                absence[reason]+=1;missing.append(dict(start_ns=minute,trades=len(t),classification='expected' if reason!='unresolved_condition_or_revision_gap' else 'exclusionary',reason=reason))
            result.update(missing_regular_minutes=dict(absence),missing_intervals=missing,
                all_print_volume_vs_bar_disagreement_minutes=volume_disagreement,
                volume_disagreement_interpretation='requires_condition_and_revision_reconciliation_not_automatic_data_corruption',
                cross_stream_equal_timestamps=len(clocks['quotes']&clocks['trades']),
                sequencing='per_stream_timestamp_order_only_no_original_arrival_or_cross_stream_total_order',
                fill_admission=False,halt_status='unknown_outside_positive_halt_evidence')
            summary[symbol]=result
        body=dict(version='execution-input-coverage-v1',day=day,sources=sources,symbols=summary,
            supported='one_full_extended_session_of_native_quote_trade_observations_for_three_symbols',
            unsupported=['full_development_period','verified_historical_quote_lot_sizes','original_arrival_latency','queue_priority','continuous_tradability','profitable_execution_claims'],
            tier1='screening_only_no_production_qualification',tier2='input_pilot_only_not_validation_admissible',execution_authority='none')
        return {**body,'result_hash':digest(body)}
    finally:panel.close()
