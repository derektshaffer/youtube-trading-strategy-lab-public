"""Apply and verify Developer ID distribution signing for the macOS app."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


def run(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, capture_output=True, text=True, check=check)


def sign(app: Path, identity: str) -> None:
    chosen = str(identity or "").strip()
    if not chosen.startswith("Developer ID Application:"):
        raise ValueError("A Developer ID Application identity is required")
    # PyInstaller produces a self-contained app with nested Mach-O code. Re-sign
    # the complete bundle recursively for direct distribution, using the hardened
    # runtime and Apple's secure timestamp required by notarization.
    run(
        [
            "codesign",
            "--force",
            "--deep",
            "--options",
            "runtime",
            "--timestamp",
            "--sign",
            chosen,
            str(app),
        ]
    )
    verification = run(
        ["codesign", "--verify", "--deep", "--strict", "--verbose=4", str(app)],
        check=False,
    )
    if verification.returncode != 0:
        raise RuntimeError(
            "Developer ID signature verification failed: "
            + " ".join((verification.stdout + " " + verification.stderr).split())[-1000:]
        )
    details = run(["codesign", "-dv", "--verbose=4", str(app)], check=False)
    text = details.stdout + "\n" + details.stderr
    if "Authority=Developer ID Application:" not in text:
        raise RuntimeError("The final app is not signed with Developer ID Application")
    if "runtime" not in text.lower():
        raise RuntimeError("The final app signature does not advertise hardened runtime")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--identity", required=True)
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        raise SystemExit("Developer ID signing requires macOS")
    app = Path(args.app).expanduser().resolve()
    if not app.is_dir() or app.suffix != ".app":
        raise SystemExit(f"Missing macOS app bundle: {app}")
    try:
        sign(app, args.identity)
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
    print(app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
