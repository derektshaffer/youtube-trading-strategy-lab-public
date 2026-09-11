"""Inspect or synchronize a completed recovery bundle; never invoke research."""
import argparse
import json
from pathlib import Path

from reconciled_strategy_store import ReconciledStrategyStore, read_bundle
from youtube_strategy_engine import GitHubCloudBackup
import os


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="Decrypted .json.gz recovery bundle")
    parser.add_argument("--directory", type=Path, required=True, help="Separate local recovery directory")
    parser.add_argument("--sync", action="store_true", help="Explicitly reconcile and write the configured private backup")
    args = parser.parse_args(argv)
    bundle = read_bundle(args.bundle)
    payload = bundle["payload"]
    if not args.sync:
        print(json.dumps({"sha256": bundle["sha256"], "source": payload["source"],
                          "destination": payload["destination"],
                          "base_sha": payload["base_sha"], "mode": "inspect_only"}, indent=2))
        return 0
    cloud = GitHubCloudBackup(os.environ.get("GITHUB_BACKUP_REPOSITORY", ""),
                             os.environ.get("GITHUB_BACKUP_TOKEN", ""),
                             branch=os.environ.get("GITHUB_BACKUP_BRANCH", ""),
                             path=os.environ.get("GITHUB_BACKUP_PATH", ""))
    cloud._verify_private_repository()  # resolve default branch before destination comparison
    store = ReconciledStrategyStore(args.directory, cloud_backup=cloud)
    store.import_recovery(args.bundle)
    print(f"Recovery {bundle['sha256']} synchronized without computation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
