"""Frozen pilot chronology. Routine tools have no holdout-unlock switch."""
from dataclasses import dataclass
from .events import ContractError, digest, timestamp_ns

PROTOCOL_HASH = "0009a9116bd6fae2636b54499b47848af95619123298eb03db26aaced80d0380"
HOLDOUT_START = timestamp_ns("2025-05-01T00:00:00-04:00")
HOLDOUT_END = timestamp_ns("2025-07-01T00:00:00-04:00")


def query_time(value):
    if isinstance(value,str) and len(value)==10:
        value += "T00:00:00Z"
    return timestamp_ns(value)


def protect_holdout(start, end):
    """Queries have inclusive endpoints, unlike research half-open windows."""
    a,b=query_time(start),query_time(end)
    if a>b:raise ContractError("research_query_time_order")
    if a<HOLDOUT_END and b>=HOLDOUT_START:
        raise ContractError("frozen_final_holdout_access_denied")


@dataclass(frozen=True)
class ResearchSplit:
    protocol_hash: str = PROTOCOL_HASH

    def __post_init__(self):
        if self.protocol_hash!=PROTOCOL_HASH:
            raise ContractError("frozen_research_protocol_mismatch")

    def authorize(self, start_ns, end_ns, *, purpose):
        if type(start_ns) is not int or type(end_ns) is not int or start_ns>=end_ns:
            raise ContractError("invalid_research_window")
        if start_ns<HOLDOUT_END and end_ns>HOLDOUT_START:
            raise ContractError("frozen_final_holdout_access_denied")
        ranges={
            "discovery_development":("2024-12-02T00:00:00-05:00","2025-03-01T00:00:00-05:00"),
            "parameter_search":("2025-01-02T00:00:00-05:00","2025-03-01T00:00:00-05:00"),
            "strategy_development":("2025-01-02T00:00:00-05:00","2025-03-01T00:00:00-05:00"),
            "frozen_validation":("2025-03-03T00:00:00-05:00","2025-04-01T00:00:00-04:00"),
            "frozen_oos":("2025-04-01T00:00:00-04:00","2025-05-01T00:00:00-04:00"),
        }
        if purpose not in ranges:raise ContractError("unknown_research_access_purpose")
        a,b=map(timestamp_ns,ranges[purpose])
        if not a<=start_ns<end_ns<=b:raise ContractError("research_period_not_authorized_for_purpose")

    def manifest(self):
        body=dict(version="frozen-research-split-v1",protocol_hash=self.protocol_hash,
            warmup=["2024-12-02","2024-12-31"],development=["2025-01-02","2025-02-28"],
            validation=["2025-03-03","2025-03-31"],out_of_sample=["2025-04-01","2025-04-30"],
            final_holdout=["2025-05-01","2025-06-30"],timezone="America/New_York",
            holdout_start_ns=HOLDOUT_START,holdout_end_exclusive_ns=HOLDOUT_END,
            holdout_locked=True,routine_unlock_supported=False,execution_authority="none")
        return {**body,"split_hash":digest(body)}
