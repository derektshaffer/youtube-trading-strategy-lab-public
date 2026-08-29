"""Read-only aggregate fidelity audit for the private Trading Intelligence library.

This script intentionally prints only aggregate counts and generic capability labels.
It must never emit source text, strategy names, URLs, authors, or secret values because
the validation workflow can run in a public repository.
"""

from __future__ import annotations

from collections import defaultdict
import json
import os
import re
from typing import Any

from trading_intelligence_core import (
    paper_execution_fidelity,
    strategy_integrity_report,
    upgrade_native_strategy_rules,
)
from trading_strategy_dna import (
    build_canonical_family_strategies,
    is_family_source_strategy,
    source_identity,
)
from youtube_strategy_engine import (
    DEFAULT_GITHUB_BACKUP_PATH,
    GitHubCloudBackup,
    normalize_machine_rules,
)


DYNAMIC_EXIT_RULES = (
    "trailing_stop_pct",
    "move_stop_to_breakeven_at_r",
    "exit_below_vwap",
    "exit_below_fast_ema",
)


def _setting(name: str, default: str = "") -> str:
    return str(os.environ.get(name) or default).strip()


def _aggregate_gaps(strategies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for strategy in strategies:
        report = strategy_integrity_report(strategy)
        identity = source_identity(strategy)
        for requirement in report.get("requirements") or []:
            if requirement.get("modeled") or not requirement.get("critical"):
                continue
            label = str(requirement.get("label") or "Unmodeled requirement")
            bucket = buckets.setdefault(
                label,
                {
                    "capability": label,
                    "area": str(requirement.get("dimension") or "other"),
                    "strategy_count": 0,
                    "source_ids": set(),
                    "limitation": str(requirement.get("limitation") or ""),
                },
            )
            bucket["strategy_count"] += 1
            bucket["source_ids"].add(identity)

    rows: list[dict[str, Any]] = []
    for bucket in buckets.values():
        rows.append(
            {
                "capability": bucket["capability"],
                "area": bucket["area"],
                "strategy_count": int(bucket["strategy_count"]),
                "independent_source_count": len(bucket["source_ids"]),
                "limitation": bucket["limitation"],
            }
        )
    rows.sort(
        key=lambda item: (
            -int(item["strategy_count"]),
            -int(item["independent_source_count"]),
            str(item["capability"]),
        )
    )
    return rows


def run_audit() -> dict[str, Any]:
    repository = _setting("GITHUB_BACKUP_REPOSITORY")
    token = _setting("GITHUB_BACKUP_TOKEN")
    branch = _setting("GITHUB_BACKUP_BRANCH")
    path = _setting("GITHUB_BACKUP_PATH", DEFAULT_GITHUB_BACKUP_PATH)
    if not repository or not token:
        raise RuntimeError("Private Trading Lab backup configuration is missing.")

    backup = GitHubCloudBackup(
        repository,
        token,
        branch=branch,
        path=path,
    )
    remote = backup.read_library()
    if not remote:
        raise RuntimeError("The private Trading Lab library does not exist.")

    library = remote["library"]
    all_strategies = [
        dict(item)
        for item in library.get("strategies") or []
        if isinstance(item, dict)
    ]
    original_sources = [
        item for item in all_strategies if is_family_source_strategy(item)
    ]

    upgraded_sources = [upgrade_native_strategy_rules(item) for item in original_sources]
    rebuilt_families, _ = build_canonical_family_strategies(upgraded_sources)

    source_reports = [strategy_integrity_report(item) for item in upgraded_sources]
    family_reports = [strategy_integrity_report(item) for item in rebuilt_families]

    source_blocked = sum(
        1 for item in source_reports if str(item.get("status") or "") == "blocked"
    )
    family_blocked = sum(
        1 for item in family_reports if str(item.get("status") or "") == "blocked"
    )
    family_faithful = sum(
        1 for item in family_reports if str(item.get("status") or "") == "faithful"
    )
    family_partial = sum(
        1 for item in family_reports if str(item.get("status") or "") == "partial"
    )

    legacy_validations_invalidated = 0
    for before, after in zip(original_sources, upgraded_sources):
        if (
            str(before.get("validation_status") or "").lower() == "validated"
            and str(after.get("validation_status") or "").lower() != "validated"
            and after.get("previous_validation_invalidated_by_integrity_audit")
        ):
            legacy_validations_invalidated += 1

    dynamic_exit_usage: dict[str, int] = defaultdict(int)
    paper_execution_blocked = 0
    for strategy in rebuilt_families:
        rules = normalize_machine_rules(strategy.get("machine_rules"))
        options = strategy.get("candidate_rule_options") or {}
        for rule_name in DYNAMIC_EXIT_RULES:
            if rules.get(rule_name) is not None or any(
                value is not None for value in (options.get(rule_name) or [])
            ):
                dynamic_exit_usage[rule_name] += 1
        if str(paper_execution_fidelity(strategy).get("status") or "") == "blocked":
            paper_execution_blocked += 1

    gaps = _aggregate_gaps(upgraded_sources)

    # Safe aggregate pattern inventory for deciding what to implement next.
    scale_patterns = {
        "strategies": 0,
        "explicit_position_fraction": 0,
        "half_position_language": 0,
        "explicit_r_target": 0,
        "runner_language": 0,
        "trailing_runner_language": 0,
        "breakeven_runner_language": 0,
    }
    avwap_patterns = {
        "strategies": 0,
        "event_or_catalyst_anchor": 0,
        "swing_high_or_low_anchor": 0,
        "day_or_session_anchor": 0,
        "ipo_anchor": 0,
        "earnings_anchor": 0,
        "gap_anchor": 0,
        "handoff_or_reanchor": 0,
    }
    float_patterns = {
        "strategies": 0,
        "explicit_share_threshold": 0,
        "qualitative_low_float_only": 0,
    }

    for strategy in upgraded_sources:
        text = " ".join(
            str(value or "")
            for value in (
                strategy.get("summary"),
                *(strategy.get("entry_conditions") or []),
                *(strategy.get("exit_conditions") or []),
                *(strategy.get("risk_rules") or []),
                *(strategy.get("stock_selection") or []),
                *(strategy.get("market_context") or []),
                *(strategy.get("unresolved_rules") or []),
            )
        ).casefold()

        has_scale = any(
            phrase in text
            for phrase in ("scale out", "scaling out", "partial profit", "take partial")
        )
        if has_scale:
            scale_patterns["strategies"] += 1
            if re.search(r"\b(?:\d+(?:\.\d+)?)\s*%\b", text):
                scale_patterns["explicit_position_fraction"] += 1
            if any(phrase in text for phrase in ("half the position", "half position", "sell half", "take half")):
                scale_patterns["half_position_language"] += 1
            if re.search(r"\b\d+(?:\.\d+)?\s*r\b", text):
                scale_patterns["explicit_r_target"] += 1
            if "runner" in text:
                scale_patterns["runner_language"] += 1
            if "runner" in text and any(phrase in text for phrase in ("trail", "trailing stop")):
                scale_patterns["trailing_runner_language"] += 1
            if "runner" in text and any(
                phrase in text for phrase in ("breakeven", "break even", "break-even")
            ):
                scale_patterns["breakeven_runner_language"] += 1

        if "anchored vwap" in text or "avwap" in text:
            avwap_patterns["strategies"] += 1
            if any(word in text for word in ("catalyst", "event", "news")):
                avwap_patterns["event_or_catalyst_anchor"] += 1
            if any(phrase in text for phrase in ("swing high", "swing low", "pivot high", "pivot low")):
                avwap_patterns["swing_high_or_low_anchor"] += 1
            if any(phrase in text for phrase in ("day one", "session open", "opening print", "start of day")):
                avwap_patterns["day_or_session_anchor"] += 1
            if "ipo" in text:
                avwap_patterns["ipo_anchor"] += 1
            if "earnings" in text:
                avwap_patterns["earnings_anchor"] += 1
            if "gap" in text:
                avwap_patterns["gap_anchor"] += 1
            if any(phrase in text for phrase in ("handoff", "re-anchor", "reanchor", "new anchor")):
                avwap_patterns["handoff_or_reanchor"] += 1

        if any(phrase in text for phrase in ("low float", "low-float", "float under", "share float")):
            float_patterns["strategies"] += 1
            if re.search(
                r"(?:float|shares?)[^\.]{0,60}\b\d+(?:\.\d+)?\s*(?:m|million|k|thousand)\b",
                text,
            ):
                float_patterns["explicit_share_threshold"] += 1
            else:
                float_patterns["qualitative_low_float_only"] += 1

    return {
        "library_updated_at": str(library.get("updated_at") or ""),
        "total_saved_strategy_records": len(all_strategies),
        "original_source_strategies": len(upgraded_sources),
        "rebuilt_canonical_families": len(rebuilt_families),
        "source_strategies_with_critical_gaps": source_blocked,
        "canonical_families_fully_modeled": family_faithful,
        "canonical_families_partially_modeled": family_partial,
        "canonical_families_with_critical_gaps": family_blocked,
        "legacy_validations_invalidated_by_audit": legacy_validations_invalidated,
        "canonical_families_blocked_from_paper_auto": paper_execution_blocked,
        "dynamic_exit_family_usage": {
            name: int(dynamic_exit_usage.get(name, 0))
            for name in DYNAMIC_EXIT_RULES
        },
        "scale_out_pattern_inventory": scale_patterns,
        "anchored_vwap_pattern_inventory": avwap_patterns,
        "float_pattern_inventory": float_patterns,
        "missing_capabilities": gaps[:30],
    }


if __name__ == "__main__":
    print("STRATEGY_INTEGRITY_AUDIT_START")
    print(json.dumps(run_audit(), indent=2, sort_keys=True))
    print("STRATEGY_INTEGRITY_AUDIT_END")
