"""Build and stage the Python service using Tauri's target-triple convention."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys


def command_output(arguments: list[str]) -> str:
    completed = subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def target_triple() -> str:
    try:
        value = command_output(["rustc", "--print", "host-tuple"])
    except subprocess.CalledProcessError:
        details = command_output(["rustc", "-Vv"])
        value = next(
            (line.split(":", 1)[1].strip() for line in details.splitlines() if line.startswith("host:")),
            "",
        )
    if not value:
        raise RuntimeError("Unable to determine the Rust target triple")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)
    root = Path(args.repo_root).expanduser().resolve()
    entrypoint = root / "hybrid_sidecar_entry.py"
    if not entrypoint.exists():
        raise SystemExit(f"Missing sidecar entrypoint: {entrypoint}")

    build_root = root / ".desktop_build"
    dist = build_root / "dist"
    work = build_root / "work"
    spec = build_root / "spec"
    for path in (dist, work, spec):
        path.mkdir(parents=True, exist_ok=True)

    name = "trading-intelligence-service"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--name",
            name,
            "--hidden-import",
            "keyring.backends.macOS",
            "--distpath",
            str(dist),
            "--workpath",
            str(work),
            "--specpath",
            str(spec),
            str(entrypoint),
        ],
        cwd=root,
        check=True,
    )
    extension = ".exe" if sys.platform == "win32" else ""
    source = dist / f"{name}{extension}"
    destination_dir = root / "desktop" / "tauri_spike" / "src-tauri" / "binaries"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{name}-{target_triple()}{extension}"
    shutil.copy2(source, destination)
    destination.chmod(destination.stat().st_mode | 0o111)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
