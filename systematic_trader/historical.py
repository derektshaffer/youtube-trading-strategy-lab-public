"""Raw-first historical bundles and explicitly virtual research availability.

The archive uses actual local receipt times. Research projection uses separately
declared availability, never overwrites the archive, and can never certify live
data. Provider exports must be converted to this versioned, unadjusted contract;
no vendor client, calendar guess, price adjustment or survivor inference lives here.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import time
import uuid

from .events import ContractError, Event, SYMBOL, digest, integer, number, text_field, timestamp_ns, validate_payload
from .features import MINUTE, Session, Universe
from .ledger import Recorder, strict_json
from .market_state import MarketState

VERSION = "historical-bundle-v1"
MAX_BYTES = 32 * 1024 * 1024


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ContractError("historical_fields_mismatch")


def _symbol(value):
    if not isinstance(value, str) or not SYMBOL.fullmatch(value):
        raise ContractError("invalid_historical_symbol")
    return value


def _identity(value):
    text_field(value)
    if ":" not in value:
        raise ContractError("historical_stable_identity_required")
    return value


def parse_bundle(raw):
    """Validate complete bounded chunk before committing any normalized rows."""
    if len(raw) > MAX_BYTES:
        raise ContractError("historical_chunk_too_large")
    b = strict_json(raw)
    _fields(b, ("version", "dataset_id", "provider", "feed", "source", "evidence", "adjustment",
                "availability", "universe_coverage", "records"))
    if b["version"] != VERSION or b["adjustment"] != "unadjusted":
        raise ContractError("unsupported_historical_version_or_adjustment")
    if b["evidence"] not in {"fixture", "vendor_export"} or b["universe_coverage"] not in {"point_in_time", "survivors_only", "unknown"}:
        raise ContractError("invalid_historical_evidence")
    for key in ("dataset_id", "provider", "feed", "source"):
        text_field(b[key])
    if b["provider"] == "recorder":
        raise ContractError("reserved_historical_provider")
    _fields(b["availability"], ("mode", "delay_ns"))
    mode = b["availability"]["mode"]
    delay = integer(b["availability"]["delay_ns"])
    if mode not in {"vendor_timestamp", "assumed_bar_close"} or (mode == "vendor_timestamp" and delay):
        raise ContractError("invalid_historical_availability_policy")
    if not isinstance(b["records"], list) or not 1 <= len(b["records"]) <= 10000:
        raise ContractError("invalid_historical_chunk_size")
    parsed = []
    sha = hashlib.sha256(raw).hexdigest()
    for row in b["records"]:
        if not isinstance(row, dict):
            raise ContractError("invalid_historical_record")
        kind = row.get("kind")
        common = ("kind", "known_at")
        symbol = instrument = source_ns = None
        if kind == "instrument":
            _fields(row, (*common, "symbol", "instrument_id", "effective_from", "effective_to"), ("tick_size", "round_lot_size"))
            symbol, instrument = _symbol(row["symbol"]), _identity(row["instrument_id"])
            p = dict(symbol=symbol, instrument_id=instrument,
                     effective_from_ns=timestamp_ns(row["effective_from"]), effective_to_ns=timestamp_ns(row["effective_to"]))
            if "tick_size" in row:
                p["tick_size"] = number(row["tick_size"], positive=True)
            if "round_lot_size" in row:
                p["round_lot_size"] = integer(row["round_lot_size"])
                if not p["round_lot_size"]:
                    raise ContractError("invalid_historical_round_lot")
            event_type = "reference.instrument"
        elif kind == "calendar":
            _fields(row, (*common, "session_id", "exchange", "pre_open", "open", "close", "post_close"))
            times = [timestamp_ns(row[k]) for k in ("pre_open", "open", "close", "post_close")]
            if not times[0] <= times[1] < times[2] <= times[3] or any(t % MINUTE for t in times):
                raise ContractError("invalid_historical_session_boundaries")
            if times[3] - times[0] > 24 * 60 * MINUTE:
                raise ContractError("historical_session_exceeds_one_day")
            text_field(row["session_id"])
            p = {k: row[k] for k in ("session_id", "exchange", "pre_open", "open", "close", "post_close")}
            event_type = "reference.calendar"
        elif kind == "corporate_action":
            _fields(row, (*common, "instrument_id", "action_id", "action_type", "effective_at", "details"))
            instrument = _identity(row["instrument_id"])
            if not isinstance(row["details"], dict):
                raise ContractError("invalid_historical_action_details")
            p = {k: row[k] for k in ("instrument_id", "action_id", "action_type", "effective_at", "details")}
            event_type = "reference.corporate_action"
        elif kind == "universe":
            _fields(row, (*common, "effective_at", "entries"))
            if not isinstance(row["entries"], list):
                raise ContractError("invalid_historical_universe")
            ids = set()
            for entry in row["entries"]:
                _fields(entry, ("instrument_id", "symbol", "active", "asset_type"))
                i = _identity(entry["instrument_id"])
                _symbol(entry["symbol"])
                text_field(entry["asset_type"])
                if type(entry["active"]) is not bool or i in ids:
                    raise ContractError("invalid_historical_membership")
                ids.add(i)
            p = dict(state="historical_universe", effective_ns=timestamp_ns(row["effective_at"]), entries=row["entries"])
            event_type = "recorder.lifecycle"
        elif kind == "coverage":
            _fields(row, (*common, "instrument_id", "session_id", "segments"))
            instrument = _identity(row["instrument_id"])
            text_field(row["session_id"])
            if (not isinstance(row["segments"], list) or not row["segments"] or
                    len(set(row["segments"])) != len(row["segments"]) or
                    not set(row["segments"]) <= {"pre", "regular", "post"}):
                raise ContractError("invalid_historical_coverage")
            p = dict(state="historical_coverage", instrument_id=instrument, session_id=row["session_id"], segments=row["segments"])
            event_type = "recorder.lifecycle"
        elif kind == "bar":
            _fields(row, ("kind", "symbol", "instrument_id", "start", "revision", "open", "high", "low", "close", "volume", "trade_count", "vwap"), ("known_at",))
            symbol, instrument = _symbol(row["symbol"]), _identity(row["instrument_id"])
            source_ns = timestamp_ns(row["start"])
            if source_ns % MINUTE:
                raise ContractError("historical_bar_not_minute_aligned")
            revision = integer(row["revision"])
            p = {k: number(row[k], positive=True) for k in ("open", "high", "low", "close")}
            if not Decimal(p["low"]) <= min(Decimal(p["open"]), Decimal(p["close"])) <= max(Decimal(p["open"]), Decimal(p["close"])) <= Decimal(p["high"]):
                raise ContractError("invalid_historical_ohlc")
            p.update(vwap=number(row["vwap"]), volume_shares=integer(row["volume"]), trade_count=integer(row["trade_count"]),
                     interval_ns=MINUTE, revision=bool(revision), revision_key=f"{instrument}:{source_ns}:1Min", tape=None)
            event_type = "market.bar_revision" if revision else "market.bar"
        else:
            raise ContractError("unknown_historical_record_kind")
        if kind == "bar" and mode == "assumed_bar_close":
            # Final revised bars must NEVER be backdated to their original close.
            if row["revision"] or "known_at" in row:
                raise ContractError("assumed_availability_requires_original_bar_without_known_at")
            known = integer(source_ns + MINUTE + delay)
        else:
            known = timestamp_ns(row.get("known_at"))
        if kind == "bar" and known < source_ns + MINUTE:
            raise ContractError("historical_bar_available_before_completion")
        provenance = dict(version=VERSION, dataset_id=b["dataset_id"], source=b["source"], source_sha256=sha,
                          known_ns=known, original_timestamps={k: row[k] for k in ("known_at", "start", "effective_from", "effective_to", "effective_at", "pre_open", "open", "close", "post_close")
                          if k in row and (kind != "bar" or k in {"known_at", "start"})},
                          availability_mode=mode if kind == "bar" else "vendor_timestamp", evidence=b["evidence"],
                          universe_coverage=b["universe_coverage"], adjustment="unadjusted")
        if kind == "bar":
            provenance["revision"] = row["revision"]
            p["provider_details"] = provenance
        else:
            p.update(source=b["source"], source_sha256=sha, historical=provenance)
            if event_type.startswith("reference."):
                Recorder.validate_reference(event_type, p)
        validate_payload(event_type, p, 2)
        parsed.append(dict(event_type=event_type, payload=p, source_time_ns=source_ns, symbol=symbol,
                           instrument_id=instrument, known_ns=known))
    return b, parsed


class HistoricalImporter:
    """One raw file per atomic chunk; identical bytes resume without duplication."""
    def __init__(self, ledger, *, clock=time.time_ns):
        self.ledger, self.clock = ledger, clock
        if ledger.connection.execute("SELECT 1 FROM events WHERE json_extract(event_json,'$.origin')='live' LIMIT 1").fetchone():
            raise ContractError("historical_import_requires_separate_live_archive")

    def ingest(self, raw):
        if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
            raise ContractError("invalid_or_oversized_historical_chunk")
        sha = hashlib.sha256(raw).hexdigest()
        for row in self.ledger.connection.execute("SELECT id,metadata_json FROM receipts ORDER BY id"):
            meta = json.loads(row["metadata_json"])
            if meta.get("adapter_version") == VERSION and meta.get("source_sha256") == sha:
                if any(r["id"] == row["id"] for r in self.ledger.pending()):
                    self.process(row["id"])
                return row["id"]
        now = self.clock()
        raw_id = self.ledger.receipt(raw, dict(adapter_version=VERSION, source_sha256=sha, received_ns=now,
                                             run_id=str(uuid.uuid4()), origin="import", internal=False))
        self.process(raw_id)
        return raw_id

    def recover(self):
        for row in self.ledger.pending():
            if json.loads(row["metadata_json"]).get("adapter_version") == VERSION:
                self.process(row["id"])

    def process(self, raw_id):
        row = self.ledger.connection.execute("SELECT * FROM receipts WHERE id=?", (raw_id,)).fetchone()
        meta = json.loads(row["metadata_json"])
        if meta.get("adapter_version") != VERSION:
            raise ContractError("historical_recovery_adapter_mismatch")
        try:
            bundle, parsed = parse_bundle(row["raw"])
            if any(p["known_ns"] > meta["received_ns"] for p in parsed):
                raise ContractError("historical_availability_after_import")
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            reason = str(exc) if isinstance(exc, ContractError) else "invalid_historical_bundle"
            bundle = dict(provider="recorder", feed="historical_quarantine")
            parsed = [dict(event_type="recorder.reject", payload=dict(reason=reason), source_time_ns=None,
                           symbol=None, instrument_id=None, known_ns=meta["received_ns"])]
        events = []
        for index, item in enumerate(parsed):
            p = item["payload"]
            event = Event(event_id=f"{meta['run_id']}:{raw_id}:{index}", run_id=meta["run_id"], connection_id=VERSION,
                          provider=bundle["provider"], feed=bundle["feed"], origin="import", received_ns=meta["received_ns"],
                          received_monotonic_ns=0, normalized_ns=max(meta["received_ns"], self.clock()),
                          event_type=item["event_type"], source_time_ns=item["source_time_ns"], source_event_id=None,
                          source_sequence=None, symbol=item["symbol"], instrument_id=item["instrument_id"], identity_event_id=None,
                          raw_id=raw_id, raw_index=index, payload=p, schema_version=2, adapter_version=VERSION,
                          content_hash=digest(dict(provider=bundle["provider"], feed=bundle["feed"], source_sha256=meta["source_sha256"], index=index)),
                          quality_flags=("not_live_evidence", "historical_backfill", "research_only", "trade_condition_policy_unapproved"))
            event.validate()
            events.append(event)
        self.ledger.append_events(raw_id, events)


def historical_view(ledger, *, through_seq, as_of_ns, dataset_id):
    """Project a fixed archive watermark into virtual time through shared state.

    Projected sequence is a research cursor, never the archived decision cursor.
    The manifest binds both; original archive rows and local clocks stay intact.
    """
    integer(as_of_ns)
    if type(through_seq) is not int or not 0 <= through_seq <= ledger.watermark():
        raise ContractError("invalid_historical_archive_watermark")
    archived = list(ledger.replay(through_seq=through_seq))
    selected = []
    cohorts = set()
    for event in archived:
        if event["adapter_version"] != VERSION:
            continue
        if event["event_type"] == "recorder.reject":
            # A quarantined chunk cannot silently disappear from a research dataset.
            raise ContractError("historical_archive_contains_rejected_chunk")
        p = event["payload"]
        h = p.get("provider_details", p.get("historical", {}))
        if h.get("dataset_id") != dataset_id:
            continue
        cohorts.add((event["provider"], event["feed"], h["evidence"], h["availability_mode"] if event["event_type"].startswith("market.") else None))
        if h["known_ns"] <= as_of_ns:
            selected.append((h["known_ns"], event["ledger_seq"], event))
    if not cohorts:
        raise ContractError("historical_dataset_unavailable")
    if len({c[:3] for c in cohorts}) != 1 or len({c[3] for c in cohorts if c[3] is not None}) > 1:
        raise ContractError("mixed_historical_dataset_cohort")
    market, universe = MarketState(), Universe()
    sessions, coverage, projected = {}, [], []
    source_rows = []
    for known, archived_seq, original in sorted(selected, key=lambda x: (x[0], x[1])):
        e = deepcopy(original)
        p = e["payload"]
        h = p.get("provider_details", p.get("historical", {}))
        h["archive_seq"] = archived_seq
        h["import_received_ns"] = original["received_ns"]
        h["projection"] = "historical-research-clock-v1"
        e.update(ledger_seq=len(projected)+1, received_ns=known, normalized_ns=known, received_monotonic_ns=0)
        if e["event_type"].startswith("market."):
            matches = [r for r in market.references.values() if r["event_type"] == "reference.instrument" and
                       r["payload"]["symbol"] == e["symbol"] and
                       r["payload"]["effective_from_ns"] <= e["source_time_ns"] < r["payload"]["effective_to_ns"]]
            if not matches or {r["payload"]["instrument_id"] for r in matches} != {e["instrument_id"]}:
                market.gaps.add(digest(["historical_identity_unresolved", archived_seq]))
                e["instrument_id"] = None
            else:
                e["identity_event_id"] = matches[-1]["event_id"]
        market.apply(e)
        if e["event_type"] == "reference.calendar":
            old = sessions.get(p["session_id"])
            if old and any(old[k] != p[k] for k in ("exchange", "pre_open", "open", "close", "post_close")):
                market.gaps.add(digest(["historical_calendar_conflict", p["session_id"]]))
            sessions[p["session_id"]] = deepcopy(p)
        elif p.get("state") == "historical_universe":
            universe.observe(p["entries"], known_ns=known, effective_ns=p["effective_ns"], source_hash=digest(p))
        elif p.get("state") == "historical_coverage":
            coverage.append(p)
        projected.append(e)
        source_rows.append(dict(archive_seq=archived_seq, event_id=original["event_id"], source_sha256=h["source_sha256"]))
    gaps = []
    for c in coverage:
        calendar = sessions.get(c["session_id"])
        if calendar is None:
            gaps.append(dict(reason="historical_calendar_unavailable", instrument_id=c["instrument_id"], session_id=c["session_id"]))
            continue
        key = (*next(iter(cohorts))[:2], "import", c["instrument_id"])
        state = market.instruments.get(key)
        segments = dict(pre=("pre_open", "open"), regular=("open", "close"), post=("close", "post_close"))
        for segment in c["segments"]:
            a, b = segments[segment]
            start, end = timestamp_ns(calendar[a]), timestamp_ns(calendar[b])
            missing = [t for t in range(start, min(end, as_of_ns - MINUTE + 1), MINUTE)
                       if state is None or (t, MINUTE) not in state.bars]
            if missing:
                gaps.append(dict(reason="missing_expected_bar_unverified", instrument_id=c["instrument_id"],
                                 session_id=c["session_id"], segment=segment, starts_ns=missing))
    # No calendar is invented for holidays/DST/early closes. Missingness blocks use.
    if not coverage:
        gaps.append(dict(reason="historical_coverage_undeclared"))
    for key, state in market.instruments.items():
        for start, interval in state.bars:
            matches = []
            for c in coverage:
                if c["instrument_id"] != key[3] or c["session_id"] not in sessions:
                    continue
                calendar = sessions[c["session_id"]]
                boundaries = dict(pre=("pre_open", "open"), regular=("open", "close"), post=("close", "post_close"))
                if any(timestamp_ns(calendar[boundaries[s][0]]) <= start and
                       start+interval <= timestamp_ns(calendar[boundaries[s][1]]) for s in c["segments"]):
                    matches.append(c["session_id"])
            if len(set(matches)) != 1:
                gaps.append(dict(reason="historical_bar_session_coverage_ambiguous", instrument_id=key[3], start_ns=start))
    for gap in gaps:
        market.gaps.add(digest(gap))
    issues = set()
    if any(e["payload"].get("provider_details", e["payload"].get("historical", {})).get("universe_coverage") != "point_in_time" for e in projected):
        issues.add("survivorship_coverage_unverified")
    if any(e["payload"].get("provider_details", {}).get("availability_mode") == "assumed_bar_close" for e in projected):
        issues.add("historical_availability_assumed")
    for s in market.instruments.values():
        s.problems.update(issues)
    manifest = dict(version="historical-view-v1", dataset_id=dataset_id, archive_watermark=through_seq,
                    as_of_ns=as_of_ns, projection_watermark=market.watermark, inputs=source_rows,
                    gaps=gaps, issues=sorted(issues), state_hash=market.fingerprint(),
                    evidence="fixture" if next(iter(cohorts))[2] == "fixture" else "research_only",
                    production_eligible=False, execution_authority="none")
    manifest["view_hash"] = digest(manifest)
    return dict(market=market, universe=universe, calendars=sessions, events=projected, manifest=manifest)


def daily_history(view):
    """Completed regular sessions only, from the SAME as-of state and calendar.

    No adjustments are applied. Corporate-action coverage and condition rules
    remain separate qualification gates; a row is a research input, not approval.
    """
    rows = []
    market = view["market"]
    for session_id, calendar in sorted(view["calendars"].items(), key=lambda x: timestamp_ns(x[1]["open"])):
        opened, closed = timestamp_ns(calendar["open"]), timestamp_ns(calendar["close"])
        if closed > view["manifest"]["as_of_ns"]:
            continue
        for key, state in sorted(market.instruments.items()):
            bars = [state.bars.get((t, MINUTE)) for t in range(opened, closed, MINUTE)]
            if not bars or any(b is None for b in bars) or state.problems:
                continue
            if any(g.get("instrument_id") == key[3] and g.get("session_id") == session_id for g in view["manifest"]["gaps"]):
                continue
            p = [b["payload"] for b in bars]
            volumes = [b["volume_shares"] for b in p]
            rows.append(dict(instrument_id=key[3], provider=key[0], feed=key[1], origin="import", session_id=session_id,
                             open_ns=opened, close_ns=closed,
                             known_ns=max(calendar["historical"]["known_ns"], max(b["received_ns"] for b in bars)),
                             high=str(max(Decimal(b["high"]) for b in p)), low=str(min(Decimal(b["low"]) for b in p)),
                             close=p[-1]["close"], volume=str(sum(volumes)), open_volume=str(sum(volumes[:5])),
                             dollar_volume=str(sum(Decimal(b["vwap"])*b["volume_shares"] for b in p)),
                             minute_volumes=volumes, adjustment="unadjusted",
                             source_hash=digest(dict(calendar=calendar, bar_events=bars))))
    return rows
