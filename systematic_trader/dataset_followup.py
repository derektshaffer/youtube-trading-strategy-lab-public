"""Reproduce bounded real quote-spread and missing-minute diagnostics.

No trade expectancy, fills, zero-volume bars or market-status assumptions are
generated. Export completeness does not prove historical first availability.
"""
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path

from .data_acquisition import verified_pages
from .events import ContractError, digest, timestamp_ns
from .features import MINUTE


def microstructure_followup(directory, *, symbols=("AAPL","POWL","MDXH"),
                            start="2025-03-03T14:30:00Z",end="2025-03-03T14:40:00Z"):
    root=Path(directory);start_ns,end_ns=timestamp_ns(start),timestamp_ns(end)
    if start_ns%MINUTE or end_ns%MINUTE or not 0<end_ns-start_ns<=60*MINUTE:
        raise ContractError("invalid_microstructure_diagnostic_window")
    source_heads={}
    for kind in ("quotes","trades"):
        manifest=json.loads((root/kind/"complete.json").read_text())
        params=manifest["params"]
        if (not set(symbols)<=set(params["symbols"].split(",")) or timestamp_ns(params["start"])>start_ns or
                timestamp_ns(params["end"])<end_ns):
            raise ContractError("microstructure_export_window_incomplete")
        source_heads[kind]=manifest["page_chain_head"]
    source_heads["bars"]=json.loads((root/"bars/complete.json").read_text())["page_chain_head"]
    spreads=defaultdict(list);trades=defaultdict(list);quotes=defaultdict(list);bar_starts=defaultdict(set)
    for kind,target in (("quotes",quotes),("trades",trades)):
        for page,meta in verified_pages(root/kind):
            for symbol,rows in page[kind].items():
                if symbol not in symbols:continue
                for row in rows:
                    t=timestamp_ns(row["t"])
                    if start_ns<=t<end_ns:
                        target[(symbol,t//MINUTE*MINUTE)].append(row)
                        if kind=="quotes":
                            bid,ask=Decimal(row["bp"]),Decimal(row["ap"])
                            if 0<bid<ask:spreads[symbol].append((ask-bid)/((ask+bid)/2)*10000)
    for page,meta in verified_pages(root/"bars"):
        for symbol in symbols:
            for row in page["bars"].get(symbol,[]):
                t=timestamp_ns(row["t"])
                if start_ns<=t<end_ns:bar_starts[symbol].add(t)
    result=dict(version="real-microstructure-followup-v2",start=start,end_exclusive=end,symbols={},source_heads=source_heads,
                evidence="descriptive_export_sample_not_execution_calibration",certified=False,execution_authority="none")
    for symbol in symbols:
        values=sorted(spreads[symbol]);buckets=[]
        for t in range(start_ns,end_ns,MINUTE):
            raw=trades[(symbol,t)];present=t in bar_starts[symbol]
            reason=("bar_present" if present else "no_trades_in_complete_export_window" if not raw else
                    "all_trades_have_odd_lot_condition" if all("I" in r["c"] for r in raw) else "absence_unresolved")
            buckets.append(dict(start_ns=t,bar_present=present,trades=len(raw),quotes=len(quotes[(symbol,t)]),absence_diagnostic=reason))
        result["symbols"][symbol]=dict(quote_samples=len(values),spread_bps={k:str(values[min(len(values)-1,int(len(values)*q))])
            for k,q in (("p50",.5),("p90",.9),("p99",.99))} if values else None,minutes=buckets)
    return {**result,"result_hash":digest(result)}
