"""Publish desktop cloud jobs into the existing Trading Intelligence queue.

This bridge intentionally does not reimplement validation. It adds one durable,
idempotent queue item to the same research library consumed by the established
cloud worker, then reconciles that remote item back into the desktop SQLite job.
Closing the desktop app stops only reconciliation; the remote worker continues.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import threading
from typing import Any, Callable, Mapping, Protocol

from .cloud_link_store import CloudLinkStore
from .contracts import (
    ExecutionTarget,
    JobRecord,
    JobStatus,
    TERMINAL_JOB_STATUSES,
    normalized_progress,
    utc_now_text,
)
from .github_library import (
    GitHubJSONFile,
    GitHubLibraryConfig,
    GitHubLibraryConflict,
    GitHubLibraryError,
    RemoteJSONDocument,
)
from .keychain import KeychainError, MacOSKeychain
from .security import redact_text
from .service import HybridService


SUPPORTED_CLOUD_JOB_TYPES = frozenset({"strategy.profit_first_validation", "strategy.stock_finder", "strategy.strategy_lab"})
DEFAULT_LIBRARY_PATH = "youtube-strategy-lab/strategy_library.json"
DEFAULT_ACTION_REPOSITORY = "derektshaffer/youtube-trading-strategy-lab-public"
DEFAULT_WORKFLOW_FILE = "continuous-trading-research.yml"


class CloudBridgeClient(Protocol):
    config: GitHubLibraryConfig

    def read(self) -> RemoteJSONDocument: ...

    def write(
        self,
        document: Mapping[str, Any],
        *,
        expected_revision: str,
        message: str,
    ) -> str: ...

    def dispatch_workflow(
        self,
        inputs: Mapping[str, Any] | None = None,
        *,
        workflow_file: str | None = None,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class DesktopCloudSettings:
    github: GitHubLibraryConfig
    token_account: str = "github_backup_token"
    keychain_service: str = "Trading Intelligence Lab"
    poll_seconds: float = 15.0


def _flatten_settings(value: Any, prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    if not isinstance(value, Mapping):
        return flattened
    for key, item in value.items():
        clean_key = str(key or "").strip().lower().replace("-", "_")
        qualified = f"{prefix}_{clean_key}".strip("_")
        if isinstance(item, Mapping):
            flattened.update(_flatten_settings(item, qualified))
        else:
            flattened[clean_key] = item
            flattened[qualified] = item
    return flattened


def _first_text(settings: Mapping[str, Any], names: tuple[str, ...], default: str = "") -> str:
    for name in names:
        value = settings.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _settings_document(data_dir: Path) -> dict[str, Any]:
    candidates = (
        data_dir / "desktop-settings.json",
        data_dir / "desktop_settings.json",
        data_dir / "settings.json",
        data_dir / "trading-intelligence-settings.json",
        data_dir / "Trading Intelligence" / "settings.json",
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(decoded, dict):
            return decoded
    return {}


def load_desktop_cloud_settings(data_dir: str | Path) -> DesktopCloudSettings | None:
    """Read non-secret bridge configuration from environment or desktop settings.

    Field aliases intentionally cover both the first desktop beta and the current
    settings screen. The GitHub token itself is never accepted from the JSON file.
    """

    root = Path(data_dir).expanduser().resolve()
    flattened = _flatten_settings(_settings_document(root))

    def configured(env_name: str, aliases: tuple[str, ...], default: str = "") -> str:
        environment = str(os.environ.get(env_name) or "").strip()
        return environment or _first_text(flattened, aliases, default)

    repository = configured(
        "TRADING_INTELLIGENCE_CLOUD_LIBRARY_REPOSITORY",
        (
            "github_repository",
            "library_repository",
            "backup_repository",
            "github_backup_repository",
            "connection_github_repository",
        ),
    )
    if not repository:
        return None
    path = configured(
        "TRADING_INTELLIGENCE_CLOUD_LIBRARY_PATH",
        (
            "github_path",
            "library_path",
            "backup_path",
            "github_backup_path",
            "connection_github_path",
        ),
        DEFAULT_LIBRARY_PATH,
    )
    branch = configured(
        "TRADING_INTELLIGENCE_CLOUD_LIBRARY_BRANCH",
        (
            "github_branch",
            "library_branch",
            "backup_branch",
            "connection_github_branch",
        ),
        "main",
    )
    action_repository = configured(
        "TRADING_INTELLIGENCE_CLOUD_ACTION_REPOSITORY",
        ("action_repository", "worker_repository", "cloud_action_repository"),
        DEFAULT_ACTION_REPOSITORY,
    )
    workflow_file = configured(
        "TRADING_INTELLIGENCE_CLOUD_WORKFLOW",
        ("workflow_file", "research_workflow", "cloud_workflow"),
        DEFAULT_WORKFLOW_FILE,
    )
    workflow_ref = configured(
        "TRADING_INTELLIGENCE_CLOUD_WORKFLOW_REF",
        ("workflow_ref", "worker_branch", "cloud_workflow_ref"),
        "main",
    )
    token_account = configured(
        "TRADING_INTELLIGENCE_CLOUD_TOKEN_ACCOUNT",
        ("github_token_account", "token_account", "keychain_account"),
        "github_backup_token",
    )
    keychain_service = configured(
        "TRADING_INTELLIGENCE_CLOUD_KEYCHAIN_SERVICE",
        ("keychain_service",),
        "Trading Intelligence Lab",
    )
    try:
        poll_seconds = float(
            configured(
                "TRADING_INTELLIGENCE_CLOUD_POLL_SECONDS",
                ("cloud_poll_seconds", "poll_seconds"),
                "15",
            )
        )
    except (TypeError, ValueError, OverflowError):
        poll_seconds = 15.0
    return DesktopCloudSettings(
        github=GitHubLibraryConfig(
            repository=repository,
            path=path,
            branch=branch,
            action_repository=action_repository,
            workflow_file=workflow_file,
            workflow_ref=workflow_ref,
        ),
        token_account=token_account,
        keychain_service=keychain_service,
        poll_seconds=max(2.0, min(300.0, poll_seconds)),
    )


def load_github_token(settings: DesktopCloudSettings) -> str:
    environment = str(
        os.environ.get("TRADING_INTELLIGENCE_CLOUD_GITHUB_TOKEN") or ""
    ).strip()
    if environment:
        return environment
    accounts: list[str] = []
    for account in (
        settings.token_account,
        "github_backup_token",
        "github_token",
        "GITHUB_TOKEN",
    ):
        clean = str(account or "").strip()
        if clean and clean not in accounts:
            accounts.append(clean)
    keychain = MacOSKeychain(settings.keychain_service)
    for account in accounts:
        try:
            token = keychain.get_secret(account).strip()
        except KeychainError:
            continue
        if token:
            return token
    raise KeychainError(
        "No GitHub cloud token is available in macOS Keychain."
    )


def _queue(library: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in library.get("research_queue") or [] if isinstance(item, dict)]


def _find_remote_item(
    library: Mapping[str, Any],
    *,
    remote_job_id: str = "",
    dedupe_key: str = "",
) -> dict[str, Any] | None:
    clean_id = str(remote_job_id or "").strip()
    clean_dedupe = str(dedupe_key or "").strip()
    for item in library.get("research_queue") or []:
        if not isinstance(item, Mapping):
            continue
        if clean_id and str(item.get("id") or "").strip() == clean_id:
            return dict(item)
        if clean_dedupe and str(item.get("dedupe_key") or "").strip() == clean_dedupe:
            return dict(item)
    return None


def _remote_identifier(dedupe_key: str) -> str:
    digest = sha256(str(dedupe_key).encode("utf-8")).hexdigest()[:24]
    return f"desktop-cloud-{digest}"


def _remote_progress(item: Mapping[str, Any]) -> float:
    payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
    raw = payload.get("distributed_progress")
    if raw is None:
        raw = item.get("progress")
    if raw is None:
        raw = item.get("progress_fraction")
    if raw is None:
        percent = item.get("progress_pct")
        try:
            raw = float(percent) / 100.0
        except (TypeError, ValueError, OverflowError):
            raw = 0.0
    try:
        return normalized_progress(raw)
    except ValueError:
        return 0.0


def _remote_stage(item: Mapping[str, Any]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
    distributed = str(payload.get("distributed_stage") or "").strip().lower()
    if distributed:
        return distributed
    for name in ("stage", "progress_stage", "current_stage"):
        value = str(item.get(name) or "").strip().lower()
        if value:
            return value
    return str(item.get("status") or "queued").strip().lower() or "queued"


def _remote_status(item: Mapping[str, Any]) -> str:
    return str(item.get("status") or "queued").strip().lower().replace("-", "_")


def _remote_message(item: Mapping[str, Any]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
    distributed = " ".join(str(payload.get("distributed_message") or "").split())[:500]
    if distributed:
        return distributed
    return ""


def _strategy_ids(item: Mapping[str, Any], local_job: JobRecord) -> list[str]:
    payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
    raw = payload.get("strategy_ids") or local_job.payload.get("strategy_ids") or []
    return [str(value).strip() for value in raw if str(value).strip()]


def _validation_evidence(
    library: Mapping[str, Any],
    *,
    strategy_ids: list[str],
    created_at: str,
) -> list[dict[str, Any]]:
    wanted = set(strategy_ids)
    rows = []
    for item in library.get("validation_runs") or []:
        if not isinstance(item, Mapping):
            continue
        strategy_id = str(item.get("strategy_id") or "").strip()
        generated_at = str(item.get("generated_at") or "")
        if wanted and strategy_id not in wanted:
            continue
        if created_at and generated_at and generated_at < created_at:
            continue
        rows.append(dict(item))
    rows.sort(key=lambda item: str(item.get("generated_at") or ""), reverse=True)
    latest: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        strategy_id = str(row.get("strategy_id") or "").strip()
        if strategy_id and strategy_id in seen:
            continue
        if strategy_id:
            seen.add(strategy_id)
        latest.append(row)
        if len(latest) >= 3:
            break
    return latest


_ACTIVE_RANK = {
    JobStatus.CLAIMED: 0,
    JobStatus.DOWNLOADING_DATA: 1,
    JobStatus.PREPARING_FEATURES: 2,
    JobStatus.SEARCHING: 3,
    JobStatus.OPTIMIZING: 4,
    JobStatus.VALIDATING: 5,
    JobStatus.SAVING: 6,
}
_STAGE_STATUS = {
    "claimed": JobStatus.CLAIMED,
    "downloading": JobStatus.DOWNLOADING_DATA,
    "downloading_data": JobStatus.DOWNLOADING_DATA,
    "preparing": JobStatus.PREPARING_FEATURES,
    "preparing_features": JobStatus.PREPARING_FEATURES,
    "discovery": JobStatus.SEARCHING,
    "searching": JobStatus.SEARCHING,
    "optimizing": JobStatus.OPTIMIZING,
    "walk_forward": JobStatus.VALIDATING,
    "validating": JobStatus.VALIDATING,
    "holdout": JobStatus.VALIDATING,
    "stress": JobStatus.VALIDATING,
    "history": JobStatus.DOWNLOADING_DATA,
    "integrity": JobStatus.PREPARING_FEATURES,
    "catalysts": JobStatus.DOWNLOADING_DATA,
    "optimization": JobStatus.OPTIMIZING,
    "stability": JobStatus.VALIDATING,
    "spread_audit": JobStatus.VALIDATING,
    "distributed_optimization": JobStatus.OPTIMIZING,
    "final_holdout": JobStatus.VALIDATING,
    "final_validation": JobStatus.VALIDATING,
    "parameter_stability": JobStatus.VALIDATING,
    "historical_spread_audit": JobStatus.VALIDATING,
    "saving_completed_report": JobStatus.SAVING,
    "finalization_retry": JobStatus.VALIDATING,
    "saving": JobStatus.SAVING,
}


class CloudBridgeWorker:
    """Publish and reconcile supported cloud-target jobs.

    Dependencies are injectable so all state, conflict, cancellation, and
    deduplication behavior can be tested without network access or real secrets.
    """

    def __init__(
        self,
        service: HybridService,
        link_store: CloudLinkStore,
        *,
        data_dir: str | Path,
        worker_id: str = "desktop-cloud-bridge",
        settings_loader: Callable[[str | Path], DesktopCloudSettings | None] = load_desktop_cloud_settings,
        token_loader: Callable[[DesktopCloudSettings], str] = load_github_token,
        client_factory: Callable[[GitHubLibraryConfig, str], CloudBridgeClient] = GitHubJSONFile,
        idle_poll_seconds: float = 2.0,
    ) -> None:
        self.service = service
        self.link_store = link_store
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.worker_id = str(worker_id or "desktop-cloud-bridge").strip()
        self.settings_loader = settings_loader
        self.token_loader = token_loader
        self.client_factory = client_factory
        self.idle_poll_seconds = max(0.25, float(idle_poll_seconds))

    def _jobs(self) -> list[JobRecord]:
        return [
            job
            for job in self.service.list(limit=1_000)
            if job.execution_target == ExecutionTarget.CLOUD
            and job.status not in TERMINAL_JOB_STATUSES
            and job.job_type in SUPPORTED_CLOUD_JOB_TYPES
        ]

    def _record_waiting(
        self,
        jobs: list[JobRecord],
        message: str,
        settings: DesktopCloudSettings | None,
    ) -> None:
        repository = settings.github.repository if settings else "unconfigured/cloud-library"
        branch = settings.github.branch if settings else "main"
        path = settings.github.path if settings else DEFAULT_LIBRARY_PATH
        for job in jobs:
            self.link_store.record_error(
                job.id,
                message,
                repository=repository,
                branch=branch,
                path=path,
                metadata={"waiting_for_connection": True},
            )

    def _ensure_claimed(self, job_id: str, message: str) -> JobRecord:
        current = self.service.get(job_id)
        if current.status == JobStatus.QUEUED:
            return self.service.store.transition_job(
                job_id,
                JobStatus.CLAIMED,
                stage="cloud_queued",
                progress=max(current.progress, 0.01),
                worker_id=self.worker_id,
                message=message,
            )
        return current

    def _complete_without_remote_validation(
        self,
        job: JobRecord,
        result: Mapping[str, Any],
    ) -> None:
        current = self._ensure_claimed(job.id, "Cloud request evaluated")
        if current.status not in TERMINAL_JOB_STATUSES:
            self.service.complete(job.id, dict(result), worker_id=self.worker_id)

    def _publication_for(
        self,
        job: JobRecord,
        library: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, bool, dict[str, Any] | None]:
        if job.job_type == "strategy.strategy_lab":
            from .strategy_lab_bridge import prepare_strategy_lab_publication

            return prepare_strategy_lab_publication(library, job)
        if job.job_type == "strategy.stock_finder":
            from .stock_finder_bridge import prepare_stock_finder_publication

            return prepare_stock_finder_publication(library, job)

        from profit_first_queue import profit_first_validation_batch

        maximum = max(1, min(3, int(job.payload.get("maximum_candidates") or 2)))
        batch = profit_first_validation_batch(
            library,
            maximum_candidates=maximum,
        )
        queue_status = str(batch.get("queue_status") or "").strip()
        dedupe_key = str(
            job.payload.get("remote_dedupe_key")
            or batch.get("dedupe_key")
            or ""
        ).strip()
        remote_id = str(
            batch.get("active_job_id")
            or batch.get("existing_job_id")
            or ""
        ).strip()
        existing = _find_remote_item(
            library,
            remote_job_id=remote_id,
            dedupe_key=dedupe_key,
        )
        if existing is not None:
            return existing, False, batch
        if queue_status in {"active", "already-attempted"}:
            # The remote library may have compacted an old queue record while
            # retaining validation evidence. Link by dedupe and let reconciliation
            # search validation_runs instead of creating duplicate work.
            synthetic = {
                "id": remote_id or _remote_identifier(dedupe_key or job.request_fingerprint),
                "type": "autonomous_validation",
                "job_type": "autonomous_validation",
                "status": "complete" if queue_status == "already-attempted" else "queued",
                "stage": queue_status,
                "progress": 1.0 if queue_status == "already-attempted" else 0.0,
                "dedupe_key": dedupe_key,
                "payload": {
                    "strategy_ids": list(
                        batch.get("active_strategy_ids")
                        or batch.get("strategy_ids")
                        or []
                    ),
                    "origin": "automatic_profit_first_validation",
                },
                "bridge_reconstructed": True,
            }
            return synthetic, False, batch
        if queue_status == "no-eligible-candidates":
            return None, False, batch
        if queue_status != "ready" or not dedupe_key:
            return None, False, {
                **batch,
                "queue_status": queue_status or "invalid-plan",
                "bridge_error": "Profit First did not produce a publishable dedupe key.",
            }

        now = utc_now_text()
        remote_id = _remote_identifier(dedupe_key)
        payload = dict(batch.get("payload") or {})
        payload["hybrid_cloud_bridge"] = {
            "version": 1,
            "local_job_id": job.id,
            "request_fingerprint": job.request_fingerprint,
        }
        item = {
            "id": remote_id,
            "request_id": remote_id,
            "type": "autonomous_validation",
            "job_type": "autonomous_validation",
            "status": "queued",
            "stage": "queued",
            "progress": 0.0,
            "priority": max(100, int(job.priority)),
            "attempt": 0,
            "attempts": 0,
            "max_attempts": 3,
            "dedupe_key": dedupe_key,
            "origin": "trading_intelligence_desktop",
            "source": "trading_intelligence_desktop",
            "created_at": now,
            "updated_at": now,
            "queued_at": now,
            "payload": payload,
        }
        queue = _queue(library)
        queue.append(item)
        library["research_queue"] = queue
        return item, True, batch

    def _link(
        self,
        job: JobRecord,
        item: Mapping[str, Any],
        settings: DesktopCloudSettings,
        *,
        revision: str,
        dispatch_error: str = "",
    ) -> dict[str, Any]:
        metadata: dict[str, Any]
        if job.job_type == "strategy.strategy_lab":
            from .strategy_lab_bridge import strategy_lab_link_metadata

            metadata = strategy_lab_link_metadata(item, job)
        elif job.job_type == "strategy.stock_finder":
            from .stock_finder_bridge import finder_link_metadata

            metadata = finder_link_metadata(item, job)
        else:
            metadata = {
                "strategy_ids": _strategy_ids(item, job),
                "job_type": job.job_type,
            }
        return self.link_store.upsert(
            local_job_id=job.id,
            remote_job_id=str(item.get("id") or ""),
            remote_dedupe_key=str(item.get("dedupe_key") or ""),
            repository=settings.github.repository,
            branch=settings.github.branch,
            path=settings.github.path,
            remote_status=_remote_status(item),
            remote_stage=_remote_stage(item),
            remote_progress=_remote_progress(item),
            last_remote_revision=revision,
            dispatch_attempted_at=utc_now_text(),
            dispatch_error=dispatch_error,
            last_sync_at=utc_now_text(),
            metadata=metadata,
        )

    def _cancel_remote_if_requested(
        self,
        job: JobRecord,
        item: dict[str, Any],
    ) -> bool:
        if not job.cancel_requested and job.status != JobStatus.CANCELLING:
            return False
        status = _remote_status(item)
        if status in {"complete", "completed", "success", "failed", "error", "cancelled", "canceled"}:
            return False
        now = utc_now_text()
        item["cancel_requested"] = True
        item["updated_at"] = now
        if status in {"queued", "pending", "retry", "retry_wait"}:
            item["status"] = "cancelled"
            item["stage"] = "cancelled"
            item["completed_at"] = now
        return True

    def _advance_from_remote(
        self,
        job: JobRecord,
        item: Mapping[str, Any],
        library: Mapping[str, Any],
        settings: DesktopCloudSettings,
        revision: str,
    ) -> None:
        status = _remote_status(item)
        stage = _remote_stage(item)
        progress = _remote_progress(item)
        self._link(job, item, settings, revision=revision)
        current = self.service.get(job.id)
        if current.status in TERMINAL_JOB_STATUSES:
            return

        if status in {"cancelled", "canceled"}:
            if current.status not in {JobStatus.QUEUED, JobStatus.RETRY_WAIT, JobStatus.CANCELLING}:
                current = self.service.store.transition_job(
                    job.id,
                    JobStatus.CANCELLING,
                    stage="cancelling",
                    progress=current.progress,
                    worker_id=self.worker_id,
                    message="Cloud worker acknowledged cancellation",
                )
            self.service.store.transition_job(
                job.id,
                JobStatus.CANCELLED,
                stage="cancelled",
                progress=current.progress,
                worker_id=self.worker_id,
                message="Cloud job cancelled",
            )
            return

        if status in {"failed", "error", "dead", "abandoned"}:
            current = self._ensure_claimed(job.id, "Cloud worker accepted the job")
            if job.job_type == "strategy.strategy_lab":
                default_message = "Cloud Strategy Lab failed"
                default_type = "CloudStrategyLabError"
            elif job.job_type == "strategy.stock_finder":
                default_message = "Cloud Stock Finder failed"
                default_type = "CloudStockFinderError"
            else:
                default_message = "Cloud validation failed"
                default_type = "CloudValidationError"
            error_value = item.get("error") or item.get("last_error") or default_message
            if isinstance(error_value, Mapping):
                error = dict(error_value)
                error.setdefault("type", default_type)
                error.setdefault("message", default_message)
            else:
                error = {
                    "type": default_type,
                    "message": redact_text(error_value),
                }
            self.service.store.transition_job(
                job.id,
                JobStatus.FAILED,
                stage="failed",
                progress=max(current.progress, progress),
                error=error,
                worker_id=self.worker_id,
                message=default_message,
            )
            return

        if status in {"complete", "completed", "success", "succeeded", "done"}:
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
                from .stock_finder_bridge import finder_report_for_remote

                report = finder_report_for_remote(library, item, job)
                if not report:
                    self.service.store.transition_job(
                        job.id,
                        JobStatus.FAILED,
                        stage="failed",
                        progress=max(current.progress, progress),
                        error={
                            "type": "CloudStockFinderResultMissing",
                            "message": (
                                "The cloud Finder job completed but its exact durable report "
                                "could not be reconciled from the research library."
                            ),
                        },
                        worker_id=self.worker_id,
                        message="Cloud Stock Finder result was missing",
                    )
                    return
                payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
                self.service.complete(
                    job.id,
                    {
                        "outcome": "stock_finder_complete",
                        "remote_job_id": str(item.get("id") or ""),
                        "remote_dedupe_key": str(item.get("dedupe_key") or ""),
                        "result_ref": str(item.get("result_ref") or ""),
                        "symbol": str(payload.get("symbol") or job.payload.get("symbol") or "").upper(),
                        "profile": str(payload.get("profile") or job.payload.get("profile") or ""),
                        "finder_report": report,
                        "research_library_revision": revision,
                    },
                    worker_id=self.worker_id,
                )
                return
            strategy_ids = _strategy_ids(item, job)
            evidence_cutoff = str(
                item.get("created_at")
                or item.get("queued_at")
                or ""
            ).strip()
            evidence = _validation_evidence(
                library,
                strategy_ids=strategy_ids,
                created_at=evidence_cutoff,
            )
            remote_result = item.get("result") or item.get("output") or item.get("validation_result")
            result = {
                "outcome": "cloud_validation_complete",
                "remote_job_id": str(item.get("id") or ""),
                "remote_dedupe_key": str(item.get("dedupe_key") or ""),
                "strategy_ids": strategy_ids,
                "remote_result": dict(remote_result) if isinstance(remote_result, Mapping) else remote_result,
                "validation_runs": evidence,
                "research_library_revision": revision,
            }
            self.service.complete(job.id, result, worker_id=self.worker_id)
            return

        current = self._ensure_claimed(job.id, "Cloud queue accepted the desktop job")
        if current.status == JobStatus.CANCELLING:
            return
        desired = _STAGE_STATUS.get(stage)
        if desired is None:
            desired = JobStatus.CLAIMED if status in {"queued", "pending", "retry", "retry_wait"} else JobStatus.VALIDATING
        if current.status in _ACTIVE_RANK and desired in _ACTIVE_RANK:
            if _ACTIVE_RANK[desired] < _ACTIVE_RANK[current.status]:
                desired = current.status
        message = _remote_message(item)
        if not message:
            message = (
                "Waiting in the cloud research queue"
                if desired == JobStatus.CLAIMED
                else f"Cloud research: {stage.replace('_', ' ')}"
            )
        self.service.store.transition_job(
            job.id,
            desired,
            stage="cloud_queued" if desired == JobStatus.CLAIMED else stage,
            progress=max(current.progress, progress, 0.01),
            worker_id=self.worker_id,
            message=message,
        )

    def run_once(self) -> bool:
        jobs = self._jobs()
        if not jobs:
            return False
        settings = self.settings_loader(self.data_dir)
        if settings is None:
            self._record_waiting(
                jobs,
                "Configure a private GitHub research library before launching cloud research.",
                None,
            )
            return True
        try:
            token = self.token_loader(settings)
            client = self.client_factory(settings.github, token)
            remote = client.read()
        except (ValueError, KeychainError, GitHubLibraryError) as exc:
            self._record_waiting(jobs, redact_text(exc), settings)
            return True

        library = dict(remote.data)
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
        published: list[tuple[JobRecord, dict[str, Any], dict[str, Any] | None]] = []
        attached: list[tuple[JobRecord, dict[str, Any], dict[str, Any] | None]] = []
        terminal_plans: list[tuple[JobRecord, dict[str, Any]]] = []
        changed = False

        for job in jobs:
            link = self.link_store.get(job.id)
            item = None
            if link:
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
                    item = _find_remote_item(
                        library,
                        remote_job_id=str(link.get("remote_job_id") or ""),
                        dedupe_key=str(link.get("remote_dedupe_key") or ""),
                    )
            plan: dict[str, Any] | None = None
            if item is None:
                try:
                    item, created, plan = self._publication_for(job, library)
                except Exception as exc:
                    self.link_store.record_error(
                        job.id,
                        redact_text(exc),
                        repository=settings.github.repository,
                        branch=settings.github.branch,
                        path=settings.github.path,
                    )
                    continue
                if item is None:
                    terminal_plans.append((job, dict(plan or {})))
                    continue
                changed = changed or created
                (published if created else attached).append((job, item, plan))
            else:
                attached.append((job, item, plan))

            if self._cancel_remote_if_requested(job, item):
                queue = _queue(library)
                for index, candidate in enumerate(queue):
                    if str(candidate.get("id") or "") == str(item.get("id") or ""):
                        queue[index] = item
                        break
                library["research_queue"] = queue
                changed = True

        revision = remote.revision
        if changed:
            try:
                revision = client.write(
                    library,
                    expected_revision=remote.revision,
                    message="Queue Trading Intelligence desktop cloud research",
                )
            except GitHubLibraryConflict as exc:
                self._record_waiting(jobs, str(exc), settings)
                return True
            except GitHubLibraryError as exc:
                self._record_waiting(jobs, str(exc), settings)
                return True

        dispatch_errors: dict[str, str] = {}
        for job, item, _plan in published:
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
                    client.dispatch_workflow(
                        {"origin": "trading_intelligence_desktop"}
                    )
            except GitHubLibraryError as exc:
                # Queue publication is already durable. Scheduled workers can still
                # claim it even when this token lacks Actions permission.
                dispatch_errors[job.id] = redact_text(exc)

        for job, item, _plan in published + attached:
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

        for job, plan in terminal_plans:
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
                current = self._ensure_claimed(job.id, "Cloud Finder request evaluated")
                self.service.store.transition_job(
                    job.id,
                    JobStatus.FAILED,
                    stage="failed",
                    progress=current.progress,
                    error={
                        "type": "CloudStockFinderPlanError",
                        "message": str(
                            plan.get("bridge_error")
                            or "Stock Finder cloud plan is not publishable."
                        ),
                    },
                    worker_id=self.worker_id,
                    message="Cloud Stock Finder plan failed",
                )
                continue
            if queue_status == "no-eligible-candidates":
                self._complete_without_remote_validation(
                    job,
                    {
                        "outcome": "no_eligible_candidates",
                        "message": (
                            "Profit First found no strategy eligible for another strict cloud validation run."
                        ),
                        "plan": plan,
                        "research_library_revision": revision,
                    },
                )
            else:
                self.link_store.record_error(
                    job.id,
                    str(plan.get("bridge_error") or "Cloud validation plan is not publishable."),
                    repository=settings.github.repository,
                    branch=settings.github.branch,
                    path=settings.github.path,
                    metadata={"plan": plan},
                )
        return True

    def run_forever(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                worked = self.run_once()
                settings = self.settings_loader(self.data_dir) if worked else None
                delay = settings.poll_seconds if settings is not None else self.idle_poll_seconds
            except Exception as exc:  # keep reconciliation alive after unexpected provider errors
                jobs = self._jobs()
                settings = self.settings_loader(self.data_dir)
                self._record_waiting(jobs, redact_text(exc), settings)
                delay = max(2.0, self.idle_poll_seconds)
            stop_event.wait(delay)
