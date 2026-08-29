from pathlib import Path

path = Path("test_trading_intelligence_core.py")
text = path.read_text(encoding="utf-8")
old = '''    def test_legacy_validation_is_invalidated_when_defining_logic_was_never_modeled(self):
        strategy = {
            "id": "old-validated-scaleout",
            "name": "Old validated scale-out setup",
            "direction": "long",
            "validation_status": "validated",
            "optimization_status": "complete",
            "exit_conditions": ["Take partial profit, then scale out into strength."],
            "machine_rules": {
                "breakout_lookback_bars": 20,
                "stop_loss_pct": 3,
                "reward_risk": 2,
            },
            "validated_rules": {"breakout_lookback_bars": 20},
            "validated_backtest_settings": {"risk_per_trade_pct": 0.5},
            "validated_at": "2026-08-20T00:00:00Z",
            "evidence": [{"location": "p.3", "description": "management", "source_excerpt": "short"}],
        }
        upgraded = upgrade_native_strategy_rules(strategy)
        self.assertEqual(upgraded["validation_status"], "unvalidated")
        self.assertEqual(upgraded["optimization_status"], "not_run")
        self.assertNotIn("validated_rules", upgraded)
        audit = upgraded.get("previous_validation_invalidated_by_integrity_audit") or {}
        self.assertIn("Scale-out", " ".join(audit.get("missing_requirements") or []))
'''
new = '''    def test_legacy_validation_is_invalidated_when_defining_logic_was_never_modeled(self):
        strategy = {
            "id": "old-validated-tape",
            "name": "Old validated tape-confirmed breakout",
            "direction": "long",
            "validation_status": "validated",
            "optimization_status": "complete",
            "entry_conditions": ["Enter only when Level 2 and tape speed confirm the breakout."],
            "machine_rules": {
                "breakout_lookback_bars": 20,
                "stop_loss_pct": 3,
                "reward_risk": 2,
            },
            "validated_rules": {"breakout_lookback_bars": 20},
            "validated_backtest_settings": {"risk_per_trade_pct": 0.5},
            "validated_at": "2026-08-20T00:00:00Z",
            "evidence": [{"location": "video", "description": "entry confirmation", "source_excerpt": "short"}],
        }
        upgraded = upgrade_native_strategy_rules(strategy)
        self.assertEqual(upgraded["validation_status"], "unvalidated")
        self.assertEqual(upgraded["optimization_status"], "not_run")
        self.assertNotIn("validated_rules", upgraded)
        audit = upgraded.get("previous_validation_invalidated_by_integrity_audit") or {}
        self.assertIn("Level-2 / tape-reading confirmation", audit.get("missing_requirements") or [])
'''
if old not in text:
    raise RuntimeError("Stale scale-out regression block not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
