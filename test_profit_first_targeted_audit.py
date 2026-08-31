"""Tests for the read-only Profit First derivative audit."""

from __future__ import annotations

import copy
import unittest

from profit_first_targeted_audit import build_float_agnostic_derivative
from trading_intelligence_core import research_readiness


class ProfitFirstTargetedAuditTests(unittest.TestCase):
    def test_float_agnostic_variant_is_separate_and_preserves_source(self):
        source = {
            "id": "source-ema",
            "name": "Moving Average Trend Pullback (9 EMA)",
            "source_type": "book_or_document",
            "summary": "Trade close to the 9 EMA in low float stocks.",
            "stock_selection": ["Low float stocks", "Strong trend"],
            "entry_conditions": [
                "Trade close to the 9 EMA.",
                "Price above VWAP and the 20 EMA and 200 EMA.",
            ],
            "risk_rules": ["Put the stop slightly below the 9 EMA."],
            "machine_rules": {
                "above_vwap": True,
                "fast_ema_period": 9,
                "slow_ema_period": 20,
                "trend_ema_period": 200,
                "require_price_above_slow_ema": True,
                "require_price_above_trend_ema": True,
                "require_fast_ema_pullback": True,
                "stop_below_fast_ema": True,
            },
            "evidence": [{"source": "book"}, {"source": "book"}],
        }
        original = copy.deepcopy(source)

        variant = build_float_agnostic_derivative(source)

        self.assertEqual(source, original)
        self.assertNotEqual(variant["id"], source["id"])
        self.assertEqual(variant["derived_from_strategy_id"], source["id"])
        self.assertEqual(variant["source_type"], "derived_research_hypothesis")
        self.assertIn("Float-Agnostic Research Variant", variant["name"])
        self.assertFalse(
            any("float" in str(item).casefold() for item in variant["stock_selection"])
        )
        self.assertEqual(
            variant["research_rule_overrides"]["pullback_touch_tolerance_pct"],
            0.5,
        )
        self.assertEqual(
            variant["research_rule_overrides"]["stop_ema_buffer_pct"],
            0.25,
        )

    def test_variant_records_deliberate_changes(self):
        source = {
            "id": "source-ema",
            "name": "EMA setup",
            "source_type": "book_or_document",
            "stock_selection": ["low float"],
            "summary": "trade near the 9 EMA",
            "risk_rules": ["stop slightly below the 9 EMA"],
            "machine_rules": {
                "fast_ema_period": 9,
                "require_fast_ema_pullback": True,
                "stop_below_fast_ema": True,
            },
            "evidence": [{"source": "book"}],
        }
        variant = build_float_agnostic_derivative(source)
        changes = variant["derived_hypothesis_changes"]
        self.assertEqual(changes[0]["requirement"], "Historical float filter")
        self.assertEqual(changes[0]["change"], "excluded")
        self.assertEqual(changes[1]["change"], "research_assumption")
        self.assertEqual(changes[2]["change"], "research_assumption")


if __name__ == "__main__":
    unittest.main()
