"""Bounded, redacted support snapshot for the Trading Intelligence desktop beta."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import platform
from pathlib import Path
import re
import sys
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit


UTC = timezone.utc
SNAPSHOT_SCHEMA = 1
MAX_FAILURES = 12
MAX_MESSAGE = 500

_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(
        r"(?i)\b(token|api[_ -]?key|secret|password|authorization)\b(\s*[:=]\s*)([^\s,;]+)"
    ),
)
_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def _clean_text(value: Any, *, limit: int = MAX_MESSAGE) -> str:
    text = " ".join(str(value or "").split())
    home = str(Path.home())
    if home:
        text = text.replace(home, "<home>")
    # Also cover an arbitrary /Users/<name> path from another machine or CI fixture.
    text = re.sub(r"/Users/[^/\s]+", "<home>", text)
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.startswith("(?i)\\b(token"):
            text = pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", text)
        else:
            text = pattern.sub("<redacted>", text)

    def strip_url_query(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parts = urlsplit(raw)
        except ValueError:
            return "<url>"
        if not parts.scheme or not parts.netloc:
            return "<url>"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    text = _URL_PATTERN.sub(strip_url_query, text)
    return text[: max(20, int(limit))]


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _safe_bool_dict(value: Any) -> dict[str, bool]:
    source = value if isinstance(value, Mapping) else {}
    return {str(key): bool(item) for key, item in source.items()}


def _safe_file_stats(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    return {
        "exists": bool(source.get("exists")),
        "bytes": max(0, _safe_int(source.get("bytes"))),
        "files": max(0, _safe_int(source.get("files"))),
    }


def _repository_name(value: Any) -> str:
    raw = str(value or "").strip().strip("/")
    return _clean_text(raw.rsplit("/", 1)[-1] if raw else "", limit=120)


def _safe_build_identity(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    commit = _clean_text(source.get("commit"), limit=64)
    return {
        "version": _clean_text(source.get("version"), limit=64),
        "bundle_short_version": _clean_text(
            source.get("bundle_short_version"), limit=32
        ),
        "build_number": _clean_text(source.get("build_number"), limit=32),
        "channel": _clean_text(source.get("channel"), limit=48),
        "commit": commit,
        "commit_short": _clean_text(
            source.get("commit_short") or commit[:12], limit=16
        ),
        "packaged": bool(source.get("packaged")),
    }


def build_support_snapshot(
    health: Mapping[str, Any],
    *,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Return a support-safe snapshot from a bounded System Health result.

    This function deliberately selects fields instead of recursively serializing
    the health payload. Full paths, credentials, research/strategy/model content,
    job payloads, cloud tokens, and library content are never copied into the
    support snapshot.
    """

    source = dict(health or {})
    checks = source.get("checks") if isinstance(source.get("checks"), Mapping) else {}
    setup = source.get("setup") if isinstance(source.get("setup"), Mapping) else {}
    setup_capabilities = (
        setup.get("capabilities")
        if isinstance(setup.get("capabilities"), Mapping)
        else {}
    )
    runtime = source.get("runtime") if isinstance(source.get("runtime"), Mapping) else {}
    library = source.get("library") if isinstance(source.get("library"), Mapping) else {}
    connection = (
        source.get("connection")
        if isinstance(source.get("connection"), Mapping)
        else {}
    )
    jobs = source.get("jobs") if isinstance(source.get("jobs"), Mapping) else {}
    storage = source.get("storage") if isinstance(source.get("storage"), Mapping) else {}

    failures: list[dict[str, Any]] = []
    for item in jobs.get("recent_failures") or []:
        if not isinstance(item, Mapping):
            continue
        failures.append(
            {
                "job_id_suffix": _clean_text(str(item.get("id") or "")[-12:], limit=20),
                "job_type": _clean_text(item.get("job_type"), limit=100),
                "stage": _clean_text(item.get("stage"), limit=100),
                "message": _clean_text(item.get("message"), limit=MAX_MESSAGE),
                "updated_at": _clean_text(item.get("updated_at"), limit=80),
            }
        )
        if len(failures) >= MAX_FAILURES:
            break

    library_counts = {}
    for name in (
        "strategies",
        "validation_runs",
        "research_queue",
        "predictive_ml_runs",
        "knowledge_sources",
        "finder_runs",
    ):
        if name in library:
            library_counts[name] = max(0, _safe_int(library.get(name)))

    storage_summary = {
        str(name): _safe_file_stats(stats)
        for name, stats in storage.items()
        if str(name) in {
            "market",
            "job_database",
            "cloud_links",
            "library_cache",
            "strategy_lab_checkpoint_cache",
            "configured_local_library",
        }
    }

    current = (created_at or datetime.now(UTC)).astimezone(UTC)
    snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        "kind": "trading-intelligence-redacted-support-snapshot",
        "created_at": current.isoformat().replace("+00:00", "Z"),
        "product": "Trading Intelligence Desktop",
        "research_only": True,
        "build": _safe_build_identity(source.get("build")),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
        },
        "health": {
            "status": _clean_text(source.get("status"), limit=40),
            "checks": _safe_bool_dict(checks),
            "required_checks": [
                _clean_text(item, limit=80)
                for item in source.get("required_checks") or []
            ][:20],
        },
        "setup": {
            "verification": _clean_text(setup.get("verification"), limit=40),
            "launch_ready": bool(setup.get("launch_ready")),
            "capabilities": {
                "library": bool(setup_capabilities.get("library")),
                "cloud": bool(setup_capabilities.get("cloud")),
                "market": bool(setup_capabilities.get("market")),
            },
        },
        "runtime": {
            "status": _clean_text(runtime.get("status"), limit=60),
            "authenticated_loopback": bool(runtime.get("authenticated_loopback")),
        },
        "library": {
            "source": _clean_text(library.get("source"), limit=100),
            "cloud_refreshed": bool(library.get("cloud_refreshed")),
            "warning": _clean_text(library.get("warning"), limit=MAX_MESSAGE),
            "counts": library_counts,
        },
        "connection": {
            "library_source_preference": _clean_text(
                connection.get("library_source_preference"), limit=60
            ),
            "library_mode": _clean_text(connection.get("library_mode"), limit=40),
            "github_required_for_library": bool(
                connection.get("github_required_for_library")
            ),
            "github_repository_name": _repository_name(
                connection.get("github_repository")
            ),
            "github_branch": _clean_text(connection.get("github_branch"), limit=100),
            "github_library_filename": _clean_text(
                Path(str(connection.get("github_path") or "")).name,
                limit=120,
            ),
            "market_feed": _clean_text(connection.get("market_feed"), limit=20),
            "local_library_filename": _clean_text(
                Path(str(connection.get("local_library_path") or "")).name,
                limit=120,
            ),
        },
        "jobs": {
            "active": max(0, _safe_int(jobs.get("active"))),
            "sample_size": max(0, _safe_int(jobs.get("sample_size"))),
            "status_counts": {
                _clean_text(key, limit=60): max(0, _safe_int(value))
                for key, value in (
                    jobs.get("status_counts").items()
                    if isinstance(jobs.get("status_counts"), Mapping)
                    else []
                )
            },
            "type_counts": {
                _clean_text(key, limit=100): max(0, _safe_int(value))
                for key, value in (
                    jobs.get("type_counts").items()
                    if isinstance(jobs.get("type_counts"), Mapping)
                    else []
                )
            },
            "recent_failures": failures,
        },
        "storage": storage_summary,
    }
    # Enforce a small support payload even if an upstream label or message grows.
    encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > 32_000:
        snapshot["jobs"]["recent_failures"] = failures[:4]
        snapshot["library"]["warning"] = _clean_text(
            snapshot["library"].get("warning"), limit=200
        )
    return snapshot


def snapshot_json(snapshot: Mapping[str, Any]) -> str:
    return json.dumps(dict(snapshot), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
