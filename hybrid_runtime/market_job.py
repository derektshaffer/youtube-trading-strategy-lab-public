"""Bound real-market job payloads while keeping full history in the artifact cache."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .market_cache import CancellationCheck, ProgressCallback, run_stock_analysis


MAX_CHART_CANDLES = 600


def run_bounded_stock_analysis(
    payload: Mapping[str, Any],
    *,
    data_dir: str | Path,
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> dict[str, Any]:
    result = dict(
        run_stock_analysis(
            payload,
            data_dir=data_dir,
            progress=progress,
            cancelled=cancelled,
        )
    )
    candles = [
        dict(row)
        for row in result.get("candles") or []
        if isinstance(row, Mapping)
    ]
    result["cached_bars"] = len(candles)
    result["candles"] = candles[-MAX_CHART_CANDLES:]
    result["chart_bars"] = len(result["candles"])
    return result
