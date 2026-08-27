"""Tests for unified Trading Intelligence strategy semantics."""

import unittest

from trading_intelligence_core import effective_strategy_for_live, effective_strategy_for_research


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


if __name__ == "__main__":
    unittest.main()
