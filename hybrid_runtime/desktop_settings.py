"""Non-secret desktop configuration stored separately from credentials."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .security import write_private_text_file


SETTINGS_FILENAME = "desktop-settings.json"
SETTINGS_VERSION = 1
DEFAULT_PRIVATE_BACKUP_REPOSITORY = (
    "derektshaffer/derektshaffer-youtube-trading-strategy-lab"
)
DEFAULT_GITHUB_BRANCH = "main"
DEFAULT_GITHUB_LIBRARY_PATH = "trading-intelligence-lab/intelligence_library.json"
GITHUB_BACKUP_TOKEN_ACCOUNT = "github-backup-token"
ALPACA_API_KEY_ACCOUNT = "alpaca-api-key"
ALPACA_SECRET_KEY_ACCOUNT = "alpaca-secret-key"
VALID_LIBRARY_SOURCES = frozenset({"auto", "local_file", "github_backup"})
VALID_MARKET_FEEDS = frozenset({"sip", "iex"})
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


class DesktopSettingsError(RuntimeError):
    pass


def _clean_text(value: Any, *, maximum: int) -> str:
    return str(value or "").strip()[:maximum]


def _clean_repository(value: Any) -> str:
    repository = _clean_text(
        value or DEFAULT_PRIVATE_BACKUP_REPOSITORY,
        maximum=180,
    )
    if not _REPOSITORY_PATTERN.fullmatch(repository):
        raise DesktopSettingsError(
            "The private backup repository must use the owner/name format."
        )
    return repository


def _clean_branch(value: Any) -> str:
    branch = _clean_text(value or DEFAULT_GITHUB_BRANCH, maximum=200)
    if (
        not _BRANCH_PATTERN.fullmatch(branch)
        or ".." in branch
        or branch.endswith("/")
        or "//" in branch
    ):
        raise DesktopSettingsError("The configured GitHub branch is invalid.")
    return branch


def _clean_github_path(value: Any) -> str:
    path = _clean_text(value or DEFAULT_GITHUB_LIBRARY_PATH, maximum=600)
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    if (
        not parts
        or any(part in {".", ".."} for part in parts)
        or path.startswith("/")
        or not parts[-1].lower().endswith(".json")
    ):
        raise DesktopSettingsError(
            "The private GitHub library path must be a relative JSON path."
        )
    return "/".join(parts)


def _clean_local_path(value: Any) -> str:
    text = _clean_text(value, maximum=2_000)
    return str(Path(text).expanduser()) if text else ""


@dataclass(frozen=True, slots=True)
class DesktopSettings:
    settings_version: int = SETTINGS_VERSION
    library_source: str = "auto"
    local_library_path: str = ""
    github_repository: str = DEFAULT_PRIVATE_BACKUP_REPOSITORY
    github_branch: str = DEFAULT_GITHUB_BRANCH
    github_path: str = DEFAULT_GITHUB_LIBRARY_PATH
    keychain_account: str = GITHUB_BACKUP_TOKEN_ACCOUNT
    market_feed: str = "sip"
    refresh_on_launch: bool = True

    def __post_init__(self) -> None:
        source = _clean_text(self.library_source or "auto", maximum=40).lower()
        if source not in VALID_LIBRARY_SOURCES:
            raise DesktopSettingsError(
                "library_source must be auto, local_file, or github_backup."
            )
        account = _clean_text(
            self.keychain_account or GITHUB_BACKUP_TOKEN_ACCOUNT,
            maximum=160,
        )
        if not account:
            raise DesktopSettingsError("A macOS Keychain account name is required.")
        market_feed = _clean_text(self.market_feed or "sip", maximum=20).lower()
        if market_feed not in VALID_MARKET_FEEDS:
            raise DesktopSettingsError("market_feed must be sip or iex.")
        object.__setattr__(self, "settings_version", SETTINGS_VERSION)
        object.__setattr__(self, "library_source", source)
        object.__setattr__(
            self,
            "local_library_path",
            _clean_local_path(self.local_library_path),
        )
        object.__setattr__(
            self,
            "github_repository",
            _clean_repository(self.github_repository),
        )
        object.__setattr__(
            self,
            "github_branch",
            _clean_branch(self.github_branch),
        )
        object.__setattr__(self, "github_path", _clean_github_path(self.github_path))
        object.__setattr__(self, "keychain_account", account)
        object.__setattr__(self, "market_feed", market_feed)
        object.__setattr__(self, "refresh_on_launch", bool(self.refresh_on_launch))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "DesktopSettings":
        data = raw if isinstance(raw, Mapping) else {}
        return cls(
            library_source=data.get("library_source") or "auto",
            local_library_path=data.get("local_library_path") or "",
            github_repository=(
                data.get("github_repository")
                or DEFAULT_PRIVATE_BACKUP_REPOSITORY
            ),
            github_branch=data.get("github_branch") or DEFAULT_GITHUB_BRANCH,
            github_path=data.get("github_path") or DEFAULT_GITHUB_LIBRARY_PATH,
            keychain_account=(
                data.get("keychain_account") or GITHUB_BACKUP_TOKEN_ACCOUNT
            ),
            market_feed=data.get("market_feed") or "sip",
            refresh_on_launch=(
                True
                if data.get("refresh_on_launch") is None
                else bool(data.get("refresh_on_launch"))
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return only non-secret configuration suitable for the settings file."""
        return asdict(self)


def settings_path(data_dir: str | Path) -> Path:
    return Path(data_dir).expanduser().resolve() / SETTINGS_FILENAME


def load_desktop_settings(data_dir: str | Path) -> DesktopSettings:
    path = settings_path(data_dir)
    if not path.is_file():
        return DesktopSettings()
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DesktopSettingsError(
            "The desktop settings file is unreadable or invalid JSON."
        ) from exc
    if not isinstance(decoded, dict):
        raise DesktopSettingsError("The desktop settings file must contain one object.")
    return DesktopSettings.from_mapping(decoded)


def save_desktop_settings(
    settings: DesktopSettings | Mapping[str, Any],
    data_dir: str | Path,
) -> Path:
    normalized = (
        settings
        if isinstance(settings, DesktopSettings)
        else DesktopSettings.from_mapping(settings)
    )
    encoded = json.dumps(
        normalized.as_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return write_private_text_file(settings_path(data_dir), encoded)
