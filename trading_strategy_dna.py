"""Strategy DNA and cross-source research synthesis for Trading Intelligence Lab.

This module never decides that an educational trading idea is profitable. It converts
existing extracted strategy fields into reusable concept tags, measures independent
source agreement, clusters related setups, and builds research-only candidate blueprints.
Historical backtesting and out-of-sample validation remain the authority on performance.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median
import re
from typing import Any

from youtube_strategy_engine import normalize_machine_rules, safe_float


DNA_SCHEMA_VERSION = 1
DNA_DIMENSIONS: tuple[str, ...] = (
    "universe",
    "catalyst",
    "momentum",
    "structure",
    "context",
    "risk",
    "exit",
    "execution",
    "market_regime",
)
DNA_LABELS = {
    "universe": "Universe",
    "catalyst": "Catalyst",
    "momentum": "Momentum",
    "structure": "Structure",
    "context": "Context",
    "risk": "Risk",
    "exit": "Exit",
    "execution": "Execution",
    "market_regime": "Market regime",
}


def _unique(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _strategy_text(strategy: dict[str, Any]) -> str:
    pieces: list[str] = []
    for field in (
        "name",
        "category",
        "summary",
        "indicators",
        "entry_conditions",
        "exit_conditions",
        "risk_rules",
        "avoid_conditions",
        "market_context",
        "stock_selection",
        "unresolved_rules",
    ):
        value = strategy.get(field)
        if isinstance(value, list):
            pieces.extend(str(item) for item in value)
        elif value:
            pieces.append(str(value))
    return " ".join(pieces).casefold()


def _contains(text: str, *phrases: str) -> bool:
    return any(phrase.casefold() in text for phrase in phrases)


def _add(dna: dict[str, list[str]], dimension: str, concept: str) -> None:
    if concept and concept not in dna[dimension]:
        dna[dimension].append(concept)


def normalize_strategy_dna(raw: Any) -> dict[str, list[str]]:
    dna = {dimension: [] for dimension in DNA_DIMENSIONS}
    if not isinstance(raw, dict):
        return dna
    for dimension in DNA_DIMENSIONS:
        value = raw.get(dimension)
        if isinstance(value, list):
            dna[dimension] = _unique(value)[:40]
        elif isinstance(value, str) and value.strip():
            dna[dimension] = [value.strip()]
    return dna


def infer_strategy_dna(strategy: dict[str, Any]) -> dict[str, list[str]]:
    """Build a reusable DNA fingerprint from extracted text plus explicit machine rules.

    Existing saved DNA is preserved, then deterministic concept inference fills obvious gaps.
    No threshold is invented and no profitability conclusion is made.
    """
    dna = normalize_strategy_dna(strategy.get("strategy_dna"))
    text = _strategy_text(strategy)
    rules = normalize_machine_rules(strategy.get("machine_rules"))

    # Universe / stock selection.
    if _contains(text, "small cap", "small-cap", "microcap", "micro-cap"):
        _add(dna, "universe", "Small-cap stocks")
    if _contains(text, "large cap", "large-cap"):
        _add(dna, "universe", "Large-cap stocks")
    if _contains(text, "low float", "low-float"):
        _add(dna, "universe", "Low-float stocks")
    if _contains(text, "high float", "high-float"):
        _add(dna, "universe", "Higher-float stocks")
    if rules.get("min_price") is not None or rules.get("max_price") is not None:
        _add(dna, "universe", "Price-filtered universe")
    if rules.get("min_dollar_volume") is not None:
        _add(dna, "universe", "Minimum dollar liquidity")
    if rules.get("max_spread_pct") is not None:
        _add(dna, "universe", "Tight-spread liquidity")

    # Catalyst.
    if rules.get("catalyst_required") is True or _contains(
        text, "news catalyst", "catalyst required", "fresh news", "breaking news"
    ):
        _add(dna, "catalyst", "News catalyst")
    catalyst_terms = (
        (("earnings", "earnings report"), "Earnings"),
        (("fda", "clinical trial", "drug approval"), "Biotech / FDA"),
        (("merger", "acquisition", "buyout"), "Merger / acquisition"),
        (("contract award", "new contract", "government contract"), "Contract award"),
        (("analyst upgrade", "price target"), "Analyst action"),
        (("offering", "dilution", "warrant"), "Financing / dilution"),
        (("guidance",), "Guidance"),
        (("partnership", "collaboration"), "Partnership"),
    )
    for phrases, label in catalyst_terms:
        if _contains(text, *phrases):
            _add(dna, "catalyst", label)

    # Momentum.
    if rules.get("min_relative_volume") is not None or _contains(text, "relative volume", "rvol"):
        _add(dna, "momentum", "High relative volume")
    if rules.get("min_day_change_pct") is not None or _contains(text, "percent gainer", "percentage move"):
        _add(dna, "momentum", "Strong percentage move")
    if rules.get("volume_surge_ratio") is not None or _contains(
        text, "volume surge", "volume expansion", "expanding volume"
    ):
        _add(dna, "momentum", "Volume expansion")
    if _contains(text, "gap up", "gapper", "gap-and-go", "gap and go", "premarket gap"):
        _add(dna, "momentum", "Gap momentum")
    if _contains(text, "momentum continuation", "continuation momentum"):
        _add(dna, "momentum", "Momentum continuation")

    # Price / chart structure.
    if rules.get("above_vwap") is True:
        _add(dna, "structure", "Above VWAP")
    if rules.get("vwap_reclaim") is True or _contains(text, "vwap reclaim", "reclaim vwap"):
        _add(dna, "structure", "VWAP reclaim")
    if rules.get("breakout_lookback_bars") is not None or _contains(
        text, "breakout", "break out", "resistance break"
    ):
        _add(dna, "structure", "Breakout")
    if rules.get("opening_range_minutes") is not None or _contains(
        text, "opening range breakout", "orb"
    ):
        _add(dna, "structure", "Opening-range breakout")
    if _contains(text, "pullback", "first pullback"):
        _add(dna, "structure", "Pullback")
    if _contains(text, "bull flag", "bear flag", "flag pattern"):
        _add(dna, "structure", "Flag continuation")
    if _contains(text, "high of day", "hod break", "day high"):
        _add(dna, "structure", "High-of-day break")
    if _contains(text, "support", "resistance"):
        _add(dna, "structure", "Support / resistance")
    if _contains(text, "higher high", "higher low"):
        _add(dna, "structure", "Higher-high / higher-low trend")

    # Time/session context.
    if rules.get("session_start") is not None or rules.get("session_end") is not None:
        _add(dna, "context", "Time-window constrained")
    if _contains(text, "premarket", "pre-market"):
        _add(dna, "context", "Premarket")
    if _contains(text, "after hours", "after-hours", "postmarket", "post-market"):
        _add(dna, "context", "After-hours")
    if _contains(text, "market open", "opening bell", "first hour", "morning session"):
        _add(dna, "context", "Market open / morning")
    if _contains(text, "midday", "lunch hour"):
        _add(dna, "context", "Midday")

    # Risk.
    if rules.get("stop_loss_pct") is not None:
        _add(dna, "risk", "Percentage stop")
    if _contains(text, "below pullback low", "below the pullback low", "structure stop", "technical stop"):
        _add(dna, "risk", "Structure-based stop")
    if _contains(text, "stop below vwap", "vwap stop"):
        _add(dna, "risk", "VWAP-based stop")
    if _contains(text, "position size", "position sizing", "risk per trade", "fixed risk"):
        _add(dna, "risk", "Risk-based position sizing")

    # Exit / trade management.
    if rules.get("reward_risk") is not None:
        _add(dna, "exit", "R-multiple target")
    if rules.get("max_hold_minutes") is not None:
        _add(dna, "exit", "Time stop")
    if _contains(text, "scale out", "scaling out", "partial profit", "take partial"):
        _add(dna, "exit", "Scale-out")
    if _contains(text, "trailing stop", "trail the stop", "trail stop"):
        _add(dna, "exit", "Trailing stop")
    if _contains(text, "momentum fades", "momentum fade", "momentum failure"):
        _add(dna, "exit", "Momentum-failure exit")

    # Execution.
    if _contains(text, "confirmation", "wait for confirmation"):
        _add(dna, "execution", "Confirmation entry")
    if _contains(text, "break of pullback high", "breaks the pullback high"):
        _add(dna, "execution", "Break-trigger entry")
    if _contains(text, "limit order"):
        _add(dna, "execution", "Limit-order execution")
    if _contains(text, "market order"):
        _add(dna, "execution", "Market-order execution")

    # Market regime.
    if _contains(text, "trending market", "strong trend", "trend day"):
        _add(dna, "market_regime", "Trending market")
    if _contains(text, "high volatility", "volatile market", "high-volatility"):
        _add(dna, "market_regime", "High volatility")
    if _contains(text, "low volatility", "low-volatility"):
        _add(dna, "market_regime", "Low volatility")
    if _contains(text, "range bound", "range-bound", "sideways market"):
        _add(dna, "market_regime", "Range-bound market")

    return {dimension: _unique(dna[dimension]) for dimension in DNA_DIMENSIONS}


def source_identity(strategy: dict[str, Any]) -> str:
    return str(
        strategy.get("source_id")
        or strategy.get("source_url")
        or strategy.get("source_title")
        or strategy.get("id")
        or strategy.get("name")
        or "unknown-source"
    ).strip()


def _validation_score(strategy: dict[str, Any]) -> float | None:
    auto = strategy.get("last_autonomous_research")
    if isinstance(auto, dict):
        value = safe_float(auto.get("global_score"))
        if value is not None:
            return value
    value = safe_float(strategy.get("robustness_score"))
    return value


def build_concept_graph(strategies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate DNA concepts while keeping source agreement and validation distinct."""
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in strategies:
        if not isinstance(raw, dict):
            continue
        strategy = dict(raw)
        dna = infer_strategy_dna(strategy)
        source_id = source_identity(strategy)
        title = str(strategy.get("source_title") or "Unknown source").strip()
        author = str(strategy.get("source_author") or strategy.get("creator") or "").strip()
        strategy_id = str(strategy.get("id") or strategy.get("name") or "").strip()
        strategy_name = str(strategy.get("name") or "Unnamed strategy").strip()
        validated = str(strategy.get("validation_status") or "").lower() == "validated"
        validation_score = _validation_score(strategy)
        evidence_count = len([x for x in strategy.get("evidence") or [] if isinstance(x, dict)])

        for dimension in DNA_DIMENSIONS:
            for concept in dna.get(dimension) or []:
                key = (dimension, concept.casefold())
                bucket = buckets.setdefault(
                    key,
                    {
                        "dimension": dimension,
                        "dimension_label": DNA_LABELS[dimension],
                        "concept": concept,
                        "strategy_ids": set(),
                        "strategy_names": set(),
                        "source_ids": set(),
                        "source_titles": set(),
                        "authors": set(),
                        "validated_strategy_ids": set(),
                        "validated_source_ids": set(),
                        "validation_scores": [],
                        "evidence_count": 0,
                    },
                )
                if strategy_id:
                    bucket["strategy_ids"].add(strategy_id)
                bucket["strategy_names"].add(strategy_name)
                bucket["source_ids"].add(source_id)
                bucket["source_titles"].add(title)
                if author:
                    bucket["authors"].add(author)
                if validated:
                    if strategy_id:
                        bucket["validated_strategy_ids"].add(strategy_id)
                    bucket["validated_source_ids"].add(source_id)
                    if validation_score is not None:
                        bucket["validation_scores"].append(validation_score)
                bucket["evidence_count"] += evidence_count

    records: list[dict[str, Any]] = []
    for bucket in buckets.values():
        source_count = len(bucket["source_ids"])
        validated_source_count = len(bucket["validated_source_ids"])
        scores = bucket["validation_scores"]
        if source_count >= 5:
            support_label = "Broad multi-source support"
        elif source_count >= 3:
            support_label = "Multi-source support"
        elif source_count >= 2:
            support_label = "Corroborated"
        else:
            support_label = "Single source"
        records.append(
            {
                "dimension": bucket["dimension"],
                "dimension_label": bucket["dimension_label"],
                "concept": bucket["concept"],
                "strategy_count": len(bucket["strategy_ids"]),
                "independent_source_count": source_count,
                "source_titles": sorted(bucket["source_titles"]),
                "authors": sorted(bucket["authors"]),
                "strategy_names": sorted(bucket["strategy_names"]),
                "validated_strategy_count": len(bucket["validated_strategy_ids"]),
                "validated_source_count": validated_source_count,
                "mean_validated_score": round(sum(scores) / len(scores), 1) if scores else None,
                "evidence_count": int(bucket["evidence_count"]),
                "support_label": support_label,
            }
        )

    dimension_index = {name: index for index, name in enumerate(DNA_DIMENSIONS)}
    records.sort(
        key=lambda item: (
            -int(item["independent_source_count"]),
            -int(item["validated_source_count"]),
            -int(item["strategy_count"]),
            dimension_index.get(str(item["dimension"]), 99),
            str(item["concept"]),
        )
    )
    return records


def _feature_set(strategy: dict[str, Any]) -> set[str]:
    dna = infer_strategy_dna(strategy)
    dimensions = ("universe", "catalyst", "momentum", "structure", "context", "market_regime")
    return {
        f"{dimension}:{concept.casefold()}"
        for dimension in dimensions
        for concept in dna.get(dimension) or []
    }


def _structure_set(strategy: dict[str, Any]) -> set[str]:
    return {value.casefold() for value in infer_strategy_dna(strategy).get("structure") or []}


def _direction_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    a = str(left.get("direction") or "unclear").lower()
    b = str(right.get("direction") or "unclear").lower()
    return a == b or a in {"both", "unclear"} or b in {"both", "unclear"}


def _strategy_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    a = _feature_set(left)
    b = _feature_set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _rule_consensus(strategies: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    values_by_rule: dict[str, list[Any]] = defaultdict(list)
    for strategy in strategies:
        rules = normalize_machine_rules(strategy.get("machine_rules"))
        for name, value in rules.items():
            if value is not None:
                values_by_rule[name].append(value)

    result: dict[str, dict[str, Any]] = {}
    for name, values in values_by_rule.items():
        unique: list[Any] = []
        for value in values:
            if value not in unique:
                unique.append(value)
        item: dict[str, Any] = {
            "observations": len(values),
            "distinct_values": unique,
            "conflict": len(unique) > 1,
            "consensus_value": unique[0] if len(unique) == 1 else None,
        }
        numeric = [
            float(value)
            for value in values
            if not isinstance(value, bool) and isinstance(value, (int, float))
        ]
        if numeric:
            item["minimum"] = min(numeric)
            item["maximum"] = max(numeric)
            item["median"] = median(numeric)
        result[name] = item
    return result


def _family_summary(strategies: list[dict[str, Any]], family_id: int) -> dict[str, Any]:
    source_ids = {source_identity(item) for item in strategies}
    source_titles = sorted(
        {str(item.get("source_title") or "Unknown source").strip() for item in strategies}
    )
    validated = [
        item
        for item in strategies
        if str(item.get("validation_status") or "").lower() == "validated"
    ]

    concept_sources: dict[tuple[str, str], set[str]] = defaultdict(set)
    concept_labels: dict[tuple[str, str], str] = {}
    for strategy in strategies:
        source_id = source_identity(strategy)
        dna = infer_strategy_dna(strategy)
        for dimension in DNA_DIMENSIONS:
            for concept in dna.get(dimension) or []:
                key = (dimension, concept.casefold())
                concept_sources[key].add(source_id)
                concept_labels[key] = concept

    common = [
        {
            "dimension": key[0],
            "dimension_label": DNA_LABELS[key[0]],
            "concept": concept_labels[key],
            "source_count": len(sources),
        }
        for key, sources in concept_sources.items()
        if len(sources) >= 2
    ]
    common.sort(key=lambda item: (-int(item["source_count"]), DNA_DIMENSIONS.index(item["dimension"]), item["concept"]))

    directions = _unique([str(item.get("direction") or "unclear").lower() for item in strategies])
    structures = [
        item["concept"]
        for item in common
        if item["dimension"] == "structure"
    ]
    categories = _unique([str(item.get("category") or "Uncategorized") for item in strategies])
    label_seed = structures[0] if structures else (categories[0] if categories else "Strategy")
    return {
        "id": f"dna-family-{family_id}",
        "name": f"{label_seed} family",
        "direction": directions[0] if len(directions) == 1 else "mixed",
        "strategy_count": len(strategies),
        "independent_source_count": len(source_ids),
        "source_titles": source_titles,
        "validated_strategy_count": len(validated),
        "validated_source_count": len({source_identity(item) for item in validated}),
        "strategies": strategies,
        "common_concepts": common,
        "rule_consensus": _rule_consensus(strategies),
    }


def build_strategy_families(
    strategies: list[dict[str, Any]],
    *,
    similarity_threshold: float = 0.28,
) -> list[dict[str, Any]]:
    """Greedily cluster related strategies using reusable DNA rather than strategy names."""
    items = [dict(item) for item in strategies if isinstance(item, dict)]
    families: list[list[dict[str, Any]]] = []

    for strategy in items:
        best_index = None
        best_score = 0.0
        strategy_structure = _structure_set(strategy)
        category = re.sub(r"[^a-z0-9]+", " ", str(strategy.get("category") or "").casefold()).strip()
        for index, family in enumerate(families):
            representative = family[0]
            if not _direction_compatible(strategy, representative):
                continue
            family_structure = set().union(*(_structure_set(member) for member in family))
            rep_category = re.sub(
                r"[^a-z0-9]+", " ", str(representative.get("category") or "").casefold()
            ).strip()
            structure_overlap = bool(strategy_structure & family_structure)
            if strategy_structure and family_structure and not structure_overlap:
                continue
            if not structure_overlap and category and rep_category and category != rep_category:
                continue
            score = max(_strategy_similarity(strategy, member) for member in family)
            if score >= similarity_threshold and score > best_score:
                best_index = index
                best_score = score

        if best_index is None:
            families.append([strategy])
        else:
            families[best_index].append(strategy)

    summaries = [_family_summary(group, index + 1) for index, group in enumerate(families)]
    summaries.sort(
        key=lambda item: (
            -int(item["independent_source_count"]),
            -int(item["validated_source_count"]),
            -int(item["strategy_count"]),
            str(item["name"]),
        )
    )
    return summaries


def build_candidate_blueprints(
    families: list[dict[str, Any]],
    *,
    min_sources: int = 2,
) -> list[dict[str, Any]]:
    """Create research-only cross-source hypotheses from strategy families.

    These are blueprints, not executable or validated strategies. Conflicting explicit
    thresholds are surfaced rather than silently averaged into a new author rule.
    """
    candidates: list[dict[str, Any]] = []
    for family in families:
        source_count = int(family.get("independent_source_count") or 0)
        if source_count < max(2, int(min_sources)):
            continue
        common = list(family.get("common_concepts") or [])
        if not common:
            continue
        rule_consensus = dict(family.get("rule_consensus") or {})
        consistent_rules = {
            name: item.get("consensus_value")
            for name, item in rule_consensus.items()
            if not item.get("conflict") and item.get("consensus_value") is not None
        }
        conflicting_rules = [
            name for name, item in rule_consensus.items() if bool(item.get("conflict"))
        ]
        core_by_dimension = {
            dimension: [
                item["concept"]
                for item in common
                if item.get("dimension") == dimension
            ]
            for dimension in DNA_DIMENSIONS
        }
        structure = core_by_dimension.get("structure") or []
        seed = structure[0] if structure else str(family.get("name") or "Cross-source setup").replace(" family", "")
        priority = min(
            100.0,
            source_count * 12.0
            + int(family.get("validated_source_count") or 0) * 15.0
            + len(common) * 2.0,
        )
        candidates.append(
            {
                "id": "candidate-" + str(family.get("id") or len(candidates) + 1),
                "name": f"Cross-source {seed} candidate",
                "family_id": family.get("id"),
                "direction": family.get("direction"),
                "research_priority_score": round(priority, 1),
                "supporting_source_count": source_count,
                "supporting_sources": list(family.get("source_titles") or []),
                "supporting_strategy_count": int(family.get("strategy_count") or 0),
                "validated_source_count": int(family.get("validated_source_count") or 0),
                "core_dna": core_by_dimension,
                "consistent_explicit_rules": consistent_rules,
                "conflicting_explicit_rules": conflicting_rules,
                "rule_consensus": rule_consensus,
                "status": "hypothesis_only",
                "requires_rule_compilation": bool(conflicting_rules) or not bool(consistent_rules),
                "note": (
                    "Independent sources contribute to this candidate, but source agreement is not "
                    "performance evidence. It must be compiled, backtested, and validated on unseen data."
                ),
            }
        )

    candidates.sort(
        key=lambda item: (
            -float(item["research_priority_score"]),
            -int(item["supporting_source_count"]),
            -int(item["validated_source_count"]),
            str(item["name"]),
        )
    )
    return candidates
