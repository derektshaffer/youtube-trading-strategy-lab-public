"""Synthetic engineering example. Never market evidence or profitability data."""
from dataclasses import asdict
from decimal import Decimal
import json
from pathlib import Path

from ..events import timestamp_ns
from .contracts import Dataset, Session, Security, Tick, NS
from .experiments import Experiment
from .replay import file_hash


def create_fixture(output, *, seconds=1200, symbol="FIXTURE"):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    t = timestamp_ns("2026-09-08T09:30:00-04:00")
    session = Session("2026-09-08", t-19800*NS, t, t+23400*NS, t+37800*NS, "synthetic-calendar")
    security = Security(symbol, "equity", "synthetic-security", first_trading_date="2020-01-01", known_ns=t-20000*NS, source="synthetic-only")
    dataset = Dataset("synthetic-engineering-only", "sip_nbbo", "fixture", "actual_receipt", 0,
        session.pre_ns, t+seconds*NS, (symbol,), (session,), (security,),
        {k:"synthetic-only" for k in ("quotes_complete", "trades_complete", "status_complete", "conditions_reviewed",
            "corrections_resolved", "share_units_verified", "identity_verified", "calendar_verified", "premarket_complete")},
        dict(train=[t-20000*NS,t+23400*NS], validation=[t+86400*NS,t+2*86400*NS],
             holdout=[t+3*86400*NS,t+4*86400*NS], purpose="development"), "global")
    sequence = 0
    with (output/"events.jsonl").open("x") as stream:
        def emit(at,kind,**fields):
            nonlocal sequence
            sequence += 1
            tick = Tick(symbol,kind,t+at,t+at,sequence,f"synthetic-{sequence}",source="synthetic-only",eligible=True,**fields)
            stream.write(json.dumps(asdict(tick))+"\n")
        emit(-90*NS,"status",status="trading")
        emit(-60*NS,"trade",price="9",size=10)
        emit(-30*NS,"trade",price="10",size=10)
        for second in range(seconds):
            mid = Decimal("10.01")+Decimal(second)*Decimal("0.002")-(Decimal("0.006") if second%10==7 else 0)
            emit(second*NS,"quote",bid=str(mid-Decimal("0.01")),ask=str(mid+Decimal("0.01")),bid_size=10000,ask_size=10000,size_unit="shares")
            emit(second*NS+1,"trade",price=str(mid+Decimal("0.01")),size=10+second%60)
    (output/"dataset.json").write_text(json.dumps(dict(dataset=asdict(dataset),events_sha256=file_hash(output/"events.jsonl")),indent=2)+"\n")
    (output/"config.json").write_text(json.dumps(dict(experiment=asdict(Experiment(hold_seconds=10,qty=10))),indent=2)+"\n")
    return output/"dataset.json"


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    create_fixture(args.output)
