"""Stamp and read non-secret Trading Intelligence desktop build identity."""

from __future__ import annotations

import os
from pathlib import Path
import plistlib
import sys
from typing import Any, Mapping


VERSION_LABEL_KEY = "TradingIntelligenceVersionLabel"
CHANNEL_KEY = "TradingIntelligenceBuildChannel"
SOURCE_COMMIT_KEY = "TradingIntelligenceSourceCommit"
DEFAULT_SOURCE_VERSION = "development"
DEFAULT_SOURCE_CHANNEL = "source"


def _clean(value: Any, *, limit: int = 96) -> str:
    text = "".join(
        character
        for character in str(value or "").strip()
        if character.isalnum() or character in ".-_+"
    )
    return text[:limit]


def _clean_commit(value: Any) -> str:
    text = _clean(value, limit=64)
    # Git commit SHAs are hexadecimal, but retain a bounded safe fallback for
    # local development labels rather than failing System Health.
    return text


def bundle_info_path_from_executable(executable: str | Path | None = None) -> Path | None:
    """Return Contents/Info.plist when running from a normal macOS .app bundle."""

    candidate = Path(executable or sys.executable).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError:
        pass
    parent = candidate.parent
    if parent.name != "MacOS" or parent.parent.name != "Contents":
        return None
    info = parent.parent / "Info.plist"
    return info if info.is_file() else None


def stamp_bundle_identity(
    app: str | Path,
    *,
    version_label: str,
    channel: str,
    commit: str = "",
    bundle_short_version: str | None = None,
    bundle_build: str | None = None,
) -> dict[str, str]:
    """Write build metadata before the caller performs its final code signing."""

    app_path = Path(app).expanduser().resolve()
    info = app_path / "Contents" / "Info.plist"
    if not info.is_file():
        raise FileNotFoundError(f"Missing Info.plist: {info}")
    with info.open("rb") as handle:
        data = plistlib.load(handle)
    if not isinstance(data, dict):
        raise ValueError("The macOS Info.plist must contain a dictionary")

    clean_version = _clean(version_label, limit=64) or DEFAULT_SOURCE_VERSION
    clean_channel = _clean(channel, limit=48) or DEFAULT_SOURCE_CHANNEL
    clean_commit = _clean_commit(
        commit
        or os.environ.get("TRADING_INTELLIGENCE_BUILD_COMMIT")
        or os.environ.get("GITHUB_SHA")
    )
    data[VERSION_LABEL_KEY] = clean_version
    data[CHANNEL_KEY] = clean_channel
    if clean_commit:
        data[SOURCE_COMMIT_KEY] = clean_commit
    else:
        data.pop(SOURCE_COMMIT_KEY, None)
    if bundle_short_version is not None:
        data["CFBundleShortVersionString"] = _clean(bundle_short_version, limit=32) or "0.0.0"
    if bundle_build is not None:
        data["CFBundleVersion"] = _clean(bundle_build, limit=32) or "1"
    with info.open("wb") as handle:
        plistlib.dump(data, handle, sort_keys=True)
    return {
        "version_label": clean_version,
        "channel": clean_channel,
        "commit": clean_commit,
        "bundle_short_version": str(data.get("CFBundleShortVersionString") or ""),
        "build_number": str(data.get("CFBundleVersion") or ""),
    }


def read_build_identity(
    *,
    info_plist: str | Path | None = None,
    executable: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Read identity from an installed app, with a safe source-mode fallback."""

    env = environment if environment is not None else os.environ
    info = (
        Path(info_plist).expanduser().resolve()
        if info_plist is not None
        else bundle_info_path_from_executable(executable)
    )
    data: dict[str, Any] = {}
    packaged = bool(info and info.is_file())
    if packaged and info is not None:
        try:
            with info.open("rb") as handle:
                loaded = plistlib.load(handle)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError, plistlib.InvalidFileException):
            data = {}
            packaged = False

    version_label = _clean(
        data.get(VERSION_LABEL_KEY)
        or env.get("TRADING_INTELLIGENCE_BUILD_VERSION")
        or data.get("CFBundleShortVersionString")
        or DEFAULT_SOURCE_VERSION,
        limit=64,
    )
    bundle_version = _clean(data.get("CFBundleShortVersionString"), limit=32)
    build_number = _clean(
        data.get("CFBundleVersion")
        or env.get("TRADING_INTELLIGENCE_BUILD_NUMBER"),
        limit=32,
    )
    channel = _clean(
        data.get(CHANNEL_KEY)
        or env.get("TRADING_INTELLIGENCE_BUILD_CHANNEL")
        or ("packaged_unknown" if packaged else DEFAULT_SOURCE_CHANNEL),
        limit=48,
    )
    commit = _clean_commit(
        data.get(SOURCE_COMMIT_KEY)
        or env.get("TRADING_INTELLIGENCE_BUILD_COMMIT")
        or env.get("GITHUB_SHA")
    )
    return {
        "version": version_label or DEFAULT_SOURCE_VERSION,
        "bundle_short_version": bundle_version,
        "build_number": build_number,
        "channel": channel or DEFAULT_SOURCE_CHANNEL,
        "commit": commit,
        "commit_short": commit[:12],
        "packaged": packaged,
    }
