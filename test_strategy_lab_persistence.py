import unittest
from copy import deepcopy

from strategy_lab_persistence import (
    MAX_STRATEGY_LAB_CHECKPOINTS,
    load_latest_strategy_lab_checkpoint,
    save_strategy_lab_checkpoint,
)
from youtube_strategy_engine import AppError


class MemoryStore:
    def __init__(self):
        self.data = {"validation_runs": []}

    def load_latest(self):
        return deepcopy(self.data)

    def save(self, data):
        self.data = deepcopy(data)
        return deepcopy(self.data)


class StrategyLabPersistenceTests(unittest.TestCase):
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
