"""Sparse archive discovery repair. Isolated from production and frozen v1."""
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal
from .events import ContractError, digest
from .features import MINUTE
from .momentum import MomentumPolicy

D=Decimal


@dataclass(frozen=True)
class SparsePolicy:
    version: str='sparse-discovery-repair-v1'
    max_anchor_age_minutes: int=5
    max_decision_age_minutes: int=10
    cold_start_ratio: str='2'

    def __post_init__(self):
        if (type(self.max_anchor_age_minutes) is not int or self.max_anchor_age_minutes<1 or
            type(self.max_decision_age_minutes) is not int or self.max_decision_age_minutes<1 or
            not D(self.cold_start_ratio).is_finite() or D(self.cold_start_ratio)<=0):
            raise ContractError('invalid_sparse_policy')


def available_prefix(states, cutoff, decision):
    result=[]
    for s in states:
        if s['minute_end_ns']>cutoff:continue
        # Optional explicit arrivals support late/out-of-order replay fixtures;
        # archive imports deliberately have no invented original arrival clock.
        a=s.get('assumed_available_ns')
        if a is not None and a>decision:continue
        result.append(s)
    result.sort(key=lambda s:s['minute_start_ns'])
    if len({s['minute_start_ns'] for s in result})!=len(result):raise ContractError('duplicate_sparse_minute')
    return result


def profile(states,session):
    selected=available_prefix(states,session.close_ns,session.close_ns)
    if ([s['minute_start_ns'] for s in selected]!=list(range(session.open_ns,session.close_ns,MINUTE)) or
        any(s['coverage']=='unresolved' or s['identity']!='archive_series_unverified' or s['action_sources'] for s in selected)):
        return None
    bars=[s['bar'] for s in selected if s['bar']]
    if not bars:return None
    p=[r['payload'] for r in bars]
    return dict(session_id=session.session_id,close_ns=session.close_ns,known_ns=session.close_ns,
        high=str(max(D(x['high']) for x in p)),low=str(min(D(x['low']) for x in p)),close=p[-1]['close'],
        open=p[0]['open'] if bars[0]['stamp']==session.open_ns else None,last_bar_ns=bars[-1]['stamp'],
        minute_volumes=[s['emitted_bar_volume'] for s in selected],volume=sum(s['emitted_bar_volume'] for s in selected),
        dollar_volume=str(sum(D(x['vwap'])*x['volume_shares'] for x in p)),
        measure='native_emitted_bar_volume_not_all_tape_volume',source_hash=digest([s['state_hash'] for s in selected]))


def snapshot(symbol,states,prior,session,*,cutoff,decision,action_boundary=False,policy=MomentumPolicy(),sparse_policy=SparsePolicy()):
    selected=available_prefix(states,cutoff,decision);selected=[s for s in selected if s['minute_start_ns']>=session.open_ns]
    missing=[];limitations=[];elapsed=(cutoff-session.open_ns)//MINUTE
    if elapsed<6:missing.append('six_completed_minutes_required')
    if [s['minute_start_ns'] for s in selected]!=list(range(session.open_ns,cutoff,MINUTE)):
        missing.append('unresolved_missing_minute_state_or_late_arrival')
    if any(s['coverage']=='unresolved' for s in selected):missing.append('unresolved_trade_or_bar_coverage')
    if any(s['identity']!='archive_series_unverified' for s in selected):missing.append('identity_exclusion')
    if action_boundary or any(s['action_sources'] for s in selected):missing.append('corporate_action_lookback_excluded')
    bars=[s['bar'] for s in selected if s['bar'] and not s['issues']]
    values={};p=[b['payload'] for b in bars];by_stamp={b['stamp']:b['payload'] for b in bars}
    past=[h for h in prior if h is None or (h['close_ns']<session.open_ns and h['known_ns']<=cutoff)]
    histories=[h for h in past[-policy.minimum_history_sessions:] if h]
    full=(len(histories)==policy.minimum_history_sessions and all(len(h['minute_volumes'])>=elapsed for h in histories))
    if not full:limitations.append('history_unavailable')
    def at(anchor):
        return next((b for b in reversed(bars) if b['stamp']+MINUTE<=anchor and anchor-b['stamp']<=sparse_policy.max_anchor_age_minutes*MINUTE),None)
    current=at(cutoff);base=at(cutoff-3*MINUTE)
    if bars:
        last=bars[-1];close=D(last['payload']['close']);age=decision-last['stamp'];v=sum(x['volume_shares'] for x in p)
        dollars=sum(D(x['vwap'])*x['volume_shares'] for x in p);high=max(D(x['high']) for x in p)
        low=min(D(x['low']) for x in p)
        values.update(last_close=str(close),last_observed_bar_start_ns=last['stamp'],last_observed_interval_end_ns=last['stamp']+MINUTE,
            staleness_upper_bound_ns=age,anchor_staleness_upper_bound_ns=cutoff-last['stamp'],
            price_state='stale_last_known_price' if last['stamp']+MINUTE<cutoff else 'last_observed_price',
            session_volume=v,session_dollar_volume=str(dollars),session_high=str(high),session_low=str(low),
            pullback_from_high=str((high-close)/high),elapsed_minutes=elapsed,
            exact_open_known=session.open_ns in by_stamp,
            return_from_open=str(close/D(by_stamp[session.open_ns]['open'])-1) if session.open_ns in by_stamp else None,
            emitted_bar_count=len(bars),documented_no_price_minutes=sum(s['coverage']=='documented_absence' for s in selected))
        if age>sparse_policy.max_decision_age_minutes*MINUTE:missing.append('stale_last_price_at_decision')
        if current is None:missing.append('stale_last_price_at_feature_anchor')
        if v:
            vwap=dollars/v;values.update(vwap=str(vwap),vwap_distance=str(close/vwap-1))
        if current and base:
            values.update(return_3m=str(D(current['payload']['close'])/D(base['payload']['close'])-1),
                continuation_reference_start_ns=base['stamp'],continuation_observation_separation_ns=current['stamp']-base['stamp'],
                continuation_reference_age_ns=cutoff-3*MINUTE-base['stamp'])
        else:missing.append('stale_or_missing_continuation_reference')
        previous=[b for b in bars if b['stamp']<last['stamp']]
        previous_high=max((D(b['payload']['high']) for b in previous),default=None)
        values.update(prior_intraday_high=str(previous_high) if previous_high is not None else None,
            breakout=close>previous_high if previous_high is not None else None)
        # Fixed wall-clock buckets, with no observation-count/time substitution.
        volumes={s['minute_start_ns']:s['emitted_bar_volume'] for s in selected}
        recent=[volumes.get(t) for t in range(cutoff-3*MINUTE,cutoff,MINUTE)]
        earlier=[volumes.get(t) for t in range(cutoff-6*MINUTE,cutoff-3*MINUTE,MINUTE)]
        ratio=D(sum(recent))/sum(earlier) if all(x is not None for x in recent+earlier) and sum(earlier)>0 else None
        values['volume_acceleration']=str(ratio) if ratio is not None else None
        tail=[by_stamp.get(t) for t in range(cutoff-6*MINUTE,cutoff,MINUTE)]
        values.update(price_acceleration=None,higher_lows=None,range_expansion=None,vwap_reclaimed=None)
        if all(tail):
            closes=[D(b['close']) for b in tail]
            values['price_acceleration']=str((closes[-1]/closes[-2]-1)-(closes[-2]/closes[-3]-1))
            values['higher_lows']=D(tail[-3]['low'])<D(tail[-2]['low'])<D(tail[-1]['low'])
            old_range=sum(D(b['high'])-D(b['low']) for b in tail[:3])
            values['range_expansion']=str(sum(D(b['high'])-D(b['low']) for b in tail[3:])/old_range) if old_range else None
        expected=sum(D(sum(h['minute_volumes'][:elapsed])) for h in histories)/len(histories) if full else None
        rv=D(v)/expected if expected else None
        values['relative_volume_at_time']=str(rv) if rv is not None else None
        if full and rv is None:limitations.append('zero_historical_volume_denominator')
        last_history=past[-1] if past else None
        gap_ok=last_history and last_history['close_ns']<session.open_ns and last_history['known_ns']<=cutoff and last_history['close_ns']-last_history.get('last_bar_ns',last_history['close_ns']-MINUTE)<=5*MINUTE
        values['gap_from_prior_close']=str(D(by_stamp[session.open_ns]['open'])/D(last_history['close'])-1) if gap_ok and session.open_ns in by_stamp and not action_boundary else None
    else:missing.append('price_observation_unavailable')
    lane='full_history' if values.get('relative_volume_at_time') is not None else 'cold_start_same_session'
    if lane=='cold_start_same_session':limitations.append('same_session_ratio_not_historical_rvol')
    body=dict(version='sparse-archive-features-v1',policy=asdict(policy),sparse_policy=asdict(sparse_policy),symbol=symbol,
        key=['alpaca','sip','import','archive-series:'+symbol],session=asdict(session),as_of_ns=cutoff,decision_ns=decision,
        values=values,missing=sorted(set(missing)),limitations=sorted(set(limitations)),history_sessions=len(histories),lane=lane,
        prefix_hash=digest([s['state_hash'] for s in selected]),history_hash=digest(histories),
        execution_missing=['historical_identity_unverified','market_status_unknown_or_halted','original_arrival_unverified','quotes_not_connected'],
        production_eligible=False,execution_authority='none',fill_allowed=False)
    return {**body,'feature_hash':digest(body)}


def rank(snapshots, *, policy=MomentumPolicy(), sparse_policy=SparsePolicy()):
    candidates=[];excluded=[]
    if len({s['symbol'] for s in snapshots})!=len(snapshots):raise ContractError('duplicate_sparse_symbol')
    if len({(s['as_of_ns'],digest(s['session']),s.get('decision_ns',s['as_of_ns'])) for s in snapshots})>1:
        raise ContractError('mixed_sparse_snapshot_times')
    for s in snapshots:
        if s['feature_hash']!=digest({k:v for k,v in s.items() if k!='feature_hash'}):raise ContractError('sparse_feature_integrity')
        if s['policy']!=asdict(policy):raise ContractError('sparse_momentum_policy_mismatch')
        if 'sparse_policy' in s and s['sparse_policy']!=asdict(sparse_policy):raise ContractError('sparse_policy_mismatch')
        v=s['values'];reasons=list(s['missing']);lane=s.get('lane','full_history')
        if not reasons:
            if D(v['last_close'])<D(policy.minimum_price):reasons.append('price_filter')
            if D(v['session_dollar_volume'])<D(policy.minimum_dollar_volume):reasons.append('dollar_liquidity_filter')
            ratio=v.get('relative_volume_at_time') if lane=='full_history' else v.get('volume_acceleration')
            if ratio is None:reasons.append('same_session_volume_comparison_unavailable' if lane!='full_history' else 'rvol_unavailable')
            elif D(ratio)<D(policy.minimum_relative_volume if lane=='full_history' else sparse_policy.cold_start_ratio):
                reasons.append('relative_volume_filter' if lane=='full_history' else 'same_session_volume_acceleration_filter')
            if D(v['return_3m'])<=0:reasons.append('positive_continuation_required')
        if reasons:
            excluded.append(dict(symbol=s['symbol'],instrument_id=s['key'][3],reasons=sorted(reasons),feature_hash=s['feature_hash']))
            continue
        signals=['unusual_volume' if lane=='full_history' else 'same_session_volume_expansion','positive_three_minute_return']
        for flag in ('breakout','higher_lows','vwap_reclaimed'):
            if v.get(flag):signals.append(flag)
        for flag in ('volume_acceleration','range_expansion'):
            if v.get(flag) and D(v[flag])>1:signals.append(flag)
        candidates.append(dict(symbol=s['symbol'],instrument_id=s['key'][3],feature_hash=s['feature_hash'],signals=signals,
            lane=lane,volume_comparison=ratio,relative_volume=v.get('relative_volume_at_time'),return_3m=v['return_3m'],
            execution_missing=s['execution_missing'],execution_authority='none'))
    candidates.sort(key=lambda c:(c['lane']!='full_history',-D(c['volume_comparison']),-D(c['return_3m']),c['instrument_id']))
    overflow=candidates[policy.limit:]
    for c in overflow:excluded.append(dict(symbol=c['symbol'],instrument_id=c['instrument_id'],reasons=['rank_cutoff'],feature_hash=c['feature_hash']))
    body=dict(version=sparse_policy.version,candidates=candidates[:policy.limit],excluded=excluded,execution_authority='none',production_eligible=False)
    return {**body,'ranking_hash':digest(body)}
