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
    KEYCHAIN_SERVICE,
    load_desktop_settings,
)
from .keychain import KeychainError, MacOSKeychain
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
    keychain: MacOSKeychain | None = None,
    job_limit: int = 200,
) -> dict[str, Any]:
    root = Path(data_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    settings = load_desktop_settings(root)
    secrets = keychain or MacOSKeychain(KEYCHAIN_SERVICE)

    database_path = root / "hybrid.sqlite3"
    jobs: list[Any] = []
    if database_path.exists():
        try:
            jobs = HybridStore(database_path).list_jobs(limit=max(10, min(500, int(job_limit))))
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

    local_library = Path(settings.local_library_path).expanduser() if settings.local_library_path else None
    cache_stats = {
        "market": _file_stats(root / "market-cache-v1"),
        "job_database": _file_stats(database_path),
        "cloud_links": _file_stats(root / "cloud-links.sqlite3"),
        "library_cache": _file_stats(root / "library-cache"),
        "strategy_lab_checkpoint_cache": _file_stats(root / "strategy-lab-checkpoint-cache"),
        "configured_local_library": _file_stats(local_library) if local_library else {"exists": False, "bytes": 0, "files": 0, "latest_mtime": 0.0},
    }

    library = dict(library_summary or {})
    source = str(library.get("source") or "unverified")
    library_healthy = bool(
        source not in {"unverified", "unavailable", "error"}
        and not str(library.get("error") or "").strip()
    )
    cloud_configured = bool(settings.github_repository and settings.github_path)

    checks = {
        "runtime_storage": database_path.exists(),
        "github_library_configured": cloud_configured,
        "github_library_credential": credentials["github_private_library"],
        "library_readable": library_healthy,
        "alpaca_credentials": credentials["alpaca_ready"],
        "market_cache_present": bool(cache_stats["market"]["files"]),
    }
    required = (
        "runtime_storage",
        "github_library_configured",
        "github_library_credential",
        "library_readable",
        "alpaca_credentials",
    )
    required_ok = all(checks[name] for name in required)

    return {
        "status": "ready" if required_ok else "attention",
        "checks": checks,
        "credentials": credentials,
        "library": library,
        "connection": {
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
