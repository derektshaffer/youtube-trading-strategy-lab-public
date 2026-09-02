"""GitHub-backed JSON storage used by the desktop-to-cloud research bridge.

The existing Trading Intelligence library can exceed the reliable payload size
of GitHub's Contents API. Reads therefore fall through to the Git Blob API and
writes always use Git data objects with a non-force ref update. A concurrent
writer causes an explicit conflict instead of silently overwriting research.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii
import json
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .contracts import canonical_json


GITHUB_API_URL = "https://api.github.com"


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
    ) -> Any:
        body = None
        if payload is not None:
            body = canonical_json(dict(payload)).encode("utf-8")
        request = Request(
            url,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": "Trading-Intelligence-Desktop-Cloud-Bridge/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                status = int(getattr(response, "status", 200))
                raw = response.read()
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
                decoded = json.loads(detail)
                message = str(decoded.get("message") or "") if isinstance(decoded, dict) else ""
            except (OSError, ValueError, AttributeError):
                message = ""
            safe = " ".join(message.split())[:350]
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
        except (URLError, TimeoutError, OSError) as exc:
            raise GitHubLibraryError(
                "GitHub could not be reached by the cloud bridge."
            ) from exc
        if status not in expected_statuses:
            raise GitHubLibraryError(f"GitHub returned unexpected status {status}.")
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
        encoded_branch = quote(self.config.branch, safe="")
        metadata = self._request(
            self._api(f"contents/{encoded_path}?ref={encoded_branch}")
        )
        if not isinstance(metadata, Mapping):
            raise GitHubLibraryError("The configured research-library path is not a file.")
        blob_sha = str(metadata.get("sha") or "").strip()
        content = str(metadata.get("content") or "")
        if content:
            raw = self._decode_blob(metadata)
        else:
            git_url = str(metadata.get("git_url") or "").strip()
            if not git_url and blob_sha:
                git_url = self._api(f"git/blobs/{quote(blob_sha, safe='')}")
            if not git_url:
                raise GitHubLibraryError("GitHub did not provide a blob URL for the research library.")
            blob = self._request(git_url)
            if not isinstance(blob, Mapping):
                raise GitHubLibraryError("GitHub returned invalid research-library blob metadata.")
            raw = self._decode_blob(blob)
            blob_sha = str(blob.get("sha") or blob_sha).strip()
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

        commit = self._request(self._api(f"git/commits/{quote(expected, safe='')}"))
        try:
            base_tree = str(commit["tree"]["sha"])
        except (TypeError, KeyError) as exc:
            raise GitHubLibraryError("GitHub commit metadata omitted its tree SHA.") from exc

        compact = canonical_json(dict(document))
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

    def dispatch_workflow(self, inputs: Mapping[str, Any] | None = None) -> bool:
        if not self.config.action_repository or not self.config.workflow_file:
            return False
        workflow = quote(self.config.workflow_file, safe="")
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
