"""Persist a safe launcher target for the separately maintained Momentum Scanner."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .security import write_private_text_file


SCANNER_LAUNCHER_FILENAME = "momentum-scanner-launcher.json"


def normalize_scanner_target(value: Any) -> str:
    target = str(value or "").strip()
    if not target:
        return ""
    parsed = urlparse(target)
    if parsed.scheme:
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("The Momentum Scanner web address must use http or https.")
        return target
    path = Path(target).expanduser().resolve()
    if not path.exists():
        raise ValueError("The selected Momentum Scanner app or launcher does not exist.")
    if path.suffix.lower() not in {".app", ".command"}:
        raise ValueError("Choose a macOS .app or .command launcher.")
    return str(path)


def launcher_path(data_dir: str | Path) -> Path:
    return Path(data_dir).expanduser().resolve() / SCANNER_LAUNCHER_FILENAME


def discover_scanner_target(data_dir: str | Path) -> str:
    configured = launcher_path(data_dir)
    if configured.is_file():
        try:
            decoded = json.loads(configured.read_text(encoding="utf-8"))
            target = normalize_scanner_target((decoded or {}).get("target"))
            if target:
                return target
        except (OSError, ValueError, json.JSONDecodeError, AttributeError):
            pass
    environment_target = str(os.environ.get("MOMENTUM_SCANNER_URL") or "").strip()
    if environment_target:
        try:
            return normalize_scanner_target(environment_target)
        except ValueError:
            pass
    home = Path.home()
    for candidate in (
        Path("/Applications/Momentum Scanner.app"),
        home / "Applications" / "Momentum Scanner.app",
        Path("/Applications/Stock Scanner.app"),
        home / "Applications" / "Stock Scanner.app",
    ):
        if candidate.exists():
            return str(candidate.resolve())
    return ""


def save_scanner_target(data_dir: str | Path, target: Any) -> str:
    normalized = normalize_scanner_target(target)
    if not normalized:
        raise ValueError("Enter the Momentum Scanner web address or choose its local app.")
    payload = json.dumps(
        {"schema_version": 1, "target": normalized},
        sort_keys=True,
        separators=(",", ":"),
    )
    write_private_text_file(launcher_path(data_dir), payload)
    return normalized


__all__ = [
    "SCANNER_LAUNCHER_FILENAME",
    "discover_scanner_target",
    "normalize_scanner_target",
    "save_scanner_target",
]
