import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from systematic_trader.events import Event, ContractError
from systematic_trader.ledger import Ledger, Recorder
from systematic_trader.providers import TradierConsolidated, AlpacaSIP, adapter_for
from systematic_trader.tradier import TradierConfig, TradierCapture, credentials
from systematic_trader.service import CaptureFailure


def timesale(**changes):
    return dict(type="timesale", symbol="SPY", exch="Q", bid="200", ask="200.02", last="200.01", size="100",
                date="1788528600123", seq=10, flag="", cancel=False, correction=False, session="normal", **changes)


def quote():
    return dict(type="quote", symbol="SPY", bid="200", ask="200.02", bidsz=1, asksz=2, bidexch="Q", askexch="Q",
                biddate="1788528600000", askdate="1788528600123")


def capture(directory, messages):
    with Ledger(directory, min_free_bytes=0) as ledger:
        recorder = Recorder(ledger, provider="tradier", feed="tradier_consolidated", origin="fixture")
        raw = b'\n'.join(json.dumps(m).encode() for m in messages)
        recorder.ingest(raw)
        assert ledger.connection.execute("SELECT raw FROM receipts").fetchone()[0] == raw
        assert ledger.verify()["pending_receipts"] == 0
        return list(ledger.replay(through_seq=ledger.watermark()))


def test_tradier_raw_first_schema_units_and_precision(tmp_path):
    events = capture(tmp_path, [timesale(), quote()])
    trade, q = events
    assert trade["provider"] == "tradier" and trade["schema_version"] == 2
    assert trade["source_sequence"] == 10 and trade["source_event_id"] is None
    assert trade["source_time_ns"] == 1788528600123000000
    assert trade["payload"]["conditions"] is None and trade["payload"]["tape"] is None
    assert trade["payload"]["provider_details"]["session"] == "normal"
    assert q["source_time_ns"] == 1788528600000000000
    assert q["payload"]["size_unit"] == "provider_unspecified"
    schema = json.loads((Path(__file__).parents[2] / "systematic_trader/event.v2.schema.json").read_text())
    import jsonschema
    for e in events:
        e.pop("ledger_seq");jsonschema.validate(e,schema);Event(**e).validate()


def test_sequence_jumps_are_suspected_not_definite_loss(tmp_path):
    events = capture(tmp_path, [timesale(), {**timesale(), "seq":12}, {**timesale(), "seq":11}, timesale()])
    assert "sequence_jump_unverified" in events[1]["quality_flags"]
    assert "sequence_repeat_or_reorder" in events[2]["quality_flags"]
    assert events[3]["duplicate_of"] == events[0]["event_id"]
    assert events[0]["payload"]["provider_details"]["sequence_contiguous"] is False


def test_amendments_do_not_fabricate_original_identity(tmp_path):
    events = capture(tmp_path, [{**timesale(), "cancel":True}, {**timesale(), "correction":True},
                               {**timesale(), "cancel":True,"correction":True}])
    assert events[0]["event_type"] == "market.trade_cancel"
    assert events[1]["event_type"] == "market.trade_correction"
    for e in events[:2]:
        assert e["payload"]["original_trade_id"] is None
        assert "unresolved_amendment_link" in e["quality_flags"]
    assert events[2]["event_type"] == "recorder.reject"


def test_deterministic_provider_normalization():
    adapter = TradierConsolidated()
    raw = json.dumps(timesale()).encode()
    assert adapter.normalize(adapter.decode(raw)[0]) == TradierConsolidated().normalize(timesale())
    assert adapter_for("alpaca", "sip").version == AlpacaSIP.version
    with pytest.raises(ContractError):adapter_for("tradier", "sip")
    with pytest.raises(ContractError):adapter_for("tradier", "tradier_consolidated", "unknown")


def test_crash_recovery_uses_saved_provider_and_original_raw(tmp_path):
    with Ledger(tmp_path, min_free_bytes=0) as ledger:
        r = Recorder(ledger,provider="tradier",feed="tradier_consolidated",origin="fixture")
        r.process = Mock(side_effect=RuntimeError("crash"))
        with pytest.raises(RuntimeError):r.ingest(json.dumps(timesale()))
        # Recovery isn't governed by the new process's default provider.
        Recorder(ledger,origin="fixture").recover()
        e = list(ledger.replay(through_seq=ledger.watermark()))[0]
        assert e["provider"] == "tradier" and e["payload"]["price"] == "200.01"
        assert "recovered_after_restart" in e["quality_flags"]


def test_credentials_absence_fails_before_network(monkeypatch):
    monkeypatch.delenv("TRADIER_ACCESS_TOKEN",raising=False);monkeypatch.delenv("TRADIER_TOKEN",raising=False)
    with pytest.raises(CaptureFailure,match="not_configured"):credentials()
    monkeypatch.setenv("TRADIER_ACCESS_TOKEN","fixture-one");monkeypatch.setenv("TRADIER_TOKEN","fixture-two")
    with pytest.raises(CaptureFailure,match="ambiguous"):credentials()


def test_tradier_subscription_retains_amendments():
    p = TradierConfig(("SPY",)).subscription("fixture-session")
    assert p["filter"] == ["timesale","quote"]
    assert p["validOnly"] is False and p["advancedDetails"] is True


def test_transport_captures_then_stops_without_order_client(tmp_path):
    import threading
    stop=threading.Event()
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def send(self,data):assert json.loads(data)["filter"] == ["timesale","quote"]
        def recv(self,timeout):stop.set();return json.dumps(timesale())
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        r=Recorder(ledger,provider="tradier",feed="tradier_consolidated")
        count=TradierCapture(r,TradierConfig(("SPY",)),"fixture-token",session_factory=lambda _:"fixture-session",
                            connect=lambda _:Socket(),stop=stop).run()
        assert count==1 and ledger.verify()["pending_receipts"]==0
        events=list(ledger.replay(through_seq=ledger.watermark()))
        assert events[-1]["payload"]["certified"] is False


def test_transport_rejection_stays_raw_and_not_retried(tmp_path):
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def send(self,_):pass
        def recv(self,timeout):return '{"error":"session already in use"}'
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        r=Recorder(ledger,provider="tradier",feed="tradier_consolidated")
        factory=Mock(return_value="fixture-session")
        with pytest.raises(CaptureFailure,match="rejected_stream"):
            TradierCapture(r,TradierConfig(("SPY",)),"fixture-token",session_factory=factory,connect=lambda _:Socket()).run()
        assert factory.call_count==1
        assert any(b'session already in use' in r[0] for r in ledger.connection.execute("SELECT raw FROM receipts"))


def test_disconnect_gets_new_session_preserves_duplicates_and_unresolved_gap(tmp_path):
    class Stop:
        stopped=False
        def is_set(self):return self.stopped
        def wait(self,_):return self.stopped
    stop=Stop();attempts=[]
    class Socket:
        calls=0
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def send(self,raw):attempts.append(json.loads(raw)["sessionid"])
        def recv(self,timeout):
            self.calls+=1
            if len(attempts)==1 and self.calls==2:raise ConnectionError("fixture-disconnect")
            if len(attempts)==2:stop.stopped=True
            return json.dumps(timesale())
    factory=Mock(side_effect=["session-one","session-two"])
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        r=Recorder(ledger,provider="tradier",feed="tradier_consolidated")
        assert TradierCapture(r,TradierConfig(("SPY",)),"test-token",stop=stop,
            session_factory=factory,connect=lambda _:Socket()).run()==2
        events=list(ledger.replay(through_seq=ledger.watermark()))
        trades=[e for e in events if e["event_type"]=="market.trade"]
        assert trades[1]["duplicate_of"]==trades[0]["event_id"]
        assert trades[1]["connection_id"]!=trades[0]["connection_id"]
        assert any(e["payload"].get("reason")=="transport_disconnect" and e["payload"]["unresolved"] for e in events)
        assert ledger.verify()["pending_receipts"]==0
    assert attempts==["session-one","session-two"]


def test_fixed_session_endpoint_no_redirect_and_secret_free_failure(monkeypatch):
    from systematic_trader.tradier import create_session, NoRedirect, SESSION_ENDPOINT
    from urllib.error import HTTPError
    class Opener:
        def open(self,request,timeout):
            assert request.full_url==SESSION_ENDPOINT and request.method=="POST"
            raise HTTPError(SESSION_ENDPOINT,403,"fixture-secret",{},None)
    monkeypatch.setattr("systematic_trader.tradier.build_opener",lambda *_:Opener())
    with pytest.raises(CaptureFailure,match="tradier_market_session_rejected_403") as exc:create_session("fixture-secret")
    assert "fixture-secret" not in str(exc.value)
    with pytest.raises(CaptureFailure,match="redirect_refused"):NoRedirect().redirect_request(None)
