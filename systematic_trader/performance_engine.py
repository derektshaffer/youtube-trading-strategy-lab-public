"""Shared orchestration of the existing ORB/RiskBook/Simulator, no new fill model."""
from dataclasses import asdict
from decimal import Decimal

from .events import ContractError, digest
from .features import MINUTE, Session, Universe, Discovery, feature_snapshot
from .market_state import MarketState
from .research_engine import OpeningRangeBreakout, Simulator, ExecutionPolicy, RiskBook, RiskPolicy


def evaluate_session(events, *, session: Session, daily_history, universe_snapshots, keys,
                     execution_policy=ExecutionPolicy()):
    # Materialize once: an untrusted iterator cannot swap between validation/use.
    events=list(events)
    from .performance_scope import require_event
    for event in events:require_event(event)
    cost_allowance=str(max(Decimal('0.02'),Decimal(execution_policy.slippage_per_share)+2*Decimal(execution_policy.fee_per_share)))
    market=MarketState();discovery=Discovery();universe=Universe()
    for snap in universe_snapshots:universe.observe(**snap)
    sim=Simulator(RiskBook(RiskPolicy(slippage_per_share=cost_allowance)),execution_policy)
    keys=[tuple(k) for k in keys];baseline=None;features=[];skips=[]
    for event in events:
        market.apply(event)
        sim.advance(event,market)
        if event['received_ns']<session.open_ns+5*MINUTE:continue
        if not all(k in market.instruments for k in keys):continue
        snapshots=[feature_snapshot(market,k,session,daily_history,as_of_ns=event['received_ns'],research=True) for k in keys]
        features.extend(snapshots)
        if baseline is None and all(not s['missing'] for s in snapshots):
            selected=discovery.freeze(session,snapshots,universe,as_of_ns=event['received_ns'])
            baseline=OpeningRangeBreakout(selected)
        if baseline:
            for snapshot in snapshots:
                if event['instrument_id']!=snapshot['key'][3]:continue
                decision=baseline.observe(event,snapshot)
                if decision:sim.submit(decision)
    result=dict(version='research-fixture-run-v1',evidence='fixture' if all(k[2]=='fixture' for k in keys) else 'research_only',
        watermark=market.watermark,market_state_hash=market.fingerprint(),feature_hashes=[s['feature_hash'] for s in features],
        discovery=discovery.audit,decisions=baseline.audit if baseline else [],risk=sim.risk.audit,
        simulation=sim.summary(),execution_audit=sim.audit,no_trade=dict(net_pnl='0',trades=0),
        execution_policy=asdict(execution_policy),production_eligible=False,execution_authority='none')
    return {**result,'result_hash':digest(result)}
