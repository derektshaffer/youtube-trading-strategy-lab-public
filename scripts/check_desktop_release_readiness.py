"""Fail-closed distribution-readiness check for the macOS desktop app."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, capture_output=True, text=True, check=False)


def inspect(app: Path) -> dict[str, Any]:
    codesign = run(["codesign", "--verify", "--deep", "--strict", "--verbose=4", str(app)])
    details = run(["codesign", "-dv", "--verbose=4", str(app)])
    text = "\n".join(filter(None, (details.stdout, details.stderr)))
    authorities = [
        line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("Authority=")
    ]
    developer_id = any("Developer ID Application" in authority for authority in authorities)
    stapler = run(["xcrun", "stapler", "validate", str(app)])
    gatekeeper = run(["spctl", "-a", "-t", "exec", "-vv", str(app)])
    ready = bool(
        codesign.returncode == 0
        and developer_id
        and stapler.returncode == 0
        and gatekeeper.returncode == 0
    )
    return {
        "codesign_valid": codesign.returncode == 0,
        "developer_id_signed": developer_id,
        "authorities": authorities,
        "stapled_ticket_valid": stapler.returncode == 0,
        "gatekeeper_accepted": gatekeeper.returncode == 0,
        "public_distribution_ready": ready,
        "classification": "public_release_ready" if ready else "internal_beta_only",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--output", default="")
    parser.add_argument("--require-public-ready", action="store_true")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        raise SystemExit("Release-readiness checks require macOS")
    app = Path(args.app).expanduser().resolve()
    if not app.is_dir():
        raise SystemExit(f"Missing app bundle: {app}")
    report = inspect(app)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        path = Path(args.output).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text, end="")
    if args.require_public_ready and not report["public_distribution_ready"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
