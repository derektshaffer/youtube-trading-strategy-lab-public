"""Strategy DNA and cross-source research synthesis for Trading Intelligence Lab.

This module never decides that an educational trading idea is profitable. It converts
existing extracted strategy fields into reusable concept tags, measures independent
source agreement, clusters related setups, and builds research-only candidate blueprints.
Historical backtesting and out-of-sample validation remain the authority on performance.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
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
    if rules.get("previous_day_high_breakout") is True:
        _add(dna, "structure", "Previous-day high breakout")
    if _contains(text, "anchored vwap", "avwap"):
        _add(dna, "structure", "Anchored VWAP")
    if _contains(text, "mean reversion", "mean-reversion", "extreme reversal", "counter-trend reversal"):
        _add(dna, "structure", "Mean-reversion reversal")
    if (
        _contains(text, "avwap pinch", "pinch strategy")
        or (
            _contains(text, "compression", "squeeze between")
            and _contains(text, "avwap", "anchored vwap")
        )
    ):
        _add(dna, "structure", "Compression / pinch")
    if _contains(text, "avwap handoff", "handoff"):
        _add(dna, "structure", "AVWAP handoff")
    if _contains(text, "short squeeze", "short interest ratio", "heavily shorted"):
        _add(dna, "structure", "Short-squeeze reclaim")
    if _contains(text, "ipo day-one", "first trading day of an ipo", "first day of an ipo"):
        _add(dna, "structure", "IPO day-one AVWAP")
    if _contains(text, "day 2", "day two") and _contains(text, "avwap", "anchored vwap"):
        _add(dna, "structure", "Multi-day AVWAP continuation")
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
    if (
        rules.get("fast_ema_period") is not None
        or (_contains(text, "ema", "exponential moving average") and _contains(text, "pullback", "pull back"))
    ):
        _add(dna, "structure", "EMA pullback")
    if (
        rules.get("require_price_above_slow_ema") is True
        or rules.get("require_price_above_trend_ema") is True
        or _contains(text, "above its moving averages", "ema alignment")
    ):
        _add(dna, "structure", "EMA trend alignment")
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
    if rules.get("stop_below_fast_ema") is True or (
        _contains(text, "stop", "stop loss") and _contains(text, "below the 9 ema", "below ema", "below the ema")
    ):
        _add(dna, "risk", "EMA-anchored stop")
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


GENERATED_STRATEGY_SOURCE_TYPES = {
    "cross_source_synthesis",
    "canonical_family",
}


def is_synthetic_strategy(strategy: dict[str, Any]) -> bool:
    return str(strategy.get("source_type") or "").strip().lower() in GENERATED_STRATEGY_SOURCE_TYPES


def is_family_source_strategy(strategy: dict[str, Any]) -> bool:
    """Return True only for original learned ideas that should define strategy families."""
    if not isinstance(strategy, dict) or is_synthetic_strategy(strategy):
        return False
    if strategy.get("optimized_for_symbol") or strategy.get("parent_strategy_id"):
        return False
    if strategy.get("is_master_strategy"):
        return False
    return bool(strategy.get("id") or strategy.get("name"))


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
        if not isinstance(raw, dict) or is_synthetic_strategy(raw):
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
    if a == b:
        return True
    # "Both" is a genuinely different executable blueprint from long-only or short-only.
    # Only an unclear extraction is allowed to attach provisionally to a directional family.
    return a == "unclear" or b == "unclear"


STRONG_STRUCTURE_CONCEPTS = {
    "previous-day high breakout",
    "vwap reclaim",
    "opening-range breakout",
    "ema pullback",
    "flag continuation",
    "high-of-day break",
    "mean-reversion reversal",
    "compression / pinch",
    "avwap handoff",
    "short-squeeze reclaim",
    "ipo day-one avwap",
    "multi-day avwap continuation",
}

# These concepts change the actual mechanism/context enough that sharing a generic trigger
# (for example, "VWAP reclaim") is not sufficient to call two strategies the same blueprint.
EXCLUSIVE_STRUCTURE_CONCEPTS = {
    "previous-day high breakout",
    "mean-reversion reversal",
    "compression / pinch",
    "avwap handoff",
    "short-squeeze reclaim",
    "ipo day-one avwap",
    "multi-day avwap continuation",
}


def _strong_structure_set(strategy: dict[str, Any]) -> set[str]:
    return {
        value
        for value in _structure_set(strategy)
        if value in STRONG_STRUCTURE_CONCEPTS
    }


def _exclusive_structure_set(strategy: dict[str, Any]) -> set[str]:
    return {
        value
        for value in _structure_set(strategy)
        if value in EXCLUSIVE_STRUCTURE_CONCEPTS
    }


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
    concept_members: dict[tuple[str, str], set[str]] = defaultdict(set)
    concept_labels: dict[tuple[str, str], str] = {}
    for index, strategy in enumerate(strategies):
        source_id = source_identity(strategy)
        member_id = str(strategy.get("id") or f"member-{index}")
        dna = infer_strategy_dna(strategy)
        for dimension in DNA_DIMENSIONS:
            for concept in dna.get(dimension) or []:
                key = (dimension, concept.casefold())
                concept_sources[key].add(source_id)
                concept_members[key].add(member_id)
                concept_labels[key] = concept

    common = [
        {
            "dimension": key[0],
            "dimension_label": DNA_LABELS[key[0]],
            "concept": concept_labels[key],
            "source_count": len(sources),
            "member_count": len(concept_members[key]),
        }
        for key, sources in concept_sources.items()
        if len(sources) >= 2
    ]
    common.sort(
        key=lambda item: (
            -int(item["source_count"]),
            -int(item["member_count"]),
            DNA_DIMENSIONS.index(item["dimension"]),
            item["concept"],
        )
    )

    member_threshold = 1 if len(strategies) == 1 else max(2, int(len(strategies) * 0.60 + 0.999))
    core = [
        {
            "dimension": key[0],
            "dimension_label": DNA_LABELS[key[0]],
            "concept": concept_labels[key],
            "member_count": len(members),
            "source_count": len(concept_sources[key]),
        }
        for key, members in concept_members.items()
        if len(members) >= member_threshold
    ]
    core.sort(
        key=lambda item: (
            -int(item["member_count"]),
            -int(item["source_count"]),
            DNA_DIMENSIONS.index(item["dimension"]),
            item["concept"],
        )
    )

    directions = _unique([str(item.get("direction") or "unclear").lower() for item in strategies])
    structures = [item["concept"] for item in core if item["dimension"] == "structure"]
    categories = _unique([str(item.get("category") or "Uncategorized") for item in strategies])
    label_seed = structures[0] if structures else (categories[0] if categories else "Strategy")
    direction_signature = directions[0] if len(directions) == 1 else "mixed"

    # The ID represents the stable blueprint, not the individual source count. New books can
    # join a family without creating a second family record as long as the core mechanism stays the same.
    signature_concepts = [
        f"{item['dimension']}:{str(item['concept']).casefold()}"
        for item in core
        if item["dimension"] in {"catalyst", "momentum", "structure", "context", "market_regime"}
    ]
    if not signature_concepts:
        signature_concepts = [
            re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
            for value in categories[:2]
        ]
    family_material = "|".join([direction_signature, *sorted(signature_concepts)])
    stable_family_id = "dna-family-" + hashlib.sha256(
        family_material.encode("utf-8", errors="ignore")
    ).hexdigest()[:16]
    return {
        "id": stable_family_id,
        "name": f"{label_seed} family",
        "direction": directions[0] if len(directions) == 1 else "mixed",
        "strategy_count": len(strategies),
        "independent_source_count": len(source_ids),
        "source_titles": source_titles,
        "validated_strategy_count": len(validated),
        "validated_source_count": len({source_identity(item) for item in validated}),
        "strategies": strategies,
        "common_concepts": common,
        "core_concepts": core,
        "rule_consensus": _rule_consensus(strategies),
    }


def build_strategy_families(
    strategies: list[dict[str, Any]],
    *,
    similarity_threshold: float = 0.40,
) -> list[dict[str, Any]]:
    """Greedily cluster related strategies using reusable DNA rather than strategy names."""
    items = sorted(
        [
            dict(item)
            for item in strategies
            if is_family_source_strategy(item)
        ],
        key=lambda item: (
            str(item.get("direction") or ""),
            str(item.get("id") or item.get("name") or ""),
        ),
    )
    families: list[list[dict[str, Any]]] = []

    for strategy in items:
        best_index = None
        best_score = 0.0
        strategy_structure = _structure_set(strategy)
        strategy_strong = _strong_structure_set(strategy)
        strategy_exclusive = _exclusive_structure_set(strategy)
        category = re.sub(r"[^a-z0-9]+", " ", str(strategy.get("category") or "").casefold()).strip()
        for index, family in enumerate(families):
            representative = family[0]
            if not _direction_compatible(strategy, representative):
                continue
            family_structure = set().union(*(_structure_set(member) for member in family))
            family_strong = set().union(*(_strong_structure_set(member) for member in family))
            family_exclusive = set().union(*(_exclusive_structure_set(member) for member in family))
            rep_category = re.sub(
                r"[^a-z0-9]+", " ", str(representative.get("category") or "").casefold()
            ).strip()

            # Exclusive mechanism/context tags must match exactly. This prevents a short-squeeze,
            # IPO day-one, multi-day continuation, handoff, pinch, or mean-reversion setup from
            # disappearing into a broader family just because both happen to use a VWAP reclaim.
            if strategy_exclusive != family_exclusive:
                continue

            # Strong mechanism tags are blueprint-defining. A bull-flag/EMA pullback should not
            # merge with an AVWAP pinch or mean-reversion setup just because both mention support.
            if strategy_strong or family_strong:
                if not (strategy_strong & family_strong):
                    continue
            else:
                structure_overlap = strategy_structure & family_structure
                if strategy_structure and family_structure and not structure_overlap:
                    continue
                if len(structure_overlap) < 2 and category and rep_category and category != rep_category:
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
                "contributing_strategy_ids": [
                    str(item.get("id") or "")
                    for item in family.get("strategies") or []
                    if isinstance(item, dict) and item.get("id")
                ],
                "validated_source_count": int(family.get("validated_source_count") or 0),
                "core_dna": core_by_dimension,
                "consistent_explicit_rules": consistent_rules,
                "conflicting_explicit_rules": conflicting_rules,
                "rule_consensus": rule_consensus,
                "status": "hypothesis_only",
                "backtest_supported": str(family.get("direction") or "").lower() in {"long", "both"},
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




def _canonical_family_name(family: dict[str, Any], core_dna: dict[str, list[str]]) -> str:
    structure = {str(value).casefold() for value in core_dna.get("structure") or []}
    momentum = {str(value).casefold() for value in core_dna.get("momentum") or []}

    if "previous-day high breakout" in structure:
        base = "Previous-Day High Continuation Breakout"
    elif "short-squeeze reclaim" in structure:
        base = "AVWAP Short-Squeeze Reclaim"
    elif "ipo day-one avwap" in structure:
        base = "IPO Day-One Anchored VWAP"
    elif "multi-day avwap continuation" in structure:
        base = "Multi-Day AVWAP Continuation"
    elif "compression / pinch" in structure:
        base = "AVWAP Pinch / Compression"
    elif "avwap handoff" in structure:
        base = "Anchored VWAP Handoff"
    elif "mean-reversion reversal" in structure:
        base = "Extreme Mean-Reversion Reversal"
    elif "anchored vwap" in structure and "vwap reclaim" in structure:
        base = "Anchored VWAP Reclaim"
    elif "anchored vwap" in structure and "pullback" in structure:
        base = "Anchored VWAP Pullback"
    elif "opening-range breakout" in structure:
        base = "Opening Range Breakout"
    elif "vwap reclaim" in structure and "pullback" in structure:
        base = "VWAP Reclaim Pullback"
    elif "vwap reclaim" in structure:
        base = "VWAP Reclaim"
    elif "ema pullback" in structure and "breakout" in structure:
        base = "EMA Pullback Breakout"
    elif "ema pullback" in structure:
        base = "EMA Pullback"
    elif "flag continuation" in structure:
        base = "Flag / Pullback Continuation"
    elif "pullback" in structure and "breakout" in structure:
        base = "Pullback Breakout"
    elif "anchored vwap" in structure:
        base = "Anchored VWAP Trend / Structure"
    elif "breakout" in structure and "high relative volume" in momentum:
        base = "High-RVOL Momentum Breakout"
    elif "breakout" in structure:
        base = "Momentum Breakout"
    elif "pullback" in structure:
        base = "Momentum Pullback"
    else:
        base = str(family.get("name") or "Strategy family").replace(" family", "").strip()
        base = base.replace("_", " ").title()

    direction = str(family.get("direction") or "").lower()
    if direction == "short":
        return f"{base} — Short"
    if direction == "both":
        return f"{base} — Long & Short"
    return base


def _canonical_blueprint_from_family(family: dict[str, Any]) -> dict[str, Any]:
    members = [item for item in family.get("strategies") or [] if isinstance(item, dict)]
    core_concepts = list(family.get("core_concepts") or [])
    core_by_dimension = {
        dimension: [
            item["concept"]
            for item in core_concepts
            if item.get("dimension") == dimension
        ]
        for dimension in DNA_DIMENSIONS
    }

    consensus = dict(family.get("rule_consensus") or {})
    consistent_rules = {
        name: details.get("consensus_value")
        for name, details in consensus.items()
        if not details.get("conflict") and details.get("consensus_value") is not None
    }
    conflicting_rules = [
        name for name, details in consensus.items() if bool(details.get("conflict"))
    ]

    return {
        "id": "canonical-" + str(family.get("id") or "family"),
        "name": _canonical_family_name(family, core_by_dimension),
        "family_id": family.get("id"),
        "direction": family.get("direction"),
        "research_priority_score": min(
            100.0,
            45.0
            + int(family.get("independent_source_count") or 0) * 8.0
            + min(25.0, int(family.get("strategy_count") or 0) * 2.0)
            + int(family.get("validated_source_count") or 0) * 8.0,
        ),
        "supporting_source_count": int(family.get("independent_source_count") or 0),
        "supporting_sources": list(family.get("source_titles") or []),
        "supporting_strategy_count": int(family.get("strategy_count") or 0),
        "contributing_strategy_ids": [
            str(item.get("id") or "")
            for item in members
            if item.get("id")
        ],
        "validated_source_count": int(family.get("validated_source_count") or 0),
        "core_dna": core_by_dimension,
        "consistent_explicit_rules": consistent_rules,
        "conflicting_explicit_rules": conflicting_rules,
        "rule_consensus": consensus,
        "status": "ai_managed_family",
        "backtest_supported": str(family.get("direction") or "").lower() in {"long", "both"},
        "requires_rule_compilation": bool(conflicting_rules) or not bool(consistent_rules),
    }


def _family_research_signature(strategy: dict[str, Any]) -> str:
    material = {
        "machine_rules": normalize_machine_rules(strategy.get("machine_rules")),
        "research_rule_overrides": normalize_machine_rules(strategy.get("research_rule_overrides")),
        "candidate_rule_options": strategy.get("candidate_rule_options") or {},
        "ai_candidate_rule_options": strategy.get("ai_candidate_rule_options") or {},
        "strategy_dna": normalize_strategy_dna(strategy.get("strategy_dna")),
        "direction": str(strategy.get("direction") or ""),
    }
    return hashlib.sha256(
        repr(material).encode("utf-8", errors="ignore")
    ).hexdigest()[:24]


def build_canonical_family_strategies(
    strategies: list[dict[str, Any]],
    *,
    existing: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collapse learned source ideas into AI-managed canonical research families.

    Raw source strategies remain untouched for provenance. The canonical record is the one
    meant for optimization, validation, market discovery, and user-facing management.
    """
    families = build_strategy_families(strategies)
    existing_by_family = {
        str(item.get("family_id") or ""): dict(item)
        for item in (existing or strategies or [])
        if isinstance(item, dict)
        and str(item.get("source_type") or "").lower() == "canonical_family"
        and item.get("family_id")
    }

    canonical: list[dict[str, Any]] = []
    for family in families:
        blueprint = _canonical_blueprint_from_family(family)
        compiled = compile_candidate_blueprint(blueprint)

        # Preserve AI/research-assumption rule ranges contributed by source variations.
        # These are not promoted to source-explicit rules; they remain clearly separated
        # optimizer hypotheses on the canonical family.
        member_ai_options: dict[str, list[Any]] = {}
        member_research_seeds: dict[str, list[Any]] = {}
        member_assumptions: list[dict[str, Any]] = []
        for member in family.get("strategies") or []:
            if not isinstance(member, dict):
                continue
            explicit_member = normalize_machine_rules(member.get("machine_rules"))
            member_overrides = normalize_machine_rules(member.get("research_rule_overrides"))
            for rule_name, value in member_overrides.items():
                if value is None or explicit_member.get(rule_name) is not None:
                    continue
                values = member_research_seeds.setdefault(str(rule_name), [])
                if value not in values:
                    values.append(value)
            raw_options = member.get("ai_candidate_rule_options")
            if isinstance(raw_options, dict):
                for rule_name, raw_values in raw_options.items():
                    if not isinstance(raw_values, list):
                        continue
                    values = member_ai_options.setdefault(str(rule_name), [])
                    for raw_value in raw_values:
                        parsed = _coerce_rule_option(str(rule_name), raw_value)
                        if parsed is not None and parsed not in values:
                            values.append(parsed)
            for assumption in member.get("compiler_assumptions") or []:
                if isinstance(assumption, dict):
                    record = dict(assumption)
                    signature = (
                        str(record.get("target_rule") or ""),
                        str(record.get("accepted_by") or ""),
                        repr(record.get("value")),
                    )
                    if not any(
                        (
                            str(existing.get("target_rule") or ""),
                            str(existing.get("accepted_by") or ""),
                            repr(existing.get("value")),
                        ) == signature
                        for existing in member_assumptions
                    ):
                        member_assumptions.append(record)

        compiled_rules = normalize_machine_rules(compiled.get("machine_rules"))
        compiled_overrides = {
            key: value
            for key, value in normalize_machine_rules(compiled.get("research_rule_overrides")).items()
            if value is not None
        }
        compiled_ai_options = {
            str(name): list(values)
            for name, values in (compiled.get("ai_candidate_rule_options") or {}).items()
            if isinstance(values, list)
        }
        for rule_name, seeds in member_research_seeds.items():
            combined = list(compiled_ai_options.get(rule_name) or [])
            for value in [*seeds, *(member_ai_options.get(rule_name) or [])]:
                if value is not None and value not in combined:
                    combined.append(value)
            if combined:
                compiled_ai_options[rule_name] = combined
            if compiled_rules.get(rule_name) is None and compiled_overrides.get(rule_name) is None:
                seed = _representative_exact_option(seeds or combined)
                if seed is not None:
                    compiled_overrides[rule_name] = seed
        for rule_name, values in member_ai_options.items():
            combined = list(compiled_ai_options.get(rule_name) or [])
            for value in values:
                if value is not None and value not in combined:
                    combined.append(value)
            if combined:
                compiled_ai_options[rule_name] = combined

        if compiled_overrides:
            compiled["research_rule_overrides"] = normalize_machine_rules(compiled_overrides)
        if compiled_ai_options:
            compiled["ai_candidate_rule_options"] = compiled_ai_options
        if member_assumptions:
            compiled["compiler_assumptions"] = [
                *(compiled.get("compiler_assumptions") or []),
                *member_assumptions,
            ][-150:]

        compiled["id"] = "canonical-" + str(family.get("id") or "")
        compiled["name"] = str(blueprint.get("name") or family.get("name") or "Strategy family")
        compiled["category"] = "AI-managed strategy family"
        compiled["source_type"] = "canonical_family"
        compiled["source_id"] = "canonical:" + str(family.get("id") or "")
        compiled["source_title"] = (
            f"AI-consolidated family · {int(family.get('strategy_count') or 0)} source variation(s)"
        )
        compiled["source_author"] = ""
        compiled["family_id"] = family.get("id")
        compiled["raw_strategy_count"] = int(family.get("strategy_count") or 0)
        compiled["supporting_source_count"] = int(family.get("independent_source_count") or 0)
        compiled["supporting_sources"] = list(family.get("source_titles") or [])
        compiled["source_strategy_ids"] = list(blueprint.get("contributing_strategy_ids") or [])
        compiled["ai_managed"] = True
        compiled["family_core_concepts"] = list(family.get("core_concepts") or [])

        # If a rule appears in only some family members, "not required" is itself a legitimate
        # family variant. Let the optimizer test that option rather than creating another strategy.
        options = {
            str(name): list(values)
            for name, values in (compiled.get("candidate_rule_options") or {}).items()
            if isinstance(values, list)
        }
        member_count = max(1, int(family.get("strategy_count") or 0))
        for rule_name, details in (family.get("rule_consensus") or {}).items():
            observations = int((details or {}).get("observations") or 0)
            if observations <= 0 or observations >= member_count:
                continue
            values = options.setdefault(str(rule_name), [])
            current = normalize_machine_rules(compiled.get("machine_rules")).get(str(rule_name))
            if current is not None and current not in values:
                values.insert(0, current)
            if None not in values:
                values.append(None)
        compiled["candidate_rule_options"] = options

        signature = _family_research_signature(compiled)
        compiled["canonical_research_signature"] = signature
        previous = existing_by_family.get(str(family.get("id") or ""))
        if previous and str(previous.get("canonical_research_signature") or "") == signature:
            for field in (
                "research_rule_overrides",
                "ai_candidate_rule_options",
                "compiler_assumptions",
                "autopilot_preparation",
                "research_readiness",
                "validation_status",
                "optimization_status",
                "last_autonomous_research",
                "validated_rules",
                "validated_backtest_settings",
                "validated_at",
                "last_backtest",
                "backtest_run_history",
            ):
                if field in previous:
                    compiled[field] = previous[field]
        else:
            compiled["validation_status"] = "unvalidated"
            compiled["optimization_status"] = "not_run"
            compiled.pop("validated_rules", None)
            compiled.pop("validated_backtest_settings", None)
            compiled.pop("validated_at", None)
            if previous:
                compiled["previous_family_validation_superseded"] = True

        canonical.append(compiled)

    canonical.sort(
        key=lambda item: (
            -int(item.get("supporting_source_count") or 0),
            -int(item.get("raw_strategy_count") or 0),
            str(item.get("name") or ""),
        )
    )
    return canonical, families


def _coerce_rule_option(rule_name: str, value: Any) -> Any:
    try:
        return normalize_machine_rules({rule_name: value}).get(rule_name)
    except Exception:
        return None


def _representative_exact_option(values: list[Any]) -> Any:
    """Choose a deterministic starting seed that is itself present in the source options."""
    clean = []
    for value in values:
        if value not in clean:
            clean.append(value)
    if not clean:
        return None
    numeric = [
        value
        for value in clean
        if not isinstance(value, bool) and isinstance(value, (int, float))
    ]
    if len(numeric) == len(clean):
        ordered = sorted(numeric, key=float)
        return ordered[(len(ordered) - 1) // 2]
    bools = [value for value in clean if isinstance(value, bool)]
    if len(bools) == len(clean):
        true_count = sum(1 for value in bools if value)
        false_count = len(bools) - true_count
        return True if true_count >= false_count else False
    return sorted(clean, key=lambda value: str(value).casefold())[0]


def _semantic_rules_from_shared_dna(core_dna: dict[str, Any]) -> dict[str, Any]:
    """Translate only shared DNA concepts with unambiguous boolean machine semantics."""
    rules: dict[str, Any] = {}
    structure = {str(value).casefold() for value in core_dna.get("structure") or []}
    catalysts = {str(value).casefold() for value in core_dna.get("catalyst") or []}
    if "above vwap" in structure:
        rules["above_vwap"] = True
    if "vwap reclaim" in structure:
        rules["vwap_reclaim"] = True
    if "news catalyst" in catalysts:
        rules["catalyst_required"] = True
    if "ema pullback" in structure:
        rules["require_fast_ema_pullback"] = True
    return rules


def compile_candidate_blueprint(blueprint: dict[str, Any]) -> dict[str, Any]:
    """Convert a cross-source blueprint into a research-only executable strategy.

    Exact rules agreed on by all contributing observations remain explicit. When sources
    disagree, one *source-supported* value is selected only as an optimizer seed and all
    observed source values are retained in candidate_rule_options for direct testing.
    No conflicting thresholds are averaged into a value that no source actually stated.
    """
    core_dna = normalize_strategy_dna(blueprint.get("core_dna"))
    explicit = normalize_machine_rules(blueprint.get("consistent_explicit_rules"))
    semantic = _semantic_rules_from_shared_dna(core_dna)
    for name, value in semantic.items():
        if explicit.get(name) is None:
            explicit[name] = value

    source_options: dict[str, list[Any]] = {}
    research_overrides: dict[str, Any] = {}
    assumption_log: list[dict[str, Any]] = []
    consensus = blueprint.get("rule_consensus") or {}
    for rule_name in blueprint.get("conflicting_explicit_rules") or []:
        details = consensus.get(rule_name) if isinstance(consensus, dict) else None
        raw_values = details.get("distinct_values") if isinstance(details, dict) else []
        clean_values: list[Any] = []
        for raw in raw_values or []:
            parsed = _coerce_rule_option(str(rule_name), raw)
            if parsed is not None and parsed not in clean_values:
                clean_values.append(parsed)
        if len(clean_values) < 2:
            continue
        source_options[str(rule_name)] = clean_values
        if explicit.get(str(rule_name)) is None:
            seed = _representative_exact_option(clean_values)
            if seed is not None:
                research_overrides[str(rule_name)] = seed
                assumption_log.append(
                    {
                        "target_rule": str(rule_name),
                        "value": seed,
                        "source_requirement": (
                            "Contributing sources explicitly disagree on this threshold."
                        ),
                        "rationale": (
                            "Selected one exact source-supported value as a neutral optimizer starting seed; "
                            "all observed source values are retained and tested separately."
                        ),
                        "confidence": 100.0,
                        "accepted_by": "cross_source_synthesis_seed",
                        "is_research_assumption": True,
                    }
                )

    mapped_concepts = {
        "Above VWAP",
        "VWAP reclaim",
        "News catalyst",
        "EMA pullback",
    }
    untranslated_dna: dict[str, list[str]] = {}
    for dimension in DNA_DIMENSIONS:
        values = [
            concept
            for concept in core_dna.get(dimension) or []
            if concept not in mapped_concepts
        ]
        if values:
            untranslated_dna[dimension] = values

    # Only entry/context DNA belongs in unresolved_rules because that field controls whether
    # autonomous testing is allowed. Risk/exit/execution concepts remain visible in
    # untranslated_dna without falsely making an otherwise testable candidate "unready".
    unresolved_dimensions = (
        "universe",
        "catalyst",
        "momentum",
        "structure",
        "context",
        "market_regime",
    )
    unresolved: list[str] = []
    for dimension in unresolved_dimensions:
        values = untranslated_dna.get(dimension) or []
        if values:
            unresolved.append(
                f"Shared Strategy DNA needs objective translation or direct testing — "
                f"{DNA_LABELS[dimension]}: {', '.join(values)}"
            )

    structure = list(core_dna.get("structure") or [])
    momentum = list(core_dna.get("momentum") or [])
    catalyst = list(core_dna.get("catalyst") or [])
    entry_conditions = [
        f"Shared structure: {value}" for value in structure
    ] + [
        f"Shared momentum condition: {value}" for value in momentum
    ] + [
        f"Shared catalyst context: {value}" for value in catalyst
    ]

    supporting_sources = _unique(list(blueprint.get("supporting_sources") or []))
    source_count = int(blueprint.get("supporting_source_count") or len(supporting_sources))
    priority = safe_float(blueprint.get("research_priority_score"), 0.0) or 0.0
    direction = str(blueprint.get("direction") or "unclear").lower()
    support_confidence = min(95.0, max(50.0, 50.0 + source_count * 6.0))

    return {
        "id": "synth-" + str(blueprint.get("family_id") or blueprint.get("id") or "candidate"),
        "name": str(blueprint.get("name") or "Cross-source strategy candidate"),
        "category": "Cross-source synthesis",
        "direction": direction,
        "summary": (
            f"Research-only strategy synthesized from {source_count} independent source(s). "
            "Shared concepts and exact source thresholds are preserved; disagreements become optimizer seeds."
        ),
        "indicators": _unique(
            [
                concept
                for dimension in ("momentum", "structure")
                for concept in core_dna.get(dimension) or []
            ]
        ),
        "entry_conditions": entry_conditions,
        "exit_conditions": [
            f"Shared exit concept: {value}" for value in core_dna.get("exit") or []
        ],
        "risk_rules": [
            f"Shared risk concept: {value}" for value in core_dna.get("risk") or []
        ],
        "avoid_conditions": [],
        "market_context": [
            f"{DNA_LABELS[dimension]}: {value}"
            for dimension in ("context", "market_regime")
            for value in core_dna.get(dimension) or []
        ],
        "stock_selection": [
            f"{DNA_LABELS[dimension]}: {value}"
            for dimension in ("universe", "catalyst")
            for value in core_dna.get(dimension) or []
        ],
        "unresolved_rules": unresolved,
        "confidence": support_confidence,
        "confidence_kind": "cross_source_structural_support",
        "machine_rules": explicit,
        "research_rule_overrides": research_overrides,
        "compiler_assumptions": assumption_log,
        "candidate_rule_options": source_options,
        "strategy_dna": core_dna,
        "untranslated_dna": untranslated_dna,
        "source_type": "cross_source_synthesis",
        "source_id": "synthesis:" + str(blueprint.get("family_id") or blueprint.get("id") or "candidate"),
        "source_title": f"Cross-source synthesis · {source_count} independent sources",
        "source_author": "",
        "source_url": "",
        "source_strategy_ids": list(blueprint.get("contributing_strategy_ids") or []),
        "supporting_sources": supporting_sources,
        "supporting_source_count": source_count,
        "validated_source_count": int(blueprint.get("validated_source_count") or 0),
        "synthesis_support_score": priority,
        "synthesis_blueprint": dict(blueprint),
        "evidence": [
            {
                "location": str(source),
                "description": "Independent source contributing to this Strategy DNA family.",
                "source_excerpt": "",
            }
            for source in supporting_sources
        ],
        "approved": False,
        "validation_status": "unvalidated",
        "optimization_status": "not_run",
        "backtest_supported": direction in {"long", "both"},
    }
