from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile

from hybrid_runtime.cloud_bridge import DesktopCloudSettings
from hybrid_runtime.cloud_link_store import CloudLinkStore
from hybrid_runtime.contracts import JobStatus
from hybrid_runtime.github_library import GitHubLibraryConfig, RemoteJSONDocument
from hybrid_runtime.service import HybridService
from hybrid_runtime.storage import HybridStore
from hybrid_runtime.unified_cloud_bridge import UnifiedCloudBridgeWorker


class FakeClient:
    def __init__(self, config: GitHubLibraryConfig, shared: dict) -> None:
        self.config = config
        self.shared = shared

    def read(self):
        return RemoteJSONDocument(
            data=deepcopy(self.shared["document"]),
            revision=f"r{self.shared['revision']}",
            blob_sha=f"b{self.shared['revision']}",
        )

    def write(self, document, *, expected_revision: str, message: str):
        assert expected_revision == f"r{self.shared['revision']}"
        self.shared["document"] = deepcopy(dict(document))
        self.shared["revision"] += 1
        self.shared["writes"] += 1
        return f"r{self.shared['revision']}"

    def dispatch_workflow(self, inputs=None):
        self.shared["dispatches"].append(
            {
                "workflow": self.config.workflow_file,
                "inputs": dict(inputs or {}),
            }
        )
        return True


def setup_bridge(tmp_path: Path):
    service = HybridService(HybridStore(tmp_path / "jobs.sqlite3"))
    links = CloudLinkStore(tmp_path / "links.sqlite3")
    settings = DesktopCloudSettings(
        github=GitHubLibraryConfig(
            repository="owner/private",
            path="trading-intelligence-lab/intelligence_library.json",
            branch="main",
            action_repository="owner/public",
            workflow_file="continuous-trading-research.yml",
            workflow_ref="main",
        ),
        poll_seconds=2.0,
    )
    shared = {
        "document": {
            "strategies": [],
            "validation_runs": [],
            "research_queue": [],
            "finder_runs": [],
        },
        "revision": 1,
        "writes": 0,
        "dispatches": [],
    }
    worker = UnifiedCloudBridgeWorker(
        service,
        links,
        data_dir=tmp_path,
        settings_loader=lambda _path: settings,
        token_loader=lambda _settings: "secret-token",
        client_factory=lambda config, _token: FakeClient(config, shared),
    )
    return service, links, worker, shared


def submit_finder(service: HybridService, profile: str = "Deep"):
    job, created = service.submit(
        {
            "job_type": "strategy.stock_finder",
            "payload": {
                "symbol": "SDOT",
                "profile": profile,
                "continue_after_app_exit": True,
            },
            "requested_target": "auto",
            "idempotency_key": f"finder-{profile}",
        }
    )
    assert created
    assert job.execution_target.value == "cloud"
    return job


def test_heavy_finder_publishes_existing_stock_finder_contract_and_dispatches_correct_workflow(tmp_path):
    service, links, worker, shared = setup_bridge(tmp_path)
    local = submit_finder(service, "Deep")

    assert worker.run_once() is True
    queue = shared["document"]["research_queue"]
    assert len(queue) == 1
    remote = queue[0]
    assert remote["type"] == "stock_finder"
    assert remote["payload"]["symbol"] == "SDOT"
    assert remote["payload"]["profile"] == "Deep"
    assert remote["payload"]["hybrid_cloud_bridge"]["local_job_id"] == local.id
    assert shared["writes"] == 1
    assert shared["dispatches"] == [
        {
            "workflow": "distributed-stock-finder.yml",
            "inputs": {"job_id": remote["id"]},
        }
    ]
    link = links.get(local.id)
    assert link["remote_job_id"] == remote["id"]
    assert "secret-token" not in str(link)


def test_second_local_job_attaches_to_same_active_symbol_profile_instead_of_duplicate(tmp_path):
    service, _links, worker, shared = setup_bridge(tmp_path)
    first = submit_finder(service, "Very Deep")
    worker.run_once()
    remote_id = shared["document"]["research_queue"][0]["id"]

    second, created = service.submit(
        {
            "job_type": "strategy.stock_finder",
            "payload": {"symbol": "SDOT", "profile": "Very Deep"},
            "requested_target": "auto",
            "idempotency_key": "second-session-same-search",
        }
    )
    assert created
    worker.run_once()
    assert len(shared["document"]["research_queue"]) == 1
    assert worker.link_store.get(first.id)["remote_job_id"] == remote_id
    assert worker.link_store.get(second.id)["remote_job_id"] == remote_id
    assert len(shared["dispatches"]) == 1


def test_completed_distributed_finder_reconciles_saved_compact_report(tmp_path):
    service, _links, worker, shared = setup_bridge(tmp_path)
    local = submit_finder(service, "Current Regime")
    worker.run_once()
    remote = shared["document"]["research_queue"][0]
    remote["status"] = "complete"
    remote["stage"] = "complete"
    remote["progress"] = 1.0
    remote["result_ref"] = "finder-runs/example"
    shared["document"]["finder_runs"] = [
        {
            "id": "finder-runs/example",
            "symbol": "SDOT",
            "profile": "Current Regime",
            "status": "complete",
            "generated_at": "2026-09-02T16:00:00Z",
            "summary": {
                "symbol": "SDOT",
                "profile": "Current Regime",
                "status": "complete",
                "generated_at": "2026-09-02T16:00:00Z",
                "winner": {"source_name": "Breakout Family"},
                "timeframe": "5Min",
                "verdict": {"status": "research_only", "code": "insufficient_robustness"},
                "robustness": {"score": 61.0},
                "walk_forward": {"profitable_fold_pct": 66.7},
                "parameter_stability": {"stable": False},
                "unique_configurations_tested": 1234,
            },
        }
    ]
    shared["revision"] += 1

    worker.run_once()
    current = service.get(local.id)
    assert current.status == JobStatus.COMPLETE
    assert current.result["outcome"] == "cloud_stock_finder_complete"
    assert current.result["winner"]["source_name"] == "Breakout Family"
    assert current.result["execution_target"] == "cloud"


def test_queued_finder_cancellation_propagates_to_remote_queue(tmp_path):
    service, _links, worker, shared = setup_bridge(tmp_path)
    local = submit_finder(service, "Deep")
    worker.run_once()
    service.cancel(local.id)

    worker.run_once()
    remote = shared["document"]["research_queue"][0]
    assert remote["status"] == "cancelled"
    assert remote["cancel_requested"] is True
    assert service.get(local.id).status == JobStatus.CANCELLED
