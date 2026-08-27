"""Tests for unified Trading Intelligence strategy semantics."""

import unittest

from trading_intelligence_core import (
    apply_compiler_suggestions,
    effective_strategy_for_live,
    effective_strategy_for_research,
    research_readiness,
)


class EffectiveStrategyTests(unittest.TestCase):
    def test_validated_strategy_uses_frozen_validated_rules(self):
        strategy = {
            "validation_status": "validated",
            "machine_rules": {"min_relative_volume": 2.0},
            "validated_rules": {"min_relative_volume": 4.0},
        }
        effective = effective_strategy_for_live(strategy)
        self.assertEqual(effective["machine_rules"]["min_relative_volume"], 4.0)
        self.assertTrue(effective["using_validated_rules"])
        self.assertEqual(strategy["machine_rules"]["min_relative_volume"], 2.0)

    def test_unvalidated_strategy_does_not_use_saved_validated_rules(self):
        strategy = {
            "validation_status": "research_only",
            "machine_rules": {"min_relative_volume": 2.0},
            "validated_rules": {"min_relative_volume": 4.0},
        }
        effective = effective_strategy_for_live(strategy)
        self.assertEqual(effective["machine_rules"]["min_relative_volume"], 2.0)
        self.assertFalse(effective["using_validated_rules"])

    def test_research_assumptions_fill_gaps_but_never_replace_source_rules(self):
        strategy = {
            "machine_rules": {"min_relative_volume": 2.0},
            "research_rule_overrides": {
                "min_relative_volume": 5.0,
                "max_vwap_distance_pct": 3.0,
            },
        }
        effective = effective_strategy_for_research(strategy)
        self.assertEqual(effective["machine_rules"]["min_relative_volume"], 2.0)
        self.assertEqual(effective["machine_rules"]["max_vwap_distance_pct"], 3.0)
        self.assertTrue(effective["using_research_overrides"])


    def test_ai_autopilot_never_overwrites_explicit_author_rule(self):
        strategy = {
            "source_type": "book_or_document",
            "machine_rules": {"min_relative_volume": 2.0},
            "evidence": [{"location": "p. 10", "description": "RVOL rule", "source_excerpt": "short"}],
        }
        compiled = {
            "model": "gemini-test",
            "generated_at": "2026-08-26T00:00:00Z",
            "summary": "test",
            "suggestions": [
                {
                    "target_rule": "min_relative_volume",
                    "parsed_value": 5.0,
                    "confidence": 99,
                    "source_requirement": "high RVOL",
                    "rationale": "proxy",
                },
                {
                    "target_rule": "max_vwap_distance_pct",
                    "parsed_value": 3.0,
                    "confidence": 90,
                    "source_requirement": "do not chase",
                    "rationale": "proxy",
                },
            ],
            "unmapped_requirements": [],
        }
        prepared = apply_compiler_suggestions(strategy, compiled, minimum_confidence=65)
        self.assertEqual(prepared["machine_rules"]["min_relative_volume"], 2.0)
        self.assertNotIn("min_relative_volume", prepared["research_rule_overrides"])
        self.assertEqual(prepared["research_rule_overrides"]["max_vwap_distance_pct"], 3.0)
        self.assertEqual(prepared["compiler_assumptions"][-1]["accepted_by"], "ai_autopilot")

    def test_ai_autopilot_skips_low_confidence_proxy(self):
        strategy = {"source_type": "book_or_document", "machine_rules": {}}
        compiled = {
            "model": "gemini-test",
            "generated_at": "2026-08-26T00:00:00Z",
            "suggestions": [
                {
                    "target_rule": "min_relative_volume",
                    "parsed_value": 2.0,
                    "confidence": 40,
                }
            ],
            "unmapped_requirements": [],
        }
        prepared = apply_compiler_suggestions(strategy, compiled, minimum_confidence=65)
        self.assertFalse(prepared.get("research_rule_overrides"))
        self.assertEqual(prepared["autopilot_preparation"]["skipped_low_confidence"], 1)

    def test_research_readiness_requires_objective_entry_rule(self):
        not_ready = research_readiness(
            {
                "source_type": "book_or_document",
                "machine_rules": {"stop_loss_pct": 2.0, "reward_risk": 2.0},
                "evidence": [{"location": "p. 4", "description": "risk", "source_excerpt": "short"}],
            }
        )
        self.assertEqual(not_ready["label"], "needs_translation")

        ready = research_readiness(
            {
                "source_type": "book_or_document",
                "machine_rules": {"min_relative_volume": 2.0, "stop_loss_pct": 2.0},
                "evidence": [{"location": "p. 4", "description": "entry", "source_excerpt": "short"}],
            }
        )
        self.assertEqual(ready["label"], "ready_for_backtest")


if __name__ == "__main__":
    unittest.main()
