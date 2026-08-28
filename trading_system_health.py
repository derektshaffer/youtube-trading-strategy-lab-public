"""Runtime health and cloud-state diagnostics for Trading Intelligence Lab.

This module has no Streamlit dependency so the important reliability decisions can
be unit-tested independently from the UI.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

UTC = timezone.utc

DEFAULT_ACTIONS_REPOSITORY = "derektshaffer/youtube-trading-strategy-lab-public"
DISTRIBUTED_FINDER_WORKFLOW = "distributed-stock-finder.yml"
CLOUD_SMOKE_WORKFLOW = "cloud-research-smoke-test.yml"


def _value(setting_reader: Callable[[str, str], str], name: str, default: str = "") -> str:
    try:
        return str(setting_reader(name, default) or "").strip()
    except TypeError:
        return str(setting_reader(name) or default or "").strip()


def configuration_checks(
    setting_reader: Callable[[str, str], str],
    *,
    backup_repository: str = "",
) -> list[dict[str, Any]]:
    """Return configuration-only health checks without making network requests."""
    repository = str(backup_repository or "").strip() or _value(
        setting_reader,
        "GITHUB_BACKUP_REPOSITORY",
    )
    backup_token = (
        _value(setting_reader, "GITHUB_BACKUP_TOKEN")
        or _value(setting_reader, "GITHUB_TOKEN")
        or _value(setting_reader, "GH_TOKEN")
    )
    alpaca_key = _value(setting_reader, "ALPACA_API_KEY")
    alpaca_secret = _value(setting_reader, "ALPACA_SECRET_KEY")
    gemini_key = _value(setting_reader, "GEMINI_API_KEY")
    actions_token = _value(setting_reader, "GITHUB_ACTIONS_TOKEN")
    actions_repository = _value(
        setting_reader,
        "GITHUB_ACTIONS_REPOSITORY",
        DEFAULT_ACTIONS_REPOSITORY,
    )

    return [
        {
            "id": "durable_storage",
            "name": "Private durable storage",
            "status": "ready" if repository and backup_token else "blocked",
            "required": True,
            "subsystems": ["stock_finder", "ai_research", "library"],
            "detail": (
                f"Configured for {repository}."
                if repository and backup_token
                else "Missing GITHUB_BACKUP_REPOSITORY or GITHUB_BACKUP_TOKEN."
            ),
        },
        {
            "id": "market_data",
            "name": "Alpaca market data",
            "status": "ready" if alpaca_key and alpaca_secret else "blocked",
            "required": True,
            "subsystems": ["stock_finder", "ai_research", "live"],
            "detail": (
                "API key and secret are configured."
                if alpaca_key and alpaca_secret
                else "Missing ALPACA_API_KEY or ALPACA_SECRET_KEY."
            ),
        },
        {
            "id": "gemini",
            "name": "Gemini research",
            "status": "ready" if gemini_key else "blocked",
            "required": True,
            "subsystems": ["ai_research", "knowledge"],
            "detail": (
                "Primary Gemini key is configured."
                if gemini_key
                else "Missing GEMINI_API_KEY."
            ),
        },
        {
            "id": "github_actions_launcher",
            "name": "GitHub Actions launcher",
            "status": "ready" if actions_token and actions_repository else "blocked",
            "required": True,
            "subsystems": ["stock_finder", "ai_research", "cloud"],
            "detail": (
                f"Immediate workflow launch is configured for {actions_repository}."
                if actions_token and actions_repository
                else "Missing GITHUB_ACTIONS_TOKEN. Cloud jobs can be saved, but immediate launch cannot be verified."
            ),
        },
    ]


def overall_system_state(checks: list[dict[str, Any]]) -> dict[str, Any]:
    required = [item for item in checks if bool(item.get("required", True))]
    blocked = [item for item in required if str(item.get("status")) != "ready"]
    return {
        "state": "READY" if not blocked else "DEGRADED",
        "ready": len(required) - len(blocked),
        "total": len(required),
        "blocked": [str(item.get("name") or item.get("id") or "") for item in blocked],
    }


def subsystem_ready(checks: list[dict[str, Any]], subsystem: str) -> tuple[bool, list[str]]:
    target = str(subsystem or "").strip()
    relevant = [
        item
        for item in checks
        if target in list(item.get("subsystems") or [])
        and bool(item.get("required", True))
    ]
    blocked = [
        str(item.get("detail") or item.get("name") or item.get("id") or "")
        for item in relevant
        if str(item.get("status")) != "ready"
    ]
    return not blocked, blocked


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def cloud_job_display_state(
    job: dict[str, Any],
    *,
    actions_configured: bool,
    launch_result: dict[str, Any] | None = None,
    now: datetime | None = None,
    stalled_after_minutes: int = 15,
) -> dict[str, str]:
    """Translate durable queue state into a user-facing operational state."""
    status = str(job.get("status") or "queued").strip().lower()
    payload = dict(job.get("payload") or {})
    launch = dict(launch_result or {})

    if status == "complete":
        return {"state": "COMPLETE", "severity": "success", "detail": "Cloud research finished and the result was saved."}
    if status == "failed":
        return {
            "state": "FAILED",
            "severity": "error",
            "detail": str(job.get("last_error") or "The cloud worker exhausted its retry attempts."),
        }
    if status == "running":
        message = str(payload.get("distributed_message") or "").strip()
        return {
            "state": "RUNNING",
            "severity": "success",
            "detail": message or "A cloud worker has claimed this job and compute is active.",
        }
    if status == "retry":
        return {
            "state": "RETRYING",
            "severity": "warning",
            "detail": str(job.get("last_error") or "The previous worker stopped; a durable retry is waiting to run."),
        }

    current = (now or datetime.now(UTC)).astimezone(UTC)
    queued_at = _parse_iso(job.get("updated_at")) or _parse_iso(job.get("created_at"))
    age_minutes = (
        max(0.0, (current - queued_at).total_seconds() / 60.0)
        if queued_at is not None
        else 0.0
    )
    if not payload.get("distributed_run_id") and age_minutes >= max(5, int(stalled_after_minutes)):
        return {
            "state": "STALLED",
            "severity": "error",
            "detail": (
                f"No cloud worker has claimed this job after about {age_minutes:.0f} minutes. "
                "Do not assume compute is running; check System Health."
            ),
        }

    if bool(launch.get("ok")):
        return {
            "state": "STARTING",
            "severity": "info",
            "detail": "GitHub accepted the launch request; waiting for the worker to claim the saved job.",
        }

    if launch and not bool(launch.get("ok")):
        return {
            "state": "QUEUED",
            "severity": "warning",
            "detail": (
                str(launch.get("detail") or "").strip()
                or "Immediate launch failed; the scheduled worker is the fallback."
            ),
        }

    if not actions_configured:
        return {
            "state": "QUEUED",
            "severity": "warning",
            "detail": (
                "The job is saved, but immediate launch is not configured. "
                "Only the scheduled worker can pick it up until GITHUB_ACTIONS_TOKEN is added."
            ),
        }

    return {
        "state": "QUEUED",
        "severity": "info",
        "detail": "The job is durably saved and waiting for a cloud worker to claim it.",
    }


def _github_request(
    repository: str,
    token: str,
    path: str,
    *,
    timeout: int = 12,
) -> tuple[int, dict[str, Any]]:
    repo = str(repository or "").strip()
    auth = str(token or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("GitHub repository must look like owner/repository.")
    if not auth:
        raise ValueError("GitHub Actions token is missing.")
    url = f"https://api.github.com/repos/{repo}/{path.lstrip('/')}"
    req = urllib_request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {auth}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Trading-Intelligence-Lab-Health",
        },
    )
    with urllib_request.urlopen(req, timeout=max(5, int(timeout))) as response:
        status = int(getattr(response, "status", 0) or 0)
        raw = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(raw) if raw.strip() else {}
    return status, parsed if isinstance(parsed, dict) else {}


def probe_github_workflow(
    repository: str,
    token: str,
    *,
    workflow: str = DISTRIBUTED_FINDER_WORKFLOW,
    timeout: int = 12,
) -> dict[str, str]:
    if not str(token or "").strip():
        return {
            "state": "BLOCKED",
            "severity": "error",
            "detail": "GITHUB_ACTIONS_TOKEN is not configured.",
        }
    try:
        status, payload = _github_request(
            repository,
            token,
            f"actions/workflows/{workflow}",
            timeout=timeout,
        )
        workflow_state = str(payload.get("state") or "").strip().lower()
        if status == 200 and workflow_state == "active":
            return {
                "state": "READY",
                "severity": "success",
                "detail": f"GitHub can read the active {workflow} workflow.",
            }
        return {
            "state": "DEGRADED",
            "severity": "warning",
            "detail": f"GitHub returned workflow state {workflow_state or 'unknown'} (HTTP {status}).",
        }
    except urllib_error.HTTPError as exc:
        if exc.code in {401, 403}:
            detail = "The Actions token lacks permission to read/run this repository's workflows."
        elif exc.code == 404:
            detail = "GitHub could not find this workflow for the configured token/repository."
        else:
            detail = f"GitHub workflow probe failed with HTTP {exc.code}."
        return {"state": "BLOCKED", "severity": "error", "detail": detail}
    except (urllib_error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "state": "UNKNOWN",
            "severity": "warning",
            "detail": f"GitHub workflow probe could not complete: {str(exc)[:220]}",
        }


def latest_workflow_run(
    repository: str,
    token: str,
    *,
    workflow: str = CLOUD_SMOKE_WORKFLOW,
    timeout: int = 12,
) -> dict[str, Any] | None:
    if not str(token or "").strip():
        return None
    try:
        _, payload = _github_request(
            repository,
            token,
            f"actions/workflows/{workflow}/runs?event=workflow_dispatch&per_page=5",
            timeout=timeout,
        )
    except (urllib_error.HTTPError, urllib_error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return None
    runs = payload.get("workflow_runs") or []
    if not isinstance(runs, list) or not runs:
        return None
    run = runs[0] if isinstance(runs[0], dict) else {}
    return {
        "id": run.get("id"),
        "status": str(run.get("status") or ""),
        "conclusion": str(run.get("conclusion") or ""),
        "created_at": str(run.get("created_at") or ""),
        "updated_at": str(run.get("updated_at") or ""),
        "html_url": str(run.get("html_url") or ""),
        "head_sha": str(run.get("head_sha") or ""),
    }


def workflow_run_display_state(run: dict[str, Any] | None) -> dict[str, str]:
    if not run:
        return {
            "state": "NOT RUN",
            "severity": "warning",
            "detail": "No end-to-end cloud smoke test has been observed yet.",
        }
    status = str(run.get("status") or "").lower()
    conclusion = str(run.get("conclusion") or "").lower()
    if status in {"queued", "requested", "waiting", "pending"}:
        return {"state": "QUEUED", "severity": "info", "detail": "The smoke test is waiting for a GitHub runner."}
    if status == "in_progress":
        return {"state": "RUNNING", "severity": "info", "detail": "The smoke test is actively checking cloud dependencies."}
    if conclusion == "success":
        return {
            "state": "PASS",
            "severity": "success",
            "detail": "GitHub Actions, private backup read/write, Alpaca, and Gemini all passed the live smoke test.",
        }
    if status == "completed":
        return {
            "state": "FAIL",
            "severity": "error",
            "detail": f"The live smoke test completed with {conclusion or 'an unknown failure'}.",
        }
    return {"state": "UNKNOWN", "severity": "warning", "detail": "The latest smoke-test state could not be classified."}
