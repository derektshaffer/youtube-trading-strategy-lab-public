"""Bounded Strategy Lab option summaries derived from the authoritative library."""

from __future__ import annotations

from typing import Any, Mapping


def build_strategy_lab_options(library: Mapping[str, Any], *, limit: int = 300) -> dict[str, Any]:
    from trading_intelligence_core import strategy_integrity_report

    maximum = max(1, min(500, int(limit)))
    faithful: list[dict[str, Any]] = []
    blocked = 0
    for raw in library.get("strategies") or []:
        if not isinstance(raw, Mapping):
            continue
        strategy = dict(raw)
        strategy_id = str(strategy.get("id") or "").strip()
        if not strategy_id:
            continue
        report = strategy_integrity_report(strategy)
        if str(report.get("status") or "").strip().lower() != "faithful":
            blocked += 1
            continue
        faithful.append(
            {
                "id": strategy_id,
                "name": str(strategy.get("name") or "Unnamed strategy")[:180],
                "category": str(strategy.get("category") or "")[:100],
                "direction": str(strategy.get("direction") or "")[:40],
                "source_type": str(strategy.get("source_type") or "")[:80],
                "source_title": str(strategy.get("source_title") or "")[:180],
                "validation_status": str(strategy.get("validation_status") or "research_only")[:80],
                "fidelity_status": "faithful",
            }
        )
    faithful.sort(
        key=lambda item: (
            str(item.get("name") or "").casefold(),
            str(item.get("id") or ""),
        )
    )
    return {
        "strategies": faithful[:maximum],
        "faithful_count": len(faithful),
        "blocked_count": blocked,
        "bounded": True,
        "limit": maximum,
        "research_only": True,
        "affects_execution": False,
    }
