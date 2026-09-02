"""Lightweight first-run setup state for the desktop UI."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .desktop_settings import (
    ALPACA_API_KEY_ACCOUNT,
    ALPACA_SECRET_KEY_ACCOUNT,
    load_desktop_settings,
    settings_path,
)
from .keychain import KeychainError, KeychainUnavailable, MacOSKeychain
from .security import write_private_text_file


UTC = timezone.utc
VERIFICATION_FILENAME = "desktop-onboarding-verification.json"
VERIFICATION_SCHEMA = 1


def _secret_present(keychain: MacOSKeychain, account: str) -> bool:
    try:
        return bool(keychain.get_secret(account).strip())
    except (KeychainError, KeychainUnavailable, ValueError):
        return False


def verification_path(data_dir: str | Path) -> Path:
    return Path(data_dir).expanduser().resolve() / VERIFICATION_FILENAME


def _configuration_fingerprint(data_dir: str | Path) -> str:
    settings = load_desktop_settings(data_dir)
    material = {
        "library_source": settings.library_source,
        "local_library_path": settings.local_library_path,
        "github_repository": settings.github_repository,
        "github_branch": settings.github_branch,
        "github_path": settings.github_path,
        "keychain_account": settings.keychain_account,
        "market_feed": settings.market_feed,
    }
    encoded = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_verification(data_dir: str | Path) -> dict[str, Any]:
    path = verification_path(data_dir)
    if not path.is_file():
        return {}
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "pending", "reason": "unreadable_verification_state"}
    return dict(decoded) if isinstance(decoded, dict) else {"status": "pending"}


def _write_verification(data_dir: str | Path, status: str) -> Path:
    clean_status = str(status or "pending").strip().lower()
    if clean_status not in {"pending", "verified"}:
        raise ValueError("setup verification status must be pending or verified")
    payload = {
        "schema": VERIFICATION_SCHEMA,
        "status": clean_status,
        "configuration_fingerprint": _configuration_fingerprint(data_dir),
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return write_private_text_file(verification_path(data_dir), encoded)


def mark_setup_pending(data_dir: str | Path) -> Path:
    """Require Setup again after any saved connection/configuration change."""

    return _write_verification(data_dir, "pending")


def mark_setup_verified(data_dir: str | Path) -> Path:
    """Persist only a non-secret fingerprint after all live setup probes pass."""

    return _write_verification(data_dir, "verified")


def configuration_status(
    data_dir: str | Path,
    *,
    keychain: MacOSKeychain | None = None,
) -> dict[str, Any]:
    """Return presence + verification readiness without importing trading engines."""

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
    full_configured = bool(library_configured and cloud_configured and market_configured)

    stored = _load_verification(root)
    stored_status = str(stored.get("status") or "").strip().lower()
    stored_fingerprint = str(stored.get("configuration_fingerprint") or "").strip()
    current_fingerprint = _configuration_fingerprint(root)
    fingerprint_matches = bool(
        stored_fingerprint and stored_fingerprint == current_fingerprint
    )
    has_settings_file = settings_path(root).is_file()

    if stored_status == "verified" and fingerprint_matches:
        setup_verification = "verified"
    elif stored_status:
        # Pending state survives restarts. A fingerprint mismatch also means the
        # previously verified configuration changed and must be verified again.
        setup_verification = "pending"
    elif has_settings_file and full_configured:
        # One-time compatibility path for people who configured an older desktop
        # beta before the onboarding-verification file existed.
        setup_verification = "legacy"
    else:
        setup_verification = "missing"

    launch_ready = bool(
        full_configured and setup_verification in {"verified", "legacy"}
    )
    return {
        "library_configured": library_configured,
        "cloud_configured": cloud_configured,
        "market_configured": market_configured,
        "full_configured": full_configured,
        "launch_ready": launch_ready,
        "setup_verification": setup_verification,
        "library_source": settings.library_source,
        "market_feed": settings.market_feed,
        "local_library_exists": local_ready,
        "github_credential_present": github_present,
        "alpaca_api_key_present": alpaca_key_present,
        "alpaca_secret_key_present": alpaca_secret_present,
        "research_only": True,
        "affects_execution": False,
    }
