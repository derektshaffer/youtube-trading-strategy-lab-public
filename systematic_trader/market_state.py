"""Causal state shared by live, imported and replay sources; no provider clients."""
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal

from .events import ContractError, Event, digest

STATE_VERSION = "point-in-time-market-state-v2"


@dataclass
class InstrumentState:
    key: tuple
    symbol: str
    symbol_time_ns: int = 0
    trades: dict = field(default_factory=dict)
    bars: dict = field(default_factory=dict)
    quote: dict | None = None
    halted: bool | None = None
    status_time_ns: int = 0
    problems: set = field(default_factory=set)
    seen: set = field(default_factory=set)


class MarketState:
    def __init__(self):
        self.watermark = 0
        self.received_ns = 0
        self.instruments = {}
        self.references = {}
        self.gaps = set()
        self.counts = {}

    def apply(self, event):
        e = deepcopy(event)
        seq = e.pop("ledger_seq")
        Event(**e).validate()
        if type(seq) is not int or seq <= self.watermark:
            raise ContractError("nonmonotonic_committed_watermark")
        if seq != self.watermark+1:
            raise ContractError("missing_committed_event_in_state_replay")
        self.watermark, self.received_ns = seq, max(self.received_ns, e["received_ns"])
        kind, p = e["event_type"], e["payload"]
        self.counts[kind] = self.counts.get(kind, 0) + 1
        if kind.startswith("reference."):
            self.references[e["event_id"]] = e
        if kind in {"recorder.gap", "recorder.reject"}:
            self.gaps.add(e["event_id"])
        if not kind.startswith("market."):
            return
        if e["instrument_id"] is None:
            return  # Receipt remains in journal; no ticker-as-identity fallback.
        key = (e["provider"], e["feed"], e["origin"], e["instrument_id"])
        state = self.instruments.setdefault(key, InstrumentState(key, e["symbol"]))
        if e["content_hash"] in state.seen:
            return
        state.seen.add(e["content_hash"])
        flags = set(e["quality_flags"])
        if flags & {"future_source_time", "clock_regression", "sequence_jump_unverified", "sequence_repeat_or_reorder"}:
            state.problems.update(flags & {"future_source_time", "clock_regression", "sequence_jump_unverified", "sequence_repeat_or_reorder"})
        if "future_source_time" in flags or e["source_time_ns"] > e["received_ns"]:
            state.problems.add("future_source_time")
            return
        if e["source_time_ns"] >= state.symbol_time_ns:
            state.symbol, state.symbol_time_ns = e["symbol"], e["source_time_ns"]
        if kind == "market.quote":
            if "unusable_quote" in flags:
                state.quote = None
            elif state.quote is None or e["source_time_ns"] >= state.quote["source_time_ns"]:
                state.quote = e
        elif kind in {"market.bar", "market.bar_revision"}:
            if e["source_time_ns"] + p["interval_ns"] > e["received_ns"]:
                state.problems.add("incomplete_bar_received");return
            bar_key = (e["source_time_ns"], p["interval_ns"])
            previous = state.bars.get(bar_key)
            if previous:
                prior = previous["payload"]
                # Historical exports carry explicit revision ordinals. An old
                # original delivered later cannot replace a known revision.
                revision = p.get("provider_details", {}).get("revision")
                old_revision = prior.get("provider_details", {}).get("revision")
                if revision is not None and old_revision is not None:
                    if revision < old_revision:
                        return
                    if revision == old_revision:
                        values = lambda b: {k: v for k, v in b.items() if k != "provider_details"}
                        if values(p) != values(prior):
                            state.problems.add("conflicting_bar_revision")
                        return
                elif prior["revision"] and not p["revision"]:
                    return
                elif not prior["revision"] and not p["revision"] and prior != p:
                    state.problems.add("conflicting_original_bar")
                    return
            state.bars[bar_key] = e
        elif kind == "market.trade":
            key = self.trade_key(e, e["source_event_id"] or e["event_id"])
            if key in state.trades and state.trades[key]["content_hash"] != e["content_hash"]:
                state.problems.add("trade_identity_collision")
            else:
                state.trades[key] = e
        elif kind in {"market.trade_cancel", "market.trade_correction"}:
            original = p.get("original_trade_id")
            # Unknown original identity or day cannot be safely reverse-applied.
            candidates = [k for k in state.trades if k[1:] == (p.get("exchange"), original)] if original else []
            if len(candidates) != 1:
                state.problems.add("unresolved_amendment_link");return
            state.trades.pop(candidates[0])
            if kind == "market.trade_correction":
                corrected = deepcopy(e)
                corrected["source_time_ns"] = candidates[0][0]
                corrected["payload"] = dict(price=p["corrected_price"], size_shares=p["corrected_size_shares"], exchange=p["exchange"])
                state.trades[(candidates[0][0],p["exchange"],p["corrected_trade_id"])] = corrected
            state.problems.add("bar_reconciliation_required")
        elif kind == "market.status":
            if e["source_time_ns"] < state.status_time_ns:return
            state.status_time_ns=e["source_time_ns"]
            code = p["status_code"]
            if code in {"H", "2", "P"}:state.halted = True
            elif code in {"T", "3"}:state.halted = False
            else:state.halted = None
            # Quote resumption alone doesn't imply trade resumption.

    @staticmethod
    def trade_key(e, identifier):
        return (e["source_time_ns"], e["payload"]["exchange"], identifier)

    def quote_shares(self, quote, side):
        p = quote["payload"]
        size = p[side + "_size"]
        if p["size_unit"] == "shares":return size
        if p["size_unit"] != "provider_round_lots":return None
        reference = self.references.get(quote["identity_event_id"])
        lot = reference["payload"].get("round_lot_size") if reference else None
        if type(lot) is not int or lot <= 0:return None
        return size * lot

    def fingerprint(self):
        return digest({"version":STATE_VERSION,"watermark":self.watermark,"references":self.references,"gaps":sorted(self.gaps),
                       "instruments":{str(k):{"symbol":v.symbol,"symbol_time_ns":v.symbol_time_ns,"quote":v.quote,"trades":[v.trades[t] for t in sorted(v.trades)],
                         "bars":[v.bars[b] for b in sorted(v.bars)],"halted":v.halted,"status_time_ns":v.status_time_ns,"problems":sorted(v.problems)}
                         for k,v in sorted(self.instruments.items())}})
