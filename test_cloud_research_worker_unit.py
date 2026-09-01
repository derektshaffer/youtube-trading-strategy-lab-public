"""Focused regression tests for cloud research worker queue semantics."""

import unittest
from copy import deepcopy
from unittest.mock import patch

import cloud_research_worker as worker
from cloud_research_worker import (
    _pending_web_strategies,
    close_empty_autonomous_validation_jobs,
)


class FakeStore:
    def __init__(self, data):
        self.data = deepcopy(data)

    def load_latest(self):
        return deepcopy(self.data)

    def save(self, data):
        self.data = deepcopy(data)
        return deepcopy(data)


class LiveLearningOutboxTests(unittest.TestCase):
    def test_worker_merges_outbox_into_main_library_then_clears_processed_rows(self):
        observation = {
            "id": "obs-1",
            "symbol": "AAA",
            "observed_at": "2026-08-31T20:00:00+00:00",
            "outcome_status": "PENDING",
            "outcomes": {},
            "research_only": True,
        }
        main_store = FakeStore(
            {
                "strategies": [],
                "predictive_ml_runs": [],
                "research_system": {"live_learning_observations": []},
            }
        )
        outbox_store = FakeStore(
            {
                "strategies": [],
                "research_system": {"live_learning_observations": [observation]},
            }
        )

        with (
            patch.object(worker, "pending_symbols", return_value=[]),
            patch.object(worker, "build_shadow_model_monitor", return_value={"status": "COLLECTING"}),
            patch.object(
                worker,
                "build_model_registry",
                return_value={"status": "COLLECTING", "champion_model_id": None},
            ),
        ):
            summary = worker.drain_live_learning_outbox(main_store, outbox_store)

        main_rows = (
            main_store.data.get("research_system", {})
            .get("live_learning_observations", [])
        )
        outbox_rows = (
            outbox_store.data.get("research_system", {})
            .get("live_learning_observations", [])
        )
        self.assertEqual([item["id"] for item in main_rows], ["obs-1"])
        self.assertEqual(outbox_rows, [])
        self.assertEqual(summary["merged"], 1)
        self.assertEqual(summary["remaining"], 0)


class PendingValidationTests(unittest.TestCase):
    def _strategy(self, status, *, retryable=False, retry_after=None):
        return {
            "id": f"s-{status}-{retryable}",
            "name": "Test RVOL strategy",
            "direction": "long",
            "source_type": "autonomous_web_research",
            "validation_status": "research_only",
            "research_source_quality_score": 80,
            "confidence": 70,
            "machine_rules": {"min_relative_volume": 2.0},
            "unresolved_rules": [],
            "evidence": [
                {
                    "location": "research",
                    "description": "RVOL condition",
                    "source_excerpt": "Historical RVOL filter.",
                }
            ],
            "last_autonomous_research": {
                "validation_status": status,
                "retryable": retryable,
                "retry_after": retry_after,
            },
        }

    def test_terminal_nonretryable_failure_is_not_selected_again(self):
        data = {
            "strategies": [
                self._strategy("validation_failed"),
                self._strategy("insufficient_data"),
            ]
        }
        self.assertEqual(_pending_web_strategies(data), [])

    def test_transient_retryable_failure_can_be_selected_again(self):
        candidate = self._strategy("validation_failed", retryable=True)
        selected = _pending_web_strategies({"strategies": [candidate]})
        self.assertEqual([item["id"] for item in selected], [candidate["id"]])

    def test_transient_failure_waits_until_retry_window_opens(self):
        candidate = self._strategy(
            "validation_failed",
            retryable=True,
            retry_after="2099-01-01T00:00:00Z",
        )
        self.assertEqual(_pending_web_strategies({"strategies": [candidate]}), [])

    def test_transient_failure_is_eligible_after_retry_window(self):
        candidate = self._strategy(
            "validation_failed",
            retryable=True,
            retry_after="2000-01-01T00:00:00Z",
        )
        selected = _pending_web_strategies({"strategies": [candidate]})
        self.assertEqual([item["id"] for item in selected], [candidate["id"]])

    def test_stale_methodology_is_eligible_for_corrected_revalidation(self):
        candidate = self._strategy("stale_methodology")
        selected = _pending_web_strategies({"strategies": [candidate]})
        self.assertEqual([item["id"] for item in selected], [candidate["id"]])

    def test_non_testable_strategy_does_not_create_empty_validation_retry(self):
        candidate = self._strategy("stale_methodology")
        candidate["machine_rules"] = {"previous_day_high_breakout": False}
        selected = _pending_web_strategies({"strategies": [candidate]})
        self.assertEqual(selected, [])

    def test_existing_empty_validation_retry_is_closed_cleanly(self):
        candidate = self._strategy("stale_methodology")
        candidate["machine_rules"] = {"previous_day_high_breakout": False}
        data = {
            "strategies": [candidate],
            "research_queue": [
                {
                    "id": "retry-validation",
                    "type": "autonomous_validation",
                    "status": "retry",
                    "next_attempt_at": "2026-08-31T00:00:00Z",
                    "last_error": "No extracted strategies are machine-testable enough.",
                    "failure_step": "job_execution",
                }
            ],
        }
        updated, closed = close_empty_autonomous_validation_jobs(data)
        self.assertEqual(closed, 1)
        job = updated["research_queue"][0]
        self.assertEqual(job["status"], "complete")
        self.assertEqual(job["result_ref"], "no-pending-validation")
        self.assertIsNone(job["next_attempt_at"])
        self.assertIsNone(job["last_error"])


if __name__ == "__main__":
    unittest.main()
