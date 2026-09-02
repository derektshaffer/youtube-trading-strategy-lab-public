"""Stamp stable numeric macOS bundle metadata before distribution signing."""

from __future__ import annotations

import argparse
from pathlib import Path
import plistlib

from scripts.package_desktop_beta import APP_NAME, build_number, bundle_short_version


def stamp(app: Path, *, version: str, build: str) -> dict[str, str]:
    info = app / "Contents" / "Info.plist"
    if not info.is_file():
        raise FileNotFoundError(f"Missing Info.plist: {info}")
    with info.open("rb") as handle:
        data = plistlib.load(handle)
    short_version = bundle_short_version(version)
    bundle_build = build_number(build)
    data["CFBundleDisplayName"] = APP_NAME
    data["CFBundleName"] = APP_NAME
    data["CFBundleShortVersionString"] = short_version
    data["CFBundleVersion"] = bundle_build
    with info.open("wb") as handle:
        plistlib.dump(data, handle, sort_keys=True)
    return {
        "bundle_short_version": short_version,
        "bundle_build": bundle_build,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-number", required=True)
    args = parser.parse_args(argv)
    app = Path(args.app).expanduser().resolve()
    if not app.is_dir() or app.suffix != ".app":
        raise SystemExit(f"Missing macOS app bundle: {app}")
    values = stamp(app, version=args.version, build=args.build_number)
    print(values["bundle_short_version"])
    print(values["bundle_build"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
