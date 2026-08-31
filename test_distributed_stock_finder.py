import os
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import distributed_stock_finder


class DistributedStockFinderReliabilityTests(unittest.TestCase):
    def test_cloud_backup_uses_intelligence_path_from_environment(self):
        with patch.dict(
            os.environ,
            {
                "GITHUB_BACKUP_REPOSITORY": "owner/private-backup",
                "GITHUB_BACKUP_TOKEN": "token",
                "GITHUB_BACKUP_PATH": "trading-intelligence-lab/intelligence_library.json",
            },
            clear=False,
        ):
            backup = distributed_stock_finder.build_cloud_backup()
        self.assertEqual(
            backup.path,
            "trading-intelligence-lab/intelligence_library.json",
        )

    def test_explicit_artifact_path_still_wins(self):
        with patch.dict(
            os.environ,
            {
                "GITHUB_BACKUP_REPOSITORY": "owner/private-backup",
                "GITHUB_BACKUP_TOKEN": "token",
                "GITHUB_BACKUP_PATH": "trading-intelligence-lab/intelligence_library.json",
            },
            clear=False,
        ):
            backup = distributed_stock_finder.build_cloud_backup(path="custom/run.json")
        self.assertEqual(backup.path, "custom/run.json")

    def test_cloud_workflows_default_to_intelligence_library(self):
        root = Path(__file__).resolve().parent
        expected = "trading-intelligence-lab/intelligence_library.json"
        for relative in (
            ".github/workflows/distributed-stock-finder.yml",
            ".github/workflows/continuous-trading-research.yml",
            ".github/workflows/cloud-research-smoke-test.yml",
        ):
            content = (root / relative).read_text(encoding="utf-8")
            self.assertIn(expected, content, msg=f"{relative} points at the wrong durable queue.")

    @staticmethod
    def _saved_sdot_job(*, status="retry"):
        return {
            "id": "rq-sdot",
            "type": "stock_finder",
            "status": status,
            "attempts": 1,
            "max_attempts": 2,
            "payload": {
                "symbol": "SDOT",
                "profile": "Very Deep",
                "distributed_run_id": "dist-sdot",
                "distributed_shards_total": 12,
                "distributed_shards_completed": list(range(12)),
                "distributed_progress": 0.98,
                "distributed_stage": "parameter_stability",
            },
        }

    @staticmethod
    def _saved_sdot_plan():
        return {
            "version": distributed_stock_finder.DISTRIBUTED_PLAN_VERSION,
            "run_id": "dist-sdot",
            "parent_job_id": "rq-sdot",
            "symbol": "SDOT",
            "profile_name": "Very Deep",
            "research_start": "2026-01-01T00:00:00+00:00",
            "research_end": "2026-08-28T00:00:00+00:00",
            "selected_strategies": [],
            "technical_skips": [],
            "one_minute_rows": [],
            "market_data_integrity": {
                "mode": "raw_prices",
                "split_detected": False,
            },
            "backtest_settings": {},
            "optimization_settings": {},
            "shards": [
                {
                    "index": index,
                    "label": f"saved-{index}",
                    "timeframe": "1Min",
                    "group": index + 1,
                    "family_ids": [f"family-{index}"],
                }
                for index in range(12)
            ],
        }

    def test_12_of_12_checkpoint_skips_all_shard_recomputation(self):
        job = self._saved_sdot_job()
        plan = self._saved_sdot_plan()

        class SavedArtifacts:
            def read_json_gz(self, path):
                if path == distributed_stock_finder.plan_path("dist-sdot"):
                    return deepcopy(plan)
                raise FileNotFoundError(path)

            def exists(self, path):
                return path.startswith(distributed_stock_finder.run_root("dist-sdot"))

        with tempfile.TemporaryDirectory() as directory, patch.object(
            distributed_stock_finder,
            "_claim_stock_finder_job",
            return_value=(deepcopy(job), {"research_queue": [deepcopy(job)]}),
        ), patch.object(
            distributed_stock_finder,
            "PrivateRunArtifactStore",
            return_value=SavedArtifacts(),
        ), patch.object(
            distributed_stock_finder,
            "_restore_parent_distribution",
        ) as restore, patch.object(
            distributed_stock_finder,
            "build_market",
            side_effect=AssertionError("history/shards must not be recomputed"),
        ):
            previous = os.getcwd()
            os.chdir(directory)
            try:
                result = distributed_stock_finder.command_prepare("rq-sdot")
                metadata = json.loads(Path("distributed_meta.json").read_text())
            finally:
                os.chdir(previous)

        self.assertEqual(result, 0)
        self.assertEqual(metadata["run_id"], "dist-sdot")
        self.assertTrue(metadata["resumed"])
        self.assertFalse(metadata["needs_shards"])
        self.assertEqual(metadata["pending_shard_count"], 0)
        restore.assert_called_once()
        self.assertEqual(restore.call_args.args[2], set(range(12)))

    def test_legacy_pre_integrity_plan_is_not_resumed(self):
        job = self._saved_sdot_job()
        plan = self._saved_sdot_plan()
        plan["version"] = 1
        plan.pop("market_data_integrity", None)

        class SavedArtifacts:
            def read_json_gz(self, path):
                return deepcopy(plan)

            def exists(self, path):
                return True

        recovered = distributed_stock_finder._resumable_plan_for_job(
            SavedArtifacts(),
            job,
        )
        self.assertIsNone(recovered)

    def test_direct_aggregate_rejects_legacy_pre_integrity_plan(self):
        plan = self._saved_sdot_plan()
        plan["version"] = 1
        plan.pop("market_data_integrity", None)

        class SavedArtifacts:
            def read_json_gz(self, path):
                return deepcopy(plan)

        with patch.object(
            distributed_stock_finder,
            "PrivateRunArtifactStore",
            return_value=SavedArtifacts(),
        ):
            with self.assertRaisesRegex(Exception, "predates the current market-data integrity"):
                distributed_stock_finder.command_aggregate("dist-sdot")

    def test_missing_saved_plan_refuses_to_discard_completed_shards(self):
        job = self._saved_sdot_job()

        class MissingArtifacts:
            def read_json_gz(self, path):
                raise FileNotFoundError(path)

        with self.assertRaisesRegex(Exception, "Refusing to start over"):
            distributed_stock_finder._resumable_plan_for_job(
                MissingArtifacts(),
                job,
            )

    def test_terminal_12_of_12_job_gets_bounded_finalization_retry(self):
        job = self._saved_sdot_job(status="failed")
        job["attempts"] = 2
        library = {"research_queue": [deepcopy(job)]}
        plan = self._saved_sdot_plan()

        class SavedArtifacts:
            def read_json_gz(self, path):
                return deepcopy(plan)

            def exists(self, path):
                return True

        def mutate(mutator, **kwargs):
            updated = mutator(deepcopy(library))
            if updated is not None:
                library.clear()
                library.update(updated)
            return deepcopy(library)

        with patch.object(
            distributed_stock_finder,
            "read_remote_library",
            return_value=deepcopy(library),
        ), patch.object(
            distributed_stock_finder,
            "mutate_remote_library",
            side_effect=mutate,
        ):
            recovered = distributed_stock_finder._requeue_completed_finder_for_finalization(
                SavedArtifacts()
            )

        self.assertEqual(recovered, "rq-sdot")
        saved = library["research_queue"][0]
        self.assertEqual(saved["status"], "retry")
        self.assertEqual(saved["max_attempts"], 3)
        self.assertEqual(saved["payload"]["distributed_finalization_recoveries"], 1)

    def test_saved_12_shards_finalize_and_mark_job_complete(self):
        plan = self._saved_sdot_plan()
        job = self._saved_sdot_job(status="running")
        library = {"research_queue": [deepcopy(job)], "strategies": []}

        class SavedArtifacts:
            def __init__(self):
                self.deleted = []

            def read_json_gz(self, path):
                if path == distributed_stock_finder.plan_path("dist-sdot"):
                    return deepcopy(plan)
                for index in range(12):
                    if path == distributed_stock_finder.shard_path("dist-sdot", index):
                        return {
                            "version": distributed_stock_finder.DISTRIBUTED_SHARD_VERSION,
                            "run_id": "dist-sdot",
                            "index": index,
                            "timeframe": "1Min",
                            "report": {"distributed_elapsed_seconds": 1.0},
                        }
                raise FileNotFoundError(path)

            def delete(self, path):
                self.deleted.append(path)

        artifacts = SavedArtifacts()
        progress_events = []

        def complete(*args, **kwargs):
            kwargs["progress"](970, 1000, "Parameter stability: saved shards")
            return {
                "generated_at": "2026-08-28T23:00:00+00:00",
                "symbol": "SDOT",
                "profile": {"name": "Very Deep"},
                "optimization": {"winner": {}},
                "configuration_history": [],
                "unique_configurations_tested": 38076,
            }

        def mutate(mutator, **kwargs):
            updated = mutator(deepcopy(library))
            library.clear()
            library.update(updated)
            return deepcopy(library)

        with patch.object(
            distributed_stock_finder,
            "PrivateRunArtifactStore",
            return_value=artifacts,
        ), patch.object(
            distributed_stock_finder,
            "_update_parent_cloud_progress",
            side_effect=lambda *args, **kwargs: progress_events.append(kwargs),
        ), patch.object(
            distributed_stock_finder,
            "combine_strategy_family_reports",
            return_value={},
        ), patch.object(
            distributed_stock_finder,
            "combine_stock_timeframe_reports",
            return_value={},
        ), patch.object(
            distributed_stock_finder,
            "complete_stock_strategy_finder_from_optimization",
            side_effect=complete,
        ), patch.object(
            distributed_stock_finder,
            "mutate_remote_library",
            side_effect=mutate,
        ):
            result = distributed_stock_finder.command_aggregate("dist-sdot")

        self.assertEqual(result, 0)
        saved = library["research_queue"][0]
        self.assertEqual(saved["status"], "complete")
        self.assertEqual(saved["result_ref"], "distributed-finder:SDOT:Very Deep:2026-08-28T23:00:00+00:00")
        self.assertTrue(any(event.get("stage") == "parameter_stability" for event in progress_events))
        self.assertEqual(len(artifacts.deleted), 13)


if __name__ == "__main__":
    unittest.main()
