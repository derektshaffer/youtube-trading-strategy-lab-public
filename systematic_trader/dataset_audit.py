"""Evidence-driven audit of real Alpaca exports; no profit or promotion claims."""
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .data_acquisition import verified_pages
from .events import ContractError, digest, integer, normalize_market, timestamp_ns
from .features import MINUTE

NY = ZoneInfo("America/New_York")


def calendar_sessions(rows):
    sessions = {}
    for r in rows:
        date = r["date"]
        if date in sessions:raise ContractError("duplicate_provider_calendar_session")
        values = {}
        for name, field in (("pre","session_open"),("open","open"),("close","close"),("post","session_close")):
            value = r.get(field)
            if not isinstance(value,str):raise ContractError("provider_calendar_extended_hours_missing")
            if len(value)==4 and value.isdigit():value=value[:2]+":"+value[2:]
            dt=datetime.fromisoformat(date+"T"+value).replace(tzinfo=NY)
            values[name]=timestamp_ns(dt.isoformat())
        if not values["pre"]<=values["open"]<values["close"]<=values["post"]:
            raise ContractError("invalid_provider_calendar_boundaries")
        if any(v%MINUTE for v in values.values()):raise ContractError("calendar_not_minute_aligned")
        sessions[date]=dict(**values,source_hash=digest(r))
    ordered=sorted(sessions.items())
    if any(a[1]["post"]>b[1]["pre"] for a,b in zip(ordered,ordered[1:])):
        raise ContractError("overlapping_provider_sessions")
    return sessions


def validate_bar(symbol, row):
    # Reuse the existing canonical adapter's exact Decimal/OHLC/nanosecond checks.
    kind,p,stamp,_=normalize_market(dict(T="b",S=symbol,**row))
    if stamp%MINUTE:raise ContractError("historical_bar_timestamp_unaligned")
    return stamp,p


def audit(directory):
    root=Path(directory)
    protocol=json.loads((root/"study-protocol.v2.json").read_text())
    if protocol["protocol_hash"]!=digest({k:v for k,v in protocol.items() if k!="protocol_hash"}):
        raise ContractError("historical_study_protocol_integrity_failure")
    symbols={s["symbol"]:s for s in protocol["selection"]}
    sessions=calendar_sessions([r for p,m in verified_pages(root/"calendar") for r in p])
    manifests={}
    for kind in ("assets","calendar","bars","actions","quotes","trades"):
        path=root/kind/"complete.json"
        if path.exists():manifests[kind]=json.loads(path.read_text())
    assets=[r for p,m in verified_pages(root/"assets") for r in p]
    stats={s:dict(bars=0,segments=Counter(),sessions={},missing_regular_minutes=0,missing_sessions=[],duplicate_records=0,
                  conflicting_duplicates=0,timestamp_order_errors=0,invalid_bars=0,outside_calendar=0,
                  outside_extended_session=0,first_ns=None,last_ns=None) for s in symbols}
    last={};seen={};invalid=[];daily={};observed_offsets=Counter()
    for page,meta in verified_pages(root/"bars"):
        for symbol,rows in page["bars"].items():
            if symbol not in symbols:raise ContractError("unrequested_historical_symbol")
            s=stats[symbol]
            for row in rows:
                try:stamp,p=validate_bar(symbol,row)
                except (ContractError,TypeError,KeyError):
                    s["invalid_bars"]+=1
                    if len(invalid)<50:invalid.append(dict(symbol=symbol,source_sha256=meta["sha256"],row_hash=digest(row)))
                    continue
                key=(symbol,stamp);hash_value=digest(row)
                if key in seen:
                    s["duplicate_records"]+=1
                    if seen[key]!=hash_value:s["conflicting_duplicates"]+=1
                    continue
                seen[key]=hash_value
                if stamp<last.get(symbol,0):s["timestamp_order_errors"]+=1
                last[symbol]=stamp;s["bars"]+=1
                s["first_ns"]=stamp if s["first_ns"] is None else min(s["first_ns"],stamp)
                s["last_ns"]=stamp if s["last_ns"] is None else max(s["last_ns"],stamp)
                local=datetime.fromtimestamp(stamp//1_000_000_000,NY)
                day=local.date().isoformat();observed_offsets[str(local.utcoffset())]+=1
                cal=sessions.get(day)
                if cal is None:s["outside_calendar"]+=1;continue
                if not cal["pre"]<=stamp<cal["post"]:s["outside_extended_session"]+=1;continue
                segment="pre" if stamp<cal["open"] else "regular" if stamp<cal["close"] else "post"
                s["segments"][segment]+=1
                session=s["sessions"].setdefault(day,dict(pre=0,regular=0,post=0,regular_starts=set()))
                session[segment]+=1
                if segment=="regular":
                    session["regular_starts"].add(stamp)
                    d=daily.setdefault((symbol,day),dict(open=p["open"],last_close=p["close"],high=p["high"],low=p["low"],volume=0,dollar_volume=Decimal(0)))
                    d["last_close"]=p["close"];d["high"]=str(max(Decimal(d["high"]),Decimal(p["high"])));d["low"]=str(min(Decimal(d["low"]),Decimal(p["low"])))
                    d["volume"]+=p["volume_shares"];d["dollar_volume"]+=Decimal(p["vwap"])*p["volume_shares"]
    for symbol,s in stats.items():
        for day,cal in sorted(sessions.items()):
            session=s["sessions"].get(day)
            starts=session.pop("regular_starts") if session else set()
            if session is None or not session["regular"]:
                s["missing_sessions"].append(day)
                s["missing_regular_minutes"]+=(cal["close"]-cal["open"])//MINUTE
            else:
                missing=[t for t in range(cal["open"],cal["close"],MINUTE) if t not in starts]
                session["missing_regular_minutes"]=len(missing)
                session["missing_regular_starts_sample"]=missing[:10]
                s["missing_regular_minutes"]+=len(missing)
        s["segments"]=dict(s["segments"])
        s["current_catalog_status"]=symbols[symbol]["status"]
        s["identity_coverage"]="current_asset_id_not_historical_effective_intervals"
    actions=[]
    if "actions" in manifests:
        for page,meta in verified_pages(root/"actions"):
            for kind,rows in page["corporate_actions"].items():
                for row in rows:actions.append(dict(type=kind,record=row,source_sha256=meta["sha256"]))
    discontinuities=[]
    for symbol in symbols:
        days=sorted((day,d) for (sym,day),d in daily.items() if sym==symbol)
        for (previous,a),(day,b) in zip(days,days[1:]):
            ratio=Decimal(b["open"])/Decimal(a["last_close"])
            if ratio<Decimal("0.65") or ratio>Decimal("1.5"):
                discontinuities.append(dict(symbol=symbol,previous=previous,day=day,raw_open_to_previous_close=str(ratio),resolution="unresolved_action_or_market_move"))
    micro={}
    for kind in ("quotes","trades"):
        result=dict(records=0,by_symbol=Counter(),invalid=0,duplicate_content=0,order_errors=0,locked_or_crossed=0,
                    quote_spread_fractions=[],coverage="bounded_opening_sample_not_full_strategy_period")
        seen_content=set();clocks={}
        if kind in manifests:
            for page,meta in verified_pages(root/kind):
                for symbol,rows in page[kind].items():
                    for row in rows:
                        result["records"]+=1;result["by_symbol"][symbol]+=1
                        try:
                            _,p,stamp,_=normalize_market(dict(T="q" if kind=="quotes" else "t",S=symbol,**row))
                            if stamp<clocks.get(symbol,0):result["order_errors"]+=1
                            clocks[symbol]=stamp
                            h=digest([symbol,row])
                            if h in seen_content:result["duplicate_content"]+=1
                            seen_content.add(h)
                            if kind=="quotes":
                                bid,ask=Decimal(p["bid"]),Decimal(p["ask"])
                                if not 0<bid<ask:result["locked_or_crossed"]+=1
                                else:result["quote_spread_fractions"].append((ask-bid)/((ask+bid)/2))
                        except (ContractError,TypeError,KeyError):result["invalid"]+=1
        spreads=sorted(result.pop("quote_spread_fractions"))
        if spreads:
            result["pooled_observation_weighted_spread_bps"]={name:str(spreads[min(len(spreads)-1,int(len(spreads)*q))]*10000) for name,q in (("p50",.5),("p90",.9),("p99",.99))}
        result["by_symbol"]=dict(result["by_symbol"]);micro[kind]=result
    blockers=["historical_first_publication_and_revision_availability_unverified",
              "point_in_time_universe_membership_and_delisting_intervals_unverified",
              "historical_symbol_identity_intervals_unverified",
              "historical_halt_luld_and_condition_eligibility_coverage_unverified",
              "full_execution_quote_trade_coverage_missing",
              "historical_corporate_action_completeness_and_known_time_unverified"]
    for field in ("invalid_bars","timestamp_order_errors","conflicting_duplicates","outside_calendar","outside_extended_session"):
        if sum(s[field] for s in stats.values()):blockers.append(field)
    if any(s["missing_regular_minutes"] for s in stats.values()):blockers.append("missing_bar_intervals_require_no_trade_halt_or_listing_resolution")
    if discontinuities:blockers.append("unresolved_price_discontinuities")
    if set(manifests)!={"assets","calendar","bars","actions","quotes","trades"}:blockers.append("incomplete_required_acquisitions")
    manifest=dict(version="real-dataset-audit-v1",provider="alpaca",feed="sip",adjustment="raw",asof_symbol_mapping="disabled_explicit_dash",
                  requested_coverage=["2024-12-02","2025-04-30"],requested_symbols=list(symbols),resolution="1Min; bounded native quote/trade sample",
                  protocol_hash=protocol["protocol_hash"],sources=manifests,calendar_sessions=len(sessions),
                  asset_catalog=dict(records=len(assets),statuses=dict(Counter(a["status"] for a in assets)),known_at="retrieval_only"),
                  bar_count=sum(s["bars"] for s in stats.values()),symbols_with_bars=sum(s["bars"]>0 for s in stats.values()),
                  by_symbol=stats,timezone_offsets_observed=dict(observed_offsets),invalid_samples=invalid,
                  corporate_actions=actions,raw_discontinuities=discontinuities,microstructure=micro,
                  classification="NOT_CERTIFIED",certified=False,blocking_findings=blockers,
                  missing_bar_semantics="Absent minute is unresolved; provider does not emit bars without qualifying price trades. Not asserted packet loss.",
                  holdout_result="NOT_REQUESTED_NOT_EVALUATED",profitability_evaluation_allowed=False,execution_authority="none")
    manifest["dataset_manifest_hash"]=digest(manifest)
    return manifest
