"""Cost decomposition, clustered uncertainty and reproducible research reports."""
from collections import defaultdict, Counter
from dataclasses import asdict
from decimal import Decimal
from random import Random
from statistics import median

from ..events import digest
from .contracts import NS

D = Decimal


def cluster_interval(rows, *, seed=17, resamples=1000):
    groups = defaultdict(list)
    for r in rows:
        groups[(r["symbol"], r["day"])].append(D(r["net"]))
    clusters = list(groups.values())
    if len(clusters) < 20:
        return dict(method="ticker_day_cluster_bootstrap", clusters=len(clusters), interval=None,
                    reason="fewer_than_20_clusters", seed=seed, resamples=resamples)
    rng = Random(seed); means = []
    for _ in range(resamples):
        sample = [r for _ in clusters for r in rng.choice(clusters)]
        means.append(sum(sample)/len(sample))
    means.sort()
    return dict(method="ticker_day_cluster_bootstrap", clusters=len(clusters),
                interval=[str(means[int(resamples*.025)]), str(means[int(resamples*.975)])],
                seed=seed, resamples=resamples,
                limitation="cross_symbol_same_day_dependence_not_removed")


def episode_row(ep):
    entry = ep["entry"]
    ins, outs = entry.fills, [f for o in ep["exits"] for f in o.fills]
    row = {k: v for k, v in ep.items() if k not in {"entry", "exits", "key", "pending_exit", "exit_due"}}
    row.update(entry_order_id=entry.id, exit_order_ids=[o.id for o in ep["exits"]],
               requested_qty=entry.qty, filled_qty=entry.filled,
               remaining_qty=entry.filled-sum(f["qty"] for f in outs), net=None,
               gross_midpoint_movement=None, spread_cost=None, slippage_cost=None,
               commissions=None, fees=None, gross_target_executed=False)
    if ep["status"] == "NO_FILL":
        row["net"] = "0"
    if ep["status"] == "CLOSED":
        fills = ins+outs
        gross = sum(D(f["midpoint"])*f["qty"] for f in outs)-sum(D(f["midpoint"])*f["qty"] for f in ins)
        costs = {k: sum(D(f[k]) for f in fills) for k in ("spread_cost", "slippage_cost", "commission", "fees")}
        net = sum(D(f["price"])*f["qty"] for f in outs)-sum(D(f["price"])*f["qty"] for f in ins)-costs["commission"]-costs["fees"]
        assert net == gross-sum(costs.values())
        avg_exit = sum(D(f["price"])*f["qty"] for f in outs)/entry.filled
        row.update(net=str(net), gross_midpoint_movement=str(gross), spread_cost=str(costs["spread_cost"]),
                   slippage_cost=str(costs["slippage_cost"]), commissions=str(costs["commission"]), fees=str(costs["fees"]),
                   gross_target_executed=avg_exit >= D(ep["entry_price"])+D(ep["target"]),
                   holding_seconds=str(D(outs[-1]["at_ns"]-ins[0]["at_ns"])/NS),
                   exit_ns=outs[-1]["at_ns"], deployed_notional=str(sum(D(f["price"])*f["qty"] for f in ins)))
    return row


def metrics(rows, exposure_ns):
    closed = [r for r in rows if r["status"] == "CLOSED"]
    unresolved = [r for r in rows if r["remaining_qty"] or r["net"] is None]
    net = sum(D(r["net"]) for r in closed)
    hours = D(exposure_ns)/NS/3600
    gross_hits = sum(r["gross_target_hit"] for r in rows)
    executed_hits = sum(r["gross_target_executed"] for r in rows)
    winners = sum(max(D(0), D(r["net"])) for r in closed)
    losers = -sum(min(D(0), D(r["net"])) for r in closed)
    by_cluster = defaultdict(lambda: D(0))
    peak = running = drawdown = D(0)
    for r in sorted(closed, key=lambda r: r["exit_ns"]):
        by_cluster[r["symbol"]+"|"+r["day"]] += D(r["net"])
        running += D(r["net"]); peak = max(peak, running); drawdown = max(drawdown, peak-running)
    positive = sum(max(D(0), v) for v in by_cluster.values())
    result = dict(opportunities=len(rows), reasonably_separated_episodes=len(rows),
        closed_trades=len(closed), unresolved_episodes=len(unresolved), gross_target_hits=gross_hits,
        executable_target_hits=executed_hits,
        gross_target_hit_rate=str(D(gross_hits)/len(rows)) if rows else None,
        executable_target_hit_rate=str(D(executed_hits)/len(rows)) if rows else None,
        apparent_hits_without_executable_hit=sum(r["gross_target_hit"] and not r["gross_target_executed"] for r in rows),
        net_executable_expectancy_per_opportunity=str(net/len(rows)) if rows and not unresolved else None,
        net_executable_expectancy_per_trade=str(net/len(closed)) if closed and not unresolved else None,
        net_per_symbol_hour=str(net/hours) if hours and not unresolved else None,
        opportunities_per_symbol_hour=str(D(len(rows))/hours) if hours else None,
        closed_subset_net=str(net), net=str(net) if not unresolved else None,
        fill_rate=str(D(sum(r["filled_qty"] for r in rows))/sum(r["requested_qty"] for r in rows)) if rows else None,
        partial_entry_rate=str(D(sum(0 < r["filled_qty"] < r["requested_qty"] for r in rows))/len(rows)) if rows else None,
        failed_entries=sum(r["filled_qty"] == 0 for r in rows),
        profit_factor=str(winners/losers) if losers and not unresolved else None,
        maximum_drawdown=None, realized_close_drawdown=str(drawdown),
        drawdown_limitation="intratrade_mark_to_market_and_unknown_liquidation_not_established",
        median_holding_seconds=str(median(D(r["holding_seconds"]) for r in closed)) if closed else None,
        return_on_sum_deployed_notional=str(net/sum(D(r["deployed_notional"]) for r in closed)) if closed and not unresolved else None,
        top_ticker_day_positive_pnl_share=str(max(by_cluster.values())/positive) if positive else None,
        observed_entry_capacity_shares=sum(r["filled_qty"] for r in rows),
        capacity_limitation="observed_depleted_top_of_book_only_no_unobserved_depth_or_scaling",
        clustered_uncertainty=cluster_interval([r for r in rows if r["net"] is not None]) if not unresolved else None,
        daily_trade_count_extrapolation="disabled_observed_episodes_only")
    for k in ("gross_midpoint_movement", "spread_cost", "slippage_cost", "commissions", "fees"):
        result[k] = str(sum(D(r[k]) for r in closed))
    shares = sum(r["filled_qty"] for r in closed)
    result["average_round_trip_spread_paid_per_share"] = str(D(result["spread_cost"])/shares) if shares else None
    result["average_round_trip_slippage_per_share"] = str(D(result["slippage_cost"])/shares) if shares else None
    return result


def report(harness):
    h = harness
    rows = [episode_row(ep) for ep in h.episodes]
    m = metrics(rows, h.exposure_ns)
    by_weekday = {}
    for weekday in range(5):
        subset = [r for r in rows if r["weekday"] == weekday]
        duration = 0
        for s in h.dataset.sessions:
            if s.weekday == weekday:
                a, b = h.config.bounds(s)
                duration += max(0, min(b,h.dataset.end_ns)-max(a,h.dataset.start_ns))*len(h.dataset.symbols)
        by_weekday[str(weekday)] = metrics(subset, duration)
    markouts = defaultdict(list)
    for f in h.sim.fills:
        for horizon, value in f["markouts"].items():
            if value is not None:
                markouts[horizon].append(D(value))
    missing = h.dataset.blockers()
    if not h.counts["quote"] or not h.counts["trade"]:
        missing.append("quotes_and_individual_trades_required")
    if missing:
        # Rejected entries do not establish zero expectancy. No economic statistic
        # from a blocked tape can masquerade as an executable result.
        for item in [m, *by_weekday.values()]:
            for key in ("net", "closed_subset_net", "net_executable_expectancy_per_opportunity",
                        "net_executable_expectancy_per_trade", "net_per_symbol_hour", "profit_factor",
                        "return_on_sum_deployed_notional", "clustered_uncertainty"):
                item[key] = None
    ci = m["clustered_uncertainty"]
    rejected = ci and ci["interval"] and D(ci["interval"][1]) < 0
    body = dict(version="short-horizon-result-v1", experiment=asdict(h.config),
                dataset=h.dataset.manifest(), execution_policy=asdict(h.sim.policy), costs=asdict(h.sim.costs),
                execution_authority="none", production_eligible=False,
                research_status="fixture_only" if h.dataset.origin == "fixture" else "research_only",
                admission="BLOCKED" if missing else "CONDITIONAL_L1_SIMULATION",
                missing=sorted(set(missing)), metrics=m, by_weekday=by_weekday,
                episodes=rows, orders=[asdict(o) for o in h.sim.orders], event_counts=dict(h.counts),
                no_trade_reasons=dict(h.no_trade_reasons), observation_comparison=h.observations,
                window_diagnostics=[dict(symbol=symbol,day=day,window=window,**stats.result())
                                    for (symbol,day,window),stats in h.window_stats.items()],
                forensic_trade_sequence_summary=h.forensics,
                markouts={k: dict(count=len(v), mean_signed_midpoint_change=str(sum(v)/len(v))) for k,v in markouts.items()},
                markout_definition="signed_midpoint_change_from_fill_time_at_fixed_horizon_no_forward_quote_search",
                gross_target_definition="eligible_trade_at_or_above_signal_trade_plus_target_within_signal_time_horizon_regardless_of_fill",
                preliminary_outcome="NOT_EVALUATED" if missing or not rows or h.dataset.origin == "fixture" else
                    "REJECT" if rejected else "PROMISING — MORE DATA NEEDED" if m["net"] is not None and D(m["net"]) > 0 else "INCONCLUSIVE",
                validation=dict(holdout_locked=True, holdout_inspections=0, parameter_selection="none",
                    stress_required=True, production_promotion_supported=False),
                limitations=["conditional_observed_book_proxy_not_broker_fills", "long_only_first_baselines",
                    "marketable_orders_only_passive_queue_models_deferred", "episode_cooldown_does_not_prove_independence",
                    "first_eligible_trade_after_boundary_with_configured_decision_cadence_not_guaranteed_open_print_entry",
                    "weekday_and_regime_are_descriptive_not_causal", "no_profitability_claim_from_fixture_or_paper_results"])
    # Dataclass limits are Decimal; serialize losslessly before hashing.
    import json
    body = json.loads(json.dumps(body, default=str))
    return {**body, "result_hash": digest(body)}
