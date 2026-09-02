"""Read-only desktop/system health summary with no secret-value exposure."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .contracts import TERMINAL_JOB_STATUSES
from .desktop_settings import (
    ALPACA_API_KEY_ACCOUNT,
    ALPACA_SECRET_KEY_ACCOUNT,
    GITHUB_BACKUP_TOKEN_ACCOUNT,
    load_desktop_settings,
)
from .keychain import KeychainError, MacOSKeychain
from .onboarding_state import configuration_status
from .storage import HybridStore


def _file_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "bytes": 0, "files": 0, "latest_mtime": 0.0}
    files = [item for item in path.rglob("*") if item.is_file()] if path.is_dir() else [path]
    return {
        "exists": True,
        "bytes": sum(item.stat().st_size for item in files),
        "files": len(files),
        "latest_mtime": max((item.stat().st_mtime for item in files), default=0.0),
    }


def _secret_present(keychain: MacOSKeychain, account: str) -> bool:
    try:
        return bool(keychain.get_secret(account))
    except KeychainError:
        return False


def build_system_health_summary(
    data_dir: str | Path,
    *,
    library_summary: Mapping[str, Any] | None = None,
    runtime_health: Mapping[str, Any] | None = None,
    keychain: MacOSKeychain | None = None,
    job_limit: int = 200,
) -> dict[str, Any]:
    """Build a bounded diagnostic snapshot without mutating trading state.

    A valid configured local library is allowed to satisfy the library-connection
    gate without GitHub credentials. GitHub credential state remains visible as
    a separate cloud-capability diagnostic. Explicitly pending first-run Setup is
    also surfaced so health and feature gates cannot disagree.
    """

    root = Path(data_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    settings = load_desktop_settings(root)
    secrets = keychain or MacOSKeychain()

    database_path = root / "hybrid.sqlite3"
    jobs: list[Any] = []
    job_store_readable = False
    if database_path.exists():
        try:
            jobs = HybridStore(database_path).list_jobs(limit=max(10, min(500, int(job_limit))))
            job_store_readable = True
        except Exception:
            jobs = []

    status_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    recent_failures: list[dict[str, Any]] = []
    active_jobs = 0
    for job in jobs:
        status = str(getattr(job.status, "value", job.status) or "unknown")
        status_counts[status] += 1
        type_counts[str(job.job_type or "unknown")] += 1
        if job.status not in TERMINAL_JOB_STATUSES:
            active_jobs += 1
        if status == "failed":
            error = job.error if isinstance(job.error, dict) else {}
            recent_failures.append(
                {
                    "id": str(job.id),
                    "job_type": str(job.job_type),
                    "stage": str(job.stage or "failed"),
                    "message": " ".join(str(error.get("message") or "Failed job").split())[:300],
                    "updated_at": str(job.updated_at or ""),
                }
            )
    recent_failures.sort(key=lambda item: item["updated_at"], reverse=True)

    credentials = {
        "github_private_library": _secret_present(secrets, GITHUB_BACKUP_TOKEN_ACCOUNT),
        "alpaca_api_key": _secret_present(secrets, ALPACA_API_KEY_ACCOUNT),
        "alpaca_secret_key": _secret_present(secrets, ALPACA_SECRET_KEY_ACCOUNT),
    }
    credentials["alpaca_ready"] = bool(
        credentials["alpaca_api_key"] and credentials["alpaca_secret_key"]
    )

    try:
        setup = configuration_status(root, keychain=secrets)
    except Exception:
        setup = {
            "setup_verification": "unavailable",
            "launch_ready": False,
            "capabilities": {"library": False, "cloud": False, "market": False},
        }
    setup_capabilities = (
        dict(setup.get("capabilities"))
        if isinstance(setup.get("capabilities"), Mapping)
        else {"library": False, "cloud": False, "market": False}
    )
    setup_pending = str(setup.get("setup_verification") or "").strip().lower() == "pending"

    local_library = Path(settings.local_library_path).expanduser() if settings.local_library_path else None
    cache_stats = {
        "market": _file_stats(root / "market-cache-v1"),
        "job_database": _file_stats(database_path),
        "cloud_links": _file_stats(root / "cloud-links.sqlite3"),
        "library_cache": _file_stats(root / "library-cache"),
        "strategy_lab_checkpoint_cache": _file_stats(root / "strategy-lab-checkpoint-cache"),
        "configured_local_library": (
            _file_stats(local_library)
            if local_library
            else {"exists": False, "bytes": 0, "files": 0, "latest_mtime": 0.0}
        ),
    }

    library = dict(library_summary or {})
    source = str(library.get("source") or "unverified")
    library_healthy = bool(
        source not in {"unverified", "unavailable", "error"}
        and not str(library.get("error") or "").strip()
    )
    local_sources = {
        "inline_fixture",
        "explicit_local_file",
        "configured_local_file",
    }
    github_sources = {
        "private_github_backup",
        "local_cache",
        "local_cache_after_cloud_error",
    }
    using_local_library = source in local_sources
    using_github_library = source in github_sources or (
        settings.library_source == "github_backup" and not using_local_library
    )
    github_configured = bool(settings.github_repository and settings.github_path)
    github_refresh_ready = bool(github_configured and credentials["github_private_library"])
    if using_local_library:
        library_connection_ready = library_healthy
        library_mode = "local"
    elif using_github_library:
        library_connection_ready = github_refresh_ready
        library_mode = "github"
    else:
        if settings.library_source == "local_file":
            library_connection_ready = bool(local_library and local_library.is_file())
            library_mode = "local"
        else:
            library_connection_ready = github_refresh_ready
            library_mode = "github"

    live_runtime = dict(runtime_health or {})
    runtime_service_ready = str(live_runtime.get("status") or "").lower() == "ok"

    checks = {
        "runtime_service": runtime_service_ready,
        "runtime_storage": bool(database_path.exists() and job_store_readable),
        "library_connection": library_connection_ready,
        "library_readable": library_healthy,
        "alpaca_credentials": credentials["alpaca_ready"],
        "github_library_credential": credentials["github_private_library"],
        "github_library_configured": github_configured,
        "market_cache_present": bool(cache_stats["market"]["files"]),
        "setup_library_verified": bool(setup_capabilities.get("library")),
        "setup_cloud_verified": bool(setup_capabilities.get("cloud")),
        "setup_market_verified": bool(setup_capabilities.get("market")),
        "setup_not_pending": not setup_pending,
    }
    required_checks = (
        "runtime_service",
        "runtime_storage",
        "library_connection",
        "library_readable",
        "alpaca_credentials",
    )
    required_ok = all(checks[name] for name in required_checks)
    overall_ready = bool(required_ok and not setup_pending)

    return {
        "status": "ready" if overall_ready else "attention",
        "checks": checks,
        "required_checks": list(required_checks),
        "setup": {
            "verification": str(setup.get("setup_verification") or "unavailable"),
            "launch_ready": bool(setup.get("launch_ready")),
            "capabilities": {
                "library": bool(setup_capabilities.get("library")),
                "cloud": bool(setup_capabilities.get("cloud")),
                "market": bool(setup_capabilities.get("market")),
            },
        },
        "credentials": credentials,
        "runtime": {
            "status": str(live_runtime.get("status") or "unverified"),
            "authenticated_loopback": runtime_service_ready,
        },
        "library": library,
        "connection": {
            "library_source_preference": settings.library_source,
            "library_mode": library_mode,
            "github_required_for_library": bool(library_mode == "github"),
            "github_repository": settings.github_repository,
            "github_branch": settings.github_branch,
            "github_path": settings.github_path,
            "market_feed": settings.market_feed,
            "local_library_path": settings.local_library_path,
        },
        "jobs": {
            "active": active_jobs,
            "status_counts": dict(status_counts),
            "type_counts": dict(type_counts),
            "recent_failures": recent_failures[:12],
            "sample_size": len(jobs),
        },
        "storage": cache_stats,
        "data_dir": str(root),
        "bounded": True,
        "research_only": True,
        "affects_live_ranking": False,
        "affects_execution": False,
    }
