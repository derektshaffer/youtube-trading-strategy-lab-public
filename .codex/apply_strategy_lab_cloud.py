from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    file = ROOT / path
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement target, found {count}: {old[:80]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, addition: str) -> None:
    file = ROOT / path
    text = file.read_text(encoding="utf-8")
    if marker in text:
        return
    file.write_text(text.rstrip() + "\n\n\n" + addition.strip() + "\n", encoding="utf-8")


# Durable research queue contract.
replace_once(
    "trading_research_orchestrator.py",
    '        "predictive_ml_backfill",\n        "stock_finder",\n',
    '        "predictive_ml_backfill",\n        "stock_finder",\n        "strategy_lab",\n',
)
replace_once(
    "cloud_research_worker.py",
    'CONTINUOUS_WORKER_JOB_TYPES = SUPPORTED_RESEARCH_JOB_TYPES - {"stock_finder"}',
    'CONTINUOUS_WORKER_JOB_TYPES = SUPPORTED_RESEARCH_JOB_TYPES - {"stock_finder", "strategy_lab"}',
)

# Public wrapper around the existing UI-independent Strategy Lab job runner.
append_once(
    "strategy_lab_jobs.py",
    "def execute_strategy_lab_job_once(",
    '''
def execute_strategy_lab_job_once(
    *,
    run_id: str,
    job: dict[str, Any],
    checkpoint_store: Any,
    market: Any,
    main_store: Any,
    executor: Any = execute_strategy_lab_run,
) -> dict[str, Any]:
    """Run one Strategy Lab job through the existing resumable job contract.

    This is used by the dedicated cloud worker. It intentionally shares the
    exact checkpoint, optimizer-resume, retry, and execution code used by the
    in-process web runner instead of reimplementing Strategy Lab logic.
    """

    return _run_job(
        str(run_id),
        dict(job),
        checkpoint_store=checkpoint_store,
        market=market,
        main_store=main_store,
        executor=executor,
    )
''',
)

# Router: Strategy Lab is true cloud; option discovery remains local/read-only.
replace_once(
    "hybrid_runtime/router.py",
    '            "strategy.stock_finder",\n            "research.autonomous",',
    '            "strategy.stock_finder",\n            "strategy.strategy_lab",\n            "research.autonomous",',
)
replace_once(
    "hybrid_runtime/router.py",
    '            "library.results_summary",\n            "strategy.profit_first_plan",',
    '            "library.results_summary",\n            "library.strategy_lab_options",\n            "strategy.profit_first_plan",',
)

# Local options + Results checkpoint merge.
replace_once(
    "hybrid_runtime/engine_adapter.py",
    '''    loaded = load_library_for_job(payload, data_dir=_desktop_data_dir())
    _check_cancelled(cancelled)
    progress(0.58, "preparing_features", "Compacting recent durable research evidence")
    from .results_summary import build_results_summary

    limit = _positive_int(payload.get("limit"), default=30, minimum=5, maximum=100)
    result = dict(build_results_summary(loaded.library, limit=limit))
''',
    '''    loaded = load_library_for_job(payload, data_dir=_desktop_data_dir())
    _check_cancelled(cancelled)
    progress(0.48, "preparing_features", "Loading the small Strategy Lab checkpoint")
    combined_library = dict(loaded.library)
    try:
        from .library_source import load_strategy_lab_checkpoint_library

        checkpoint = load_strategy_lab_checkpoint_library(data_dir=_desktop_data_dir())
        checkpoint_runs = [
            dict(item)
            for item in checkpoint.library.get("validation_runs") or []
            if isinstance(item, Mapping)
        ]
        if checkpoint_runs:
            combined_library["validation_runs"] = [
                *checkpoint_runs,
                *[
                    dict(item)
                    for item in combined_library.get("validation_runs") or []
                    if isinstance(item, Mapping)
                    and str(item.get("record_type") or "") != "strategy_lab_checkpoint"
                ],
            ]
    except Exception:
        # Results remains usable from the authoritative main library even when
        # the optional small Strategy Lab checkpoint cannot refresh.
        pass
    progress(0.62, "preparing_features", "Compacting recent durable research evidence")
    from .results_summary import build_results_summary

    limit = _positive_int(payload.get("limit"), default=30, minimum=5, maximum=100)
    result = dict(build_results_summary(combined_library, limit=limit))
''',
)
replace_once(
    "hybrid_runtime/engine_adapter.py",
    '''def profit_first_plan_handler(
''',
    '''def strategy_lab_options_handler(
    payload: Mapping[str, Any],
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> Mapping[str, Any]:
    progress(0.15, "downloading_data", "Loading the authoritative strategy library")
    from .library_source import load_library_for_job

    loaded = load_library_for_job(payload, data_dir=_desktop_data_dir())
    _check_cancelled(cancelled)
    progress(0.58, "preparing_features", "Applying the Strategy Lab fidelity gate")
    from .strategy_lab_options import build_strategy_lab_options

    limit = _positive_int(payload.get("limit"), default=300, minimum=1, maximum=500)
    result = dict(build_strategy_lab_options(loaded.library, limit=limit))
    result["library"] = dict(loaded.metadata)
    _check_cancelled(cancelled)
    progress(0.92, "saving", "Preparing faithful Strategy Lab choices")
    return result


def profit_first_plan_handler(
''',
)
replace_once(
    "hybrid_runtime/engine_adapter.py",
    '        "library.results_summary": results_summary_handler,\n        "strategy.profit_first_plan": profit_first_plan_handler,',
    '        "library.results_summary": results_summary_handler,\n        "library.strategy_lab_options": strategy_lab_options_handler,\n        "strategy.profit_first_plan": profit_first_plan_handler,',
)

# Optional small Strategy Lab checkpoint source for Results and cloud progress.
append_once(
    "hybrid_runtime/library_source.py",
    "def load_strategy_lab_checkpoint_library(",
    '''
def load_strategy_lab_checkpoint_library(
    *,
    data_dir: str | Path | None = None,
) -> LoadedLibrary:
    """Load the small durable Strategy Lab checkpoint without touching job secrets."""

    from .strategy_lab_bridge import STRATEGY_LAB_CHECKPOINT_PATH

    root = _desktop_data_dir(data_dir)
    settings = load_desktop_settings(root)
    token, credential_source = _github_token(settings)
    cache_directory = root / "strategy-lab-checkpoint-cache"
    cache_path = cache_directory / "strategy_library.json"
    if not token:
        if cache_path.is_file():
            return _loaded(
                _read_json_object(cache_path),
                source="strategy_lab_checkpoint_cache",
                source_detail=str(cache_path),
                cloud_refreshed=False,
                warning="Strategy Lab checkpoint is from the last local cache.",
            )
        return _loaded(
            {"validation_runs": []},
            source="strategy_lab_checkpoint_unavailable",
            source_detail=STRATEGY_LAB_CHECKPOINT_PATH,
            cloud_refreshed=False,
            warning="Strategy Lab checkpoint is unavailable until the GitHub connection is configured.",
        )

    from youtube_strategy_engine import GitHubCloudBackup, StrategyStore

    cloud = GitHubCloudBackup(
        settings.github_repository,
        token,
        branch=settings.github_branch,
        path=STRATEGY_LAB_CHECKPOINT_PATH,
    )
    store = StrategyStore(directory=cache_directory, cloud_backup=cloud)
    try:
        library = store.load_latest()
        return _loaded(
            library,
            source="private_strategy_lab_checkpoint",
            source_detail=(
                f"{settings.github_repository}@{settings.github_branch}:"
                f"{STRATEGY_LAB_CHECKPOINT_PATH}"
            ),
            cloud_refreshed=True,
            credential_source=credential_source,
        )
    except Exception as exc:
        if store.path.is_file():
            try:
                cached = _read_json_object(store.path)
            except LibrarySourceError:
                cached = None
            if cached is not None:
                return _loaded(
                    cached,
                    source="strategy_lab_checkpoint_cache_after_cloud_error",
                    source_detail=str(store.path),
                    cloud_refreshed=False,
                    warning=redact_text(exc, (token,))[:240],
                    credential_source=credential_source,
                )
        return _loaded(
            {"validation_runs": []},
            source="strategy_lab_checkpoint_error",
            source_detail=STRATEGY_LAB_CHECKPOINT_PATH,
            cloud_refreshed=False,
            warning=redact_text(exc, (token,))[:240],
            credential_source=credential_source,
        )
''',
)

# Production entrypoint.
replace_once(
    "desktop/trading_intelligence/ui.py",
    "from .results_window import MainWindow, clean_error, write_metrics",
    "from .strategy_lab_window import MainWindow, clean_error, write_metrics",
)

# Cloud bridge registration and stage mapping.
replace_once(
    "hybrid_runtime/cloud_bridge.py",
    'SUPPORTED_CLOUD_JOB_TYPES = frozenset({"strategy.profit_first_validation", "strategy.stock_finder"})',
    'SUPPORTED_CLOUD_JOB_TYPES = frozenset({"strategy.profit_first_validation", "strategy.stock_finder", "strategy.strategy_lab"})',
)
replace_once(
    "hybrid_runtime/cloud_bridge.py",
    '    "stress": JobStatus.VALIDATING,\n    "distributed_optimization": JobStatus.OPTIMIZING,',
    '    "stress": JobStatus.VALIDATING,\n    "history": JobStatus.DOWNLOADING_DATA,\n    "integrity": JobStatus.PREPARING_FEATURES,\n    "catalysts": JobStatus.DOWNLOADING_DATA,\n    "optimization": JobStatus.OPTIMIZING,\n    "stability": JobStatus.VALIDATING,\n    "spread_audit": JobStatus.VALIDATING,\n    "distributed_optimization": JobStatus.OPTIMIZING,',
)
replace_once(
    "hybrid_runtime/cloud_bridge.py",
    '''    def _publication_for(
        self,
        job: JobRecord,
        library: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, bool, dict[str, Any] | None]:
        if job.job_type == "strategy.stock_finder":
''',
    '''    def _publication_for(
        self,
        job: JobRecord,
        library: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, bool, dict[str, Any] | None]:
        if job.job_type == "strategy.strategy_lab":
            from .strategy_lab_bridge import prepare_strategy_lab_publication

            return prepare_strategy_lab_publication(library, job)
        if job.job_type == "strategy.stock_finder":
''',
)
replace_once(
    "hybrid_runtime/cloud_bridge.py",
    '''        metadata: dict[str, Any]
        if job.job_type == "strategy.stock_finder":
            from .stock_finder_bridge import finder_link_metadata

            metadata = finder_link_metadata(item, job)
        else:
''',
    '''        metadata: dict[str, Any]
        if job.job_type == "strategy.strategy_lab":
            from .strategy_lab_bridge import strategy_lab_link_metadata

            metadata = strategy_lab_link_metadata(item, job)
        elif job.job_type == "strategy.stock_finder":
            from .stock_finder_bridge import finder_link_metadata

            metadata = finder_link_metadata(item, job)
        else:
''',
)
replace_once(
    "hybrid_runtime/cloud_bridge.py",
    '        library = dict(remote.data)\n        published:',
    '''        library = dict(remote.data)
        strategy_lab_checkpoint_library: dict[str, Any] = {}
        if any(job.job_type == "strategy.strategy_lab" for job in jobs):
            try:
                from .strategy_lab_bridge import strategy_lab_checkpoint_config

                checkpoint_client = self.client_factory(
                    strategy_lab_checkpoint_config(settings.github),
                    token,
                )
                strategy_lab_checkpoint_library = dict(checkpoint_client.read().data)
            except (ValueError, GitHubLibraryError):
                # The checkpoint may not exist until the remote worker writes its
                # first progress record. The durable main queue remains authoritative.
                strategy_lab_checkpoint_library = {}
        published:''',
)
replace_once(
    "hybrid_runtime/cloud_bridge.py",
    '''            if link:
                if job.job_type == "strategy.stock_finder":
                    from .stock_finder_bridge import find_finder_remote_item

                    item = find_finder_remote_item(
                        library,
                        local_job_id=job.id,
                        remote_job_id=str(link.get("remote_job_id") or ""),
                        dedupe_key=str(link.get("remote_dedupe_key") or ""),
                    )
                else:
''',
    '''            if link:
                if job.job_type == "strategy.strategy_lab":
                    from .strategy_lab_bridge import find_strategy_lab_remote_item

                    item = find_strategy_lab_remote_item(
                        library,
                        local_job_id=job.id,
                        remote_job_id=str(link.get("remote_job_id") or ""),
                        dedupe_key=str(link.get("remote_dedupe_key") or ""),
                    )
                elif job.job_type == "strategy.stock_finder":
                    from .stock_finder_bridge import find_finder_remote_item

                    item = find_finder_remote_item(
                        library,
                        local_job_id=job.id,
                        remote_job_id=str(link.get("remote_job_id") or ""),
                        dedupe_key=str(link.get("remote_dedupe_key") or ""),
                    )
                else:
''',
)
replace_once(
    "hybrid_runtime/cloud_bridge.py",
    '''        for job, item, _plan in published:
            try:
                if job.job_type == "strategy.stock_finder":
                    from .stock_finder_bridge import DISTRIBUTED_STOCK_FINDER_WORKFLOW

                    client.dispatch_workflow(
                        {"job_id": str(item.get("id") or "")},
                        workflow_file=DISTRIBUTED_STOCK_FINDER_WORKFLOW,
                    )
                else:
''',
    '''        for job, item, _plan in published:
            try:
                if job.job_type == "strategy.strategy_lab":
                    from .strategy_lab_bridge import CLOUD_STRATEGY_LAB_WORKFLOW

                    client.dispatch_workflow(
                        {"job_id": str(item.get("id") or "")},
                        workflow_file=CLOUD_STRATEGY_LAB_WORKFLOW,
                    )
                elif job.job_type == "strategy.stock_finder":
                    from .stock_finder_bridge import DISTRIBUTED_STOCK_FINDER_WORKFLOW

                    client.dispatch_workflow(
                        {"job_id": str(item.get("id") or "")},
                        workflow_file=DISTRIBUTED_STOCK_FINDER_WORKFLOW,
                    )
                else:
''',
)
replace_once(
    "hybrid_runtime/cloud_bridge.py",
    '''        for job, item, _plan in published + attached:
            self._link(
                job,
                item,
                settings,
                revision=revision,
                dispatch_error=dispatch_errors.get(job.id, ""),
            )
            self._advance_from_remote(
                job,
                item,
                library,
                settings,
                revision,
            )
''',
    '''        for job, item, _plan in published + attached:
            effective_item = item
            if job.job_type == "strategy.strategy_lab":
                from .strategy_lab_bridge import (
                    overlay_strategy_lab_checkpoint,
                    strategy_lab_checkpoint_record,
                )

                payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
                run_id = str(payload.get("run_id") or job.payload.get("run_id") or "")
                checkpoint = strategy_lab_checkpoint_record(
                    strategy_lab_checkpoint_library,
                    run_id,
                )
                effective_item = overlay_strategy_lab_checkpoint(item, checkpoint)
            self._link(
                job,
                effective_item,
                settings,
                revision=revision,
                dispatch_error=dispatch_errors.get(job.id, ""),
            )
            self._advance_from_remote(
                job,
                effective_item,
                library,
                settings,
                revision,
            )
''',
)
replace_once(
    "hybrid_runtime/cloud_bridge.py",
    '''            default_message = (
                "Cloud Stock Finder failed"
                if job.job_type == "strategy.stock_finder"
                else "Cloud validation failed"
            )
            default_type = (
                "CloudStockFinderError"
                if job.job_type == "strategy.stock_finder"
                else "CloudValidationError"
            )
''',
    '''            if job.job_type == "strategy.strategy_lab":
                default_message = "Cloud Strategy Lab failed"
                default_type = "CloudStrategyLabError"
            elif job.job_type == "strategy.stock_finder":
                default_message = "Cloud Stock Finder failed"
                default_type = "CloudStockFinderError"
            else:
                default_message = "Cloud validation failed"
                default_type = "CloudValidationError"
''',
)
replace_once(
    "hybrid_runtime/cloud_bridge.py",
    '''        if status in {"complete", "completed", "success", "succeeded", "done"}:
            current = self._ensure_claimed(job.id, "Cloud worker accepted the job")
            if job.job_type == "strategy.stock_finder":
''',
    '''        if status in {"complete", "completed", "success", "succeeded", "done"}:
            current = self._ensure_claimed(job.id, "Cloud worker accepted the job")
            if job.job_type == "strategy.strategy_lab":
                remote_result = item.get("result") if isinstance(item.get("result"), Mapping) else {}
                if not remote_result or str(remote_result.get("outcome") or "") != "strategy_lab_complete":
                    self.service.store.transition_job(
                        job.id,
                        JobStatus.FAILED,
                        stage="failed",
                        progress=max(current.progress, progress),
                        error={
                            "type": "CloudStrategyLabResultMissing",
                            "message": (
                                "The cloud Strategy Lab job completed but its exact durable "
                                "checkpoint result could not be reconciled."
                            ),
                        },
                        worker_id=self.worker_id,
                        message="Cloud Strategy Lab result was missing",
                    )
                    return
                self.service.complete(
                    job.id,
                    {
                        **dict(remote_result),
                        "remote_job_id": str(item.get("id") or ""),
                        "remote_dedupe_key": str(item.get("dedupe_key") or ""),
                        "result_ref": str(item.get("result_ref") or ""),
                        "research_library_revision": revision,
                    },
                    worker_id=self.worker_id,
                )
                return
            if job.job_type == "strategy.stock_finder":
''',
)
replace_once(
    "hybrid_runtime/cloud_bridge.py",
    '''        for job, plan in terminal_plans:
            queue_status = str(plan.get("queue_status") or "invalid-plan")
            if job.job_type == "strategy.stock_finder":
''',
    '''        for job, plan in terminal_plans:
            queue_status = str(plan.get("queue_status") or "invalid-plan")
            if job.job_type == "strategy.strategy_lab":
                current = self._ensure_claimed(job.id, "Cloud Strategy Lab request evaluated")
                self.service.store.transition_job(
                    job.id,
                    JobStatus.FAILED,
                    stage="failed",
                    progress=current.progress,
                    error={
                        "type": "CloudStrategyLabPlanError",
                        "message": str(
                            plan.get("bridge_error")
                            or "Strategy Lab cloud plan is not publishable."
                        ),
                    },
                    worker_id=self.worker_id,
                    message="Cloud Strategy Lab plan failed",
                )
                continue
            if job.job_type == "strategy.stock_finder":
''',
)
replace_once(
    "hybrid_runtime/cloud_bridge.py",
    "Configure a private GitHub research library before launching cloud validation.",
    "Configure a private GitHub research library before launching cloud research.",
)

# If the small checkpoint reaches complete before the main queue's final write,
# let the desktop reconcile immediately from that exact durable result.
replace_once(
    "hybrid_runtime/strategy_lab_bridge.py",
    '''    if checkpoint_status == "complete":
        result["status"] = "complete"
        result["progress"] = 1.0
        result["stage"] = "complete"
''',
    '''    if checkpoint_status == "complete":
        result["status"] = "complete"
        result["progress"] = 1.0
        result["stage"] = "complete"
        summary = strategy_lab_result_from_checkpoint(checkpoint)
        if summary:
            result["result"] = summary
            result["result_ref"] = (
                f"strategy-lab-checkpoint:{str(checkpoint.get('id') or '')}"
            )
''',
)

print("Strategy Lab cloud integration patch applied")
