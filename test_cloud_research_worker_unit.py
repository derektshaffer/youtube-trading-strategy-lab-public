"""Focused regression tests for cloud research worker queue semantics."""

import unittest

from cloud_research_worker import (
    _pending_web_strategies,
    close_empty_autonomous_validation_jobs,
)


class PendingValidationTests(unittest.TestCase):
    def _strategy(self, status, *, retryable=False):
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
