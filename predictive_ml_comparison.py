"""Compact comparison helpers for predictive-ML research runs."""

from __future__ import annotations

from typing import Any


ROUTES = (
    "same_ticker_history",
    "similarity_weighted_transfer",
    "broad_cross_stock_transfer",
)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number:
        return None
    return number


def _by_symbol(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    router = run.get("stock_learning_router")
    if not isinstance(router, dict):
        return {}
    output: dict[str, dict[str, Any]] = {}
    for raw in router.get("by_symbol") or []:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or "").strip().upper()
        if symbol:
            output[symbol] = raw
    return output


def compare_predictive_ml_runs(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """Compare router evidence between two saved predictive-ML runs."""
    prior = _by_symbol(previous or {})
    latest = _by_symbol(current or {})
    symbols = sorted(set(prior) | set(latest))
    rows: list[dict[str, Any]] = []

    for symbol in symbols:
        before = prior.get(symbol) or {}
        after = latest.get(symbol) or {}
        before_routes = (
            before.get("routes") if isinstance(before.get("routes"), dict) else {}
        )
        after_routes = (
            after.get("routes") if isinstance(after.get("routes"), dict) else {}
        )
        route_changes: dict[str, dict[str, Any]] = {}
        for route in ROUTES:
            old = before_routes.get(route)
            new = after_routes.get(route)
            old = old if isinstance(old, dict) else {}
            new = new if isinstance(new, dict) else {}
            old_brier = _number(old.get("brier_score"))
            new_brier = _number(new.get("brier_score"))
            old_auc = _number(old.get("roc_auc"))
            new_auc = _number(new.get("roc_auc"))
            route_changes[route] = {
                "previous_brier": old_brier,
                "current_brier": new_brier,
                "brier_delta": (
                    None
                    if old_brier is None or new_brier is None
                    else new_brier - old_brier
                ),
                "previous_auc": old_auc,
                "current_auc": new_auc,
                "auc_delta": (
                    None
                    if old_auc is None or new_auc is None
                    else new_auc - old_auc
                ),
            }

        previous_route = str(before.get("recommended_route") or "") or None
        current_route = str(after.get("recommended_route") or "") or None
        previous_status = str(before.get("route_status") or "") or None
        current_status = str(after.get("route_status") or "") or None
        rows.append(
            {
                "symbol": symbol,
                "previous_route": previous_route,
                "current_route": current_route,
                "route_changed": previous_route != current_route,
                "previous_route_status": previous_status,
                "current_route_status": current_status,
                "route_status_changed": previous_status != current_status,
                "previous_paired_oos_rows": int(before.get("paired_oos_rows") or 0),
                "current_paired_oos_rows": int(after.get("paired_oos_rows") or 0),
                "paired_oos_rows_delta": (
                    int(after.get("paired_oos_rows") or 0)
                    - int(before.get("paired_oos_rows") or 0)
                ),
                "routes": route_changes,
                "current_reason": str(after.get("reason") or ""),
            }
        )

    changed = [row["symbol"] for row in rows if row["route_changed"]]
    status_changed = [
        row["symbol"] for row in rows if row["route_status_changed"]
    ]
    return {
        "previous_run_id": previous.get("id"),
        "current_run_id": current.get("id"),
        "previous_suite_version": int(previous.get("model_suite_version") or 0),
        "current_suite_version": int(current.get("model_suite_version") or 0),
        "symbols_compared": len(rows),
        "route_changes": changed,
        "route_status_changes": status_changed,
        "by_symbol": rows,
    }


def compact_comparison_lines(report: dict[str, Any]) -> list[str]:
    """Render one compact diagnostic line per compared symbol."""
    lines = [
        (
            "[predictive-ml-compare] "
            f"previous={report.get('previous_run_id') or 'unknown'} "
            f"current={report.get('current_run_id') or 'unknown'} "
            f"previous_suite={int(report.get('previous_suite_version') or 0)} "
            f"current_suite={int(report.get('current_suite_version') or 0)} "
            f"symbols={int(report.get('symbols_compared') or 0)} "
            f"route_changes={','.join(report.get('route_changes') or []) or 'none'}"
        )
    ]
    for row in report.get("by_symbol") or []:
        routes = row.get("routes") if isinstance(row.get("routes"), dict) else {}
        pieces = []
        for route in ROUTES:
            metrics = routes.get(route)
            metrics = metrics if isinstance(metrics, dict) else {}
            brier_delta = _number(metrics.get("brier_delta"))
            auc_delta = _number(metrics.get("auc_delta"))
            pieces.append(
                f"{route}:db={brier_delta:+.6f if brier_delta is not None else 'na'}"
            )
            pieces.append(
                f"{route}:da={auc_delta:+.6f if auc_delta is not None else 'na'}"
            )
        lines.append(
            "[predictive-ml-compare-symbol] "
            f"symbol={row.get('symbol') or 'unknown'} "
            f"previous={row.get('previous_route') or 'none'} "
            f"current={row.get('current_route') or 'none'} "
            f"status_before={row.get('previous_route_status') or 'none'} "
            f"status_after={row.get('current_route_status') or 'none'} "
            f"paired_oos_delta={int(row.get('paired_oos_rows_delta') or 0)}"
        )
    return lines
