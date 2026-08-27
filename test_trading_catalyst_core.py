"""Tests for point-in-time catalyst enrichment."""

import unittest

from trading_catalyst_core import enrich_bars_with_point_in_time_catalysts
from youtube_strategy_engine import evaluate_signal, normalize_machine_rules


class CatalystPointInTimeTests(unittest.TestCase):
    def test_future_news_never_appears_on_earlier_bar(self):
        rows = [
            {"t": "2026-08-20T13:59:00Z", "o": 10, "h": 10.2, "l": 9.9, "c": 10.1, "v": 1000},
            {"t": "2026-08-20T14:00:00Z", "o": 10.1, "h": 10.4, "l": 10.0, "c": 10.3, "v": 2000},
        ]
        articles = [
            {
                "created_at": "2026-08-20T14:00:00Z",
                "headline": "Company receives FDA approval for new therapy",
                "summary": "",
                "symbols": ["TEST"],
            }
        ]
        enriched, summary = enrich_bars_with_point_in_time_catalysts(rows, articles)
        self.assertFalse(enriched[0]["has_catalyst"])
        self.assertTrue(enriched[1]["has_catalyst"])
        self.assertEqual(summary["specific_catalysts"], 1)

    def test_catalyst_required_rule_is_enforced_when_data_available(self):
        rules = normalize_machine_rules({"catalyst_required": True})
        no_catalyst = {
            "close": 10.0,
            "clock_minute": 600,
            "catalyst_data_available": True,
            "has_catalyst": False,
        }
        catalyst = {**no_catalyst, "has_catalyst": True}
        self.assertFalse(evaluate_signal(no_catalyst, rules))
        self.assertTrue(evaluate_signal(catalyst, rules))

    def test_legacy_rows_without_catalyst_enrichment_keep_old_behavior(self):
        rules = normalize_machine_rules({"catalyst_required": True})
        legacy = {"close": 10.0, "clock_minute": 600}
        self.assertTrue(evaluate_signal(legacy, rules))


if __name__ == "__main__":
    unittest.main()
