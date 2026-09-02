"""Lightweight, presence-only first-run setup state for the desktop UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .desktop_settings import (
    ALPACA_API_KEY_ACCOUNT,
    ALPACA_SECRET_KEY_ACCOUNT,
    load_desktop_settings,
)
from .keychain import KeychainError, KeychainUnavailable, MacOSKeychain


def _secret_present(keychain: MacOSKeychain, account: str) -> bool:
    try:
        return bool(keychain.get_secret(account).strip())
    except (KeychainError, KeychainUnavailable, ValueError):
        return False


def configuration_status(
    data_dir: str | Path,
    *,
    keychain: MacOSKeychain | None = None,
) -> dict[str, Any]:
    """Return presence-only readiness without importing trading engines."""

    root = Path(data_dir).expanduser().resolve()
    settings = load_desktop_settings(root)
    secrets = keychain or MacOSKeychain()
    github_present = _secret_present(secrets, settings.keychain_account)
    alpaca_key_present = _secret_present(secrets, ALPACA_API_KEY_ACCOUNT)
    alpaca_secret_present = _secret_present(secrets, ALPACA_SECRET_KEY_ACCOUNT)
    local_path = Path(settings.local_library_path).expanduser() if settings.local_library_path else None
    local_ready = bool(local_path and local_path.is_file())
    github_target_ready = bool(settings.github_repository and settings.github_path)
    if settings.library_source == "local_file":
        library_configured = local_ready
    elif settings.library_source == "github_backup":
        library_configured = bool(github_target_ready and github_present)
    else:
        library_configured = bool(local_ready or (github_target_ready and github_present))
    cloud_configured = bool(github_target_ready and github_present)
    market_configured = bool(alpaca_key_present and alpaca_secret_present)
    return {
        "library_configured": library_configured,
        "cloud_configured": cloud_configured,
        "market_configured": market_configured,
        "full_configured": bool(library_configured and cloud_configured and market_configured),
        "library_source": settings.library_source,
        "market_feed": settings.market_feed,
        "local_library_exists": local_ready,
        "github_credential_present": github_present,
        "alpaca_api_key_present": alpaca_key_present,
        "alpaca_secret_key_present": alpaca_secret_present,
        "research_only": True,
        "affects_execution": False,
    }
