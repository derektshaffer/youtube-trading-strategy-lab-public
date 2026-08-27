"""Tests for cross-stock generalization scoring."""

import unittest

from trading_universe_research import cross_stock_generalization
from youtube_strategy_engine import BacktestSettings


def bars(start_price: float, up: bool):
    rows = []
    price = start_price
    for minute in range(30):
        close = price + (0.2 if up else -0.15)
        rows.append(
            {
                "t": f"2026-08-20T14:{minute:02d}:00Z",
                "o": price,
                "h": max(price, close) + 0.05,
                "l": min(price, close) - 0.05,
                "c": close,
                "v": 10000 + minute * 100,
            }
        )
        price = close
    return rows


class UniverseResearchTests(unittest.TestCase):
    def test_report_preserves_symbol_count_and_frozen_rules(self):
        strategy = {
            "id": "s1",
            "name": "Simple",
            "direction": "long",
            "validation_status": "validated",
            "machine_rules": {"stop_loss_pct": 2.0, "reward_risk": 2.0},
            "validated_rules": {
                "stop_loss_pct": 1.5,
                "reward_risk": 2.0,
                "minimum_green_bars": 1,
            },
        }
        report = cross_stock_generalization(
            {"AAA": bars(10, True), "BBB": bars(20, False)},
            strategy,
            BacktestSettings(starting_cash=10000),
        )
        self.assertEqual(report["symbols_tested"], 2)
        self.assertTrue(report["using_validated_rules"])
        self.assertIn("score", report["summary"])
        self.assertEqual(len(report["results"]), 2)


if __name__ == "__main__":
    unittest.main()
