"""Versioned point-in-time research features and causal universe selection."""
from dataclasses import asdict, dataclass
from decimal import Decimal
from copy import deepcopy

from .events import ContractError, digest, integer
import re

MINUTE = 60_000_000_000
FEATURE_VERSION = "orb-point-in-time-v1"


@dataclass(frozen=True)
class Session:
    session_id: str
    open_ns: int
    close_ns: int
    calendar_source_hash: str

    def __post_init__(self):
        integer(self.open_ns); integer(self.close_ns)
        if (not isinstance(self.session_id, str) or not self.session_id or
            self.close_ns <= self.open_ns + 5 * MINUTE or not re.fullmatch(r"[0-9a-f]{64}", self.calendar_source_hash)):
            raise ContractError("invalid_session_calendar")


class Universe:
    """Append-only membership snapshots; preserve delistings and past membership."""
    def __init__(self):self.snapshots = []

    def observe(self, entries, *, known_ns, effective_ns, source_hash):
        integer(known_ns); integer(effective_ns)
        if not re.fullmatch(r"[0-9a-f]{64}", source_hash):
            raise ContractError("invalid_universe_provenance")
        ids = [e["instrument_id"] for e in entries]
        if len(ids) != len(set(ids)):
            raise ContractError("duplicate_universe_identity")
        self.snapshots.append(dict(entries=deepcopy(entries),known_ns=known_ns,effective_ns=effective_ns,source_hash=source_hash))

    def as_of(self, when):
        eligible = [s for s in self.snapshots if s["known_ns"] <= when and s["effective_ns"] <= when]
        if not eligible:return {}, None
        snapshot = max(enumerate(eligible), key=lambda x:(x[1]["effective_ns"],x[1]["known_ns"],x[0]))[1]
        return {e["instrument_id"]:deepcopy(e) for e in snapshot["entries"] if e.get("active") is True}, snapshot["source_hash"]


def feature_snapshot(market, key, session: Session, daily_history, *, as_of_ns, research=False):
    if as_of_ns < market.received_ns:
        raise ContractError("future_receipts_in_feature_state")
    state = market.instruments[key]
    missing = set(state.problems)
    if market.gaps:missing.add("unresolved_capture_gap")
    if key[2] == "live":missing.add("live_data_not_certified")
    if not research:missing.add("production_pipeline_not_certified")
    if state.halted is not False:missing.add("market_status_unknown_or_halted")
    expected = [session.open_ns + i * MINUTE for i in range(5)]
    bars = [state.bars.get((t,MINUTE)) for t in expected]
    if any(b is None for b in bars) or as_of_ns < session.open_ns + 5 * MINUTE:
        missing.add("opening_range_incomplete")
    history = [d for d in daily_history if d["instrument_id"] == key[3] and d["close_ns"] < session.open_ns
               and d["known_ns"] <= as_of_ns and d["provider"] == key[0] and d["feed"] == key[1]
               and d.get("origin") == key[2]]
    for d in history:
        for field in ("high", "low", "close", "volume", "dollar_volume", "open_volume"):
            value = d.get(field)
            if value is None:
                continue
            value = Decimal(str(value))
            if not value.is_finite() or value < 0:
                raise ContractError("invalid_historical_value")
        if not Decimal(d["low"]) <= Decimal(d["close"]) <= Decimal(d["high"]):
            raise ContractError("invalid_historical_ohlc")
        if d["known_ns"] < d["close_ns"] or not d.get("source_hash"):
            raise ContractError("historical_availability_unverified")
    history = sorted(history,key=lambda d:d["close_ns"])[-15:]
    if len({d["session_id"] for d in history}) != len(history):
        raise ContractError("duplicate_daily_session")
    if len(history) < 15:missing.add("fourteen_true_ranges_require_fifteen_closes")
    values = {}
    if len(history) >= 15:
        true_ranges = [max(Decimal(b["high"])-Decimal(b["low"]),abs(Decimal(b["high"])-Decimal(a["close"])),
                           abs(Decimal(b["low"])-Decimal(a["close"]))) for a,b in zip(history,history[1:])]
        for name, source in [("mean_daily_volume","volume"),("mean_dollar_volume","dollar_volume"),("mean_open_volume","open_volume")]:
            if any(d.get(source) is None for d in history[-14:]):missing.add("missing_"+source)
            else:values[name] = str(sum(Decimal(d[source]) for d in history[-14:])/14)
        values["atr14"] = str(sum(true_ranges)/14)
    if all(b is not None for b in bars) and "opening_range_incomplete" not in missing:
        values.update(opening_high=str(max(Decimal(b["payload"]["high"]) for b in bars)),
                      opening_low=str(min(Decimal(b["payload"]["low"]) for b in bars)),
                      opening_open=bars[0]["payload"]["open"], opening_close=bars[-1]["payload"]["close"],
                      opening_volume=str(sum(b["payload"]["volume_shares"] for b in bars)),
                      range_ready_ns=max(session.open_ns+5*MINUTE,max(b["received_ns"] for b in bars)))
        if Decimal(values.get("mean_open_volume","0")) > 0:
            values["opening_relative_volume"] = str(Decimal(values["opening_volume"])/Decimal(values["mean_open_volume"]))
        else:missing.add("opening_volume_baseline_unavailable")
    q=state.quote
    if q is None:missing.add("quote_unavailable")
    else:
        age=as_of_ns-q["source_time_ns"]
        if not 0 <= age <= 2_000_000_000:missing.add("quote_not_fresh")
        values.update(bid=q["payload"]["bid"],ask=q["payload"]["ask"],quote_age_ns=age,
                      ask_size_shares=market.quote_shares(q,"ask"),bid_size_shares=market.quote_shares(q,"bid"))
        if values["ask_size_shares"] is None or values["bid_size_shares"] is None:missing.add("quote_size_units_unverified")
    previous_minute=(as_of_ns//MINUTE)*MINUTE-MINUTE
    volume_bar=state.bars.get((previous_minute,MINUTE))
    values["preceding_minute_volume"] = volume_bar["payload"]["volume_shares"] if volume_bar else None
    if volume_bar is None:missing.add("preceding_minute_volume_missing")
    reference=market.references.get(q["identity_event_id"]) if q else None
    if reference is None or not reference["payload"].get("tick_size"):missing.add("tick_size_missing")
    else:values["tick_size"]=reference["payload"]["tick_size"]
    if not (reference and reference["payload"].get("corporate_actions_complete") is True):missing.add("corporate_action_coverage_unverified")
    if any(b and "trade_condition_policy_unapproved" in b["quality_flags"] for b in bars) and not (
        research and key[2] == "fixture" and reference and reference["payload"].get("fixture_condition_policy") is True):
        missing.add("trade_condition_policy_unapproved")
    data=dict(version=FEATURE_VERSION,session=asdict(session),key=list(key),symbol=state.symbol,as_of_ns=as_of_ns,
              watermark=market.watermark,state_hash=market.fingerprint(),values=values,missing=sorted(missing),
              history_hash=digest(history),evidence="fixture" if key[2]=="fixture" else "research_only",execution_authority="none")
    return {**data,"feature_hash":digest(data)}


class Discovery:
    """Rank available features and freeze the first complete session candidate set."""
    def __init__(self, limit=20):
        if type(limit) is not int or limit<1:raise ContractError("invalid_discovery_limit")
        self.limit,self.frozen,self.audit=limit,{},[]

    def freeze(self, session, snapshots, universe: Universe, *, as_of_ns):
        if session.session_id in self.frozen:return deepcopy(self.frozen[session.session_id])
        if as_of_ns < session.open_ns+5*MINUTE:raise ContractError("candidate_freeze_before_range_end")
        members,source=universe.as_of(as_of_ns)
        if source is None:raise ContractError("universe_unavailable")
        # The caller must cover the whole declared capture/research universe;
        # missing names cannot silently turn a partial scan into a complete rank.
        by_id={s["key"][3]:s for s in snapshots}
        if len(by_id)!=len(snapshots) or len({tuple(s["key"][:3]) for s in snapshots})>1:
            raise ContractError("mixed_or_duplicate_discovery_cohort")
        required={i for i,e in members.items() if e.get("asset_type")=="common_stock"}
        if not required <= set(by_id):raise ContractError("discovery_coverage_incomplete")
        if any("opening_range_incomplete" in by_id[i]["missing"] for i in required):
            raise ContractError("discovery_range_not_ready")
        candidates=[]
        for i in sorted(required):
            s=by_id[i];v=s["values"]
            if s["as_of_ns"]>as_of_ns:raise ContractError("future_discovery_features")
            if s["as_of_ns"] != as_of_ns or s["session"] != asdict(session):
                raise ContractError("stale_or_wrong_session_discovery_features")
            if digest({k:v for k,v in s.items() if k != "feature_hash"}) != s["feature_hash"]:
                raise ContractError("discovery_feature_hash_mismatch")
            if s["missing"]:continue
            if (Decimal(v["opening_close"])>5 and Decimal(v["mean_daily_volume"])>=1_000_000
                and Decimal(v["mean_dollar_volume"])>=10_000_000 and Decimal(v["atr14"])>Decimal("0.50")
                and Decimal(v["opening_close"])>Decimal(v["opening_open"]) and Decimal(v["opening_relative_volume"])>=2):
                candidates.append(s)
        candidates.sort(key=lambda s:(-Decimal(s["values"]["opening_relative_volume"]),s["key"][3]))
        result=deepcopy(candidates[:self.limit]);self.frozen[session.session_id]=result
        self.audit.append(dict(session=session.session_id,as_of_ns=as_of_ns,universe_hash=source,
                               candidates=[s["key"][3] for s in result],features=[s["feature_hash"] for s in result]))
        return deepcopy(result)
