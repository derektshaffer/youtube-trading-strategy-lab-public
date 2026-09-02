"""Create final ZIP + DMG containers from a signed and stapled macOS app.

This script never modifies the app bundle. It requires the app itself to pass the
public release-readiness gate before packaging, preserving its Developer ID
signature and stapled notarization ticket byte-for-byte.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from scripts.check_desktop_release_readiness import inspect
from scripts.package_desktop_beta import clean_version


def run(arguments: list[str]) -> None:
    subprocess.run(arguments, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        raise SystemExit("Distribution packaging requires macOS")

    app = Path(args.app).expanduser().resolve()
    if not app.is_dir() or app.suffix != ".app":
        raise SystemExit(f"Missing macOS app bundle: {app}")
    readiness = inspect(app)
    if not readiness.get("public_distribution_ready"):
        raise SystemExit(
            "Refusing distribution packaging: app is not Developer ID signed, "
            "stapled, and Gatekeeper accepted."
        )

    output = Path(args.output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = clean_version(args.version)
    base = f"Trading-Intelligence-{version}-apple-silicon"
    zip_path = output / f"{base}.zip"
    dmg_path = output / f"{base}.dmg"
    zip_path.unlink(missing_ok=True)
    dmg_path.unlink(missing_ok=True)

    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(zip_path)])
    with tempfile.TemporaryDirectory(prefix="trading-intelligence-release-") as temporary:
        stage = Path(temporary) / "Trading Intelligence"
        stage.mkdir(parents=True)
        shutil.copytree(app, stage / app.name, symlinks=True)
        (stage / "Applications").symlink_to("/Applications", target_is_directory=True)
        run(
            [
                "hdiutil",
                "create",
                "-volname",
                "Trading Intelligence",
                "-srcfolder",
                str(stage),
                "-ov",
                "-format",
                "UDZO",
                str(dmg_path),
            ]
        )
    run(["hdiutil", "verify", str(dmg_path)])
    print(zip_path)
    print(dmg_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
