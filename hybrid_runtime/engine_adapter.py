"""Lazy adapters from the hybrid job service to existing Trading Lab engines."""

from __future__ import annotations

import json
from pathlib import Path
import platform
import sys
from typing import Any, Callable, Mapping


ProgressCallback = Callable[[float, str, str], None]
CancellationCheck = Callable[[], bool]
JobHandler = Callable[[Mapping[str, Any], ProgressCallback, CancellationCheck], Mapping[str, Any]]


class JobCancelled(RuntimeError):
    pass


def _check_cancelled(cancelled: CancellationCheck) -> None:
    if cancelled():
        raise JobCancelled("Job cancellation was requested")


def system_health_handler(
    payload: Mapping[str, Any],
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> Mapping[str, Any]:
    _check_cancelled(cancelled)
    progress(0.5, "preparing_features", "Inspecting the local runtime")
    _check_cancelled(cancelled)
    return {
        "status": "ok",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "requested_checks": list(payload.get("checks") or []),
    }


def _load_library(payload: Mapping[str, Any]) -> dict[str, Any]:
    inline = payload.get("library")
    if isinstance(inline, dict):
        return dict(inline)
    raw_path = str(payload.get("library_path") or "").strip()
    if not raw_path:
        raise ValueError("strategy.profit_first_plan requires library or library_path")
    path = Path(raw_path).expanduser().resolve()
    decoded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("The Trading Intelligence library must be a JSON object")
    return decoded


def profit_first_plan_handler(
    payload: Mapping[str, Any],
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> Mapping[str, Any]:
    progress(0.15, "preparing_features", "Loading the research library")
    library = _load_library(payload)
    _check_cancelled(cancelled)
    progress(0.55, "searching", "Ranking strict Profit First candidates")
    # Lazy import keeps the hybrid core independent of Streamlit and heavy engine
    # modules until this real Trading Lab operation is requested.
    from profit_first_queue import profit_first_validation_batch

    maximum = max(1, min(3, int(payload.get("maximum_candidates") or 2)))
    result = profit_first_validation_batch(library, maximum_candidates=maximum)
    _check_cancelled(cancelled)
    progress(0.9, "saving", "Preparing the candidate plan")
    return dict(result)


def default_handlers() -> dict[str, JobHandler]:
    return {
        "system.health": system_health_handler,
        "strategy.profit_first_plan": profit_first_plan_handler,
    }
