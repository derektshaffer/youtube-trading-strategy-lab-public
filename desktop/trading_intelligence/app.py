"""Production Trading Intelligence desktop entrypoint."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


# Running the source file directly and running a frozen PyInstaller entrypoint
# need the repository/package root available before importing shared contracts.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from desktop.trading_intelligence.runtime import DesktopRuntime
from desktop.trading_intelligence.ui import run_gui


def default_data_dir() -> Path:
    configured = str(os.environ.get("TRADING_INTELLIGENCE_DESKTOP_DATA_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "Trading Intelligence Lab"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trading Intelligence desktop")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--metrics-output", default="")
    parser.add_argument("--data-dir", default=str(default_data_dir()))
    parser.add_argument(
        "--library-fixture",
        default="",
        help="Development/CI only: local JSON library used without production secrets.",
    )
    return parser


def configure_fixture(data_dir: Path, fixture: str) -> None:
    raw = str(fixture or "").strip()
    if not raw:
        return
    from hybrid_runtime.desktop_settings import DesktopSettings, save_desktop_settings

    path = Path(raw).expanduser().resolve()
    decoded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise SystemExit("--library-fixture must contain one JSON object")
    save_desktop_settings(
        DesktopSettings(
            library_source="local_file",
            local_library_path=str(path),
            refresh_on_launch=True,
        ),
        data_dir,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = Path(args.data_dir).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    configure_fixture(data_dir, args.library_fixture)
    runtime = DesktopRuntime(data_dir=data_dir)
    return run_gui(
        runtime,
        smoke=bool(args.smoke),
        metrics_output=str(args.metrics_output or ""),
    )


if __name__ == "__main__":
    raise SystemExit(main())
