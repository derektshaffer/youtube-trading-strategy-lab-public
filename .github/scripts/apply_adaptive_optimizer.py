from pathlib import Path

engine_path = Path("youtube_strategy_engine.py")
engine = engine_path.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# Replace the fixed-grid rule generator with a balanced coarse search plus
# reusable local-refinement generators.
# ---------------------------------------------------------------------------
start = engine.index("def generate_strategy_variants(")
end = engine.index("def generate_execution_variants(", start)
new_generators = r'''def _valid_optimizer_rules(candidate: dict[str, Any]) -> bool:
    if candidate.get("session_start") and candidate.get("session_end"):
        start_clock = parse_clock_minutes(candidate["session_start"])
        end_clock = parse_clock_minutes(candidate["session_end"])
        if start_clock is not None and end_clock is not None and start_clock >= end_clock:
            return False
    min_price = safe_float(candidate.get("min_price"))
    max_price = safe_float(candidate.get("max_price"))
    return not (min_price is not None and max_price is not None and min_price >= max_price)


def _neighbor_values(
    value: float,
    steps: tuple[float, ...],
    *,
    minimum: float,
    maximum: float,
    integer: bool = False,
) -> list[float | int]:
    options: list[float | int] = []
    for step in steps:
        for direction in (-1.0, 1.0):
            candidate = min(maximum, max(minimum, value + direction * step))
            rounded: float | int = int(round(candidate)) if integer else round(candidate, 4)
            if rounded != value and rounded not in options:
                options.append(rounded)
    return options


def generate_strategy_variants(
    strategy: dict[str, Any],
    backtest_settings: BacktestSettings | None = None,
    *,
    maximum: int = 36,
) -> list[dict[str, Any]]:
    """Create a balanced coarse search across every measurable rule family.

    This stage intentionally avoids spending nearly the entire budget on stop x target
    combinations. Promising coarse candidates are refined later by the optimizer.
    """
    settings = backtest_settings or BacktestSettings()
    settings.validate()
    limit = max(1, min(320, int(maximum)))
    original = normalize_machine_rules(strategy.get("machine_rules"))
    baseline = dict(original)
    baseline["stop_loss_pct"] = original.get("stop_loss_pct") or settings.default_stop_pct
    baseline["reward_risk"] = original.get("reward_risk") or settings.default_reward_risk
    variants: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(updates: dict[str, Any]) -> None:
        if len(variants) >= limit:
            return
        candidate = normalize_machine_rules({**baseline, **updates})
        if not _valid_optimizer_rules(candidate):
            return
        signature = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
        if signature not in seen:
            seen.add(signature)
            variants.append(candidate)

    add({})

    if limit <= 64:
        stop_values = [0.75, 1.5, 2.5, 4.0, 5.0, 7.5, 10.0, 15.0]
        reward_values = [1.0, 1.5, 2.0, 3.0, 5.0]
        options_per_rule = 2
    elif limit <= 140:
        stop_values = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.5, 10.0, 12.5, 15.0]
        reward_values = [0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 5.0]
        options_per_rule = 3
    else:
        stop_values = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.2, 4.5, 5.0, 6.0, 7.5, 10.0, 12.5, 15.0]
        reward_values = [0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0]
        options_per_rule = 5

    stop_values = list(dict.fromkeys([round(float(baseline["stop_loss_pct"]), 4), *stop_values]))
    reward_values = list(dict.fromkeys([round(float(baseline["reward_risk"]), 4), *reward_values]))

    # Reserve part of the coarse budget for interactions. Everything else gets a fair
    # chance to be explored before stop/target cross-products can consume the search.
    reserve_for_interactions = min(max(8, int(limit * 0.28)), max(0, limit - 1))
    single_limit = max(1, limit - reserve_for_interactions)

    for stop in stop_values:
        if len(variants) >= single_limit:
            break
        add({"stop_loss_pct": stop})
    for reward in reward_values:
        if len(variants) >= single_limit:
            break
        add({"reward_risk": reward})

    tunable = (
        ("min_price", (0.60, 0.80, 1.20, 1.50), 0.01, 1_000.0, False),
        ("max_price", (0.70, 0.85, 1.20, 1.50), 0.01, 5_000.0, False),
        ("min_day_change_pct", (0.50, 0.70, 0.85, 1.15, 1.30, 1.60), -50.0, 200.0, False),
        ("min_relative_volume", (0.50, 0.70, 0.85, 1.15, 1.30, 1.60), 0.10, 50.0, False),
        ("min_dollar_volume", (0.50, 0.70, 0.85, 1.20, 1.50, 2.0), 100.0, 2_000_000_000.0, False),
        ("max_spread_pct", (0.60, 0.80, 1.25, 1.60), 0.01, 50.0, False),
        ("max_vwap_distance_pct", (0.50, 0.70, 0.85, 1.20, 1.50, 2.0), 0.05, 100.0, False),
        ("breakout_lookback_bars", (0.50, 0.70, 0.85, 1.20, 1.50, 2.0), 1.0, 150.0, True),
        ("opening_range_minutes", (0.50, 0.75, 1.25, 1.50, 2.0), 1.0, 180.0, True),
        ("volume_surge_ratio", (0.50, 0.70, 0.85, 1.20, 1.50, 2.0), 0.10, 50.0, False),
        ("minimum_green_bars", (0.50, 0.75, 1.25, 1.50, 2.0), 1.0, 12.0, True),
        ("max_hold_minutes", (0.50, 0.70, 0.85, 1.20, 1.50, 2.0), 1.0, 390.0, True),
    )
    single_adjustments: list[dict[str, Any]] = []
    for field_name, multipliers, minimum, maximum_value, integer in tunable:
        current = safe_float(original.get(field_name))
        if current is None:
            continue
        options = _optimizer_number_options(
            current,
            multipliers,
            minimum=minimum,
            maximum=maximum_value,
            integer=integer,
        )
        # Spread the coarse budget across rule families instead of exhausting one field.
        for option in options[:options_per_rule]:
            if option == current:
                continue
            update = {field_name: option}
            single_adjustments.append(update)
            if len(variants) < single_limit:
                add(update)

    for field_name in ("session_start", "session_end"):
        clock = original.get(field_name)
        if not clock:
            continue
        offsets = (-30, -15, 15, 30) if limit <= 140 else (-60, -30, -15, 15, 30, 60)
        for offset in offsets:
            adjusted = _shift_strategy_clock(str(clock), offset, earliest=9 * 60 + 30, latest=15 * 60 + 55)
            if adjusted != clock:
                update = {field_name: adjusted}
                single_adjustments.append(update)
                if len(variants) < single_limit:
                    add(update)

    for field_name in ("above_vwap", "vwap_reclaim"):
        current = original.get(field_name)
        if isinstance(current, bool):
            update = {field_name: not current}
            single_adjustments.append(update)
            if len(variants) < single_limit:
                add(update)

    # Interaction budget: alternate stop/target interactions with interactions among
    # other rule families so no single class monopolizes the remaining combinations.
    stop_reward_pairs = (
        {"stop_loss_pct": stop, "reward_risk": reward}
        for stop in stop_values
        for reward in reward_values
    )
    other_pairs = (
        {**left, **right}
        for left_index, left in enumerate(single_adjustments)
        for right in single_adjustments[left_index + 1:]
        if set(left).isdisjoint(right)
    )
    stop_iter = iter(stop_reward_pairs)
    other_iter = iter(other_pairs)
    while len(variants) < limit:
        added_before = len(variants)
        try:
            add(next(stop_iter))
        except StopIteration:
            pass
        if len(variants) >= limit:
            break
        try:
            add(next(other_iter))
        except StopIteration:
            pass
        if len(variants) == added_before:
            # Both iterators may be exhausted or producing only duplicates.
            try:
                add(next(stop_iter))
            except StopIteration:
                try:
                    add(next(other_iter))
                except StopIteration:
                    break
            if len(variants) == added_before:
                break

    return variants[:limit]


def generate_local_strategy_refinements(
    seed_rules: dict[str, Any],
    backtest_settings: BacktestSettings,
    *,
    maximum: int = 48,
    stage: str = "fine",
) -> list[dict[str, Any]]:
    """Refine numeric rules around a promising coarse candidate.

    Fine refinement makes meaningful jumps around the coarse winner. Final refinement
    uses smaller increments so values such as a 9.5% stop can be discovered even when
    the coarse grid only contained 7.5% and 10%.
    """
    backtest_settings.validate()
    limit = max(1, min(160, int(maximum)))
    baseline = normalize_machine_rules(seed_rules)
    baseline["stop_loss_pct"] = baseline.get("stop_loss_pct") or backtest_settings.default_stop_pct
    baseline["reward_risk"] = baseline.get("reward_risk") or backtest_settings.default_reward_risk
    variants: list[dict[str, Any]] = []
    seen: set[str] = {json.dumps(baseline, sort_keys=True, separators=(",", ":"))}

    def add(updates: dict[str, Any]) -> None:
        if len(variants) >= limit:
            return
        candidate = normalize_machine_rules({**baseline, **updates})
        if not _valid_optimizer_rules(candidate):
            return
        signature = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
        if signature not in seen:
            seen.add(signature)
            variants.append(candidate)

    fine_specs: dict[str, tuple[tuple[float, ...], float, float, bool]] = {
        "stop_loss_pct": ((2.5, 2.0, 1.0, 0.5), 0.1, 30.0, False),
        "reward_risk": ((1.0, 0.5, 0.25), 0.2, 10.0, False),
        "min_price": ((1.0, 0.50, 0.25, 0.10), 0.01, 1_000.0, False),
        "max_price": ((2.0, 1.0, 0.50, 0.25), 0.01, 5_000.0, False),
        "min_day_change_pct": ((2.0, 1.0, 0.5, 0.25), -50.0, 200.0, False),
        "min_relative_volume": ((1.0, 0.5, 0.25, 0.10), 0.10, 50.0, False),
        "min_dollar_volume": ((500_000.0, 250_000.0, 100_000.0, 50_000.0), 100.0, 2_000_000_000.0, False),
        "max_spread_pct": ((0.50, 0.25, 0.10), 0.01, 50.0, False),
        "max_vwap_distance_pct": ((2.0, 1.0, 0.5, 0.25), 0.05, 100.0, False),
        "breakout_lookback_bars": ((10.0, 5.0, 2.0, 1.0), 1.0, 150.0, True),
        "opening_range_minutes": ((15.0, 10.0, 5.0, 2.0), 1.0, 180.0, True),
        "volume_surge_ratio": ((1.0, 0.5, 0.25, 0.10), 0.10, 50.0, False),
        "minimum_green_bars": ((2.0, 1.0), 1.0, 12.0, True),
        "max_hold_minutes": ((60.0, 30.0, 15.0, 5.0), 1.0, 390.0, True),
    }
    final_specs: dict[str, tuple[tuple[float, ...], float, float, bool]] = {
        "stop_loss_pct": ((0.5, 0.25, 0.10), 0.1, 30.0, False),
        "reward_risk": ((0.25, 0.10, 0.05), 0.2, 10.0, False),
        "min_price": ((0.25, 0.10, 0.05), 0.01, 1_000.0, False),
        "max_price": ((0.50, 0.25, 0.10), 0.01, 5_000.0, False),
        "min_day_change_pct": ((0.50, 0.25, 0.10), -50.0, 200.0, False),
        "min_relative_volume": ((0.25, 0.10, 0.05), 0.10, 50.0, False),
        "min_dollar_volume": ((100_000.0, 50_000.0, 25_000.0), 100.0, 2_000_000_000.0, False),
        "max_spread_pct": ((0.10, 0.05, 0.02), 0.01, 50.0, False),
        "max_vwap_distance_pct": ((0.50, 0.25, 0.10), 0.05, 100.0, False),
        "breakout_lookback_bars": ((2.0, 1.0), 1.0, 150.0, True),
        "opening_range_minutes": ((5.0, 2.0, 1.0), 1.0, 180.0, True),
        "volume_surge_ratio": ((0.25, 0.10, 0.05), 0.10, 50.0, False),
        "minimum_green_bars": ((1.0,), 1.0, 12.0, True),
        "max_hold_minutes": ((15.0, 5.0, 1.0), 1.0, 390.0, True),
    }
    specs = final_specs if stage == "final" else fine_specs

    local_values: dict[str, list[float | int]] = {}
    for field_name, (steps, minimum, maximum_value, integer) in specs.items():
        current = safe_float(baseline.get(field_name))
        if current is None:
            continue
        options = _neighbor_values(
            current,
            steps,
            minimum=minimum,
            maximum=maximum_value,
            integer=integer,
        )
        local_values[field_name] = options
        for option in options:
            add({field_name: option})

    clock_offsets = (-30, -15, 15, 30) if stage != "final" else (-5, 5)
    for field_name in ("session_start", "session_end"):
        clock = baseline.get(field_name)
        if not clock:
            continue
        for offset in clock_offsets:
            adjusted = _shift_strategy_clock(str(clock), offset, earliest=9 * 60 + 30, latest=15 * 60 + 55)
            if adjusted != clock:
                add({field_name: adjusted})

    # Stop and reward interact strongly, so refine them jointly after one-at-a-time
    # neighbors. This local cross-product is bounded by the remaining budget.
    stop_options = local_values.get("stop_loss_pct", [])[:6]
    reward_options = local_values.get("reward_risk", [])[:6]
    for stop in stop_options:
        for reward in reward_options:
            add({"stop_loss_pct": stop, "reward_risk": reward})
            if len(variants) >= limit:
                break
        if len(variants) >= limit:
            break

    return variants[:limit]


def generate_local_execution_refinements(
    seed_settings: BacktestSettings,
    ceiling_settings: BacktestSettings,
    *,
    maximum: int = 24,
    stage: str = "fine",
) -> list[BacktestSettings]:
    """Refine risk and position size without exceeding the user's ceilings."""
    seed_settings.validate()
    ceiling_settings.validate()
    limit = max(1, min(64, int(maximum)))
    risk_steps = (2.5, 1.0, 0.5, 0.25) if stage != "final" else (0.5, 0.25, 0.10)
    position_steps = (25.0, 10.0, 5.0, 2.0) if stage != "final" else (5.0, 2.0, 1.0)
    risk_values = [float(seed_settings.risk_per_trade_pct)] + [
        float(value)
        for value in _neighbor_values(
            float(seed_settings.risk_per_trade_pct), risk_steps,
            minimum=0.05,
            maximum=float(ceiling_settings.risk_per_trade_pct),
        )
    ]
    position_values = [float(seed_settings.max_position_pct)] + [
        float(value)
        for value in _neighbor_values(
            float(seed_settings.max_position_pct), position_steps,
            minimum=1.0,
            maximum=float(ceiling_settings.max_position_pct),
        )
    ]
    candidates: list[BacktestSettings] = []
    seen: set[tuple[float, float]] = set()
    for risk in risk_values:
        for position in position_values:
            signature = (round(risk, 4), round(position, 4))
            if signature in seen or (
                math.isclose(risk, seed_settings.risk_per_trade_pct)
                and math.isclose(position, seed_settings.max_position_pct)
            ):
                continue
            seen.add(signature)
            candidates.append(replace(seed_settings, risk_per_trade_pct=risk, max_position_pct=position))
            if len(candidates) >= limit:
                return candidates
    return candidates


'''
engine = engine[:start] + new_generators + engine[end:]

# ---------------------------------------------------------------------------
# Historical-P/L mode: coarse rules -> fine rules -> sizing -> final local pass.
# ---------------------------------------------------------------------------
historical_sort = '''        rule_candidates.sort(
            key=lambda item: _historical_metric_key(item["metrics"], optimizer.maximum_drawdown_pct),
            reverse=True,
        )
        finalists = rule_candidates[:min(len(rule_candidates), optimizer.finalists_per_strategy)]
'''
historical_sort_replacement = '''        rule_candidates.sort(
            key=lambda item: _historical_metric_key(item["metrics"], optimizer.maximum_drawdown_pct),
            reverse=True,
        )

        adaptive_rule_tests = 0
        seen_rule_signatures = {
            json.dumps(item["rules"], sort_keys=True, separators=(",", ":")) for item in rule_candidates
        }
        refinement_seed_count = min(6, len(rule_candidates))
        refinement_budget = min(120, max(24, optimizer.max_variants_per_strategy // 2))
        per_seed_budget = max(4, refinement_budget // max(1, refinement_seed_count))
        for seed in rule_candidates[:refinement_seed_count]:
            for refined_rules in generate_local_strategy_refinements(
                seed["rules"], seed["settings"], maximum=per_seed_budget, stage="fine"
            ):
                signature = json.dumps(refined_rules, sort_keys=True, separators=(",", ":"))
                if signature in seen_rule_signatures or adaptive_rule_tests >= refinement_budget:
                    continue
                seen_rule_signatures.add(signature)
                candidate_settings = replace(
                    settings,
                    default_stop_pct=float(refined_rules.get("stop_loss_pct") or settings.default_stop_pct),
                    default_reward_risk=float(refined_rules.get("reward_risk") or settings.default_reward_risk),
                )
                metrics = evaluate(refined_rules, candidate_settings)["metrics"]
                adaptive_rule_tests += 1
                rule_candidates.append({
                    "variant_index": len(variants) + adaptive_rule_tests,
                    "execution_index": 0,
                    "rules": refined_rules,
                    "settings": candidate_settings,
                    "metrics": metrics,
                })
                notify(f"{name}: adaptive rule refinement {adaptive_rule_tests} of {refinement_budget}")
        rule_candidates.sort(
            key=lambda item: _historical_metric_key(item["metrics"], optimizer.maximum_drawdown_pct),
            reverse=True,
        )
        finalists = rule_candidates[:min(len(rule_candidates), optimizer.finalists_per_strategy)]
'''
if historical_sort not in engine:
    raise SystemExit("Could not find historical rule-sort block")
engine = engine.replace(historical_sort, historical_sort_replacement, 1)

historical_best = '''        sized_candidates.sort(
            key=lambda item: _historical_metric_key(item["metrics"], optimizer.maximum_drawdown_pct),
            reverse=True,
        )
        best = sized_candidates[0]
'''
historical_best_replacement = '''        sized_candidates.sort(
            key=lambda item: _historical_metric_key(item["metrics"], optimizer.maximum_drawdown_pct),
            reverse=True,
        )

        adaptive_final_rule_tests = 0
        adaptive_final_execution_tests = 0
        local_seed = sized_candidates[0]
        final_rule_budget = min(64, max(16, optimizer.max_variants_per_strategy // 6))
        seen_final_rules = {
            json.dumps(item["rules"], sort_keys=True, separators=(",", ":")) for item in sized_candidates
        }
        for refined_rules in generate_local_strategy_refinements(
            local_seed["rules"], local_seed["settings"], maximum=final_rule_budget, stage="final"
        ):
            signature = json.dumps(refined_rules, sort_keys=True, separators=(",", ":"))
            if signature in seen_final_rules:
                continue
            seen_final_rules.add(signature)
            candidate_settings = replace(
                local_seed["settings"],
                default_stop_pct=float(refined_rules.get("stop_loss_pct") or local_seed["settings"].default_stop_pct),
                default_reward_risk=float(refined_rules.get("reward_risk") or local_seed["settings"].default_reward_risk),
            )
            metrics = evaluate(refined_rules, candidate_settings)["metrics"]
            adaptive_final_rule_tests += 1
            sized_candidates.append({
                **local_seed,
                "variant_index": len(variants) + adaptive_rule_tests + adaptive_final_rule_tests,
                "rules": refined_rules,
                "settings": candidate_settings,
                "metrics": metrics,
            })
            notify(f"{name}: final rule refinement {adaptive_final_rule_tests}")

        sized_candidates.sort(
            key=lambda item: _historical_metric_key(item["metrics"], optimizer.maximum_drawdown_pct),
            reverse=True,
        )
        local_seed = sized_candidates[0]
        final_execution_budget = min(32, max(8, optimizer.max_execution_variants_per_finalist // 2))
        for execution in generate_local_execution_refinements(
            local_seed["settings"], settings, maximum=final_execution_budget, stage="final"
        ):
            candidate_settings = replace(
                execution,
                default_stop_pct=float(local_seed["rules"].get("stop_loss_pct") or execution.default_stop_pct),
                default_reward_risk=float(local_seed["rules"].get("reward_risk") or execution.default_reward_risk),
            )
            metrics = evaluate(local_seed["rules"], candidate_settings)["metrics"]
            adaptive_final_execution_tests += 1
            sized_candidates.append({
                **local_seed,
                "execution_index": len(execution_variants) + adaptive_final_execution_tests,
                "settings": candidate_settings,
                "metrics": metrics,
            })
            notify(f"{name}: final sizing refinement {adaptive_final_execution_tests}")

        sized_candidates.sort(
            key=lambda item: _historical_metric_key(item["metrics"], optimizer.maximum_drawdown_pct),
            reverse=True,
        )
        best = sized_candidates[0]
'''
if historical_best not in engine:
    raise SystemExit("Could not find historical best-candidate block")
engine = engine.replace(historical_best, historical_best_replacement, 1)

historical_counts = '''        settings_tested = len(variants) + len(finalists) * len(execution_variants)
        ranked.append({
'''
historical_counts_replacement = '''        settings_tested = (
            len(variants)
            + adaptive_rule_tests
            + len(finalists) * len(execution_variants)
            + adaptive_final_rule_tests
            + adaptive_final_execution_tests
        )
        adaptive_refinement_tests = adaptive_rule_tests + adaptive_final_rule_tests + adaptive_final_execution_tests
        ranked.append({
'''
if historical_counts not in engine:
    raise SystemExit("Could not find historical count block")
engine = engine.replace(historical_counts, historical_counts_replacement, 1)
engine = engine.replace(
    '''            "rule_variants_tested": len(variants),
            "execution_variants_tested": len(finalists) * len(execution_variants),
            "finalists_tested": len(finalists),
''',
    '''            "rule_variants_tested": len(variants) + adaptive_rule_tests + adaptive_final_rule_tests,
            "execution_variants_tested": len(finalists) * len(execution_variants) + adaptive_final_execution_tests,
            "adaptive_refinement_tests": adaptive_refinement_tests,
            "finalists_tested": len(finalists),
''',
    1,
)
engine = engine.replace(
    '''        "execution_variants_tested": sum(item["execution_variants_tested"] for item in ranked),
        "training_sessions": sessions,
''',
    '''        "execution_variants_tested": sum(item["execution_variants_tested"] for item in ranked),
        "adaptive_refinement_tests": sum(item.get("adaptive_refinement_tests", 0) for item in ranked),
        "training_sessions": sessions,
''',
    1,
)

# ---------------------------------------------------------------------------
# Validated mode: do every refinement on training data only, then validate the
# already-selected finalists. The untouched holdout remains untouched.
# ---------------------------------------------------------------------------
validated_order = '''        ordered = sorted(trained, key=lambda item: (item["training_score"], -item["variant_index"]), reverse=True)
        finalist_count = min(len(ordered), optimizer.finalists_per_strategy)
'''
validated_order_replacement = '''        ordered = sorted(trained, key=lambda item: (item["training_score"], -item["variant_index"]), reverse=True)

        adaptive_rule_tests = 0
        seen_rule_signatures = {
            json.dumps(item["rules"], sort_keys=True, separators=(",", ":")) for item in trained
        }
        refinement_seed_count = min(6, len(ordered))
        refinement_budget = min(120, max(24, optimizer.max_variants_per_strategy // 2))
        per_seed_budget = max(4, refinement_budget // max(1, refinement_seed_count))
        for seed in ordered[:refinement_seed_count]:
            for refined_rules in generate_local_strategy_refinements(
                seed["rules"], seed["settings"], maximum=per_seed_budget, stage="fine"
            ):
                signature = json.dumps(refined_rules, sort_keys=True, separators=(",", ":"))
                if signature in seen_rule_signatures or adaptive_rule_tests >= refinement_budget:
                    continue
                seen_rule_signatures.add(signature)
                candidate_settings = replace(
                    settings,
                    default_stop_pct=float(refined_rules.get("stop_loss_pct") or settings.default_stop_pct),
                    default_reward_risk=float(refined_rules.get("reward_risk") or settings.default_reward_risk),
                )
                candidate_strategy = {**source_strategy, "machine_rules": refined_rules}
                metrics = evaluate(candidate_strategy, "training", candidate_settings)["metrics"]
                adaptive_rule_tests += 1
                trained.append({
                    "variant_index": len(variants) + adaptive_rule_tests,
                    "execution_index": 0,
                    "rules": refined_rules,
                    "settings": candidate_settings,
                    "training_metrics": metrics,
                    "training_score": _optimization_score(
                        metrics,
                        candidate_settings,
                        optimizer.minimum_training_trades,
                        maximum_drawdown_pct=optimizer.maximum_drawdown_pct,
                    ),
                })
                notify(f"Refining {name}: rule candidate {adaptive_rule_tests} of {refinement_budget}")
        ordered = sorted(trained, key=lambda item: (item["training_score"], -item["variant_index"]), reverse=True)
        finalist_count = min(len(ordered), optimizer.finalists_per_strategy)
'''
if validated_order not in engine:
    raise SystemExit("Could not find validated ordered block")
engine = engine.replace(validated_order, validated_order_replacement, 1)

validated_sized = '''        sized_candidates.sort(
            key=lambda item: (item["training_score"], -item["variant_index"], -item["execution_index"]),
            reverse=True,
        )
        finalists = sized_candidates[:min(len(sized_candidates), optimizer.finalists_per_strategy)]
'''
validated_sized_replacement = '''        sized_candidates.sort(
            key=lambda item: (item["training_score"], -item["variant_index"], -item["execution_index"]),
            reverse=True,
        )

        adaptive_final_rule_tests = 0
        adaptive_final_execution_tests = 0
        local_seed = sized_candidates[0]
        final_rule_budget = min(64, max(16, optimizer.max_variants_per_strategy // 6))
        seen_final_rules = {
            json.dumps(item["rules"], sort_keys=True, separators=(",", ":")) for item in sized_candidates
        }
        for refined_rules in generate_local_strategy_refinements(
            local_seed["rules"], local_seed["settings"], maximum=final_rule_budget, stage="final"
        ):
            signature = json.dumps(refined_rules, sort_keys=True, separators=(",", ":"))
            if signature in seen_final_rules:
                continue
            seen_final_rules.add(signature)
            candidate_settings = replace(
                local_seed["settings"],
                default_stop_pct=float(refined_rules.get("stop_loss_pct") or local_seed["settings"].default_stop_pct),
                default_reward_risk=float(refined_rules.get("reward_risk") or local_seed["settings"].default_reward_risk),
            )
            candidate_strategy = {**source_strategy, "machine_rules": refined_rules}
            metrics = evaluate(candidate_strategy, "training", candidate_settings)["metrics"]
            adaptive_final_rule_tests += 1
            sized_candidates.append({
                **local_seed,
                "variant_index": len(variants) + adaptive_rule_tests + adaptive_final_rule_tests,
                "rules": refined_rules,
                "settings": candidate_settings,
                "training_metrics": metrics,
                "training_score": _optimization_score(
                    metrics,
                    candidate_settings,
                    optimizer.minimum_training_trades,
                    maximum_drawdown_pct=optimizer.maximum_drawdown_pct,
                ),
            })
            notify(f"Final rule refinement for {name}: {adaptive_final_rule_tests}")

        sized_candidates.sort(
            key=lambda item: (item["training_score"], -item["variant_index"], -item["execution_index"]),
            reverse=True,
        )
        local_seed = sized_candidates[0]
        final_execution_budget = min(32, max(8, optimizer.max_execution_variants_per_finalist // 2))
        for execution in generate_local_execution_refinements(
            local_seed["settings"], settings, maximum=final_execution_budget, stage="final"
        ):
            candidate_settings = replace(
                execution,
                default_stop_pct=float(local_seed["rules"].get("stop_loss_pct") or execution.default_stop_pct),
                default_reward_risk=float(local_seed["rules"].get("reward_risk") or execution.default_reward_risk),
            )
            candidate_strategy = {**source_strategy, "machine_rules": local_seed["rules"]}
            metrics = evaluate(candidate_strategy, "training", candidate_settings)["metrics"]
            adaptive_final_execution_tests += 1
            sized_candidates.append({
                **local_seed,
                "execution_index": len(execution_variants) + adaptive_final_execution_tests,
                "settings": candidate_settings,
                "training_metrics": metrics,
                "training_score": _optimization_score(
                    metrics,
                    candidate_settings,
                    optimizer.minimum_training_trades,
                    maximum_drawdown_pct=optimizer.maximum_drawdown_pct,
                ),
            })
            notify(f"Final sizing refinement for {name}: {adaptive_final_execution_tests}")

        sized_candidates.sort(
            key=lambda item: (item["training_score"], -item["variant_index"], -item["execution_index"]),
            reverse=True,
        )
        finalists = sized_candidates[:min(len(sized_candidates), optimizer.finalists_per_strategy)]
'''
if validated_sized not in engine:
    raise SystemExit("Could not find validated sized block")
engine = engine.replace(validated_sized, validated_sized_replacement, 1)

validated_counts = '''        settings_tested = len(variants) + len(rule_finalists) * (len(execution_variants) - 1)
        ranked.append(
'''
validated_counts_replacement = '''        settings_tested = (
            len(variants)
            + adaptive_rule_tests
            + len(rule_finalists) * (len(execution_variants) - 1)
            + adaptive_final_rule_tests
            + adaptive_final_execution_tests
        )
        adaptive_refinement_tests = adaptive_rule_tests + adaptive_final_rule_tests + adaptive_final_execution_tests
        ranked.append(
'''
if validated_counts not in engine:
    raise SystemExit("Could not find validated count block")
engine = engine.replace(validated_counts, validated_counts_replacement, 1)
engine = engine.replace(
    '''                "rule_variants_tested": len(variants),
                "execution_variants_tested": settings_tested - len(variants),
                "finalists_tested": len(validated),
''',
    '''                "rule_variants_tested": len(variants) + adaptive_rule_tests + adaptive_final_rule_tests,
                "execution_variants_tested": len(rule_finalists) * (len(execution_variants) - 1) + adaptive_final_execution_tests,
                "adaptive_refinement_tests": adaptive_refinement_tests,
                "finalists_tested": len(validated),
''',
    1,
)
engine = engine.replace(
    '''        "execution_variants_tested": sum(item["execution_variants_tested"] for item in ranked),
        "training_sessions": training_sessions,
''',
    '''        "execution_variants_tested": sum(item["execution_variants_tested"] for item in ranked),
        "adaptive_refinement_tests": sum(item.get("adaptive_refinement_tests", 0) for item in ranked),
        "training_sessions": training_sessions,
''',
    1,
)

# Historical timeframe aggregation should preserve the adaptive count too.
engine = engine.replace(
    '''        "execution_variants_tested": sum(report["execution_variants_tested"] for _, report in by_interval),
        "rankings": candidates,
''',
    '''        "execution_variants_tested": sum(report["execution_variants_tested"] for _, report in by_interval),
        "adaptive_refinement_tests": sum(report.get("adaptive_refinement_tests", 0) for _, report in by_interval),
        "rankings": candidates,
''',
    1,
)

engine_path.write_text(engine, encoding="utf-8")

# ---------------------------------------------------------------------------
# UI: explain that the listed depth is the broad search budget and adaptive
# refinement runs automatically afterward. Also surface how many adaptive tests ran.
# ---------------------------------------------------------------------------
app_path = Path("youtube_strategy_app.py")
app = app_path.read_text(encoding="utf-8")
app = app.replace(
    'help="The combination limit applies to each saved strategy. Comprehensive and Exhaustive search stop/target interactions and more rule thresholds.",',
    'help="This is the broad-search budget per saved strategy. The optimizer then automatically zooms in around the strongest candidates with finer parameter steps.",',
    1,
)
old_note = '''                f'{optimization_report.get("variants_tested", 0)} settings combinations tested',
'''
new_note = '''                (
                    f'{optimization_report.get("variants_tested", 0)} settings combinations tested'
                    + (
                        f' · {optimization_report.get("adaptive_refinement_tests", 0)} adaptive refinements'
                        if optimization_report.get("adaptive_refinement_tests") else ""
                    )
                ),
'''
if old_note not in app:
    raise SystemExit("Could not find optimizer combinations metric note")
app = app.replace(old_note, new_note, 1)
app_path.write_text(app, encoding="utf-8")
