"""Migration-safe access to the authoritative Trading Intelligence library."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from .desktop_settings import DesktopSettings, load_desktop_settings
from .keychain import KeychainError, KeychainUnavailable, MacOSKeychain
from .security import redact_text, write_private_text_file


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
    token, source = _environment_secret()
    if token:
        return token, source
    try:
        token = MacOSKeychain().get_secret(settings.keychain_account).strip()
    except (KeychainError, KeychainUnavailable):
        return "", ""
    return (token, "macos_keychain") if token else ("", "")


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


def _github_store(
    settings: DesktopSettings,
    *,
    data_dir: Path,
):
    token, credential_source = _github_token(settings)
    if not token:
        raise LibrarySourceError(
            "The desktop app cannot update the private Trading Intelligence library "
            "because no GitHub token is stored in macOS Keychain."
        )
    from youtube_strategy_engine import GitHubCloudBackup, StrategyStore

    cache_directory = data_dir / "library-cache"
    cloud = GitHubCloudBackup(
        settings.github_repository,
        token,
        branch=settings.github_branch,
        path=settings.github_path,
    )
    return StrategyStore(directory=cache_directory, cloud_backup=cloud), token, credential_source


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


def mutate_library_for_job(
    mutation: Callable[[dict[str, Any]], dict[str, Any]],
    payload: Mapping[str, Any] | None = None,
    *,
    data_dir: str | Path | None = None,
) -> LoadedLibrary:
    """Apply a deterministic mutation to the latest authoritative library.

    Production writes always re-read the destination immediately before the
    mutation. This matters for holdout-exposure and cloud-worker reconciliation:
    a long local calculation cannot overwrite or ignore records added while it
    was running.
    """

    request = payload if isinstance(payload, Mapping) else {}
    inline = request.get("library")
    if isinstance(inline, dict):
        changed = mutation(dict(inline))
        if not isinstance(changed, dict):
            raise LibrarySourceError("The library mutation did not return an object.")
        return _loaded(
            changed,
            source="inline_fixture",
            source_detail="In-memory test library; not persisted",
            cloud_refreshed=False,
            warning="Inline fixture mutation was intentionally not persisted.",
        )

    root = _desktop_data_dir(data_dir)
    explicit_path = str(request.get("library_path") or "").strip()
    settings = load_desktop_settings(root)
    local_path = explicit_path or (
        str(settings.local_library_path or "").strip()
        if settings.library_source in {"auto", "local_file"}
        else ""
    )
    if local_path:
        path = Path(local_path).expanduser().resolve()
        latest = _read_json_object(path)
        changed = mutation(dict(latest))
        if not isinstance(changed, dict):
            raise LibrarySourceError("The library mutation did not return an object.")
        write_private_text_file(
            path,
            json.dumps(
                changed,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
                default=str,
            ),
        )
        return _loaded(
            changed,
            source=("explicit_local_file" if explicit_path else "configured_local_file"),
            source_detail=str(path),
            cloud_refreshed=False,
        )

    if settings.library_source not in {"auto", "github_backup"}:
        raise LibrarySourceError("No writable Trading Intelligence library source is configured.")
    store, token, credential_source = _github_store(settings, data_dir=root)
    try:
        latest = store.load_latest()
        changed = mutation(dict(latest))
        if not isinstance(changed, dict):
            raise LibrarySourceError("The library mutation did not return an object.")
        saved = store.save(changed)
        return _loaded(
            saved,
            source="private_github_backup",
            source_detail=(
                f"{settings.github_repository}@{settings.github_branch}:"
                f"{settings.github_path}"
            ),
            cloud_refreshed=True,
            credential_source=credential_source,
        )
    except LibrarySourceError:
        raise
    except Exception as exc:
        message = redact_text(exc, (token,))[:500]
        raise LibrarySourceError(
            "The authoritative Trading Intelligence library could not be updated: "
            f"{type(exc).__name__}: {message}"
        ) from exc


def library_connection_summary(
    payload: Mapping[str, Any] | None = None,
    *,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    loaded = load_library_for_job(payload, data_dir=data_dir)
    return dict(loaded.metadata)
