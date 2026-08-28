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
    complete_stock_strategy_finder_from_optimization,
    merge_finder_report_into_library,
    search_profile,
    selected_strategies_for_profile,
    stock_finder_optimizer_settings,
)
from trading_catalyst_core import (
    enrich_bars_with_point_in_time_catalysts,
    historical_news,
)
from trading_research_orchestrator import (
    claim_next_research_job,
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
    normalize_machine_rules,
    optimize_stock_strategies_parallel,
    resample_intraday_bars,
    isoformat_utc,
    utc_now,
)

UTC = timezone.utc


def env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def build_cloud_backup(*, path: str = DEFAULT_GITHUB_BACKUP_PATH) -> GitHubCloudBackup:
    repository = env("GITHUB_BACKUP_REPOSITORY")
    token = env("GITHUB_BACKUP_TOKEN")
    if not repository or not token:
        raise AppError(
            "Distributed Finder needs GITHUB_BACKUP_REPOSITORY and GITHUB_BACKUP_TOKEN."
        )
    return GitHubCloudBackup(
        repository,
        token,
        branch=env("GITHUB_BACKUP_BRANCH"),
        path=path,
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
        current = helper._request(helper._contents_url(), missing_ok=True)
        body: dict[str, Any] = {
            "message": "Store distributed Stock Strategy Finder shard",
            "content": base64.b64encode(payload).decode("ascii"),
            "branch": helper.branch,
        }
        if current is not None:
            body["sha"] = current.get("sha")
        helper._request(
            helper._contents_url(include_branch=False),
            method="PUT",
            payload=body,
        )

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


def _claim_stock_finder_job(worker_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    holder: dict[str, Any] = {}

    def mutation(data: dict[str, Any]) -> dict[str, Any] | None:
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


def _mark_parent_failed(job_id: str, message: str) -> None:
    def mutation(data: dict[str, Any]) -> dict[str, Any]:
        failed = fail_research_job(
            data,
            job_id,
            message,
            retry_delay_minutes=15,
        )
        return record_worker_run(
            failed,
            worker_id="distributed-finder",
            job_id=job_id,
            job_type="stock_finder",
            status="failed",
            detail=message,
        )

    mutate_remote_library(mutation)


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


def _balanced_family_groups(
    strategy_ids: list[str],
    group_count: int,
) -> list[list[str]]:
    count = max(1, min(int(group_count), len(strategy_ids)))
    groups: list[list[str]] = [[] for _ in range(count)]
    for index, strategy_id in enumerate(strategy_ids):
        groups[index % count].append(strategy_id)
    return [group for group in groups if group]


def command_prepare() -> int:
    worker_id = f"distributed-prepare:{os.getpid()}"
    job, library = _claim_stock_finder_job(worker_id)
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
    run_id = "dist-" + hashlib.sha256(
        f"{job_id}|{job.get('attempts')}|{isoformat_utc(utc_now())}".encode("utf-8")
    ).hexdigest()[:20]

    try:
        strategies = [
            dict(item)
            for item in library.get("strategies") or []
            if isinstance(item, dict)
        ]
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
            max_pages=400,
            progress=history_progress,
        )
        rows = list(rows_by_symbol.get(symbol) or [])
        if not rows:
            raise AppError(f"No historical bars were returned for {symbol}.")

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
            "version": 1,
            "run_id": run_id,
            "parent_job_id": job_id,
            "symbol": symbol,
            "profile_name": profile.name,
            "research_start": start.isoformat(),
            "research_end": end.isoformat(),
            "created_at": isoformat_utc(utc_now()),
            "all_strategies": strategies,
            "selected_strategies": selected,
            "technical_skips": skipped,
            "one_minute_rows": rows,
            "backtest_settings": asdict(BacktestSettings()),
            "optimization_settings": asdict(
                stock_finder_optimizer_settings(profile)
            ),
            "shards": matrix,
        }
        artifacts = PrivateRunArtifactStore()
        artifacts.write_json_gz(plan_path(run_id), plan)
        _update_parent_distribution(
            job_id,
            run_id=run_id,
            shard_count=len(matrix),
            start=start,
            end=end,
        )

        public_matrix = {
            "include": [
                {
                    "index": item["index"],
                    "label": item["label"],
                }
                for item in matrix
            ]
        }
        Path("distributed_matrix.json").write_text(
            json.dumps(public_matrix, separators=(",", ":")),
            encoding="utf-8",
        )
        Path("distributed_meta.json").write_text(
            json.dumps(
                {
                    "has_job": True,
                    "run_id": run_id,
                    "parent_job_id": job_id,
                    "symbol": symbol,
                    "profile": profile.name,
                    "shard_count": len(matrix),
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        print(
            f"Prepared {symbol} {profile.name}: {len(selected)} families, "
            f"{len(profile.timeframes)} timeframes, {len(matrix)} distributed shards.",
            flush=True,
        )
        return 0
    except Exception as exc:
        _mark_parent_failed(job_id, f"Distributed Finder prepare failed: {exc}")
        raise


def command_shard(run_id: str, index: int) -> int:
    artifacts = PrivateRunArtifactStore()
    plan = artifacts.read_json_gz(plan_path(run_id))
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
            "version": 1,
            "run_id": run_id,
            "index": int(index),
            "timeframe": timeframe,
            "family_ids": sorted(wanted),
            "report": report,
        },
    )
    print(
        f"Completed shard {index}: {timeframe}, {len(selected)} families, "
        f"{int(report.get('unique_configurations_tested') or 0):,} configurations.",
        flush=True,
    )
    return 0


def command_aggregate(run_id: str) -> int:
    artifacts = PrivateRunArtifactStore()
    plan = artifacts.read_json_gz(plan_path(run_id))
    job_id = str(plan.get("parent_job_id") or "")
    specs = [item for item in plan.get("shards") or [] if isinstance(item, dict)]
    try:
        shard_payloads: list[dict[str, Any]] = []
        missing: list[int] = []
        for spec in specs:
            index = int(spec.get("index") or 0)
            try:
                shard_payloads.append(
                    artifacts.read_json_gz(shard_path(run_id, index))
                )
            except FileNotFoundError:
                missing.append(index)
        if missing:
            raise AppError(
                "Distributed Finder did not receive every shard result. Missing: "
                + ", ".join(str(value) for value in missing)
            )

        selected = [
            dict(item)
            for item in plan.get("selected_strategies") or []
            if isinstance(item, dict)
        ]
        all_strategies = [
            dict(item)
            for item in plan.get("all_strategies") or []
            if isinstance(item, dict)
        ]
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

        optimization_started = min(
            (
                float((payload.get("report") or {}).get("distributed_elapsed_seconds") or 0.0)
                for payload in shard_payloads
            ),
            default=0.0,
        )
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

        report = complete_stock_strategy_finder_from_optimization(
            one_minute_rows,
            all_strategies,
            selected,
            list(plan.get("technical_skips") or []),
            symbol,
            profile,
            settings,
            optimizer,
            optimization,
            optimization_seconds=slowest_shard_seconds,
            parallel_workers=len(shard_payloads),
        )
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
                f"Distributed Finder aggregate failed: {exc}",
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
    args = parser.parse_args()

    if args.command == "prepare":
        return command_prepare()
    if args.command == "shard":
        if not args.run_id or args.index < 0:
            raise AppError("Shard mode requires --run-id and --index.")
        return command_shard(args.run_id, args.index)
    if not args.run_id:
        raise AppError("Aggregate mode requires --run-id.")
    return command_aggregate(args.run_id)


if __name__ == "__main__":
    raise SystemExit(main())
