"""Interpretable, causal intraday watchlists; no execution authority.

The ORB baseline remains unchanged. This complementary research discovery path
can rank a changing point-in-time universe beyond the capture test symbols.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal

from .events import ContractError, digest, timestamp_ns
from .features import MINUTE, Session, Universe

D = Decimal


@dataclass(frozen=True)
class MomentumPolicy:
    version: str = "momentum-watchlist-v1"
    minimum_history_sessions: int = 14
    limit: int = 20
    minimum_price: str = "1"
    minimum_dollar_volume: str = "1000000"
    minimum_relative_volume: str = "2"

    def __post_init__(self):
        if (type(self.minimum_history_sessions) is not int or self.minimum_history_sessions < 1 or
                type(self.limit) is not int or self.limit < 1):
            raise ContractError("invalid_momentum_windows")
        for x in (self.minimum_price, self.minimum_dollar_volume, self.minimum_relative_volume):
            if not D(x).is_finite() or D(x) <= 0:
                raise ContractError("invalid_momentum_threshold")


def intraday_values(p, prior, elapsed):
    """Shared arithmetic for already selected bar prefixes; grants no admission.

    Production/replay callers retain all state, identity and coverage gates.
    Bounded archive diagnostics may reuse arithmetic with explicit limitations.
    """
    values = {}; missing = set()
    if p:
        closes = [D(b["close"]) for b in p]
        volumes = [b["volume_shares"] for b in p]
        high, low = max(D(b["high"]) for b in p), min(D(b["low"]) for b in p)
        volume = sum(volumes)
        dollars = sum(D(b["vwap"])*b["volume_shares"] for b in p)
        values.update(last_close=str(closes[-1]), session_volume=volume, session_dollar_volume=str(dollars),
                      session_high=str(high), session_low=str(low), elapsed_minutes=elapsed,
                      pullback_from_high=str((high-closes[-1])/high),
                      return_from_open=str(closes[-1]/D(p[0]["open"])-1))
        if volume and dollars > 0:
            vwap = dollars/volume
            values.update(vwap=str(vwap), vwap_distance=str(closes[-1]/vwap-1))
        else:
            missing.add("vwap_unavailable")
        if len(p) >= 6:
            returns = [b/a-1 for a,b in zip(closes, closes[1:])]
            previous_high = max(D(b["high"]) for b in p[:-1])
            recent_range = sum(D(b["high"])-D(b["low"]) for b in p[-3:])
            prior_range = sum(D(b["high"])-D(b["low"]) for b in p[-6:-3])
            values.update(return_1m=str(returns[-1]), return_3m=str(closes[-1]/closes[-4]-1),
                          price_acceleration=str(returns[-1]-returns[-2]),
                          prior_intraday_high=str(previous_high), breakout=closes[-1] > previous_high,
                          higher_lows=D(p[-3]["low"]) < D(p[-2]["low"]) < D(p[-1]["low"]),
                          vwap_reclaimed=False)
            prior_volume = sum(volumes[-6:-3])
            values["volume_acceleration"] = str(D(sum(volumes[-3:]))/prior_volume) if prior_volume else None
            values["range_expansion"] = str(recent_range/prior_range) if prior_range else None
            previous_volume = sum(volumes[:-1])
            if previous_volume:
                previous_vwap = sum(D(b["vwap"])*b["volume_shares"] for b in p[:-1])/previous_volume
                values["vwap_reclaimed"] = closes[-2] <= previous_vwap and D(values.get("vwap_distance", "0")) > 0
        profiles_ok = prior and all(isinstance(h.get("minute_volumes"), list) and len(h["minute_volumes"]) >= elapsed for h in prior)
        if profiles_ok:
            expected = sum(D(sum(h["minute_volumes"][:elapsed])) for h in prior)/len(prior)
            values["relative_volume_at_time"] = str(D(volume)/expected) if expected > 0 else None
        if prior:
            values.update(prior_close=prior[-1]["close"], prior_high=prior[-1]["high"], prior_low=prior[-1]["low"],
                          gap_from_prior_close=str(D(p[0]["open"])/D(prior[-1]["close"])-1))
    if values.get("relative_volume_at_time") is None:
        missing.add("relative_volume_at_time_unavailable")
    return values, missing


def intraday_snapshot(market, key, session: Session, history, *, as_of_ns, policy=MomentumPolicy()):
    if as_of_ns < market.received_ns:
        raise ContractError("future_receipts_in_intraday_state")
    if not session.open_ns <= as_of_ns < session.close_ns:
        raise ContractError("intraday_snapshot_outside_session")
    state = market.instruments[key]
    missing = set(state.problems)
    if market.gaps:
        missing.add("unresolved_capture_gap")
    elapsed = (as_of_ns-session.open_ns)//MINUTE
    bars = [state.bars.get((session.open_ns+i*MINUTE, MINUTE)) for i in range(elapsed)]
    if elapsed < 6:
        missing.add("six_completed_minutes_required")
    if any(b is None for b in bars):
        missing.add("intraday_bar_coverage_incomplete")
    # History is selected BEFORE aggregation. Current/future sessions and late
    # revisions cannot enter either relative volume or prior-day price levels.
    prior = [deepcopy(h) for h in history if (h["provider"], h["feed"], h["origin"], h["instrument_id"]) == key
             and h["close_ns"] < session.open_ns and h["known_ns"] <= as_of_ns]
    prior.sort(key=lambda h:h["close_ns"])
    if len({h["session_id"] for h in prior}) != len(prior):
        raise ContractError("duplicate_intraday_history_session")
    prior = prior[-policy.minimum_history_sessions:]
    for h in prior:
        if h["known_ns"] < h["close_ns"] or not h.get("source_hash"):
            raise ContractError("intraday_history_availability_unverified")
        prices = [D(h[n]) for n in ("high", "low", "close")]
        if any(not p.is_finite() or p <= 0 for p in prices) or not prices[1] <= prices[2] <= prices[0]:
            raise ContractError("invalid_intraday_history_prices")
        profile = h.get("minute_volumes")
        if profile is None or not isinstance(profile, list) or any(type(v) is not int or v < 0 for v in profile):
            missing.add("historical_minute_volume_profile_unavailable")
        elif len(profile) < elapsed:
            # Do not extrapolate an early-close session into a later time bucket.
            missing.add("historical_time_bucket_unavailable")
        if h.get("adjustment", "unadjusted") != "unadjusted":
            missing.add("mixed_price_adjustment_basis")
    if len(prior) < policy.minimum_history_sessions:
        missing.add("insufficient_intraday_history")
    p = [b["payload"] for b in bars] if bars and all(b is not None for b in bars) else []
    values, value_missing = intraday_values(p, prior, elapsed)
    missing.update(value_missing)
    # Unadjusted series across a known split/distribution cannot silently be
    # treated as economic returns; adjusting it requires a separate PIT policy.
    for ref in market.references.values():
        if ref["event_type"] == "reference.corporate_action" and ref["payload"]["instrument_id"] == key[3] and prior:
            if prior[0]["close_ns"] < timestamp_ns(ref["payload"]["effective_at"]) <= as_of_ns:
                missing.add("corporate_action_boundary_unadjusted")
    execution_missing = {"production_pipeline_not_certified", "corporate_action_coverage_unverified", "trade_condition_policy_unapproved"}
    q = state.quote
    if q is None or not 0 <= as_of_ns-q["source_time_ns"] <= 2_000_000_000:
        execution_missing.add("fresh_quote_unavailable")
    else:
        bid, ask = D(q["payload"]["bid"]), D(q["payload"]["ask"])
        if not 0 < bid < ask:
            execution_missing.add("unusable_quote")
        else:
            values["spread_fraction"] = str((ask-bid)/((ask+bid)/2))
        if market.quote_shares(q,"ask") is None or market.quote_shares(q,"bid") is None:
            execution_missing.add("quote_size_units_unverified")
    if state.halted is not False:
        execution_missing.add("market_status_unknown_or_halted")
    data = dict(version="intraday-momentum-features-v1", policy=asdict(policy), key=list(key), symbol=state.symbol,
                session=asdict(session), as_of_ns=as_of_ns, watermark=market.watermark, state_hash=market.fingerprint(),
                history_hash=digest(prior), values=values, missing=sorted(missing), execution_missing=sorted(execution_missing),
                evidence="fixture" if key[2] == "fixture" else "research_only", production_eligible=False, execution_authority="none")
    return {**data, "feature_hash":digest(data)}


def rank_movers(snapshots, universe: Universe, session: Session, *, as_of_ns, policy=MomentumPolicy()):
    """Re-rank each point-in-time observation; results are immutable watchlists.

    Ranking is ordinal and interpretable, not a calibrated profit probability.
    Missing execution inputs remain visible even for an eligible watchlist name.
    """
    members, universe_hash = universe.as_of(as_of_ns)
    if universe_hash is None:
        raise ContractError("momentum_universe_unavailable")
    required = {i for i,e in members.items() if e.get("asset_type") == "common_stock"}
    by_id = {s["key"][3]:s for s in snapshots}
    if len(by_id) != len(snapshots) or len({tuple(s["key"][:3]) for s in snapshots}) > 1:
        raise ContractError("mixed_or_duplicate_momentum_cohort")
    if not required <= by_id.keys():
        raise ContractError("momentum_universe_coverage_incomplete")
    candidates, excluded = [], []
    for i in sorted(required):
        s = by_id[i]
        if s["as_of_ns"] != as_of_ns or s["session"] != asdict(session) or s["policy"] != asdict(policy):
            raise ContractError("momentum_feature_context_mismatch")
        if s["feature_hash"] != digest({k:v for k,v in s.items() if k != "feature_hash"}):
            raise ContractError("momentum_feature_hash_mismatch")
        if s["symbol"] != members[i]["symbol"]:
            raise ContractError("momentum_membership_symbol_mismatch")
        v = s["values"]
        reasons = list(s["missing"])
        if not reasons:
            if D(v["last_close"]) < D(policy.minimum_price):reasons.append("price_filter")
            if D(v["session_dollar_volume"]) < D(policy.minimum_dollar_volume):reasons.append("dollar_liquidity_filter")
            if D(v["relative_volume_at_time"]) < D(policy.minimum_relative_volume):reasons.append("relative_volume_filter")
            if D(v["return_3m"]) <= 0:reasons.append("positive_continuation_required")
        if reasons:
            excluded.append(dict(instrument_id=i, reasons=sorted(reasons), feature_hash=s["feature_hash"]))
            continue
        signals = ["unusual_volume", "positive_three_minute_return"]
        for flag in ("breakout", "higher_lows", "vwap_reclaimed"):
            if v.get(flag):signals.append(flag)
        if v.get("volume_acceleration") and D(v["volume_acceleration"]) > 1:signals.append("volume_acceleration")
        if v.get("range_expansion") and D(v["range_expansion"]) > 1:signals.append("range_expansion")
        candidates.append(dict(instrument_id=i, symbol=s["symbol"], signals=signals, feature_hash=s["feature_hash"],
                               relative_volume=v["relative_volume_at_time"], return_3m=v["return_3m"],
                               execution_missing=s["execution_missing"], execution_authority="none"))
    candidates.sort(key=lambda c:(-D(c["relative_volume"]), -D(c["return_3m"]), c["instrument_id"]))
    data = dict(version=policy.version, policy=asdict(policy), session=asdict(session), as_of_ns=as_of_ns,
                universe_hash=universe_hash, features=[by_id[i]["feature_hash"] for i in sorted(required)],
                candidates=deepcopy(candidates[:policy.limit]), excluded=excluded,
                evidence="research_watchlist_only", production_eligible=False, execution_authority="none")
    return {**data, "ranking_hash":digest(data)}


def historical_scan(view, *, session_id, policy=MomentumPolicy()):
    """Connect archived membership, calendar, bars and daily profiles to ranking."""
    from .historical import daily_history
    calendar = view["calendars"].get(session_id)
    if calendar is None:
        raise ContractError("historical_scan_calendar_unavailable")
    session = Session(session_id, timestamp_ns(calendar["open"]), timestamp_ns(calendar["close"]), digest(calendar))
    when = view["manifest"]["as_of_ns"]
    members, source = view["universe"].as_of(when)
    if source is None:
        raise ContractError("historical_scan_universe_unavailable")
    required = {i for i,e in members.items() if e.get("asset_type") == "common_stock"}
    market = view["market"]
    keys = {k[3]:k for k in market.instruments}
    if not required <= keys.keys():
        raise ContractError("historical_scan_market_coverage_incomplete")
    history = daily_history(view)
    snapshots = [intraday_snapshot(market, keys[i], session, history, as_of_ns=when, policy=policy) for i in sorted(required)]
    ranking = rank_movers(snapshots, view["universe"], session, as_of_ns=when, policy=policy)
    return dict(view=view["manifest"], features=snapshots, ranking=ranking, production_eligible=False, execution_authority="none")
