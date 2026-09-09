"""Read-only operational evidence. Health cannot grant execution authority."""
from collections import Counter


def capture_health(ledger, *, now_ns):
    verification=ledger.verify()
    watermark=ledger.watermark()
    counts=Counter();flags=Counter();latest=None;origins=Counter();providers=Counter()
    for event in ledger.replay(through_seq=watermark):
        counts[event["event_type"]]+=1;flags.update(event["quality_flags"])
        if event["event_type"].startswith("market."):
            latest=max(latest or 0,event["received_ns"]);origins[event["origin"]]+=1
            providers[(event["provider"],event["feed"])]+=1
    return dict(watermark=watermark,journal=verification,event_counts=dict(counts),quality_counts=dict(flags),
                market_origins=dict(origins),market_providers={"/".join(k):v for k,v in providers.items()},
                market_receipt_age_ns=None if latest is None else now_ns-latest,
                unresolved_gap_records=counts["recorder.gap"],
                certification=dict(alpaca_sip="BLOCKED_ENTITLEMENT",tradier="UNVERIFIED",production="BLOCKED"),
                production_eligible=False,execution_authority="none",autonomous_live_trading=False)
