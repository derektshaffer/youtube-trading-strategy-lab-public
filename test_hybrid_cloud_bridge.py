from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from hybrid_runtime.cloud_bridge import (
    CloudBridgeWorker,
    DesktopCloudSettings,
    _validation_evidence,
    load_desktop_cloud_settings,
)
from hybrid_runtime.cloud_link_store import CloudLinkStore
from hybrid_runtime.contracts import JobStatus
from hybrid_runtime.github_library import GitHubLibraryConfig, GitHubLibraryConflict, GitHubLibraryError, RemoteJSONDocument
from hybrid_runtime.service import HybridService
from hybrid_runtime.storage import HybridStore


class FakeGitHubClient:
    def __init__(self, document: dict) -> None:
        self.config = GitHubLibraryConfig(repository="owner/private-data")
        self.document = deepcopy(document)
        self.revision_number = 1
        self.dispatch_count = 0
        self.write_count = 0

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
            raise AssertionError("test client received stale expected revision")
        self.document = deepcopy(dict(document))
        self.revision_number += 1
        self.write_count += 1
        return self.revision

    def dispatch_workflow(self, inputs=None, *, workflow_file=None) -> bool:
        self.dispatch_count += 1
        return True


class CloudBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.service = HybridService(HybridStore(self.root / "jobs.sqlite3"))
        self.links = CloudLinkStore(self.root / "cloud-links.sqlite3")
        self.settings = DesktopCloudSettings(
            github=GitHubLibraryConfig(repository="owner/private-data"),
            poll_seconds=2.0,
        )
        self.client = FakeGitHubClient(
            {
                "strategies": [
                    {
                        "id": "strategy-one",
                        "name": "Strategy One",
                        "source_type": "research",
                    }
                ],
                "validation_runs": [],
                "research_queue": [],
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def ready(_strategy):
        return {
            "label": "ready_for_backtest",
            "score": 90.0,
            "semantic_critical_missing_requirements": [],
        }

    def worker(self) -> CloudBridgeWorker:
        return CloudBridgeWorker(
            self.service,
            self.links,
            data_dir=self.root,
            settings_loader=lambda _path: self.settings,
            token_loader=lambda _settings: "test-token",
            client_factory=lambda _config, _token: self.client,
        )

    def submit(self, *, idempotency_key: str = "desktop-profit-first-one"):
        record, created = self.service.submit(
            {
                "job_type": "strategy.profit_first_validation",
                "payload": {"maximum_candidates": 2},
                "requested_target": "auto",
                "idempotency_key": idempotency_key,
                "engine_version": "validation-v7",
            }
        )
        self.assertTrue(created)
        self.assertEqual(record.execution_target.value, "cloud")
        return record

    @patch("profit_first_queue.research_readiness")
    def test_publish_is_deduplicated_and_linked(self, readiness):
        readiness.side_effect = self.ready
        local = self.submit()
        worker = self.worker()

        self.assertTrue(worker.run_once())
        queue = self.client.document["research_queue"]
        self.assertEqual(len(queue), 1)
        remote = queue[0]
        self.assertEqual(remote["type"], "autonomous_validation")
        self.assertEqual(
            remote["payload"]["origin"],
            "automatic_profit_first_validation",
        )
        self.assertEqual(
            remote["payload"]["hybrid_cloud_bridge"]["local_job_id"],
            local.id,
        )
        self.assertEqual(self.client.write_count, 1)
        self.assertEqual(self.client.dispatch_count, 1)
        current = self.service.get(local.id)
        self.assertEqual(current.status, JobStatus.CLAIMED)
        self.assertEqual(current.stage, "cloud_queued")
        link = self.links.get(local.id)
        self.assertEqual(link["remote_job_id"], remote["id"])
        self.assertNotIn("test-token", str(link))

        self.assertTrue(worker.run_once())
        self.assertEqual(len(self.client.document["research_queue"]), 1)
        self.assertEqual(self.client.write_count, 1)
        self.assertEqual(self.client.dispatch_count, 1)

    @patch("profit_first_queue.research_readiness")
    def test_profit_first_dispatch_matches_the_no_input_workflow(self, readiness):
        readiness.side_effect = self.ready
        self.submit()
        with patch.object(self.client, "dispatch_workflow", return_value=True) as dispatch:
            self.worker().run_once()
        dispatch.assert_called_once_with()
        workflow = (Path(__file__).parent / ".github/workflows/continuous-trading-research.yml").read_text()
        declaration = re.search(r"(?m)^  workflow_dispatch:\n((?: {4,}[^\n]*\n|\n)*)", workflow)
        self.assertIsNotNone(declaration)
        self.assertNotIn("inputs:", declaration.group(1))

    @patch("profit_first_queue.research_readiness")
    def test_dispatch_failure_survives_reconciliation_without_duplicate_dispatch(self, readiness):
        readiness.side_effect = self.ready
        local = self.submit()
        worker = self.worker()
        with patch.object(self.client, "dispatch_workflow", side_effect=GitHubLibraryError("Workflow rejected input")) as dispatch:
            worker.run_once()
            initial = self.links.get(local.id)
            self.assertTrue(initial["dispatch_attempted_at"])
            self.assertEqual(initial["dispatch_error"], "Workflow rejected input")
            with patch.object(self.client, "read", side_effect=GitHubLibraryError("Temporary read failure")):
                worker.run_once()
            self.assertEqual(self.links.get(local.id)["dispatch_error"], "Temporary read failure")
            worker.run_once()
            worker.run_once()
            dispatch.assert_called_once()
        current = self.links.get(local.id)
        self.assertEqual(current["dispatch_attempted_at"], initial["dispatch_attempted_at"])
        self.assertEqual(current["dispatch_error"], initial["dispatch_error"])
        self.assertEqual(self.client.write_count, 1)
        self.assertEqual(len(self.client.document["research_queue"]), 1)
        self.assertEqual(self.service.get(local.id).status, JobStatus.CLAIMED)

    @patch("profit_first_queue.research_readiness")
    def test_successful_dispatch_timestamp_is_not_rewritten_by_polling(self, readiness):
        readiness.side_effect = self.ready
        local = self.submit()
        worker = self.worker()
        worker.run_once()
        initial = self.links.get(local.id)
        with patch.object(self.client, "read", side_effect=GitHubLibraryError("Temporary read failure")):
            worker.run_once()
        worker.run_once()
        self.assertEqual(self.links.get(local.id)["dispatch_attempted_at"], initial["dispatch_attempted_at"])
        self.assertEqual(self.links.get(local.id)["dispatch_error"], "")
        self.assertEqual(self.client.dispatch_count, 1)

    @patch("profit_first_queue.research_readiness")
    def test_disabled_dispatch_is_not_reported_as_success(self, readiness):
        readiness.side_effect = self.ready
        local = self.submit()
        with patch.object(self.client, "dispatch_workflow", return_value=False):
            self.worker().run_once()
        self.assertIn("not configured", self.links.get(local.id)["dispatch_error"])
        self.assertEqual(len(self.client.document["research_queue"]), 1)

    @patch("profit_first_queue.research_readiness")
    def test_interrupted_read_keeps_job_queued_then_publishes_once(self, readiness):
        readiness.side_effect = self.ready
        local = self.submit()
        worker = self.worker()
        with patch.object(self.client, "read", side_effect=GitHubLibraryError(
            "GitHub cloud bridge transfer failed after 3 attempt(s) (IncompleteRead)."
        )):
            worker.run_once()
        self.assertEqual(self.service.get(local.id).status, JobStatus.QUEUED)
        self.assertIn("IncompleteRead", self.links.get(local.id)["dispatch_error"])
        self.assertEqual(self.client.write_count, 0)
        self.assertEqual(self.client.dispatch_count, 0)

        worker.run_once()
        worker.run_once()
        self.assertEqual(self.service.get(local.id).status, JobStatus.CLAIMED)
        self.assertEqual(self.client.write_count, 1)
        self.assertEqual(self.client.dispatch_count, 1)
        self.assertEqual(len(self.client.document["research_queue"]), 1)
        self.assertEqual(self.links.get(local.id)["dispatch_error"], "")

    @patch("profit_first_queue.research_readiness")
    def test_upload_failures_preserve_all_three_jobs_then_publish_once(self, readiness):
        readiness.side_effect = self.ready
        jobs = [self.submit()]
        for job_type, payload in (
            ("strategy.stock_finder", {"symbol": "SDOT", "profile": "Deep"}),
            ("strategy.strategy_lab", {
                "run_id": "upload-recovery-lab", "ticker": "SDOT",
                "strategy_ids": ["strategy-one"], "run_walk_forward": True,
            }),
        ):
            job, created = self.service.submit({
                "job_type": job_type, "payload": payload,
                "requested_target": "cloud", "idempotency_key": job_type,
            })
            self.assertTrue(created)
            jobs.append(job)
        self.client.document["historical_artifacts"] = {"completed": ["must-survive"]}
        worker = self.worker()
        for failure in (
            GitHubLibraryError("Large cloud-library Git push timed out"),
            GitHubLibraryConflict("GitHub branch moved during the large-library push"),
        ):
            with patch.object(self.client, "write", side_effect=failure):
                worker.run_once()
            self.assertEqual(self.client.dispatch_count, 0)
            self.assertEqual(self.client.write_count, 0)
            self.assertEqual(self.client.document["research_queue"], [])
            for original in jobs:
                current = self.service.get(original.id)
                self.assertEqual(current.status, JobStatus.QUEUED)
                self.assertEqual(current.payload, original.payload)
                self.assertIn(str(failure), self.links.get(original.id)["dispatch_error"])

        worker.run_once()
        worker.run_once()
        self.assertEqual(self.client.write_count, 1)
        self.assertEqual(self.client.dispatch_count, 3)
        self.assertEqual(len(self.client.document["research_queue"]), 3)
        self.assertEqual(self.client.document["historical_artifacts"], {"completed": ["must-survive"]})
        self.assertEqual({job.id for job in self.service.list()}, {job.id for job in jobs})
        for original in jobs:
            self.assertEqual(self.service.get(original.id).status, JobStatus.CLAIMED)
            self.assertEqual(self.links.get(original.id)["dispatch_error"], "")

    @patch("profit_first_queue.research_readiness")
    def test_completed_remote_validation_finishes_local_job(self, readiness):
        readiness.side_effect = self.ready
        local = self.submit()
        worker = self.worker()
        worker.run_once()
        remote = self.client.document["research_queue"][0]
        remote["status"] = "complete"
        remote["stage"] = "complete"
        remote["progress"] = 1.0
        remote["result"] = {"validation_status": "research_only"}
        # Anchor evidence to the actual queue timestamp rather than a wall-clock
        # constant, so this regression remains valid indefinitely.
        remote_stamp = str(remote["created_at"])
        remote["updated_at"] = remote_stamp
        self.client.document["validation_runs"] = [
            {
                "strategy_id": "strategy-one",
                "generated_at": remote_stamp,
                "validation_status": "research_only",
                "evidence_verdict": {"code": "insufficient_robustness"},
            }
        ]
        self.client.revision_number += 1

        worker.run_once()
        current = self.service.get(local.id)
        self.assertEqual(current.status, JobStatus.COMPLETE)
        self.assertEqual(current.result["outcome"], "cloud_validation_complete")
        self.assertEqual(current.result["strategy_ids"], ["strategy-one"])
        self.assertEqual(len(current.result["validation_runs"]), 1)

    def test_compacted_remote_can_reuse_older_latest_matching_evidence(self):
        library = {
            "validation_runs": [
                {
                    "strategy_id": "strategy-one",
                    "generated_at": "2026-08-30T12:00:00Z",
                    "validation_status": "research_only",
                },
                {
                    "strategy_id": "other-strategy",
                    "generated_at": "2026-09-02T12:00:00Z",
                    "validation_status": "research_only",
                },
            ]
        }
        evidence = _validation_evidence(
            library,
            strategy_ids=["strategy-one"],
            created_at="",
        )
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["strategy_id"], "strategy-one")

    @patch("profit_first_queue.research_readiness")
    def test_missing_local_link_reattaches_without_duplicate_remote_job(self, readiness):
        readiness.side_effect = self.ready
        local = self.submit()
        worker = self.worker()
        worker.run_once()
        remote_id = self.client.document["research_queue"][0]["id"]
        self.links.delete(local.id)

        worker.run_once()
        self.assertEqual(len(self.client.document["research_queue"]), 1)
        self.assertEqual(self.links.get(local.id)["remote_job_id"], remote_id)
        self.assertEqual(self.client.write_count, 1)
        self.assertIsNone(self.links.get(local.id)["dispatch_attempted_at"])
        self.assertEqual(self.client.dispatch_count, 1)

    @patch("profit_first_queue.research_readiness")
    def test_queued_remote_job_honors_desktop_cancellation(self, readiness):
        readiness.side_effect = self.ready
        local = self.submit()
        worker = self.worker()
        worker.run_once()
        cancelled = self.service.cancel(local.id)
        self.assertEqual(cancelled.status, JobStatus.CANCELLING)

        worker.run_once()
        remote = self.client.document["research_queue"][0]
        self.assertEqual(remote["status"], "cancelled")
        self.assertTrue(remote["cancel_requested"])
        self.assertEqual(self.service.get(local.id).status, JobStatus.CANCELLED)

    @patch("profit_first_queue.research_readiness")
    def test_no_eligible_candidate_completes_with_clear_nonvalidation_outcome(self, readiness):
        readiness.return_value = {
            "label": "partially_modeled",
            "score": 45.0,
            "semantic_critical_missing_requirements": ["Historical float"],
        }
        local = self.submit()

        self.worker().run_once()
        current = self.service.get(local.id)
        self.assertEqual(current.status, JobStatus.COMPLETE)
        self.assertEqual(current.result["outcome"], "no_eligible_candidates")
        self.assertEqual(self.client.document["research_queue"], [])
        self.assertEqual(self.client.write_count, 0)

    def test_unconfigured_bridge_leaves_job_queued_and_records_actionable_state(self):
        local = self.submit()
        worker = CloudBridgeWorker(
            self.service,
            self.links,
            data_dir=self.root,
            settings_loader=lambda _path: None,
            token_loader=lambda _settings: "unused",
            client_factory=lambda _config, _token: self.client,
        )

        self.assertTrue(worker.run_once())
        self.assertEqual(self.service.get(local.id).status, JobStatus.QUEUED)
        link = self.links.get(local.id)
        self.assertTrue(link["metadata"]["waiting_for_connection"])
        self.assertIn("Configure", link["dispatch_error"])

    def test_settings_loader_accepts_production_desktop_aliases_without_secret(self):
        (self.root / "desktop-settings.json").write_text(
            """{
              "connection": {
                "github_repository": "owner/private-data",
                "github_branch": "research",
                "github_path": "data/library.json",
                "github_token": "must-not-be-read"
              },
              "cloud_poll_seconds": 9
            }""",
            encoding="utf-8",
        )
        settings = load_desktop_cloud_settings(self.root)
        self.assertIsNotNone(settings)
        self.assertEqual(settings.github.repository, "owner/private-data")
        self.assertEqual(settings.github.branch, "research")
        self.assertEqual(settings.github.path, "data/library.json")
        self.assertEqual(settings.poll_seconds, 9.0)
        self.assertEqual(settings.token_account, "github_backup_token")


if __name__ == "__main__":
    unittest.main()
