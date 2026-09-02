"""Bounded, secret-free summaries for the native Trading Intelligence Results page."""

from __future__ import annotations

from typing import Any, Mapping


STRATEGY_LAB_RECORD_TYPE = "strategy_lab_checkpoint"


def _text(value: Any, maximum: int = 240) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _stamp(item: Mapping[str, Any]) -> str:
    for key in (
        "generated_at",
        "completed_at",
        "saved_at",
        "updated_at",
        "created_at",
        "started_at",
    ):
        value = _text(item.get(key), 80)
        if value:
            return value
    return ""


def _verdict(item: Mapping[str, Any]) -> str:
    for key in ("evidence_verdict", "verdict", "validation_verdict"):
        value = item.get(key)
        if isinstance(value, Mapping):
            for nested in ("label", "code", "status", "verdict"):
                text = _text(value.get(nested), 100)
                if text:
                    return text
        else:
            text = _text(value, 100)
            if text:
                return text
    for key in ("validation_status", "status"):
        text = _text(item.get(key), 100)
        if text:
            return text
    return ""


def _finder_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    holdout = item.get("holdout_metrics") if isinstance(item.get("holdout_metrics"), Mapping) else {}
    validation = item.get("validation_metrics") if isinstance(item.get("validation_metrics"), Mapping) else {}
    robustness = item.get("robustness") if isinstance(item.get("robustness"), Mapping) else {}
    return {
        "id": _text(item.get("id"), 160),
        "generated_at": _stamp(item),
        "symbol": _text(item.get("symbol"), 16).upper(),
        "profile": _text(item.get("profile"), 60),
        "winner": _text(item.get("winner_strategy_name"), 160),
        "strategy_id": _text(item.get("winner_source_strategy_id"), 180),
        "timeframe": _text(item.get("timeframe"), 40),
        "configurations": _int(item.get("unique_configurations_tested")),
        "verdict": _verdict(item),
        "robustness_score": _number(robustness.get("score")),
        "validation_pnl": _number(validation.get("net_pnl")),
        "validation_trades": _int(validation.get("trade_count")),
        "holdout_pnl": _number(holdout.get("net_pnl")),
        "holdout_trades": _int(holdout.get("trade_count")),
    }


def _validation_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    evidence = item.get("evidence_verdict") if isinstance(item.get("evidence_verdict"), Mapping) else {}
    method = (
        item.get("validation_method_version")
        or item.get("method_version")
        or item.get("method")
        or item.get("validation_method")
    )
    return {
        "id": _text(item.get("id"), 180),
        "generated_at": _stamp(item),
        "strategy_id": _text(item.get("strategy_id"), 180),
        "strategy_name": _text(
            item.get("strategy_name")
            or item.get("name")
            or item.get("source_strategy_name"),
            180,
        ),
        "symbol": _text(item.get("symbol") or item.get("ticker"), 16).upper(),
        "status": _text(item.get("validation_status") or item.get("status"), 80),
        "verdict": _verdict(item),
        "verdict_code": _text(evidence.get("code"), 100),
        "method": _text(method, 100),
        "research_only": bool(item.get("research_only")),
    }


def _strategy_lab_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    result = item.get("result") if isinstance(item.get("result"), Mapping) else {}
    report = result.get("report") if isinstance(result.get("report"), Mapping) else {}
    winner = report.get("winner") if isinstance(report.get("winner"), Mapping) else {}
    strength = result.get("strength") if isinstance(result.get("strength"), Mapping) else {}
    evidence = result.get("evidence_verdict") if isinstance(result.get("evidence_verdict"), Mapping) else {}
    return {
        "id": _text(item.get("id"), 180),
        "saved_at": _stamp(item),
        "ticker": _text(item.get("ticker") or result.get("ticker"), 16).upper(),
        "status": _text(item.get("status"), 60),
        "stage": _text(item.get("stage"), 80),
        "message": _text(item.get("message"), 300),
        "timeframe": _text(result.get("timeframe"), 40),
        "history_days": _int(result.get("history_days")),
        "winner": _text(winner.get("strategy_name"), 180),
        "winner_strategy_id": _text(winner.get("source_strategy_id"), 180),
        "strength_score": _number(strength.get("score")),
        "verdict": _text(
            evidence.get("label") or evidence.get("code") or evidence.get("status"),
            100,
        ),
    }


def _strategy_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": _text(item.get("id"), 180),
        "name": _text(item.get("name"), 180),
        "category": _text(item.get("category"), 100),
        "optimized_for_symbol": _text(item.get("optimized_for_symbol"), 16).upper(),
        "validation_status": _text(item.get("validation_status"), 80),
        "source_type": _text(item.get("source_type"), 80),
        "updated_at": _stamp(item),
    }


def _latest(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    ordered = sorted(
        items,
        key=lambda item: str(
            item.get("generated_at")
            or item.get("saved_at")
            or item.get("updated_at")
            or ""
        ),
        reverse=True,
    )
    return ordered[: max(1, min(100, int(limit)))]


def build_results_summary(
    library: Mapping[str, Any],
    *,
    limit: int = 25,
) -> dict[str, Any]:
    """Return compact recent evidence without copying full optimization payloads."""

    finder = [
        _finder_summary(item)
        for item in library.get("stock_strategy_finder_runs") or []
        if isinstance(item, Mapping)
    ]
    validation_records = [
        item
        for item in library.get("validation_runs") or []
        if isinstance(item, Mapping)
    ]
    labs = [
        _strategy_lab_summary(item)
        for item in validation_records
        if str(item.get("record_type") or "") == STRATEGY_LAB_RECORD_TYPE
    ]
    validations = [
        _validation_summary(item)
        for item in validation_records
        if str(item.get("record_type") or "") != STRATEGY_LAB_RECORD_TYPE
    ]
    strategies = [
        _strategy_summary(item)
        for item in library.get("strategies") or []
        if isinstance(item, Mapping) and _text(item.get("validation_status"), 80)
    ]
    validated = [
        item
        for item in strategies
        if str(item.get("validation_status") or "").strip().lower() == "validated"
    ]

    return {
        "counts": {
            "finder_runs": len(finder),
            "validation_runs": len(validations),
            "strategy_lab_runs": len(labs),
            "strategies_with_status": len(strategies),
            "validated_strategies": len(validated),
        },
        "finder_runs": _latest(finder, limit),
        "validation_runs": _latest(validations, limit),
        "strategy_lab_runs": _latest(labs, limit),
        "strategies": _latest(strategies, limit),
        "validated_strategies": _latest(validated, limit),
        "bounded": True,
        "limit": max(1, min(100, int(limit))),
    }
