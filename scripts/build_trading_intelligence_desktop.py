"""Build the production Trading Intelligence Apple Silicon app."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hybrid_runtime.build_identity import stamp_bundle_identity


APP_NAME = "Trading Intelligence"
BUNDLE_ID = "com.derektshaffer.trading-intelligence"
DEFAULT_BUILD_VERSION = "0.1.0-dev"
DEFAULT_BUILD_CHANNEL = "development_candidate"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--sidecar", default="")
    args = parser.parse_args(argv)
    root = Path(args.repo_root).expanduser().resolve()
    sidecar = (
        Path(args.sidecar).expanduser().resolve()
        if str(args.sidecar).strip()
        else root / ".desktop_build" / "dist" / "trading-intelligence-service"
    )
    if not sidecar.is_file():
        subprocess.run(
            [sys.executable, str(root / "scripts" / "build_desktop_sidecar.py")],
            cwd=root,
            check=True,
        )
    if not sidecar.is_file():
        raise SystemExit(f"Missing packaged sidecar: {sidecar}")

    build_root = root / ".desktop_build" / "production"
    dist = build_root / "dist"
    work = build_root / "work"
    spec = build_root / "spec"
    for path in (dist, work, spec):
        path.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name",
        APP_NAME,
        "--paths",
        str(root),
        "--osx-bundle-identifier",
        BUNDLE_ID,
        "--hidden-import",
        "keyring.backends.macOS",
        "--add-binary",
        f"{sidecar}{os.pathsep}.",
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(spec),
    ]
    if sys.platform == "darwin":
        architecture = "arm64" if platform.machine().lower() == "arm64" else "x86_64"
        command.extend(["--target-architecture", architecture])
        command.extend(["--codesign-identity", "-"])
    command.append(str(root / "desktop" / "trading_intelligence" / "app.py"))
    subprocess.run(command, cwd=root, check=True)

    app = dist / f"{APP_NAME}.app"
    if not app.is_dir():
        raise SystemExit(f"PyInstaller did not create the expected app: {app}")
    if sys.platform == "darwin":
        # Stamp before the final signature. Beta/notarized packaging deliberately
        # overwrites this development identity with its exact release identity.
        stamp_bundle_identity(
            app,
            version_label=os.environ.get(
                "TRADING_INTELLIGENCE_BUILD_VERSION",
                DEFAULT_BUILD_VERSION,
            ),
            channel=os.environ.get(
                "TRADING_INTELLIGENCE_BUILD_CHANNEL",
                DEFAULT_BUILD_CHANNEL,
            ),
            commit=os.environ.get("GITHUB_SHA", ""),
        )
        subprocess.run(
            ["codesign", "--force", "--deep", "--sign", "-", str(app)],
            check=True,
        )
    print(app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
