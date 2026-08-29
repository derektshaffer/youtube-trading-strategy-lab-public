"""Tests for Strategy DNA and cross-source synthesis."""

import unittest

from trading_strategy_dna import (
    build_candidate_blueprints,
    build_canonical_family_strategies,
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


class CanonicalFamilyManagerTests(unittest.TestCase):
    def _strategy(self, strategy_id, source_id, *, rvol=5.0, breakout=10):
        return {
            "id": strategy_id,
            "name": f"Momentum breakout {strategy_id}",
            "category": "Momentum Breakout",
            "direction": "long",
            "source_id": source_id,
            "source_title": f"Source {source_id}",
            "summary": "Momentum breakout above VWAP after a pullback with strong relative volume.",
            "entry_conditions": ["Buy the breakout after the pullback."],
            "machine_rules": {
                "min_relative_volume": rvol,
                "above_vwap": True,
                "breakout_lookback_bars": breakout,
            },
            "validation_status": "unvalidated",
        }

    def test_similar_source_strategies_collapse_into_one_canonical_family(self):
        source = [
            self._strategy("a", "book-1", rvol=4.0),
            self._strategy("b", "book-2", rvol=7.0),
        ]
        canonical, families = build_canonical_family_strategies(source)

        self.assertEqual(len(families), 1)
        self.assertEqual(len(canonical), 1)
        family = canonical[0]
        self.assertEqual(family["source_type"], "canonical_family")
        self.assertEqual(family["raw_strategy_count"], 2)
        self.assertEqual(family["supporting_source_count"], 2)
        self.assertEqual(set(family["candidate_rule_options"]["min_relative_volume"]), {4.0, 7.0})
        self.assertEqual(set(family["source_strategy_ids"]), {"a", "b"})

    def test_stock_specific_optimized_copy_does_not_create_or_expand_family(self):
        original = self._strategy("a", "book-1", rvol=4.0)
        optimized = {
            **self._strategy("a-sdot", "book-1", rvol=9.0),
            "optimized_for_symbol": "SDOT",
            "parent_strategy_id": "a",
        }
        canonical, families = build_canonical_family_strategies([original, optimized])

        self.assertEqual(len(families), 1)
        self.assertEqual(canonical[0]["raw_strategy_count"], 1)
        self.assertEqual(canonical[0]["source_strategy_ids"], ["a"])

    def test_same_family_blueprint_preserves_validation_when_more_identical_evidence_arrives(self):
        first = self._strategy("a", "book-1", rvol=5.0)
        initial, _ = build_canonical_family_strategies([first])
        saved = dict(initial[0])
        saved["validation_status"] = "validated"
        saved["validated_rules"] = {"min_relative_volume": 5.0, "above_vwap": True}

        second = self._strategy("b", "book-2", rvol=5.0)
        refreshed, _ = build_canonical_family_strategies(
            [first, second],
            existing=[saved],
        )

        self.assertEqual(len(refreshed), 1)
        self.assertEqual(refreshed[0]["validation_status"], "validated")
        self.assertEqual(refreshed[0]["validated_rules"]["min_relative_volume"], 5.0)

    def test_new_rule_option_supersedes_old_family_validation(self):
        first = self._strategy("a", "book-1", rvol=5.0)
        initial, _ = build_canonical_family_strategies([first])
        saved = dict(initial[0])
        saved["validation_status"] = "validated"
        saved["validated_rules"] = {"min_relative_volume": 5.0, "above_vwap": True}

        second = self._strategy("b", "book-2", rvol=8.0)
        refreshed, _ = build_canonical_family_strategies(
            [first, second],
            existing=[saved],
        )

        self.assertEqual(refreshed[0]["validation_status"], "unvalidated")
        self.assertTrue(refreshed[0].get("previous_family_validation_superseded"))

    def test_same_entry_family_preserves_dynamic_exit_variations_for_optimizer(self):
        first = self._strategy("trail-a", "book-1", rvol=5.0)
        second = self._strategy("trail-b", "book-2", rvol=5.0)
        first["exit_conditions"] = ["Trail the runner with a 3% trailing stop."]
        second["exit_conditions"] = ["Trail the runner with a 6% trailing stop."]
        first["machine_rules"]["trailing_stop_pct"] = 3.0
        second["machine_rules"]["trailing_stop_pct"] = 6.0

        canonical, families = build_canonical_family_strategies([first, second])
        self.assertEqual(len(families), 1)
        family = canonical[0]
        self.assertEqual(
            set(family["candidate_rule_options"]["trailing_stop_pct"]),
            {3.0, 6.0},
        )

        variants = generate_strategy_variants(
            family,
            BacktestSettings(),
            maximum=30,
        )
        dynamic_variants = [
            item for item in variants
            if item.get("trailing_stop_pct") in {3.0, 6.0}
        ]
        self.assertTrue(dynamic_variants)
        self.assertTrue(
            all(item.get("reward_risk") is None for item in dynamic_variants),
            "A source-supported trailing exit must not inherit an invented fixed reward target.",
        )

    def test_optional_family_rule_can_be_tested_as_not_required(self):
        first = self._strategy("a", "book-1", rvol=5.0)
        second = self._strategy("b", "book-2", rvol=5.0)
        second["machine_rules"].pop("above_vwap")

        canonical, _ = build_canonical_family_strategies([first, second])
        family = canonical[0]
        self.assertIn(None, family["candidate_rule_options"]["above_vwap"])

        variants = generate_strategy_variants(
            family,
            BacktestSettings(),
            maximum=20,
        )
        self.assertTrue(any(item.get("above_vwap") is None for item in variants))


    def test_long_and_short_variations_do_not_collapse_into_same_family(self):
        long_setup = self._strategy("long-a", "book-1", rvol=5.0)
        short_setup = self._strategy("short-a", "book-2", rvol=5.0)
        short_setup["direction"] = "short"

        canonical, families = build_canonical_family_strategies([long_setup, short_setup])

        self.assertEqual(len(families), 2)
        self.assertEqual(len(canonical), 2)
        self.assertEqual({item["direction"] for item in canonical}, {"long", "short"})

    def test_avwap_reclaim_does_not_absorb_handoff_or_ipo_mechanism(self):
        reclaim = {
            **self._strategy("reclaim", "book-1"),
            "name": "Rising AVWAP Pullback / Support Reclaim",
            "summary": "Buy a pullback when price reclaims a rising Anchored VWAP.",
            "entry_conditions": ["Price pulls back and crosses back above the AVWAP."],
            "machine_rules": {"above_vwap": True, "vwap_reclaim": True},
        }
        handoff = {
            **self._strategy("handoff", "book-1"),
            "name": "AVWAP Handoff (Uptrend Layering)",
            "summary": "Anchor a new AVWAP at the higher-low handoff point and buy the pullback.",
            "entry_conditions": ["Use the new AVWAP handoff after trend acceleration."],
            "machine_rules": {"above_vwap": True},
        }
        ipo = {
            **self._strategy("ipo", "book-1"),
            "name": "IPO Day-One AVWAP",
            "summary": "On the first trading day of an IPO anchor AVWAP to the second minute.",
            "entry_conditions": ["First day of an IPO; buy the bounce from second-minute AVWAP."],
            "machine_rules": {"above_vwap": True},
        }

        canonical, families = build_canonical_family_strategies([reclaim, handoff, ipo])

        self.assertEqual(len(families), 3)
        self.assertEqual(len(canonical), 3)
        names = {item["name"] for item in canonical}
        self.assertTrue(any("Reclaim" in name for name in names))
        self.assertTrue(any("Handoff" in name for name in names))
        self.assertTrue(any("IPO Day-One" in name for name in names))


    def test_special_avwap_contexts_do_not_disappear_into_generic_reclaim_family(self):
        generic = {
            **self._strategy("generic", "book-1"),
            "name": "AVWAP Cross Purchase",
            "summary": "Buy when price crosses back above Anchored VWAP after a dip.",
            "entry_conditions": ["Cross back above AVWAP."],
            "machine_rules": {"above_vwap": True, "vwap_reclaim": True},
        }
        squeeze = {
            **self._strategy("squeeze", "book-1"),
            "name": "Structural Short Squeeze",
            "summary": "Buy a heavily shorted stock when it reclaims AVWAP and shorts are trapped.",
            "entry_conditions": ["Short Interest Ratio is elevated and price reclaims AVWAP."],
            "machine_rules": {"above_vwap": True, "vwap_reclaim": True},
        }
        day_two = {
            **self._strategy("day-two", "book-1"),
            "name": "Multi-Day AVWAP Day Two Follow-Through",
            "summary": "On Day 2 of an AVWAP momentum campaign buy the current VWAP reclaim.",
            "entry_conditions": ["Day 2 holds prior AVWAP and reclaims current VWAP."],
            "machine_rules": {"above_vwap": True, "vwap_reclaim": True},
        }

        canonical, families = build_canonical_family_strategies([generic, squeeze, day_two])

        self.assertEqual(len(families), 3)
        self.assertEqual(len(canonical), 3)


    def test_generic_momentum_squeeze_language_is_not_mislabeled_as_avwap_pinch(self):
        strategy = {
            **self._strategy("micro", "video-1"),
            "name": "Micro Pullback / Bull Flag Pattern",
            "summary": "A strong stock forms a brief momentum compression, then a micro pullback before continuation.",
            "entry_conditions": ["Enter on the first candle making a new high after the pullback."],
            "indicators": ["VWAP", "9 EMA"],
            "machine_rules": {"above_vwap": True, "fast_ema_period": 9},
        }

        dna = infer_strategy_dna(strategy)

        self.assertNotIn("Compression / pinch", dna["structure"])


if __name__ == "__main__":
    unittest.main()
