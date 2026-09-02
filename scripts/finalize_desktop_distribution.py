"""Finalize a notarized desktop candidate with fail-closed manifest + hashes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from scripts.check_desktop_release_readiness import inspect as inspect_app
from scripts.package_desktop_beta import bundle_short_version, clean_version


def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, capture_output=True, text=True, check=False)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_dmg(path: Path) -> dict[str, Any]:
    signature = run(["codesign", "--verify", "--verbose=4", str(path)])
    stapler = run(["xcrun", "stapler", "validate", str(path)])
    gatekeeper = run(
        [
            "spctl",
            "-a",
            "-t",
            "open",
            "--context",
            "context:primary-signature",
            "-v",
            str(path),
        ]
    )
    return {
        "codesign_valid": signature.returncode == 0,
        "stapled_ticket_valid": stapler.returncode == 0,
        "gatekeeper_accepted": gatekeeper.returncode == 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--dmg", required=True)
    parser.add_argument("--zip", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-number", required=True)
    parser.add_argument("--commit", default="")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        raise SystemExit("Distribution finalization requires macOS")

    app = Path(args.app).expanduser().resolve()
    dmg = Path(args.dmg).expanduser().resolve()
    zip_path = Path(args.zip).expanduser().resolve()
    output = Path(args.output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    for path in (app, dmg, zip_path):
        if not path.exists():
            raise SystemExit(f"Missing distribution artifact: {path}")

    app_state = inspect_app(app)
    dmg_state = inspect_dmg(dmg)
    ready = bool(
        app_state.get("public_distribution_ready")
        and dmg_state.get("codesign_valid")
        and dmg_state.get("stapled_ticket_valid")
        and dmg_state.get("gatekeeper_accepted")
    )
    if not ready:
        raise SystemExit(
            "Distribution candidate failed signing/notarization/Gatekeeper gates: "
            + json.dumps({"app": app_state, "dmg": dmg_state}, sort_keys=True)
        )

    version = clean_version(args.version)
    manifest = {
        "schema_version": 1,
        "product": "Trading Intelligence",
        "channel": "notarized_candidate",
        "version": version,
        "bundle_short_version": bundle_short_version(version),
        "build_number": str(args.build_number),
        "commit": str(args.commit or "").strip(),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "architecture": "arm64",
        "app": app_state,
        "dmg": dmg_state,
        "public_distribution_ready": True,
        # Deliberately separate release signing from update-system signing. An
        # automatic updater is not safe merely because the app is notarized.
        "automatic_updates_ready": False,
        "artifacts": {
            dmg.name: {"sha256": sha256_file(dmg), "bytes": dmg.stat().st_size},
            zip_path.name: {"sha256": sha256_file(zip_path), "bytes": zip_path.stat().st_size},
        },
    }
    manifest_path = output / "distribution-manifest.json"
    checksum_path = output / "SHA256SUMS.txt"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksum_path.write_text(
        "\n".join(
            f"{details['sha256']}  {name}"
            for name, details in manifest["artifacts"].items()
        )
        + "\n",
        encoding="utf-8",
    )
    print(manifest_path)
    print(checksum_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
