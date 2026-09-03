"""Migration-safe access to the authoritative Trading Intelligence library."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .desktop_settings import DesktopSettings, load_desktop_settings
from .keychain import KeychainError, KeychainUnavailable, MacOSKeychain
from .security import redact_text


ENV_GITHUB_TOKEN_NAMES = (
    "TRADING_INTELLIGENCE_BACKUP_TOKEN",
    "TRADING_LAB_BACKUP_TOKEN",
    "GITHUB_BACKUP_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
)


class LibrarySourceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LoadedLibrary:
    library: dict[str, Any]
    metadata: dict[str, Any]


def _desktop_data_dir(explicit: str | Path | None = None) -> Path:
    value = str(
        explicit
        or os.environ.get("TRADING_INTELLIGENCE_DESKTOP_DATA_DIR")
        or (
            Path.home()
            / "Library"
            / "Application Support"
            / "Trading Intelligence Lab"
        )
    )
    path = Path(value).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LibrarySourceError(f"The library file does not exist: {path}") from exc
    except OSError as exc:
        raise LibrarySourceError(f"The library file could not be read: {path}") from exc
    except json.JSONDecodeError as exc:
        raise LibrarySourceError(f"The library file is not valid JSON: {path}") from exc
    if not isinstance(decoded, dict):
        raise LibrarySourceError("The Trading Intelligence library must contain one JSON object.")
    return decoded


def _environment_secret() -> tuple[str, str]:
    for name in ENV_GITHUB_TOKEN_NAMES:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value, f"environment:{name}"
    return "", ""


def _github_token(settings: DesktopSettings) -> tuple[str, str]:
    # The desktop settings and health screens validate the Keychain account, so
    # desktop library jobs must use that same credential when it is available.
    # Generic GITHUB_TOKEN/GH_TOKEN values can be inherited from a launcher or
    # development shell and may only have access to the public application repo.
    try:
        token = MacOSKeychain().get_secret(settings.keychain_account).strip()
    except (KeychainError, KeychainUnavailable):
        token = ""
    if token:
        return token, "macos_keychain"
    return _environment_secret()


def _library_counts(library: Mapping[str, Any]) -> dict[str, int]:
    fields = (
        "strategies",
        "validation_runs",
        "research_queue",
        "predictive_ml_runs",
        "knowledge_sources",
        "finder_runs",
    )
    return {
        field: len(library.get(field) or [])
        if isinstance(library.get(field), list)
        else 0
        for field in fields
    }


def _loaded(
    library: dict[str, Any],
    *,
    source: str,
    source_detail: str,
    cloud_refreshed: bool,
    warning: str = "",
    credential_source: str = "",
) -> LoadedLibrary:
    metadata: dict[str, Any] = {
        "source": source,
        "source_detail": source_detail,
        "cloud_refreshed": bool(cloud_refreshed),
        "warning": str(warning or ""),
        "credential_source": str(credential_source or ""),
        **_library_counts(library),
    }
    return LoadedLibrary(library=dict(library), metadata=metadata)


def _inline_library(payload: Mapping[str, Any]) -> LoadedLibrary | None:
    inline = payload.get("library")
    if not isinstance(inline, dict):
        return None
    return _loaded(
        dict(inline),
        source="inline_fixture",
        source_detail="Explicit library supplied to the local job",
        cloud_refreshed=False,
    )


def _explicit_local_library(payload: Mapping[str, Any]) -> LoadedLibrary | None:
    raw_path = str(payload.get("library_path") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path).expanduser().resolve()
    return _loaded(
        _read_json_object(path),
        source="explicit_local_file",
        source_detail=str(path),
        cloud_refreshed=False,
    )


def _configured_local_library(settings: DesktopSettings) -> LoadedLibrary | None:
    raw_path = str(settings.local_library_path or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        if settings.library_source == "local_file":
            raise LibrarySourceError(
                "The configured local Trading Intelligence library does not exist."
            )
        return None
    return _loaded(
        _read_json_object(path),
        source="configured_local_file",
        source_detail=str(path),
        cloud_refreshed=False,
    )


def _load_github_backup(
    settings: DesktopSettings,
    *,
    data_dir: Path,
) -> LoadedLibrary:
    token, credential_source = _github_token(settings)
    cache_directory = data_dir / "library-cache"
    cache_path = cache_directory / "strategy_library.json"
    if not token:
        if cache_path.is_file():
            return _loaded(
                _read_json_object(cache_path),
                source="local_cache",
                source_detail=str(cache_path),
                cloud_refreshed=False,
                warning=(
                    "The private GitHub credential is unavailable. Showing the last "
                    "local cache; add the token in Connection Settings to refresh it."
                ),
            )
        raise LibrarySourceError(
            "The desktop app cannot refresh the private Trading Intelligence library "
            "because no GitHub token is stored in macOS Keychain."
        )

    # Import lazily so the desktop settings and offline screens remain fast and
    # independently testable. These are the same reconciliation classes used by
    # the existing Streamlit application, not a second library implementation.
    from youtube_strategy_engine import GitHubCloudBackup, StrategyStore

    cloud = GitHubCloudBackup(
        settings.github_repository,
        token,
        branch=settings.github_branch,
        path=settings.github_path,
    )
    store = StrategyStore(directory=cache_directory, cloud_backup=cloud)
    try:
        library = store.load_latest()
        return _loaded(
            library,
            source="private_github_backup",
            source_detail=(
                f"{settings.github_repository}@{settings.github_branch}:"
                f"{settings.github_path}"
            ),
            cloud_refreshed=True,
            credential_source=credential_source,
        )
    except Exception as exc:
        # load_latest may have refreshed the cache before a later reconciliation
        # error. Prefer its local copy only when it is still valid JSON, and make
        # the degraded state explicit.
        if store.path.is_file():
            try:
                cached = _read_json_object(store.path)
            except LibrarySourceError:
                cached = None
            if cached is not None:
                return _loaded(
                    cached,
                    source="local_cache_after_cloud_error",
                    source_detail=str(store.path),
                    cloud_refreshed=False,
                    warning=(
                        "The private GitHub refresh reported "
                        f"{type(exc).__name__}: {redact_text(exc, (token,))[:240]}"
                    ),
                    credential_source=credential_source,
                )
        message = redact_text(exc, (token,))[:500]
        raise LibrarySourceError(
            "The private GitHub library could not be loaded: "
            f"{type(exc).__name__}: {message}"
        ) from exc


def load_library_for_job(
    payload: Mapping[str, Any] | None = None,
    *,
    data_dir: str | Path | None = None,
) -> LoadedLibrary:
    """Resolve one authoritative library without putting credentials in the job."""

    request = payload if isinstance(payload, Mapping) else {}
    inline = _inline_library(request)
    if inline is not None:
        return inline
    explicit = _explicit_local_library(request)
    if explicit is not None:
        return explicit

    root = _desktop_data_dir(data_dir)
    settings = load_desktop_settings(root)
    if settings.library_source in {"auto", "local_file"}:
        local = _configured_local_library(settings)
        if local is not None:
            return local
    if settings.library_source in {"auto", "github_backup"}:
        return _load_github_backup(settings, data_dir=root)
    raise LibrarySourceError("No Trading Intelligence library source is configured.")


def library_connection_summary(
    payload: Mapping[str, Any] | None = None,
    *,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    loaded = load_library_for_job(payload, data_dir=data_dir)
    return dict(loaded.metadata)


def load_strategy_lab_checkpoint_library(
    *,
    data_dir: str | Path | None = None,
) -> LoadedLibrary:
    """Load the small durable Strategy Lab checkpoint without touching job secrets."""

    from .strategy_lab_bridge import STRATEGY_LAB_CHECKPOINT_PATH

    root = _desktop_data_dir(data_dir)
    settings = load_desktop_settings(root)
    token, credential_source = _github_token(settings)
    cache_directory = root / "strategy-lab-checkpoint-cache"
    cache_path = cache_directory / "strategy_library.json"
    if not token:
        if cache_path.is_file():
            return _loaded(
                _read_json_object(cache_path),
                source="strategy_lab_checkpoint_cache",
                source_detail=str(cache_path),
                cloud_refreshed=False,
                warning="Strategy Lab checkpoint is from the last local cache.",
            )
        return _loaded(
            {"validation_runs": []},
            source="strategy_lab_checkpoint_unavailable",
            source_detail=STRATEGY_LAB_CHECKPOINT_PATH,
            cloud_refreshed=False,
            warning="Strategy Lab checkpoint is unavailable until the GitHub connection is configured.",
        )

    from youtube_strategy_engine import GitHubCloudBackup, StrategyStore

    cloud = GitHubCloudBackup(
        settings.github_repository,
        token,
        branch=settings.github_branch,
        path=STRATEGY_LAB_CHECKPOINT_PATH,
    )
    store = StrategyStore(directory=cache_directory, cloud_backup=cloud)
    try:
        library = store.load_latest()
        return _loaded(
            library,
            source="private_strategy_lab_checkpoint",
            source_detail=(
                f"{settings.github_repository}@{settings.github_branch}:"
                f"{STRATEGY_LAB_CHECKPOINT_PATH}"
            ),
            cloud_refreshed=True,
            credential_source=credential_source,
        )
    except Exception as exc:
        if store.path.is_file():
            try:
                cached = _read_json_object(store.path)
            except LibrarySourceError:
                cached = None
            if cached is not None:
                return _loaded(
                    cached,
                    source="strategy_lab_checkpoint_cache_after_cloud_error",
                    source_detail=str(store.path),
                    cloud_refreshed=False,
                    warning=redact_text(exc, (token,))[:240],
                    credential_source=credential_source,
                )
        return _loaded(
            {"validation_runs": []},
            source="strategy_lab_checkpoint_error",
            source_detail=STRATEGY_LAB_CHECKPOINT_PATH,
            cloud_refreshed=False,
            warning=redact_text(exc, (token,))[:240],
            credential_source=credential_source,
        )
