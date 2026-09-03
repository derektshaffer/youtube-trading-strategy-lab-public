"""Build and stage the Python service using Tauri's target-triple convention."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
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


def inferred_target_triple() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "aarch64-apple-darwin"
    if system == "darwin" and machine in {"x86_64", "amd64"}:
        return "x86_64-apple-darwin"
    if system == "linux" and machine in {"arm64", "aarch64"}:
        return "aarch64-unknown-linux-gnu"
    if system == "linux" and machine in {"x86_64", "amd64"}:
        return "x86_64-unknown-linux-gnu"
    if system == "windows" and machine in {"arm64", "aarch64"}:
        return "aarch64-pc-windows-msvc"
    if system == "windows" and machine in {"x86_64", "amd64"}:
        return "x86_64-pc-windows-msvc"
    raise RuntimeError(f"Unsupported desktop build platform: {system}/{machine}")


def target_triple(explicit: str = "") -> str:
    clean = str(explicit or "").strip()
    if clean:
        return clean
    try:
        value = command_output(["rustc", "--print", "host-tuple"])
    except (FileNotFoundError, subprocess.CalledProcessError):
        value = inferred_target_triple()
    if not value:
        raise RuntimeError("Unable to determine the desktop target triple")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--target-triple", default="")
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
    command = [
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
        "--collect-data",
        "certifi",
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(spec),
    ]
    if sys.platform == "darwin":
        command.extend(["--target-architecture", "arm64" if platform.machine() == "arm64" else "x86_64"])
        command.extend(["--codesign-identity", "-"])
    command.append(str(entrypoint))
    subprocess.run(command, cwd=root, check=True)

    extension = ".exe" if sys.platform == "win32" else ""
    source = dist / f"{name}{extension}"
    destination_dir = root / "desktop" / "tauri_spike" / "src-tauri" / "binaries"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{name}-{target_triple(args.target_triple)}{extension}"
    shutil.copy2(source, destination)
    destination.chmod(destination.stat().st_mode | 0o111)
    print(source)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
