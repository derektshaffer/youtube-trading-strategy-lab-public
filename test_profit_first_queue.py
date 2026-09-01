"""Tests for automatic Profit First validation candidate selection."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import profit_first_queue as queue


def strategy(strategy_id: str, score: float = 90.0) -> dict:
    return {"id": strategy_id, "name": strategy_id, "source_type": "research"}


def run(
    strategy_id: str,
    *,
    generated_at: str,
    version: int,
    validation_pnl: float,
    holdout_pnl: float,
    stress_pnl: float,
    robustness: float = 70.0,
    walk_pct: float = 100.0,
) -> dict:
    def metrics(pnl: float) -> dict:
        return {
            "trade_count": 10 if pnl != 0 else 0,
            "net_pnl": pnl,
        }

    return {
        "strategy_id": strategy_id,
        "generated_at": generated_at,
        "autonomous": True,
        "validation_method_version": version,
        "validation_status": "research_only",
        "optimizer_status": "UNSTABLE",
        "validation_metrics": metrics(validation_pnl),
        "holdout_metrics": metrics(holdout_pnl),
        "stress_metrics": metrics(stress_pnl),
        "robustness": {"score": robustness},
        "walk_forward_summary": {"profitable_fold_pct": walk_pct},
    }


class ProfitFirstQueueTests(unittest.TestCase):
    def ready(self, item):
        return {
            "label": "ready_for_backtest",
            "score": 90.0,
            "semantic_critical_missing_requirements": [],
        }

    @patch.object(queue, "research_readiness")
    def test_legacy_positive_evidence_is_ranked_before_weaker_candidate(self, readiness):
        readiness.side_effect = self.ready
        library = {
            "strategies": [strategy("strong"), strategy("weak")],
            "validation_runs": [
                run(
                    "strong",
                    generated_at="2026-08-27T18:29:33Z",
                    version=0,
                    validation_pnl=30,
                    holdout_pnl=100,
                    stress_pnl=10,
                    robustness=95,
                    walk_pct=100,
                ),
                run(
                    "weak",
                    generated_at="2026-08-31T04:57:21Z",
                    version=3,
                    validation_pnl=38,
                    holdout_pnl=-45,
                    stress_pnl=31,
                    robustness=39,
                    walk_pct=50,
                ),
            ],
        }
        report = queue.profit_first_validation_candidates(library, maximum=2)
        self.assertEqual(report["phase"], "legacy_revalidation")
        self.assertEqual(
            [item["strategy_id"] for item in report["candidates"]],
            ["strong", "weak"],
        )

    @patch.object(queue, "research_readiness")
    def test_current_protocol_failure_is_not_requeued(self, readiness):
        readiness.side_effect = self.ready
        library = {
            "strategies": [strategy("method5")],
            "validation_runs": [
                run(
                    "method5",
                    generated_at="2026-08-31T07:24:05Z",
                    version=queue.CURRENT_AUTONOMOUS_VALIDATION_METHOD_VERSION,
                    validation_pnl=0,
                    holdout_pnl=0,
                    stress_pnl=0,
                )
            ],
        }
        report = queue.profit_first_validation_candidates(library)
        self.assertEqual(report["candidates"], [])
        self.assertEqual(report["current_protocol_skipped_count"], 1)

    @patch.object(queue, "research_readiness")
    def test_current_protocol_completion_advances_to_never_validated(self, readiness):
        readiness.side_effect = self.ready
        library = {
            "strategies": [strategy("legacy"), strategy("fresh")],
            "validation_runs": [
                run(
                    "legacy",
                    generated_at="2026-08-31T23:17:32Z",
                    version=queue.CURRENT_AUTONOMOUS_VALIDATION_METHOD_VERSION,
                    validation_pnl=-1,
                    holdout_pnl=-1,
                    stress_pnl=-1,
                )
            ],
        }
        report = queue.profit_first_validation_candidates(library)
        self.assertEqual(report["phase"], "never_validated")
        self.assertEqual(
            [item["strategy_id"] for item in report["candidates"]],
            ["fresh"],
        )
        self.assertEqual(report["current_protocol_skipped_count"], 1)

    @patch.object(queue, "research_readiness")
    def test_fidelity_blocked_strategy_is_excluded(self, readiness):
        readiness.return_value = {
            "label": "partially_modeled",
            "score": 55.0,
            "semantic_critical_missing_requirements": ["Historical float filter"],
        }
        library = {
            "strategies": [strategy("blocked")],
            "validation_runs": [
                run(
                    "blocked",
                    generated_at="2026-08-27T18:29:33Z",
                    version=0,
                    validation_pnl=30,
                    holdout_pnl=100,
                    stress_pnl=10,
                )
            ],
        }
        report = queue.profit_first_validation_candidates(library)
        self.assertEqual(report["candidates"], [])
        self.assertEqual(report["fidelity_blocked_count"], 1)
        self.assertEqual(
            report["fidelity_blocked"][0]["critical_missing"],
            ["Historical float filter"],
        )

    @patch.object(queue, "research_readiness")
    def test_never_validated_candidates_are_fallback_only(self, readiness):
        readiness.side_effect = self.ready
        library = {
            "strategies": [strategy("legacy"), strategy("fresh")],
            "validation_runs": [
                run(
                    "legacy",
                    generated_at="2026-08-27T18:29:33Z",
                    version=0,
                    validation_pnl=10,
                    holdout_pnl=10,
                    stress_pnl=10,
                )
            ],
        }
        report = queue.profit_first_validation_candidates(library)
        self.assertEqual(report["phase"], "legacy_revalidation")
        self.assertEqual(report["candidates"][0]["strategy_id"], "legacy")

    def test_profit_first_active_job_detection(self):
        library = {
            "research_queue": [
                {
                    "id": "job",
                    "type": "autonomous_validation",
                    "status": "queued",
                    "payload": {
                        "origin": "automatic_profit_first_validation",
                        "strategy_ids": ["abc"],
                    },
                }
            ]
        }
        self.assertEqual(
            queue.active_profit_first_validation_job(library)["id"],
            "job",
        )


if __name__ == "__main__":
    unittest.main()
