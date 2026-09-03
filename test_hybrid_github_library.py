from __future__ import annotations

import base64
import gzip
from hashlib import sha1
from http.client import IncompleteRead
from io import BytesIO
import json
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from hybrid_runtime.github_library import (
    GitHubJSONFile,
    GitHubLibraryConfig,
    GitHubLibraryConflict,
    GitHubLibraryError,
)


PAYLOAD = b'{"strategies":[],"research_queue":[]}'
BLOB_SHA = sha1(f"blob {len(PAYLOAD)}\0".encode() + PAYLOAD).hexdigest()
REVISION = "a" * 40


class Response(BytesIO):
    status = 200
    headers = {}


def json_response(value):
    return Response(json.dumps(value).encode())


def client():
    return GitHubJSONFile(
        GitHubLibraryConfig(repository="owner/private-data", path="lab/library.json"),
        "test-secret-token",
    )


def metadata(*, inline=False):
    return {
        "type": "file",
        "sha": BLOB_SHA,
        "encoding": "base64" if inline else "none",
        "content": base64.b64encode(PAYLOAD).decode() if inline else "",
        "git_url": "https://untrusted.example/blob",
    }


@pytest.mark.parametrize("inline", [True, False])
def test_library_read_is_commit_pinned_and_integrity_checked(inline):
    responses = [json_response({"object": {"sha": REVISION}}), json_response(metadata(inline=inline))]
    if not inline:
        responses.append(Response(PAYLOAD))
    with patch("hybrid_runtime.github_library.urlopen", side_effect=responses) as opened:
        document = client().read()
    assert document.data == json.loads(PAYLOAD)
    assert document.revision == REVISION
    assert document.blob_sha == BLOB_SHA
    assert opened.call_count == (2 if inline else 3)
    for call in opened.call_args_list[1:2]:
        request = call.args[0]
        assert request.full_url == f"https://api.github.com/repos/owner/private-data/contents/lab/library.json?ref={REVISION}"
        assert request.get_method() == "GET"
    if not inline:
        request = opened.call_args.args[0]
        assert request.full_url == f"https://api.github.com/repos/owner/private-data/git/blobs/{BLOB_SHA}"
        assert request.get_header("Accept") == "application/vnd.github.raw+json"
        assert request.get_header("Accept-encoding") == "gzip"
        assert opened.call_args.kwargs["timeout"] >= 90


def test_compressed_raw_blob_is_decoded_and_hash_verified():
    compressed = Response(gzip.compress(PAYLOAD))
    compressed.headers = {"Content-Encoding": "gzip"}
    responses = [json_response({"object": {"sha": REVISION}}), json_response(metadata()), compressed]
    with patch("hybrid_runtime.github_library.urlopen", side_effect=responses):
        assert client().read().data == json.loads(PAYLOAD)


def test_truncated_gzip_is_retried_without_accepting_partial_data():
    truncated = Response(gzip.compress(PAYLOAD)[:-8])
    truncated.headers = {"Content-Encoding": "gzip"}
    complete = Response(gzip.compress(PAYLOAD))
    complete.headers = {"Content-Encoding": "gzip"}
    with patch("hybrid_runtime.github_library.urlopen", side_effect=[truncated, complete]) as opened, patch("hybrid_runtime.github_library.sleep"):
        assert client()._request("https://api.github.com/example", raw_response=True) == PAYLOAD
    assert opened.call_count == 2


def test_interrupted_library_download_retries_from_scratch():
    class Interrupted(Response):
        def read(self, *args):
            raise IncompleteRead(b"partial", 100)

    responses = [
        json_response({"object": {"sha": REVISION}}),
        json_response(metadata()),
        Interrupted(),
        Response(PAYLOAD),
    ]
    with patch("hybrid_runtime.github_library.urlopen", side_effect=responses) as opened, patch("hybrid_runtime.github_library.sleep"):
        document = client().read()
    assert document.data == json.loads(PAYLOAD)
    assert opened.call_count == 4
    assert opened.call_args_list[-1].args[0].full_url == opened.call_args_list[-2].args[0].full_url


def test_repeated_partial_download_is_bounded_and_secret_safe():
    with patch("hybrid_runtime.github_library.urlopen", side_effect=IncompleteRead(b"test-secret-token", 10)) as opened, patch("hybrid_runtime.github_library.sleep"):
        with pytest.raises(GitHubLibraryError, match=r"3 attempt\(s\) \(IncompleteRead\)") as error:
            client()._request("https://api.github.com/example", raw_response=True)
    assert opened.call_count == 3
    assert "test-secret-token" not in str(error.value)


@pytest.mark.parametrize("payload", [b'{"strategies":[]}', PAYLOAD[:-1]])
def test_wrong_or_truncated_library_is_never_accepted(payload):
    responses = [json_response({"object": {"sha": REVISION}}), json_response(metadata()), Response(payload)]
    with patch("hybrid_runtime.github_library.urlopen", side_effect=responses):
        with pytest.raises(GitHubLibraryError, match="integrity verification"):
            client().read()


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_uncertain_mutation_is_not_retried(method):
    with patch("hybrid_runtime.github_library.urlopen", side_effect=IncompleteRead(b"", 10)) as opened:
        with pytest.raises(GitHubLibraryError, match=r"1 attempt\(s\)"):
            client()._request("https://api.github.com/example", method=method, payload={})
    assert opened.call_count == 1


def test_permission_failure_is_not_retried():
    denied = HTTPError("https://api.github.com/example", 403, "Forbidden", {}, BytesIO(b'{}'))
    with patch("hybrid_runtime.github_library.urlopen", side_effect=denied) as opened:
        with pytest.raises(GitHubLibraryError, match="permissions"):
            client()._request("https://api.github.com/example", raw_response=True)
    assert opened.call_count == 1


def test_concurrent_branch_update_still_fails_closed():
    with patch("hybrid_runtime.github_library.urlopen", return_value=json_response({"object": {"sha": "b" * 40}})) as opened:
        with pytest.raises(GitHubLibraryConflict, match="moved"):
            client().write({}, expected_revision=REVISION, message="test")
    assert opened.call_count == 1


def test_completed_write_remains_non_force():
    responses = [
        json_response({"object": {"sha": REVISION}}),
        json_response({"tree": {"sha": "base-tree"}}),
        json_response({"sha": "blob"}),
        json_response({"sha": "tree"}),
        json_response({"sha": "commit"}),
        json_response({}),
    ]
    for response in responses[2:5]:
        response.status = 201
    with patch("hybrid_runtime.github_library.urlopen", side_effect=responses) as opened:
        assert client().write({}, expected_revision=REVISION, message="test") == "commit"
    request = opened.call_args.args[0]
    assert request.get_method() == "PATCH"
    assert json.loads(request.data) == {"sha": "commit", "force": False}
