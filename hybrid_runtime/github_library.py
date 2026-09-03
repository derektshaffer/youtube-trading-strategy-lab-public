"""GitHub-backed JSON storage used by the desktop-to-cloud research bridge.

The existing Trading Intelligence library can exceed the inline payload size
of GitHub's Contents API. Large reads use compressed raw Git blobs, and
writes use Git data objects or compressed Git transport with a non-force update.
A concurrent writer causes an explicit conflict instead of silently overwriting research.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii
import gzip
from hashlib import sha1
from http.client import HTTPException
import json
from time import sleep
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import zlib

from .contracts import canonical_json


GITHUB_API_URL = "https://api.github.com"
# Match the existing backup engine's conservative REST payload boundary.
GITHUB_LIBRARY_API_SAFE_BYTES = 1_000_000


class GitHubLibraryError(RuntimeError):
    """A secret-safe remote library error."""


class GitHubLibraryConflict(GitHubLibraryError):
    """The branch moved after it was read; the caller must reload and retry."""


@dataclass(frozen=True, slots=True)
class GitHubLibraryConfig:
    repository: str
    path: str = "youtube-strategy-lab/strategy_library.json"
    branch: str = "main"
    action_repository: str = "derektshaffer/youtube-trading-strategy-lab-public"
    workflow_file: str = "continuous-trading-research.yml"
    workflow_ref: str = "main"

    def __post_init__(self) -> None:
        repository = str(self.repository or "").strip().strip("/")
        path = str(self.path or "").strip().strip("/")
        branch = str(self.branch or "main").strip() or "main"
        action_repository = str(self.action_repository or "").strip().strip("/")
        workflow_file = str(self.workflow_file or "").strip().strip("/")
        workflow_ref = str(self.workflow_ref or "main").strip() or "main"
        if repository.count("/") != 1:
            raise ValueError("repository must use owner/name form")
        if not path or path.startswith(".") or ".." in path.split("/"):
            raise ValueError("path must be a safe repository-relative JSON path")
        if action_repository and action_repository.count("/") != 1:
            raise ValueError("action_repository must use owner/name form")
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "branch", branch)
        object.__setattr__(self, "action_repository", action_repository)
        object.__setattr__(self, "workflow_file", workflow_file)
        object.__setattr__(self, "workflow_ref", workflow_ref)


@dataclass(frozen=True, slots=True)
class RemoteJSONDocument:
    data: dict[str, Any]
    revision: str
    blob_sha: str


class GitHubJSONFile:
    """Read and atomically update one JSON file on a GitHub branch."""

    def __init__(
        self,
        config: GitHubLibraryConfig,
        token: str,
        *,
        timeout_seconds: float = 45.0,
    ) -> None:
        self.config = config
        self._token = str(token or "").strip()
        if not self._token:
            raise ValueError("A GitHub token is required")
        self.timeout_seconds = max(5.0, min(180.0, float(timeout_seconds)))

    def _api(self, suffix: str) -> str:
        return f"{GITHUB_API_URL}/repos/{self.config.repository}/{suffix.lstrip('/')}"

    def _request(
        self,
        url: str,
        *,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
        expected_statuses: tuple[int, ...] = (200,),
        raw_response: bool = False,
    ) -> Any:
        body = None
        if payload is not None:
            body = canonical_json(dict(payload)).encode("utf-8")
        request = Request(
            url,
            data=body,
            method=method,
            headers={
                "Accept": (
                    "application/vnd.github.raw+json"
                    if raw_response else "application/vnd.github+json"
                ),
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept-Encoding": "gzip" if raw_response else "identity",
                "User-Agent": "Trading-Intelligence-Desktop-Cloud-Bridge/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            # Retry only reads: replaying a POST/PATCH after an uncertain response
            # could duplicate writes. Never decode a partial response body.
            attempts = 3 if method == "GET" else 1
            for attempt in range(attempts):
                try:
                    timeout = (
                        max(90.0, self.timeout_seconds)
                        if raw_response else self.timeout_seconds
                    )
                    with urlopen(request, timeout=timeout) as response:
                        status = int(getattr(response, "status", 200))
                        raw = response.read()
                        encoding = str(response.headers.get("Content-Encoding") or "").lower()
                        if encoding == "gzip":
                            raw = gzip.decompress(raw)
                        elif encoding not in {"", "identity"}:
                            raise GitHubLibraryError("GitHub returned an unsupported transfer encoding.")
                    break
                except HTTPError:
                    raise
                except (HTTPException, URLError, TimeoutError, OSError, EOFError, zlib.error):
                    if attempt + 1 == attempts:
                        raise
                    sleep(0.25 * (attempt + 1))
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
                decoded = json.loads(detail)
                message = str(decoded.get("message") or "") if isinstance(decoded, dict) else ""
            except (HTTPException, OSError, ValueError, AttributeError):
                message = ""
            safe = " ".join(message.replace(self._token, "<redacted>").split())[:350]
            if exc.code in {409, 422}:
                raise GitHubLibraryConflict(
                    f"GitHub branch changed during update ({exc.code})."
                ) from exc
            if exc.code in {401, 403}:
                raise GitHubLibraryError(
                    "GitHub denied the cloud bridge request. Check repository and workflow permissions."
                ) from exc
            if exc.code == 404:
                raise GitHubLibraryError(
                    "The configured GitHub repository, branch, workflow, or library path was not found."
                ) from exc
            raise GitHubLibraryError(
                f"GitHub request failed ({exc.code})" + (f": {safe}" if safe else ".")
            ) from exc
        except (HTTPException, URLError, TimeoutError, OSError, EOFError, zlib.error) as exc:
            raise GitHubLibraryError(
                f"GitHub cloud bridge transfer failed after {attempts} "
                f"attempt(s) ({type(exc).__name__})."
            ) from exc
        if status not in expected_statuses:
            raise GitHubLibraryError(f"GitHub returned unexpected status {status}.")
        if raw_response:
            return raw
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubLibraryError("GitHub returned invalid JSON metadata.") from exc

    def head_revision(self) -> str:
        encoded = quote(self.config.branch, safe="")
        response = self._request(self._api(f"git/ref/heads/{encoded}"))
        try:
            revision = str(response["object"]["sha"])
        except (TypeError, KeyError) as exc:
            raise GitHubLibraryError("GitHub branch metadata omitted its commit SHA.") from exc
        if not revision:
            raise GitHubLibraryError("GitHub branch metadata contained an empty commit SHA.")
        return revision

    @staticmethod
    def _decode_blob(response: Mapping[str, Any]) -> bytes:
        content = str(response.get("content") or "")
        encoding = str(response.get("encoding") or "").strip().lower()
        if not content:
            raise GitHubLibraryError("The remote research library is empty.")
        try:
            if encoding == "base64":
                return base64.b64decode(content.encode("ascii"), validate=False)
            if encoding in {"utf-8", "utf8", ""}:
                return content.encode("utf-8")
        except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
            raise GitHubLibraryError("The remote research library could not be decoded.") from exc
        raise GitHubLibraryError(f"Unsupported GitHub blob encoding: {encoding}")

    def read(self) -> RemoteJSONDocument:
        revision = self.head_revision()
        encoded_path = quote(self.config.path, safe="/")
        # Pin metadata and content to the same commit used by the later
        # compare-and-swap write, even if main changes during the download.
        contents_url = self._api(f"contents/{encoded_path}?ref={quote(revision, safe='')}")
        metadata = self._request(contents_url)
        if not isinstance(metadata, Mapping):
            raise GitHubLibraryError("The configured research-library path is not a file.")
        blob_sha = str(metadata.get("sha") or "").strip()
        if len(blob_sha) != 40 or any(character not in "0123456789abcdef" for character in blob_sha):
            raise GitHubLibraryError("GitHub did not provide a valid research-library blob SHA.")
        content = str(metadata.get("content") or "")
        if content:
            raw = self._decode_blob(metadata)
        else:
            # Avoid both the base64 wrapper and the uncompressed Contents raw
            # response: both truncated for the real private library. The raw
            # Git Blob endpoint supports gzip. Use only our API URL, not
            # a metadata-provided URL that could receive the bearer token.
            raw = self._request(self._api(f"git/blobs/{blob_sha}"), raw_response=True)
        digest = sha1(f"blob {len(raw)}\0".encode("ascii"))
        digest.update(raw)
        if digest.hexdigest() != blob_sha:
            raise GitHubLibraryError("The downloaded research library failed Git blob integrity verification.")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubLibraryError("The remote research library is not valid UTF-8 JSON.") from exc
        if not isinstance(decoded, dict):
            raise GitHubLibraryError("The remote research library must be a JSON object.")
        return RemoteJSONDocument(data=decoded, revision=revision, blob_sha=blob_sha)

    def write(
        self,
        document: Mapping[str, Any],
        *,
        expected_revision: str,
        message: str,
    ) -> str:
        expected = str(expected_revision or "").strip()
        if not expected:
            raise ValueError("expected_revision is required")
        current = self.head_revision()
        if current != expected:
            raise GitHubLibraryConflict("GitHub branch moved before the research-library update.")

        compact = canonical_json(dict(document))
        serialized = compact.encode("utf-8")
        if len(serialized) > GITHUB_LIBRARY_API_SAFE_BYTES:
            from .github_git_upload import write_large_library

            return write_large_library(
                self.config, self._token, serialized,
                expected_revision=expected, message=message,
            )

        commit = self._request(self._api(f"git/commits/{quote(expected, safe='')}"))
        try:
            base_tree = str(commit["tree"]["sha"])
        except (TypeError, KeyError) as exc:
            raise GitHubLibraryError("GitHub commit metadata omitted its tree SHA.") from exc

        blob = self._request(
            self._api("git/blobs"),
            method="POST",
            payload={"content": compact, "encoding": "utf-8"},
            expected_statuses=(201,),
        )
        blob_sha = str((blob or {}).get("sha") or "").strip()
        if not blob_sha:
            raise GitHubLibraryError("GitHub did not return a blob SHA.")

        tree = self._request(
            self._api("git/trees"),
            method="POST",
            payload={
                "base_tree": base_tree,
                "tree": [
                    {
                        "path": self.config.path,
                        "mode": "100644",
                        "type": "blob",
                        "sha": blob_sha,
                    }
                ],
            },
            expected_statuses=(201,),
        )
        tree_sha = str((tree or {}).get("sha") or "").strip()
        if not tree_sha:
            raise GitHubLibraryError("GitHub did not return an updated tree SHA.")

        created = self._request(
            self._api("git/commits"),
            method="POST",
            payload={
                "message": str(message or "Update Trading Intelligence cloud queue")[:240],
                "tree": tree_sha,
                "parents": [expected],
            },
            expected_statuses=(201,),
        )
        commit_sha = str((created or {}).get("sha") or "").strip()
        if not commit_sha:
            raise GitHubLibraryError("GitHub did not return the new commit SHA.")

        # A non-force update is compare-and-swap in practice here: our new commit
        # descends from expected. If another writer advanced the ref first, GitHub
        # rejects this update as non-fast-forward instead of losing their changes.
        encoded_branch = quote(self.config.branch, safe="")
        self._request(
            self._api(f"git/refs/heads/{encoded_branch}"),
            method="PATCH",
            payload={"sha": commit_sha, "force": False},
            expected_statuses=(200,),
        )
        return commit_sha

    def dispatch_workflow(
        self,
        inputs: Mapping[str, Any] | None = None,
        *,
        workflow_file: str | None = None,
    ) -> bool:
        if not self.config.action_repository:
            return False
        selected_workflow = str(workflow_file or self.config.workflow_file or "").strip().strip("/")
        if not selected_workflow:
            return False
        workflow = quote(selected_workflow, safe="")
        repository = self.config.action_repository
        url = f"{GITHUB_API_URL}/repos/{repository}/actions/workflows/{workflow}/dispatches"
        payload: dict[str, Any] = {"ref": self.config.workflow_ref}
        if inputs:
            payload["inputs"] = {str(key): str(value) for key, value in inputs.items()}
        self._request(
            url,
            method="POST",
            payload=payload,
            expected_statuses=(204,),
        )
        return True
