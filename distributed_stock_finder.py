"""Distributed Stock Strategy Finder orchestration.

This module lets GitHub Actions split a Deep/Very Deep Finder search across
independent cloud runners without publishing proprietary strategy definitions or
market data as workflow artifacts. Temporary plans and shard results live only in
the configured PRIVATE backup repository.

Flow:
1. prepare: atomically claim one stock_finder queue job, download/enrich history,
   and write a private run plan.
2. shard: each matrix runner optimizes one timeframe/family slice and writes a
   private compressed shard result.
3. aggregate: merge every shard with the same deterministic ranking logic as the
   normal Finder, touch holdout once, run walk-forward/stability, save the result,
   and remove temporary private artifacts.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import argparse
import base64
import binascii
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any

from stock_strategy_finder import (
    apply_historical_spread_integrity_guard,
    complete_stock_strategy_finder_from_optimization,
    merge_finder_report_into_library,
    search_profile,
    selected_strategies_for_profile,
    stock_finder_strategy_families,
    stock_finder_optimizer_settings,
)
from trading_catalyst_core import (
    enrich_bars_with_point_in_time_catalysts,
    historical_news,
)
from trading_research_orchestrator import (
    claim_next_research_job,
    claim_research_job_by_id,
    fail_research_job,
    finish_research_job,
    record_worker_run,
)
from youtube_strategy_engine import (
    AlpacaMarketData,
    AppError,
    BacktestSettings,
    DEFAULT_GITHUB_BACKUP_PATH,
    GitHubCloudBackup,
    StrategyStore,
    combine_stock_timeframe_reports,
    combine_strategy_family_reports,
    historical_entry_spread_audit,
    normalize_machine_rules,
    optimize_stock_strategies_parallel,
    resample_intraday_bars,
    safe_float,
    split_safe_raw_research_rows,
    isoformat_utc,
    utc_now,
)

UTC = timezone.utc
MAX_FINALIZATION_RECOVERIES = 3
DISTRIBUTED_PLAN_VERSION = 2
DISTRIBUTED_SHARD_VERSION = 2


def env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def build_cloud_backup(*, path: str = "") -> GitHubCloudBackup:
    repository = env("GITHUB_BACKUP_REPOSITORY")
    token = env("GITHUB_BACKUP_TOKEN")
    if not repository or not token:
        raise AppError(
            "Distributed Finder needs GITHUB_BACKUP_REPOSITORY and GITHUB_BACKUP_TOKEN."
        )
    effective_path = str(path or "").strip() or env(
        "GITHUB_BACKUP_PATH",
        DEFAULT_GITHUB_BACKUP_PATH,
    )
    return GitHubCloudBackup(
        repository,
        token,
        branch=env("GITHUB_BACKUP_BRANCH"),
        path=effective_path,
    )


def build_market() -> AlpacaMarketData:
    return AlpacaMarketData(
        env("ALPACA_API_KEY"),
        env("ALPACA_SECRET_KEY"),
        env("ALPACA_LIVE_FEED", "iex"),
        env("ALPACA_HISTORICAL_FEED", "sip"),
    )


def _is_conflict(exc: Exception) -> bool:
    text = str(exc).casefold()
    return (
        "changed while" in text
        or "different or newer" in text
        or "same saved timestamp" in text
        or "newer records" in text
    )


def read_remote_library() -> dict[str, Any]:
    cloud = build_cloud_backup()
    remote = cloud.read_library()
    if remote is None:
        return StrategyStore.blank()
    return StrategyStore.normalize_library(remote["library"])


def mutate_remote_library(
    mutator,
    *,
    attempts: int = 8,
) -> dict[str, Any]:
    """Retry a narrow library mutation if another cloud worker writes first."""
    cloud = build_cloud_backup()
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        remote = cloud.read_library()
        data = StrategyStore.normalize_library(
            remote["library"] if remote is not None else StrategyStore.blank()
        )
        previous_updated_at = data.get("updated_at")
        updated = mutator(deepcopy(data))
        if updated is None:
            return data
        updated = StrategyStore.normalize_library(updated)
        updated["version"] = max(2, int(updated.get("version") or 2))
        updated["updated_at"] = isoformat_utc(utc_now())
        try:
            cloud.save_library(
                updated,
                previous_updated_at=previous_updated_at,
            )
            return updated
        except AppError as exc:
            last_error = exc
            if not _is_conflict(exc) or attempt + 1 >= attempts:
                raise
            time.sleep(min(8.0, 0.8 * (attempt + 1)))
    if last_error:
        raise last_error
    raise AppError("Distributed Finder could not update durable research state.")


class PrivateRunArtifactStore:
    """Read/write compressed run files inside the private backup repository."""

    def __init__(self):
        self.repository = env("GITHUB_BACKUP_REPOSITORY")
        self.token = env("GITHUB_BACKUP_TOKEN")
        self.branch = env("GITHUB_BACKUP_BRANCH")
        if not self.repository or not self.token:
            raise AppError("Private distributed-run storage is not configured.")

    def _helper(self, path: str) -> GitHubCloudBackup:
        return GitHubCloudBackup(
            self.repository,
            self.token,
            branch=self.branch,
            path=path,
        )

    @staticmethod
    def _content_bytes(helper: GitHubCloudBackup, record: dict[str, Any]) -> bytes:
        try:
            if record.get("encoding") == "base64" and record.get("content"):
                encoded = "".join(str(record.get("content") or "").split())
            else:
                sha = str(record.get("sha") or "")
                if not re.fullmatch(r"[a-fA-F0-9]{40,64}", sha):
                    raise AppError("Private run artifact did not have a readable blob id.")
                blob = helper._request(
                    f"{helper._repository_url}/git/blobs/{sha}"
                )
                if not blob or blob.get("encoding") != "base64":
                    raise AppError("Private run artifact blob was unreadable.")
                encoded = "".join(str(blob.get("content") or "").split())
            return base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AppError("Private distributed-run artifact was damaged.") from exc

    def write_bytes(self, path: str, payload: bytes) -> None:
        helper = self._helper(path)
        helper._verify_private_repository()
        encoded = base64.b64encode(payload).decode("ascii")
        last_error: Exception | None = None
        for attempt in range(8):
            current = helper._request(helper._contents_url(), missing_ok=True)
            body: dict[str, Any] = {
                "message": "Store distributed Stock Strategy Finder shard",
                "content": encoded,
                "branch": helper.branch,
            }
            if current is not None:
                body["sha"] = current.get("sha")
            try:
                helper._request(
                    helper._contents_url(include_branch=False),
                    method="PUT",
                    payload=body,
                )
                return
            except AppError as exc:
                last_error = exc
                if not _is_conflict(exc) or attempt >= 7:
                    raise
                time.sleep(min(8.0, 0.6 * (attempt + 1)))
        if last_error:
            raise last_error

    def read_bytes(self, path: str) -> bytes:
        helper = self._helper(path)
        helper._verify_private_repository()
        record = helper._request(helper._contents_url(), missing_ok=True)
        if record is None:
            raise FileNotFoundError(path)
        return self._content_bytes(helper, record)

    def exists(self, path: str) -> bool:
        helper = self._helper(path)
        helper._verify_private_repository()
        return helper._request(helper._contents_url(), missing_ok=True) is not None

    def delete(self, path: str) -> None:
        helper = self._helper(path)
        helper._verify_private_repository()
        record = helper._request(helper._contents_url(), missing_ok=True)
        if record is None:
            return
        helper._request(
            helper._contents_url(include_branch=False),
            method="DELETE",
            payload={
                "message": "Clean distributed Stock Strategy Finder temporary artifact",
                "sha": record.get("sha"),
                "branch": helper.branch,
            },
        )

    def write_json_gz(self, path: str, value: dict[str, Any]) -> None:
        raw = json.dumps(value, separators=(",", ":"), default=str).encode("utf-8")
        self.write_bytes(path, gzip.compress(raw, compresslevel=6))

    def read_json_gz(self, path: str) -> dict[str, Any]:
        try:
            raw = gzip.decompress(self.read_bytes(path))
            parsed = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise AppError(f"Distributed Finder artifact {path} could not be read.") from exc
        if not isinstance(parsed, dict):
            raise AppError(f"Distributed Finder artifact {path} was not a JSON object.")
        return parsed


def run_root(run_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(run_id or "").strip())
    if not safe:
        raise AppError("Distributed Finder run id is missing.")
    return f"youtube-strategy-lab/distributed-finder/{safe}"


def plan_path(run_id: str) -> str:
    return f"{run_root(run_id)}/plan.json.gz"


def shard_path(run_id: str, index: int) -> str:
    return f"{run_root(run_id)}/shard-{int(index):03d}.json.gz"


def _plan_has_current_integrity(plan: dict[str, Any]) -> bool:
    integrity = plan.get("market_data_integrity")
    return (
        int(plan.get("version") or 0) == DISTRIBUTED_PLAN_VERSION
        and isinstance(integrity, dict)
        and str(integrity.get("mode") or "") in {
            "raw_prices",
            "raw_prices_post_latest_split",
            "raw_prices_post_corporate_action",
        }
    )


def _require_current_integrity_plan(plan: dict[str, Any], run_id: str) -> None:
    if not _plan_has_current_integrity(plan):
        raise AppError(
            f"Distributed Finder run {run_id} predates the current market-data integrity "
            "contract and cannot be finalized or extended. Start a fresh run."
        )


def _completed_shard_numbers(payload: dict[str, Any]) -> set[int]:
    return {
        int(value)
        for value in payload.get("distributed_shards_completed") or []
        if str(value).lstrip("-").isdigit()
    }


def _resumable_plan_for_job(
    artifacts: PrivateRunArtifactStore,
    job: dict[str, Any],
) -> tuple[dict[str, Any], set[int]] | None:
    """Return an existing private plan and the shard artifacts that really exist.

    Queue progress is useful for display, but the private artifacts are the
    source of truth for recovery. A job that says shards completed must never
    silently create a new run if its saved plan cannot be read.
    """
    payload = dict(job.get("payload") or {})
    run_id = str(payload.get("distributed_run_id") or "").strip()
    if not run_id:
        return None
    try:
        plan = artifacts.read_json_gz(plan_path(run_id))
    except FileNotFoundError:
        if _completed_shard_numbers(payload):
            raise AppError(
                f"Saved distributed run {run_id} reports completed shards, but its "
                "private run plan is missing. Refusing to start over or discard the checkpoint."
            )
        return None

    if not _plan_has_current_integrity(plan):
        # Pre-integrity plans may contain split-adjusted or otherwise unverified
        # price history. They are deliberately not resumed under the newer engine.
        # Returning None causes the retry to create a fresh research window/plan;
        # the old private artifacts remain untouched for forensic recovery.
        return None

    job_id = str(job.get("id") or "")
    if str(plan.get("run_id") or "") != run_id:
        raise AppError(f"Saved distributed run {run_id} has a mismatched plan id.")
    if str(plan.get("parent_job_id") or "") != job_id:
        raise AppError(f"Saved distributed run {run_id} belongs to a different queue job.")
    symbol = str(payload.get("symbol") or "").strip().upper()
    profile_name = str(payload.get("profile") or "Deep").strip()
    if str(plan.get("symbol") or "").strip().upper() != symbol:
        raise AppError(f"Saved distributed run {run_id} belongs to a different stock.")
    if str(plan.get("profile_name") or "").strip() != profile_name:
        raise AppError(f"Saved distributed run {run_id} belongs to a different search profile.")

    specs = [item for item in plan.get("shards") or [] if isinstance(item, dict)]
    if not specs:
        raise AppError(f"Saved distributed run {run_id} does not contain a shard plan.")
    completed = {
        int(spec.get("index") or 0)
        for spec in specs
        if artifacts.exists(shard_path(run_id, int(spec.get("index") or 0)))
    }
    return plan, completed


def _write_public_run_metadata(
    plan: dict[str, Any],
    pending_specs: list[dict[str, Any]],
    *,
    resumed: bool,
) -> None:
    # GitHub still parses the matrix for a skipped job. Keep one harmless
    # placeholder when recovery can go directly to aggregate/finalization.
    public_specs = pending_specs or [{"index": 0, "label": "finalization-only"}]
    Path("distributed_matrix.json").write_text(
        json.dumps(
            {
                "include": [
                    {
                        "index": int(item.get("index") or 0),
                        "label": str(item.get("label") or "shard"),
                    }
                    for item in public_specs
                ]
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    Path("distributed_meta.json").write_text(
        json.dumps(
            {
                "has_job": True,
                "run_id": str(plan.get("run_id") or ""),
                "parent_job_id": str(plan.get("parent_job_id") or ""),
                "symbol": str(plan.get("symbol") or ""),
                "profile": str(plan.get("profile_name") or ""),
                "shard_count": len(plan.get("shards") or []),
                "pending_shard_count": len(pending_specs),
                "needs_shards": bool(pending_specs),
                "resumed": bool(resumed),
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def _restore_parent_distribution(
    job_id: str,
    plan: dict[str, Any],
    completed: set[int],
) -> None:
    run_id = str(plan.get("run_id") or "")
    total = max(1, len(plan.get("shards") or []))
    complete_count = min(total, len(completed))

    def mutation(data: dict[str, Any]) -> dict[str, Any]:
        queue: list[dict[str, Any]] = []
        now_text = isoformat_utc(utc_now())
        for raw in data.get("research_queue") or []:
            item = dict(raw)
            if str(item.get("id") or "") != job_id:
                queue.append(item)
                continue
            payload = dict(item.get("payload") or {})
            previous_progress = float(payload.get("distributed_progress") or 0.0)
            all_complete = complete_count == total
            base_progress = 0.88 if all_complete else 0.10 + 0.75 * (complete_count / total)
            stage = "finalization_retry" if all_complete else "distributed_optimization_resume"
            message = (
                f"Recovered all {total} saved shard artifacts; resuming final validation without recomputing shards."
                if all_complete
                else f"Recovered {complete_count} of {total} saved shard artifacts; only missing shards will run."
            )
            payload.update(
                {
                    "distributed_run_id": run_id,
                    "distributed_shards_total": total,
                    "distributed_mode": True,
                    "distributed_stage": stage,
                    "distributed_progress": max(base_progress, min(0.98, previous_progress)),
                    "distributed_message": message,
                    "distributed_shards_completed": sorted(completed),
                    "distributed_last_update": now_text,
                    "distributed_resumed_at": now_text,
                }
            )
            item["payload"] = payload
            item["updated_at"] = now_text
            item["status_message"] = message
            queue.append(item)
        data["research_queue"] = queue
        return data

    mutate_remote_library(mutation)


def _requeue_completed_finder_for_finalization(
    artifacts: PrivateRunArtifactStore,
    *,
    preferred_job_id: str = "",
) -> str:
    """Give a terminal Finder job a bounded finalization-only recovery budget."""
    library = read_remote_library()
    candidates = [
        dict(item)
        for item in library.get("research_queue") or []
        if isinstance(item, dict)
        and str(item.get("type") or "") == "stock_finder"
        and str(item.get("status") or "") == "failed"
        and (
            not str(preferred_job_id or "").strip()
            or str(item.get("id") or "") == str(preferred_job_id).strip()
        )
    ]
    candidates.sort(
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )
    chosen: dict[str, Any] | None = None
    for item in candidates:
        payload = dict(item.get("payload") or {})
        recoveries = int(payload.get("distributed_finalization_recoveries") or 0)
        total = int(payload.get("distributed_shards_total") or 0)
        if recoveries >= MAX_FINALIZATION_RECOVERIES or total <= 0:
            continue
        try:
            recovered = _resumable_plan_for_job(artifacts, item)
        except (AppError, OSError):
            continue
        if recovered is None:
            continue
        plan, completed = recovered
        if len(completed) != len(plan.get("shards") or []):
            continue
        chosen = item
        break
    if chosen is None:
        return ""

    chosen_id = str(chosen.get("id") or "")

    def mutation(data: dict[str, Any]) -> dict[str, Any] | None:
        changed = False
        queue: list[dict[str, Any]] = []
        now_text = isoformat_utc(utc_now())
        for raw in data.get("research_queue") or []:
            item = dict(raw)
            if str(item.get("id") or "") != chosen_id or str(item.get("status") or "") != "failed":
                queue.append(item)
                continue
            payload = dict(item.get("payload") or {})
            recoveries = int(payload.get("distributed_finalization_recoveries") or 0)
            if recoveries >= MAX_FINALIZATION_RECOVERIES:
                queue.append(item)
                continue
            payload["distributed_finalization_recoveries"] = recoveries + 1
            payload["distributed_stage"] = "finalization_retry"
            payload["distributed_message"] = (
                "Saved shard artifacts are intact; reopening this job for finalization only."
            )
            item["payload"] = payload
            item["status"] = "retry"
            item["worker_id"] = None
            item["next_attempt_at"] = None
            item["updated_at"] = now_text
            item["max_attempts"] = max(
                int(item.get("max_attempts") or 0),
                int(item.get("attempts") or 0) + 1,
            )
            item["status_message"] = str(payload["distributed_message"])
            queue.append(item)
            changed = True
        if not changed:
            return None
        data["research_queue"] = queue
        return data

    mutate_remote_library(mutation)
    return chosen_id


def _claim_stock_finder_job(
    worker_id: str,
    preferred_job_id: str = "",
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    holder: dict[str, Any] = {}

    def mutation(data: dict[str, Any]) -> dict[str, Any] | None:
        if str(preferred_job_id or "").strip():
            updated, job = claim_research_job_by_id(
                data,
                worker_id,
                str(preferred_job_id).strip(),
                allowed_types={"stock_finder"},
            )
        else:
            updated, job = claim_next_research_job(
                data,
                worker_id,
                allowed_types={"stock_finder"},
            )
        holder["job"] = job
        if job is None:
            return None
        return updated

    updated = mutate_remote_library(mutation)
    return holder.get("job"), updated


def _mark_parent_failed(job_id: str, message: str, *, stage: str) -> None:
    def mutation(data: dict[str, Any]) -> dict[str, Any]:
        failed = fail_research_job(
            data,
            job_id,
            message,
            retry_delay_minutes=15,
            failure_step=stage,
        )
        now_text = isoformat_utc(utc_now())
        for item in failed.get("research_queue") or []:
            if str(item.get("id") or "") != job_id:
                continue
            payload = dict(item.get("payload") or {})
            payload["distributed_failed_stage"] = str(stage)
            payload["distributed_last_error"] = str(message)[:1800]
            payload["distributed_stage"] = f"{stage}_retry"
            payload["distributed_message"] = (
                f"{str(stage).replace('_', ' ').title()} failed: {message}"
            )[:500]
            payload["distributed_last_update"] = now_text
            item["payload"] = payload
        return record_worker_run(
            failed,
            worker_id="distributed-finder",
            job_id=job_id,
            job_type="stock_finder",
            status="failed",
            detail=message,
        )

    mutate_remote_library(mutation)


def _record_parent_step_failure(job_id: str, *, stage: str, message: str) -> None:
    """Record a shard/step error while leaving aggregate to decide job retry state."""
    def mutation(data: dict[str, Any]) -> dict[str, Any]:
        now_text = isoformat_utc(utc_now())
        for item in data.get("research_queue") or []:
            if str(item.get("id") or "") != job_id:
                continue
            payload = dict(item.get("payload") or {})
            payload["distributed_failed_stage"] = str(stage)
            payload["distributed_last_error"] = str(message)[:1800]
            payload["distributed_message"] = (
                f"{str(stage).replace('_', ' ').title()} failed: {message}"
            )[:500]
            payload["distributed_last_update"] = now_text
            item["payload"] = payload
            item["updated_at"] = now_text
            item["failure_step"] = str(stage)
            item["status_message"] = str(payload["distributed_message"])
        return data

    mutate_remote_library(mutation)


def _parent_job_id_for_run(run_id: str) -> str:
    library = read_remote_library()
    for item in library.get("research_queue") or []:
        if not isinstance(item, dict):
            continue
        payload = dict(item.get("payload") or {})
        if str(payload.get("distributed_run_id") or "") == str(run_id or ""):
            return str(item.get("id") or "")
    return ""


def _update_parent_distribution(
    job_id: str,
    *,
    run_id: str,
    shard_count: int,
    start: datetime,
    end: datetime,
) -> None:
    def mutation(data: dict[str, Any]) -> dict[str, Any]:
        queue: list[dict[str, Any]] = []
        now_text = isoformat_utc(utc_now())
        for raw in data.get("research_queue") or []:
            item = dict(raw)
            if str(item.get("id") or "") == job_id:
                payload = dict(item.get("payload") or {})
                payload.update(
                    {
                        "distributed_run_id": run_id,
                        "distributed_shards_total": int(shard_count),
                        "distributed_mode": True,
                        "distributed_stage": "distributed_optimization",
                        "distributed_progress": 0.10,
                        "distributed_message": f"Prepared {shard_count} cloud shards; waiting for optimization workers.",
                        "distributed_shards_completed": [],
                        "research_start": start.isoformat(),
                        "research_end": end.isoformat(),
                    }
                )
                item["payload"] = payload
                item["updated_at"] = now_text
            queue.append(item)
        data["research_queue"] = queue
        return data

    mutate_remote_library(mutation)


def _update_parent_cloud_progress(
    job_id: str,
    *,
    run_id: str,
    stage: str,
    progress: float,
    message: str,
    shard_index: int | None = None,
    shard_label: str = "",
) -> None:
    """Persist coarse distributed progress without coupling the UI to Actions logs."""
    def mutation(data: dict[str, Any]) -> dict[str, Any]:
        queue: list[dict[str, Any]] = []
        now_text = isoformat_utc(utc_now())
        for raw in data.get("research_queue") or []:
            item = dict(raw)
            if str(item.get("id") or "") != job_id:
                queue.append(item)
                continue
            payload = dict(item.get("payload") or {})
            if str(payload.get("distributed_run_id") or "") not in {"", run_id}:
                queue.append(item)
                continue
            completed = {
                int(value)
                for value in payload.get("distributed_shards_completed") or []
                if str(value).lstrip("-").isdigit()
            }
            if shard_index is not None:
                completed.add(int(shard_index))
            payload.update(
                {
                    "distributed_run_id": run_id,
                    "distributed_stage": str(stage),
                    "distributed_progress": max(0.0, min(1.0, float(progress))),
                    "distributed_message": str(message)[:500],
                    "distributed_shards_completed": sorted(completed),
                    "distributed_last_update": now_text,
                }
            )
            payload.pop("distributed_failed_stage", None)
            payload.pop("distributed_last_error", None)
            if shard_label:
                payload["distributed_last_shard"] = str(shard_label)
            item["payload"] = payload
            item["updated_at"] = now_text
            item["failure_step"] = None
            item["status_message"] = str(message)[:2000]
            queue.append(item)
        data["research_queue"] = queue
        return data

    mutate_remote_library(mutation)


def _balanced_family_groups(
    strategy_ids: list[str],
    group_count: int,
) -> list[list[str]]:
    count = max(1, min(int(group_count), len(strategy_ids)))
    groups: list[list[str]] = [[] for _ in range(count)]
    for index, strategy_id in enumerate(strategy_ids):
        groups[index % count].append(strategy_id)
    return [group for group in groups if group]


def command_prepare(preferred_job_id: str = "") -> int:
    worker_id = f"distributed-prepare:{os.getpid()}"
    job, library = _claim_stock_finder_job(
        worker_id,
        preferred_job_id=preferred_job_id,
    )
    artifacts = PrivateRunArtifactStore()
    if job is None:
        recovered_job_id = _requeue_completed_finder_for_finalization(
            artifacts,
            preferred_job_id=preferred_job_id,
        )
        if recovered_job_id:
            job, library = _claim_stock_finder_job(
                worker_id,
                preferred_job_id=recovered_job_id,
            )
    if job is None:
        Path("distributed_meta.json").write_text(
            json.dumps({"has_job": False}, separators=(",", ":")),
            encoding="utf-8",
        )
        Path("distributed_matrix.json").write_text(
            json.dumps({"include": []}, separators=(",", ":")),
            encoding="utf-8",
        )
        print("No queued Stock Strategy Finder job is ready.", flush=True)
        return 0

    job_id = str(job.get("id") or "")
    payload = dict(job.get("payload") or {})
    symbol = str(payload.get("symbol") or "").strip().upper()
    profile = search_profile(str(payload.get("profile") or "Deep"))

    try:
        resumed = _resumable_plan_for_job(artifacts, job)
        if resumed is not None:
            plan, completed = resumed
            specs = [item for item in plan.get("shards") or [] if isinstance(item, dict)]
            pending = [
                item
                for item in specs
                if int(item.get("index") or 0) not in completed
            ]
            _restore_parent_distribution(job_id, plan, completed)
            _write_public_run_metadata(plan, pending, resumed=True)
            if pending:
                print(
                    f"Recovered {symbol} {profile.name} run {plan.get('run_id')}: "
                    f"{len(completed)}/{len(specs)} saved shards; launching only "
                    f"the {len(pending)} missing shard(s).",
                    flush=True,
                )
            else:
                print(
                    f"Recovered {symbol} {profile.name} run {plan.get('run_id')}: "
                    f"all {len(specs)} shard artifacts are intact; skipping optimization "
                    "and resuming final validation.",
                    flush=True,
                )
            return 0

        run_id = "dist-" + hashlib.sha256(
            f"{job_id}|{job.get('attempts')}|{isoformat_utc(utc_now())}".encode("utf-8")
        ).hexdigest()[:20]
        strategies = stock_finder_strategy_families(
            list(library.get("strategies") or [])
        )
        selected, skipped = selected_strategies_for_profile(
            strategies,
            symbol,
            profile,
        )
        if not selected:
            raise AppError(
                f"No machine-testable long strategy families are available for {symbol}."
            )

        market = build_market()
        end = datetime.now(UTC)
        if market.historical_feed == "sip" and market.live_feed != "sip":
            end -= timedelta(minutes=16)
        start = end - timedelta(days=profile.history_days)

        def history_progress(page: int) -> None:
            if page == 1 or page % 10 == 0:
                print(f"[prepare] {symbol} history page {page}", flush=True)

        rows_by_symbol = market.bars(
            [symbol],
            start=start,
            end=end,
            timeframe="1Min",
            adjustment="raw",
            max_pages=400,
            progress=history_progress,
        )
        rows = list(rows_by_symbol.get(symbol) or [])
        if not rows:
            raise AppError(f"No historical bars were returned for {symbol}.")

        split_actions = market.research_reset_actions(
            [symbol],
            start=start,
            end=end,
        )
        rows, split_guard = split_safe_raw_research_rows(
            rows,
            split_actions,
            symbol,
        )
        if not rows:
            raise AppError(f"No split-safe raw-price history remained for {symbol}.")
        if split_guard.get("corporate_action_reset_detected"):
            print(
                "[prepare] corporate-action guard: raw prices preserved; "
                f"research restarted at {split_guard.get('latest_split_date')} after "
                f"discarding {int(split_guard.get('discarded_pre_split_rows') or 0):,} pre-split bars.",
                flush=True,
            )

        needs_catalyst_history = any(
            bool(
                normalize_machine_rules(item.get("machine_rules")).get(
                    "catalyst_required"
                )
            )
            for item in selected
        )
        if needs_catalyst_history:
            articles = historical_news(
                market,
                [symbol],
                start=start - timedelta(hours=24),
                end=end,
                max_pages=120,
            )
            rows, _ = enrich_bars_with_point_in_time_catalysts(
                rows,
                articles,
                lookback_hours=24.0,
            )

        requested_groups = max(
            1,
            min(
                12,
                int(env("DISTRIBUTED_FINDER_FAMILY_SHARDS", "4") or 4),
            ),
        )
        family_ids = [str(item.get("id") or "") for item in selected]
        groups = _balanced_family_groups(family_ids, requested_groups)
        matrix: list[dict[str, Any]] = []
        shard_index = 0
        for timeframe in profile.timeframes:
            for group_index, group in enumerate(groups):
                matrix.append(
                    {
                        "index": shard_index,
                        "label": f"{timeframe}-g{group_index + 1}",
                        "timeframe": timeframe,
                        "group": group_index + 1,
                        "family_ids": group,
                    }
                )
                shard_index += 1

        plan = {
            "version": DISTRIBUTED_PLAN_VERSION,
            "run_id": run_id,
            "parent_job_id": job_id,
            "symbol": symbol,
            "profile_name": profile.name,
            "research_start": start.isoformat(),
            "research_end": end.isoformat(),
            "created_at": isoformat_utc(utc_now()),
            "strategies_considered_count": len(strategies),
            "selected_strategies": selected,
            "technical_skips": skipped,
            "one_minute_rows": rows,
            "market_data_integrity": split_guard,
            "backtest_settings": asdict(BacktestSettings()),
            "optimization_settings": asdict(
                stock_finder_optimizer_settings(profile)
            ),
            "shards": matrix,
        }
        artifacts.write_json_gz(plan_path(run_id), plan)
        _update_parent_distribution(
            job_id,
            run_id=run_id,
            shard_count=len(matrix),
            start=start,
            end=end,
        )

        _write_public_run_metadata(plan, matrix, resumed=False)
        print(
            f"Prepared {symbol} {profile.name}: {len(selected)} families, "
            f"{len(profile.timeframes)} timeframes, {len(matrix)} distributed shards.",
            flush=True,
        )
        return 0
    except Exception as exc:
        _mark_parent_failed(
            job_id,
            f"Distributed Finder prepare failed: {exc}",
            stage="prepare_or_recovery",
        )
        raise


def _command_shard(run_id: str, index: int) -> int:
    artifacts = PrivateRunArtifactStore()
    plan = artifacts.read_json_gz(plan_path(run_id))
    _require_current_integrity_plan(plan, run_id)
    specs = [
        item
        for item in plan.get("shards") or []
        if isinstance(item, dict) and int(item.get("index") or 0) == int(index)
    ]
    if len(specs) != 1:
        raise AppError(f"Distributed Finder shard {index} was not in the run plan.")
    spec = specs[0]
    wanted = {str(value or "") for value in spec.get("family_ids") or []}
    selected = [
        dict(item)
        for item in plan.get("selected_strategies") or []
        if isinstance(item, dict) and str(item.get("id") or "") in wanted
    ]
    if not selected:
        raise AppError(f"Distributed Finder shard {index} has no strategy families.")

    timeframe = str(spec.get("timeframe") or "")
    rows = resample_intraday_bars(
        list(plan.get("one_minute_rows") or []),
        timeframe,
        include_extended_hours=True,
    )
    settings = BacktestSettings(**dict(plan.get("backtest_settings") or {}))
    profile = search_profile(str(plan.get("profile_name") or "Deep"))
    optimizer = stock_finder_optimizer_settings(profile)
    local_workers = max(
        1,
        min(
            4,
            int(env("DISTRIBUTED_FINDER_LOCAL_WORKERS", "2") or 2),
        ),
    )
    started = time.perf_counter()

    def progress(completed: int, total: int, message: str) -> None:
        if completed == total or completed % max(1, total // 10) == 0:
            print(
                f"[shard {index}] {timeframe} {completed}/{max(1,total)} · {message}",
                flush=True,
            )

    report = optimize_stock_strategies_parallel(
        rows,
        selected,
        str(plan.get("symbol") or ""),
        settings,
        optimizer,
        max_workers=local_workers,
        progress=progress,
        finalize_holdout=False,
    )
    report["timeframe"] = timeframe
    report["distributed_shard_index"] = int(index)
    report["distributed_family_ids"] = sorted(wanted)
    report["distributed_elapsed_seconds"] = round(
        time.perf_counter() - started,
        3,
    )
    for candidate in report.get("rankings") or []:
        candidate["timeframe"] = timeframe
    for record in report.get("configuration_history") or []:
        record["timeframe"] = timeframe

    artifacts.write_json_gz(
        shard_path(run_id, index),
        {
            "version": DISTRIBUTED_SHARD_VERSION,
            "run_id": run_id,
            "index": int(index),
            "timeframe": timeframe,
            "family_ids": sorted(wanted),
            "report": report,
        },
    )
    parent_job_id = str(plan.get("parent_job_id") or "")
    total_shards = max(1, len(plan.get("shards") or []))
    if parent_job_id:
        # Each shard reports only at completion. Concurrent writes are reconciled
        # by mutate_remote_library, so the durable completed-shard set never
        # loses another worker's progress.
        current_library = read_remote_library()
        parent = next(
            (
                item
                for item in current_library.get("research_queue") or []
                if isinstance(item, dict)
                and str(item.get("id") or "") == parent_job_id
            ),
            {},
        )
        parent_payload = dict((parent or {}).get("payload") or {})
        already_done = {
            int(value)
            for value in parent_payload.get("distributed_shards_completed") or []
            if str(value).lstrip("-").isdigit()
        }
        projected_done = min(total_shards, len(already_done | {int(index)}))
        shard_progress = 0.10 + 0.75 * (projected_done / total_shards)
        _update_parent_cloud_progress(
            parent_job_id,
            run_id=run_id,
            stage="distributed_optimization",
            progress=shard_progress,
            message=(
                f"Cloud optimization: {projected_done} of {total_shards} shards complete"
            ),
            shard_index=int(index),
            shard_label=str(spec.get("label") or f"shard-{index}"),
        )
    print(
        f"Completed shard {index}: {timeframe}, {len(selected)} families, "
        f"{int(report.get('unique_configurations_tested') or 0):,} configurations.",
        flush=True,
    )
    return 0


def command_shard(run_id: str, index: int) -> int:
    try:
        return _command_shard(run_id, index)
    except Exception as exc:
        # The matrix uses fail-fast=false, so make the exact failed shard visible
        # while the remaining shards finish. Aggregate will perform the durable
        # retry transition after it sees which artifacts are missing.
        try:
            plan = PrivateRunArtifactStore().read_json_gz(plan_path(run_id))
            job_id = str(plan.get("parent_job_id") or "")
            if job_id:
                _record_parent_step_failure(
                    job_id,
                    stage=f"shard_{int(index)}",
                    message=str(exc),
                )
        except Exception:
            pass
        raise


def command_aggregate(run_id: str) -> int:
    artifacts = PrivateRunArtifactStore()
    try:
        plan = artifacts.read_json_gz(plan_path(run_id))
    except Exception as exc:
        print(
            f"[aggregate] Could not load saved plan for {run_id}: {exc}",
            flush=True,
        )
        try:
            job_id = _parent_job_id_for_run(run_id)
            if job_id:
                _mark_parent_failed(
                    job_id,
                    f"Distributed Finder could not load saved plan {run_id}: {exc}",
                    stage="loading_saved_plan",
                )
        except Exception:
            pass
        raise
    _require_current_integrity_plan(plan, run_id)
    job_id = str(plan.get("parent_job_id") or "")
    specs = [item for item in plan.get("shards") or [] if isinstance(item, dict)]
    finalization_stage = ["loading_saved_shards"]
    try:
        if job_id:
            _update_parent_cloud_progress(
                job_id,
                run_id=run_id,
                stage="final_holdout",
                progress=0.88,
                message="All cloud shards finished; combining results and running the untouched holdout.",
            )
        shard_payloads: list[dict[str, Any]] = []
        missing: list[int] = []
        for spec in specs:
            index = int(spec.get("index") or 0)
            try:
                payload = artifacts.read_json_gz(shard_path(run_id, index))
                payload_index = payload.get("index")
                if (
                    int(payload.get("version") or 0) != DISTRIBUTED_SHARD_VERSION
                    or str(payload.get("run_id") or "") != run_id
                    or (
                        int(payload_index)
                        if payload_index is not None
                        else -1
                    ) != index
                ):
                    raise AppError(
                        f"Distributed Finder shard {index} does not match the current "
                        "integrity/version contract."
                    )
                shard_payloads.append(payload)
            except FileNotFoundError:
                missing.append(index)
        if missing:
            raise AppError(
                "Distributed Finder did not receive every shard result. Missing: "
                + ", ".join(str(value) for value in missing)
            )

        finalization_stage[0] = "combining_saved_shards"
        selected = [
            dict(item)
            for item in plan.get("selected_strategies") or []
            if isinstance(item, dict)
        ]
        strategies_considered_count = int(
            plan.get("strategies_considered_count") or len(selected)
        )
        one_minute_rows = list(plan.get("one_minute_rows") or [])
        symbol = str(plan.get("symbol") or "").strip().upper()
        profile = search_profile(str(plan.get("profile_name") or "Deep"))
        settings = BacktestSettings(**dict(plan.get("backtest_settings") or {}))
        optimizer = stock_finder_optimizer_settings(profile)

        by_timeframe: dict[str, list[dict[str, Any]]] = {}
        for payload in shard_payloads:
            timeframe = str(payload.get("timeframe") or "")
            report = dict(payload.get("report") or {})
            by_timeframe.setdefault(timeframe, []).append(report)

        reports_by_interval: dict[str, dict[str, Any]] = {}
        for timeframe in profile.timeframes:
            reports = by_timeframe.get(timeframe) or []
            expected = sum(
                1
                for item in specs
                if str(item.get("timeframe") or "") == timeframe
            )
            if len(reports) != expected:
                raise AppError(
                    f"Distributed {timeframe} aggregation expected {expected} shards "
                    f"but received {len(reports)}."
                )
            merged = combine_strategy_family_reports(
                reports,
                parallel_workers=len(reports),
            )
            merged["timeframe"] = timeframe
            reports_by_interval[timeframe] = merged

        optimization = combine_stock_timeframe_reports(
            one_minute_rows,
            selected,
            symbol,
            reports_by_interval,
            profile.timeframes,
        )
        optimization["parallel_workers"] = len(shard_payloads)
        optimization["parallelized_by"] = "distributed_strategy_family_timeframe"
        compute_seconds = sum(
            float((payload.get("report") or {}).get("distributed_elapsed_seconds") or 0.0)
            for payload in shard_payloads
        )
        slowest_shard_seconds = max(
            (
                float((payload.get("report") or {}).get("distributed_elapsed_seconds") or 0.0)
                for payload in shard_payloads
            ),
            default=0.0,
        )

        def final_progress(completed: int, total: int, message: str) -> None:
            if not job_id:
                return
            if completed >= 965:
                stage = "parameter_stability"
                fraction = 0.98
            elif completed >= 910:
                stage = "walk_forward"
                fraction = 0.94
            else:
                stage = "final_validation"
                fraction = 0.91
            finalization_stage[0] = stage
            _update_parent_cloud_progress(
                job_id,
                run_id=run_id,
                stage=stage,
                progress=fraction,
                message=message,
            )

        finalization_stage[0] = "final_holdout"
        report = complete_stock_strategy_finder_from_optimization(
            one_minute_rows,
            selected,
            selected,
            list(plan.get("technical_skips") or []),
            symbol,
            profile,
            settings,
            optimizer,
            optimization,
            progress=final_progress,
            optimization_seconds=slowest_shard_seconds,
            parallel_workers=len(shard_payloads),
            strategies_considered_count=strategies_considered_count,
        )
        report["market_data_integrity"] = plan.get("market_data_integrity") or {}

        optimization_for_spread = report.get("optimization") or {}
        winner_for_spread = optimization_for_spread.get("winner") or {}
        winning_backtest_for_spread = optimization_for_spread.get("winning_backtest") or {}
        optimized_settings_for_spread = (
            winner_for_spread.get("optimized_backtest_settings") or {}
        )
        optimizer_settings_for_spread = (
            optimization_for_spread.get("optimization_settings") or {}
        )
        sensitivity_multipliers = [
            safe_float(value)
            for value in (
                optimizer_settings_for_spread.get("execution_sensitivity_multipliers")
                or (1.25, 1.5, 1.75, 2.0)
            )
        ]
        maximum_stress_multiplier = max(
            [value for value in sensitivity_multipliers if value is not None]
            or [2.0]
        )
        spread_audit_trades = list(winning_backtest_for_spread.get("trades") or [])
        spread_audit_sessions = list(optimization_for_spread.get("holdout_sessions") or [])
        spread_market = (
            build_market()
            if spread_audit_trades and spread_audit_sessions
            else None
        )
        spread_audit = historical_entry_spread_audit(
            spread_market,
            symbol,
            spread_audit_trades,
            spread_audit_sessions,
            modeled_spread_bps=(
                safe_float(optimized_settings_for_spread.get("spread_bps"), 12.0)
                or 12.0
            ),
            maximum_stress_multiplier=maximum_stress_multiplier,
        )
        report = apply_historical_spread_integrity_guard(report, spread_audit)

        report["distributed"] = {
            "enabled": True,
            "run_id": run_id,
            "shard_count": len(shard_payloads),
            "family_group_count": len(
                {
                    int(item.get("group") or 0)
                    for item in specs
                }
            ),
            "timeframes": list(profile.timeframes),
            "optimization_compute_seconds_sum": round(compute_seconds, 3),
            "slowest_shard_seconds": round(slowest_shard_seconds, 3),
            "research_start": plan.get("research_start"),
            "research_end": plan.get("research_end"),
        }
        report["parallel_workers"] = len(shard_payloads)
        report["parallelized_by"] = "distributed_strategy_family_timeframe"
        finalization_stage[0] = "saving_completed_report"

        def save_result(data: dict[str, Any]) -> dict[str, Any]:
            data = merge_finder_report_into_library(data, report)
            data = finish_research_job(
                data,
                job_id,
                result_ref=(
                    f"distributed-finder:{symbol}:{profile.name}:"
                    f"{report.get('generated_at')}"
                ),
            )
            return record_worker_run(
                data,
                worker_id="distributed-finder-aggregate",
                job_id=job_id,
                job_type="stock_finder",
                status="complete",
                detail=(
                    f"{symbol} {profile.name} completed across {len(shard_payloads)} "
                    f"distributed shards and "
                    f"{int(report.get('unique_configurations_tested') or 0):,} configurations."
                ),
            )

        mutate_remote_library(save_result)

        for spec in specs:
            try:
                artifacts.delete(
                    shard_path(run_id, int(spec.get("index") or 0))
                )
            except Exception:
                pass
        try:
            artifacts.delete(plan_path(run_id))
        except Exception:
            pass

        print(
            f"Distributed Finder complete: {symbol} {profile.name}, "
            f"{len(shard_payloads)} shards, "
            f"{int(report.get('unique_configurations_tested') or 0):,} configurations.",
            flush=True,
        )
        return 0
    except Exception as exc:
        if job_id:
            _mark_parent_failed(
                job_id,
                (
                    "Distributed Finder finalization failed during "
                    f"{finalization_stage[0]}: {exc}"
                ),
                stage=finalization_stage[0],
            )
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("prepare", "shard", "aggregate"),
    )
    parser.add_argument("--run-id", default="")
    parser.add_argument("--index", type=int, default=-1)
    parser.add_argument("--job-id", default="")
    args = parser.parse_args()

    if args.command == "prepare":
        return command_prepare(args.job_id)
    if args.command == "shard":
        if not args.run_id or args.index < 0:
            raise AppError("Shard mode requires --run-id and --index.")
        return command_shard(args.run_id, args.index)
    if not args.run_id:
        raise AppError("Aggregate mode requires --run-id.")
    return command_aggregate(args.run_id)


if __name__ == "__main__":
    raise SystemExit(main())
