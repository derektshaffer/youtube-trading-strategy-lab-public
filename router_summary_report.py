"""Read-only diagnostic for the latest stock-learning router result."""

from __future__ import annotations

import json
import os

from youtube_strategy_engine import (
    DEFAULT_GITHUB_BACKUP_PATH,
    GitHubCloudBackup,
    StrategyStore,
)


def main() -> int:
    repository = str(os.environ.get("GITHUB_BACKUP_REPOSITORY") or "").strip()
    token = str(os.environ.get("GITHUB_BACKUP_TOKEN") or "").strip()
    branch = str(os.environ.get("GITHUB_BACKUP_BRANCH") or "").strip()
    path = str(
        os.environ.get("GITHUB_BACKUP_PATH") or DEFAULT_GITHUB_BACKUP_PATH
    ).strip()
    if not repository or not token:
        raise RuntimeError(
            "GITHUB_BACKUP_REPOSITORY and GITHUB_BACKUP_TOKEN are required."
        )

    store = StrategyStore(
        cloud_backup=GitHubCloudBackup(
            repository,
            token,
            branch=branch,
            path=path,
        )
    )
    library = store.load_latest()
    research_system = (
        dict(library.get("research_system") or {})
        if isinstance(library.get("research_system"), dict)
        else {}
    )
    status = dict(research_system.get("predictive_ml_backfill_status") or {})
    run_id = str(status.get("run_id") or "").strip()
    runs = [
        item
        for item in library.get("predictive_ml_runs") or []
        if isinstance(item, dict)
    ]
    active_run = next(
        (item for item in runs if str(item.get("id") or "").strip() == run_id),
        runs[0] if runs else {},
    )
    router = (
        dict(active_run.get("stock_learning_router") or {})
        if isinstance(active_run, dict)
        else {}
    )

    by_symbol = []
    for item in router.get("by_symbol") or []:
        if not isinstance(item, dict):
            continue
        routes = dict(item.get("routes") or {}) if isinstance(item.get("routes"), dict) else {}
        row = {
            "symbol": item.get("symbol"),
            "status": item.get("status"),
            "recommended_route": item.get("recommended_route"),
            "recommended_route_label": item.get("recommended_route_label"),
            "provisional_lowest_brier_route": item.get("provisional_lowest_brier_route"),
            "provisional_lowest_brier_route_label": item.get(
                "provisional_lowest_brier_route_label"
            ),
            "paired_oos_rows": item.get("paired_oos_rows"),
            "reason": item.get("reason"),
            "routes": {},
        }
        for route_name in (
            "same_ticker_history",
            "similarity_weighted_transfer",
            "broad_cross_stock_transfer",
        ):
            route = dict(routes.get(route_name) or {})
            row["routes"][route_name] = {
                "brier_score": route.get("brier_score"),
                "roc_auc": route.get("roc_auc"),
                "oos_rows": route.get("oos_rows"),
            }
        by_symbol.append(row)

    summary = {
        "backfill_status": status,
        "run_id": active_run.get("id") if isinstance(active_run, dict) else None,
        "router_status": router.get("status"),
        "symbols_compared": router.get("symbols_compared"),
        "clear_route_count": router.get("clear_route_count"),
        "route_counts": router.get("route_counts"),
        "router_reason": router.get("reason"),
        "by_symbol": by_symbol,
    }
    print("ML_ROUTER_SUMMARY_START")
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    print("ML_ROUTER_SUMMARY_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
