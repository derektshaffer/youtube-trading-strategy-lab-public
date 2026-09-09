"""Versioned event envelope and lossless Alpaca SIP normalization.

Integer UTC nanoseconds never pass through a floating point timestamp. Prices
are decimal strings. Raw provider units and conditions remain available.
"""
from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any

SCHEMA_VERSION = 1
ADAPTER_VERSION = "alpaca-sip-v1"
KINDS = {
    "t": "market.trade", "q": "market.quote", "b": "market.bar",
    "u": "market.bar_revision", "c": "market.trade_correction",
    "x": "market.trade_cancel", "s": "market.status", "l": "market.luld",
}
EVENT_TYPES = frozenset(KINDS.values()) | {
    "reference.instrument", "reference.corporate_action", "reference.calendar",
    "recorder.lifecycle", "recorder.gap", "recorder.heartbeat", "recorder.reject",
    "provider.control",
}
RESERVED_TYPES = frozenset({
    "strategy.candidate", "strategy.decision", "risk.decision", "order.intent",
    "order.ack", "order.fill", "order.cancel", "broker.reconciliation",
    "position.snapshot", "trade.outcome", "registry.promotion",
})
SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,15}\Z")
STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})\Z")

TEXT_VALUE = {"type": "string", "minLength": 1}
DECIMAL_VALUE = {"type": "string", "pattern": r"^[0-9]+(?:\.[0-9]+)?$"}
INTEGER_VALUE = {"type": "integer", "minimum": 0, "maximum": 2**63 - 1}
CONDITIONS_VALUE = {"type": "array", "items": {"type": "string"}}
NULLABLE_TEXT = {"type": ["string", "null"]}


def payload_contracts(version=1):
    """One source for runtime type checks and the exported JSON Schema."""
    trade = {"price": DECIMAL_VALUE, "size_shares": INTEGER_VALUE, "exchange": TEXT_VALUE, "conditions": CONDITIONS_VALUE, "tape": TEXT_VALUE}
    bar = {**{k: DECIMAL_VALUE for k in ("open", "high", "low", "close", "vwap")},
           "volume_shares": INTEGER_VALUE, "trade_count": INTEGER_VALUE, "interval_ns": {"const": 60_000_000_000},
           "revision": {"type": "boolean"}, "revision_key": TEXT_VALUE, "tape": NULLABLE_TEXT}
    contracts = {
        "market.trade": trade,
        "market.quote": {"bid": DECIMAL_VALUE, "ask": DECIMAL_VALUE, "bid_size": INTEGER_VALUE, "ask_size": INTEGER_VALUE,
                         "bid_exchange": TEXT_VALUE, "ask_exchange": TEXT_VALUE, "conditions": CONDITIONS_VALUE,
                         "tape": TEXT_VALUE, "size_unit": {"const": "provider_round_lots"}, "round_lot_size": {"const": None}},
        "market.bar": {**bar, "revision": {"const": False}},
        "market.bar_revision": {**bar, "revision": {"const": True}},
        "market.trade_correction": {**{k: TEXT_VALUE for k in ("original_trade_id", "corrected_trade_id", "exchange", "tape")},
                                    **{k: DECIMAL_VALUE for k in ("original_price", "corrected_price")},
                                    **{k: INTEGER_VALUE for k in ("original_size_shares", "corrected_size_shares")},
                                    **{k: CONDITIONS_VALUE for k in ("original_conditions", "corrected_conditions")}},
        "market.trade_cancel": {k: v for k, v in {**trade, "original_trade_id": TEXT_VALUE, "action": {"enum": ["C", "E"]}}.items() if k != "conditions"},
        "market.status": {k: TEXT_VALUE for k in ("status_code", "reason_code", "tape")},
        "market.luld": {"limit_up": DECIMAL_VALUE, "limit_down": DECIMAL_VALUE, "indicator": TEXT_VALUE, "tape": TEXT_VALUE},
        "reference.instrument": {"symbol": TEXT_VALUE, "instrument_id": TEXT_VALUE, "effective_from_ns": INTEGER_VALUE,
                                 "effective_to_ns": INTEGER_VALUE, "source": TEXT_VALUE, "source_sha256": TEXT_VALUE},
        "reference.calendar": {k: TEXT_VALUE for k in ("exchange", "open", "close", "source", "source_sha256")},
        "reference.corporate_action": {k: TEXT_VALUE for k in ("instrument_id", "action_id", "action_type", "effective_at", "source", "source_sha256")},
        "recorder.reject": {"reason": TEXT_VALUE}, "recorder.gap": {"reason": TEXT_VALUE, "unresolved": {"const": True}},
        "recorder.lifecycle": {"state": TEXT_VALUE}, "recorder.heartbeat": {"state": TEXT_VALUE}, "provider.control": {"type": TEXT_VALUE},
    }
    if version == 2:
        # Additive schema, not a reinterpretation of persisted v1 observations.
        for kind, fields in contracts.items():
            if not kind.startswith("market."):
                continue
            fields["provider_details"] = {"type": "object"}
            for name in ("tape", "conditions", "original_trade_id", "corrected_trade_id", "original_price",
                         "original_size_shares", "original_conditions", "corrected_conditions"):
                if name in fields:
                    fields[name] = {"anyOf": [fields[name], {"type": "null"}]}
        contracts["market.quote"]["size_unit"] = {"enum": ["provider_round_lots", "shares", "provider_unspecified"]}
    return contracts


def validate_payload(kind, payload, version=1):
    fields = payload_contracts(version)[kind]
    if set(fields) - set(payload) or (kind.startswith("market.") and set(payload) - set(fields)):
        raise ContractError("payload_fields_mismatch")
    for key, spec in fields.items():
        value = payload[key]
        if "anyOf" in spec:
            if value is None:
                continue
            spec = spec["anyOf"][0]
        if "const" in spec and (value != spec["const"] or type(value) is not type(spec["const"])):
            raise ContractError("payload_constant_mismatch")
        if "enum" in spec and value not in spec["enum"]:
            raise ContractError("payload_enum_mismatch")
        expected = spec.get("type")
        if expected == ["string", "null"]:
            if value is not None and not isinstance(value, str):
                raise ContractError("invalid_payload_text")
        elif expected == "string":
            if not isinstance(value, str) or (spec.get("minLength") and not value):
                raise ContractError("invalid_payload_text")
            if "pattern" in spec and not re.fullmatch(spec["pattern"], value):
                raise ContractError("invalid_decimal_string")
        elif expected == "integer":
            integer(value)
        elif expected == "boolean" and type(value) is not bool:
            raise ContractError("invalid_payload_boolean")
        elif expected == "array":
            conditions(value)
        elif expected == "object" and not isinstance(value, dict):
            raise ContractError("invalid_payload_object")


class ContractError(ValueError):
    """Messages are fixed reason codes, never provider bodies or secrets."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def timestamp_ns(value: str) -> int:
    if not isinstance(value, str) or not (match := STAMP.fullmatch(value)):
        raise ContractError("invalid_timestamp")
    try:
        dt = datetime.fromisoformat(match[1] + match[3].replace("Z", "+00:00"))
        seconds = calendar.timegm(dt.utctimetuple())
        result = seconds * 1_000_000_000 + int((match[2] or "").ljust(9, "0"))
        if not 0 < result < 2**63:
            raise ValueError
        return result
    except (ValueError, OverflowError):
        raise ContractError("invalid_timestamp") from None


def number(value: Any, *, positive: bool = False) -> str:
    if isinstance(value, bool) or value is None:
        raise ContractError("invalid_number")
    try:
        if len(str(value)) > 64:
            raise InvalidOperation
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (positive and result == 0):
            raise InvalidOperation
        if abs(result.as_tuple().exponent) > 18 or result.adjusted() > 18:
            raise InvalidOperation
        return format(result, "f")
    except (InvalidOperation, ValueError):
        raise ContractError("invalid_number") from None


def integer(value: Any) -> int:
    if type(value) is not int or value < 0 or value >= 2**63:
        raise ContractError("invalid_integer")
    return value


def text_field(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ContractError("invalid_text")
    return value


def conditions(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise ContractError("invalid_conditions")
    return value


@dataclass(frozen=True)
class Event:
    event_id: str
    event_type: str
    run_id: str
    connection_id: str
    provider: str
    feed: str
    origin: str
    received_ns: int
    received_monotonic_ns: int
    normalized_ns: int
    source_time_ns: int | None
    source_event_id: str | None
    source_sequence: int | None
    symbol: str | None
    instrument_id: str | None
    identity_event_id: str | None
    raw_id: int
    raw_index: int
    payload: dict
    quality_flags: tuple[str, ...]
    content_hash: str
    duplicate_of: str | None = None
    schema_version: int = SCHEMA_VERSION
    adapter_version: str = ADAPTER_VERSION
    execution_authority: str = "none"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["quality_flags"] = list(self.quality_flags)
        return data

    def validate(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2} or self.event_type not in EVENT_TYPES:
            raise ContractError("unsupported_schema_or_event")
        if self.execution_authority != "none" or self.origin not in {"live", "fixture", "import"}:
            raise ContractError("invalid_authority_or_origin")
        for key in ("event_id", "run_id", "connection_id", "provider", "feed", "adapter_version"):
            text_field(getattr(self, key))
        if not isinstance(self.payload, dict) or not isinstance(self.quality_flags, (list, tuple)):
            raise ContractError("invalid_payload_or_flags")
        if any(not isinstance(flag, str) for flag in self.quality_flags):
            raise ContractError("invalid_quality_flags")
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_hash):
            raise ContractError("invalid_content_hash")
        for key in ("source_time_ns", "source_sequence"):
            if getattr(self, key) is not None:
                integer(getattr(self, key))
        if self.symbol is not None and not SYMBOL.fullmatch(self.symbol):
            raise ContractError("invalid_symbol")
        for key in ("received_ns", "received_monotonic_ns", "normalized_ns", "raw_id", "raw_index"):
            integer(getattr(self, key))
        if self.normalized_ns < self.received_ns:
            raise ContractError("normalization_before_receipt")
        if self.event_type.startswith("market.") and (self.source_time_ns is None or self.symbol is None):
            raise ContractError("market_provenance_missing")
        validate_payload(self.event_type, self.payload, self.schema_version)
        canonical_json(self.to_dict())


def normalize_market(message: dict) -> tuple[str, dict, int, str | None]:
    """No condition filtering, aggregation, tick rounding or unit guesses here."""
    code = message.get("T")
    if code not in KINDS:
        raise ContractError("unknown_message_type")
    if not isinstance(message.get("S"), str) or not SYMBOL.fullmatch(message["S"]):
        raise ContractError("invalid_symbol")
    stamp = timestamp_ns(message.get("t"))
    source_id = None
    payload = {"tape": None if code in {"b", "u"} else text_field(message.get("z"))}
    if code == "t":
        source_id = str(integer(message.get("i")))
        payload.update(price=number(message.get("p"), positive=True),
                       size_shares=integer(message.get("s")),
                       exchange=text_field(message.get("x")),
                       conditions=conditions(message.get("c")))
    elif code == "q":
        payload.update(bid=number(message.get("bp")), ask=number(message.get("ap")),
                       bid_size=integer(message.get("bs")), ask_size=integer(message.get("as")),
                       size_unit="provider_round_lots", round_lot_size=None,
                       bid_exchange=text_field(message.get("bx")),
                       ask_exchange=text_field(message.get("ax")),
                       conditions=conditions(message.get("c")))
    elif code in {"b", "u"}:
        payload.update({name: number(message.get(key), positive=True)
                        for name, key in [("open", "o"), ("high", "h"), ("low", "l"), ("close", "c")]})
        if not (Decimal(payload["low"]) <= min(Decimal(payload["open"]), Decimal(payload["close"]))
                <= max(Decimal(payload["open"]), Decimal(payload["close"])) <= Decimal(payload["high"])):
            raise ContractError("invalid_ohlc")
        payload.update(volume_shares=integer(message.get("v")),
                       trade_count=integer(message.get("n")), vwap=number(message.get("vw")),
                       interval_ns=60_000_000_000, revision=(code == "u"),
                       revision_key=f"{message['S']}:{stamp}:1Min")
    elif code == "c":
        source_id = str(integer(message.get("ci")))
        payload.update(original_trade_id=str(integer(message.get("oi"))),
                       corrected_trade_id=source_id, original_price=number(message.get("op"), positive=True),
                       corrected_price=number(message.get("cp"), positive=True),
                       original_size_shares=integer(message.get("os")),
                       corrected_size_shares=integer(message.get("cs")),
                       original_conditions=conditions(message.get("oc")),
                       corrected_conditions=conditions(message.get("cc")),
                       exchange=text_field(message.get("x")))
    elif code == "x":
        source_id = str(integer(message.get("i")))
        if message.get("a") not in {"C", "E"}:
            raise ContractError("invalid_cancel_action")
        payload.update(original_trade_id=source_id, action=message["a"],
                       price=number(message.get("p"), positive=True),
                       size_shares=integer(message.get("s")), exchange=text_field(message.get("x")))
    elif code == "s":
        payload.update(status_code=text_field(message.get("sc")), reason_code=text_field(message.get("rc")))
    elif code == "l":
        payload.update(limit_up=number(message.get("u")), limit_down=number(message.get("d")),
                       indicator=text_field(message.get("i")))
    return KINDS[code], payload, stamp, source_id
