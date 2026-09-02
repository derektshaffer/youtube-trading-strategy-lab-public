"""First-run desktop setup checks with secrets confined to macOS Keychain."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .desktop_settings import (
    ALPACA_API_KEY_ACCOUNT,
    ALPACA_SECRET_KEY_ACCOUNT,
    DesktopSettings,
    load_desktop_settings,
)
from .keychain import KeychainError, KeychainUnavailable, MacOSKeychain


UTC = timezone.utc
ProgressCallback = Callable[[float, str, str], None]
CancellationCheck = Callable[[], bool]


class OnboardingError(RuntimeError):
    pass


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
    """Return presence-only first-run readiness; never return secret values."""

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


def _github_access(settings: DesktopSettings, keychain: MacOSKeychain) -> dict[str, Any]:
    try:
        token = keychain.get_secret(settings.keychain_account).strip()
    except (KeychainError, KeychainUnavailable) as exc:
        raise OnboardingError("Add the private GitHub token to enable cloud research.") from exc
    if not token:
        raise OnboardingError("Add the private GitHub token to enable cloud research.")
    request = Request(
        f"https://api.github.com/repos/{settings.github_repository}",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Trading-Intelligence-Desktop-Onboarding",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=15) as response:
            status = int(getattr(response, "status", 200) or 200)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise OnboardingError(
                "GitHub rejected the token. Use a token that can read the private Trading Intelligence backup repository."
            ) from exc
        if exc.code == 404:
            raise OnboardingError(
                "GitHub could not access the configured backup repository with this token."
            ) from exc
        raise OnboardingError(f"GitHub access check failed with HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise OnboardingError("GitHub could not be reached for the cloud-access check.") from exc
    if status < 200 or status >= 300:
        raise OnboardingError(f"GitHub access check returned HTTP {status}.")
    return {
        "ready": True,
        "repository": settings.github_repository,
        "branch": settings.github_branch,
        "path": settings.github_path,
        "message": "Private GitHub/cloud access verified.",
    }


def _library_access(settings: DesktopSettings, data_dir: Path) -> dict[str, Any]:
    from .library_source import load_library_for_job

    loaded = load_library_for_job({}, data_dir=data_dir)
    metadata = dict(loaded.metadata)
    source = str(metadata.get("source") or "unknown")
    warning = str(metadata.get("warning") or "").strip()
    return {
        "ready": True,
        "source": source,
        "strategies": int(metadata.get("strategies") or 0),
        "validation_runs": int(metadata.get("validation_runs") or 0),
        "cloud_refreshed": bool(metadata.get("cloud_refreshed")),
        "message": (
            f"Research library verified from {source.replace('_', ' ')}."
            + (" " + warning if warning else "")
        ),
    }


def _market_access(settings: DesktopSettings, keychain: MacOSKeychain) -> dict[str, Any]:
    try:
        api_key = keychain.get_secret(ALPACA_API_KEY_ACCOUNT).strip()
        secret_key = keychain.get_secret(ALPACA_SECRET_KEY_ACCOUNT).strip()
    except (KeychainError, KeychainUnavailable) as exc:
        raise OnboardingError("Add both Alpaca credentials to enable real market data.") from exc
    if not api_key or not secret_key:
        raise OnboardingError("Add both Alpaca credentials to enable real market data.")

    from youtube_strategy_engine import AlpacaMarketData

    provider = AlpacaMarketData(
        api_key,
        secret_key,
        live_feed=settings.market_feed,
        historical_feed=settings.market_feed,
    )
    end = datetime.now(UTC)
    start = end - timedelta(days=7)
    try:
        response = provider.bars(
            ["AAPL"],
            start=start,
            end=end,
            timeframe="1Day",
            feed=settings.market_feed,
            adjustment="split",
            max_pages=2,
        )
    except Exception as exc:
        # Provider errors are already credential/feed focused. Bound the text so
        # no backend detail can turn into a large or secret-bearing UI payload.
        message = " ".join(str(exc).split())[:400]
        raise OnboardingError(
            "Alpaca market-data verification failed for the selected "
            f"{settings.market_feed.upper()} feed. {message}"
        ) from exc
    rows = response.get("AAPL") if isinstance(response, Mapping) else None
    count = len(rows or [])
    if count <= 0:
        raise OnboardingError(
            "Alpaca accepted the request but returned no AAPL daily bars. Check the selected feed and account permissions."
        )
    return {
        "ready": True,
        "feed": settings.market_feed,
        "probe_symbol": "AAPL",
        "bars": count,
        "message": f"Alpaca {settings.market_feed.upper()} market-data access verified.",
    }


def verify_setup(
    data_dir: str | Path,
    *,
    progress: ProgressCallback | None = None,
    cancelled: CancellationCheck | None = None,
    keychain: MacOSKeychain | None = None,
) -> dict[str, Any]:
    """Verify library, cloud, and market-data access without exposing secrets."""

    root = Path(data_dir).expanduser().resolve()
    settings = load_desktop_settings(root)
    secrets = keychain or MacOSKeychain()

    def check_cancelled() -> None:
        if cancelled and cancelled():
            raise OnboardingError("Setup verification was cancelled.")

    results: dict[str, dict[str, Any]] = {}
    check_cancelled()
    if progress:
        progress(0.08, "preparing_features", "Checking saved desktop configuration")

    if progress:
        progress(0.18, "downloading_data", "Verifying the research library")
    try:
        results["library"] = _library_access(settings, root)
    except Exception as exc:
        results["library"] = {
            "ready": False,
            "message": " ".join(str(exc).split())[:500] or "Research library verification failed.",
        }

    check_cancelled()
    if progress:
        progress(0.48, "downloading_data", "Verifying private GitHub/cloud access")
    try:
        results["cloud"] = _github_access(settings, secrets)
    except Exception as exc:
        results["cloud"] = {
            "ready": False,
            "message": " ".join(str(exc).split())[:500] or "Cloud access verification failed.",
        }

    check_cancelled()
    if progress:
        progress(0.7, "downloading_data", f"Verifying Alpaca {settings.market_feed.upper()} market data")
    try:
        results["market"] = _market_access(settings, secrets)
    except Exception as exc:
        results["market"] = {
            "ready": False,
            "feed": settings.market_feed,
            "message": " ".join(str(exc).split())[:500] or "Market-data verification failed.",
        }

    check_cancelled()
    if progress:
        progress(0.94, "saving", "Preparing setup readiness")
    ready = all(bool((results.get(name) or {}).get("ready")) for name in ("library", "cloud", "market"))
    return {
        "status": "ready" if ready else "attention",
        "ready": ready,
        "checks": results,
        "configuration": configuration_status(root, keychain=secrets),
        "research_only": True,
        "affects_live_ranking": False,
        "affects_execution": False,
    }
