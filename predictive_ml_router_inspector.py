"""Print a compact summary of the newest stock-learning-router ML run.

This is intentionally read-only. It downloads the private durable Trading Intelligence
Lab library with the existing GitHub backup token, extracts only the newest completed
predictive ML run that contains stock-learning-router evidence, and writes a small JSON
summary that is easy to inspect from GitHub Actions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib import parse, request


OUTPUT_PATH = Path("predictive_ml_router_summary.json")


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _github_json(url: str, token: str) -> dict[str, Any]:
    req = request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "trading-intelligence-lab-router-inspector",
        },
    )
    with request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_library(repository: str, token: str, branch: str, path: str) -> dict[str, Any]:
    if not branch:
        meta = _github_json(f"https://api.github.com/repos/{repository}", token)
        branch = str(meta.get("default_branch") or "main")

    encoded_path = parse.quote(path.lstrip("/"), safe="/")
    encoded_ref = parse.quote(branch, safe="")
    url = f"https://api.github.com/repos/{repository}/contents/{encoded_path}?ref={encoded_ref}"
    req = request.Request(
        url,
        headers={
            "Accept": "application/vnd.github.raw+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "trading-intelligence-lab-router-inspector",
        },
    )
    with request.urlopen(req, timeout=180) as response:
        payload = response.read()
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("Trading Intelligence Lab library root is not a JSON object.")
    return data


def _newest_router_run(library: dict[str, Any]) -> dict[str, Any]:
    runs = [
        dict(item)
        for item in library.get("predictive_ml_runs") or []
        if isinstance(item, dict) and isinstance(item.get("stock_learning_router"), dict)
    ]
    if not runs:
        raise RuntimeError("No predictive ML run with stock-learning-router evidence was found.")
    runs.sort(
        key=lambda item: (
            str(item.get("completed_at") or ""),
            int(item.get("model_suite_version") or item.get("suite_version") or 0),
        ),
        reverse=True,
    )
    return runs[0]


def build_summary(run: dict[str, Any]) -> dict[str, Any]:
    router = dict(run.get("stock_learning_router") or {})
    by_symbol = router.get("by_symbol")
    if not isinstance(by_symbol, (dict, list)):
        by_symbol = {}

    return {
        "run_id": run.get("id") or run.get("run_id"),
        "completed_at": run.get("completed_at"),
        "model_suite_version": run.get("model_suite_version") or run.get("suite_version"),
        "symbols": run.get("symbols"),
        "dataset_row_count": (
            (run.get("dataset") or {}).get("row_count")
            if isinstance(run.get("dataset"), dict)
            else run.get("dataset_row_count")
        ),
        "router": {
            "status": router.get("status"),
            "symbols_compared": router.get("symbols_compared"),
            "clear_routes": router.get("clear_routes"),
            "route_counts": router.get("route_counts"),
            "minimum_paired_oos_rows": router.get("minimum_paired_oos_rows"),
            "minimum_brier_edge": router.get("minimum_brier_edge"),
            "minimum_auc_edge": router.get("minimum_auc_edge"),
            "by_symbol": by_symbol,
        },
        "affects_live_ranking": bool(router.get("affects_live_ranking", False)),
        "affects_execution": bool(router.get("affects_execution", False)),
    }


def main() -> int:
    repository = _env("GITHUB_BACKUP_REPOSITORY")
    token = _env("GITHUB_BACKUP_TOKEN")
    branch = _env("GITHUB_BACKUP_BRANCH")
    path = _env(
        "GITHUB_BACKUP_PATH",
        "trading-intelligence-lab/intelligence_library.json",
    )
    if not repository or not token:
        raise RuntimeError(
            "GITHUB_BACKUP_REPOSITORY and GITHUB_BACKUP_TOKEN are required."
        )

    library = _download_library(repository, token, branch, path)
    run = _newest_router_run(library)
    summary = build_summary(run)

    OUTPUT_PATH.write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print("PREDICTIVE_ML_ROUTER_SUMMARY_BEGIN")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print("PREDICTIVE_ML_ROUTER_SUMMARY_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
