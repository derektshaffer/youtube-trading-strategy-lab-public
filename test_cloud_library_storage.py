import base64
from copy import deepcopy
import hashlib
import json
from unittest.mock import Mock
import zlib

import pytest

import cloud_library_storage as storage
from hybrid_runtime.github_library import GitHubJSONFile, GitHubLibraryConfig, GitHubLibraryError
import youtube_strategy_engine as engine
import cloud_research_worker as worker


def raw_document():
    return json.dumps({'strategies': [], 'updated_at': 'frozen',
        'research_queue': [{'id': 'job', 'status': 'queued', 'input': 'é'}],
        'audit': ['exact evidence' * 1000] * 100,
        'provenance': {'sha256': 'a' * 64, 'research_only': True},
        'numbers': [1, 1.0, -0.0, 1e-15]}).encode()


def test_lossless_deterministic_opt_in_and_legacy_read():
    raw = raw_document()
    assert storage.encode_library(raw) == raw
    assert storage.decode_library(raw) == (raw, False)
    encoded = storage.encode_library(raw, compress=True)
    assert encoded == storage.encode_library(raw, compress=True)
    assert len(encoded) < len(raw) / 10
    assert storage.decode_library(encoded) == (raw, True)


@pytest.mark.parametrize('field,value', [
    ('sha256', '0' * 64), ('uncompressed_bytes', 1),
    ('uncompressed_bytes', storage.MAX_LOGICAL_BYTES + 1),
    ('uncompressed_bytes', True), ('compressed_bytes', -1),
    ('compressed_bytes', 2), ('format', 'future'), ('payload', '%%%'), ('payload', 'é'),
])
def test_corruption_fails_closed(field, value):
    encoded = json.loads(storage.encode_library(raw_document(), compress=True))
    encoded[storage.MARKER][field] = value
    with pytest.raises(storage.CloudStorageError):
        storage.decode_library(json.dumps(encoded).encode())


@pytest.mark.parametrize('transform', [lambda p: p[:-4], lambda p: p + b'extra',
                                     lambda p: p + zlib.compress(b'hidden')])
def test_truncation_and_trailing_streams_rejected(transform):
    encoded = json.loads(storage.encode_library(raw_document(), compress=True))
    arc = encoded[storage.MARKER]
    packed = transform(base64.b64decode(arc['payload']))
    arc.update(payload=base64.b64encode(packed).decode(), compressed_bytes=len(packed))
    with pytest.raises(storage.CloudStorageError):
        storage.decode_library(json.dumps(encoded).encode())


def test_size_boundary_and_reserve(monkeypatch):
    monkeypatch.setattr(storage, 'WRITE_LIMIT_BYTES', 20)
    monkeypatch.setattr(storage, 'WORK_RESERVE_BYTES', 5)
    storage.check_write_size(20)
    with pytest.raises(storage.CloudStorageError, match='size_limit'):
        storage.check_write_size(21)
    assert storage.storage_preflight(b'{"x":1}')['retention_policy'] == 'preserve_all_records'
    with pytest.raises(storage.CloudStorageError):
        storage.storage_preflight(b'{"x":"1234567890"}')


def test_expanded_bound_and_ambiguous_envelope(monkeypatch):
    monkeypatch.setattr(storage, 'MAX_LOGICAL_BYTES', 10)
    with pytest.raises(storage.CloudStorageError, match='size_limit'):
        storage.encode_library(raw_document(), compress=True)
    with pytest.raises(storage.CloudStorageError):
        storage.decode_library(b'{"_trading_lab_storage":{},"strategies":[]}')


def configure_cloud(monkeypatch, raw):
    cloud = engine.GitHubCloudBackup('owner/private', 'fake', branch='main')
    cloud._repository_checked = True
    sha = hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest()
    metadata = {'sha': sha, 'size': len(raw), 'encoding': 'base64',
                'content': base64.b64encode(raw).decode()}
    monkeypatch.setattr(cloud, '_request', Mock(side_effect=lambda *args, **kwargs:
        {'content': {'sha': 'b' * 40}} if kwargs.get('method') == 'PUT' else metadata))
    if len(raw) > 1_000_000:
        monkeypatch.setattr(cloud, '_request_bytes', Mock(return_value=raw))
    return cloud


def test_worker_cold_restore_keeps_local_plain_json(monkeypatch, tmp_path):
    raw = raw_document()
    cloud = configure_cloud(monkeypatch, storage.encode_library(raw, compress=True))
    store = engine.StrategyStore(tmp_path, cloud_backup=cloud)
    loaded = store.load()
    assert loaded['audit'] == json.loads(raw)['audit']
    assert store.path.read_bytes() == raw
    assert storage.MARKER not in json.loads(store.path.read_bytes())
    assert cloud._compressed_storage
    assert store.load_latest() == loaded


def test_worker_rewrite_preserves_compression_without_env(monkeypatch):
    monkeypatch.delenv('TRADING_LAB_COMPRESSED_CLOUD_STORAGE', raising=False)
    raw = raw_document()
    cloud = configure_cloud(monkeypatch, storage.encode_library(raw, compress=True))
    document = json.loads(raw); document['updated_at'] = 'new'
    result = cloud.save_library(document, previous_updated_at='frozen')
    writes = [call.kwargs for call in cloud._request.call_args_list if call.kwargs.get('method') == 'PUT']
    encoded = base64.b64decode(writes[0]['payload']['content'])
    restored, compressed = storage.decode_library(encoded)
    assert compressed and json.loads(restored) == document
    assert result['library'] == document


def test_desktop_reads_and_rest_writes_compressed_document(monkeypatch):
    client = GitHubJSONFile(GitHubLibraryConfig('owner/private'), 'fake')
    raw = raw_document(); stored = storage.encode_library(raw, compress=True)
    sha = hashlib.sha1(f'blob {len(stored)}\0'.encode() + stored).hexdigest()
    revision = 'a' * 40
    monkeypatch.setattr(client, 'head_revision', lambda: revision)
    posted = []
    def request(url, **kwargs):
        if '/contents/' in url:
            return {'sha': sha, 'content': base64.b64encode(stored).decode(), 'encoding': 'base64'}
        if kwargs.get('method') == 'POST':
            posted.append((url, kwargs['payload']))
            return {'sha': 'b' * 40}
        if '/git/commits/' in url:
            return {'tree': {'sha': 'c' * 40}}
        return {'object': {'sha': 'b' * 40}}
    monkeypatch.setattr(client, '_request', request)
    document = client.read()
    assert document.data == json.loads(raw)
    client.write(document.data, expected_revision=revision, message='test')
    payload = next(v for u,v in posted if u.endswith('/git/blobs'))
    restored, compressed = storage.decode_library(payload['content'].encode())
    assert compressed and json.loads(restored) == document.data


def test_desktop_preflight_refuses_before_upload(monkeypatch):
    monkeypatch.setattr(storage, 'WRITE_LIMIT_BYTES', 10)
    client = GitHubJSONFile(GitHubLibraryConfig('owner/private'), 'fake')
    monkeypatch.setattr(client, 'head_revision', lambda: 'a' * 40)
    request = Mock(side_effect=AssertionError('No network upload allowed'))
    monkeypatch.setattr(client, '_request', request)
    with pytest.raises(GitHubLibraryError, match='size_limit'):
        client.write({'evidence': 'preserve'}, expected_revision='a' * 40, message='test')
    request.assert_not_called()


def test_capacity_failure_preserves_local_result_and_remote(monkeypatch, tmp_path):
    cloud = engine.GitHubCloudBackup('owner/private', 'fake', branch='main')
    monkeypatch.setattr(cloud, 'read_library', lambda **kwargs: None)
    store = engine.StrategyStore(tmp_path, cloud_backup=cloud)
    monkeypatch.setattr(storage, 'WRITE_LIMIT_BYTES', 10)
    upload = Mock(side_effect=AssertionError('must not upload'))
    monkeypatch.setattr(cloud, '_request', upload)
    document = {'strategies': [], 'research_queue': [{'id': 'done', 'status': 'complete'}],
                'evidence': {'checkpoint': 'critical', 'hash': 'b' * 64}}
    before = deepcopy(document)
    with pytest.raises(engine.AppError, match='Saved locally.*size_limit'):
        store.save(document)
    assert store.load()['evidence'] == before['evidence']
    assert store.load()['research_queue'] == before['research_queue']
    assert document == before
    assert 'size_limit' in store.cloud_status()['last_error']
    upload.assert_not_called()


def test_capacity_failure_is_not_retried(monkeypatch):
    store = Mock()
    store.save.side_effect = engine.AppError('Saved locally, but permanent cloud backup failed: [size_limit]')
    sleep = Mock(); monkeypatch.setattr(worker.time, 'sleep', sleep)
    with pytest.raises(engine.AppError, match='size_limit'):
        worker.persist_store(store, {'evidence': 'exact'})
    store.sync_cloud_backup.assert_not_called(); sleep.assert_not_called()


def test_preflight_stops_main_before_outbox_or_claim(monkeypatch):
    store = Mock()
    store.cloud_backup.storage_preflight.side_effect = engine.AppError('[size_limit]')
    monkeypatch.setattr(worker, 'build_store', lambda: store)
    outbox = Mock(); claim = Mock()
    monkeypatch.setattr(worker, 'build_live_learning_outbox_store', outbox)
    monkeypatch.setattr(worker, 'claim_next_research_job', claim)
    with pytest.raises(engine.AppError, match='size_limit'):
        worker.main()
    outbox.assert_not_called(); claim.assert_not_called(); store.save.assert_not_called()


def test_health_does_not_call_full_read_or_hide_capacity(monkeypatch, tmp_path):
    cloud = engine.GitHubCloudBackup('owner/private', 'fake', branch='main')
    monkeypatch.setattr(cloud, 'library_revision', lambda: {'sha': 'a' * 40, 'size': storage.HARD_LIMIT_BYTES - 88})
    read = Mock(side_effect=AssertionError('Health must use metadata only'))
    monkeypatch.setattr(cloud, 'read_library', read)
    store = engine.StrategyStore(tmp_path, cloud_backup=cloud)
    store._record_cloud_status(last_write_at='prior successful write')
    status = store.persistence_status(verify=True)
    assert status['verified'] and status['write_verified']
    assert not status['healthy']
    assert 'size_limit' in status['verification_error']
    assert status['storage_bytes'] == storage.HARD_LIMIT_BYTES - 88
    read.assert_not_called()


def test_cloud_cas_guards_still_reject_changed_timestamp(monkeypatch):
    cloud = configure_cloud(monkeypatch, storage.encode_library(raw_document(), compress=True))
    data = json.loads(raw_document()); data['updated_at'] = 'new'
    with pytest.raises(engine.AppError, match='newer saved library'):
        cloud.save_library(data, previous_updated_at='stale')
    assert not any(c.kwargs.get('method') == 'PUT' for c in cloud._request.call_args_list)


def test_direct_git_writer_refuses_oversize_before_git(monkeypatch):
    from hybrid_runtime import github_git_upload as upload
    monkeypatch.setattr(storage, 'WRITE_LIMIT_BYTES', 10)
    run = Mock(side_effect=AssertionError('Must fail before Git'))
    monkeypatch.setattr(upload, '_run_git', run)
    with pytest.raises(GitHubLibraryError, match='size_limit'):
        upload.write_large_library(GitHubLibraryConfig('owner/private'), 'fake', b'x' * 11,
                                   expected_revision='a' * 40, message='test')
    run.assert_not_called()
