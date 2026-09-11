"""Controlled Actions storage test. No research execution or production writes.

Only a uniquely named private fixture branch may be mutated. Production capacity
is measured through an adapter that forbids every non-GET request.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
from threading import Barrier
from urllib.parse import quote

from cloud_library_codec import encode_library_bytes, decode_library_bytes, MAGIC, GIT_BLOB_LIMIT
from cloud_backup_reconciliation import canonical
from reconciled_strategy_store import ReconciledStrategyStore, retain_actions_artifact, read_bundle
from youtube_strategy_engine import GitHubCloudBackup, StrategyStore, CloudBackupConflict, isoformat_utc, utc_now
from hybrid_runtime.github_library import GitHubJSONFile, GitHubLibraryConfig

SOURCE_REF = "refs/heads/codex/cloud-backup-capacity-smoke"
FIXTURE_PATH = "fixture/library.json"
OUTPUT = Path("capacity-smoke-output")


def report(name, data):
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data), flush=True)


def source_id():
    value = os.environ.get("FIXTURE_SOURCE_RUN", "")
    if not value.isdigit() or os.environ.get("GITHUB_REF") != SOURCE_REF:
        raise RuntimeError("Fixture source identity required")
    return value


def fixture_branch():
    return "codex/recovery-capacity-fixture-" + source_id()


def configure():
    if os.environ.get("GITHUB_REF") != SOURCE_REF:
        raise RuntimeError("Smoke source branch required")
    plan = json.loads(Path(".github/cloud-backup-smoke-plan.json").read_text())
    phase = plan["phase"]
    if phase not in {"retain", "recover"}:
        raise RuntimeError("Unknown smoke phase")
    run_id = os.environ["GITHUB_RUN_ID"] if phase == "retain" else str(plan["source_run_id"])
    if not run_id.isdigit() or (phase == "recover" and run_id == os.environ["GITHUB_RUN_ID"]):
        raise RuntimeError("Recovery must use an earlier workflow run")
    with open(os.environ["GITHUB_ENV"], "a") as handle:
        handle.write(f"FIXTURE_SOURCE_RUN={run_id}\nGITHUB_BACKUP_BRANCH=codex/recovery-capacity-fixture-{run_id}\n")
        handle.write(f"GITHUB_BACKUP_PATH={FIXTURE_PATH}\nGITHUB_BACKUP_STORAGE_FORMAT=gzip-v1\n")
    with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
        handle.write(f"phase={phase}\n")


class ReadOnlyProduction(GitHubCloudBackup):
    def _request(self, url, **kwargs):
        if kwargs.get("method", "GET") != "GET":
            raise RuntimeError("Production writes are forbidden")
        return super()._request(url, **kwargs)

    def save_library(self, *args, **kwargs):
        raise RuntimeError("Production writes are forbidden")

    def _save_large_library(self, *args, **kwargs):
        raise RuntimeError("Production writes are forbidden")


class FixtureBackup(GitHubCloudBackup):
    race_barrier = None
    race_waited = False
    conflicts = 0

    def __init__(self):
        super().__init__(os.environ["GITHUB_BACKUP_REPOSITORY"], os.environ["GITHUB_BACKUP_TOKEN"],
                         branch=fixture_branch(), path=FIXTURE_PATH)
        if (os.environ.get("GITHUB_BACKUP_BRANCH") != self.branch
                or os.environ.get("GITHUB_BACKUP_PATH") != self.path):
            raise RuntimeError("Fixture destination mismatch")

    def _save_large_library(self, *args, **kwargs):
        if self.race_barrier is not None and not self.race_waited:
            self.race_waited = True
            self.race_barrier.wait(timeout=120)
        try:
            return super()._save_large_library(*args, **kwargs)
        except CloudBackupConflict:
            self.conflicts += 1
            raise

    def _request(self, url, **kwargs):
        if kwargs.get("method", "GET") != "GET":
            payload = kwargs.get("payload") or {}
            if payload.get("branch") != self.branch:
                raise RuntimeError("Fixture mutation lacks exact branch")
        return super()._request(url, **kwargs)


def capacity():
    cloud = ReadOnlyProduction(os.environ["GITHUB_BACKUP_REPOSITORY"], os.environ["GITHUB_BACKUP_TOKEN"],
                              branch=os.environ.get("PRODUCTION_BACKUP_BRANCH", ""),
                              path=os.environ["PRODUCTION_BACKUP_PATH"])
    remote = cloud.read_library(include_raw=True)
    if remote is None:
        raise RuntimeError("Production read-only capacity sample missing")
    raw = remote["_raw_bytes"]
    encoded = encode_library_bytes(raw, storage_format="gzip-v1")
    restored = decode_library_bytes(encoded)
    if restored != raw:
        raise RuntimeError("Production capacity round trip changed bytes")
    report("capacity", {"mode": "production_read_only", "blob_sha": remote["sha"],
        "decoded_bytes": len(raw), "encoded_bytes": len(encoded), "wire_limit_bytes": GIT_BLOB_LIMIT,
        "encoded_headroom_bytes": GIT_BLOB_LIMIT - len(encoded),
        "sha256_before": hashlib.sha256(raw).hexdigest(), "sha256_after": hashlib.sha256(restored).hexdigest(),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "collection_counts": {k: len(v) for k, v in remote["library"].items() if isinstance(v, list)},
        "production_writes": 0})


def manifest(cloud):
    url = cloud._repository_url + "/contents/fixture/manifest.json?ref=" + quote(cloud.branch, safe="")
    import base64
    return json.loads(base64.b64decode(cloud._request(url)["content"]))


def prepare():
    if source_id() != os.environ["GITHUB_RUN_ID"]:
        raise RuntimeError("Only the originating run may initialize a fixture")
    cloud = FixtureBackup()
    cloud._verify_private_repository()
    # Only this bootstrap bypasses the data adapter, to create a new root commit.
    # It cannot update an existing ref and contains no production tree/history.
    def create(suffix, payload):
        return GitHubCloudBackup._request(cloud, cloud._repository_url + "/" + suffix,
                                         method="POST", payload=payload)
    marker = json.dumps({"fixture_only": True, "source_run_id": source_id()})
    tree = create("git/trees", {"tree": [{"path": "fixture/README.json", "mode": "100644", "type": "blob", "content": marker}]})
    commit = create("git/commits", {"message": "Initialize isolated recovery capacity fixture", "tree": tree["sha"], "parents": []})
    create("git/refs", {"ref": "refs/heads/" + cloud.branch, "sha": commit["sha"]})
    data = StrategyStore.blank()
    data["updated_at"] = isoformat_utc(utc_now())
    data["research_runs"] = [{"id": "fixture-seed", "outcome": "research_only"}]
    saved = cloud.save_library(data, expected_sha="")  # small legacy JSON
    large = deepcopy(data)
    large["updated_at"] = isoformat_utc(utc_now())
    large["fixture_history"] = "retained history;" * 6600000
    large["fixture_entropy"] = "".join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(40000))
    raw = canonical(large)
    if len(raw) <= GIT_BLOB_LIMIT:
        raise RuntimeError("Fixture must exceed the old logical file capacity")
    saved = cloud.save_library(large, previous_updated_at=data["updated_at"], expected_sha=saved["sha"])
    restored = cloud.read_library()
    if restored["library"] != large:
        raise RuntimeError("Fixture capacity round trip failed")
    desktop = GitHubJSONFile(GitHubLibraryConfig(cloud.repository, path=cloud.path, branch=cloud.branch), cloud.token)
    if desktop.read().data != large:
        raise RuntimeError("Desktop and worker decoded different fixture data")
    proof = {"fixture_only": True, "source_run_id": source_id(), "base_sha": saved["sha"],
             "base_updated_at": large["updated_at"],
             "decoded_bytes": len(raw), "encoded_bytes": len(encode_library_bytes(raw)),
             "logical_sha256": hashlib.sha256(canonical(large)).hexdigest()}
    import base64
    cloud._request(cloud._repository_url + "/contents/fixture/manifest.json", method="PUT", payload={
        "branch": cloud.branch, "message": "Record isolated fixture baseline", "content": base64.b64encode(canonical(proof)).decode()})
    report("prepare", proof)


def retain():
    if source_id() != os.environ["GITHUB_RUN_ID"]:
        raise RuntimeError("An old fixture must never be recomputed")
    cloud = FixtureBackup()
    baseline = manifest(cloud)
    def retain_then_interrupt(path, digest):
        retain_actions_artifact(path, digest)
        report("retained", {"artifact_acknowledged": True, "delta_sha256": digest,
                            "base_sha": baseline["base_sha"], "source_run_id": source_id(),
                            "intentional_exit": 75, "canonical_sync_started": False})
        os._exit(75)  # emulate runner death after upload and before synchronization
    store = ReconciledStrategyStore(Path(os.environ["RUNNER_TEMP"]) / "fixture-retain", cloud_backup=cloud,
                                    retain=retain_then_interrupt)
    data = store.load_latest()
    data["research_runs"].append({"id": "fixture-computed-once", "outcome": "research_only", "payload_hash": "fixture-original-result"})
    store.save(data)
    raise RuntimeError("Interruption hook did not execute")


def verify_retained():
    proof = json.loads((OUTPUT / "retained.json").read_text())
    cloud = FixtureBackup()
    remote = cloud.read_library()
    if remote["sha"] != proof["base_sha"] or any(r["id"] == "fixture-computed-once" for r in remote["library"]["research_runs"]):
        raise RuntimeError("Interrupted save unexpectedly changed canonical fixture")
    report("interruption-verified", {"fixture_unchanged": True, "artifact_acknowledged": True, "delta_sha256": proof["delta_sha256"]})


def verify_recovered():
    cloud = FixtureBackup()
    remote = cloud.read_library()
    rows = remote["library"]["research_runs"]
    if [r["id"] for r in rows].count("fixture-computed-once") != 1:
        raise RuntimeError("Recovery lost or duplicated the completed fixture result")
    baseline = manifest(cloud)
    prior = deepcopy(remote["library"])
    prior["research_runs"] = [r for r in rows if r["id"] != "fixture-computed-once"]
    # Exact historic content remains; only the newly completed result/timestamp differ.
    prior["updated_at"] = baseline["base_updated_at"]
    if hashlib.sha256(canonical(prior)).hexdigest() != baseline["logical_sha256"]:
        raise RuntimeError("Historic fixture payload changed")
    report("recovered", {"completed_result_count": 1, "library_sha": remote["sha"],
                          "original_base_sha": baseline["base_sha"], "computation_executed": False})


def race():
    barrier = Barrier(2)
    clouds = [FixtureBackup(), FixtureBackup()]
    stores = [ReconciledStrategyStore(Path(os.environ["RUNNER_TEMP"]) / f"race-{i}", cloud_backup=cloud)
              for i, cloud in enumerate(clouds)]
    data = [store.load_latest() for store in stores]
    before = deepcopy(data[0])
    for i in range(2):
        data[i]["research_runs"].append({"id": f"fixture-racer-{i}", "outcome": "research_only"})
        clouds[i].race_barrier = barrier
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(stores[i].save, data[i]) for i in range(2)]
        for future in futures:
            future.result(timeout=600)
    remote = FixtureBackup().read_library()
    ids = [r["id"] for r in remote["library"]["research_runs"]]
    expected = {"fixture-seed", "fixture-computed-once", "fixture-racer-0", "fixture-racer-1"}
    if set(ids) != expected or len(ids) != 4 or sum(c.conflicts for c in clouds) < 1:
        raise RuntimeError("Real CAS race did not preserve exactly four fixture results")
    without_racers = deepcopy(remote["library"])
    without_racers["research_runs"] = [r for r in without_racers["research_runs"] if not r["id"].startswith("fixture-racer-")]
    without_racers["updated_at"] = before["updated_at"]
    if canonical(without_racers) != canonical(before):
        raise RuntimeError("Concurrent updates changed historical fixture content")
    report("race", {"real_conflicts": sum(c.conflicts for c in clouds), "all_four_results_retained": True,
                     "library_sha": remote["sha"], "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss})


if __name__ == "__main__":
    try:
        {"configure": configure, "capacity": capacity, "prepare": prepare, "retain": retain,
         "verify-retained": verify_retained, "verify-recovered": verify_recovered, "race": race}[sys.argv[1]]()
    except Exception as exc:
        print(f"Controlled storage smoke failed ({type(exc).__name__}); private payload and credentials withheld.", file=sys.stderr)
        raise SystemExit(1) from None
