"""Explicit tick, availability, reference, calendar and data-admission contracts."""
from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from ..events import ContractError, digest, integer, number, timestamp_ns
from ..research_split import HOLDOUT_START, HOLDOUT_END

NS = 1_000_000_000
NY = ZoneInfo("America/New_York")


def decimal(value):
    return Decimal(number(value))


@dataclass(frozen=True)
class Security:
    symbol: str
    security_type: str = "unknown"
    instrument_id: str | None = None
    underlying_symbol: str | None = None
    expiration: str | None = None
    exercise_subscription_ratio: str | None = None
    subscription_strike_terms: str | None = None
    first_trading_date: str | None = None
    last_trading_date: str | None = None
    known_ns: int | None = None
    source: str | None = None

    def __post_init__(self):
        if not self.symbol or self.security_type not in {"equity", "etf", "right", "warrant", "unit", "unknown"}:
            raise ContractError("short_horizon_security_type_invalid")
        if self.known_ns is not None:
            integer(self.known_ns)
        for value in (self.expiration, self.first_trading_date, self.last_trading_date):
            if value is not None:
                if not isinstance(value,str) or len(value) != 10 or datetime.fromisoformat(value).date().isoformat() != value:
                    raise ContractError("short_horizon_security_date_invalid")
        if self.first_trading_date and self.last_trading_date and self.first_trading_date > self.last_trading_date:
            raise ContractError("short_horizon_security_dates_reversed")
        if self.exercise_subscription_ratio is not None and decimal(self.exercise_subscription_ratio) <= 0:
            raise ContractError("short_horizon_security_ratio_invalid")

    def missing(self):
        required = ["instrument_id", "source", "known_ns", "first_trading_date"]
        if self.security_type in {"right", "warrant", "unit"}:
            required += ["underlying_symbol", "expiration", "exercise_subscription_ratio",
                         "subscription_strike_terms", "last_trading_date"]
        result = [k for k in required if getattr(self, k) is None]
        if self.security_type not in {"equity", "etf", "right", "warrant", "unit"}:
            result.append("security_type")
        return result


@dataclass(frozen=True)
class Session:
    day: str
    pre_ns: int
    open_ns: int
    close_ns: int
    end_ns: int
    source: str
    regime: str = "unknown"
    regime_known_ns: int | None = None

    def __post_init__(self):
        for t in (self.pre_ns, self.open_ns, self.close_ns, self.end_ns):
            integer(t)
        if not self.pre_ns < self.open_ns < self.close_ns <= self.end_ns or not self.source:
            raise ContractError("short_horizon_calendar_invalid")
        for t in (self.pre_ns, self.open_ns, self.close_ns, self.end_ns - 1):
            if datetime.fromtimestamp(t // NS, NY).date().isoformat() != self.day:
                raise ContractError("short_horizon_calendar_day_mismatch")
        if datetime.fromtimestamp(self.open_ns // NS, NY).strftime("%H:%M:%S") != "09:30:00":
            raise ContractError("short_horizon_regular_open_invalid")
        if self.weekday > 4:
            raise ContractError("short_horizon_weekend_session")

    @property
    def weekday(self):
        return datetime.fromisoformat(self.day).weekday()

    def segment(self, now):
        return ("premarket" if self.pre_ns <= now < self.open_ns else
                "regular" if self.open_ns <= now < self.close_ns else
                "postmarket" if self.close_ns <= now < self.end_ns else "outside")


@dataclass(frozen=True)
class Tick:
    symbol: str
    kind: str
    exchange_ns: int
    received_ns: int | None
    sequence: int | None
    event_id: str
    bid: str | None = None
    ask: str | None = None
    bid_size: int | None = None
    ask_size: int | None = None
    price: str | None = None
    size: int | None = None
    venue: str | None = None
    status: str = "unknown"
    eligible: bool = False
    size_unit: str = "unknown"
    source: str = "unknown"

    def __post_init__(self):
        if self.kind not in {"quote", "trade", "status", "gap"}:
            raise ContractError("short_horizon_tick_only_no_bars_or_unhandled_revisions")
        if not self.symbol or not self.event_id or self.source == "unknown":
            raise ContractError("short_horizon_tick_lineage_required")
        integer(self.exchange_ns)
        for t in (self.received_ns, self.sequence, self.bid_size, self.ask_size, self.size):
            if t is not None:
                integer(t)
        if self.received_ns is not None and self.received_ns < self.exchange_ns:
            raise ContractError("short_horizon_receipt_before_source")
        if type(self.eligible) is not bool:
            raise ContractError("short_horizon_eligibility_invalid")
        for price in (self.bid, self.ask, self.price):
            if price is not None:
                decimal(price)
        if self.kind == "quote" and any(x is None for x in (self.bid, self.ask, self.bid_size, self.ask_size)):
            raise ContractError("short_horizon_quote_fields_missing")
        if self.kind == "trade" and (self.price is None or self.size is None or decimal(self.price) <= 0):
            raise ContractError("short_horizon_trade_fields_missing")


@dataclass(frozen=True)
class Dataset:
    source: str
    feed: str
    origin: str
    clock: str
    source_delay_ns: int
    start_ns: int
    end_ns: int
    symbols: tuple[str, ...]
    sessions: tuple[Session, ...]
    securities: tuple[Security, ...]
    evidence: dict
    split: dict
    sequence_scope: str = "unknown"

    def __post_init__(self):
        if self.feed not in {"sip_nbbo", "iex", "unknown"} or self.origin not in {"historical", "fixture", "recorded"}:
            raise ContractError("short_horizon_fidelity_invalid")
        if self.clock not in {"actual_receipt", "assumed_source_delay"}:
            raise ContractError("short_horizon_clock_required")
        integer(self.source_delay_ns)
        if self.clock == "actual_receipt" and self.source_delay_ns:
            raise ContractError("short_horizon_clock_contradiction")
        integer(self.start_ns); integer(self.end_ns)
        if self.start_ns >= self.end_ns or not self.source or not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ContractError("short_horizon_coverage_invalid")
        if self.start_ns < HOLDOUT_END and self.end_ns > HOLDOUT_START:
            raise ContractError("frozen_final_holdout_access_denied")
        if set(self.symbols) != {s.symbol for s in self.securities} or len(self.securities) != len(self.symbols):
            raise ContractError("short_horizon_reference_scope_invalid")
        intervals = sorted((s.pre_ns, s.end_ns) for s in self.sessions)
        if not intervals or any(b > c for (_, b), (c, _) in zip(intervals, intervals[1:])):
            raise ContractError("short_horizon_calendar_overlap")
        if self.sequence_scope not in {"global", "unknown"}:
            raise ContractError("short_horizon_sequence_scope_invalid")
        # A new protocol cannot relabel previously protected pilot validation/OOS.
        a = timestamp_ns("2025-03-01T00:00:00-05:00")
        if self.start_ns < HOLDOUT_START and self.end_ns > a:
            raise ContractError("existing_pilot_validation_or_oos_not_development")
        split = self.split
        if set(split) != {"train", "validation", "holdout", "purpose"} or split["purpose"] != "development":
            raise ContractError("short_horizon_split_development_only")
        bounds = [split[k] for k in ("train", "validation", "holdout")]
        if any(not isinstance(b, (list, tuple)) or len(b) != 2 or any(type(t) is not int for t in b) or b[0] >= b[1] for b in bounds):
            raise ContractError("short_horizon_split_invalid")
        if not bounds[0][1] <= bounds[1][0] < bounds[1][1] <= bounds[2][0]:
            raise ContractError("short_horizon_split_overlap")
        if not bounds[0][0] <= self.start_ns < self.end_ns <= bounds[0][1]:
            raise ContractError("short_horizon_validation_holdout_locked")

    def available(self, event):
        if self.clock == "actual_receipt":
            if event.received_ns is None:
                raise ContractError("short_horizon_original_receipt_missing")
            return event.received_ns
        if event.received_ns is not None:
            raise ContractError("short_horizon_receipt_must_control_availability")
        return event.exchange_ns + self.source_delay_ns

    def blockers(self):
        required = ("quotes_complete", "trades_complete", "status_complete", "conditions_reviewed",
                    "corrections_resolved", "share_units_verified", "identity_verified", "calendar_verified")
        reasons = [k for k in required if not isinstance(self.evidence.get(k), str) or not self.evidence[k].strip()]
        if self.feed != "sip_nbbo":
            reasons.append("consolidated_nbbo_required")
        for s in self.securities:
            reasons += [s.symbol + ":" + k for k in s.missing()]
            if s.known_ns is not None and s.known_ns > self.start_ns:
                reasons.append(s.symbol + ":reference_not_available_at_start")
            first = datetime.fromtimestamp(self.start_ns // NS, NY).date().isoformat()
            last = datetime.fromtimestamp((self.end_ns - 1) // NS, NY).date().isoformat()
            if s.first_trading_date and s.first_trading_date > first:
                reasons.append(s.symbol + ":before_first_trading_date")
            if s.last_trading_date and s.last_trading_date < last:
                reasons.append(s.symbol + ":after_last_trading_date")
        return sorted(set(reasons))

    def manifest(self):
        body = asdict(self)
        body.update(execution_authority="none", production_eligible=False,
                    fill_model="conditional_L1_aggressive_only", admission_blockers=self.blockers(),
                    actual_arrival_verified=self.clock == "actual_receipt",
                    evidence_status="fixture_only" if self.origin == "fixture" else "research_only",
                    reference_evidence="declared_provenance_requires_independent_audit")
        return {**body, "manifest_hash": digest(body)}


def dataset_from_dict(value):
    value = dict(value)
    value["symbols"] = tuple(value["symbols"])
    value["sessions"] = tuple(Session(**s) for s in value["sessions"])
    value["securities"] = tuple(Security(**s) for s in value["securities"])
    return Dataset(**value)
