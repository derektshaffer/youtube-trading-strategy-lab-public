"""Run the production desktop shell and Python engine directly from this checkout."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import signal
import sys

from .runtime import DesktopRuntime


ROOT = Path(__file__).resolve().parents[2]
DEV_NAME = "Trading Lab Dev"


class SourceRuntime(DesktopRuntime):
    is_development = True

    def _command(self) -> list[str]:
        # Explicitly bypass packaged-sidecar discovery, including env overrides.
        return [
            sys.executable, "-m", "hybrid_runtime.server",
            "--host", "127.0.0.1", "--port", str(self.port),
            "--data-dir", str(self.data_dir),
        ]


def record_launch(window: object, runtime: SourceRuntime) -> None:
    """Non-secret provenance for diagnosing which checkout Finder opened."""
    evidence = {
        "source_root": str(ROOT),
        "python": sys.executable,
        "ui_source": str(Path(__file__).resolve()),
        "engine_source": str(ROOT / "hybrid_runtime" / "server.py"),
        "ui_pid": os.getpid(),
        "engine_pid": runtime.process.pid,
        "window_title": window.windowTitle(),
        "window_visible": window.isVisible(),
    }
    window.metrics.update(evidence)
    (runtime.data_dir / "dev-launch.json").write_text(json.dumps(evidence, indent=2))


def seed_settings(stable: Path, development: Path) -> None:
    # Copy only configuration, never databases, queued jobs, runtime tokens,
    # caches, or a previous launch's verification status.
    for name in ("desktop-settings.json", "momentum-scanner-launcher.json"):
        source, target = stable / name, development / name
        if source.is_file() and not target.exists():
            with target.open("x", encoding="utf-8") as output:
                output.write(source.read_text(encoding="utf-8"))
            target.chmod(0o600)


def main(argv: list[str] | None = None) -> int:
    if getattr(sys, "frozen", False):
        raise RuntimeError("Development mode must run from source")
    os.chdir(ROOT)
    os.umask(0o077)
    os.environ["TRADING_INTELLIGENCE_DEV_MODE"] = "1"
    from .qt_startup import prepare_platform_plugins
    prepare_platform_plugins()
    from .app import build_parser, configure_fixture
    from .ui import run_gui

    stable = Path.home() / "Library" / "Application Support" / "Trading Intelligence Lab"
    parser = build_parser()
    parser.set_defaults(data_dir=str(ROOT / ".desktop-dev" / "data"))
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir).expanduser().resolve()
    if data_dir == stable.resolve() or stable.resolve() in data_dir.parents:
        raise RuntimeError("Development mode cannot use the stable app's data directory")
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "dev.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Trading Lab Dev is already using this development data folder.")
            return 0
        if not args.library_fixture:
            seed_settings(stable, data_dir)
        configure_fixture(data_dir, args.library_fixture)
        os.environ["TRADING_INTELLIGENCE_DESKTOP_DATA_DIR"] = str(data_dir)
        runtime = SourceRuntime(data_dir=data_dir)

        def stop_on_signal(signum: int, _frame: object) -> None:
            runtime.stop()
            raise SystemExit(128 + signum)

        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(signum, stop_on_signal)
        try:
            return run_gui(runtime, smoke=args.smoke, metrics_output=args.metrics_output)
        finally:
            runtime.stop()


if __name__ == "__main__":
    raise SystemExit(main())
