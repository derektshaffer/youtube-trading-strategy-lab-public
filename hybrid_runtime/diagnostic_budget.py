"""Opt-in execution budgets; no changes to strategy validation semantics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

DEFAULT_DIAGNOSTIC_TIMEOUT_MINUTES = 20


def diagnostic_budget(payload: Mapping[str, Any]) -> dict[str, Any]:
    mode = payload.get("diagnostic_mode", False)
    if type(mode) is not bool:
        raise ValueError("diagnostic_mode must be a boolean")
    if not mode:
        if any(key in payload for key in ("diagnostic_max_attempts", "diagnostic_timeout_minutes")):
            raise ValueError("Diagnostic bounds require diagnostic_mode=true")
        return {}
    attempts = payload.get("diagnostic_max_attempts", 1)
    minutes = payload.get("diagnostic_timeout_minutes", DEFAULT_DIAGNOSTIC_TIMEOUT_MINUTES)
    if type(attempts) is not int or attempts != 1:
        raise ValueError("Diagnostic jobs support exactly one attempt")
    if type(minutes) is not int or not 5 <= minutes <= 30:
        raise ValueError("Diagnostic timeout must be an integer from 5 to 30 minutes")
    return {"diagnostic_mode": True, "diagnostic_max_attempts": 1,
            "diagnostic_timeout_minutes": minutes}


def strategy_lab_dispatch_inputs(item: Mapping[str, Any]) -> dict[str, str]:
    bounds = diagnostic_budget(item.get("payload") or {})
    return {"job_id": str(item.get("id") or ""), **{
        key: "true" if value is True else str(value) for key, value in bounds.items()
    }}


def stamp_diagnostic_deadline(payload: dict[str, Any]) -> None:
    bounds = diagnostic_budget(payload)
    if bounds:
        now = datetime.now(timezone.utc)
        payload.update(bounds)
        payload.setdefault("diagnostic_attempt_started_at", now.isoformat())
        start = datetime.fromisoformat(payload["diagnostic_attempt_started_at"].replace("Z", "+00:00"))
        payload["diagnostic_deadline_at"] = (
            start + timedelta(minutes=bounds["diagnostic_timeout_minutes"])
        ).isoformat()


def remaining_diagnostic_seconds(payload: Mapping[str, Any]) -> float:
    diagnostic_budget(payload)
    deadline = datetime.fromisoformat(str(payload["diagnostic_deadline_at"]).replace("Z", "+00:00"))
    return max(0.0, (deadline - datetime.now(timezone.utc)).total_seconds())


def workflow_execution_minutes(mode: bool, attempts: int = 1, minutes: int = 20) -> int:
    if not mode:
        return 330
    bounds = diagnostic_budget({"diagnostic_mode": mode, "diagnostic_max_attempts": attempts,
                                "diagnostic_timeout_minutes": minutes})
    return bounds["diagnostic_timeout_minutes"] + 2


if __name__ == "__main__":
    import os
    mode = os.environ.get("DIAGNOSTIC_MODE", "false") == "true"
    timeout = workflow_execution_minutes(
        mode, int(os.environ.get("DIAGNOSTIC_MAX_ATTEMPTS") or 1),
        int(os.environ.get("DIAGNOSTIC_TIMEOUT_MINUTES") or 20),
    )
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"execution_timeout_minutes={timeout}\n")
