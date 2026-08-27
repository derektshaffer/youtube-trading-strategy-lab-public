"""Tests for Strategy DNA and cross-source synthesis."""

import unittest

from trading_strategy_dna import (
    build_candidate_blueprints,
    build_concept_graph,
    build_strategy_families,
    compile_candidate_blueprint,
    infer_strategy_dna,
)
from youtube_strategy_engine import BacktestSettings, generate_strategy_variants


class StrategyDnaTests(unittest.TestCase):
    def test_infers_reusable_dna_from_machine_rules_and_text(self):
        strategy = {
            "name": "First Pullback",
            "category": "Momentum",
            "summary": "Trade a low-float stock with fresh news after the first pullback.",
            "entry_conditions": ["Enter on a break of the pullback high above VWAP."],
            "machine_rules": {
                "min_relative_volume": 5.0,
                "above_vwap": True,
                "catalyst_required": True,
                "stop_loss_pct": 2.0,
                "reward_risk": 2.0,
            },
        }
        dna = infer_strategy_dna(strategy)
        self.assertIn("Low-float stocks", dna["universe"])
        self.assertIn("News catalyst", dna["catalyst"])
        self.assertIn("High relative volume", dna["momentum"])
        self.assertIn("Pullback", dna["structure"])
        self.assertIn("Above VWAP", dna["structure"])
        self.assertIn("Percentage stop", dna["risk"])
        self.assertIn("R-multiple target", dna["exit"])


class CrossSourceGraphTests(unittest.TestCase):
    def _strategy(self, strategy_id, source_id, source_title, *, validated=False, rvol=5.0):
        return {
            "id": strategy_id,
            "name": f"Breakout {strategy_id}",
            "category": "Momentum",
            "direction": "long",
            "source_id": source_id,
            "source_title": source_title,
            "summary": "Momentum breakout above VWAP with strong relative volume.",
            "entry_conditions": ["Buy the breakout."],
            "machine_rules": {
                "min_relative_volume": rvol,
                "above_vwap": True,
                "breakout_lookback_bars": 10,
            },
            "validation_status": "validated" if validated else "unvalidated",
            "last_autonomous_research": {"global_score": 82.0} if validated else {},
            "evidence": [{"location": "p. 10", "description": "setup", "source_excerpt": "short"}],
        }

    def test_independent_source_count_does_not_double_count_same_book(self):
        strategies = [
            self._strategy("a", "book-1", "Book One"),
            self._strategy("b", "book-1", "Book One"),
            self._strategy("c", "book-2", "Book Two", validated=True),
        ]
        graph = build_concept_graph(strategies)
        breakout = next(item for item in graph if item["concept"] == "Breakout")
        self.assertEqual(breakout["independent_source_count"], 2)
        self.assertEqual(breakout["strategy_count"], 3)
        self.assertEqual(breakout["validated_source_count"], 1)

    def test_cross_source_family_generates_research_candidate_and_surfaces_rule_conflict(self):
        strategies = [
            self._strategy("a", "book-1", "Book One", rvol=5.0),
            self._strategy("b", "book-2", "Book Two", validated=True, rvol=8.0),
        ]
        families = build_strategy_families(strategies)
        cross_source = next(
            family for family in families if family["independent_source_count"] == 2
        )
        candidates = build_candidate_blueprints([cross_source], min_sources=2)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["status"], "hypothesis_only")
        self.assertIn("min_relative_volume", candidate["conflicting_explicit_rules"])
        self.assertNotIn("min_relative_volume", candidate["consistent_explicit_rules"])
        self.assertEqual(candidate["supporting_source_count"], 2)
        self.assertTrue(candidate["backtest_supported"])

    def test_blueprint_compiler_uses_exact_source_seed_and_keeps_all_options(self):
        strategies = [
            self._strategy("a", "book-1", "Book One", rvol=5.0),
            self._strategy("b", "book-2", "Book Two", validated=True, rvol=8.0),
        ]
        family = next(
            family
            for family in build_strategy_families(strategies)
            if family["independent_source_count"] == 2
        )
        blueprint = build_candidate_blueprints([family], min_sources=2)[0]
        compiled = compile_candidate_blueprint(blueprint)
        self.assertEqual(compiled["source_type"], "cross_source_synthesis")
        self.assertIn(
            compiled["research_rule_overrides"]["min_relative_volume"],
            {5.0, 8.0},
        )
        self.assertEqual(
            set(compiled["candidate_rule_options"]["min_relative_volume"]),
            {5.0, 8.0},
        )
        self.assertFalse(compiled["approved"])
        self.assertEqual(compiled["validation_status"], "unvalidated")

    def test_synthetic_candidate_is_not_counted_as_independent_source(self):
        base = [
            self._strategy("a", "book-1", "Book One"),
            self._strategy("b", "book-2", "Book Two"),
        ]
        family = next(
            family
            for family in build_strategy_families(base)
            if family["independent_source_count"] == 2
        )
        synthetic = compile_candidate_blueprint(
            build_candidate_blueprints([family], min_sources=2)[0]
        )
        graph = build_concept_graph([*base, synthetic])
        breakout = next(item for item in graph if item["concept"] == "Breakout")
        self.assertEqual(breakout["independent_source_count"], 2)

    def test_family_id_is_stable_when_input_order_changes(self):
        first = self._strategy("a", "book-1", "Book One")
        second = self._strategy("b", "book-2", "Book Two")
        family_one = next(
            family
            for family in build_strategy_families([first, second])
            if family["independent_source_count"] == 2
        )
        family_two = next(
            family
            for family in build_strategy_families([second, first])
            if family["independent_source_count"] == 2
        )
        self.assertEqual(family_one["id"], family_two["id"])

    def test_family_id_stays_stable_when_new_source_joins(self):
        first = self._strategy("a", "book-1", "Book One")
        second = self._strategy("b", "book-2", "Book Two")
        third = self._strategy("c", "book-3", "Book Three")
        original = next(
            family
            for family in build_strategy_families([first, second])
            if family["independent_source_count"] == 2
        )
        expanded = next(
            family
            for family in build_strategy_families([first, second, third])
            if family["independent_source_count"] == 3
        )
        self.assertEqual(original["id"], expanded["id"])

    def test_optimizer_tests_exact_cross_source_values(self):
        strategy = {
            "id": "synth-test",
            "name": "Synthesized breakout",
            "direction": "long",
            "machine_rules": {
                "min_relative_volume": 5.0,
                "above_vwap": True,
                "stop_loss_pct": 2.0,
                "reward_risk": 2.0,
            },
            "candidate_rule_options": {
                "min_relative_volume": [5.0, 7.3],
            },
        }
        variants = generate_strategy_variants(
            strategy,
            BacktestSettings(),
            maximum=12,
        )
        self.assertTrue(
            any(item.get("min_relative_volume") == 7.3 for item in variants)
        )


if __name__ == "__main__":
    unittest.main()
