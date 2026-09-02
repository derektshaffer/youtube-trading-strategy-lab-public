from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from hybrid_runtime.cloud_bridge import CloudBridgeWorker, DesktopCloudSettings
from hybrid_runtime.cloud_link_store import CloudLinkStore
from hybrid_runtime.contracts import ExecutionTarget, JobRequest, JobStatus
from hybrid_runtime.github_library import GitHubLibraryConfig, RemoteJSONDocument
from hybrid_runtime.router import RoutingPolicy
from hybrid_runtime.service import HybridService
from hybrid_runtime.stock_finder_bridge import (
    DISTRIBUTED_STOCK_FINDER_WORKFLOW,
    finder_dedupe_key,
)
from hybrid_runtime.storage import HybridStore


class FakeClient:
    def __init__(self) -> None:
        self.config = GitHubLibraryConfig(repository="owner/private-data")
        self.document = {
            "strategies": [],
            "validation_runs": [],
            "research_queue": [],
            "stock_strategy_finder_runs": [],
        }
        self.revision_number = 1
        self.write_count = 0
        self.dispatches: list[dict] = []

    @property
    def revision(self) -> str:
        return f"revision-{self.revision_number}"

    def read(self) -> RemoteJSONDocument:
        return RemoteJSONDocument(
            data=deepcopy(self.document),
            revision=self.revision,
            blob_sha=f"blob-{self.revision_number}",
        )

    def write(self, document, *, expected_revision: str, message: str) -> str:
        if expected_revision != self.revision:
            raise AssertionError("stale test revision")
        self.document = deepcopy(dict(document))
        self.revision_number += 1
        self.write_count += 1
        return self.revision

    def dispatch_workflow(self, inputs=None, *, workflow_file=None) -> bool:
        self.dispatches.append(
            {
                "inputs": dict(inputs or {}),
                "workflow_file": workflow_file,
            }
        )
        return True


class DesktopStockFinderBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.service = HybridService(HybridStore(self.root / "jobs.sqlite3"))
        self.links = CloudLinkStore(self.root / "links.sqlite3")
        self.client = FakeClient()
        self.settings = DesktopCloudSettings(
            github=GitHubLibraryConfig(repository="owner/private-data"),
            poll_seconds=2.0,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def worker(self) -> CloudBridgeWorker:
        return CloudBridgeWorker(
            self.service,
            self.links,
            data_dir=self.root,
            settings_loader=lambda _path: self.settings,
            token_loader=lambda _settings: "test-token",
            client_factory=lambda _config, _token: self.client,
        )

    def submit(self, symbol: str = "SDOT", profile: str = "Deep"):
        record, created = self.service.submit(
            {
                "job_type": "strategy.stock_finder",
                "payload": {
                    "symbol": symbol,
                    "profile": profile,
                    "continue_after_app_exit": True,
                },
                "requested_target": "auto",
                "idempotency_key": f"desktop-finder-{symbol}-{profile}",
                "engine_version": "desktop-stock-finder-v1",
            }
        )
        self.assertTrue(created)
        self.assertEqual(record.execution_target.value, "cloud")
        return record

    def test_router_keeps_stock_finder_cloud_only(self):
        decision = RoutingPolicy().decide(
            JobRequest(
                "strategy.stock_finder",
                {"symbol": "SDOT", "profile": "Quick"},
                requested_target=ExecutionTarget.LOCAL,
            )
        )
        self.assertEqual(decision.target.value, "cloud")

    def test_publish_uses_existing_stock_finder_queue_and_distributed_workflow(self):
        local = self.submit()
        worker = self.worker()

        self.assertTrue(worker.run_once())
        queue = self.client.document["research_queue"]
        self.assertEqual(len(queue), 1)
        remote = queue[0]
        self.assertEqual(remote["type"], "stock_finder")
        self.assertEqual(remote["payload"]["symbol"], "SDOT")
        self.assertEqual(remote["payload"]["profile"], "Deep")
        self.assertEqual(
            remote["payload"]["hybrid_cloud_bridge"]["local_job_id"],
            local.id,
        )
        self.assertEqual(remote["dedupe_key"], finder_dedupe_key("SDOT", "Deep"))
        self.assertEqual(len(self.client.dispatches), 1)
        self.assertEqual(
            self.client.dispatches[0]["workflow_file"],
            DISTRIBUTED_STOCK_FINDER_WORKFLOW,
        )
        self.assertEqual(
            self.client.dispatches[0]["inputs"]["job_id"],
            remote["id"],
        )
        current = self.service.get(local.id)
        self.assertEqual(current.status, JobStatus.CLAIMED)
        self.assertEqual(current.stage, "cloud_queued")
        link = self.links.get(local.id)
        self.assertEqual(link["metadata"]["symbol"], "SDOT")
        self.assertEqual(link["metadata"]["profile"], "Deep")

    def test_distributed_payload_progress_reconciles_to_local_job_and_link(self):
        local = self.submit()
        worker = self.worker()
        worker.run_once()
        remote = self.client.document["research_queue"][0]
        remote["status"] = "running"
        remote["payload"].update(
            {
                "distributed_stage": "distributed_optimization",
                "distributed_progress": 0.55,
                "distributed_message": "Cloud optimization: 6 of 12 shards complete",
                "distributed_run_id": "dist-one",
                "distributed_shards_total": 12,
                "distributed_shards_completed": [0, 1, 2, 3, 4, 5],
            }
        )
        self.client.revision_number += 1

        worker.run_once()
        current = self.service.get(local.id)
        self.assertEqual(current.status, JobStatus.OPTIMIZING)
        self.assertAlmostEqual(current.progress, 0.55)
        link = self.links.get(local.id)
        self.assertEqual(link["remote_stage"], "distributed_optimization")
        self.assertEqual(link["metadata"]["distributed_run_id"], "dist-one")
        self.assertEqual(len(link["metadata"]["distributed_shards_completed"]), 6)

    def test_completion_returns_exact_saved_finder_report(self):
        local = self.submit()
        worker = self.worker()
        worker.run_once()
        remote = self.client.document["research_queue"][0]
        generated_at = "2026-09-02T18:00:00+00:00"
        remote["status"] = "complete"
        remote["result_ref"] = f"distributed-finder:SDOT:Deep:{generated_at}"
        remote["payload"].update(
            {
                "distributed_stage": "complete",
                "distributed_progress": 1.0,
            }
        )
        self.client.document["stock_strategy_finder_runs"] = [
            {
                "id": "finder-summary-one",
                "generated_at": generated_at,
                "symbol": "SDOT",
                "profile": "Deep",
                "winner_strategy_name": "Momentum Breakout",
                "winner_source_strategy_id": "family-1",
                "timeframe": "5Min",
                "unique_configurations_tested": 1234,
                "robustness": {"score": 72.0},
                "verdict": {"code": "research_only"},
                "holdout_metrics": {"net_pnl": 55.0, "trade_count": 4},
                "validation_metrics": {"net_pnl": 40.0, "trade_count": 5},
            }
        ]
        self.client.revision_number += 1

        worker.run_once()
        current = self.service.get(local.id)
        self.assertEqual(current.status, JobStatus.COMPLETE)
        self.assertEqual(current.result["outcome"], "stock_finder_complete")
        report = current.result["finder_report"]
        self.assertEqual(report["symbol"], "SDOT")
        profile = report.get("profile")
        if isinstance(profile, dict):
            self.assertEqual(profile.get("name"), "Deep")
        else:
            self.assertEqual(profile, "Deep")
        self.assertEqual(report["winner_strategy_name"], "Momentum Breakout")
        self.assertEqual(report["unique_configurations_tested"], 1234)

    def test_completed_old_same_dedupe_does_not_block_new_finder_run(self):
        self.client.document["research_queue"] = [
            {
                "id": "old-run",
                "type": "stock_finder",
                "status": "complete",
                "dedupe_key": finder_dedupe_key("SDOT", "Deep"),
                "payload": {"symbol": "SDOT", "profile": "Deep"},
            }
        ]
        local = self.submit()
        self.worker().run_once()
        queue = self.client.document["research_queue"]
        self.assertEqual(len(queue), 2)
        active = [item for item in queue if item.get("status") == "queued"]
        self.assertEqual(len(active), 1)
        self.assertEqual(
            active[0]["payload"]["hybrid_cloud_bridge"]["local_job_id"],
            local.id,
        )

    def test_link_loss_reattaches_by_local_marker_without_duplicate(self):
        local = self.submit()
        worker = self.worker()
        worker.run_once()
        remote_id = self.client.document["research_queue"][0]["id"]
        self.links.delete(local.id)

        worker.run_once()
        self.assertEqual(len(self.client.document["research_queue"]), 1)
        self.assertEqual(self.links.get(local.id)["remote_job_id"], remote_id)
        self.assertEqual(len(self.client.dispatches), 1)

    def test_complete_without_matching_durable_report_fails_closed(self):
        local = self.submit()
        worker = self.worker()
        worker.run_once()
        remote = self.client.document["research_queue"][0]
        remote["status"] = "complete"
        remote["result_ref"] = "distributed-finder:SDOT:Deep:missing-stamp"
        self.client.revision_number += 1

        worker.run_once()
        current = self.service.get(local.id)
        self.assertEqual(current.status, JobStatus.FAILED)
        self.assertEqual(current.error["type"], "CloudStockFinderResultMissing")


if __name__ == "__main__":
    unittest.main()
