from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from explosive_stock_core import (
    build_explosive_candidate,
    completed_daily_profile,
    completed_intraday_rows,
    forward_explosion_labels,
    rank_latent_daily_candidates,
    scan_explosive_candidates,
    score_explosive_profile,
    score_latent_daily_candidate,
)


def _daily_rows(count: int = 35, *, start_price: float = 5.0) -> list[dict]:
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    rows = []
    price = start_price
    for index in range(count):
        price *= 1.002
        rows.append(
            {
                "t": (start + timedelta(days=index)).isoformat(),
                "o": price * 0.99,
                "h": price * 1.02,
                "l": price * 0.98,
                "c": price,
                "v": 500_000 + index * 1_000,
            }
        )
    return rows


def _intraday_rows(now: datetime, count: int = 24) -> list[dict]:
    rows = []
    start = now - timedelta(minutes=count)
    for index in range(count):
        price = 5.0 + index * 0.01
        rows.append(
            {
                "t": (start + timedelta(minutes=index)).isoformat(),
                "o": price,
                "h": price + 0.03,
                "l": price - 0.02,
                "c": price + 0.01,
                "v": 10_000 + index * 500,
            }
        )
    return rows


class ExplosiveStockCoreTests(unittest.TestCase):
    def test_completed_intraday_rows_drops_current_forming_minute(self):
        now = datetime(2026, 8, 31, 14, 37, 45, tzinfo=timezone.utc)
        rows = [
            {
                "t": datetime(2026, 8, 31, 14, 36, 0, tzinfo=timezone.utc).isoformat(),
                "o": 1,
                "h": 1,
                "l": 1,
                "c": 1,
                "v": 1,
            },
            {
                "t": datetime(2026, 8, 31, 14, 37, 0, tzinfo=timezone.utc).isoformat(),
                "o": 1,
                "h": 1,
                "l": 1,
                "c": 1,
                "v": 1,
            },
        ]
        completed = completed_intraday_rows(rows, now=now)
        self.assertEqual(len(completed), 1)
        self.assertIn("14:36:00", completed[0]["t"])

    def test_forward_labels_are_future_only_and_separate_from_profile(self):
        rows = _daily_rows(6, start_price=10.0)
        rows[3]["c"] = 10.0
        rows[4]["h"] = 14.0
        rows[5]["h"] = 22.0

        profile_before_future = completed_daily_profile(rows[:4])
        labels = forward_explosion_labels(rows, 3, horizons=(1, 2), thresholds_pct=(30.0, 100.0))

        self.assertNotIn("hit_30pct_within_1d", profile_before_future)
        self.assertTrue(labels["hit_30pct_within_1d"])
        self.assertFalse(labels["hit_100pct_within_1d"])
        self.assertTrue(labels["hit_100pct_within_2d"])

    def test_score_is_explicitly_not_probability(self):
        scored = score_explosive_profile(
            {
                "relative_volume": 4.0,
                "day_change_pct": 12.0,
                "above_vwap": True,
                "distance_from_high_pct": 2.0,
                "spread_pct": 0.5,
                "dollar_volume": 8_000_000,
                "price": 5.0,
                "vwap_distance_pct": 3.0,
            },
            {
                "compression_ratio_5v20": 0.6,
                "previous_day_volume_ratio": 1.8,
                "largest_single_day_gain_pct": 35.0,
                "runner_days_20pct": 2,
                "runner_days_30pct": 1,
            },
            {
                "features": {
                    "volume_acceleration_ratio": 2.0,
                    "consolidation_then_expansion_up": True,
                    "uptrend_structure": True,
                    "base_range_atr_ratio": 2.0,
                }
            },
            {
                "fresh_specific_count": 1,
                "fresh_positive_count": 1,
                "strongest_positive_score": 7.0,
                "fresh_dilution_count": 0,
                "fresh_structural_risk_count": 0,
            },
        )
        self.assertFalse(scored["score_is_probability"])
        self.assertEqual(scored["validation_status"], "experimental_unvalidated")
        self.assertGreater(scored["profile_score"], 0)

    def test_dilution_remains_visible_as_separate_risk(self):
        clean = score_explosive_profile(
            {"spread_pct": 0.4, "dollar_volume": 10_000_000, "price": 4.0},
            {},
            {},
            {"fresh_dilution_count": 0, "fresh_structural_risk_count": 0},
        )
        diluted = score_explosive_profile(
            {"spread_pct": 0.4, "dollar_volume": 10_000_000, "price": 4.0},
            {},
            {},
            {"fresh_dilution_count": 1, "fresh_structural_risk_count": 1},
        )
        self.assertGreater(diluted["risk_score"], clean["risk_score"])
        self.assertTrue(any("dilution" in warning.lower() for warning in diluted["warnings"]))

    def test_latent_prescreen_score_is_not_probability(self):
        profile = completed_daily_profile(_daily_rows(35))
        candidate = score_latent_daily_candidate("TEST", profile)
        self.assertIsNotNone(candidate)
        self.assertFalse(candidate["score_is_probability"])
        self.assertEqual(candidate["validation_status"], "experimental_unvalidated")

    def test_latent_prescreen_rejects_insufficient_history(self):
        profile = completed_daily_profile(_daily_rows(10))
        self.assertIsNone(score_latent_daily_candidate("TEST", profile))

    def test_rank_latent_daily_candidates_is_bounded(self):
        universe = {
            f"T{index}": _daily_rows(35, start_price=2.0 + index * 0.1)
            for index in range(6)
        }
        ranked = rank_latent_daily_candidates(universe, top_n=3)
        self.assertLessEqual(len(ranked), 3)
        self.assertTrue(all(item.get("score_is_probability") is False for item in ranked))

    def test_candidate_does_not_fabricate_float_or_market_cap(self):
        now = datetime(2026, 8, 31, 18, 0, 30, tzinfo=timezone.utc)
        snapshot = {
            "latestTrade": {"p": 5.5, "t": now.isoformat()},
            "latestQuote": {"bp": 5.48, "ap": 5.52, "t": now.isoformat()},
            "dailyBar": {"c": 5.5, "h": 5.6, "v": 2_000_000, "vw": 5.2},
            "prevDailyBar": {"c": 5.0},
        }
        candidate = build_explosive_candidate(
            "TEST",
            snapshot,
            _daily_rows(),
            _intraday_rows(now),
            [],
            now=now,
        )
        self.assertIsNotNone(candidate)
        self.assertIsNone(candidate["structural_supply"]["float"])
        self.assertIsNone(candidate["structural_supply"]["market_cap"])
        self.assertIn("float", candidate["missing_data"])
        self.assertIn("market_cap", candidate["missing_data"])

    def test_scan_ranks_active_candidate_above_low_profile_candidate(self):
        now = datetime(2026, 8, 31, 18, 0, 30, tzinfo=timezone.utc)

        class FakeMarket:
            live_feed = "iex"

            def snapshots(self, symbols):
                return {
                    "HOT": {
                        "latestTrade": {"p": 6.0, "t": now.isoformat()},
                        "latestQuote": {"bp": 5.98, "ap": 6.02, "t": now.isoformat()},
                        "dailyBar": {"c": 6.0, "h": 6.05, "v": 5_000_000, "vw": 5.4},
                        "prevDailyBar": {"c": 5.0},
                    },
                    "COLD": {
                        "latestTrade": {"p": 5.0, "t": now.isoformat()},
                        "latestQuote": {"bp": 4.98, "ap": 5.02, "t": now.isoformat()},
                        "dailyBar": {"c": 5.0, "h": 5.1, "v": 100_000, "vw": 5.0},
                        "prevDailyBar": {"c": 5.0},
                    },
                }

            def bars(self, symbols, *, timeframe, **kwargs):
                if timeframe == "1Day":
                    result = {}
                    for symbol in symbols:
                        values = _daily_rows()
                        if symbol == "HOT":
                            values[-2]["c"] = values[-3]["c"] * 1.35
                            values[-1]["v"] = values[-2]["v"] * 2
                        result[symbol] = values
                    return result
                result = {}
                for symbol in symbols:
                    values = _intraday_rows(now)
                    if symbol == "HOT":
                        for index, row in enumerate(values):
                            row["v"] = 5_000 + index * 2_000
                    else:
                        for row in values:
                            row["v"] = 1_000
                    result[symbol] = values
                return result

            def news(self, symbols, hours):
                return {
                    "HOT": [
                        {
                            "headline": "Company awarded major contract",
                            "summary": "New commercial contract award announced.",
                            "created_at": (now - timedelta(hours=1)).isoformat(),
                            "symbols": ["HOT"],
                        }
                    ],
                    "COLD": [],
                }

        results = scan_explosive_candidates(FakeMarket(), ["HOT", "COLD"], now=now)
        self.assertEqual({item["symbol"] for item in results}, {"HOT", "COLD"})
        hot = next(item for item in results if item["symbol"] == "HOT")
        cold = next(item for item in results if item["symbol"] == "COLD")
        self.assertGreater(hot["profile_score"], cold["profile_score"])


if __name__ == "__main__":
    unittest.main()
