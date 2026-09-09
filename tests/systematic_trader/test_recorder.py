import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from systematic_trader.events import ContractError, normalize_market, timestamp_ns
from systematic_trader.ledger import Ledger, LedgerError, Recorder
from systematic_trader.service import Config, CaptureFailure, CaptureService, CHANNELS, AUTO_CHANNELS

T = timestamp_ns("2026-09-04T13:30:00.123456789Z")


class Clock:
    def __init__(self, value=T + 1_000_000):
        self.value = value

    def __call__(self):
        self.value += 1
        return self.value


@pytest.fixture
def capture(tmp_path):
    with Ledger(tmp_path, min_free_bytes=0) as ledger:
        clock = Clock()
        yield ledger, Recorder(ledger, origin="fixture", clock=clock, monotonic=clock), clock


def trade(**changes):
    return {"T": "t", "S": "AAPL", "i": 42, "p": "200.123456789", "s": 10,
            "x": "Q", "c": ["@", "I"], "t": "2026-09-04T13:30:00.123456789Z", "z": "C", **changes}


def quote(**changes):
    return {"T": "q", "S": "AAPL", "bp": "200.10", "ap": "200.12", "bs": 2, "as": 3,
            "bx": "Q", "ax": "Q", "c": ["R"], "t": "2026-09-04T13:30:00.123456789Z", "z": "C", **changes}


def bar(**changes):
    return {"T": "b", "S": "AAPL", "o": 200, "h": 202, "l": 199, "c": 201,
            "v": 1000, "n": 100, "vw": "200.7", "t": "2026-09-04T13:30:00Z", **changes}


def rows(ledger):
    return list(ledger.replay(through_seq=ledger.watermark()))


def test_nanoseconds_and_offsets_are_exact():
    assert T == timestamp_ns("2026-09-04T09:30:00.123456789-04:00")
    assert T - timestamp_ns("2026-09-04T13:30:00Z") == 123456789


@pytest.mark.parametrize("stamp", ["2026-09-04T13:30:00", "bad", "2026-13-04T13:30:00Z", None,
                                    "2026-09-04T13:30:00.1234567890Z", 123, "2026-09-04T13:30:60Z"])
def test_bad_timestamps_rejected(stamp):
    with pytest.raises(ContractError):
        timestamp_ns(stamp)


def test_exact_raw_receipt_and_decimal_payload(capture):
    ledger, recorder, clock = capture
    raw = b'[{"T":"t","S":"AAPL","i":42,"p":200.123456789,"s":10,"x":"Q","c":["@","I"],"t":"2026-09-04T13:30:00.123456789Z","z":"C"}]'
    recorder.ingest(raw)
    event = rows(ledger)[0]
    assert ledger.connection.execute("SELECT raw FROM receipts").fetchone()[0] == raw
    assert event["payload"]["price"] == "200.123456789"
    assert event["source_time_ns"] == T < event["received_ns"] <= event["normalized_ns"]
    assert event["payload"]["conditions"] == ["@", "I"]
    assert {"not_live_evidence", "unresolved_instrument"} <= set(event["quality_flags"])
    assert event["execution_authority"] == "none"
    assert ledger.verify()["pending_receipts"] == 0


def test_quotes_preserve_round_lots(capture):
    ledger, recorder, _ = capture
    recorder.ingest(json.dumps([quote()]))
    p = rows(ledger)[0]["payload"]
    assert p["ask_size"] == 3 and p["size_unit"] == "provider_round_lots"
    assert p["round_lot_size"] is None


@pytest.mark.parametrize("changes", [{"ap": 199}, {"bp": "0.00"}, {"bp": "200.12"}])
def test_unusable_quotes_marked_not_repaired(capture, changes):
    ledger, recorder, _ = capture
    recorder.ingest(json.dumps([quote(**changes)]))
    assert "unusable_quote" in rows(ledger)[0]["quality_flags"]


@pytest.mark.parametrize("message", [trade(p="NaN"), trade(s=-1), trade(s=True), trade(p=0),
                                    trade(t="bad"), trade(c=None), bar(h=198), trade(S="bad/symbol"),
                                    trade(p="1e99999999"), trade(p="1e-99999999")])
def test_invalid_market_payload_quarantined_with_raw(capture, message):
    ledger, recorder, _ = capture
    recorder.ingest(json.dumps([message]))
    assert rows(ledger)[0]["event_type"] == "recorder.reject"
    assert ledger.verify()["receipts"] == 1


@pytest.mark.parametrize("raw", [b"{", b"[]", b"{}", b'[NaN]', b'["x"]', b'[{"T":"t","T":"q"}]', b'\xff', b'[' * 2000 + b']' * 2000])
def test_malformed_frames_are_durable(capture, raw):
    ledger, recorder, _ = capture
    recorder.ingest(raw)
    assert rows(ledger)[0]["event_type"] == "recorder.reject"
    assert ledger.verify()["pending_receipts"] == 0


def test_revisions_never_change_earlier_view(capture):
    ledger, recorder, clock = capture
    recorder.ingest(json.dumps([bar()]))
    cursor, first = ledger.watermark(), rows(ledger)[0]
    clock.value += 60_000_000_000
    recorder.ingest(json.dumps([bar(T="u", c=202, v=1100)]))
    assert list(ledger.replay(through_seq=cursor)) == [first]
    assert len(list(ledger.replay(through_seq=ledger.watermark(), received_through_ns=first["received_ns"]))) == 1
    updated = rows(ledger)[1]
    assert updated["event_type"] == "market.bar_revision"
    assert updated["payload"]["revision_key"] == first["payload"]["revision_key"]
    assert updated["payload"]["close"] == "202"


def test_corrections_cancels_status_luld(capture):
    ledger, recorder, _ = capture
    correction = dict(T="c", S="AAPL", x="Q", oi=42, op=200, os=10, oc=["@"],
                      ci=43, cp=201, cs=12, cc=["@"], t="2026-09-04T13:30:01Z", z="C")
    cancel = trade(T="x", a="C")
    status = dict(T="s", S="AAPL", sc="H", rc="T12", t="2026-09-04T13:30:01Z", z="C")
    luld = dict(T="l", S="AAPL", u=220, d=180, i="B", t="2026-09-04T13:30:01Z", z="C")
    recorder.ingest(json.dumps([trade(), correction, cancel, status, luld]))
    result = rows(ledger)
    assert [r["event_type"] for r in result] == ["market.trade", "market.trade_correction", "market.trade_cancel", "market.status", "market.luld"]
    assert result[1]["payload"]["original_trade_id"] == "42"
    assert result[2]["payload"]["original_trade_id"] == "42"


def test_repeat_delivery_retained_across_restarts(capture):
    ledger, recorder, clock = capture
    recorder.ingest(json.dumps([trade()]))
    Recorder(ledger, origin="fixture", clock=clock, monotonic=clock).ingest(json.dumps([trade()]))
    first, second = rows(ledger)
    assert second["duplicate_of"] == first["event_id"]
    assert second["event_id"] != first["event_id"]
    assert second["run_id"] != first["run_id"]


def test_future_and_clock_regression_visible(capture):
    ledger, recorder, clock = capture
    recorder.ingest(json.dumps([trade(t="2026-09-05T13:30:00Z")]))
    clock.value -= 1_000_000_000
    recorder.ingest(json.dumps([trade()]))
    assert "future_source_time" in rows(ledger)[0]["quality_flags"]
    assert "clock_regression" in rows(ledger)[1]["quality_flags"]


def instrument(**changes):
    return {"symbol": "AAPL", "instrument_id": "alpaca_asset:test-stable-id", "effective_from_ns": T - 100,
            "effective_to_ns": T + 10**12, "source": "test_vendor_identity", "source_sha256": "a" * 64, **changes}


def test_identity_is_receipt_and_effective_time_scoped(capture):
    ledger, recorder, clock = capture
    recorder.ingest(json.dumps([trade()]))
    recorder.internal("reference.instrument", instrument())
    recorder.ingest(json.dumps([trade(i=43)]))
    first, reference, second = rows(ledger)
    assert first["instrument_id"] is None
    assert second["instrument_id"] == "alpaca_asset:test-stable-id"
    assert second["identity_event_id"] == reference["event_id"]
    recorder.internal("reference.instrument", instrument(instrument_id="vendor:conflicting-id"))
    recorder.ingest(json.dumps([trade(i=44)]))
    assert rows(ledger)[-1]["instrument_id"] is None


def test_late_reference_cannot_repair_pending_past_receipt(capture):
    ledger, recorder, clock = capture
    old_process = recorder.process
    recorder.process = Mock(side_effect=RuntimeError("injected crash"))
    with pytest.raises(RuntimeError):
        recorder.ingest(json.dumps([trade()]))
    recorder.process = old_process
    recorder.internal("reference.instrument", instrument())
    recorder.recover()
    event = rows(ledger)[-1]
    assert event["instrument_id"] is None
    assert "recovered_after_restart" in event["quality_flags"]


def test_writer_lock_and_live_read_snapshot(capture):
    ledger, recorder, _ = capture
    recorder.ingest(json.dumps([trade()]))
    with pytest.raises(LedgerError, match="already_running"):
        Ledger(ledger.directory, min_free_bytes=0)
    with Ledger(ledger.directory, read_only=True) as reader:
        assert reader.verify()["events"] == 1
        with pytest.raises(sqlite3.OperationalError):
            reader.connection.execute("INSERT INTO metadata VALUES(1)")


def test_update_delete_prevented_and_integrity_checked(capture):
    ledger, recorder, _ = capture
    recorder.ingest(json.dumps([trade()]))
    for table in ("receipts", "events", "completions", "metadata"):
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            ledger.connection.execute(f"DELETE FROM {table}")
        ledger.connection.rollback()
    assert ledger.verify()["events"] == 1
    # Deliberate administrator-level tamper bypasses the trigger; verifier detects it.
    ledger.connection.execute("DROP TRIGGER no_UPDATE_events")
    ledger.connection.execute("UPDATE events SET event_json='{}'")
    ledger.connection.commit()
    with pytest.raises(LedgerError, match="hash_chain"):
        ledger.verify()


def test_disk_failure_no_false_ack(capture, monkeypatch):
    ledger, recorder, _ = capture
    ledger.min_free_bytes = 100
    monkeypatch.setattr("systematic_trader.ledger.shutil.disk_usage", lambda _: type("Disk", (), {"free": 10})())
    with pytest.raises(LedgerError, match="disk_reserve"):
        recorder.ingest(json.dumps([trade()]))
    assert ledger.watermark() == 0


def test_batch_normalization_failure_rolls_back_all_events(capture, monkeypatch):
    ledger, recorder, _ = capture
    from systematic_trader.events import Event
    original = Event.validate
    def fail_second(event):
        if event.raw_index == 1:
            raise ContractError("injected_failure")
        original(event)
    monkeypatch.setattr(Event, "validate", fail_second)
    with pytest.raises(ContractError):
        recorder.ingest(json.dumps([trade(), quote()]))
    assert ledger.watermark() == 0 and len(ledger.pending()) == 1
    monkeypatch.setattr(Event, "validate", original)
    recorder.recover()
    assert ledger.watermark() == 2 and not ledger.pending()


def test_process_death_after_raw_commit_recovers_original_receipt(tmp_path):
    code = '''
import os,sys
from systematic_trader.ledger import Ledger,Recorder
r=Recorder(Ledger(sys.argv[1],min_free_bytes=0),origin="fixture")
r.process=lambda _:os._exit(37)
r.ingest('[{"T":"q"}]')
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True)
    assert result.returncode == 37
    with Ledger(tmp_path, min_free_bytes=0) as ledger:
        before = json.loads(ledger.pending()[0]["metadata_json"])
        assert ledger.watermark() == 0
        Recorder(ledger, origin="fixture").recover()
        event = rows(ledger)[0]
        assert event["received_ns"] == before["received_ns"]
        assert event["normalized_ns"] > event["received_ns"]
        assert event["event_type"] == "recorder.reject"


@pytest.mark.parametrize("kwargs", [{"symbols": ()}, {"symbols": ("AAPL", "AAPL")},
                                   {"symbols": ("*",)}, {"symbols": ("AAPL",), "feed": "iex"},
                                   {"symbols": ("AAPL",), "heartbeat_seconds": float("nan")}])
def test_config_fails_closed(kwargs):
    with pytest.raises(ContractError):
        Config(**kwargs)


class Socket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def send(self, value):
        self.sent.append(json.loads(value))

    def recv(self, timeout):
        item = next(self.messages)
        if isinstance(item, Exception):
            raise item
        return json.dumps(item)


def handshake():
    return [[{"T": "success", "msg": "connected"}], [{"T": "success", "msg": "authenticated"}],
            [{"T": "subscription", **{c: ["AAPL"] for c in CHANNELS + AUTO_CHANNELS}}]]


@pytest.mark.parametrize("code", [402, 405, 406, 409, 410, 407])
def test_provider_rejection_never_retries_or_downgrades(tmp_path, code):
    with Ledger(tmp_path, min_free_bytes=0) as ledger:
        socket = Socket([[{"T": "error", "code": code, "msg": "sensitive provider detail"}]])
        connect = Mock(return_value=socket)
        service = CaptureService(Recorder(ledger), Config(("AAPL",)), ("test-key-123", "test-secret-456"), connect=connect)
        with pytest.raises(CaptureFailure, match=f"provider_rejected_{code}"):
            service.run()
        assert connect.call_count == 1
        assert "sensitive provider detail" not in json.dumps(rows(ledger))
        assert rows(ledger)[-1]["payload"]["state"] == "failed"


def test_partial_subscription_blocks_capture(tmp_path):
    with Ledger(tmp_path, min_free_bytes=0) as ledger:
        messages = handshake()
        messages[-1][0]["statuses"] = []
        service = CaptureService(Recorder(ledger), Config(("AAPL",)), ("test-key-123", "test-secret-456"),
                                 connect=lambda _: Socket(messages))
        with pytest.raises(CaptureFailure, match="incomplete_subscription"):
            service.run()


def test_credentials_are_never_written(tmp_path):
    with Ledger(tmp_path, min_free_bytes=0) as ledger:
        socket = Socket([[{"T": "error", "msg": "test-secret-456", "code": 402}]])
        service = CaptureService(Recorder(ledger), Config(("AAPL",)), ("test-key-123", "test-secret-456"), connect=lambda _: socket)
        with pytest.raises(CaptureFailure, match="credential_reflection"):
            service.run()
        raw = b"".join(r[0] for r in ledger.connection.execute("SELECT raw FROM receipts"))
        assert b"test-secret-456" not in raw and b"test-key-123" not in raw
        assert any(e["payload"].get("reason") == "credential_reflection_redacted" for e in rows(ledger))


def test_bounded_reconnect_keeps_gap(tmp_path):
    with Ledger(tmp_path, min_free_bytes=0) as ledger:
        connect = Mock(side_effect=ConnectionError("sensitive network text"))
        stop = Mock(); stop.is_set.return_value = False
        service = CaptureService(Recorder(ledger), Config(("AAPL",), max_reconnects=1),
                                 ("test-key-123", "test-secret-456"), connect=connect, stop=stop)
        with pytest.raises(CaptureFailure, match="reconnect_budget"):
            service.run()
        assert connect.call_count == 2
        assert all(e["payload"]["unresolved"] for e in rows(ledger) if e["event_type"] == "recorder.gap")
        assert "sensitive network text" not in json.dumps(rows(ledger))


def test_out_of_order_delivery_not_sorted_into_past(capture):
    ledger, recorder, _ = capture
    recorder.ingest(json.dumps([trade(i=1, t="2026-09-04T13:30:02Z"), trade(i=2)]))
    assert rows(ledger)[0]["source_event_id"] == "1"
    assert rows(ledger)[1]["source_event_id"] == "2"
    assert "out_of_order_source_time" in rows(ledger)[1]["quality_flags"]


def test_fixture_reference_cannot_resolve_live_identity(capture):
    ledger, recorder, clock = capture
    recorder.internal("reference.instrument", instrument())
    Recorder(ledger, origin="live", clock=clock, monotonic=clock).ingest(json.dumps([trade()]))
    assert rows(ledger)[-1]["instrument_id"] is None


def test_market_events_validate_against_published_schema(capture):
    jsonschema = pytest.importorskip("jsonschema")
    from systematic_trader.events import Event, EVENT_TYPES
    ledger, recorder, _ = capture
    recorder.ingest(json.dumps([trade(), quote(), bar(), bar(T="u"), {"T": "unknown"}]))
    schema = json.loads((Path(__file__).parents[2] / "systematic_trader/event.schema.json").read_text())
    assert set(schema["properties"]) == set(Event.__dataclass_fields__)
    assert set(schema["properties"]["event_type"]["enum"]) == EVENT_TYPES
    for row in rows(ledger):
        row.pop("ledger_seq")
        jsonschema.validate(row, schema)
    bad = rows(ledger)[0]
    bad.pop("ledger_seq")
    bad["payload"]["price"] = 200.1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
    with pytest.raises(ContractError):
        Event(**bad).validate()
    from systematic_trader.events import payload_contracts
    for rule in schema["allOf"][1:]:
        kind = rule["if"]["properties"]["event_type"]["const"]
        assert rule["then"]["properties"]["payload"]["properties"] == payload_contracts()[kind]


@pytest.mark.parametrize("change", [{"schema_version": 2}, {"schema_version": True},
                                   {"execution_authority": "live"}, {"event_type": "order.intent"}])
def test_capture_contract_refuses_order_authority_and_unknown_versions(capture, change):
    from systematic_trader.events import Event
    ledger, recorder, _ = capture
    recorder.ingest(json.dumps([trade()]))
    event = rows(ledger)[0]
    event.pop("ledger_seq")
    event.update(change)
    with pytest.raises(ContractError):
        Event(**event).validate()


def test_silence_persists_gap_and_heartbeat(tmp_path):
    import threading
    stop = threading.Event()
    clock = [0]
    class SilentSocket(Socket):
        def recv(self, timeout):
            try:
                return super().recv(timeout)
            except StopIteration:
                clock[0] += 1
                if clock[0] >= 4:
                    stop.set()
                raise TimeoutError
    with Ledger(tmp_path, min_free_bytes=0) as ledger:
        service = CaptureService(Recorder(ledger), Config(("AAPL",), heartbeat_seconds=1, silence_seconds=2),
                                 ("test-key-123", "test-secret-456"), connect=lambda _: SilentSocket(handshake()),
                                 stop=stop, monotonic=lambda: clock[0])
        service.run()
        data = rows(ledger)
        assert any(r["payload"].get("reason") == "market_stream_silent" for r in data)
        assert any(r["payload"].get("state") == "capture_degraded" for r in data)
        assert data[-1]["payload"]["state"] == "stopped"


def test_real_local_websocket_capture_and_clean_stop(tmp_path):
    """Exercises the actual transport against a local fixture, not Alpaca."""
    pytest.importorskip("websockets")
    from websockets.sync.server import serve
    from websockets.sync.client import connect
    import threading
    stop = threading.Event()
    observed = []
    def handler(ws):
        ws.send(json.dumps(handshake()[0]))
        observed.append(json.loads(ws.recv()))
        ws.send(json.dumps(handshake()[1]))
        observed.append(json.loads(ws.recv()))
        ws.send(json.dumps(handshake()[2]))
        ws.send(json.dumps([trade(), quote(), bar()]))
        try:
            ws.recv(timeout=5)
        except Exception:
            pass
    with serve(handler, "127.0.0.1", 0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        address = f"ws://127.0.0.1:{server.socket.getsockname()[1]}"
        class Client:
            def __enter__(self):
                self.ws = connect(address)
                return self
            def __exit__(self, *_):
                self.ws.close()
            def send(self, value):
                self.ws.send(value)
            def recv(self, timeout):
                data = self.ws.recv(timeout=timeout)
                if json.loads(data)[0].get("T") == "t":
                    stop.set()
                return data
        try:
            with Ledger(tmp_path, min_free_bytes=0) as ledger:
                CaptureService(Recorder(ledger), Config(("AAPL",)), ("test-key-123", "test-secret-456"),
                               connect=lambda _: Client(), stop=stop).run()
                data = rows(ledger)
                assert sum(e["event_type"].startswith("market.") for e in data) == 3
                assert data[-1]["payload"]["state"] == "stopped"
                assert ledger.verify()["pending_receipts"] == 0
                raw = b"".join(r[0] for r in ledger.connection.execute("SELECT raw FROM receipts"))
                assert b"test-secret-456" not in raw
            assert observed[0]["action"] == "auth"
            assert observed[1] == Config(("AAPL",)).subscription()
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.mark.parametrize("source,service", [("lab-keychain", "Trading Intelligence Lab"),
                                           ("lab-dev-keychain", "Trading Intelligence Lab Dev")])
def test_credential_source_is_explicit_and_never_mixes_namespaces(monkeypatch, source, service):
    from systematic_trader.__main__ import get_credentials
    class Keychain:
        def __init__(self):
            self.service = "inherited-unwanted-service"
            self._fallback_service = "unwanted-fallback"
        def get_secret(self, account):
            assert self.service == service
            assert self._fallback_service is None
            return "fixture-value-" + account
    monkeypatch.setenv("TRADING_INTELLIGENCE_DEV_MODE", "1")
    monkeypatch.setattr("hybrid_runtime.keychain.MacOSKeychain", Keychain)
    pair = get_credentials(source)
    assert len(pair) == 2 and all(pair)


def test_unknown_credential_source_rejected():
    from systematic_trader.__main__ import get_credentials
    with pytest.raises(CaptureFailure, match="unknown_credential_source"):
        get_credentials("unapproved-location")
