"""Tests for compact profit-first research-library audit reporting."""

from __future__ import annotations

import unittest

from research_library_audit import profit_first_validation_summary


def run(
    strategy_id: str,
    *,
    status: str = "research_only",
    validation_pnl: float = 0.0,
    holdout_pnl: float = 0.0,
    stress_pnl: float = 0.0,
    robustness: float = 0.0,
    generated_at: str = "2026-08-31T20:00:00Z",
) -> dict:
    def metrics(pnl: float) -> dict:
        return {"trade_count": 5 if pnl != 0 else 0, "net_pnl": pnl}

    return {
        "id": f"{strategy_id}:{generated_at}",
        "strategy_id": strategy_id,
        "strategy_name": strategy_id,
        "symbol": "TEST",
        "generated_at": generated_at,
        "validation_status": status,
        "optimizer_status": "PROMISING",
        "robustness": {"score": robustness, "label": "STRONG"},
        "validation_metrics": metrics(validation_pnl),
        "holdout_metrics": metrics(holdout_pnl),
        "stress_metrics": metrics(stress_pnl),
        "walk_forward_summary": {"profitable_fold_pct": 66.7},
        "parameter_stability": {"label": "MIXED", "positive_pct": 50.0},
        "evidence_verdict": {
            "code": "promising",
            "label": "PROMISING STOCK-SPECIFIC SETUP",
            "reason": "A stability gate still failed.",
        },
        "paper_execution_fidelity": {"status": "ready"},
        "historical_spread_audit": {"status": "OK"},
        "holdout_reuse_audit": {"status": "CLEAN"},
    }


class ResearchLibraryProfitFirstAuditTests(unittest.TestCase):
    def test_strict_edge_requires_validated_and_all_three_positive_periods(self):
        report = profit_first_validation_summary(
            [
                run(
                    "strict",
                    status="validated",
                    validation_pnl=10,
                    holdout_pnl=8,
                    stress_pnl=3,
                    robustness=78,
                ),
                run(
                    "negative-stress",
                    status="validated",
                    validation_pnl=10,
                    holdout_pnl=8,
                    stress_pnl=-1,
                    robustness=82,
                ),
                run(
                    "research-only",
                    validation_pnl=20,
                    holdout_pnl=20,
                    stress_pnl=20,
                    robustness=90,
                ),
            ]
        )
        self.assertEqual(report["strict_profit_edge_count"], 1)
        self.assertEqual(report["strict_profit_edges"][0]["strategy_id"], "strict")
        strict = report["strict_profit_edges"][0]
        self.assertEqual(strict["optimizer_status"], "PROMISING")
        self.assertEqual(strict["parameter_stability_positive_pct"], 50.0)
        self.assertEqual(strict["evidence_verdict_code"], "promising")
        self.assertEqual(strict["evidence_verdict_reason"], "A stability gate still failed.")
        self.assertEqual(strict["paper_execution_status"], "ready")
        self.assertEqual(strict["historical_spread_status"], "OK")
        self.assertEqual(strict["holdout_reuse_status"], "CLEAN")

    def test_latest_run_controls_each_strategy_status(self):
        report = profit_first_validation_summary(
            [
                run(
                    "edge",
                    status="validated",
                    validation_pnl=10,
                    holdout_pnl=10,
                    stress_pnl=10,
                    generated_at="2026-08-30T20:00:00Z",
                ),
                run(
                    "edge",
                    status="research_only",
                    validation_pnl=-1,
                    holdout_pnl=-1,
                    stress_pnl=-1,
                    generated_at="2026-08-31T20:00:00Z",
                ),
            ]
        )
        self.assertEqual(report["distinct_strategy_count"], 1)
        self.assertEqual(report["strict_profit_edge_count"], 0)


if __name__ == "__main__":
    unittest.main()
