"""First-run desktop setup verification with secrets confined to macOS Keychain."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .desktop_settings import (
    ALPACA_API_KEY_ACCOUNT,
    ALPACA_SECRET_KEY_ACCOUNT,
    DesktopSettings,
    load_desktop_settings,
)
from .keychain import KeychainError, KeychainUnavailable, MacOSKeychain
from .onboarding_state import configuration_status


UTC = timezone.utc
ProgressCallback = Callable[[float, str, str], None]
CancellationCheck = Callable[[], bool]
CLOUD_ACTION_REPOSITORY = "derektshaffer/youtube-trading-strategy-lab-public"
CLOUD_PERMISSION_WORKFLOW = "desktop-cloud-credential-smoke.yml"
CLOUD_WORKFLOW_REF = "main"


class OnboardingError(RuntimeError):
    pass


def _workflow_dispatch_access(token: str) -> dict[str, Any]:
    """Run one harmless no-secret Actions handshake to prove dispatch permission.

    The desktop cloud bridge publishes real Finder/Strategy Lab queue items and
    then dispatches GitHub Actions. Repository write permission alone is not
    enough for fine-grained PATs; GitHub requires Actions: write for workflow
    dispatch. This dedicated workflow only echoes a constant string on an Ubuntu
    runner and cannot read secrets or mutate trading/research state.
    """

    workflow = quote(CLOUD_PERMISSION_WORKFLOW, safe="")
    request = Request(
        (
            "https://api.github.com/repos/"
            f"{CLOUD_ACTION_REPOSITORY}/actions/workflows/{workflow}/dispatches"
        ),
        data=json.dumps({"ref": CLOUD_WORKFLOW_REF}, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Trading-Intelligence-Desktop-Onboarding",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            status = int(getattr(response, "status", 204) or 204)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise OnboardingError(
                "The GitHub token can write the private library but cannot start cloud workflows. "
                "For a fine-grained token, add Actions: read/write permission on the Trading Intelligence app repository."
            ) from exc
        if exc.code == 404:
            raise OnboardingError(
                "The cloud-dispatch verification workflow is unavailable to this GitHub token. "
                "Make sure the token can access the Trading Intelligence app repository."
            ) from exc
        raise OnboardingError(
            f"GitHub cloud-workflow dispatch check failed with HTTP {exc.code}."
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise OnboardingError(
            "GitHub could not be reached for the cloud-workflow dispatch check."
        ) from exc
    # The 2022-11-28 API returns 204; newer GitHub API variants may return 200
    # with run metadata. Accept either successful shape.
    if status not in {200, 204}:
        raise OnboardingError(
            f"GitHub cloud-workflow dispatch check returned HTTP {status}."
        )
    return {
        "ready": True,
        "action_repository": CLOUD_ACTION_REPOSITORY,
        "workflow": CLOUD_PERMISSION_WORKFLOW,
        "message": "GitHub Actions cloud-dispatch permission verified.",
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
            raw = response.read(1_000_000)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise OnboardingError(
                "GitHub rejected the token. Use a token with read/write access to the private Trading Intelligence backup repository."
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
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OnboardingError("GitHub returned an unreadable repository-access response.") from exc
    permissions = decoded.get("permissions") if isinstance(decoded, dict) else None
    can_write = bool(
        isinstance(permissions, dict)
        and (
            permissions.get("push")
            or permissions.get("maintain")
            or permissions.get("admin")
        )
    )
    if not can_write:
        raise OnboardingError(
            "The GitHub token can see the repository but does not have write access. "
            "Cloud Finder and Strategy Lab need repository contents read/write permission."
        )
    dispatch = _workflow_dispatch_access(token)
    return {
        "ready": True,
        "repository": settings.github_repository,
        "branch": settings.github_branch,
        "path": settings.github_path,
        "write_access": True,
        "workflow_dispatch": bool(dispatch.get("ready")),
        "action_repository": dispatch.get("action_repository"),
        "message": "Private GitHub queue write + cloud workflow dispatch verified.",
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

    # This module is loaded by the packaged sidecar. Keep the lightweight GUI
    # path in onboarding_state.py so the desktop executable does not import the
    # trading engine merely to decide whether first-run setup is needed.
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
        progress(0.08, "downloading_data", "Checking saved desktop configuration")

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
        progress(0.48, "downloading_data", "Verifying private GitHub queue write and cloud dispatch")
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
