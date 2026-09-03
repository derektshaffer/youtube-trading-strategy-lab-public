"""Run only an explicitly named, existing desktop Profit First queue item.

No seeding, outbox drain, automatic follow-up, or unrelated stale-job recovery.
The normal validator still owns fidelity, duplicate-experiment and risk gates.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import os
import socket

from cloud_research_worker import _target_strategy_ids, build_store, execute_job
from distributed_stock_finder import mutate_remote_library
from trading_research_orchestrator import claim_research_job_by_id, fail_research_job
from youtube_strategy_engine import AppError


def run_once(job_id: str) -> dict:
    job_id = str(job_id or "").strip()
    if not job_id:
        raise AppError("An exact existing Profit First job ID is required.")
    worker_id = f"profit-first-exact:{socket.gethostname()}:{os.getpid()}"
    holder: dict = {}

    def claim(library: dict) -> dict | None:
        holder.clear()
        matches = [row for row in library.get("research_queue", []) if row.get("id") == job_id]
        if len(matches) != 1:
            raise AppError("Exact Profit First job is missing or duplicated; no work started.")
        row = matches[0]
        if row.get("type") != "autonomous_validation" or row.get("source") != "trading_intelligence_desktop":
            raise AppError("Exact job is not a desktop Profit First validation; no work started.")
        if row.get("status") not in {"queued", "retry"}:
            return None
        if int(row.get("attempts") or 0) >= int(row.get("max_attempts") or 3):
            raise AppError("Exact job has exhausted its retry budget; review required.")
        if not _target_strategy_ids(row.get("payload") or {}):
            raise AppError("Exact Profit First job has no selected strategies; refusing broad validation.")
        # The shared helper recovers stale jobs in its input. Isolate the named
        # item so even another stale/running Finder is byte-for-byte untouched.
        _, claimed = claim_research_job_by_id(
            {"research_queue": [deepcopy(row)]}, worker_id, job_id,
            allowed_types={"autonomous_validation"},
        )
        if claimed is None:
            return None
        holder["job"] = deepcopy(claimed)
        library["research_queue"] = [claimed if item.get("id") == job_id else item
                                     for item in library["research_queue"]]
        return library

    mutate_remote_library(claim)
    job = holder.get("job")
    if job is None:
        return {"status": "idle", "job_id": job_id}
    try:
        # autonomous_validation does not use the Gemini router. Do not construct
        # it or require unrelated provider credentials in this bounded path.
        result_ref = execute_job(build_store(), None, job, worker_id)
        return {"status": "complete", "job_id": job_id, "result_ref": result_ref}
    except Exception as exc:
        def fail(library: dict) -> dict | None:
            current = next((r for r in library.get("research_queue", []) if r.get("id") == job_id), {})
            if current.get("status") != "running" or current.get("worker_id") != worker_id:
                return None
            return fail_research_job(library, job_id, exc, failure_step="exact_profit_first_execution")
        mutate_remote_library(fail)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args(argv)
    print(run_once(args.job_id), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
