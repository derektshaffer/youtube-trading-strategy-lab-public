from pathlib import Path
import ast


def test_catalyst_workspace_wires_sec_and_news_evidence():
    source = Path("trading_intelligence_app.py").read_text(encoding="utf-8")
    ast.parse(source)

    start = source.index('elif module == "Catalyst Intelligence":')
    end = source.index('elif module == "Market Discovery":', start)
    block = source[start:end]

    assert "SecEdgarClient" in source
    assert 'setting("SEC_USER_AGENT")' in block
    assert "Load news + SEC catalyst intelligence" in block
    assert "rank_catalyst_evidence(" in block
    assert "Dilution / offering risk" in block
    assert "Open source evidence" in block


def test_stock_analyzer_shows_compact_catalyst_sec_context():
    source = Path("trading_intelligence_app.py").read_text(encoding="utf-8")
    ast.parse(source)

    start = source.index('elif module == "Stock Analyzer":')
    end = source.index('elif module == "Live / Paper":', start)
    block = source[start:end]

    assert "catalyst_evidence" in block
    assert "Checking SEC EDGAR" in block
    assert "Fresh dilution / offering evidence detected" in block
    assert 'with st.expander("Catalyst + SEC evidence"' in block
