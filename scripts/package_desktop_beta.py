"""Package the Trading Intelligence macOS app as an internal beta DMG + ZIP.

This script never upgrades an ad-hoc development signature into a production
claim. It records the exact signature/notarization state in a machine-readable
manifest so a beta artifact cannot be mistaken for a public release.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any


APP_NAME = "Trading Intelligence"
DEFAULT_VERSION = "0.1.0-beta"


def run(arguments: list[str], *, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=check,
        capture_output=capture,
        text=True,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def clean_version(raw: str) -> str:
    value = "".join(character for character in str(raw or "").strip() if character.isalnum() or character in ".-_+")
    return value[:64] or DEFAULT_VERSION


def build_number(raw: str) -> str:
    digits = "".join(character for character in str(raw or "") if character.isdigit())
    return (digits[-12:] if digits else "1") or "1"


def update_bundle_version(app: Path, version: str, build: str) -> None:
    info = app / "Contents" / "Info.plist"
    with info.open("rb") as handle:
        data = plistlib.load(handle)
    data["CFBundleDisplayName"] = APP_NAME
    data["CFBundleName"] = APP_NAME
    data["CFBundleShortVersionString"] = clean_version(version)
    data["CFBundleVersion"] = build_number(build)
    with info.open("wb") as handle:
        plistlib.dump(data, handle, sort_keys=True)


def signature_metadata(app: Path) -> dict[str, Any]:
    verification = run(
        ["codesign", "--verify", "--deep", "--strict", "--verbose=4", str(app)],
        capture=True,
        check=False,
    )
    details = run(
        ["codesign", "-dv", "--verbose=4", str(app)],
        capture=True,
        check=False,
    )
    text = "\n".join(filter(None, (details.stdout, details.stderr)))
    authorities = [
        line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("Authority=")
    ]
    team_id = next(
        (
            line.split("=", 1)[1].strip()
            for line in text.splitlines()
            if line.startswith("TeamIdentifier=")
        ),
        "",
    )
    identifier = next(
        (
            line.split("=", 1)[1].strip()
            for line in text.splitlines()
            if line.startswith("Identifier=")
        ),
        "",
    )
    # Ad-hoc signatures have no Developer ID authority chain and no usable team.
    developer_id = any("Developer ID Application" in authority for authority in authorities)
    return {
        "codesign_valid": verification.returncode == 0,
        "developer_id_signed": developer_id,
        "authorities": authorities,
        "team_id": team_id if team_id not in {"not set", ""} else "",
        "identifier": identifier,
    }


def notarization_metadata(app: Path) -> dict[str, Any]:
    stapler = run(["xcrun", "stapler", "validate", str(app)], capture=True, check=False)
    gatekeeper = run(["spctl", "-a", "-t", "exec", "-vv", str(app)], capture=True, check=False)
    return {
        "stapled_ticket_valid": stapler.returncode == 0,
        "gatekeeper_accepted": gatekeeper.returncode == 0,
        "stapler_detail": " ".join((stapler.stdout + " " + stapler.stderr).split())[-500:],
        "gatekeeper_detail": " ".join((gatekeeper.stdout + " " + gatekeeper.stderr).split())[-500:],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--version", default=os.environ.get("TRADING_INTELLIGENCE_DESKTOP_VERSION", DEFAULT_VERSION))
    parser.add_argument("--build-number", default=os.environ.get("GITHUB_RUN_NUMBER", "1"))
    parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""))
    args = parser.parse_args(argv)

    if sys.platform != "darwin":
        raise SystemExit("Desktop beta packaging requires macOS")

    app = Path(args.app).expanduser().resolve()
    if not app.is_dir() or app.suffix != ".app":
        raise SystemExit(f"Missing macOS app bundle: {app}")
    output = Path(args.output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = clean_version(args.version)
    build = build_number(args.build_number)

    update_bundle_version(app, version, build)
    # Updating Info.plist invalidates the previous ad-hoc signature. Re-sign the
    # internal beta before verifying. A future Developer ID release workflow must
    # re-sign with its own identity after this packaging metadata is applied.
    run(["codesign", "--force", "--deep", "--sign", "-", str(app)])
    signature = signature_metadata(app)
    if not signature["codesign_valid"]:
        raise SystemExit("The beta app failed macOS code-signature verification")
    notarization = notarization_metadata(app)

    base = f"Trading-Intelligence-{version}-apple-silicon"
    zip_path = output / f"{base}.zip"
    dmg_path = output / f"{base}.dmg"
    manifest_path = output / f"{base}.manifest.json"
    checksums_path = output / "SHA256SUMS.txt"

    if zip_path.exists():
        zip_path.unlink()
    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(zip_path)])

    with tempfile.TemporaryDirectory(prefix="trading-intelligence-dmg-") as temporary:
        stage = Path(temporary) / "Trading Intelligence Beta"
        stage.mkdir(parents=True)
        shutil.copytree(app, stage / app.name, symlinks=True)
        (stage / "Applications").symlink_to("/Applications", target_is_directory=True)
        if dmg_path.exists():
            dmg_path.unlink()
        run(
            [
                "hdiutil",
                "create",
                "-volname",
                "Trading Intelligence Beta",
                "-srcfolder",
                str(stage),
                "-ov",
                "-format",
                "UDZO",
                str(dmg_path),
            ]
        )
    run(["hdiutil", "verify", str(dmg_path)])

    manifest = {
        "schema_version": 1,
        "product": APP_NAME,
        "channel": "internal_beta",
        "version": version,
        "build_number": build,
        "commit": str(args.commit or "").strip(),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "architecture": "arm64",
        "app_bytes": directory_size(app),
        "signature": signature,
        "notarization": notarization,
        "public_distribution_ready": bool(
            signature["developer_id_signed"]
            and notarization["stapled_ticket_valid"]
            and notarization["gatekeeper_accepted"]
        ),
        "artifacts": {
            zip_path.name: {"sha256": sha256_file(zip_path), "bytes": zip_path.stat().st_size},
            dmg_path.name: {"sha256": sha256_file(dmg_path), "bytes": dmg_path.stat().st_size},
        },
        "warning": (
            "Internal beta only. Do not publish or enable automatic updates until "
            "Developer ID signing, Apple notarization, stapling, and Gatekeeper acceptance all pass."
        ),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksums_path.write_text(
        "\n".join(
            f"{details['sha256']}  {name}"
            for name, details in manifest["artifacts"].items()
        )
        + "\n",
        encoding="utf-8",
    )
    print(manifest_path)
    print(zip_path)
    print(dmg_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
