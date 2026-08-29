from pathlib import Path
import ast


def test_pattern_validation_uses_explicit_trading_day_control():
    source = Path("trading_intelligence_app.py").read_text(encoding="utf-8")
    ast.parse(source)

    start = source.index('elif module == "Pattern Validation":')
    end = source.index('elif module == "Catalyst Intelligence":', start)
    block = source[start:end]

    assert '"Trading days"' in block
    assert '"History (days)"' not in block
    assert "session_limit=pattern_days" in block
    assert "Weekends and market holidays do not count" in block
    assert "calendar_lookback_days" in block
