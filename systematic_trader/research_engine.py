"""Deterministic ORB/no-trade baselines, simulated fills and independent risk.

Research only: these objects cannot submit brokerage orders. No SDK, credential,
network, or production authority is accepted by this module.
"""
from dataclasses import asdict, dataclass
from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR

from .events import ContractError, digest
from .features import MINUTE

D = Decimal


@dataclass(frozen=True)
class BaselinePolicy:
    version: str = "orb-long-research-v2"
    atr_stop_fraction: str = "0.10"
    cap_risk_fraction: str = "0.10"
    spread_risk_fraction: str = "0.10"
    expiry_ns: int = 2_000_000_000
    entry_window_minutes: int = 60

    def __post_init__(self):
        for value in (self.atr_stop_fraction,self.cap_risk_fraction,self.spread_risk_fraction):
            if not D(value).is_finite() or D(value)<=0:raise ContractError("invalid_strategy_policy")
        if self.expiry_ns<=0 or self.entry_window_minutes<=5:raise ContractError("invalid_strategy_window")


class NoTrade:
    version="no-trade-v1"
    def observe(self,*_):return None


class OpeningRangeBreakout:
    def __init__(self,candidates,policy=BaselinePolicy()):
        from copy import deepcopy
        self.candidates={tuple(s["key"]):deepcopy(s) for s in candidates}
        self.policy,self.attempted,self.audit=policy,set(),[]

    def observe(self,event,snapshot):
        key=tuple(snapshot["key"])
        frozen=self.candidates.get(key)
        if frozen is None or key in self.attempted or event["event_type"]!="market.trade":return None
        if tuple((event["provider"],event["feed"],event["origin"],event["instrument_id"])) != key:
            raise ContractError("decision_event_cohort_mismatch")
        if event["origin"]=="live":return None
        now=event["received_ns"];session=frozen["session"];v=frozen["values"];current=snapshot["values"]
        if snapshot["as_of_ns"]!=now or snapshot["watermark"]!=event["ledger_seq"]:
            raise ContractError("decision_input_watermark_mismatch")
        if now<v["range_ready_ns"] or now>=min(session["open_ns"]+self.policy.entry_window_minutes*MINUTE,session["close_ns"]-5*MINUTE):return None
        if event["source_time_ns"]<session["open_ns"]+5*MINUTE or not 0<=now-event["source_time_ns"]<=2_000_000_000:return None
        tick=D(v["tick_size"]);trigger=D(v["opening_high"])+tick
        if D(event["payload"]["price"])<trigger:return None
        self.attempted.add(key)
        reasons=list(snapshot["missing"])
        distance=D(v["atr14"])*D(self.policy.atr_stop_fraction)
        stop=((trigger-distance)/tick).to_integral_value(rounding=ROUND_FLOOR)*tick
        cap=((trigger+distance*D(self.policy.cap_risk_fraction))/tick).to_integral_value(rounding=ROUND_FLOOR)*tick
        if D(current.get("ask",str(cap+1)))>cap or D(event["payload"]["price"])>cap:reasons.append("entry_above_price_cap")
        if D(current.get("ask","0"))-D(current.get("bid","0"))>distance*D(self.policy.spread_risk_fraction):reasons.append("spread_limit")
        decision=dict(instrument_id=key[3],key=list(key),symbol=snapshot["symbol"],policy=asdict(self.policy),
                      feature_hash=snapshot["feature_hash"],candidate_feature_hash=frozen["feature_hash"],
                      input_event_id=event["event_id"],watermark=event["ledger_seq"],created_ns=now,
                      expires_ns=now+self.policy.expiry_ns,liquidate_ns=session["close_ns"]-5*MINUTE,
                      session_close_ns=session["close_ns"],
                      trigger=str(trigger),cap=str(cap),stop=str(stop),rejections=reasons,
                      preceding_minute_volume=current.get("preceding_minute_volume"),ask_size_shares=current.get("ask_size_shares"),
                      evidence=snapshot["evidence"],execution_authority="none")
        decision["decision_id"]=digest(decision);self.audit.append(decision)
        return decision


@dataclass(frozen=True)
class RiskPolicy:
    version: str="research-risk-v2"
    equity: str="10000"
    risk_per_attempt: str="10"
    total_reserved_risk: str="20"
    gross_notional: str="5000"
    position_notional: str="2500"
    correlated_notional: str="2500"
    daily_loss: str="30"
    maximum_drawdown: str="100"
    max_positions: int=2
    slippage_per_share: str="0.02"
    minute_participation: str="0.01"
    displayed_participation: str="0.10"

    def __post_init__(self):
        for k,v in asdict(self).items():
            if k in {"version","max_positions"}:continue
            if not D(v).is_finite() or D(v)<=0:raise ContractError("invalid_risk_policy")
        if self.max_positions<1 or D(self.gross_notional)>D(self.equity):raise ContractError("leverage_or_position_limit")
        if max(D(self.minute_participation),D(self.displayed_participation))>1:raise ContractError("invalid_participation")


class RiskBook:
    def __init__(self,policy=RiskPolicy(), *, correlation_groups=None):
        self.policy,self.reservations,self.audit=policy,{},[]
        self.realized=D(0);self.unrealized=D(0);self.killed=False
        self.correlation_groups=deepcopy(correlation_groups or {})
        self.peak_pnl=D(0);self.execution_failures=0

    def mark_pnl(self, unrealized):
        value=D(unrealized)
        if not value.is_finite():raise ContractError("invalid_risk_mark")
        self.unrealized=value
        total=self.realized+self.unrealized
        self.peak_pnl=max(self.peak_pnl,total)
        if total<=-D(self.policy.daily_loss) or self.peak_pnl-total>=D(self.policy.maximum_drawdown):
            self.killed=True

    def record_execution_failure(self, reason):
        if reason not in {"rejection", "reconciliation_unknown", "provider_failure"}:
            raise ContractError("invalid_execution_failure_reason")
        self.execution_failures+=1
        self.audit.append(dict(type="execution_failure",reason=reason,count=self.execution_failures))
        if self.execution_failures>=3 or reason=="reconciliation_unknown":self.killed=True

    def reserve(self,decision, *, market=None):
        p=self.policy;reasons=list(decision["rejections"]);i=decision["instrument_id"]
        if digest({k:v for k,v in decision.items() if k != "decision_id"}) != decision["decision_id"]:
            reasons.append("decision_hash_mismatch")
        if decision["evidence"] not in {"fixture","research_only"} or decision["key"][2]=="live":reasons.append("no_live_authority")
        times=[decision.get(k) for k in ("created_ns","expires_ns","liquidate_ns","session_close_ns")]
        if any(type(t) is not int for t in times) or not all(a<b for a,b in zip(times,times[1:])):
            reasons.append("invalid_order_session_window")
        # Alpha cannot bless market health by supplying an empty rejection list.
        if market is None:
            reasons.append("risk_market_context_unavailable")
        else:
            state=market.instruments.get(tuple(decision["key"]))
            if market.watermark!=decision["watermark"] or market.received_ns!=decision["created_ns"]:
                reasons.append("risk_market_watermark_mismatch")
            if market.gaps:reasons.append("risk_unresolved_data_gap")
            if state is None or state.problems or state.halted is not False:
                reasons.append("risk_market_state_unsafe")
            quote=state.quote if state else None
            if quote is None or not 0<=decision["created_ns"]-quote["source_time_ns"]<=2_000_000_000:
                reasons.append("risk_quote_unavailable_or_stale")
            elif (market.quote_shares(quote,"ask") != decision["ask_size_shares"] or
                  D(quote["payload"]["ask"])>D(decision["cap"])):
                reasons.append("risk_quote_liquidity_or_cap_mismatch")
            volume_bar=state.bars.get(((decision["created_ns"]//MINUTE)*MINUTE-MINUTE,MINUTE)) if state else None
            if volume_bar is None or volume_bar["payload"]["volume_shares"]!=decision["preceding_minute_volume"]:
                reasons.append("risk_volume_evidence_mismatch")
        if self.killed:reasons.append("kill_switch")
        if self.realized+self.unrealized<=-D(p.daily_loss):reasons.append("daily_loss_limit")
        if self.peak_pnl-self.realized-self.unrealized>=D(p.maximum_drawdown):reasons.append("drawdown_limit")
        if i in self.reservations:reasons.append("existing_exposure")
        if len(self.reservations)>=p.max_positions:reasons.append("position_limit")
        cap,stop=D(decision["cap"]),D(decision["stop"])
        if not cap.is_finite() or not stop.is_finite() or cap<=0:
            raise ContractError("invalid_risk_order_prices")
        per_share=cap-stop+D(p.slippage_per_share)
        if per_share<=0 or stop<=0:reasons.append("invalid_stop")
        volume,displayed=decision["preceding_minute_volume"],decision["ask_size_shares"]
        if type(volume) is not int or type(displayed) is not int or min(volume,displayed)<=0:reasons.append("liquidity_unavailable")
        qty=0
        if not reasons:
            remaining_risk=D(p.total_reserved_risk)-sum(r["risk"] for r in self.reservations.values())
            remaining_notional=D(p.gross_notional)-sum(r["notional"] for r in self.reservations.values())
            # Missing classifications share a conservative unknown group. The
            # strategy's own metadata cannot choose its risk correlation group.
            group=self.correlation_groups.get(i,"unknown")
            remaining_correlated=D(p.correlated_notional)-sum(r["notional"] for j,r in self.reservations.items()
                if self.correlation_groups.get(j,"unknown")==group)
            qty=max(0,int(min(D(p.risk_per_attempt)/per_share,remaining_risk/per_share,
                       D(p.position_notional)/cap,remaining_notional/cap,remaining_correlated/cap,
                       D(volume)*D(p.minute_participation),D(displayed)*D(p.displayed_participation))))
            if qty<1:reasons.append("budget_or_liquidity_limit")
        result=dict(decision_id=decision["decision_id"],quantity=qty,rejections=reasons,policy=asdict(p),execution_authority="none")
        self.audit.append(result)
        if qty and not reasons:self.reservations[i]=dict(qty=qty,risk=per_share*qty,notional=cap*qty)
        return result

    def resize(self,instrument,remaining):
        old=self.reservations[instrument]
        if not 0<=remaining<=old["qty"]:raise ContractError("risk_reservation_increase_refused")
        if remaining==0:self.reservations.pop(instrument);return
        ratio=D(remaining)/old["qty"];old.update(qty=remaining,risk=old["risk"]*ratio,notional=old["notional"]*ratio)


@dataclass(frozen=True)
class ExecutionPolicy:
    version: str="quote-simulation-v2"
    arrival_latency_ns: int=100_000_000
    cancel_latency_ns: int=100_000_000
    fee_per_share: str="0.005"
    slippage_per_share: str="0.01"
    quote_participation: str="0.10"

    def __post_init__(self):
        if min(self.arrival_latency_ns,self.cancel_latency_ns)<0:raise ContractError("negative_latency")
        for value in (self.fee_per_share,self.slippage_per_share,self.quote_participation):
            if not D(value).is_finite() or D(value)<0:raise ContractError("invalid_execution_cost")
        if not 0<D(self.quote_participation)<=1:raise ContractError("invalid_execution_participation")


class Simulator:
    """Existing intent is evaluated before the current event can create new intent."""
    def __init__(self,risk=None,policy=ExecutionPolicy()):
        self.risk=risk or RiskBook();self.policy=policy
        if D(self.risk.policy.slippage_per_share)<D(policy.slippage_per_share)+2*D(policy.fee_per_share):
            raise ContractError("risk_cost_allowance_too_small")
        self.pending={};self.positions={};self.audit=[];self.completed=[];self.blocked=False
        self.seen_quotes=set();self.last_seq=0
        self.ambiguities=[]
        self.market=None

    def submit(self,decision, *, market=None):
        from .performance_scope import require_decision
        require_decision(decision)
        if self.blocked:return None
        decision=deepcopy(decision)
        approval=self.risk.reserve(decision,market=market if market is not None else self.market)
        if approval["rejections"]:return None
        i=decision["instrument_id"]
        self.pending[i]=dict(decision=decision,remaining=approval["quantity"],filled=0)
        self.audit.append(dict(type="simulation_intent",decision_id=decision["decision_id"],quantity=approval["quantity"]))
        return approval

    def advance(self,event,market):
        from .performance_scope import require_event
        require_event(event)
        seq,now=event["ledger_seq"],event["received_ns"]
        if seq<=self.last_seq:raise ContractError("simulator_out_of_order_ledger")
        if self.last_seq and seq!=self.last_seq+1:raise ContractError("simulator_missing_ledger_event")
        if market.watermark!=seq:raise ContractError("simulator_market_watermark_mismatch")
        self.last_seq=seq
        self.market=market
        if event["origin"]=="live":raise ContractError("simulator_requires_research_source")
        if event["event_type"] in {"recorder.gap","recorder.reject"}:
            self.blocked=True;self.risk.killed=True
            if self.pending or self.positions:
                self.ambiguities.append("data_gap_execution_state_unknown")
        if self.blocked:
            # A feed gap is not a broker cancellation acknowledgement. Preserve
            # pending exposure and its reservation; never assign later fills.
            return
        for i,pending in list(self.pending.items()):
            d=pending["decision"]
            if now>=d["expires_ns"]+self.policy.cancel_latency_ns:
                self.audit.append(dict(type="simulated_cancel_ack",instrument_id=i,unfilled=pending["remaining"],received_ns=now))
                self.risk.resize(i,self.positions.get(i,{}).get("qty",0));del self.pending[i]
        if event["event_type"]!="market.quote" or event["instrument_id"] is None:return
        i=event["instrument_id"];q=event["payload"]
        cohort=(event["provider"],event["feed"],event["origin"],i)
        if event["content_hash"] in self.seen_quotes:return
        self.seen_quotes.add(event["content_hash"])
        state=market.instruments.get(cohort)
        if state is None or state.quote is None or state.quote["event_id"]!=event["event_id"]:return
        if state.halted is not False or state.problems or not 0<=now-event["source_time_ns"]<=2_000_000_000:return
        ask_size,bid_size=market.quote_shares(event,"ask"),market.quote_shares(event,"bid")
        if ask_size is None or bid_size is None:return
        if i in self.positions:
            position=self.positions[i];decision=position["decision"]
            if tuple(decision["key"])!=cohort:return
            if now>=decision["session_close_ns"] or event["source_time_ns"]>=decision["session_close_ns"]:
                if "session_closed_with_exposure" not in self.ambiguities:self.ambiguities.append("session_closed_with_exposure")
                self.blocked=True;self.risk.killed=True
                return
            position["mark_pnl"]=(D(q["bid"])-D(self.policy.slippage_per_share)-position["entry_price"]
                -D(self.policy.fee_per_share)-position["entry_fee_per_share"])*position["qty"]
            self.risk.mark_pnl(sum(p.get("mark_pnl",D(0)) for p in self.positions.values()))
            if self.risk.realized+self.risk.unrealized<=-D(self.risk.policy.daily_loss):self.risk.killed=True
            if D(q["bid"])<=D(decision["stop"]) or now>=decision["liquidate_ns"] or self.risk.killed:
                if not position.get("exiting"):
                    position["exiting"]=True;position["exit_ready_ns"]=now+self.policy.arrival_latency_ns
                    position["exit_intent_seq"]=seq
                    self.audit.append(dict(type="simulated_exit_intent",instrument_id=i,received_ns=now,
                                           eligible_fill_ns=position["exit_ready_ns"]))
            if position.get("exiting"):
                # Once a stop/session exit is triggered, continue liquidating residuals.
                if i in self.pending:
                    # An entry cancellation racing a stop can change exposure.
                    # Do not invent an immediate broker acknowledgement or fill.
                    self.ambiguities.append("entry_cancel_exit_race")
                    self.blocked=True;self.risk.killed=True
                    return
                if now<position["exit_ready_ns"] or seq<=position["exit_intent_seq"]:return
                qty=min(position["qty"],int(D(bid_size)*D(self.policy.quote_participation)))
                if qty:
                    price=max(D(0),D(q["bid"])-D(self.policy.slippage_per_share))
                    pnl=(price-position["entry_price"])*qty-D(self.policy.fee_per_share)*qty-position["entry_fee_per_share"]*qty
                    position["net_pnl"]+=pnl;position["qty"]-=qty;self.risk.realized+=pnl
                    position["mark_pnl"]=(price-position["entry_price"]-D(self.policy.fee_per_share)-position["entry_fee_per_share"])*position["qty"]
                    self.risk.mark_pnl(sum(p.get("mark_pnl",D(0)) for p in self.positions.values()))
                    self.audit.append(dict(type="simulated_exit_fill",instrument_id=i,quantity=qty,price=str(price),net_pnl=str(pnl),event_id=event["event_id"]))
                    self.risk.resize(i,position["qty"])
                    if position["qty"]==0:
                        self.completed.append(dict(decision_id=decision["decision_id"],instrument_id=i,net_pnl=str(position["net_pnl"]),
                            initial_risk=str(position["initial_risk"]),net_r=str(position["net_pnl"]/position["initial_risk"]),
                            label_end_ns=now,evidence=decision["evidence"],execution_authority="none"));del self.positions[i]
                return
        if i not in self.pending or self.blocked or self.risk.killed:return
        pending=self.pending[i];d=pending["decision"]
        if tuple(d["key"])!=cohort or seq<=d["watermark"] or now<d["created_ns"]+self.policy.arrival_latency_ns:return
        price=D(q["ask"])+D(self.policy.slippage_per_share)
        if price>D(d["cap"]):return
        qty=min(pending["remaining"],int(D(ask_size)*D(self.policy.quote_participation)))
        if qty<1:return
        position=self.positions.setdefault(i,dict(decision=d,qty=0,entry_price=D(0),entry_fee_per_share=D(self.policy.fee_per_share),net_pnl=D(0),initial_risk=D(0)))
        position["entry_price"]=(position["entry_price"]*position["qty"]+price*qty)/(position["qty"]+qty)
        position["qty"]+=qty;position["initial_risk"]+=(price-D(d["stop"]))*qty
        pending["remaining"]-=qty;pending["filled"]+=qty
        self.audit.append(dict(type="simulated_entry_fill",instrument_id=i,quantity=qty,price=str(price),event_id=event["event_id"],
                               during_cancel_race=now>=d["expires_ns"]))
        if pending["remaining"]==0:del self.pending[i]

    def summary(self):
        return dict(completed=self.completed,residual_positions={i:p["qty"] for i,p in self.positions.items()},
                    pending_entries={i:p["remaining"] for i,p in self.pending.items()},net_pnl=str(self.risk.realized),
                    audit_hash=digest(self.audit),critical_gaps=self.blocked,ambiguities=list(self.ambiguities),
                    production_eligible=False,execution_authority="none")
