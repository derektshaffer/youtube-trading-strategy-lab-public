import unittest
from copy import deepcopy
import tempfile

from strategy_lab_persistence import (
    MAX_STRATEGY_LAB_CHECKPOINTS,
    STRATEGY_LAB_CLOUD_CONFLICT_MARKER,
    load_latest_strategy_lab_checkpoint,
    merge_strategy_lab_checkpoint_libraries,
    save_strategy_lab_checkpoint,
)
from youtube_strategy_engine import AppError, StrategyStore


class MemoryStore:
    def __init__(self):
        self.data = {"validation_runs": []}

    def load_latest(self):
        return deepcopy(self.data)

    def save(self, data):
        self.data = deepcopy(data)
        return deepcopy(self.data)


def checkpoint(
    run_id,
    *,
    status="running",
    saved_at="2026-09-02T16:00:00+00:00",
    progress=0.0,
    result=None,
    job=None,
):
    item = {
        "id": run_id,
        "record_type": "strategy_lab_checkpoint",
        "status": status,
        "ticker": "SLS",
        "saved_at": saved_at,
        "progress": progress,
    }
    if result is not None:
        item["result"] = result
    if job is not None:
        item["job"] = job
    return item

class StrategyLabPersistenceTests(unittest.TestCase):
    def test_merge_preserves_distinct_local_and_cloud_runs_newest_first(self):
        local = {
            "validation_runs": [
                checkpoint(
                    "local-run",
                    saved_at="2026-09-03T01:40:00+00:00",
                    progress=0.1,
                    job={"search_depth": 160},
                )
            ]
        }
        remote = {
            "validation_runs": [
                checkpoint(
                    "cloud-run",
                    status="complete",
                    saved_at="2026-09-02T16:22:00+00:00",
                    progress=1.0,
                    result={"ticker": "SLS", "report": {"variants_tested": 160}},
                )
            ]
        }

        merged = merge_strategy_lab_checkpoint_libraries(local, remote)

        self.assertEqual(
            [item["id"] for item in merged["validation_runs"]],
            ["local-run", "cloud-run"],
        )
        self.assertEqual(merged["validation_runs"][1]["status"], "complete")
        self.assertTrue(merged["validation_runs"][1]["result"])

    def test_merge_never_regresses_completed_run_to_stale_running_state(self):
        local = {
            "validation_runs": [
                checkpoint(
                    "same-run",
                    saved_at="2026-09-03T01:45:00+00:00",
                    progress=0.9,
                    job={"search_depth": 160},
                )
            ]
        }
        remote = {
            "validation_runs": [
                checkpoint(
                    "same-run",
                    status="complete",
                    saved_at="2026-09-03T01:44:00+00:00",
                    progress=1.0,
                    result={"ticker": "SLS", "report": {"variants_tested": 160}},
                )
            ]
        }

        merged = merge_strategy_lab_checkpoint_libraries(local, remote)

        self.assertEqual(len(merged["validation_runs"]), 1)
        self.assertEqual(merged["validation_runs"][0]["status"], "complete")
        self.assertNotIn("job", merged["validation_runs"][0])

    def test_merge_keeps_furthest_progress_for_same_running_run(self):
        local = {
            "validation_runs": [
                checkpoint(
                    "same-run",
                    saved_at="2026-09-03T01:40:00+00:00",
                    progress=0.65,
                    job={"search_depth": 160},
                )
            ]
        }
        remote = {
            "validation_runs": [
                checkpoint(
                    "same-run",
                    saved_at="2026-09-03T01:41:00+00:00",
                    progress=0.25,
                    job={"search_depth": 160},
                )
            ]
        }

        merged = merge_strategy_lab_checkpoint_libraries(local, remote)

        self.assertEqual(merged["validation_runs"][0]["progress"], 0.65)

    def test_load_recovers_known_conflict_by_backing_up_then_saving_merge(self):
        class ConflictStore:
            def __init__(self):
                self.local = {
                    "validation_runs": [
                        checkpoint(
                            "local-run",
                            saved_at="2026-09-03T01:40:00+00:00",
                            progress=0.1,
                            job={"search_depth": 160},
                        )
                    ]
                }
                self.remote = {
                    "validation_runs": [
                        checkpoint(
                            "cloud-run",
                            status="complete",
                            saved_at="2026-09-02T16:22:00+00:00",
                            progress=1.0,
                            result={"ticker": "SLS", "report": {"variants_tested": 160}},
                        )
                    ]
                }
                self.calls = []

            def load_latest(self):
                raise AppError(STRATEGY_LAB_CLOUD_CONFLICT_MARKER + " Neither copy was overwritten.")

            def load(self):
                self.calls.append("load-local")
                return deepcopy(self.local)

            def restore_cloud_backup(self):
                self.calls.append("restore-with-backup")
                return deepcopy(self.remote)

            def save(self, data):
                self.calls.append("save-merged")
                self.remote = deepcopy(data)
                return deepcopy(data)

        store = ConflictStore()

        latest = load_latest_strategy_lab_checkpoint(store)

        self.assertEqual(latest["id"], "local-run")
        self.assertEqual(
            store.calls,
            ["load-local", "restore-with-backup", "save-merged"],
        )
        self.assertTrue(store.checkpoint_conflict_recovered)
        self.assertEqual(
            {item["id"] for item in store.remote["validation_runs"]},
            {"local-run", "cloud-run"},
        )

    def test_load_does_not_recover_an_unrecognized_cloud_error(self):
        class BrokenStore:
            def load_latest(self):
                raise AppError("Authentication failed.")

        with self.assertRaisesRegex(AppError, "Authentication failed"):
            load_latest_strategy_lab_checkpoint(BrokenStore())

    def test_real_store_recovery_preserves_local_backup_and_uses_cloud_cas(self):
        class FakeCloud:
            repository = "owner/private-backups"
            path = "trading-intelligence-lab/strategy_lab_latest.json"

            def __init__(self):
                self.remote = {
                    "version": 2,
                    "validation_runs": [
                        checkpoint(
                            "cloud-run",
                            status="complete",
                            saved_at="2026-09-02T16:22:00+00:00",
                            progress=1.0,
                            result={"ticker": "SLS", "report": {"variants_tested": 160}},
                        )
                    ],
                    "updated_at": "2026-09-02T16:22:30+00:00",
                }
                self.expected_updates = []

            def read_library(self):
                return {"library": deepcopy(self.remote), "sha": "a" * 40}

            def save_library(self, data, *, previous_updated_at=None, force_write=False):
                self.expected_updates.append(previous_updated_at)
                if previous_updated_at != self.remote.get("updated_at"):
                    raise AppError("GitHub branch moved before the checkpoint update.")
                self.remote = deepcopy(data)
                return {"library": deepcopy(self.remote), "sha": "b" * 40}

        with tempfile.TemporaryDirectory() as directory:
            cloud = FakeCloud()
            store = StrategyStore(directory, cloud_backup=cloud)
            store._write_local(
                {
                    "version": 2,
                    "validation_runs": [
                        checkpoint(
                            "local-run",
                            saved_at="2026-09-03T01:40:00+00:00",
                            progress=0.1,
                            job={"search_depth": 160},
                        )
                    ],
                    "updated_at": "2026-09-03T01:40:30+00:00",
                },
                make_backup=False,
            )
            store._record_cloud_status(
                synced_updated_at="2026-09-02T15:00:00+00:00",
                last_synced_at="2026-09-02T15:00:00+00:00",
            )

            latest = load_latest_strategy_lab_checkpoint(store)

            self.assertEqual(latest["id"], "local-run")
            self.assertTrue(store.checkpoint_conflict_recovered)
            self.assertEqual(
                {item["id"] for item in cloud.remote["validation_runs"]},
                {"local-run", "cloud-run"},
            )
            self.assertEqual(
                cloud.expected_updates,
                ["2026-09-02T16:22:30+00:00"],
            )
            self.assertGreaterEqual(
                len(list(store.backups_directory.glob("strategy_*.json"))),
                1,
            )

    def test_running_checkpoint_is_replaced_by_completed_result(self):
        store = MemoryStore()
        save_strategy_lab_checkpoint(
            store,
            run_id="run-1",
            status="running",
            ticker="sls",
            message="Optimization is running.",
        )
        save_strategy_lab_checkpoint(
            store,
            run_id="run-1",
            status="complete",
            ticker="sls",
            message="Optimization + validation complete.",
            result={"ticker": "SLS", "report": {"variants_tested": 160}},
        )

        self.assertEqual(len(store.data["validation_runs"]), 1)
        latest = load_latest_strategy_lab_checkpoint(store)
        self.assertEqual(latest["status"], "complete")
        self.assertEqual(latest["ticker"], "SLS")
        self.assertEqual(latest["result"]["report"]["variants_tested"], 160)

    def test_running_checkpoint_retains_resumable_job_and_optimizer_state(self):
        store = MemoryStore()
        save_strategy_lab_checkpoint(
            store,
            run_id="run-deep",
            status="running",
            ticker="sls",
            message="Queued.",
            progress=0.01,
            stage="queued",
            job={"search_depth": 160, "candidates": [{"id": "one"}]},
            attempt=1,
        )
        save_strategy_lab_checkpoint(
            store,
            run_id="run-deep",
            status="running",
            ticker="sls",
            message="One family saved.",
            progress=0.52,
            stage="optimization",
            optimizer_state={"completed_strategy_ids": ["one"]},
            attempt=1,
        )

        latest = load_latest_strategy_lab_checkpoint(store)
        self.assertEqual(latest["job"]["search_depth"], 160)
        self.assertEqual(latest["optimizer_state"]["completed_strategy_ids"], ["one"])
        self.assertEqual(latest["progress"], 0.52)
        self.assertEqual(latest["stage"], "optimization")

    def test_terminal_checkpoint_drops_restart_payload(self):
        store = MemoryStore()
        save_strategy_lab_checkpoint(
            store,
            run_id="run-deep",
            status="running",
            ticker="sls",
            job={"search_depth": 160},
            optimizer_state={"completed_strategy_ids": ["one"]},
        )
        save_strategy_lab_checkpoint(
            store,
            run_id="run-deep",
            status="complete",
            ticker="sls",
            result={"ticker": "SLS", "report": {"variants_tested": 160}},
        )

        latest = load_latest_strategy_lab_checkpoint(store)
        self.assertNotIn("job", latest)
        self.assertNotIn("optimizer_state", latest)
        self.assertEqual(latest["progress"], 1.0)
        self.assertEqual(latest["stage"], "complete")

    def test_stale_running_save_cannot_replace_completed_result(self):
        store = MemoryStore()
        save_strategy_lab_checkpoint(
            store,
            run_id="run-deep",
            status="complete",
            ticker="sls",
            result={"ticker": "SLS", "report": {"variants_tested": 160}},
        )

        returned = save_strategy_lab_checkpoint(
            store,
            run_id="run-deep",
            status="running",
            ticker="sls",
            progress=0.8,
            stage="optimization",
            job={"search_depth": 160},
        )

        self.assertEqual(returned["status"], "complete")
        self.assertEqual(store.data["validation_runs"][0]["status"], "complete")
        self.assertTrue(store.data["validation_runs"][0]["result"])

    def test_stale_running_save_cannot_move_progress_backward(self):
        store = MemoryStore()
        save_strategy_lab_checkpoint(
            store,
            run_id="run-deep",
            status="running",
            ticker="sls",
            progress=0.65,
            stage="optimization",
            job={"search_depth": 160},
        )

        returned = save_strategy_lab_checkpoint(
            store,
            run_id="run-deep",
            status="running",
            ticker="sls",
            progress=0.25,
            stage="history",
            job={"search_depth": 160},
        )

        self.assertEqual(returned["progress"], 0.65)
        self.assertEqual(store.data["validation_runs"][0]["stage"], "optimization")

    def test_checkpoint_history_is_bounded(self):
        store = MemoryStore()
        for index in range(MAX_STRATEGY_LAB_CHECKPOINTS + 3):
            save_strategy_lab_checkpoint(
                store,
                run_id=f"run-{index}",
                status="failed",
                ticker="SLS",
                message=f"failure {index}",
            )

        self.assertEqual(
            len(store.data["validation_runs"]),
            MAX_STRATEGY_LAB_CHECKPOINTS,
        )
        self.assertEqual(store.data["validation_runs"][0]["id"], "run-7")

    def test_completed_checkpoint_requires_result(self):
        store = MemoryStore()
        with self.assertRaises(AppError):
            save_strategy_lab_checkpoint(
                store,
                run_id="run-1",
                status="complete",
                ticker="SLS",
            )

    def test_unknown_status_is_rejected(self):
        store = MemoryStore()
        with self.assertRaises(AppError):
            save_strategy_lab_checkpoint(
                store,
                run_id="run-1",
                status="mystery",
                ticker="SLS",
            )


if __name__ == "__main__":
    unittest.main()
