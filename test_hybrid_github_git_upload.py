from __future__ import annotations

from hashlib import sha1
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from hybrid_runtime import github_git_upload as upload
from hybrid_runtime.contracts import canonical_json
from hybrid_runtime.github_library import GitHubJSONFile, GitHubLibraryConfig, GitHubLibraryConflict, GitHubLibraryError


GIT = shutil.which("git")
TOKEN = "test-token-never-in-logs"


def git(root, *arguments, input_bytes=None):
    return subprocess.run(
        [GIT, "-c", "user.name=Test", "-c", "user.email=test@example.test", *arguments],
        cwd=root, input=input_bytes, capture_output=True, check=True, timeout=60,
    ).stdout.decode().strip()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    if not GIT:
        pytest.skip("Git is required for real transport regressions")
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    origin.mkdir()
    seed.mkdir()
    git(origin, "init", "--bare")
    git(seed, "init", "-b", "main")
    (seed / "lab").mkdir()
    (seed / "lab/library.json").write_text('{"existing":true}')
    (seed / "untouched.txt").write_text("preserve this file\n")
    (seed / "executable.sh").write_text("#!/bin/sh\nexit 0\n")
    (seed / "executable.sh").chmod(0o755)
    (seed / ".gitattributes").write_text("lab/*.json filter=untrusted\n")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "Initial library")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "origin", "main")
    expected = git(seed, "rev-parse", "HEAD")
    calls = []
    original = upload._run_git

    def intercept(executable, arguments, **kwargs):
        arguments = list(arguments)
        if arguments[:3] == ["remote", "add", "origin"]:
            arguments[3] = str(origin)
        assert TOKEN not in str(arguments)
        root = kwargs["root"]
        assert root.stat().st_mode & 0o777 == 0o700
        helper = Path(kwargs["environment"]["GIT_ASKPASS"])
        assert helper.stat().st_mode & 0o777 == 0o700
        assert TOKEN not in helper.read_text()
        calls.append((list(arguments), root))
        return original(executable, arguments, **kwargs)

    monkeypatch.setattr(upload, "_run_git", intercept)
    return origin, seed, expected, calls, intercept


def config(**kwargs):
    return GitHubLibraryConfig(repository="owner/private-data", path="lab/library.json", **kwargs)


def write(expected, serialized):
    return upload.write_large_library(config(), TOKEN, serialized, expected_revision=expected, message="Queue test research")


@pytest.mark.parametrize("size", [1_000_001, 87_000_000])
def test_real_git_large_write_is_lossless_single_file_and_non_force(repository, size):
    origin, seed, expected, calls, _ = repository
    document = {"history": "x" * size, "research_queue": [{"id": "existing-job"}], "unicode": "café"}
    serialized = canonical_json(document).encode()
    commit = write(expected, serialized)
    assert git(origin, "rev-parse", "refs/heads/main") == commit
    assert git(origin, "rev-parse", f"{commit}^") == expected
    assert git(origin, "diff-tree", "--no-commit-id", "--name-only", "-r", commit) == "lab/library.json"
    restored = git(origin, "show", f"{commit}:lab/library.json")
    assert json.loads(restored) == document
    assert git(origin, "show", f"{commit}:untouched.txt") == "preserve this file"
    assert git(origin, "ls-tree", commit, "executable.sh").startswith("100755 ")
    pushes = [arguments for arguments, _ in calls if arguments[0] == "push"]
    assert pushes == [["push", "--porcelain", "origin", f"{commit}:refs/heads/main"]]
    assert not any(arguments[0] in {"checkout", "add", "commit", "merge", "rebase"} for arguments, _ in calls)
    assert all(not root.exists() for _, root in calls)


def advance(seed):
    (seed / "new-research.txt").write_text("new research must survive\n")
    git(seed, "add", "new-research.txt")
    git(seed, "commit", "-m", "Concurrent research")
    git(seed, "push", "origin", "main")
    return git(seed, "rev-parse", "HEAD")


def test_branch_move_before_fetch_never_pushes_stale_library(repository):
    origin, seed, expected, calls, _ = repository
    concurrent = advance(seed)
    with pytest.raises(GitHubLibraryConflict, match="before"):
        write(expected, b'{"new":true}')
    assert git(origin, "rev-parse", "refs/heads/main") == concurrent
    assert not any(arguments[0] == "push" for arguments, _ in calls)


def test_branch_move_during_upload_is_not_overwritten(repository, monkeypatch):
    origin, seed, expected, calls, intercept = repository
    concurrent = []

    def race(executable, arguments, **kwargs):
        if arguments[0] == "push":
            concurrent.append(advance(seed))
        return intercept(executable, arguments, **kwargs)

    monkeypatch.setattr(upload, "_run_git", race)
    with pytest.raises(GitHubLibraryConflict, match="moved"):
        write(expected, b'{"new":true}')
    assert git(origin, "rev-parse", "refs/heads/main") == concurrent[0]
    assert json.loads(git(origin, "show", "main:lab/library.json")) == {"existing": True}
    assert sum(arguments[0] == "push" for arguments, _ in calls) == 1


def test_lost_success_response_is_confirmed_without_replaying_push(repository, monkeypatch):
    origin, _, expected, calls, intercept = repository

    def lost_response(executable, arguments, **kwargs):
        result = intercept(executable, arguments, **kwargs)
        if arguments[0] == "push":
            raise GitHubLibraryError("Connection lost after acceptance")
        return result

    monkeypatch.setattr(upload, "_run_git", lost_response)
    commit = write(expected, b'{"new":true}')
    assert git(origin, "rev-parse", "refs/heads/main") == commit
    assert sum(arguments[0] == "push" for arguments, _ in calls) == 1


def test_failed_push_retains_remote_state_and_cleans_temporary_credentials(repository, monkeypatch):
    origin, _, expected, calls, intercept = repository
    pushes = []

    def failed(executable, arguments, **kwargs):
        if arguments[0] == "push":
            pushes.append(arguments)
            raise GitHubLibraryError("Upload unavailable")
        return intercept(executable, arguments, **kwargs)

    monkeypatch.setattr(upload, "_run_git", failed)
    with pytest.raises(GitHubLibraryError, match="Upload unavailable"):
        write(expected, b'{"new":true}')
    assert git(origin, "rev-parse", "refs/heads/main") == expected
    assert len(pushes) == 1
    assert all(not root.exists() for _, root in calls)


def test_large_writer_routing_never_posts_the_large_json_payload(monkeypatch):
    client = GitHubJSONFile(config(), TOKEN)
    expected = "a" * 40
    monkeypatch.setattr(client, "head_revision", lambda: expected)
    calls = []

    def uploaded(settings, token, serialized, **kwargs):
        calls.append((settings, token, serialized, kwargs))
        return "b" * 40

    monkeypatch.setattr(upload, "write_large_library", uploaded)
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: pytest.fail("Large payload used REST"))
    document = {"data": "é" * 500_001}
    assert client.write(document, expected_revision=expected, message="test") == "b" * 40
    assert len(calls) == 1
    assert json.loads(calls[0][2]) == document
    assert calls[0][3]["expected_revision"] == expected


def test_missing_git_is_actionable_and_secret_safe(monkeypatch):
    monkeypatch.setattr(upload.shutil, "which", lambda *args, **kwargs: None)
    with pytest.raises(GitHubLibraryError, match="Git is required") as error:
        write("a" * 40, b"{}")
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize("kind", ["timeout", "stderr"])
def test_subprocess_failures_do_not_leak_credentials(tmp_path, monkeypatch, kind):
    def fail(*args, **kwargs):
        if kind == "timeout":
            raise subprocess.TimeoutExpired([TOKEN], 300, stderr=TOKEN.encode())
        return subprocess.CompletedProcess([], 1, b"", f"Authorization: Bearer {TOKEN}".encode())

    monkeypatch.setattr(upload.subprocess, "run", fail)
    with pytest.raises(GitHubLibraryError) as error:
        upload._run_git("git", ["push"], root=tmp_path, environment={}, operation="push", timeout=300)
    assert TOKEN not in str(error.value)
    assert error.value.__cause__ is None


def test_subprocess_environment_drops_overrides_and_other_secrets(tmp_path, monkeypatch):
    for key in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "GIT_TRACE", "GIT_TRACE_CURL", "GIT_SSL_NO_VERIFY", "GIT_DIR", "GIT_CONFIG_COUNT", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        monkeypatch.setenv(key, "unwanted")
    monkeypatch.setenv("SSL_CERT_FILE", "/trusted/certifi.pem")
    environment = upload._git_environment(tmp_path / "askpass.sh", TOKEN)
    assert "unwanted" not in environment.values()
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert environment["GIT_SSL_CAINFO"] == "/trusted/certifi.pem"
    assert environment["TRADING_INTELLIGENCE_GIT_UPLOAD_TOKEN"] == TOKEN


def test_blob_hash_mismatch_is_rejected_before_push(repository, monkeypatch):
    _, _, expected, calls, intercept = repository

    def corrupt(executable, arguments, **kwargs):
        result = intercept(executable, arguments, **kwargs)
        return "a" * 40 if arguments[0] == "hash-object" else result

    monkeypatch.setattr(upload, "_run_git", corrupt)
    with pytest.raises(GitHubLibraryError, match="integrity"):
        write(expected, b"{}")
    assert not any(arguments[0] == "push" for arguments, _ in calls)


@pytest.mark.parametrize("branch", ["main:other", "../main", "main with spaces"])
def test_invalid_branch_cannot_become_a_push_refspec(repository, branch):
    _, _, expected, calls, _ = repository
    with pytest.raises(GitHubLibraryError):
        upload.write_large_library(config(branch=branch), TOKEN, b"{}", expected_revision=expected, message="test")
    assert not any(arguments[0] in {"fetch", "push"} for arguments, _ in calls)
