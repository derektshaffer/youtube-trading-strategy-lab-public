"""Frozen comparison plans. No holdout reader, optimizer or promotion function."""
from dataclasses import replace, asdict
from decimal import Decimal

from ..events import ContractError, digest
from .experiments import LATENCY_MS


def stress_plan(experiment, policy, costs):
    result = []
    for ms in LATENCY_MS:
        result.append(dict(name=f"latency_{ms}ms", experiment=asdict(experiment),
                           policy=asdict(replace(policy, latency_ns=ms*1_000_000)), costs=asdict(costs)))
    for label, p, c in (
        ("slippage_x1.5", policy, replace(costs, slippage_per_share=str(Decimal(costs.slippage_per_share)*Decimal("1.5")))),
        ("latency_plus_100ms", replace(policy, latency_ns=policy.latency_ns+100_000_000), costs),
        ("latency_plus_50pct", replace(policy, latency_ns=policy.latency_ns*3//2), costs),
        ("spread_x1.5", replace(policy, spread_multiplier=str(Decimal(policy.spread_multiplier)*Decimal("1.5"))), costs),
        ("displayed_size_half", replace(policy, displayed_fraction=str(Decimal(policy.displayed_fraction)/2)), costs),
    ):
        result.append(dict(name=label, experiment=asdict(experiment), policy=asdict(p), costs=asdict(c)))
    for field in ("target", "minimum_direction", "max_spread", "minimum_acceleration", "maximum_pullback"):
        for factor in ("0.8", "0.9", "1", "1.1", "1.2"):
            e = replace(experiment, **{field: str(Decimal(getattr(experiment, field))*Decimal(factor))})
            result.append(dict(name=field+"_x"+factor, experiment=asdict(e), policy=asdict(policy), costs=asdict(costs)))
    return dict(comparisons=result, selection="predeclared_no_optimization", passive_fill_stress="not_applicable_no_passive_fills",
                plan_hash=digest(result), execution_authority="none")


def walk_forward_plan(session_days, *, train_sessions=20, test_sessions=5, embargo_sessions=1):
    if session_days != sorted(set(session_days)) or min(train_sessions, test_sessions, embargo_sessions) < 1:
        raise ContractError("short_horizon_walk_forward_plan_invalid")
    folds = []
    for i in range(train_sessions+embargo_sessions, len(session_days)-test_sessions+1, test_sessions):
        folds.append(dict(train=session_days[:i-embargo_sessions], embargo=session_days[i-embargo_sessions:i],
                          test=session_days[i:i+test_sessions]))
    return dict(folds=folds, status="PLANNED_NOT_RUN", holdout_access=False,
                limitation="development_only_folds_do_not_replace_untouched_holdout")
