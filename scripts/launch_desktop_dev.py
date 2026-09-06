"""Bootstrap a source-only desktop environment; never build a release bundle."""

from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ("requirements.txt", "requirements-desktop.txt", "requirements-desktop-pyside.txt")
CANONICAL_SOURCE_ROOT = Path(
    os.environ.get(
        "TRADING_INTELLIGENCE_CANONICAL_SOURCE_ROOT",
        "/Users/Derek_1/Documents/Codex/2026-09-03/referenced-chatgpt-conversation-this-is-an/work/trading-lab-dev",
    )
).resolve()
MANIFEST_FILES = (
    "trading_intelligence_app.py",
    "desktop/trading_intelligence/dev.py",
    "desktop/trading_intelligence/runtime.py",
    "hybrid_runtime/server.py",
)


def requirements_satisfied(python: Path) -> bool:
    # Verify the declared version ranges, not just that imports happen to work.
    check = """
import importlib.metadata as metadata
from packaging.requirements import Requirement
from pathlib import Path
for filename in ('requirements.txt', 'requirements-desktop.txt', 'requirements-desktop-pyside.txt'):
    for line in Path(filename).read_text().splitlines():
        if not line.strip() or line.startswith(('#', '-r ')):
            continue
        requirement = Requirement(line)
        assert metadata.version(requirement.name) in requirement.specifier
import PySide6.QtWidgets, fastapi, uvicorn, keyring
"""
    return subprocess.run(
        [str(python), "-c", check], cwd=ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def _git_output(*args: str) -> str:
    command = ["git", *args]
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def _source_manifest_sha256() -> str:
    digest = hashlib.sha256()
    for relative in MANIFEST_FILES:
        path = ROOT / relative
        digest.update(relative.encode("utf-8"))
        if path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(b"<missing>")
    return digest.hexdigest()


def _print_source_identity() -> None:
    manifest_sha256 = _source_manifest_sha256()
    branch = _git_output("rev-parse", "--abbrev-ref", "HEAD") or "unknown-branch"
    commit = _git_output("rev-parse", "HEAD") or "unknown-commit"
    git_root = _git_output("rev-parse", "--show-toplevel") or ""
    git_status = _git_output("status", "--short")

    print(f"Trading Lab Dev — canonical source: {CANONICAL_SOURCE_ROOT}", flush=True)
    print(f"Trading Lab Dev — current source root: {ROOT.resolve()}", flush=True)
    print(f"Trading Lab Dev — git toplevel: {git_root or 'unknown'}", flush=True)
    print(f"Trading Lab Dev — branch: {branch}", flush=True)
    print(f"Trading Lab Dev — commit: {commit}", flush=True)
    print(f"Trading Lab Dev — manifest sha256: {manifest_sha256}", flush=True)
    if git_status:
        print("Trading Lab Dev — git status: dirty working tree", flush=True)
    else:
        print("Trading Lab Dev — git status: clean", flush=True)


def ensure_canonical_source() -> bool:
    expected = CANONICAL_SOURCE_ROOT
    current = ROOT.resolve()
    if current != expected:
        print("Trading Lab Dev blocked: this checkout is not the canonical source tree.")
        print(f"  expected: {expected}")
        print(f"  current:  {current}")
        print(
            "Set TRADING_INTELLIGENCE_CANONICAL_SOURCE_ROOT to this checkout before allowing "
            "a non-canonical launch."
        )
        return False
    return True


def select_python() -> Path:
    for directory in (".venv", "venv"):
        python = ROOT / directory / "bin" / "python"
        if python.is_file() and requirements_satisfied(python):
            return python
    environment = ROOT / ".venv-dev"
    python = environment / "bin" / "python"
    stamp = environment / ".desktop-requirements.sha256"
    digest = hashlib.sha256(b"".join((ROOT / name).read_bytes() for name in REQUIREMENTS)).hexdigest()
    if not python.is_file():
        print("Creating this checkout's development Python environment…", flush=True)
        venv.EnvBuilder(with_pip=True).create(environment)
    if not stamp.is_file() or stamp.read_text() != digest or not requirements_satisfied(python):
        print("Installing desktop dependencies (first launch or changed requirements)…", flush=True)
        subprocess.run(
            [str(python), "-m", "pip", "install", "-r", "requirements-desktop-pyside.txt"],
            cwd=ROOT, check=True,
        )
        if not requirements_satisfied(python):
            raise RuntimeError("Desktop dependencies did not pass verification")
        stamp.write_text(digest)
    return python


def run_frontend(command: list[str], *, environment: dict[str, str]) -> int:
    """Keep launcher ownership/lock until the signalled frontend is reaped."""
    child = None
    requested_signal = None

    def forward_signal(signum: int, _frame: object) -> None:
        nonlocal requested_signal
        if requested_signal is None:
            requested_signal = signum
            if child is not None and child.poll() is None:
                child.send_signal(signum)

    previous = {}
    try:
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            previous[signum] = signal.signal(signum, forward_signal)
        child = subprocess.Popen(command, cwd=ROOT, env=environment)
        if requested_signal is not None and child.poll() is None:
            child.send_signal(requested_signal)
        return child.wait()
    finally:
        try:
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)


def main() -> int:
    os.chdir(ROOT)
    os.umask(0o077)
    if not ensure_canonical_source():
        return 1
    _print_source_identity()
    state = ROOT / ".desktop-dev"
    state.mkdir(exist_ok=True)
    # Also serializes the initial dependency installation. Never launch a second
    # engine against the same dev database when Finder is double-clicked twice.
    with (state / "launcher.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Trading Lab Dev is already running for this checkout. Quit it before relaunching.")
            return 0
        python = select_python()
        print(f"Trading Lab Dev — source: {ROOT}", flush=True)
        print(f"Python: {python}", flush=True)
        if sys.argv[1:] == ["--bootstrap-only"]:
            return 0
        environment = os.environ.copy()
        environment.pop("TRADING_INTELLIGENCE_SIDECAR_PATH", None)
        environment["TRADING_INTELLIGENCE_DEV_MODE"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"
        return run_frontend(
            [str(python), "-m", "desktop.trading_intelligence.dev", *sys.argv[1:]],
            environment=environment,
        )


if __name__ == "__main__":
    raise SystemExit(main())
