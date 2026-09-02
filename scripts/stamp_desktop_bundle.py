"""Stamp stable macOS bundle metadata before distribution signing."""

from __future__ import annotations

import argparse
from pathlib import Path

from hybrid_runtime.build_identity import stamp_bundle_identity
from scripts.package_desktop_beta import APP_NAME, build_number, bundle_short_version, clean_version


def stamp(
    app: Path,
    *,
    version: str,
    build: str,
    commit: str = "",
    channel: str = "notarized_candidate",
) -> dict[str, str]:
    clean_label = clean_version(version)
    short_version = bundle_short_version(clean_label)
    bundle_build = build_number(build)
    values = stamp_bundle_identity(
        app,
        version_label=clean_label,
        channel=channel,
        commit=commit,
        bundle_short_version=short_version,
        bundle_build=bundle_build,
    )
    # Preserve the product names set by the package/build system. The helper
    # changes only version/build identity and never claims signing readiness.
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-number", required=True)
    parser.add_argument("--commit", default="")
    parser.add_argument("--channel", default="notarized_candidate")
    args = parser.parse_args(argv)
    app = Path(args.app).expanduser().resolve()
    if not app.is_dir() or app.suffix != ".app":
        raise SystemExit(f"Missing macOS app bundle: {app}")
    values = stamp(
        app,
        version=args.version,
        build=args.build_number,
        commit=args.commit,
        channel=args.channel,
    )
    print(values["bundle_short_version"])
    print(values["build_number"])
    print(values["version_label"])
    print(values["channel"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
