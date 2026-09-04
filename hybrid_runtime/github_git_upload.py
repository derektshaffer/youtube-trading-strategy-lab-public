"""Bounded, conflict-safe Git transport for oversized desktop library writes.

Use a private temporary bare repository: no research checkout, filters, hooks,
or user Git configuration can rewrite the library or expose the bearer token.
Only the requested file changes, and the new commit has exactly the revision
the caller read as its parent. A normal push cannot overwrite a newer writer.
"""

from __future__ import annotations

from hashlib import sha1
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .github_library import GitHubLibraryConfig, GitHubLibraryConflict, GitHubLibraryError


GIT_TRANSFER_TIMEOUT_SECONDS = 300


def _failure_diagnostic(detail: str) -> tuple[str, str]:
    # Allowlisted descriptions only: never echo a URL, header, filename, token,
    # proxy setting, or arbitrary Git output into the desktop or durable logs.
    categories = (
        ("size_limit", ("gh001", "exceeds github's file size limit", "exceeds the file size limit",
                        "pack exceeds maximum allowed size", "http 413", "error: 413"),
         "GitHub rejected the upload size. The cloud library needs a size-safe storage change; repeated retries will not fix this."),
        ("repository_policy", ("gh006", "gh013", "protected branch", "repository rule violations",
                               "push declined due", "pre-receive hook declined"),
         "GitHub rejected the push under a repository policy. Check the branch rules or push-protection report; do not bypass them."),
        ("authentication", ("authentication failed", "invalid username or password", "invalid username or token",
                            "could not read username", "could not read password", "terminal prompts disabled",
                            "http basic: access denied", "error: 401"),
         "Git could not authenticate. Check the GitHub credential used by this desktop connection."),
        ("repository_access", ("write access to repository not granted", "permission to", "denied to",
                               "repository not found", "error: 403", "http 403"),
         "GitHub could not grant repository access. Check the repository name and this desktop credential's repository authorization and Contents write permission."),
        ("certificate", ("ssl certificate problem", "certificate verify failed", "server certificate verification failed"),
         "Git could not verify the HTTPS certificate. Check the trusted certificate or network proxy configuration; keep TLS verification enabled."),
        ("dns", ("could not resolve host", "could not resolve proxy"),
         "Git could not resolve the GitHub or proxy address. Check DNS and network connectivity."),
        ("connection", ("rpc failed", "remote end hung up", "unexpected disconnect", "connection reset",
                        "failed to connect", "connection timed out", "http/2 stream", "early eof",
                        "error: 500", "error: 502", "error: 503", "error: 504"),
         "The Git transfer was interrupted or the remote service was unavailable. Retry must check whether the saved request already reached GitHub."),
        ("local_storage", ("no space left on device", "disk quota exceeded"),
         "Git ran out of local temporary storage. Free disk space before retrying."),
    )
    for code, markers, message in categories:
        if any(marker in detail for marker in markers):
            return code, message
    return ("unclassified",
            "Git did not provide a recognized failure category. Raw diagnostics were withheld to protect credentials; the exact cause is not yet known.")


def _git_environment(askpass: Path, token: str) -> dict[str, str]:
    # Do not forward unrelated brokerage credentials, tracing, Git overrides,
    # or the bundled Python runtime's library search paths to system Git.
    allowed = {
        "PATH", "HOME", "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "WINDIR",
        "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
        "https_proxy", "http_proxy", "all_proxy", "no_proxy",
        "SSL_CERT_FILE", "SSL_CERT_DIR",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.update({
        "PATH": environment.get("PATH") or os.defpath,
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": str(askpass),
        "TRADING_INTELLIGENCE_GIT_UPLOAD_TOKEN": token,
        "GIT_AUTHOR_NAME": "Trading Intelligence Lab",
        "GIT_AUTHOR_EMAIL": "trading-intelligence-lab@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "Trading Intelligence Lab",
        "GIT_COMMITTER_EMAIL": "trading-intelligence-lab@users.noreply.github.com",
    })
    if environment.get("SSL_CERT_FILE"):
        environment["GIT_SSL_CAINFO"] = environment["SSL_CERT_FILE"]
    return environment


def _run_git(
    git: str, arguments: list[str], *, root: Path, environment: dict[str, str],
    operation: str, input_bytes: bytes | None = None,
    timeout: int = 60,
) -> str:
    command = [
        git, "-c", f"core.hooksPath={os.devnull}",
        "-c", "credential.helper=", "-c", "http.sslVerify=true",
        "-c", "commit.gpgSign=false", "-c", "push.gpgSign=false",
        *arguments,
    ]
    try:
        result = subprocess.run(
            command, cwd=root, env=environment, input=input_bytes,
            capture_output=True, timeout=timeout, check=False,
        )
    except FileNotFoundError:
        raise GitHubLibraryError(
            "Git is required for large cloud-library uploads. Install Git or the macOS command-line tools."
        ) from None
    except subprocess.TimeoutExpired:
        raise GitHubLibraryError(
            f"Large cloud-library Git {operation} timed out after {timeout}s. "
            "No force push or automatic write retry was attempted."
        ) from None
    except OSError:
        raise GitHubLibraryError(f"Large cloud-library Git {operation} could not start.") from None
    if result.returncode:
        # --porcelain reports per-ref rejection status on stdout; transport
        # diagnostics can be on stderr. Inspect both, but never expose either.
        detail = (result.stdout + b"\n" + result.stderr).decode("utf-8", errors="replace").lower()
        if operation == "push" and any(
            marker in detail for marker in ("non-fast-forward", "fetch first")
        ):
            raise GitHubLibraryConflict("GitHub branch moved during the large-library push.")
        code, explanation = _failure_diagnostic(detail)
        raise GitHubLibraryError(
            f"Large cloud-library Git {operation} failed [{code}; exit {int(result.returncode)}]. "
            + explanation
        )
    return result.stdout.decode("utf-8", errors="replace").strip()


def _sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise GitHubLibraryError("Git returned an invalid large-library object identifier.")
    return value


def write_large_library(
    config: GitHubLibraryConfig, token: str, serialized: bytes, *,
    expected_revision: str, message: str,
) -> str:
    """Publish one verified blob; never merge, rebase, force, or replay a push."""
    expected = _sha(expected_revision)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config.repository):
        raise GitHubLibraryError("Large-library repository must use owner/name form.")
    if any(character in config.path for character in "\x00\r\n\\") or any(
        part.lower() in {"", ".", "..", ".git"} for part in config.path.split("/")
    ):
        raise GitHubLibraryError("Large-library path must be a safe repository-relative file.")
    if not token or any(character in token for character in "\x00\r\n"):
        raise GitHubLibraryError("A valid GitHub credential is required for the large-library upload.")

    with tempfile.TemporaryDirectory(prefix="trading-intelligence-git-upload-") as temporary:
        root = Path(temporary)
        askpass = root / "askpass.sh"
        askpass.write_text(
            '#!/bin/sh\ncase "$1" in\n'
            '  *Username*) printf "%s\\n" "x-access-token" ;;\n'
            '  *Password*) printf "%s\\n" "$TRADING_INTELLIGENCE_GIT_UPLOAD_TOKEN" ;;\n'
            '  *) exit 1 ;;\nesac\n', encoding="utf-8",
        )
        askpass.chmod(0o700)
        environment = _git_environment(askpass, token)
        git = shutil.which("git", path=environment["PATH"])
        if not git:
            raise GitHubLibraryError(
                "Git is required for large cloud-library uploads. Install Git or the macOS command-line tools."
            )

        def run(arguments: list[str], operation: str, **kwargs) -> str:
            return _run_git(git, arguments, root=root, environment=environment, operation=operation, **kwargs)

        ref = f"refs/heads/{config.branch}"
        run(["check-ref-format", ref], "branch validation")
        run(["init", "--bare", "--template=", "."], "initialization")
        run(["remote", "add", "origin", f"https://github.com/{config.repository}.git"], "configuration")
        # Fetch trees and the tip commit, not the whole historical library. The
        # server may decline filtering; a shallow full fetch is also correct.
        run(["fetch", "--depth=1", "--filter=blob:none", "--no-tags", "origin", ref],
            "fetch", timeout=GIT_TRANSFER_TIMEOUT_SECONDS)
        fetched = _sha(run(["rev-parse", "FETCH_HEAD^{commit}"], "revision verification"))
        if fetched != expected:
            raise GitHubLibraryConflict("GitHub branch moved before the large-library upload.")

        blob = _sha(run(["hash-object", "-w", "--stdin"], "blob creation", input_bytes=serialized))
        digest = sha1(f"blob {len(serialized)}\0".encode("ascii"))
        digest.update(serialized)
        if blob != digest.hexdigest():
            raise GitHubLibraryError("Large-library Git blob integrity verification failed.")

        run(["read-tree", f"{expected}^{{tree}}"], "tree preparation")
        run(["update-index", "--add", "--cacheinfo", "100644", blob, config.path], "tree update")
        tree = _sha(run(["write-tree"], "tree creation"))
        commit = _sha(run(["commit-tree", tree, "-p", expected], "commit creation",
                          input_bytes=(str(message or "Update Trading Intelligence cloud queue")[:240] + "\n").encode("utf-8")))
        try:
            run(["push", "--porcelain", "origin", f"{commit}:{ref}"], "push",
                timeout=GIT_TRANSFER_TIMEOUT_SECONDS)
        except GitHubLibraryError:
            # A connection can fail after GitHub accepts the commit. Confirm
            # that exact commit before declaring failure; never push it again.
            try:
                remote = run(["ls-remote", "--exit-code", "origin", ref], "push verification")
            except GitHubLibraryError:
                remote = ""
            if remote.split()[:1] == [commit]:
                return commit
            raise
        return commit
