"""Isolated durable storage for Explosive Stock Lab artifacts.

This module intentionally uses a separate local directory and a separate private
GitHub path from Trading Intelligence Lab. Shared storage primitives are reused,
but the data files and synchronization tokens are independent.
"""

from __future__ import annotations

import os
from pathlib import Path

from youtube_strategy_engine import GitHubCloudBackup, StrategyStore

DEFAULT_EXPLOSIVE_BACKUP_PATH = "explosive-stock-lab/prescreen_library.json"
DEFAULT_EXPLOSIVE_DATA_DIR = ".explosive_stock_lab_data"


def build_explosive_store(
    repository: str,
    token: str,
    *,
    branch: str = "",
    path: str = DEFAULT_EXPLOSIVE_BACKUP_PATH,
    directory: str | Path | None = None,
) -> StrategyStore:
    cloud = None
    if str(repository or "").strip() and str(token or "").strip():
        cloud = GitHubCloudBackup(
            str(repository).strip(),
            str(token).strip(),
            branch=str(branch or "").strip(),
            path=str(path or DEFAULT_EXPLOSIVE_BACKUP_PATH).strip(),
        )
    chosen_directory = (
        Path(directory)
        if directory is not None
        else Path(os.environ.get("EXPLOSIVE_STOCK_DATA_DIR") or DEFAULT_EXPLOSIVE_DATA_DIR)
    )
    return StrategyStore(directory=chosen_directory, cloud_backup=cloud)
