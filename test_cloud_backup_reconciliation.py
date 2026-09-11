from copy import deepcopy
import gzip
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from cloud_backup_reconciliation import reconcile, ReconciliationConflict, claim_identity
from reconciled_strategy_store import ReconciledStrategyStore, PersistencePending, read_bundle
from youtube_strategy_engine import StrategyStore, GitHubCloudBackup, CloudBackupConflict, AppError


def library(**changes):
    return {**StrategyStore.blank(), "updated_at": "2026-09-09T06:00:00Z", **changes}


def result(id):
    return {"id": id, "outcome": "research_only"}


class Cloud:
    repository, branch, path = "fixture/private", "main", "library.json"

    def __init__(self, data=None):
        self.data = deepcopy(data or library())
        self.revision = 1
        self.writes = 0
        self.conflicts = 0
        self.after_write = False
        self.history = []

    def read_library(self, **kwargs):
        return {"library": deepcopy(self.data), "sha": str(self.revision)}

    def save_library(self, data, *, previous_updated_at=None, expected_sha=None):
        self.writes += 1
        if self.conflicts:
            self.conflicts -= 1
            self.data["research_runs"].append(result(f"racer-{self.revision}"))
            self.revision += 1
            raise CloudBackupConflict("fixture writer moved")
        assert expected_sha == str(self.revision)
        assert previous_updated_at == self.data["updated_at"]
        self.data = deepcopy(data)
        self.revision += 1
        self.history.append(deepcopy(data))
        if self.after_write:
            self.after_write = False
            raise AppError("uncertain network acknowledgement")
        return self.read_library()


def store(tmp_path, cloud, **kwargs):
    kwargs.setdefault("retain", lambda *_: None)
    value = ReconciledStrategyStore(tmp_path, cloud_backup=cloud, sleeper=lambda _: None, **kwargs)
    value.load_latest()
    return value


def test_long_running_two_writers_preserve_all_results(tmp_path):
    cloud = Cloud(library(research_runs=[result("existing")]))
    a = store(tmp_path / "a", cloud)
    b = store(tmp_path / "b", cloud)
    data_a, data_b = a.load(), b.load()
    data_b["research_runs"].append(result("b"))
    b.save(data_b)
    data_a["research_runs"].append(result("a"))
    a.save(data_a)
    assert {r["id"] for r in cloud.data["research_runs"]} == {"existing", "a", "b"}
    assert cloud.writes == 2


def test_identical_duplicate_converges_without_write(tmp_path):
    cloud = Cloud()
    a, b = store(tmp_path / "a", cloud), store(tmp_path / "b", cloud)
    x, y = a.load(), b.load()
    x["research_runs"] = y["research_runs"] = [result("same")]
    a.save(x)
    b.save(y)
    assert cloud.writes == 1
    assert len(cloud.data["research_runs"]) == 1


@pytest.mark.parametrize("existing", [False, True])
def test_different_payload_same_id_fails_closed(existing):
    base = library(research_runs=[result("x")] if existing else [])
    local, remote = deepcopy(base), deepcopy(base)
    local["research_runs"] = [{"id": "x", "outcome": "changed"}]
    remote["research_runs"] = [result("x")]
    with pytest.raises(ReconciliationConflict, match="research_runs/x"):
        reconcile(base, local, remote)


def job(**changes):
    return {"id": "job", "type": "predictive_ml_backfill", "status": "running",
            "attempts": 1, "max_attempts": 3, "worker_id": "owner",
            "started_at": "2026-09-09T06:00:00Z", "created_at": "2026-09-09T05:00:00Z",
            "updated_at": "2026-09-09T06:00:00Z", "payload": {"symbols": ["FIXTURE"]},
            **changes}


def completed(base):
    local = deepcopy(base)
    local["research_queue"] = [job(status="complete", worker_id=None,
        result_ref="predictive-ml:ml", completed_at="2026-09-09T08:00:00Z",
        updated_at="2026-09-09T08:00:00Z")]
    local["predictive_ml_runs"] = [{"id": "ml", "origin_job_id": "job", "origin_claim": claim_identity(base["research_queue"][0]), "research_only": True}]
    return local


@pytest.mark.parametrize("new", ["complete", "cancelled", "retry"])
def test_unchanged_stale_queue_preserves_remote_state(new):
    base = library(research_queue=[job()])
    remote = library(research_queue=[job(status=new, worker_id="new-owner")])
    local = deepcopy(base)
    local["research_runs"] = [result("a")]
    merged = reconcile(base, local, remote)
    assert merged["research_queue"] == remote["research_queue"]


@pytest.mark.parametrize("change", [
    {"worker_id": "foreign", "attempts": 2}, {"status": "cancelled"},
    {"status": "complete", "result_ref": "predictive-ml:other"},
    {"cloud_worker": {"run_id": "foreign"}},
])
def test_competing_claim_or_terminal_conflict(change):
    base = library(research_queue=[job()])
    remote = library(research_queue=[job(**change)])
    with pytest.raises(ReconciliationConflict, match="both writers"):
        reconcile(base, completed(base), remote)


@pytest.mark.parametrize("field,value", [
    ("attempts", 0), ("attempts", 2), ("payload", {}),
    ("retry_of_job_id", "foreign"), ("cloud_worker", {"run_id": "foreign"}),
    ("started_at", "2026-09-09T07:00:00Z"), ("worker_id", "foreign"),
])
def test_invalid_local_queue_identity_or_ancestry(field, value):
    base = library(research_queue=[job()])
    local = completed(base)
    local["research_queue"][0][field] = value
    with pytest.raises(ReconciliationConflict):
        reconcile(base, local, base)


def test_complete_claim_with_unrelated_remote_record():
    base = library(research_queue=[job()])
    remote = deepcopy(base)
    remote["research_runs"].append(result("b"))
    merged = reconcile(base, completed(base), remote)
    assert merged["research_queue"][0]["status"] == "complete"
    assert merged["research_runs"] == remote["research_runs"]


def test_foreign_result_association_rejected():
    base = library(research_queue=[job()])
    local = completed(base)
    local["predictive_ml_runs"][0]["origin_job_id"] = "foreign"
    with pytest.raises(ReconciliationConflict, match="foreign ML"):
        reconcile(base, local, base)


def test_terminal_cannot_regress():
    base = library(research_queue=[job(status="complete")])
    local = library(research_queue=[job()])
    with pytest.raises(ReconciliationConflict, match="state transition"):
        reconcile(base, local, base)


def test_repeated_cas_conflict_rebases_same_delta(tmp_path):
    cloud = Cloud()
    a = store(tmp_path, cloud)
    cloud.conflicts = 3
    local = a.load()
    local["research_runs"] = [result("a")]
    a.save(local)
    assert cloud.writes == 4
    assert len(cloud.data["research_runs"]) == 4
    assert len({r["id"] for r in cloud.data["research_runs"]}) == 4


def test_retry_exhaustion_restart_without_computation(tmp_path):
    cloud = Cloud()
    a = store(tmp_path, cloud)
    cloud.conflicts = 4
    local = a.load()
    local["research_runs"] = [result("completed")]
    with pytest.raises(PersistencePending):
        a.save(local)
    assert cloud.writes == 4
    bundle = read_bundle(a.pending_path)
    assert bundle["payload"]["local"]["research_runs"] == [result("completed")]
    restarted = ReconciledStrategyStore(tmp_path, cloud_backup=cloud, retain=lambda *_: None, sleeper=lambda _: None)
    restarted.load_latest()
    assert not restarted.pending_path.exists()
    assert "completed" in {r["id"] for r in cloud.data["research_runs"]}


def test_crash_before_cloud_sync_retains_complete_bundle(tmp_path):
    cloud = Cloud()
    def crash(*args):
        raise SystemExit("simulated process death")
    a = store(tmp_path, cloud, retain=crash)
    local = a.load()
    local["research_runs"] = [result("completed")]
    with pytest.raises(SystemExit):
        a.save(local)
    assert cloud.writes == 0
    b = ReconciledStrategyStore(tmp_path / "recovery", cloud_backup=cloud, retain=lambda *_: None)
    b.import_recovery(a.pending_path)
    assert cloud.data["research_runs"] == [result("completed")]


def test_uncertain_ack_is_idempotent_on_recovery(tmp_path):
    cloud = Cloud()
    a = store(tmp_path, cloud)
    cloud.after_write = True
    local = a.load()
    local["research_runs"] = [result("completed")]
    with pytest.raises(PersistencePending):
        a.save(local)
    a.sync_cloud_backup()
    assert cloud.writes == 1
    assert len(cloud.data["research_runs"]) == 1


def test_retention_failure_prevents_cloud_write(tmp_path):
    cloud = Cloud()
    def unavailable(*args):
        raise AppError("artifact unavailable")
    a = store(tmp_path, cloud, retain=unavailable)
    with pytest.raises(PersistencePending):
        a.save(library(research_runs=[result("a")]))
    assert cloud.writes == 0
    assert a.pending_path.exists()


def test_retention_pruning_cannot_delete_cloud_history():
    base = library(research_runs=[result("old")])
    local = library(research_runs=[result("new")])
    remote = library(research_runs=[result("other"), result("old")])
    merged = reconcile(base, local, remote)
    assert {r["id"] for r in merged["research_runs"]} == {"old", "new", "other"}


@pytest.mark.parametrize("change", ["missing_id", "duplicate_id", "collection_type", "nan"])
def test_malformed_library_fails(change):
    base, local = library(), library()
    local["research_runs"] = {
        "missing_id": [{}], "duplicate_id": [result("x"), result("x")],
        "collection_type": {}, "nan": [{"id": "x", "v": float("nan")}],
    }[change]
    with pytest.raises((ReconciliationConflict, ValueError)):
        reconcile(base, local, base)


def test_corrupt_recovery_and_wrong_destination_fail(tmp_path):
    cloud = Cloud()
    a = store(tmp_path / "a", cloud, retain=lambda *_: (_ for _ in ()).throw(AppError("offline")))
    with pytest.raises(PersistencePending):
        a.save(library(research_runs=[result("a")]))
    other = Cloud()
    other.path = "other.json"
    b = ReconciledStrategyStore(tmp_path / "b", cloud_backup=other, retain=lambda *_: None)
    with pytest.raises(PersistencePending, match="destination"):
        b.import_recovery(a.pending_path)
    data = json.loads(gzip.decompress(a.pending_path.read_bytes()))
    data["payload"]["local"]["research_runs"][0]["outcome"] = "tampered"
    a.pending_path.write_bytes(gzip.compress(json.dumps(data).encode()))
    with pytest.raises(PersistencePending, match="damaged"):
        a.sync_cloud_backup()
    assert cloud.writes == other.writes == 0


def test_same_timestamp_different_blob_is_cas_conflict():
    cloud = GitHubCloudBackup("fixture/private", "fake-token", branch="main")
    with patch.object(cloud, "read_library", return_value={"library": library(), "sha": "b"}), patch.object(cloud, "_request") as write:
        with pytest.raises(CloudBackupConflict):
            cloud.save_library(library(), expected_sha="a")
        write.assert_not_called()


def test_oversized_blob_does_not_run_git():
    cloud = GitHubCloudBackup("fixture/private", "fake-token", branch="main")
    class Oversized(bytes):
        def __len__(self):
            return 100 * 1024 * 1024 + 1
    with patch("youtube_strategy_engine.subprocess.run") as git:
        with pytest.raises(AppError, match="100 MiB"):
            cloud._save_large_library(Oversized())
        git.assert_not_called()


@pytest.mark.parametrize('marker,is_conflict', [
    ('[rejected] non-fast-forward', True), ('fetch first', True),
    ('[remote rejected] GH001: large files detected', False),
    ('[remote rejected] repository rule violation', False),
])
def test_git_rejection_classification(marker, is_conflict):
    import subprocess
    cloud = GitHubCloudBackup('fixture/private', 'fake-token', branch='main')
    with patch('youtube_strategy_engine.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', marker)):
        with pytest.raises(AppError) as raised:
            cloud._save_large_library(b'{}')
        assert isinstance(raised.value, CloudBackupConflict) is is_conflict


@pytest.mark.parametrize('ref,actual,expected,is_conflict', [
    ('main', 'a' * 40, 'b' * 40, True),
    ('main', 'a' * 64, 'b' * 64, True),
    ('main', 'a' * 40, 'a' * 40, False),
    ('other', 'a' * 40, 'b' * 40, False),
    ('MAIN', 'a' * 40, 'b' * 40, False),
    ('main', 'unknown', 'unknown', False),
    ('main', 'a' * 40, 'b' * 64, False),
])
def test_server_ref_cas_rejection(ref, actual, expected, is_conflict):
    import subprocess
    marker = f"remote: error: cannot lock ref 'refs/heads/{ref}': is at {actual} but expected {expected}"
    cloud = GitHubCloudBackup('fixture/private', 'fake-token', branch='main')
    def git(args, **kwargs):
        if args[1] == 'push':
            return subprocess.CompletedProcess(args, 1, '', marker)
        return subprocess.CompletedProcess(args, 0, 'f' * 40 if args[1] == 'hash-object' else '', '')
    with patch('youtube_strategy_engine.subprocess.run', side_effect=git):
        with pytest.raises(AppError) as caught:
            cloud._save_large_library(b'{}')
    assert isinstance(caught.value, CloudBackupConflict) is is_conflict


def test_download_body_must_match_metadata_sha():
    cloud = GitHubCloudBackup('fixture/private', 'fake-token', branch='main')
    cloud._repository_checked = True
    with patch.object(cloud, '_request', return_value={'type': 'file', 'size': 1000001, 'sha': 'a' * 40}), patch.object(cloud, '_request_bytes', return_value=b'{"strategies":[]}'):
        with pytest.raises(CloudBackupConflict, match='integrity'):
            cloud.read_library()


def test_competing_ml_summary_projection_fails_closed():
    base = library(research_system={'predictive_ml_backfill_status': {'run_id': 'base'}})
    local = library(research_system={'predictive_ml_backfill_status': {'run_id': 'a'}})
    remote = library(research_system={'predictive_ml_backfill_status': {'run_id': 'b'}})
    with pytest.raises(ReconciliationConflict, match='projection'):
        reconcile(base, local, remote)


def test_independent_settings_preserved():
    base = library(research_system={'unchanged': 1})
    local = library(research_system={'unchanged': 1, 'local': {'enabled': False}})
    remote = library(research_system={'unchanged': 1, 'remote': {'enabled': False}})
    assert reconcile(base, local, remote)['research_system'] == {
        'unchanged': 1, 'local': {'enabled': False}, 'remote': {'enabled': False}}


def test_result_from_old_attempt_cannot_finish_new_attempt():
    base = library(research_queue=[job()])
    local = completed(base)
    local['predictive_ml_runs'][0]['origin_claim']['attempts'] = 0
    with pytest.raises(ReconciliationConflict, match='attempt association'):
        reconcile(base, local, base)


def test_full_ml_compute_once_then_persistence_recovery(tmp_path):
    import cloud_research_worker as worker
    cloud = Cloud(library(research_queue=[job()]))
    a = store(tmp_path, cloud)
    # Status save must work, then block the post-computation artifact/sync.
    def retain(path, digest):
        bundle = read_bundle(path)
        if bundle['payload']['local'].get('predictive_ml_runs'):
            raise AppError('simulated retention outage')
    a.retain = retain
    output = {'id': 'ml', 'symbols': ['FIXTURE'], 'completed_at': '2026-09-09T08:00:00Z',
              'dataset_summary': {'row_count': 10}, 'research_only': True}
    with patch.object(worker, 'build_market'), patch.object(worker, 'run_predictive_ml_backfill', return_value=output) as compute:
        with pytest.raises(PersistencePending):
            worker.execute_job(a, None, job(), 'owner')
        assert a.load()['research_queue'][0]['status'] == 'complete'
        restarted = ReconciledStrategyStore(tmp_path, cloud_backup=cloud, retain=lambda *_: None)
        recovered = restarted.load_latest()
        assert compute.call_count == 1
    assert recovered['research_queue'][0]['result_ref'] == 'predictive-ml:ml'
    assert len(recovered['predictive_ml_runs']) == len(recovered['research_worker_runs']) == 1
    restarted.sync_cloud_backup()
    assert len(cloud.data['predictive_ml_runs']) == len(cloud.data['research_worker_runs']) == 1


def test_two_worker_receipts_rebuild_one_coherent_projection():
    from trading_research_orchestrator import record_worker_run
    base = library()
    local = record_worker_run(base, worker_id='A', job_id='job-a', job_type='web_research', status='complete')
    remote = record_worker_run(base, worker_id='B', job_id='job-b', job_type='web_research', status='complete')
    merged = reconcile(base, local, remote)
    assert len(merged['research_worker_runs']) == 2
    newest = max(merged['research_worker_runs'], key=lambda r: (r['generated_at'], r['id']))
    assert merged['research_system']['last_worker_id'] == newest['worker_id']
    assert merged['research_system']['last_worker_at'] == newest['generated_at']


def test_unconfigured_actions_retention_fails_closed(tmp_path, monkeypatch):
    from reconciled_strategy_store import retain_actions_artifact
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.delenv('RETAIN_CLOUD_RECOVERY', raising=False)
    with pytest.raises(PersistencePending, match='not configured'):
        retain_actions_artifact(tmp_path / 'unused', 'a' * 64)


def test_real_git_cas_preserves_competing_writer(tmp_path):
    import subprocess
    def git(*args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
    bare, first, other = tmp_path / 'bare.git', tmp_path / 'first', tmp_path / 'other'
    git('init', '--bare', str(bare))
    git('clone', str(bare), str(first))
    git('config', 'user.email', 'fixture@example.invalid', cwd=first)
    git('config', 'user.name', 'Fixture', cwd=first)
    (first / 'library.json').write_text('{"strategies":[]}')
    git('add', '.', cwd=first)
    git('commit', '-m', 'fixture', cwd=first)
    git('branch', '-M', 'main', cwd=first)
    git('push', 'origin', 'main', cwd=first)
    original_sha = git('hash-object', 'library.json', cwd=first)
    git('clone', '--branch', 'main', str(bare), str(other))
    git('config', 'user.email', 'fixture@example.invalid', cwd=other)
    git('config', 'user.name', 'Fixture B', cwd=other)
    (other / 'library.json').write_text('{"strategies":[{"id":"B"}]}')
    git('add', '.', cwd=other)
    git('commit', '-m', 'writer B', cwd=other)
    real_run = subprocess.run
    raced = False
    def run(args, **kwargs):
        nonlocal raced
        if args[:3] == ['git', 'push', 'origin'] and not raced:
            raced = True
            real_run(['git', 'push', 'origin', 'main'], cwd=other, check=True, capture_output=True)
        return real_run(args, **kwargs)
    cloud = GitHubCloudBackup('fixture/private', 'fake-token', branch='main', path='library.json')
    with patch.object(cloud, '_git_clone_url', return_value=bare.as_uri()), patch('youtube_strategy_engine.subprocess.run', side_effect=run):
        with pytest.raises(CloudBackupConflict):
            cloud._save_large_library(b'{"strategies":[{"id":"A"}]}', current_sha=original_sha)
    assert git('--git-dir', str(bare), 'show', 'main:library.json') == '{"strategies":[{"id":"B"}]}'


def test_corrupt_remote_collection_cannot_be_normalized_away(tmp_path):
    cloud = Cloud()
    a = store(tmp_path, cloud)
    cloud.data['experiment_registry'] = {'malformed': True}
    with pytest.raises(PersistencePending, match='Invalid cloud collection'):
        a.save(library(research_runs=[result('completed')]))
    assert cloud.writes == 0
    assert read_bundle(a.pending_path)['payload']['local']['research_runs'] == [result('completed')]


def test_local_install_crash_recovers_from_write_ahead_bundle(tmp_path):
    cloud = Cloud()
    a = store(tmp_path, cloud)
    with patch.object(a, '_write_local', side_effect=OSError('disk failure')):
        with pytest.raises(PersistencePending, match='journaled'):
            a.save(library(research_runs=[result('completed')]))
    assert cloud.writes == 0
    b = ReconciledStrategyStore(tmp_path, cloud_backup=cloud, retain=lambda *_: None)
    b.load_latest()
    assert cloud.data['research_runs'] == [result('completed')]


def test_repeat_save_same_result_does_not_duplicate_or_change_receipt(tmp_path):
    cloud = Cloud(library(research_queue=[job()]))
    a = store(tmp_path, cloud)
    local = completed(a.load())
    first = a.save(local)
    writes = cloud.writes
    second = a.save(first)
    assert first == second
    assert cloud.writes == writes
    assert len(second['predictive_ml_runs']) == 1


def test_valid_claim_increments_exactly_one_attempt():
    base = library(research_queue=[job(status='queued', worker_id=None, started_at=None, attempts=0)])
    local = library(research_queue=[job()])
    assert reconcile(base, local, base)['research_queue'] == local['research_queue']


def test_completed_result_cannot_disappear_with_remote_library(tmp_path):
    cloud = Cloud()
    a = store(tmp_path, cloud)
    with patch.object(cloud, 'read_library', return_value=None):
        with pytest.raises(PersistencePending, match='disappeared'):
            a.save(library(research_runs=[result('completed')]))
    assert cloud.writes == 0


def test_recovery_receipt_is_immutable_and_not_canonical_library_write():
    import base64
    cloud = GitHubCloudBackup('fixture/private', 'fake-token', branch='main', path='library.json')
    cloud._repository_checked = True
    digest = 'a' * 64
    with patch.object(cloud, '_request', side_effect=[None, {}]) as request:
        cloud.acknowledge_recovery(digest, 'fixture-sha')
    args = request.call_args_list[1]
    assert args.args[0].endswith(f'library.json.recovery-receipts/{digest}.json')
    receipt = json.loads(base64.b64decode(args.kwargs['payload']['content']))
    assert receipt['delta_sha256'] == digest
    with patch.object(cloud, '_request', return_value={'content': base64.b64encode(json.dumps(receipt).encode()).decode()}) as request:
        cloud.acknowledge_recovery(digest, 'newer-merged-sha')
        assert request.call_count == 1
    receipt['delta_sha256'] = 'foreign'
    with patch.object(cloud, '_request', return_value={'content': base64.b64encode(json.dumps(receipt).encode()).decode()}):
        with pytest.raises(AppError, match='conflicting identity'):
            cloud.acknowledge_recovery(digest, 'fixture-sha')


def test_stale_protocol_claim_never_recomputes_even_if_artifact_expired():
    from datetime import datetime, timezone
    from trading_research_orchestrator import recover_stale_research_jobs, claim_next_research_job
    original = job(cloud_persistence_protocol=1)
    data = library(research_queue=[original])
    later = datetime(2026, 11, 1, tzinfo=timezone.utc)
    recovered, count = recover_stale_research_jobs(data, now=later)
    assert count == 0
    assert recovered['research_queue'] == [original]
    _, claimed = claim_next_research_job(recovered, 'foreign', now=later,
                                         allowed_types={'predictive_ml_backfill'})
    assert claimed is None


def test_noop_save_creates_no_additional_recovery_artifact_or_receipt(tmp_path):
    cloud = Cloud()
    retained = []
    a = store(tmp_path, cloud, retain=lambda path, digest: retained.append(digest))
    first = a.save(library(research_runs=[result('completed')]))
    first['updated_at'] = '1999-01-01T00:00:00Z'
    again = a.save(first)
    assert len(retained) == 1
    assert len(list(a.recovery_directory.glob('*.synced'))) == 1
    assert again['updated_at'] != first['updated_at']
