"""Preregisterable OOS diagnostics; deliberately no strategy promotion API.

Numeric defaults are conservative research protocol examples, not a guarantee
of profitability. A diagnostic pass never clears independent production gates.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal

from .events import ContractError, digest, integer
from .validation import session_groups, session_statistics

D = Decimal


@dataclass(frozen=True)
class QualificationProtocol:
    version: str = "qualification-diagnostics-v1"
    candidate: str = "orb"
    baseline: str = "no_trade"
    registered_policies: tuple = ("orb", "no_trade")
    neighbors: tuple = ()
    execution_scenarios: tuple = ("double_costs", "one_second_arrival")
    minimum_trades: int = 100
    minimum_sessions: int = 30
    minimum_instruments: int = 5
    minimum_regimes: int = 2
    minimum_periods: int = 3
    maximum_ticker_trade_share: str = "0.35"
    maximum_ticker_profit_share: str = "0.50"

    def __post_init__(self):
        if (not isinstance(self.registered_policies, tuple) or not isinstance(self.neighbors, tuple) or
            not isinstance(self.execution_scenarios, tuple)):
            raise ContractError("immutable_qualification_policy_sets_required")
        for names in (self.registered_policies, self.neighbors, self.execution_scenarios):
            if len(set(names)) != len(names) or any(not isinstance(n, str) or not n for n in names):
                raise ContractError("invalid_qualification_policy_set")
        if (self.candidate == self.baseline or not {self.candidate, self.baseline, *self.neighbors} <= set(self.registered_policies)
            or self.candidate in self.neighbors or self.baseline in self.neighbors or not self.execution_scenarios):
            raise ContractError("incomplete_qualification_protocol")
        for name in ("minimum_trades", "minimum_sessions", "minimum_instruments", "minimum_regimes", "minimum_periods"):
            if type(getattr(self,name)) is not int or getattr(self,name) < 1:
                raise ContractError("invalid_qualification_minimum")
        for value in (self.maximum_ticker_trade_share, self.maximum_ticker_profit_share):
            if not D(value).is_finite() or not 0 < D(value) <= 1:
                raise ContractError("invalid_qualification_concentration_limit")


def qualification_diagnostics(records, *, protocol=QualificationProtocol()):
    """Assess frozen OOS outcomes without selecting/tuning or touching holdout.

    Records extend walk-forward session rows with unique outcome IDs, instrument,
    policy trade counts, as-of regime/period tags, execution scenario results and
    split lineage. Final holdout and training/selection outcomes are forbidden.
    """
    if not records:
        raise ContractError("qualification_outcomes_required")
    ids = set()
    cohorts = set()
    for r in records:
        if not isinstance(r.get("outcome_id"), str) or not r["outcome_id"] or r["outcome_id"] in ids:
            raise ContractError("duplicate_or_missing_qualification_outcome_id")
        ids.add(r["outcome_id"])
        if r.get("split") != "out_of_sample" or not r.get("fold_id") or not r.get("input_hash"):
            raise ContractError("qualification_requires_oos_lineage")
        if r.get("selection_cutoff_ns", r["start_ns"]) >= r["start_ns"]:
            raise ContractError("qualification_selection_not_frozen_before_test")
        if not isinstance(r.get("instrument_id"), str) or ":" not in r["instrument_id"]:
            raise ContractError("qualification_stable_instrument_required")
        if not r.get("regime") or not r.get("period") or r.get("regime_known_ns", r["start_ns"]+1) > r["start_ns"]:
            raise ContractError("qualification_asof_regime_required")
        if set(r["policy_net_r"]) != set(protocol.registered_policies) or set(r.get("policy_trade_counts", {})) != set(protocol.registered_policies):
            raise ContractError("qualification_policy_coverage_incomplete")
        for n in r["policy_trade_counts"].values():integer(n)
        if any(not D(v).is_finite() for v in r["policy_net_r"].values()):
            raise ContractError("invalid_qualification_outcome")
        if any(r["policy_trade_counts"][p] == 0 and D(r["policy_net_r"][p]) != 0 for p in protocol.registered_policies):
            raise ContractError("profit_without_trades")
        if set(r.get("execution_net_r", {})) != set(protocol.execution_scenarios):
            raise ContractError("qualification_execution_coverage_incomplete")
        if any(not D(v).is_finite() for v in r["execution_net_r"].values()):
            raise ContractError("invalid_execution_sensitivity_outcome")
        cohorts.add(r["evidence"])
    if len(cohorts) != 1:
        raise ContractError("mixed_qualification_evidence")
    groups = session_groups(records)  # Reject overlaps, pending labels/gaps/residuals.
    ordered = [r for g in groups for r in sorted(g, key=lambda r:r["outcome_id"])]
    # Duplicate economic windows under renamed outcome IDs must not inflate n.
    windows = set()
    for r in ordered:
        # Contract is one aggregate outcome per instrument and session; trade
        # counts are explicit. Splitting/relabeling the window cannot double n.
        key = (r["instrument_id"], r["session_id"])
        if key in windows:
            raise ContractError("duplicate_qualification_economic_window")
        windows.add(key)
    candidate, baseline = protocol.candidate, protocol.baseline
    totals = {p:sum(D(r["policy_net_r"][p]) for r in ordered) for p in protocol.registered_policies}
    stress = {s:sum(D(r["execution_net_r"][s]) for r in ordered) for s in protocol.execution_scenarios}
    by_ticker, by_regime, by_period = {}, {}, {}
    for r in ordered:
        n, pnl = r["policy_trade_counts"][candidate], D(r["policy_net_r"][candidate])
        ticker = by_ticker.setdefault(r["instrument_id"], dict(trades=0, net_r=D(0), positive_r=D(0)))
        ticker["trades"] += n; ticker["net_r"] += pnl; ticker["positive_r"] += max(D(0),pnl)
        for mapping, label in ((by_regime,r["regime"]),(by_period,r["period"])):
            value = mapping.setdefault(label,dict(trades=0,net_r=D(0),sessions=set()))
            value["trades"]+=n;value["net_r"]+=pnl;value["sessions"].add(r["session_id"])
    trades=sum(v["trades"] for v in by_ticker.values())
    positive=sum(v["positive_r"] for v in by_ticker.values())
    max_trade_share=max((D(v["trades"])/trades for v in by_ticker.values()),default=D(1)) if trades else D(1)
    max_profit_share=max((v["positive_r"]/positive for v in by_ticker.values()),default=D(1)) if positive else D(1)
    deltas=[dict(session_id=g[0]["session_id"],net_r=str(sum(D(r["policy_net_r"][candidate])-D(r["policy_net_r"][baseline]) for r in g))) for g in groups]
    stats=session_statistics(deltas)
    checks=dict(minimum_trades=trades>=protocol.minimum_trades,
                minimum_sessions=len(groups)>=protocol.minimum_sessions,
                minimum_instruments=sum(v["trades"]>0 for v in by_ticker.values())>=protocol.minimum_instruments,
                ticker_trade_concentration=max_trade_share<=D(protocol.maximum_ticker_trade_share),
                ticker_profit_concentration=max_profit_share<=D(protocol.maximum_ticker_profit_share),
                positive_candidate_return=totals[candidate]>0, beats_baseline=totals[candidate]>totals[baseline],
                baseline_difference_bootstrap_positive=D(stats["session_bootstrap_95"][0])>0,
                regime_coverage=len(by_regime)>=protocol.minimum_regimes,
                regime_stability=all(v["trades"]>0 and v["net_r"]>0 for v in by_regime.values()),
                period_coverage=len(by_period)>=protocol.minimum_periods,
                period_stability=all(v["trades"]>0 and v["net_r"]>0 for v in by_period.values()),
                execution_sensitivity=all(value>0 for value in stress.values()),
                parameter_neighborhood_declared=bool(protocol.neighbors),
                parameter_neighborhood_positive=bool(protocol.neighbors) and all(totals[n]>totals[baseline] for n in protocol.neighbors))
    def serialize(mapping):
        return {label:{k:(str(v) if isinstance(v,D) else sorted(v) if isinstance(v,set) else v) for k,v in item.items()}
                for label,item in sorted(mapping.items())}
    data=dict(version=protocol.version, protocol=asdict(protocol), protocol_hash=digest(asdict(protocol)),
              outcomes_hash=digest(ordered), evidence=next(iter(cohorts)), checks=checks,
              research_checks_passed=all(checks.values()), failed_checks=sorted(k for k,v in checks.items() if not v),
              trade_count=trades, session_count=len(groups), policy_trials=len(protocol.registered_policies),
              policy_net_r={k:str(v) for k,v in totals.items()}, execution_net_r={k:str(v) for k,v in stress.items()},
              ticker_trade_share=str(max_trade_share),ticker_profit_share=str(max_profit_share),
              by_instrument=serialize(by_ticker),by_regime=serialize(by_regime),by_period=serialize(by_period),
              paired_session_statistics=stats,
              promotion_blockers=["independent_oos_lineage_verification_required", "multiple_testing_adjustment_unverified",
                                  "locked_final_holdout_gate_separate", "live_data_not_certified", "paper_execution_unqualified"],
              qualified=False,production_eligible=False,execution_authority="none")
    if data["evidence"] == "fixture":data["promotion_blockers"].append("fixture_not_performance_evidence")
    return {**data,"diagnostics_hash":digest(data)}
