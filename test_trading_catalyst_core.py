"""Tests for point-in-time catalyst enrichment."""

import unittest

from trading_catalyst_core import (
    catalyst_freshness,
    classify_catalyst,
    enrich_bars_with_point_in_time_catalysts,
    rank_catalyst_evidence,
)
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



    def test_freshness_is_explicit_and_time_bounded(self):
        from datetime import datetime, timezone

        as_of = datetime(2026, 8, 29, 18, 0, tzinfo=timezone.utc)
        fresh = catalyst_freshness("2026-08-29T17:00:00Z", as_of=as_of)
        stale = catalyst_freshness("2026-08-20T17:00:00Z", as_of=as_of)
        self.assertEqual(fresh["freshness"], "breaking")
        self.assertEqual(stale["freshness"], "stale")
        self.assertGreater(fresh["freshness_weight"], stale["freshness_weight"])

    def test_exact_repeat_headline_is_downweighted_as_non_novel(self):
        from datetime import datetime, timezone

        as_of = datetime(2026, 8, 29, 18, 0, tzinfo=timezone.utc)
        newer = classify_catalyst(
            {
                "created_at": "2026-08-29T17:00:00Z",
                "headline": "Company receives FDA approval",
                "summary": "",
            }
        )
        older = classify_catalyst(
            {
                "created_at": "2026-08-29T16:00:00Z",
                "headline": "Company receives FDA approval",
                "summary": "",
            }
        )
        ranked = rank_catalyst_evidence([newer, older], as_of=as_of)
        repeats = [item for item in ranked if item["novelty"] == "repeat"]
        self.assertEqual(len(repeats), 1)
        self.assertEqual(repeats[0]["novelty_weight"], 0.5)

    def test_sec_evidence_gets_primary_source_weight(self):
        from datetime import datetime, timezone

        as_of = datetime(2026, 8, 29, 18, 0, tzinfo=timezone.utc)
        sec_item = {
            "published_at": "2026-08-29T17:00:00Z",
            "headline": "Registered direct offering",
            "category": "offering prospectus / dilution risk",
            "score": -8.0,
            "is_specific_catalyst": True,
            "is_negative": True,
            "is_positive": False,
            "is_dilution_risk": True,
            "evidence_type": "sec_filing",
            "fingerprint": "sec offering",
        }
        news_item = {
            **sec_item,
            "evidence_type": "news",
            "fingerprint": "news offering",
        }
        ranked = rank_catalyst_evidence([news_item], [sec_item], as_of=as_of)
        sec_ranked = next(item for item in ranked if item["evidence_type"] == "sec_filing")
        news_ranked = next(item for item in ranked if item["evidence_type"] == "news")
        self.assertGreater(abs(sec_ranked["effective_score"]), abs(news_ranked["effective_score"]))



    def test_generic_earnings_headline_is_specific_but_not_directional(self):
        item = classify_catalyst(
            {
                "created_at": "2026-08-29T17:00:00Z",
                "headline": "Company reports quarterly results",
                "summary": "",
            }
        )
        self.assertTrue(item["is_specific_catalyst"])
        self.assertTrue(item["direction_requires_context"])
        self.assertEqual(item["score"], 0.0)
        self.assertFalse(item["is_positive"])
        self.assertFalse(item["is_negative"])

    def test_merger_headline_does_not_assume_bullish_direction(self):
        item = classify_catalyst(
            {
                "created_at": "2026-08-29T17:00:00Z",
                "headline": "Company announces merger agreement",
                "summary": "",
            }
        )
        self.assertTrue(item["is_specific_catalyst"])
        self.assertEqual(item["category"], "merger / acquisition")
        self.assertEqual(item["score"], 0.0)

    def test_legacy_rows_without_catalyst_enrichment_keep_old_behavior(self):
        rules = normalize_machine_rules({"catalyst_required": True})
        legacy = {"close": 10.0, "clock_minute": 600}
        self.assertTrue(evaluate_signal(legacy, rules))


if __name__ == "__main__":
    unittest.main()
