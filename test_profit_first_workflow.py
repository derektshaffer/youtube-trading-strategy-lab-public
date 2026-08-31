"""Regression guards for the Trading Lab's profit-first primary workflow."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent
APP_PATH = ROOT / "trading_intelligence_app.py"


class ProfitFirstWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_PATH.read_text(encoding="utf-8")
        ast.parse(cls.source)

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
            "Re-run strict validation on strongest candidate",
            '"validation_protocol": "strict_manual_v1"',
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
