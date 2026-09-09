"""Detection-prefix error analysis and native execution-input audit; never fills."""
from bisect import bisect_left
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from statistics import median
from datetime import datetime
from zoneinfo import ZoneInfo
import json
from .data_acquisition import verified_pages
from .events import ContractError, digest, timestamp_ns
from .features import MINUTE
from .research_check import save
from .sparse_benchmark import verified_json
from .sparse_observations import SparsePanel, trade_rule
from .sparse_discovery import available_prefix
from .selectivity_study import allowed_day

D=Decimal


def prefix_features(states, detection, previous_states=()):
    cutoff=detection['feature_cutoff_ns'];decision=detection['decision_ns']
    if cutoff>decision:raise ContractError('diagnostic_future_cutoff')
    prefix=available_prefix(states,cutoff,decision)
    if not prefix or any(s['coverage']=='unresolved' for s in prefix):
        raise ContractError('diagnostic_prefix_unresolved')
    bars=[s['bar'] for s in prefix if s['bar'] and not s['issues']]
    if not bars or bars[-1]['payload']['close']!=detection['last_close']:
        raise ContractError('diagnostic_reference_price_mismatch')
    p=[b['payload'] for b in bars];last=D(p[-1]['close']);v=sum(x['volume_shares'] for x in p)
    dollars=sum(D(x['vwap'])*x['volume_shares'] for x in p);high=max(D(x['high']) for x in p)
    by={s['minute_start_ns']:s for s in prefix}
    def volume(start,end):
        seq=[by.get(t) for t in range(start,end,MINUTE)]
        return sum(s['emitted_bar_volume'] for s in seq) if all(s and s['emitted_bar_volume'] is not None for s in seq) else None
    denominator=volume(cutoff-6*MINUTE,cutoff-3*MINUTE);numerator=volume(cutoff-3*MINUTE,cutoff)
    recent=[s for s in prefix if s['minute_start_ns']>=cutoff-6*MINUTE]
    trade_count=sum(s['eligible_price_prints'] for s in recent) if len(recent)==6 and all(s['source_hash'] and s['eligible_price_prints'] is not None for s in recent) else None
    exact_open=prefix[0]['bar'];opened=D(exact_open['payload']['open']) if exact_open else None
    previous=[s for s in previous_states if s['minute_end_ns']<=prefix[0]['minute_start_ns']]
    usable_prior=bool(previous and not any(s['coverage']=='unresolved' or s['action_sources'] for s in previous) and not detection['action_sources'])
    prevbars=[s['bar'] for s in previous if s['bar'] and not s['issues']]
    priorclose=D(prevbars[-1]['payload']['close']) if usable_prior and prevbars and previous[-1]['minute_end_ns']-prevbars[-1]['stamp']<=5*MINUTE else None
    prioropen=D(previous[0]['bar']['payload']['open']) if usable_prior and previous[0]['bar'] else None
    priorhigh=max((D(b['payload']['high']) for b in bars[:-1]),default=None)
    tail=[s['bar']['payload'] if s['bar'] else None for s in recent]
    expansion=None
    if len(tail)==6 and all(tail):
        oldrange=sum(D(x['high'])-D(x['low']) for x in tail[:3])
        if oldrange:expansion=sum(D(x['high'])-D(x['low']) for x in tail[3:])/oldrange
    def string(value):return str(value) if value is not None else None
    body=dict(lane=detection['lane'],decision_ns=decision,feature_cutoff_ns=cutoff,
        hour_et=datetime.fromtimestamp(decision//10**9,ZoneInfo('America/New_York')).strftime('%H'),
        relative_volume=detection['relative_volume'],volume_acceleration=detection['same_session_volume_acceleration'],
        prior_3m_volume=denominator,recent_3m_volume=numerator,continuation=detection['continuation'],
        session_dollar_volume=detection['session_dollar_volume'],staleness_upper_bound_minutes=detection['staleness_upper_bound_ns']/MINUTE,
        price_forming_prints_last_6m=trade_count,price_forming_bar_minutes_last_6m=sum(bool(s['bar']) for s in recent),
        opening_move=string(last/opened-1 if opened else None),vwap_distance=string(last/(dollars/v)-1 if dollars and v else None),
        pullback_from_high=string((high-last)/high),breakout=bool(last>priorhigh) if priorhigh else None,
        gap=string(opened/priorclose-1 if opened and priorclose else None),prior_session_return=string(priorclose/prioropen-1 if priorclose and prioropen else None),
        range_expansion=string(expansion),spread_bps=None,spread_missing_reason='not_connected_to_frozen_detection; no synthetic quote',
        prefix_hash=digest([s['state_hash'] for s in prefix]),prior_source_hash=digest([s['state_hash'] for s in previous]),
        original_arrival_verified=False,execution_authority='none')
    return {**body,'diagnostic_hash':digest(body)}


def error_analysis(history_root, directory):
    h=Path(history_root);out=Path(directory);out.mkdir(parents=True,exist_ok=False,mode=0o700)
    pred=verified_json(h/'sparse-v1/calendar-benchmark/frozen-predictions.json','result_hash')
    sparse=SparsePanel(h/'sparse-v1/observations-with-state')
    calendar=json.loads((h/'certification-v2/panel/manifest.json').read_text())['calendar'];days=sorted(calendar)
    features=[]
    for day in sorted(pred['trace_hashes']):
        allowed_day(day);states=sparse.session(day);prior=sparse.session(days[days.index(day)-1])
        traces=verified_json(h/'sparse-v1/calendar-benchmark'/(day+'-traces.json'),'result_hash')
        if traces['result_hash']!=pred['trace_hashes'][day]:raise ContractError('diagnostic_trace_changed')
        for row in pred['detections']:
            if row['day']!=day:continue
            det=row['detection'];seq=traces['traces'][row['delay_minutes']][row['symbol']]
            index=next(i for i,r in enumerate(seq) if r['decision_ns']==det['decision_ns'])
            f=prefix_features(states[row['symbol']],det,prior[row['symbol']])
            f.update(eligible_immediately_prior=index>0 and seq[index-1]['rank'] is not None,
                     previous_eligible_scan_count=sum(r['rank'] is not None for r in seq[:index]))
            f.pop('diagnostic_hash');f['diagnostic_hash']=digest(f)
            features.append(dict(day=day,symbol=row['symbol'],delay_minutes=row['delay_minutes'],features=f))
    # Prefix data is frozen before outcome labels are opened or compared.
    raw=dict(version='baseline-detection-prefix-diagnostics-v1',source_predictions=pred['result_hash'],rows=features)
    save(out/'prefix-features.json',{**raw,'result_hash':digest(raw)})
    certificate=verified_json(h/'outcome-v1/certification/selectivity.json','result_hash');labels={}
    for day,sha in certificate['evidence_heads'].items():
        r=verified_json(h/'outcome-v1/certification'/(day+'-outcomes.json'),'result_hash')
        if r['result_hash']!=sha:raise ContractError('diagnostic_labels_changed')
        for row in r['rows']:
            if row['threshold']=='0.05':labels[(day,row['symbol'])]=row['label']['state']
    joined=[dict(**r,outcome=labels[(r['day'],r['symbol'])]) for r in features];groups={}
    numeric=['relative_volume','volume_acceleration','prior_3m_volume','recent_3m_volume','continuation','session_dollar_volume',
        'staleness_upper_bound_minutes','price_forming_prints_last_6m','price_forming_bar_minutes_last_6m','opening_move',
        'vwap_distance','pullback_from_high','gap','prior_session_return','range_expansion','spread_bps']
    for delay in ('0','2','5'):
        groups[delay]={}
        for lane in ('all','cold_start_same_session','full_history'):
            groups[delay][lane]={}
            for outcome in ('positive','negative','unknown'):
                fs=[r['features'] for r in joined if r['delay_minutes']==delay and r['outcome']==outcome and (lane=='all' or r['features']['lane']==lane)]
                metrics={}
                for k in numeric:
                    values=[D(str(f[k])) for f in fs if f[k] is not None]
                    metrics[k]=dict(known=len(values),missing=len(fs)-len(values),median=str(median(values)) if values else None,
                                   minimum=str(min(values)) if values else None,maximum=str(max(values)) if values else None)
                groups[delay][lane][outcome]=dict(count=len(fs),metrics=metrics,hours=dict(Counter(f['hour_et'] for f in fs)),
                    breakout=dict(Counter(str(f['breakout']) for f in fs)),zero_denominator=sum(f['prior_3m_volume']==0 for f in fs),
                    stale_more_than_one_completed_minute=sum(f['staleness_upper_bound_minutes']>int(delay)+1 for f in fs))
    body=dict(version='baseline-positive-negative-analysis-v1',prefix_hash=digest(raw),outcome_hash=certificate['result_hash'],rows=joined,groups=groups,
        interpretation='descriptive outcome comparisons of frozen detection-time inputs; no causal effect or new tuning claim',parameter_selection_from_analysis=False,execution_authority='none')
    result={**body,'result_hash':digest(body)};save(out/'error-analysis.json',result);return result


class QuoteIndex:
    """Prepared event-time index; never sorts away a native order violation."""
    def __init__(self, quotes):
        self.quotes=quotes;self.times=[timestamp_ns(q['t']) for q in quotes]
        if self.times!=sorted(self.times):raise ContractError('quote_index_order_regression')

    def context(self, stamp):
        quotes,times=self.quotes,self.times;i=bisect_left(times,stamp)-1
        if i<0:return dict(available=False,reason='no_strictly_prior_quote')
        q=quotes[i];t=times[i];start=bisect_left(times,t)
        if len({digest(r) for r in quotes[start:i+1]})>1:
            return dict(available=False,reason='ambiguous_quote_timestamp')
        bid,ask=D(str(q['bp'])),D(str(q['ap']))
        if not bid.is_finite() or not ask.is_finite() or bid<=0 or ask<=bid or q['bs']<=0 or q['as']<=0:
            return dict(available=False,reason='nonpositive_locked_crossed_or_empty_quote')
        return dict(available=True,quote_event_ns=t,age_ns=stamp-t,bid=str(bid),ask=str(ask),
            spread_bps=str((ask-bid)/((ask+bid)/2)*10000),bid_size_native=q['bs'],ask_size_native=q['as'],
            size_unit='provider_round_lots_not_verified_shares',raw_quote_hash=digest(q),
            conditions=q.get('c'),condition_execution_eligibility='unverified',original_arrival_ns=None,fill_allowed=False)


def quote_context(quotes, stamp):
    return QuoteIndex(quotes).context(stamp)


def execution_audit(history_root, directory):
    h=Path(history_root);root=Path(directory);root.mkdir(parents=True,exist_ok=False,mode=0o700)
    source=h/'certification-v2/high-fidelity-2025-01-02';day='2025-01-02';allowed_day(day)
    opened=timestamp_ns(day+'T09:30:00-05:00');closed=timestamp_ns(day+'T16:00:00-05:00');data={};manifests={};metrics={}
    for kind in ('quotes','trades'):
        request=json.loads((source/kind/'request.json').read_text())
        if request['kind']!=kind or set(request['params']['symbols'].split(','))!={'MDXH','POWL','ASPI'} or not request['params']['start'].startswith(day) or not request['params']['end'].startswith(day):
            raise ContractError('execution_subset_not_preregistered')
        grouped=defaultdict(list);keys=Counter()
        for page,meta in verified_pages(source/kind):
            for symbol,rows in page[kind].items():
                if symbol not in {'MDXH','POWL','ASPI'}:raise ContractError('execution_unrequested_symbol')
                grouped[symbol].extend(rows)
                for row in rows:keys.update(row.keys())
        manifests[kind]=json.loads((source/kind/'complete.json').read_text());data[kind]=grouped
        for symbol,rows in grouped.items():
            stamps=[timestamp_ns(r['t']) for r in rows]
            if stamps!=sorted(stamps):raise ContractError('execution_native_order_regression')
        metrics[kind]=dict(total=sum(map(len,grouped.values())),fields=dict(keys),original_arrival_field_present=any(k in keys for k in ('ts_recv','received_at','sip_timestamp','participant_timestamp','original_available_ns')),
            correction_lineage_fields_present=any(k in keys for k in ('oi','ci','correction_id','cancel_id')))
    result={}
    for symbol in sorted(data['trades']):
        quotes=[q for q in data['quotes'][symbol] if opened<=timestamp_ns(q['t'])<closed]
        trades=[t for t in data['trades'][symbol] if opened<=timestamp_ns(t['t'])<closed]
        index=QuoteIndex(quotes);contexts=[index.context(timestamp_ns(t['t'])) for t in trades];good=[c for c in contexts if c['available']]
        spans=Counter((timestamp_ns(q['t'])//MINUTE) for q in quotes)
        result[symbol]=dict(regular_quotes=len(quotes),regular_trades=len(trades),quote_minutes_observed=len(spans),expected_regular_minutes=390,
            absence_does_not_prove_gap_or_tradability=True,exact_duplicate_quotes=len(quotes)-len({digest(q) for q in quotes}),
            exact_duplicate_trades=len(trades)-len({digest(t) for t in trades}),quote_context_rejections=dict(Counter(c.get('reason') for c in contexts if not c['available'])),
            price_forming_trades=sum(trade_rule(t)[0] is True for t in trades),unknown_trade_rules=sum(trade_rule(t)[0] is None for t in trades),
            trade_size_median=median(t['s'] for t in trades) if trades else None,
            quote_size_unit='native_round_lots_not_converted_without_security_lot_history',
            median_prior_quote_spread_bps=str(median(D(c['spread_bps']) for c in good)) if good else None,
            median_prior_quote_age_ns=median(c['age_ns'] for c in good) if good else None,
            quote_age_sensitivity={str(seconds):sum(c['age_ns']<=seconds*10**9 for c in good) for seconds in (1,5,30)},
            quotes_with_nonpositive_locked_crossed_or_empty_values=sum(not quote_context([q],timestamp_ns(q['t'])+1)['available'] for q in quotes),
            quote_conditions=dict(Counter(','.join(q.get('c',[])) for q in quotes)),
            query_exhausted=True,original_delivery_complete=False,full_status_coverage=False,execution_qualified=False)
    body=dict(version='saved-execution-subset-audit-v1',day=day,sources=manifests,native_fields=metrics,symbols=result,
        scope='complete saved final-export query; event-time descriptive quote context, not executable fill evidence',
        trades_compared_using_strictly_earlier_quotes=True,new_market_data_requests=0,performance_experiments=0,execution_authority='none')
    value={**body,'result_hash':digest(body)};save(root/'execution-audit.json',value);return value


def detection_quote_review(history_root, directory):
    """Supplemental only: use saved quotes where detection's symbol/day is covered."""
    h=Path(history_root);out=Path(directory);out.mkdir(parents=True,exist_ok=False,mode=0o700)
    prefix=verified_json(h/'selectivity-v1/diagnostics-v2/prefix-features.json','result_hash')
    source=h/'certification-v2/high-fidelity-2025-01-02/quotes';quotes=defaultdict(list)
    for page,meta in verified_pages(source):
        for symbol,rows in page['quotes'].items():quotes[symbol].extend(rows)
    indices={s:QuoteIndex(rows) for s,rows in quotes.items()};rows=[]
    for row in prefix['rows']:
        allowed_day(row['day']);f=row['features']
        if row['day']=='2025-01-02' and row['symbol'] in indices:
            context=indices[row['symbol']].context(f['feature_cutoff_ns'])
            if context['available']:context['age_at_detection_ns']=f['decision_ns']-context['quote_event_ns']
        else:context=dict(available=False,reason='no_saved_full_quote_query_for_symbol_day')
        rows.append(dict(day=row['day'],symbol=row['symbol'],delay_minutes=row['delay_minutes'],context=context))
    body=dict(version='supplemental-detection-quotes-v1',prefix_hash=prefix['result_hash'],
        query=__import__('json').loads((source/'complete.json').read_text()),rows=rows,
        scope='event-time strictly before frozen feature cutoff; original arrival unknown; no filter or fill',policy_changed=False,execution_authority='none')
    result={**body,'result_hash':digest(body)};save(out/'detection-quotes.json',result);return result
