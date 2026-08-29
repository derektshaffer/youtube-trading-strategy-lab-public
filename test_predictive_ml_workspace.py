from pathlib import Path


def _pattern_validation_block() -> str:
    source = Path("trading_intelligence_app.py").read_text(encoding="utf-8")
    start = source.index('elif module == "Pattern Validation":')
    end = source.index('elif module == "Catalyst Intelligence":', start)
    return source[start:end]


def test_predictive_ml_workspace_is_research_only_and_walk_forward():
    block = _pattern_validation_block()
    assert "### Predictive ML Research" in block
    assert "build_cross_stock_training_dataset" in block
    assert "walk_forward_logistic_baseline" in block
    assert 'embargo_sessions=1' in block
    assert 'test_sessions_per_fold=2' in block
    assert "does not change scanner rankings" in block
    assert "No model from this panel is used live." in block


def test_predictive_ml_workspace_is_bounded_for_interactive_use():
    block = _pattern_validation_block()
    assert 'options=[5, 15, 30]' in block
    assert 'max_pages=120' in block
    assert '[:5]' in block
    assert '"ML history (days)"' in block
