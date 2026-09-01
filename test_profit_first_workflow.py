"""Regression guards for the Trading Lab's profit-first primary workflow."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent
APP_PATH = ROOT / "trading_intelligence_app.py"
WORKER_PATH = ROOT / "cloud_research_worker.py"


class ProfitFirstWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_PATH.read_text(encoding="utf-8")
        cls.worker_source = WORKER_PATH.read_text(encoding="utf-8")
        ast.parse(cls.source)
        ast.parse(cls.worker_source)

    def test_profit_first_is_the_start_here_workspace(self):
        self.assertIn('"Profit First": "0. Profit-First Edge"', self.source)
        self.assertIn('("START HERE", ["Profit First"])', self.source)
        self.assertIn('"title": "Profit-First Edge Finder"', self.source)
        self.assertIn('"★ Find a validated profitable edge"', self.source)

    def test_profit_first_requires_saved_validation_and_positive_unseen_periods(self):
        self.assertIn('validation_status == "validated"', self.source)
        self.assertIn("validation_positive", self.source)
        self.assertIn("holdout_positive", self.source)
        self.assertIn("stress_positive", self.source)
        self.assertIn('"strict_profit_edge": strict_profit_edge', self.source)
        self.assertIn("A current stock match by itself never qualifies a strategy here.", self.source)

    def test_profit_first_fails_closed_on_legacy_inconsistent_evidence(self):
        for marker in (
            '"UNSTABLE": 49.0',
            '"requires_revalidation": requires_revalidation',
            '"stored_robustness_score": stored_robustness_score',
            '"current_protocol": current_protocol',
            "Legacy/incomplete validation record.",
            "Top historical candidate needs revalidation",
            "Revalidate strongest candidate under current protocol",
            '"validation_protocol": "strict_manual_v1"',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.source)

    def test_profit_first_shows_automatic_validator_status(self):
        for marker in (
            "Automatic profit-first validator",
            "automatic_profit_first_validation",
            "Failures stay research-only",
            "does not recycle ",
            "current-protocol failures automatically",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.source)

    def test_profit_first_has_one_obvious_full_validation_action(self):
        for marker in (
            "FIND ME A PROFITABLE VALIDATED STRATEGY",
            "PROFITABLE STRATEGY SEARCH IN PROGRESS",
            "candidate discovery → optimization → adaptive walk-forward",
            "profitable-neighborhood test → untouched holdout → execution stress",
            "You do not need to open Strategy Lab or manually turn on walk-forward.",
            'workflow="continuous-trading-research.yml"',
            "Search expanded automatically.",
            "If they fail, the search continues",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.source)

    def test_profit_first_ui_and_worker_share_candidate_batch_decision(self):
        self.assertIn("profit_first_validation_batch(", self.source)
        self.assertIn("profit_first_validation_batch(", self.worker_source)
        self.assertIn(
            "CURRENT_AUTONOMOUS_VALIDATION_METHOD_VERSION",
            self.source,
        )

    def test_profit_first_rechecks_before_every_worker_slot(self):
        loop_source = self.worker_source.split(
            "for _ in range(jobs_per_run):",
            1,
        )[1]
        before_claim = loop_source.split(
            "data, job = claim_next_research_job(",
            1,
        )[0]
        self.assertIn(
            "refresh_automatic_profit_first_validation_job(",
            before_claim,
        )
        self.assertIn(
            "Re-evaluate Profit First before every worker slot",
            before_claim,
        )

    def test_profit_first_blocks_revalidation_until_strategy_is_faithfully_modeled(self):
        for marker in (
            "Revalidation is blocked by strategy fidelity.",
            "critical_missing_requirements",
            "Review fidelity gaps",
            "Open Rule Builder",
            "point-in-time float must remain a hard blocker",
            "A deliberately modified derivative hypothesis must be ",
            "tracked separately.",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.source)

    def test_profit_first_revalidation_uses_durable_cloud_validator(self):
        for marker in (
            "Revalidate strongest candidate under current protocol",
            '"origin": "profit_first_revalidation"',
            '"strategy_ids": [strongest_strategy_id]',
            '"autonomous_validation"',
            'priority=100',
            'workflow="continuous-trading-research.yml"',
            "Current-protocol revalidation",
            "holdout, stress, cross-stock, and walk-forward gates",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.source)

    def test_profit_first_does_not_hide_failure_to_find_an_edge(self):
        self.assertIn("No strategy currently clears the strict profit-first bar.", self.source)
        self.assertIn("Closest research candidates — still not validated profitable edges", self.source)
        self.assertIn("Continue AI strategy research", self.source)
        self.assertIn("Review all validation evidence", self.source)

    def test_validated_edge_routes_to_market_discovery(self):
        self.assertIn("Find stocks matching this validated edge", self.source)
        self.assertIn('st.session_state["til_market_discovery_strategy_id"] = selected_strategy_id', self.source)
        self.assertIn('navigate_to_workspace("Market Discovery", pending=True)', self.source)


if __name__ == "__main__":
    unittest.main()
