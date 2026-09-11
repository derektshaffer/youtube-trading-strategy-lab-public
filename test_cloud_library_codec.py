import base64
from copy import deepcopy
import hashlib
import json
import struct
from unittest.mock import patch

import pytest

import cloud_library_codec as codec
from youtube_strategy_engine import GitHubCloudBackup, StrategyStore, AppError, CloudBackupConflict
from hybrid_runtime.github_library import GitHubJSONFile, GitHubLibraryConfig


def fixture():
    return {**StrategyStore.blank(), "updated_at": "2026-09-11T10:00:00Z",
            "knowledge_sources": [{"id": "large", "body": "all records retained — 雪\n" * 80000}]}


def raw_json(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()


def blob(raw):
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


def cloud_reader(raw):
    cloud = GitHubCloudBackup("fixture/private", "fixture-token", branch="fixture", path="library.json")
    cloud._repository_checked = True
    record = {"type": "file", "sha": blob(raw), "size": len(raw), "encoding": "base64",
              "content": base64.b64encode(raw).decode()}
    cloud._request = lambda *a, **k: record
    cloud._request_bytes = lambda *a, **k: raw
    return cloud


@pytest.mark.parametrize("mode", ["json", "gzip-v1"])
def test_exact_bytes_hashes_and_every_field_survive(mode):
    raw = raw_json(fixture())
    wire = codec.encode_library_bytes(raw, storage_format=mode)
    restored = codec.decode_library_bytes(wire)
    assert restored == raw
    assert hashlib.sha256(restored).digest() == hashlib.sha256(raw).digest()
    assert json.loads(restored) == fixture()


def test_compression_requires_explicit_rollout(monkeypatch):
    monkeypatch.delenv("GITHUB_BACKUP_STORAGE_FORMAT", raising=False)
    raw = raw_json(fixture())
    assert codec.encode_library_bytes(raw) == raw
    monkeypatch.setenv("GITHUB_BACKUP_STORAGE_FORMAT", "gzip-v1")
    assert codec.encode_library_bytes(raw).startswith(codec.MAGIC)
    monkeypatch.setenv("GITHUB_BACKUP_STORAGE_FORMAT", "typo")
    with pytest.raises(codec.LibraryEncodingError): codec.encode_library_bytes(raw)


@pytest.mark.parametrize("damage", ["body", "hash", "length", "truncated", "trailing", "version", "bound"])
def test_corrupt_or_unsupported_envelope_fails_closed(damage):
    wire = bytearray(codec.encode_library_bytes(raw_json(fixture()), storage_format="gzip-v1"))
    if damage == "body": wire[-10] ^= 255
    if damage == "hash": wire[20] ^= 255
    if damage == "length": wire[15] ^= 1
    if damage == "truncated": del wire[-5:]
    if damage == "trailing": wire.extend(b"trailing")
    if damage == "version": wire[7] = ord("2")
    if damage == "bound": wire[8:16] = struct.pack(">Q", codec.MAX_JSON_BYTES + 1)
    with pytest.raises(codec.LibraryEncodingError): codec.decode_library_bytes(bytes(wire))


def test_remaining_wire_capacity_limit_still_fails_closed(monkeypatch):
    monkeypatch.setattr(codec, "GIT_BLOB_LIMIT", 100)
    with pytest.raises(codec.LibraryEncodingError):
        codec.encode_library_bytes(b"x" * 101, storage_format="json")


def test_over_100_mib_logical_library_round_trips_losslessly():
    raw = b'{"strategies":[],"historical_payload":"' + b"retained history;" * 6600000 + b'"}'
    assert len(raw) > codec.GIT_BLOB_LIMIT
    with pytest.raises(codec.LibraryEncodingError): codec.encode_library_bytes(raw, storage_format="json")
    wire = codec.encode_library_bytes(raw, storage_format="gzip-v1")
    assert len(wire) < codec.GIT_BLOB_LIMIT
    assert codec.decode_library_bytes(wire) == raw


@pytest.mark.parametrize("mode", ["json", "gzip-v1"])
def test_cloud_desktop_and_cold_local_restore_share_format(mode, tmp_path):
    expected = fixture()
    wire = codec.encode_library_bytes(raw_json(expected), storage_format=mode)
    cloud = cloud_reader(wire)
    assert cloud.read_library()["library"] == expected
    store = StrategyStore(tmp_path, cloud_backup=cloud)
    assert store.load() == expected
    assert json.loads(store.path.read_text()) == expected
    assert store.load() == expected  # subsequent local load is still ordinary JSON
    desktop = GitHubJSONFile(GitHubLibraryConfig("fixture/private"), "fixture-token")
    desktop.head_revision = lambda: "a" * 40
    desktop._request = cloud._request
    assert desktop.read().data == expected


def test_cloud_sha_always_covers_wire_before_decoding():
    wire = codec.encode_library_bytes(raw_json(fixture()), storage_format="gzip-v1")
    cloud = cloud_reader(wire)
    record = cloud._request("")
    record["sha"] = blob(codec.decode_library_bytes(wire))
    with pytest.raises(CloudBackupConflict): cloud.read_library()


def test_cloud_save_compresses_without_changing_logical_document(monkeypatch):
    monkeypatch.setenv("GITHUB_BACKUP_STORAGE_FORMAT", "gzip-v1")
    data = fixture()
    cloud = cloud_reader(raw_json(data))
    before = cloud.read_library()
    changed = deepcopy(data)
    changed["updated_at"] = "2026-09-11T10:01:00Z"
    changed["research_runs"] = [{"id": "new-result", "outcome": "research_only"}]
    calls = []
    def request(url, **kwargs):
        if kwargs.get("method") == "PUT":
            wire = base64.b64decode(kwargs["payload"]["content"])
            calls.append(wire)
            return {"content": {"sha": blob(wire)}}
        return record
    record = cloud._request("")
    cloud._request = request
    saved = cloud.save_library(changed, previous_updated_at=data["updated_at"], expected_sha=before["sha"])
    assert calls[0].startswith(codec.MAGIC)
    assert json.loads(codec.decode_library_bytes(calls[0])) == changed
    assert saved["library"] == changed


def test_cloned_audit_reader_accepts_encoded_file(tmp_path):
    from research_library_audit import summarize
    data = StrategyStore.blank()
    data["unknown_history"] = "x" * 1100000
    p = tmp_path / "library.json"
    p.write_bytes(raw_json(data))
    expected = summarize(p)
    p.write_bytes(codec.encode_library_bytes(raw_json(data), storage_format="gzip-v1"))
    actual = summarize(p)
    # These reports include wall clock ages; empty fixtures have no age differences.
    expected.pop("generated_at", None)
    actual.pop("generated_at", None)
    assert actual.pop("source_size_bytes") < expected.pop("source_size_bytes")
    assert actual == expected


def test_smoke_ref_and_destination_guards(monkeypatch):
    import cloud_backup_capacity_smoke as smoke
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("FIXTURE_SOURCE_RUN", "123")
    with pytest.raises(RuntimeError): smoke.fixture_branch()
    monkeypatch.setenv("GITHUB_REF", smoke.SOURCE_REF)
    monkeypatch.setenv("GITHUB_BACKUP_REPOSITORY", "fixture/private")
    monkeypatch.setenv("GITHUB_BACKUP_TOKEN", "fixture-token")
    monkeypatch.setenv("GITHUB_BACKUP_BRANCH", "main")
    monkeypatch.setenv("GITHUB_BACKUP_PATH", smoke.FIXTURE_PATH)
    with pytest.raises(RuntimeError): smoke.FixtureBackup()
    readonly = smoke.ReadOnlyProduction("fixture/private", "fixture-token")
    with pytest.raises(RuntimeError): readonly._request("unused", method="PUT")
    with pytest.raises(RuntimeError): readonly.save_library({})
    with pytest.raises(RuntimeError): readonly._save_large_library(b"data")


def test_desktop_rest_writer_encodes_and_preserves_cas(monkeypatch):
    monkeypatch.setenv("GITHUB_BACKUP_STORAGE_FORMAT", "gzip-v1")
    desktop = GitHubJSONFile(GitHubLibraryConfig("fixture/private"), "fixture-token")
    desktop.head_revision = lambda: "a" * 40
    calls = []
    def request(url, **kwargs):
        calls.append((url, kwargs))
        if kwargs.get("method", "GET") == "GET": return {"tree": {"sha": "b" * 40}}
        return {"sha": "c" * 40}
    desktop._request = request
    desktop.write(fixture(), expected_revision="a" * 40, message="fixture")
    payload = next(k["payload"] for u, k in calls if u.endswith("git/blobs"))
    wire = base64.b64decode(payload["content"])
    assert wire.startswith(codec.MAGIC)
    assert json.loads(codec.decode_library_bytes(wire)) == fixture()
    update = calls[-1][1]["payload"]
    assert update["force"] is False
