from pathlib import Path
import ast


def test_pattern_validation_workspace_is_wired_without_main_navigation_clutter():
    source = Path("trading_intelligence_app.py").read_text(encoding="utf-8")
    ast.parse(source)

    assert '"Pattern Validation"' in source
    assert '"Pattern Validation": "10A. Pattern Validation"' in source
    assert 'elif module == "Pattern Validation":' in source
    assert "run_detector_scorecards(" in source
    assert "Advanced / Research Details" in source

    primary_start = source.index("primary_navigation = [")
    primary_end = source.index("]", primary_start)
    primary_block = source[primary_start:primary_end]
    assert "Pattern Validation" not in primary_block


def test_pattern_validation_ui_is_bounded_for_interactive_use():
    source = Path("trading_intelligence_app.py").read_text(encoding="utf-8")
    pattern_start = source.index('elif module == "Pattern Validation":')
    pattern_end = source.index('elif module == "Catalyst Intelligence":', pattern_start)
    block = source[pattern_start:pattern_end]

    assert "][:5]" in block
    assert '"History (days)"' in block
    assert 'key="til_pattern_validation_days"' in block
    assert 'timeframe="1Min"' in block
    assert "does not automatically become a trading rule." in block
