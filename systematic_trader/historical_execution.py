"""Bounded final-export evidence and explicit availability-clock diagnostics.

There is no fill engine here. A hypothetical delayed source clock is labelled
as such; it cannot stand in for observed arrival or certify tradability.
"""
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
from statistics import median

from .data_acquisition import verified_pages
from .events import ContractError, digest, normalize_market, timestamp_ns
from .features import MINUTE
from .research_check import save
from .selectivity_study import allowed_day
from .sparse_observations import trade_rule


@dataclass(frozen=True)
class AvailabilityClock:
    mode: str
    delay_ns: int = 0

    def __post_init__(self):
        if self.mode not in {'actual_application_receipt','assumed_source_delay'}:
            raise ContractError('historical_availability_mode_required')
        if type(self.delay_ns) is not int or self.delay_ns<0 or (self.mode=='actual_application_receipt' and self.delay_ns):
            raise ContractError('historical_availability_delay_invalid')

    def available(self, row):
        event=timestamp_ns(row['t'])
        if self.mode=='assumed_source_delay':return event+self.delay_ns
        receipt=row.get('original_application_receipt_ns')
        if receipt is None:return None
        if type(receipt) is not int or receipt<event:raise ContractError('historical_receipt_invalid')
        return receipt


class CausalQuoteIndex:
    """Quotes strictly available before a decision; ambiguous latest state fails.

    Arrival reordering is allowed only with explicit per-record receipt clocks.
    Source order violations in a declared sorted export fail. Equal availability
    with differing states has no inferred tie-break; identical duplicates do not
    advance age. Late stale source events do not regress the market state.
    """
    def __init__(self, quotes, clock):
        self.clock=clock;self.unknown=0;rows=[];previous=None
        for q in quotes:
            event=timestamp_ns(q['t'])
            if previous is not None and event<previous:raise ContractError('execution_native_order_regression')
            previous=event;available=clock.available(q)
            if available is None:self.unknown+=1;continue
            rows.append((available,event,digest(q),dict(q)))
        rows.sort(key=lambda r:r[:3]);self.times=[];self.states=[];state=[];i=0
        while i<len(rows):
            available=rows[i][0];group=[]
            while i<len(rows) and rows[i][0]==available:group.append(rows[i]);i+=1
            candidates=state+group;newest=max(r[1] for r in candidates)
            # Differing source timestamps at the same arrival instant still
            # have a causal source order. Same event time, different state does not.
            state=list({digest({k:v for k,v in r[3].items() if k!='original_application_receipt_ns'}):r
                        for r in candidates if r[1]==newest}.values())
            self.times.append(available);self.states.append(tuple(state))

    def context(self, decision_ns, *, max_age_ns):
        if type(decision_ns) is not int or type(max_age_ns) is not int or max_age_ns<0:
            raise ContractError('execution_decision_clock_invalid')
        base=dict(clock_mode=self.clock.mode,assumed_delay_ns=self.clock.delay_ns,
                  actual_arrival_verified=False,fill_allowed=False)
        index=bisect_left(self.times,decision_ns)-1
        if index<0:return dict(base,available=False,reason='no_strictly_available_quote',unknown_arrivals=self.unknown)
        state=self.states[index]
        if len(state)!=1:return dict(base,available=False,reason='ambiguous_quote_state')
        arrived,event,_,q=state[0]
        age=decision_ns-event
        if age>max_age_ns:return dict(base,available=False,reason='stale_quote',age_ns=age)
        _,p,_,_=normalize_market(dict(T='q',S='AUDIT',**{k:v for k,v in q.items() if k!='original_application_receipt_ns'}))
        bid,ask=Decimal(p['bid']),Decimal(p['ask'])
        if bid<=0 or ask<=bid or p['bid_size']<=0 or p['ask_size']<=0:
            return dict(base,available=False,reason='nonpositive_locked_crossed_or_empty_quote')
        base['actual_arrival_verified']=self.clock.mode=='actual_application_receipt'
        return dict(base,available=True,quote_event_ns=event,quote_available_ns=arrived,age_ns=age,
                    bid=str(bid),ask=str(ask),spread_bps=str((ask-bid)/((ask+bid)/2)*10000),
                    bid_size_native=p['bid_size'],ask_size_native=p['ask_size'],size_unit=p['size_unit'],
                    historical_round_lot_size=None,conditions=p['conditions'],raw_quote_hash=digest(q))


def load_export(directory, *, kind, day, symbols):
    """Validate scope before reading prices; then verify the entire page chain."""
    allowed_day(day);root=Path(directory);request=json.loads((root/'request.json').read_text());p=request['params']
    opened=timestamp_ns(day+'T09:30:00-05:00');closed=timestamp_ns(day+'T16:00:00-05:00')
    declared=set(p['symbols'].split(','))
    if (request['kind']!=kind or request['provider']!='alpaca' or p.get('feed')!='sip' or p.get('asof')!='-'
        or p.get('sort')!='asc' or not set(symbols)<=declared
        or timestamp_ns(p['start'])>opened or timestamp_ns(p['end'])<closed-1
        or timestamp_ns(p['start'])<timestamp_ns(day+'T00:00:00-05:00')
        or timestamp_ns(p['end'])>=timestamp_ns(day+'T23:59:59.999999999-05:00')):
        raise ContractError('execution_export_scope_mismatch')
    grouped=defaultdict(list);receipts=[]
    for page,meta in verified_pages(root):
        receipts.append(dict(raw_sha256=meta['sha256'],retrieved_ns=meta['retrieved_ns'],index=meta['index']))
        for symbol,rows in page[kind].items():
            if symbol not in declared:raise ContractError('execution_unrequested_symbol')
            if symbol not in symbols:continue
            for row in rows:
                t=timestamp_ns(row['t'])
                if not timestamp_ns(p['start'])<=t<=timestamp_ns(p['end']):raise ContractError('execution_record_outside_query')
                if opened<=t<closed:grouped[symbol].append(row)
    for rows in grouped.values():
        times=[timestamp_ns(r['t']) for r in rows]
        if times!=sorted(times):raise ContractError('execution_native_order_regression')
    manifest=json.loads((root/'complete.json').read_text())
    return grouped,dict(manifest=manifest,receipts=receipts)


def audit(history_root, protocol, directory):
    h=Path(history_root);out=Path(directory);out.mkdir(parents=True,exist_ok=False,mode=0o700)
    if protocol['protocol_hash']!=digest({k:v for k,v in protocol.items() if k!='protocol_hash'}):raise ContractError('execution_protocol_hash')
    symbols=protocol['symbols'];days=protocol['development_days'];sessions={};source_refs={}
    for day in days:
        allowed_day(day);first=day=='2025-01-02'
        qpath=h/'certification-v2/high-fidelity-2025-01-02/quotes' if first else h/'admission-v3/execution'/day/'quotes'
        tpath=h/'certification-v2/high-fidelity-2025-01-02/trades' if first else h/'sparse-v1/tape'/day/'trades'
        quotes,qr=load_export(qpath,kind='quotes',day=day,symbols=symbols)
        trades,tr=load_export(tpath,kind='trades',day=day,symbols=symbols)
        source_refs[day]=dict(quotes=qr,trades=tr);sessions[day]={}
        for symbol in symbols:
            q,t=quotes[symbol],trades[symbol];row={};normalized={}
            for kind,records in [('quotes',q),('trades',t)]:
                fields=Counter();conditions=Counter();minutes=Counter();native=[]
                for r in records:
                    fields.update(r.keys());conditions.update(r.get('c',[]));minutes[timestamp_ns(r['t'])//MINUTE]+=1
                    event,p,stamp,sequence=normalize_market(dict(T='q' if kind=='quotes' else 't',S=symbol,**r))
                    native.append(dict(event_type=event,payload=p,source_ns=stamp,sequence=sequence,raw_hash=digest(r)))
                normalized[kind]=digest(native)
                row[kind]=dict(count=len(records),exact_duplicates=len(records)-len({digest(r) for r in records}),
                    native_fields=dict(fields),conditions=dict(conditions),minutes_observed=len(minutes),session_minutes=390,
                    density_per_minute={str(m):n for m,n in sorted(minutes.items())},native_order_verified=True,
                    original_arrival_fields_present=any(k in fields for k in ('ts_recv','received_at','original_application_receipt_ns')),
                    correction_lineage_fields_present=any(k in fields for k in ('oi','ci','correction_id','cancel_id')))
            row['price_forming_trades']=sum(trade_rule(r)[0] is True for r in t)
            row['unknown_trade_rules']=sum(trade_rule(r)[0] is None for r in t)
            row['trade_size_median']=str(median(r['s'] for r in t)) if t else None
            # Fixed diagnostic scenarios, not tuned execution parameters. No P&L.
            row['clock_sensitivity']={}
            for mode,delay in [('actual_application_receipt',0),('assumed_source_delay',0),('assumed_source_delay',10**9),('assumed_source_delay',5*10**9)]:
                index=CausalQuoteIndex(q,AvailabilityClock(mode,delay))
                contexts=[index.context(timestamp_ns(r['t']),max_age_ns=30*10**9) for r in t]
                good=[c for c in contexts if c['available']]
                row['clock_sensitivity'][mode+':'+str(delay)]=dict(decisions=len(t),available_contexts=len(good),
                    rejections=dict(Counter(c['reason'] for c in contexts if not c['available'])),
                    median_spread_bps=str(median(Decimal(c['spread_bps']) for c in good)) if good else None,
                    median_age_ns=str(median(c['age_ns'] for c in good)) if good else None,
                    fill_allowed=False,scope='diagnostic_at_native_trade_event_time_not_strategy_decisions')
            row.update(normalized_hashes=normalized,query_exhausted=True,original_delivery_complete=False,
                       quote_size_unit='native_round_lots_historical_conversion_unverified',tradability='UNKNOWN',execution_qualified=False)
            sessions[day][symbol]=row
    body=dict(version='bounded-execution-evidence-v1',protocol_hash=protocol['protocol_hash'],symbols=symbols,days=days,
        sources=source_refs,sessions=sessions,performance_experiments=0,execution_qualified=False,
        missing=['security_and_action_lineage','continuous_status_and_luld','historical_quote_lot_size',
                 'quote_condition_execution_rules','original_revision_and_cancel_chain','actual_arrival_clock'],
        execution_authority='none')
    result={**body,'result_hash':digest(body)};save(out/'execution-evidence.json',result);return result
