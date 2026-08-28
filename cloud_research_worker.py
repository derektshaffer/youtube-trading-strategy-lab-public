"""Cloud worker entrypoint for persistent Trading Intelligence research.

Designed to run outside Streamlit (for example GitHub Actions or a dedicated
worker service) so research continues while the user's browser/computer is off.
"""

from __future__ import annotations

import os
import socket
import sys
from typing import Any

from trading_auto_research import (
    merge_autonomous_research_into_library,
    run_autonomous_research,
)
from trading_research_orchestrator import (
    DEFAULT_GEMINI_BULK_FALLBACK_MODEL,
    DEFAULT_GEMINI_BULK_RESEARCH_MODEL,
    DEFAULT_GEMINI_SPECIALIST_FALLBACK_MODEL,
    DEFAULT_GEMINI_SPECIALIST_MODEL,
    GeminiResearchRouter,
    apply_specialist_review,
    claim_next_research_job,
    fail_research_job,
    find_external_research_run,
    find_research_hypothesis,
    finish_research_job,
    merge_grounded_research,
    record_worker_run,
    research_queue_status,
    seed_continuous_research_cycle,
)
from youtube_strategy_engine import (
    AlpacaMarketData,
    AppError,
    DEFAULT_GITHUB_BACKUP_PATH,
    GitHubCloudBackup,
    StrategyStore,
)


def env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def build_store() -> StrategyStore:
    repository = env("GITHUB_BACKUP_REPOSITORY")
    token = env("GITHUB_BACKUP_TOKEN")
    if not repository or not token:
        raise AppError(
            "Cloud research needs GITHUB_BACKUP_REPOSITORY and GITHUB_BACKUP_TOKEN "
            "so work is durable across worker runs."
        )
    cloud = GitHubCloudBackup(
        repository,
        token,
        branch=env("GITHUB_BACKUP_BRANCH"),
        path=env("GITHUB_BACKUP_PATH", DEFAULT_GITHUB_BACKUP_PATH),
    )
    return StrategyStore(cloud_backup=cloud)


def build_router() -> GeminiResearchRouter:
    return GeminiResearchRouter(
        env("GEMINI_API_KEY"),
        paid_api_key=env("GEMINI_PAID_API_KEY"),
        bulk_model=env("GEMINI_RESEARCH_BULK_MODEL", DEFAULT_GEMINI_BULK_RESEARCH_MODEL),
        bulk_fallback_model=env(
            "GEMINI_RESEARCH_BULK_FALLBACK_MODEL",
            DEFAULT_GEMINI_BULK_FALLBACK_MODEL,
        ),
        specialist_model=env(
            "GEMINI_RESEARCH_SPECIALIST_MODEL",
            DEFAULT_GEMINI_SPECIALIST_MODEL,
        ),
        specialist_fallback_model=env(
            "GEMINI_RESEARCH_SPECIALIST_FALLBACK_MODEL",
            DEFAULT_GEMINI_SPECIALIST_FALLBACK_MODEL,
        ),
    )


def build_market() -> AlpacaMarketData:
    return AlpacaMarketData(
        env("ALPACA_API_KEY"),
        env("ALPACA_SECRET_KEY"),
        env("ALPACA_LIVE_FEED", "iex"),
        env("ALPACA_HISTORICAL_FEED", "sip"),
    )


def _pending_web_strategies(data: dict[str, Any], maximum: int = 3) -> list[dict[str, Any]]:
    candidates = []
    for item in data.get("strategies") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("source_type") or "") != "autonomous_web_research":
            continue
        if str(item.get("validation_status") or "") == "validated":
            continue
        last = item.get("last_autonomous_research")
        if isinstance(last, dict) and str(last.get("validation_status") or "") in {
            "validated",
            "research_only",
        }:
            # Avoid repeatedly burning compute on the same hypothesis until a
            # later research cycle changes its rules/evidence.
            continue
        candidates.append(dict(item))
    candidates.sort(
        key=lambda item: (
            float(item.get("research_source_quality_score") or 0),
            float(item.get("confidence") or 0),
        ),
        reverse=True,
    )
    return candidates[: max(1, int(maximum))]


def execute_job(
    store: StrategyStore,
    router: GeminiResearchRouter,
    job: dict[str, Any],
    worker_id: str,
) -> str:
    job_type = str(job.get("type") or "")
    payload = dict(job.get("payload") or {})

    if job_type == "web_research":
        topic = str(payload.get("topic") or "").strip()
        research = router.grounded_research(topic)
        latest = store.load_latest()
        latest, run_id, hypothesis_ids = merge_grounded_research(
            latest,
            research,
            topic=topic,
            origin_job_id=str(job.get("id") or ""),
        )
        latest = finish_research_job(latest, str(job.get("id") or ""), result_ref=run_id)
        latest = record_worker_run(
            latest,
            worker_id=worker_id,
            job_id=str(job.get("id") or ""),
            job_type=job_type,
            status="complete",
            detail=f"Grounded research saved with {len(hypothesis_ids)} hypotheses.",
        )
        store.save(latest)
        return run_id

    if job_type == "specialist_review":
        hypothesis_id = str(payload.get("hypothesis_id") or "")
        run_id = str(payload.get("research_run_id") or "")
        latest = store.load_latest()
        hypothesis = find_research_hypothesis(latest, hypothesis_id)
        if hypothesis is None:
            raise AppError("The queued specialist hypothesis no longer exists.")
        research_run = find_external_research_run(latest, run_id)
        review = router.specialist_review(hypothesis, research_run=research_run)
        latest, strategy_id = apply_specialist_review(latest, hypothesis_id, review)
        result_ref = strategy_id or hypothesis_id
        latest = finish_research_job(
            latest,
            str(job.get("id") or ""),
            result_ref=result_ref,
        )
        latest = record_worker_run(
            latest,
            worker_id=worker_id,
            job_id=str(job.get("id") or ""),
            job_type=job_type,
            status="complete",
            detail=f"Specialist decision: {review.get('decision')}.",
        )
        store.save(latest)
        return result_ref

    if job_type == "autonomous_validation":
        latest = store.load_latest()
        candidates = _pending_web_strategies(
            latest,
            maximum=int(env("RESEARCH_VALIDATION_BATCH_SIZE", "3") or 3),
        )
        if not candidates:
            latest = finish_research_job(
                latest,
                str(job.get("id") or ""),
                result_ref="no-pending-validation",
            )
            latest = record_worker_run(
                latest,
                worker_id=worker_id,
                job_id=str(job.get("id") or ""),
                job_type=job_type,
                status="complete",
                detail="No new web-research strategies were awaiting deterministic validation.",
            )
            store.save(latest)
            return "no-pending-validation"

        market = build_market()

        def progress(message: str) -> None:
            print(f"[validation] {message}", flush=True)

        report = run_autonomous_research(
            market,
            candidates,
            universe_sample_size=int(env("RESEARCH_VALIDATION_UNIVERSE_SIZE", "250") or 250),
            deep_strategy_limit=min(
                len(candidates),
                int(env("RESEARCH_VALIDATION_DEEP_LIMIT", "3") or 3),
            ),
            symbols_per_strategy=int(env("RESEARCH_VALIDATION_SYMBOLS_PER_STRATEGY", "6") or 6),
            progress=progress,
        )
        latest = store.load_latest()
        latest = merge_autonomous_research_into_library(latest, report)
        result_ref = f"autonomous:{report.get('generated_at')}"
        latest = finish_research_job(
            latest,
            str(job.get("id") or ""),
            result_ref=result_ref,
        )
        latest = record_worker_run(
            latest,
            worker_id=worker_id,
            job_id=str(job.get("id") or ""),
            job_type=job_type,
            status="complete",
            detail=(
                f"Validated {int(report.get('deep_strategies_tested') or 0)} hypothesis strategy "
                f"candidate(s); {int(report.get('deep_strategies_failed') or 0)} skipped."
            ),
        )
        store.save(latest)
        return result_ref

    raise AppError(f"Unknown cloud research job type: {job_type}")


def main() -> int:
    store = build_store()
    worker_id = env("RESEARCH_WORKER_ID") or (
        f"{socket.gethostname()}:{os.getpid()}"
    )
    jobs_per_run = max(1, min(12, int(env("RESEARCH_JOBS_PER_RUN", "4") or 4)))

    # Seed once per UTC day. Follow-up questions and specialist reviews add more
    # work automatically, so this is a bounded self-feeding queue rather than an
    # uncontrolled infinite API loop.
    data = store.load_latest()
    data, seeded = seed_continuous_research_cycle(
        data,
        maximum_topics=int(env("RESEARCH_TOPICS_PER_CYCLE", "10") or 10),
    )
    if seeded:
        store.save(data)
        print(f"Seeded {seeded} autonomous research topics.", flush=True)

    router = build_router()
    completed = 0
    for _ in range(jobs_per_run):
        data = store.load_latest()
        data, job = claim_next_research_job(data, worker_id)
        if job is None:
            print("No research jobs are ready.", flush=True)
            break
        store.save(data)
        job_id = str(job.get("id") or "")
        job_type = str(job.get("type") or "")
        print(f"Running {job_type} job {job_id}…", flush=True)
        try:
            result_ref = execute_job(store, router, job, worker_id)
            completed += 1
            print(f"Completed {job_id}: {result_ref}", flush=True)
        except Exception as exc:
            latest = store.load_latest()
            latest = fail_research_job(latest, job_id, exc)
            latest = record_worker_run(
                latest,
                worker_id=worker_id,
                job_id=job_id,
                job_type=job_type,
                status="failed",
                detail=str(exc),
            )
            store.save(latest)
            print(f"Failed {job_id}: {exc}", file=sys.stderr, flush=True)

    final = store.load_latest()
    status = research_queue_status(final)
    print(
        f"Worker finished: {completed} completed this run; "
        f"{status['active']} active queued/running/retry job(s) remain.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
