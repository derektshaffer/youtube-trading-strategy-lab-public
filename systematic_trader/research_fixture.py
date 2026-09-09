"""Small, explicitly synthetic integration input; never provider/live evidence."""
from datetime import datetime, timezone
from dataclasses import asdict

from .events import canonical_json, digest, timestamp_ns
from .features import MINUTE, Session, Universe, Discovery, feature_snapshot
from .ledger import Recorder
from .market_state import MarketState
from .research_engine import OpeningRangeBreakout, Simulator, ExecutionPolicy, RiskBook, RiskPolicy

OPEN = timestamp_ns("2026-09-04T13:30:00Z")
SESSION = Session("fixture-2026-09-04", OPEN, OPEN+390*MINUTE, digest("synthetic-calendar-v1"))
KEY = ("alpaca", "sip", "fixture", "fixture:AAPL")


def stamp(ns):
    seconds, fraction = divmod(ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")+f".{fraction:09d}Z"


def history(*, session=SESSION, key=KEY):
    return [dict(instrument_id=key[3],provider=key[0],feed=key[1],origin="fixture",
                 session_id=f"fixture-prior-{i}",close_ns=session.open_ns-(16-i)*1440*MINUTE,
                 known_ns=session.open_ns-(16-i)*1440*MINUTE+MINUTE,high="102",low="98",close="100",
                 volume="2000000",dollar_volume="200000000",open_volume="20000",
                 source_hash=digest(["synthetic-history",i])) for i in range(15)]


def universe():
    u=Universe()
    u.observe([dict(instrument_id=KEY[3],symbol="AAPL",active=True,asset_type="common_stock")],
              known_ns=OPEN-MINUTE,effective_ns=OPEN-MINUTE,source_hash=digest("synthetic-universe"))
    return u


def populate(ledger, *, session=SESSION, scenario="positive"):
    """One fixed fixture session with an opening range and executable later quotes."""
    if scenario not in {"positive", "losing", "no_trade"}:raise ValueError("unknown_fixture_scenario")
    if ledger.watermark():raise ValueError("fixture_requires_empty_journal")
    now=[session.open_ns-MINUTE]
    r=Recorder(ledger,origin="fixture",clock=lambda:now[0],monotonic=lambda:now[0]-session.open_ns+MINUTE,
               run_id="fixture-run-v1")
    r.connection_id="fixture-connection-v1"
    r.internal("reference.instrument",dict(symbol="AAPL",instrument_id=KEY[3],effective_from_ns=session.open_ns-MINUTE,
       effective_to_ns=session.close_ns+MINUTE,source="synthetic-fixture-only",source_sha256=digest("fixture-reference"),
       round_lot_size=100,tick_size="0.01",corporate_actions_complete=True,fixture_condition_policy=True))
    def send(ns,message):
        now[0]=ns
        r.ingest(canonical_json([message]))
    send(session.open_ns,dict(T="s",S="AAPL",sc="T",rc="N",t=stamp(session.open_ns),z="C"))
    for minute in range(5):
        start=session.open_ns+minute*MINUTE
        send(start+MINUTE,dict(T="b",S="AAPL",o="100",h="100.20",l="99.90",c="100.15",
             v=10000,n=100,vw="100.10",t=stamp(start)))
    def quote(ns,bid,ask,size=1):
        send(ns,dict(T="q",S="AAPL",bp=bid,ap=ask,bs=size,**{"as":size},bx="Q",ax="Q",c=["R"],t=stamp(ns),z="C"))
    ready=session.open_ns+5*MINUTE
    quote(ready,"100.20","100.21")
    send(ready+10_000_000,dict(T="t",S="AAPL",i=1,p="100.00" if scenario=="no_trade" else "100.21",s=50,x="Q",c=["@"],t=stamp(ready+10_000_000),z="C"))
    quote(ready+120_000_000,"100.20","100.21")
    quote(ready+250_000_000,"100.20","100.21",2)
    bid,ask=("99.70","99.71") if scenario=="losing" else ("100.50","100.51")
    quote(session.close_ns-5*MINUTE,bid,ask,3)
    quote(session.close_ns-5*MINUTE+120_000_000,bid,ask,3)
    return ledger.verify()


def evaluate(events, *, execution_policy=ExecutionPolicy()):
    """Reconstruct every feature/decision/fill from a fixed committed sequence."""
    from .performance_engine import evaluate_session
    return evaluate_session(events, session=SESSION, daily_history=history(),
        universe_snapshots=universe().snapshots, keys=[KEY], execution_policy=execution_policy)
