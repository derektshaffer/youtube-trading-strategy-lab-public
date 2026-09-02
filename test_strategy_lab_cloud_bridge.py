from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest

from cloud_strategy_lab_worker import _resolve_candidates
from hybrid_runtime.cloud_bridge import CloudBridgeWorker, DesktopCloudSettings
from hybrid_runtime.cloud_link_store import CloudLinkStore
from hybrid_runtime.contracts import ExecutionTarget, JobRequest, JobStatus
from hybrid_runtime.github_library import GitHubLibraryConfig, RemoteJSONDocument
from hybrid_runtime.router import RoutingPolicy
from hybrid_runtime.service import HybridService
from hybrid_runtime.storage import HybridStore
from hybrid_runtime.strategy_lab_bridge import (
    CLOUD_STRATEGY_LAB_WORKFLOW,
    STRATEGY_LAB_CHECKPOINT_PATH,
    prepare_strategy_lab_publication,
)
from strategy_lab_jobs import execute_strategy_lab_job_once
from youtube_strategy_engine import StrategyStore


class FakeGitHub:
    def __init__(self) -> None:
        self.documents = {
            "main-library.json": {
                "strategies": [],
                "validation_runs": [],
                "research_queue": [],
            },
            STRATEGY_LAB_CHECKPOINT_PATH: {"validation_runs": []},
        }
        self.revisions = {key: 1 for key in self.documents}
        self.dispatches: list[dict] = []

    def client(self, config: GitHubLibraryConfig, _token: str):
        owner = self
        path = config.path

        class Client:
            def __init__(self) -> None:
                self.config = config

            @property
            def revision(self) -> str:
                return f"{path}-revision-{owner.revisions.setdefault(path, 1)}"

            def read(self) -> RemoteJSONDocument:
                data = owner.documents.setdefault(path, {"validation_runs": []})
                return RemoteJSONDocument(
                    data=deepcopy(data),
                    revision=self.revision,
                    blob_sha=f"blob-{owner.revisions.setdefault(path, 1)}",
                )

            def write(self, document, *, expected_revision: str, message: str) -> str:
                if expected_revision != self.revision:
                    raise AssertionError("stale fake revision")
                owner.documents[path] = deepcopy(dict(document))
                owner.revisions[path] = owner.revisions.setdefault(path, 1) + 1
                return self.revision

            def dispatch_workflow(self, inputs=None, *, workflow_file=None) -> bool:
                owner.dispatches.append(
                    {
                        "inputs": dict(inputs or {}),
                        "workflow_file": workflow_file,
                    }
                )
                return True

        return Client()


def _request() -> JobRequest:
    return JobRequest(
        "strategy.strategy_lab",
        {
            "run_id": "strategy-lab-test-1",
            "ticker": "SDOT",
            "timeframe": "5Min",
            "history_days": 30,
            "search_depth": 160,
            "strategy_ids": ["strategy-one"],
            "starting_cash": 2000.0,
            "risk_per_trade": 10.0,
            "max_position": 100.0,
            "max_drawdown": 15.0,
            "training_fraction": 0.60,
            "validation_fraction": 0.20,
            "run_walk_forward": True,
            "wf_history_sessions": 8,
            "wf_test_sessions": 2,
            "wf_folds": 3,
            "continue_after_app_exit": True,
        },
        requested_target=ExecutionTarget.AUTO,
        idempotency_key="strategy-lab-test-1",
    )


def test_strategy_lab_is_cloud_only_even_if_local_is_requested():
    policy = RoutingPolicy()
    automatic = policy.decide(_request())
    forced = policy.decide(
        JobRequest(
            "strategy.strategy_lab",
            {"run_id": "r", "ticker": "SDOT", "strategy_ids": ["s"]},
            requested_target=ExecutionTarget.LOCAL,
        )
    )
    assert automatic.target == ExecutionTarget.CLOUD
    assert forced.target == ExecutionTarget.CLOUD


def test_publication_contains_ids_and_settings_but_not_strategy_definitions():
    with tempfile.TemporaryDirectory() as directory:
        service = HybridService(HybridStore(Path(directory) / "jobs.sqlite3"))
        record, created = service.submit(_request().as_dict())
        assert created
        library = {"research_queue": []}
        item, published, _plan = prepare_strategy_lab_publication(library, record)
        assert published is True
        assert item["type"] == "strategy_lab"
        assert item["payload"]["strategy_ids"] == ["strategy-one"]
        assert item["payload"]["search_depth"] == 160
        assert item["payload"]["continue_after_app_exit"] is True
        assert "candidates" not in item["payload"]
        assert "machine_rules" not in str(item["payload"])


def test_bridge_dispatches_progresses_and_completes_from_small_checkpoint():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        service = HybridService(HybridStore(root / "jobs.sqlite3"))
        links = CloudLinkStore(root / "links.sqlite3")
        fake = FakeGitHub()
        settings = DesktopCloudSettings(
            github=GitHubLibraryConfig(
                repository="owner/private-data",
                path="main-library.json",
                action_repository="owner/app",
            ),
            poll_seconds=2.0,
        )
        worker = CloudBridgeWorker(
            service,
            links,
            data_dir=root,
            settings_loader=lambda _path: settings,
            token_loader=lambda _settings: "test-token",
            client_factory=fake.client,
        )
        local, created = service.submit(_request().as_dict())
        assert created
        assert local.execution_target == ExecutionTarget.CLOUD

        assert worker.run_once()
        queue = fake.documents["main-library.json"]["research_queue"]
        assert len(queue) == 1
        remote = queue[0]
        assert remote["type"] == "strategy_lab"
        assert fake.dispatches == [
            {
                "inputs": {"job_id": remote["id"]},
                "workflow_file": CLOUD_STRATEGY_LAB_WORKFLOW,
            }
        ]

        remote["status"] = "running"
        remote["stage"] = "running"
        fake.documents["main-library.json"]["research_queue"] = [remote]
        fake.documents[STRATEGY_LAB_CHECKPOINT_PATH] = {
            "validation_runs": [
                {
                    "id": "strategy-lab-test-1",
                    "record_type": "strategy_lab_checkpoint",
                    "status": "running",
                    "ticker": "SDOT",
                    "progress": 0.63,
                    "stage": "optimization",
                    "message": "Testing strategy family 7 of 10",
                    "saved_at": "2026-09-02T19:00:00Z",
                }
            ]
        }
        worker.run_once()
        current = service.get(local.id)
        assert current.status == JobStatus.OPTIMIZING
        assert current.progress >= 0.63
        link = links.get(local.id)
        assert link["metadata"]["distributed_message"] == "Testing strategy family 7 of 10"

        fake.documents[STRATEGY_LAB_CHECKPOINT_PATH] = {
            "validation_runs": [
                {
                    "id": "strategy-lab-test-1",
                    "record_type": "strategy_lab_checkpoint",
                    "status": "complete",
                    "ticker": "SDOT",
                    "progress": 1.0,
                    "stage": "complete",
                    "message": "Optimization + validation complete",
                    "saved_at": "2026-09-02T19:03:00Z",
                    "result": {
                        "ticker": "SDOT",
                        "timeframe": "5Min",
                        "history_days": 30,
                        "report": {
                            "winner": {
                                "source_strategy_id": "strategy-one",
                                "strategy_name": "VWAP continuation",
                                "validation_metrics": {"net_pnl": 75.0},
                                "holdout_metrics": {"net_pnl": 55.0},
                            }
                        },
                        "strength": {"score": 81.0},
                        "evidence_verdict": {"code": "research_only"},
                    },
                }
            ]
        }
        worker.run_once()
        complete = service.get(local.id)
        assert complete.status == JobStatus.COMPLETE
        assert complete.result["outcome"] == "strategy_lab_complete"
        assert complete.result["winner_strategy_name"] == "VWAP continuation"
        assert complete.result["holdout_metrics"]["net_pnl"] == 55.0
        assert "report" not in complete.result


def test_cloud_wrapper_actually_calls_existing_executor_and_persists_completion():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        checkpoint_store = StrategyStore(root / "checkpoint")
        main_store = StrategyStore(root / "main")
        calls: list[dict] = []

        class Market:
            historical_feed = "sip"
            live_feed = "sip"

        def fake_executor(job, *, market, main_store, progress, optimizer_resume_state, optimizer_checkpoint):
            calls.append(
                {
                    "ticker": job["ticker"],
                    "resume_state": optimizer_resume_state,
                    "market_type": type(market).__name__,
                }
            )
            progress(0.55, "optimization", "Halfway")
            optimizer_checkpoint({"completed_strategy_ids": ["strategy-one"]})
            return {
                "ticker": job["ticker"],
                "timeframe": job["timeframe"],
                "history_days": job["history_days"],
                "report": {"winner": {"strategy_name": "Fake winner"}},
                "strength": {"score": 77.0},
                "evidence_verdict": {"code": "research_only"},
            }

        outcome = execute_strategy_lab_job_once(
            run_id="wrapper-execution-test",
            job={
                "ticker": "SDOT",
                "timeframe": "5Min",
                "history_days": 30,
                "search_depth": 36,
                "candidates": [{"id": "strategy-one"}],
            },
            checkpoint_store=checkpoint_store,
            market=Market(),
            main_store=main_store,
            executor=fake_executor,
        )
        assert outcome["status"] == "complete"
        assert calls and calls[0]["ticker"] == "SDOT"
        saved = checkpoint_store.load_latest()
        record = saved["validation_runs"][0]
        assert record["id"] == "wrapper-execution-test"
        assert record["status"] == "complete"
        assert record["result"]["report"]["winner"]["strategy_name"] == "Fake winner"


@patch("cloud_strategy_lab_worker.effective_strategy_for_research")
@patch("cloud_strategy_lab_worker.strategy_integrity_report")
def test_cloud_worker_reapplies_fidelity_gate(integrity, effective):
    library = {
        "strategies": [
            {"id": "good", "name": "Good"},
            {"id": "blocked", "name": "Blocked"},
        ]
    }
    integrity.side_effect = lambda item: {
        "status": "faithful" if item["id"] == "good" else "blocked"
    }
    effective.side_effect = lambda item: {**item, "effective": True}

    selected = _resolve_candidates(library, {"strategy_ids": ["good"]})
    assert selected == [{"id": "good", "name": "Good", "effective": True}]

    with pytest.raises(Exception, match="no longer fully modeled"):
        _resolve_candidates(library, {"strategy_ids": ["blocked"]})
