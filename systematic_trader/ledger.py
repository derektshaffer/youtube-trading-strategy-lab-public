"""Single-writer, append-only local capture journal; never an order database.

Raw receipts are committed before interpretation. A crashed normalization is
recovered from those original receipts; its new availability cursor is retained.
"""
from __future__ import annotations

from dataclasses import replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

from .events import (ContractError, Event, KINDS, canonical_json, digest,
                     normalize_market, timestamp_ns)

DDL = """
CREATE TABLE metadata (version INTEGER NOT NULL CHECK(version=1));
INSERT INTO metadata VALUES(1);
CREATE TABLE receipts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 metadata_json TEXT NOT NULL,
 raw BLOB NOT NULL,
 previous_hash TEXT NOT NULL,
 hash TEXT NOT NULL UNIQUE
);
CREATE TABLE events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id TEXT NOT NULL UNIQUE,
 raw_id INTEGER NOT NULL REFERENCES receipts(id),
 raw_index INTEGER NOT NULL,
 received_ns INTEGER NOT NULL,
 event_type TEXT NOT NULL,
 content_hash TEXT NOT NULL,
 event_json TEXT NOT NULL,
 previous_hash TEXT NOT NULL,
 hash TEXT NOT NULL UNIQUE,
 UNIQUE(raw_id, raw_index)
);
CREATE TABLE completions (
 raw_id INTEGER PRIMARY KEY REFERENCES receipts(id),
 event_count INTEGER NOT NULL CHECK(event_count > 0)
);
CREATE INDEX events_received ON events(received_ns,seq);
CREATE INDEX events_content ON events(content_hash);
CREATE INDEX events_type ON events(event_type,seq);
CREATE INDEX events_source_clock ON events(event_type,json_extract(event_json,'$.symbol'),json_extract(event_json,'$.source_time_ns'));
"""


class LedgerError(RuntimeError):
    pass


def strict_json(raw):
    def pairs(items):
        output = {}
        for key, value in items:
            if key in output:
                raise ContractError("duplicate_json_key")
            output[key] = value
        return output

    def invalid(_):
        raise ContractError("nonfinite_json")
    return json.loads(raw, parse_float=str, parse_constant=invalid, object_pairs_hook=pairs)


class Ledger:
    def __init__(self, directory: str | Path, *, min_free_bytes: int = 1_073_741_824, read_only=False):
        self.directory = Path(directory).expanduser().resolve()
        self.min_free_bytes = min_free_bytes
        self.path = self.directory / "capture.sqlite3"
        self.lock = None
        self.connection = None
        if read_only:
            from urllib.parse import quote
            self.connection = sqlite3.connect("file:" + quote(str(self.path), safe="/") + "?mode=ro", uri=True)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA query_only=ON")
            self.connection.execute("BEGIN")
            return
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = open(self.directory / "writer.lock", "a+b")
        os.chmod(self.directory / "writer.lock", 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise LedgerError("capture_writer_already_running") from None
        self.connection = None
        try:
            self._space()
            fresh = not self.path.exists()
            if fresh:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            self.connection = sqlite3.connect(self.path, timeout=5)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA fullfsync=ON")
            if fresh:
                with self.connection:
                    self.connection.executescript(DDL)
                    for table in ("receipts", "events", "completions", "metadata"):
                        for action in ("UPDATE", "DELETE"):
                            self.connection.execute(
                                f"CREATE TRIGGER no_{action}_{table} BEFORE {action} ON {table} "
                                "BEGIN SELECT RAISE(ABORT, 'append_only'); END")
            if self.connection.execute("SELECT version FROM metadata").fetchall()[0][0] != 1:
                raise LedgerError("unsupported_ledger_version")
            self.verify()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.lock is not None and not self.lock.closed:
            self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _space(self):
        if shutil.disk_usage(self.directory).free < self.min_free_bytes:
            raise LedgerError("disk_reserve_reached")

    def receipt(self, raw: bytes, metadata: dict) -> int:
        self._space()
        encoded = canonical_json(metadata)
        previous = self.connection.execute("SELECT hash FROM receipts ORDER BY id DESC LIMIT 1").fetchone()
        previous_hash = previous[0] if previous else "0" * 64
        checksum = hashlib.sha256(previous_hash.encode() + encoded.encode() + raw).hexdigest()
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO receipts(metadata_json,raw,previous_hash,hash) VALUES(?,?,?,?)",
                (encoded, raw, previous_hash, checksum))
        return cursor.lastrowid

    def append_events(self, raw_id: int, events: list[Event]):
        self._space()
        if not events:
            raise ContractError("empty_event_batch")
        with self.connection:
            for event in events:
                event.validate()
                if event.raw_id != raw_id:
                    raise ContractError("raw_link_mismatch")
                duplicate = self.connection.execute(
                    "SELECT event_id FROM events WHERE content_hash=? ORDER BY seq LIMIT 1",
                    (event.content_hash,)).fetchone()
                if duplicate:
                    event = replace(event, duplicate_of=duplicate[0],
                                    quality_flags=tuple(sorted(set(event.quality_flags) | {"repeat_observation"})))
                encoded = canonical_json(event.to_dict())
                previous = self.connection.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
                previous_hash = previous[0] if previous else "0" * 64
                checksum = hashlib.sha256(previous_hash.encode() + encoded.encode()).hexdigest()
                self.connection.execute(
                    "INSERT INTO events(event_id,raw_id,raw_index,received_ns,event_type,content_hash,event_json,previous_hash,hash) "
                    "VALUES(?,?,?,?,?,?,?,?,?)", (event.event_id, raw_id, event.raw_index, event.received_ns,
                    event.event_type, event.content_hash, encoded, previous_hash, checksum))
            self.connection.execute("INSERT INTO completions VALUES(?,?)", (raw_id, len(events)))

    def pending(self):
        return self.connection.execute(
            "SELECT r.* FROM receipts r LEFT JOIN completions c ON c.raw_id=r.id WHERE c.raw_id IS NULL ORDER BY r.id").fetchall()

    def watermark(self) -> int:
        return self.connection.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]

    def replay(self, *, through_seq: int, received_through_ns: int | None = None):
        """A recorded decision watermark is mandatory; wall-clock time alone isn't enough."""
        if type(through_seq) is not int or through_seq < 0:
            raise ContractError("invalid_watermark")
        query = "SELECT seq,event_json FROM events WHERE seq<=?"
        args = [through_seq]
        if received_through_ns is not None:
            query += " AND received_ns<=?"
            args.append(received_through_ns)
        for row in self.connection.execute(query + " ORDER BY seq", args):
            yield {"ledger_seq": row[0], **json.loads(row[1])}

    def verify(self) -> dict:
        if [r[0] for r in self.connection.execute("SELECT version FROM metadata")] != [1]:
            raise LedgerError("unsupported_ledger_version")
        if self.connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise LedgerError("sqlite_integrity_failure")
        if self.connection.execute("PRAGMA foreign_key_check").fetchone():
            raise LedgerError("raw_link_integrity_failure")
        result = {}
        for table, key in [("receipts", "id"), ("events", "seq")]:
            previous, count = "0" * 64, 0
            for row in self.connection.execute(f"SELECT * FROM {table} ORDER BY {key}"):
                material = (row["metadata_json"].encode() + row["raw"] if table == "receipts"
                            else row["event_json"].encode())
                checksum = hashlib.sha256(previous.encode() + material).hexdigest()
                count += 1
                if row[key] != count or row["previous_hash"] != previous or row["hash"] != checksum:
                    raise LedgerError("journal_hash_chain_failure")
                if table == "events":
                    data = json.loads(row["event_json"])
                    Event(**data).validate()
                    for column in ("event_id", "raw_id", "raw_index", "received_ns", "event_type", "content_hash"):
                        if data[column] != row[column]:
                            raise LedgerError("event_index_mismatch")
                previous = checksum
            result[table] = count
            result[table + "_head"] = previous
        bad = self.connection.execute(
            "SELECT c.raw_id FROM completions c LEFT JOIN events e ON e.raw_id=c.raw_id "
            "GROUP BY c.raw_id HAVING COUNT(e.seq)!=c.event_count").fetchone()
        if bad:
            raise LedgerError("completion_integrity_failure")
        if self.connection.execute(
                "SELECT e.seq FROM events e LEFT JOIN completions c ON e.raw_id=c.raw_id WHERE c.raw_id IS NULL LIMIT 1").fetchone():
            raise LedgerError("orphan_event_batch")
        result["pending_receipts"] = len(self.pending())
        result["execution_authority"] = "none"
        return result


class Recorder:
    def __init__(self, ledger: Ledger, *, origin="live", feed="sip", clock=time.time_ns,
                 monotonic=time.monotonic_ns, run_id=None, provider="alpaca"):
        from .providers import adapter_for
        self.adapter = adapter_for(provider, feed)
        self.provider = provider
        if origin not in {"live", "fixture", "import"}:
            raise ContractError("unsupported_capture_mode")
        self.ledger, self.origin, self.feed = ledger, origin, feed
        self.clock, self.monotonic = clock, monotonic
        self.run_id = run_id or str(uuid.uuid4())
        self.connection_id = str(uuid.uuid4())
        self.last_receipt_ns = 0

    def ingest(self, raw: str | bytes, *, internal=False) -> int:
        received, mono = self.clock(), self.monotonic()
        metadata = dict(run_id=self.run_id, connection_id=self.connection_id,
                        provider=self.provider, adapter_version=self.adapter.version,
                        origin=self.origin, feed=self.feed, received_ns=received,
                        received_monotonic_ns=mono, internal=internal,
                        clock_regression=received < self.last_receipt_ns)
        self.last_receipt_ns = max(received, self.last_receipt_ns)
        raw_id = self.ledger.receipt(raw.encode() if isinstance(raw, str) else raw, metadata)
        self.process(raw_id)
        return raw_id

    def internal(self, event_type: str, payload: dict):
        return self.ingest(canonical_json([{"event_type": event_type, "payload": payload}]), internal=True)

    def recover(self):
        for row in self.ledger.pending():
            if json.loads(row["metadata_json"]).get("adapter_version") == "historical-bundle-v1":
                from .historical import HistoricalImporter
                HistoricalImporter(self.ledger, clock=self.clock).process(row["id"])
            else:
                self.process(row["id"], recovered=True)

    def identity(self, symbol, source_ns, received_ns, origin):
        matches = []
        for row in self.ledger.connection.execute(
                "SELECT event_json FROM events WHERE event_type='reference.instrument' AND received_ns<=? ORDER BY seq",
                (received_ns,)):
            event = json.loads(row[0]); p = event["payload"]
            if event["origin"] == "fixture" and origin != "fixture":
                continue
            if p.get("symbol") == symbol and p["effective_from_ns"] <= source_ns < p["effective_to_ns"]:
                matches.append(event)
        if not matches:
            return None, None
        # A correction of the same ID can add facts. Ambiguous identity fails closed.
        if len({e["payload"]["instrument_id"] for e in matches}) != 1:
            return None, None
        return matches[-1]["payload"]["instrument_id"], matches[-1]["event_id"]

    def process(self, raw_id: int, *, recovered=False):
        row = self.ledger.connection.execute("SELECT * FROM receipts WHERE id=?", (raw_id,)).fetchone()
        meta = json.loads(row["metadata_json"])
        from .providers import adapter_for
        provider = meta.get("provider", "alpaca")
        adapter = adapter_for(provider, meta["feed"], meta.get("adapter_version"))
        try:
            messages = strict_json(row["raw"]) if meta["internal"] else adapter.decode(row["raw"])
            if not isinstance(messages, list) or not messages or len(messages) > 10000:
                raise ContractError("invalid_message_batch")
        except (ValueError, UnicodeError, RecursionError):
            messages = [None]
        events = []
        source_highwaters = {}
        sequence_highwaters = {}
        for index, message in enumerate(messages):
            flags = {"capture_only", "source_sequence_unavailable"}
            if recovered:
                flags.add("recovered_after_restart")
            if meta["origin"] != "live":
                flags.add("not_live_evidence")
            if meta["clock_regression"]:
                flags.add("clock_regression")
            symbol = instrument = identity = source_id = source_ns = None
            sequence = None
            kind = "recorder.reject"
            payload = {"reason": "invalid_message"}
            try:
                if not isinstance(message, dict):
                    raise ContractError("invalid_message")
                if meta["internal"]:
                    kind, payload = message["event_type"], message["payload"]
                    if kind.startswith("reference."):
                        self.validate_reference(kind, payload)
                elif provider == "alpaca" and message.get("T") in {"success", "error", "subscription"}:
                    kind, payload = "provider.control", {"type": message["T"]}
                    if message["T"] == "error":
                        payload["code"] = message.get("code")
                    elif message["T"] == "subscription":
                        payload["channels"] = {k: v for k, v in message.items() if k != "T" and isinstance(v, list)}
                    else:
                        payload["state"] = message.get("msg") if message.get("msg") in {"connected", "authenticated"} else "unknown"
                else:
                    observation = adapter.normalize(message)
                    kind, payload, source_ns, source_id, symbol = (observation.kind, observation.payload,
                        observation.source_ns, observation.source_id, observation.symbol)
                    sequence = observation.sequence
                    flags.update(observation.flags)
                    if not kind.startswith("market."):
                        # Provider controls do not participate in market clocks/identity.
                        raise _ProviderControl(kind, payload)
                    if sequence is not None:
                        flags.discard("source_sequence_unavailable")
                        key = (symbol, payload.get("provider_details", {}).get("session"), source_ns // 86_400_000_000_000)
                        if key not in sequence_highwaters:
                            last = self.ledger.connection.execute(
                                "SELECT MAX(json_extract(event_json,'$.source_sequence')) FROM events "
                                "WHERE json_extract(event_json,'$.connection_id')=? AND json_extract(event_json,'$.symbol')=? "
                                "AND json_extract(event_json,'$.payload.provider_details.session')=? "
                                "AND json_extract(event_json,'$.source_time_ns') / 86400000000000=?",
                                (meta["connection_id"], *key)).fetchone()[0]
                            sequence_highwaters[key] = last
                        last = sequence_highwaters[key]
                        if last is not None:
                            if sequence > last + 1:
                                flags.add("sequence_jump_unverified")
                            elif sequence <= last:
                                flags.add("sequence_repeat_or_reorder")
                        sequence_highwaters[key] = max(sequence, last or 0)
                    clock_key = (kind, symbol)
                    if clock_key not in source_highwaters:
                        last = self.ledger.connection.execute(
                            "SELECT MAX(json_extract(event_json,'$.source_time_ns')) FROM events "
                            "WHERE event_type=? AND json_extract(event_json,'$.symbol')=? "
                            "AND json_extract(event_json,'$.origin')=? AND json_extract(event_json,'$.feed')=?",
                            (*clock_key, meta["origin"], meta["feed"])).fetchone()[0]
                        source_highwaters[clock_key] = last or 0
                    if source_ns < source_highwaters[clock_key]:
                        flags.add("out_of_order_source_time")
                    source_highwaters[clock_key] = max(source_ns, source_highwaters[clock_key])
                    instrument, identity = self.identity(symbol, source_ns, meta["received_ns"], meta["origin"])
                    if not instrument:
                        flags.add("unresolved_instrument")
                    if source_ns > meta["received_ns"] + 1_000_000_000:
                        flags.add("future_source_time")
                    if meta["received_ns"] - source_ns > 2_000_000_000 and kind in {"market.trade", "market.quote"}:
                        flags.add("stale_on_receipt")
                    if kind == "market.quote":
                        from decimal import Decimal
                        if Decimal(payload["ask"]) <= Decimal(payload["bid"]) or min(Decimal(payload["ask"]), Decimal(payload["bid"])) <= 0:
                            flags.add("unusable_quote")
                        flags.add("quote_size_conversion_unverified")
                    flags.update({"corporate_action_coverage_unverified", "trade_condition_policy_unapproved"})
            except _ProviderControl as control:
                kind, payload = control.kind, control.payload
            except (ContractError, KeyError, TypeError) as exc:
                kind, payload = "recorder.reject", {"reason": str(exc) if isinstance(exc, ContractError) else "invalid_message"}
                flags.add("quarantined")
            # Identity of provider content excludes local delivery metadata. Repeated
            # deliveries stay in the journal, including quotes without provider IDs.
            content_hash = digest({"provider": provider, "feed": meta["feed"],
                                   "origin": meta["origin"], "message": message})
            event = Event(event_id=f"{meta['run_id']}:{raw_id}:{index}", event_type=kind,
                          run_id=meta["run_id"], connection_id=meta["connection_id"],
                          provider="recorder" if meta["internal"] else provider, feed=meta["feed"], origin=meta["origin"],
                          received_ns=meta["received_ns"], received_monotonic_ns=meta["received_monotonic_ns"],
                          normalized_ns=max(self.clock(), meta["received_ns"]), source_time_ns=source_ns,
                          source_event_id=source_id, source_sequence=sequence, symbol=symbol,
                          instrument_id=instrument, identity_event_id=identity, raw_id=raw_id, raw_index=index,
                          payload=payload, quality_flags=tuple(sorted(flags)), content_hash=content_hash,
                          schema_version=adapter.schema_version, adapter_version=adapter.version)
            events.append(event)
        self.ledger.append_events(raw_id, events)

    @staticmethod
    def validate_reference(kind, payload):
        if not isinstance(payload, dict):
            raise ContractError("invalid_reference")
        for key in ("source", "source_sha256"):
            if not isinstance(payload.get(key), str) or not payload[key]:
                raise ContractError("reference_provenance_missing")
        if len(payload["source_sha256"]) != 64 or any(c not in "0123456789abcdef" for c in payload["source_sha256"]):
            raise ContractError("invalid_reference_hash")
        if kind == "reference.instrument":
            from .events import SYMBOL, integer
            if not SYMBOL.fullmatch(str(payload.get("symbol", ""))) or not payload.get("instrument_id"):
                raise ContractError("invalid_instrument_identity")
            start, end = integer(payload.get("effective_from_ns")), integer(payload.get("effective_to_ns"))
            if start >= end or ":" not in payload["instrument_id"]:
                raise ContractError("invalid_identity_interval")
        elif kind == "reference.corporate_action":
            if not all(payload.get(k) for k in ("instrument_id", "action_id", "action_type", "effective_at")):
                raise ContractError("invalid_corporate_action")
            timestamp_ns(payload["effective_at"])
        elif kind == "reference.calendar":
            if not payload.get("exchange") or timestamp_ns(payload.get("open")) >= timestamp_ns(payload.get("close")):
                raise ContractError("invalid_calendar")


class _ProviderControl(Exception):
    def __init__(self, kind, payload):
        self.kind, self.payload = kind, payload
