"""Research-only per-stock learning-route comparison.

The Trading Intelligence Lab now has three complementary historical learning paths:
1) broad cross-stock transfer,
2) similarity-weighted cross-stock transfer, and
3) same-ticker historical learning.

This module aligns predictions from those paths on the exact same unseen historical
rows and asks which route produced the best probability quality for each stock.
It does not change live rankings, sizing, Paper Auto, or execution.
"""

from __future__ import annotations

import math
from typing import Any

from sklearn.metrics import roc_auc_score


DEFAULT_MIN_PAIRED_ROWS = 40
DEFAULT_MIN_BRIER_EDGE = 0.0025
DEFAULT_MIN_AUC_EDGE = 0.02
DEFAULT_MAX_AUC_TRADEOFF = 0.015
DEFAULT_MAX_BRIER_TRADEOFF = 0.001

ROUTE_LABELS = {
    "same_ticker_history": "Same-ticker history",
    "similarity_weighted_transfer": "Similarity-weighted transfer",
    "broad_cross_stock_transfer": "Broad cross-stock transfer",
}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _row_key(symbol: str, row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        _symbol(symbol),
        str(row.get("session") or ""),
        str(row.get("timestamp") or ""),
    )


def _safe_auc(actual: list[int], probability: list[float]) -> float | None:
    if len(actual) < 2 or len(set(actual)) < 2:
        return None
    try:
        return float(roc_auc_score(actual, probability))
    except ValueError:
        return None


def _metrics(rows: list[dict[str, Any]], probability_key: str) -> dict[str, Any]:
    actual = [int(row["actual"]) for row in rows]
    probability = [float(row[probability_key]) for row in rows]
    brier = float(
        sum((prob - outcome) ** 2 for prob, outcome in zip(probability, actual))
        / len(actual)
    )
    return {
        "oos_rows": len(rows),
        "positive_rate": float(sum(actual) / len(actual)),
        "brier_score": brier,
        "roc_auc": _safe_auc(actual, probability),
    }


def _choose_route(
    metrics: dict[str, dict[str, Any]],
    *,
    min_brier_edge: float,
    min_auc_edge: float,
    max_auc_tradeoff: float,
    max_brier_tradeoff: float,
) -> tuple[str | None, str, str]:
    ordered_brier = sorted(
        metrics.items(),
        key=lambda item: (
            float(item[1]["brier_score"]),
            -float(item[1].get("roc_auc") or -1.0),
            item[0],
        ),
    )
    provisional_route = ordered_brier[0][0]
    best_route, best = ordered_brier[0]
    second_route, second = ordered_brier[1]
    brier_edge = float(second["brier_score"]) - float(best["brier_score"])
    best_auc = _number(best.get("roc_auc"))
    second_auc = _number(second.get("roc_auc"))

    auc_tradeoff_ok = (
        best_auc is None
        or second_auc is None
        or best_auc >= second_auc - max_auc_tradeoff
    )
    if brier_edge >= min_brier_edge and auc_tradeoff_ok:
        return (
            best_route,
            provisional_route,
            (
                f"{ROUTE_LABELS[best_route]} has the clearest probability-quality edge: "
                f"Brier improves by {brier_edge:.4f} versus the next-best route"
                + (
                    f" with AUC {best_auc:.3f}."
                    if best_auc is not None
                    else "."
                )
            ),
        )

    auc_candidates = [
        (route, row)
        for route, row in metrics.items()
        if _number(row.get("roc_auc")) is not None
    ]
    auc_candidates.sort(
        key=lambda item: (
            -float(item[1]["roc_auc"]),
            float(item[1]["brier_score"]),
            item[0],
        )
    )
    if len(auc_candidates) >= 2:
        auc_route, auc_best = auc_candidates[0]
        _, auc_second = auc_candidates[1]
        auc_edge = float(auc_best["roc_auc"]) - float(auc_second["roc_auc"])
        brier_tradeoff = (
            float(auc_best["brier_score"])
            - float(ordered_brier[0][1]["brier_score"])
        )
        if auc_edge >= min_auc_edge and brier_tradeoff <= max_brier_tradeoff:
            return (
                auc_route,
                provisional_route,
                (
                    f"{ROUTE_LABELS[auc_route]} has a meaningful AUC edge "
                    f"({auc_edge:+.3f}) without a material Brier penalty."
                ),
            )

    return (
        None,
        provisional_route,
        (
            "No learning route has a material enough out-of-sample edge yet. "
            f"{ROUTE_LABELS[provisional_route]} currently has the lowest Brier score, "
            "but the difference is too small to treat as a reliable routing decision."
        ),
    )


def build_stock_learning_router(
    ticker_specific: dict[str, Any],
    similarity_validation: dict[str, Any],
    *,
    minimum_paired_rows: int = DEFAULT_MIN_PAIRED_ROWS,
    min_brier_edge: float = DEFAULT_MIN_BRIER_EDGE,
    min_auc_edge: float = DEFAULT_MIN_AUC_EDGE,
    max_auc_tradeoff: float = DEFAULT_MAX_AUC_TRADEOFF,
    max_brier_tradeoff: float = DEFAULT_MAX_BRIER_TRADEOFF,
) -> dict[str, Any]:
    """Compare learning sources for each ticker on exactly aligned unseen rows."""

    ticker_predictions = [
        dict(row)
        for row in (ticker_specific or {}).get("predictions") or []
        if isinstance(row, dict)
    ]
    similarity_predictions = [
        dict(row)
        for row in (similarity_validation or {}).get("predictions") or []
        if isinstance(row, dict)
    ]
    if not ticker_predictions or not similarity_predictions:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": (
                "Ticker-specific and similarity validation both need row-level "
                "out-of-sample predictions before learning routes can be compared."
            ),
            "research_only": True,
            "affects_live_ranking": False,
            "affects_execution": False,
        }

    ticker_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in ticker_predictions:
        symbol = _symbol(row.get("model_symbol") or row.get("symbol"))
        probability = _number(row.get("probability"))
        if not symbol or probability is None:
            continue
        ticker_by_key[_row_key(symbol, row)] = {
            "actual": 1 if bool(row.get("actual")) else 0,
            "ticker_probability": probability,
        }

    paired_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in similarity_predictions:
        symbol = _symbol(row.get("held_out_symbol") or row.get("symbol"))
        similarity_probability = _number(row.get("similarity_probability"))
        baseline_probability = _number(row.get("baseline_probability"))
        if (
            not symbol
            or similarity_probability is None
            or baseline_probability is None
        ):
            continue
        key = _row_key(symbol, row)
        ticker = ticker_by_key.get(key)
        if not ticker:
            continue
        actual = 1 if bool(row.get("actual")) else 0
        if int(ticker["actual"]) != actual:
            continue
        paired_by_symbol.setdefault(symbol, []).append(
            {
                "actual": actual,
                "ticker_probability": float(ticker["ticker_probability"]),
                "similarity_probability": float(similarity_probability),
                "baseline_probability": float(baseline_probability),
            }
        )

    minimum_paired_rows = max(1, int(minimum_paired_rows))
    by_symbol: list[dict[str, Any]] = []
    route_counts = {route: 0 for route in ROUTE_LABELS}
    inconclusive = 0

    for symbol in sorted(paired_by_symbol):
        rows = paired_by_symbol[symbol]
        if len(rows) < minimum_paired_rows:
            by_symbol.append(
                {
                    "symbol": symbol,
                    "status": "INSUFFICIENT_DATA",
                    "paired_oos_rows": len(rows),
                    "minimum_paired_rows": minimum_paired_rows,
                    "reason": (
                        f"Only {len(rows)} exactly aligned unseen rows are available; "
                        f"{minimum_paired_rows} are required."
                    ),
                }
            )
            continue

        metrics = {
            "same_ticker_history": _metrics(rows, "ticker_probability"),
            "similarity_weighted_transfer": _metrics(
                rows, "similarity_probability"
            ),
            "broad_cross_stock_transfer": _metrics(
                rows, "baseline_probability"
            ),
        }
        recommended_route, provisional_route, reason = _choose_route(
            metrics,
            min_brier_edge=max(0.0, float(min_brier_edge)),
            min_auc_edge=max(0.0, float(min_auc_edge)),
            max_auc_tradeoff=max(0.0, float(max_auc_tradeoff)),
            max_brier_tradeoff=max(0.0, float(max_brier_tradeoff)),
        )
        if recommended_route:
            route_counts[recommended_route] += 1
            route_status = "PROVISIONAL_ROUTE_LEADER"
        else:
            inconclusive += 1
            route_status = "NO_CLEAR_ROUTE"

        by_symbol.append(
            {
                "symbol": symbol,
                "status": "EVALUATED",
                "route_status": route_status,
                "paired_oos_rows": len(rows),
                "recommended_route": recommended_route,
                "recommended_route_label": (
                    ROUTE_LABELS.get(recommended_route)
                    if recommended_route
                    else None
                ),
                "provisional_lowest_brier_route": provisional_route,
                "provisional_lowest_brier_route_label": ROUTE_LABELS[
                    provisional_route
                ],
                "reason": reason,
                "routes": metrics,
            }
        )

    evaluated = [row for row in by_symbol if row.get("status") == "EVALUATED"]
    if not evaluated:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": (
                "The learning paths did not overlap on enough identical unseen rows "
                "for any stock."
            ),
            "minimum_paired_rows": minimum_paired_rows,
            "by_symbol": by_symbol,
            "research_only": True,
            "affects_live_ranking": False,
            "affects_execution": False,
        }

    clear = sum(route_counts.values())
    return {
        "status": "EVALUATED",
        "validation_type": "paired_stock_learning_route_comparison",
        "symbols_compared": len(evaluated),
        "symbols_with_clear_route": clear,
        "symbols_inconclusive": inconclusive,
        "route_counts": route_counts,
        "by_symbol": by_symbol,
        "minimum_paired_rows": minimum_paired_rows,
        "decision_policy": {
            "primary_metric": "brier_score",
            "minimum_brier_edge": float(min_brier_edge),
            "minimum_auc_edge": float(min_auc_edge),
            "maximum_auc_tradeoff": float(max_auc_tradeoff),
            "maximum_brier_tradeoff": float(max_brier_tradeoff),
            "exact_row_alignment_required": True,
        },
        "note": (
            "Each comparison uses the same ticker, session, timestamp, and realized "
            "outcome across all three learning paths. A route is only called a "
            "provisional leader when its out-of-sample edge clears the configured "
            "materiality threshold. The router is diagnostic only."
        ),
        "research_only": True,
        "affects_live_ranking": False,
        "affects_execution": False,
    }
