import time
import unittest
from copy import deepcopy
from threading import Event, Lock

from strategy_lab_jobs import (
    strategy_lab_job_active,
    strategy_lab_job_outcome,
    strategy_lab_runs_in_background,
    submit_strategy_lab_job,
)
from strategy_lab_persistence import (
    load_latest_strategy_lab_checkpoint,
    save_strategy_lab_checkpoint,
)


class MemoryStore:
    def __init__(self):
        self.data = {"validation_runs": []}
        self.lock = Lock()

    def load(self):
        with self.lock:
            return deepcopy(self.data)

    def load_latest(self):
        return self.load()

    def save(self, data):
        with self.lock:
            self.data = deepcopy(data)
            return deepcopy(self.data)


def wait_for_outcome(run_id, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        outcome = strategy_lab_job_outcome(run_id)
        if outcome.get("status") != "running":
            return outcome
        time.sleep(0.01)
    raise AssertionError(f"background run {run_id} did not finish")


class StrategyLabJobTests(unittest.TestCase):
    def test_quick_stays_inline_and_very_deep_is_background(self):
        self.assertFalse(strategy_lab_runs_in_background(12))
        self.assertFalse(strategy_lab_runs_in_background(36))
        self.assertFalse(strategy_lab_runs_in_background(96))
        self.assertTrue(strategy_lab_runs_in_background(160))

    def test_very_deep_survives_caller_return_and_commits_result(self):
        store = MemoryStore()
        started = Event()
        release = Event()
        run_id = "background-completion"
        job = {
            "ticker": "SLS",
            "search_depth": 160,
            "started_at": "2026-09-01T12:00:00+00:00",
        }
        save_strategy_lab_checkpoint(
            store,
            run_id=run_id,
            status="running",
            ticker="SLS",
            job=job,
            progress=0.01,
            stage="queued",
            attempt=0,
        )

        def execute(job, **kwargs):
            started.set()
            kwargs["progress"](0.45, "optimization", "Testing variants")
            kwargs["optimizer_checkpoint"](
                {"completed_strategy_ids": ["family-one"], "rankings": []}
            )
            self.assertTrue(release.wait(2.0))
            return {"ticker": job["ticker"], "report": {"variants_tested": 160}}

        launched = submit_strategy_lab_job(
            run_id=run_id,
            job=job,
            checkpoint_store=store,
            market=object(),
            main_store=object(),
            execute=execute,
        )
        self.assertTrue(launched)
        self.assertTrue(started.wait(1.0))
        self.assertTrue(strategy_lab_job_active(run_id))
        self.assertFalse(
            submit_strategy_lab_job(
                run_id=run_id,
                job=job,
                checkpoint_store=store,
                market=object(),
                main_store=object(),
                execute=execute,
            )
        )

        release.set()
        outcome = wait_for_outcome(run_id)
        self.assertEqual(outcome["status"], "complete")
        latest = load_latest_strategy_lab_checkpoint(store)
        self.assertEqual(latest["status"], "complete")
        self.assertEqual(latest["result"]["report"]["variants_tested"], 160)
        self.assertNotIn("job", latest)
        self.assertNotIn("optimizer_state", latest)

    def test_process_restart_resumes_saved_optimizer_families(self):
        store = MemoryStore()
        run_id = "background-resume"
        job = {
            "ticker": "SLS",
            "search_depth": 160,
            "started_at": "2026-09-01T12:00:00+00:00",
        }
        saved_state = {
            "completed_strategy_ids": ["family-one"],
            "rankings": [{"source_strategy_id": "family-one"}],
        }
        save_strategy_lab_checkpoint(
            store,
            run_id=run_id,
            status="running",
            ticker="SLS",
            job=job,
            optimizer_state=saved_state,
            attempt=1,
            progress=0.50,
            stage="optimization",
        )
        observed = {}

        def execute(job, **kwargs):
            observed["resume"] = kwargs["optimizer_resume_state"]
            return {"ticker": job["ticker"], "report": {"variants_tested": 160}}

        self.assertTrue(
            submit_strategy_lab_job(
                run_id=run_id,
                job=job,
                checkpoint_store=store,
                market=object(),
                main_store=object(),
                execute=execute,
            )
        )
        outcome = wait_for_outcome(run_id)
        self.assertEqual(outcome["status"], "complete")
        self.assertEqual(observed["resume"], saved_state)

    def test_abort_is_visible_instead_of_leaving_running_marker(self):
        store = MemoryStore()
        run_id = "background-abort"
        job = {"ticker": "SLS", "search_depth": 160}
        save_strategy_lab_checkpoint(
            store,
            run_id=run_id,
            status="running",
            ticker="SLS",
            job=job,
        )

        def execute(_job, **_kwargs):
            raise SystemExit("simulated Streamlit runner stop")

        self.assertTrue(
            submit_strategy_lab_job(
                run_id=run_id,
                job=job,
                checkpoint_store=store,
                market=object(),
                main_store=object(),
                execute=execute,
            )
        )
        outcome = wait_for_outcome(run_id)
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("SystemExit", outcome["message"])
        latest = load_latest_strategy_lab_checkpoint(store)
        self.assertEqual(latest["status"], "failed")
        self.assertEqual(latest["stage"], "failed")
        self.assertIn("simulated Streamlit runner stop", latest["message"])

    def test_repeated_process_kills_fail_closed_after_three_attempts(self):
        store = MemoryStore()
        run_id = "background-attempt-limit"
        job = {"ticker": "SLS", "search_depth": 160}
        save_strategy_lab_checkpoint(
            store,
            run_id=run_id,
            status="running",
            ticker="SLS",
            job=job,
            attempt=3,
            progress=0.62,
            stage="optimization",
        )
        executed = Event()

        def execute(_job, **_kwargs):
            executed.set()
            return {"ticker": "SLS", "report": {"variants_tested": 160}}

        self.assertTrue(
            submit_strategy_lab_job(
                run_id=run_id,
                job=job,
                checkpoint_store=store,
                market=object(),
                main_store=object(),
                execute=execute,
            )
        )
        outcome = wait_for_outcome(run_id)
        self.assertEqual(outcome["status"], "failed")
        self.assertFalse(executed.is_set())
        self.assertIn("three separate process attempts", outcome["message"])
        latest = load_latest_strategy_lab_checkpoint(store)
        self.assertEqual(latest["status"], "failed")
        self.assertEqual(latest["stage"], "aborted")


if __name__ == "__main__":
    unittest.main()
