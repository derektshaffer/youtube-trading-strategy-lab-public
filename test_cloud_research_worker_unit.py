"""Focused regression tests for cloud research worker queue semantics."""

import unittest

from cloud_research_worker import _pending_web_strategies


class PendingValidationTests(unittest.TestCase):
    def _strategy(self, status, *, retryable=False):
        return {
            "id": f"s-{status}-{retryable}",
            "source_type": "autonomous_web_research",
            "validation_status": "research_only",
            "research_source_quality_score": 80,
            "confidence": 70,
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


if __name__ == "__main__":
    unittest.main()
