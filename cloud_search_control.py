"""Exact GitHub Actions identity for user-requested search cancellation.

Cancellation intent is durable before calling Actions. A 202 is a request,
not proof of stopping; only the matching completed run confirms shutdown.
"""
import os
import re

WORKFLOWS = {
    "stock_finder": "distributed-stock-finder.yml",
    "strategy_lab": "cloud-strategy-lab.yml",
}


def guard_cancelled_rerun(library):
    # Actions cancel targets a run ID, not an attempt. A rerun of an ID with
    # durable stop intent must never claim some other search in the small
    # interval between desktop verification and GitHub accepting the stop.
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not run_id or not repository:
        return
    for item in library.get("research_queue") or []:
        bound = item.get("cloud_worker") or {}
        if (item.get("cancel_requested") and bound.get("repository") == repository
                and str(bound.get("run_id")) == run_id):
            raise ValueError("This workflow run has a saved stop request; use a new workflow run for new research.")


def bind_claim(job):
    # Only these dedicated, one-search workflows support whole-run cancellation.
    expected = WORKFLOWS.get(job.get("type"))
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    workflow_ref = os.environ.get("GITHUB_WORKFLOW_REF", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    sha = os.environ.get("GITHUB_SHA", "")
    job.pop("cloud_worker", None)  # never inherit a previous attempt's capability
    if (expected and repository.count("/") == 1 and run_id.isdigit() and attempt.isdigit()
            and re.fullmatch(r"[0-9a-f]{40}", sha)
            and workflow_ref.startswith(f"{repository}/.github/workflows/{expected}@")):
        job["cloud_worker"] = {
            "version": 1, "repository": repository, "run_id": run_id,
            "run_attempt": int(attempt), "head_sha": sha,
            "workflow": expected,
        }


def supports_stop(item, repository):
    binding = item.get("cloud_worker") or {}
    return (isinstance(binding, dict) and binding.get("version") == 1
            and binding.get("repository") == repository
            and binding.get("workflow") == WORKFLOWS.get(item.get("type"))
            and str(binding.get("run_id") or "").isdigit()
            and isinstance(binding.get("run_attempt"), int) and binding["run_attempt"] > 0
            and bool(re.fullmatch(r"[0-9a-f]{40}", str(binding.get("head_sha") or ""))))


def verify_worker_run(binding, run):
    if (str(run.get("id")) != str(binding["run_id"])
            or run.get("run_attempt") != binding["run_attempt"]
            or run.get("head_sha") != binding["head_sha"]
            or (run.get("repository") or {}).get("full_name") != binding["repository"]
            or str(run.get("path") or "").split("@")[0] != ".github/workflows/" + binding["workflow"]):
        raise ValueError("The cloud worker identity changed. No stop was sent; refresh first.")
    return run
