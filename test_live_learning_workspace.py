from pathlib import Path


def test_live_shadow_learning_is_wired_to_market_discovery_and_stock_analyzer():
    source = Path("trading_intelligence_app.py").read_text(encoding="utf-8")

    assert "from live_learning import (" in source
    assert "def persist_live_learning_cycle(" in source
    assert 'source="market_discovery"' in source
    assert 'source="stock_analyzer"' in source
    assert '"til_live_learning_market_discovery_status"' in source
    assert '"til_live_learning_stock_analyzer_status"' in source
    assert "This does not affect live rankings." in source
    assert "This does not affect strategy ranking." in source


def test_live_learning_storage_is_bounded_and_research_only():
    source = Path("trading_intelligence_app.py").read_text(encoding="utf-8")

    assert "DEFAULT_MAX_OBSERVATIONS" in source
    assert "LIVE_LEARNING_MAX_NEW_PER_SCAN = 50" in source
    assert "LIVE_LEARNING_MAX_MATURATION_SYMBOLS = 25" in source
    assert '"affects_live_ranking": False' in source
    assert '"research_only": True' in source
