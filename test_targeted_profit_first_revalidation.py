"""Regression tests for targeted profit-first revalidation jobs."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import cloud_research_worker as worker


TARGET = "til-e375e878fadbe87a841b"


class TargetedProfitFirstRevalidationTests(unittest.TestCase):
    def test_targeted_validation_accepts_non_web_strategy_ids(self):
        data = {
            "strategies": [
                {
                    "id": TARGET,
                    "name": "Moving Average Trend Pullback (9 EMA)",
                    "source_type": "document",
                    "validation_status": "research_only",
                }
            ]
        }
        with patch.object(
            worker,
            "research_readiness",
            return_value={"label": "ready_for_backtest"},
        ):
            candidates, missing, blocked = worker._targeted_validation_strategies(
                data,
                [TARGET],
            )

        self.assertEqual([item["id"] for item in candidates], [TARGET])
        self.assertEqual(missing, [])
        self.assertEqual(blocked, [])

    def test_targeted_validation_reports_missing_and_blocked_ids(self):
        data = {
            "strategies": [
                {
                    "id": "blocked",
                    "source_type": "document",
                }
            ]
        }

        def readiness(item):
            return {"label": "needs_rule_work"}

        with patch.object(worker, "research_readiness", side_effect=readiness):
            candidates, missing, blocked = worker._targeted_validation_strategies(
                data,
                ["missing", "blocked"],
            )

        self.assertEqual(candidates, [])
        self.assertEqual(missing, ["missing"])
        self.assertEqual(blocked, ["blocked: needs_rule_work"])

    def test_cleanup_does_not_close_targeted_validation_job(self):
        data = {
            "strategies": [],
            "research_queue": [
                {
                    "id": "target-job",
                    "type": "autonomous_validation",
                    "status": "queued",
                    "payload": {"strategy_ids": [TARGET]},
                }
            ],
        }
        updated, closed = worker.close_empty_autonomous_validation_jobs(data)
        self.assertEqual(closed, 0)
        self.assertEqual(updated["research_queue"][0]["status"], "queued")

    def test_target_payload_accepts_comma_separated_ids(self):
        self.assertEqual(
            worker._target_strategy_ids(
                {"strategy_ids": f"{TARGET}, second, {TARGET}"}
            ),
            [TARGET, "second"],
        )


if __name__ == "__main__":
    unittest.main()
