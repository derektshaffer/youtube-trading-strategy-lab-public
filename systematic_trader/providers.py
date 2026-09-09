"""Pure provider boundary. Decode/normalize only after the journal commits raw bytes."""
from dataclasses import dataclass, field
from typing import Protocol

from .events import ContractError, integer, normalize_market, number, text_field, SYMBOL


@dataclass(frozen=True)
class MarketObservation:
    kind: str
    payload: dict
    source_ns: int | None
    symbol: str | None
    source_id: str | None = None
    sequence: int | None = None
    flags: tuple = ()


class ProviderAdapter(Protocol):
    provider: str
    feed: str
    version: str
    schema_version: int

    def decode(self, raw: bytes) -> list[dict]: ...
    def normalize(self, message: dict) -> MarketObservation: ...


class AlpacaSIP:
    provider, feed, version, schema_version = "alpaca", "sip", "alpaca-sip-v1", 1

    def decode(self, raw):
        from .ledger import strict_json
        messages = strict_json(raw)
        if not isinstance(messages, list) or not messages or len(messages) > 10000:
            raise ContractError("invalid_message_batch")
        return messages

    def normalize(self, message):
        kind, payload, stamp, source_id = normalize_market(message)
        return MarketObservation(kind, payload, stamp, message["S"], source_id)


def provider_integer(value):
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 19:
        value = int(value)
    return integer(value)


def milliseconds_ns(value):
    # No seconds/milliseconds heuristic and no manufactured sub-millisecond precision.
    result = provider_integer(value) * 1_000_000
    if not 0 < result < 2**63:
        raise ContractError("invalid_millisecond_timestamp")
    return result


class TradierConsolidated:
    provider, feed, version, schema_version = "tradier", "tradier_consolidated", "tradier-stream-v1", 2

    def decode(self, raw):
        from .ledger import strict_json
        # A websocket message may carry newline-delimited JSON. Every whole
        # message has already been persisted, including its original separators.
        try:
            value = strict_json(raw)
            result = value if isinstance(value, list) else [value]
        except ValueError:
            result = [strict_json(line) for line in raw.splitlines() if line.strip()]
        if not result or len(result) > 10000:
            raise ContractError("invalid_message_batch")
        return result

    def normalize(self, m):
        if "error" in m:
            return MarketObservation("provider.control", {"type": "error", "reason": "tradier_stream_rejected"}, None, None)
        kind = m.get("type")
        if kind not in {"timesale", "quote"}:
            raise ContractError("unsupported_tradier_message")
        symbol = m.get("symbol")
        if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
            # Slash/class mappings require an explicit instrument reference map.
            raise ContractError("unmapped_provider_symbol")
        flags = ["timestamp_precision_milliseconds", "trade_conditions_unavailable", "halt_coverage_unverified"]
        meta = {"native_type": kind, "timestamp_precision_ns": 1_000_000}
        if kind == "quote":
            bid_ns, ask_ns = milliseconds_ns(m.get("biddate")), milliseconds_ns(m.get("askdate"))
            meta.update(bid_time_ns=bid_ns, ask_time_ns=ask_ns)
            payload = dict(bid=number(m.get("bid")), ask=number(m.get("ask")),
                           bid_size=provider_integer(m.get("bidsz")), ask_size=provider_integer(m.get("asksz")),
                           bid_exchange=text_field(m.get("bidexch")), ask_exchange=text_field(m.get("askexch")),
                           size_unit="provider_unspecified", round_lot_size=None, conditions=None, tape=None,
                           provider_details=meta)
            flags.append("quote_size_conversion_unverified")
            # Oldest side controls freshness; both timestamps retained.
            return MarketObservation("market.quote", payload, min(bid_ns, ask_ns), symbol, flags=tuple(flags))
        stamp, seq = milliseconds_ns(m.get("date")), provider_integer(m.get("seq"))
        if type(m.get("cancel")) is not bool or type(m.get("correction")) is not bool:
            raise ContractError("amendment_flags_missing")
        if not isinstance(m.get("flag"), str) or not isinstance(m.get("session"), str):
            raise ContractError("timesale_context_missing")
        meta.update(session=m["session"], flag=m["flag"], cancel=m["cancel"], correction=m["correction"],
                    sequence_scope="unverified", sequence_contiguous=False,
                    at_event_bid=number(m.get("bid")), at_event_ask=number(m.get("ask")))
        flags.extend(["sequence_scope_unverified", "timesale_eligibility_unapproved"])
        price, size, exchange = number(m.get("last"), positive=True), provider_integer(m.get("size")), text_field(m.get("exch"))
        if m["cancel"] and m["correction"]:
            raise ContractError("ambiguous_amendment_flags")
        if m["cancel"]:
            payload = dict(original_trade_id=None, action="C", price=price, size_shares=size,
                           exchange=exchange, tape=None, provider_details=meta)
            kind = "market.trade_cancel"
            flags.append("unresolved_amendment_link")
        elif m["correction"]:
            payload = dict(original_trade_id=None, corrected_trade_id=None, original_price=None,
                           corrected_price=price, original_size_shares=None, corrected_size_shares=size,
                           original_conditions=None, corrected_conditions=None, exchange=exchange,
                           tape=None, provider_details=meta)
            kind = "market.trade_correction"
            flags.append("unresolved_amendment_link")
        else:
            payload = dict(price=price, size_shares=size, exchange=exchange, conditions=None, tape=None, provider_details=meta)
            kind = "market.trade"
        return MarketObservation(kind, payload, stamp, symbol, sequence=seq, flags=tuple(flags))


class AlpacaIEX(AlpacaSIP):
    """Same wire fields, explicitly single-venue coverage; never relabeled SIP."""
    feed, version = 'iex', 'alpaca-iex-partial-v1'

    def normalize(self, message):
        from dataclasses import replace
        observation = super().normalize(message)
        return replace(observation, flags=(*observation.flags, 'IEX_only_not_consolidated',
            'status_LULD_coverage_unavailable', 'sequence_continuity_unavailable'))


ADAPTERS = {("alpaca", "sip"): AlpacaSIP, ("alpaca", "iex"): AlpacaIEX,
            ("tradier", "tradier_consolidated"): TradierConsolidated}


def adapter_for(provider, feed, version=None):
    try:
        adapter = ADAPTERS[(provider, feed)]()
    except KeyError:
        raise ContractError("unknown_provider_feed") from None
    if version is not None and adapter.version != version:
        raise ContractError("unavailable_adapter_version")
    return adapter


class RecordedSource:
    """Replay transport shares the canonical consumer boundary, never live credentials."""
    def __init__(self, ledger, through_seq):
        self.ledger, self.through_seq = ledger, through_seq

    def __iter__(self):
        yield from self.ledger.replay(through_seq=self.through_seq)
