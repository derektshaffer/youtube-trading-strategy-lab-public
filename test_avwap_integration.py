from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

import youtube_strategy_engine as engine
from trading_intelligence_core import (
    effective_strategy_for_research,
    paper_execution_fidelity,
    strategy_integrity_report,
    upgrade_native_strategy_rules,
)


def bars() -> list[dict]:
    start = datetime(2026, 8, 20, 13, 30, tzinfo=timezone.utc)
    closes = [10.00, 10.05, 10.10, 10.18, 10.12, 10.22, 10.32, 10.40]
    result = []
    for index, close in enumerate(closes):
        result.append(
            {
                "t": (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
                "o": close - 0.03,
                "h": close + 0.06,
                "l": close - 0.06,
                "c": close,
                "v": 25_000 + index * 1_000,
            }
        )
    return result


class AvwapIntegrationTests(unittest.TestCase):
    def test_rule_schema_normalizes_avwap_fields(self):
        rules = engine.normalize_machine_rules(
            {
                "avwap_anchor_mode": "higher_low_handoff",
                "avwap_pivot_confirm_bars": "2",
                "require_price_above_avwap": "true",
                "avwap_reclaim": True,
                "require_avwap_rising": True,
                "require_avwap_pullback": True,
                "avwap_pullback_tolerance_pct": "0.6",
                "max_avwap_distance_pct": "4",
                "stop_below_avwap": True,
                "stop_avwap_buffer_pct": "0.3",
                "exit_below_avwap": True,
            }
        )
        self.assertEqual(rules["avwap_anchor_mode"], "higher_low_handoff")
        self.assertEqual(rules["avwap_pivot_confirm_bars"], 2)
        self.assertTrue(rules["require_price_above_avwap"])
        self.assertTrue(rules["avwap_reclaim"])
        self.assertEqual(rules["avwap_pullback_tolerance_pct"], 0.6)
        self.assertTrue(rules["exit_below_avwap"])

    def test_session_open_avwap_is_available_to_backtest(self):
        strategy = {
            "id": "avwap-session",
            "name": "Session AVWAP research probe",
            "direction": "long",
            "machine_rules": {
                "min_price": 1.0,
                "avwap_anchor_mode": "session_open",
                "require_price_above_avwap": True,
                "stop_loss_pct": 5.0,
                "reward_risk": 1.0,
            },
        }
        frame = engine.add_indicators(engine.bars_to_frame(bars()), strategy)
        self.assertTrue(frame["avwap"].notna().any())
        self.assertTrue(frame["avwap_anchor_active"].fillna(False).any())
        report = engine.run_backtest(
            bars(),
            strategy,
            "TEST",
            engine.BacktestSettings(
                spread_bps=0,
                slippage_bps=0,
                allow_extended_hours=False,
            ),
        )
        self.assertIn("metrics", report)

    def test_saved_rising_avwap_pullback_is_upgraded_without_reupload(self):
        strategy = {
            "id": "rising-avwap",
            "name": "Rising AVWAP Pullback / Support Reclaim",
            "direction": "long",
            "summary": "Anchor VWAP from the confirmed swing low and use the rising AVWAP as support.",
            "entry_conditions": [
                "Wait for a pullback into anchored VWAP and buy the reclaim back above AVWAP."
            ],
            "risk_rules": [],
            "exit_conditions": [],
            "unresolved_rules": [],
            "machine_rules": {},
            "evidence": [{"location": "chapter", "description": "setup", "source_excerpt": "short"}],
        }
        upgraded = upgrade_native_strategy_rules(strategy)
        effective = effective_strategy_for_research(upgraded)
        rules = effective["machine_rules"]
        self.assertEqual(rules["avwap_anchor_mode"], "swing_low")
        self.assertEqual(rules["avwap_pivot_confirm_bars"], 2)
        self.assertTrue(rules["require_avwap_rising"])
        self.assertTrue(rules["require_avwap_pullback"])
        self.assertTrue(rules["avwap_reclaim"])
        self.assertIsNotNone(rules["avwap_pullback_tolerance_pct"])
        audit = strategy_integrity_report(upgraded)
        self.assertNotIn("Anchored VWAP structure", audit["critical_missing_requirements"])

    def test_multi_anchor_pinch_remains_blocked(self):
        strategy = {
            "id": "pinch",
            "name": "AVWAP Pinch Strategy",
            "direction": "long",
            "summary": "Trade compression between multiple anchored VWAP lines before the pinch resolves.",
            "entry_conditions": ["Enter on the breakout from the AVWAP compression pinch."],
            "machine_rules": {
                "avwap_anchor_mode": "swing_low",
                "avwap_pivot_confirm_bars": 2,
                "require_price_above_avwap": True,
            },
            "evidence": [{"location": "chapter", "description": "setup", "source_excerpt": "short"}],
        }
        audit = strategy_integrity_report(strategy)
        self.assertEqual(audit["status"], "blocked")
        self.assertIn(
            "Multi-anchor AVWAP compression structure",
            audit["critical_missing_requirements"],
        )

    def test_paper_auto_stays_blocked_until_live_avwap_execution_is_equivalent(self):
        strategy = {
            "id": "avwap-paper",
            "direction": "long",
            "machine_rules": {
                "avwap_anchor_mode": "swing_low",
                "avwap_pivot_confirm_bars": 2,
                "require_price_above_avwap": True,
                "stop_loss_pct": 3.0,
                "reward_risk": 2.0,
            },
        }
        fidelity = paper_execution_fidelity(strategy)
        self.assertEqual(fidelity["status"], "blocked")
        self.assertIn("Anchored VWAP", " ".join(fidelity["unsupported_management"]))


if __name__ == "__main__":
    unittest.main()
