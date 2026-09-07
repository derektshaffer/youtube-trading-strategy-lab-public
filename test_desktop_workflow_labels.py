"""Approved persistent workflow navigation and exact context contract."""
import pytest
pytest.importorskip("PySide6")
from test_desktop_web_workflow import app, window

EXPECTED_NAVIGATION = ["Find Stocks", "Analyze", "Strategy", "Validation", "Results", "Research"]


def test_sidebar_is_grouped_once_in_workflow_order(window, app, monkeypatch):
    from test_desktop_web_workflow import select, SID
    select(window)
    window.show()
    app.processEvents()
    workflow = window.workflow
    assert workflow.sidebar.isVisible() and workflow.header.isVisible()
    primary = workflow.navigation[:6]
    assert [button.text() for button, _ in primary] == EXPECTED_NAVIGATION
    assert [button.accessibleName() for button, _ in primary] == EXPECTED_NAVIGATION
    assert len({page for _, page in workflow.navigation}) == len(workflow.navigation)
    legacy_targets = {window.stack.widget(int(button.property("stack_index")) if button.property("stack_index") is not None else index)
                      for index, button in enumerate(window.nav_buttons)}
    assert {page for _, page in workflow.navigation} == legacy_targets
    assert not workflow.utilities.body.isVisibleTo(workflow.sidebar)
    workflow.utilities.toggle.click()
    assert workflow.utilities.body.isVisibleTo(workflow.sidebar)
    for button, page in workflow.navigation:
        assert button.isVisibleTo(workflow.sidebar) and button.isEnabled()
        button.click()
        app.processEvents()
        assert window.stack.currentWidget() is page
        assert button.property("active")
        assert sum(bool(b.property("active")) for b, _ in workflow.navigation) == 1
        assert workflow.context_ticker.text() == ("Research Library" if page is window.research_ml else "SPY")
        if page is window.research_ml:
            assert "not filtered" in workflow.context_strategy.text()
            assert workflow.ticker == "SPY" and workflow.strategy_id == SID
        else:
            assert SID in workflow.context_strategy.text()
        assert window.strategy_lab.strategy.currentData() == SID
    # Exercise the original Find -> Analyze handoff without fetching market data.
    analysis_requests = []
    monkeypatch.setattr(window.analysis, "emit_analysis", lambda: analysis_requests.append("SPY"))
    workflow.go(window.market_discovery)
    for destination in (window.analysis, window.finder, window.strategy_lab, window.results):
        assert workflow.next.isEnabled()
        workflow.next.click()
        app.processEvents()
        assert window.stack.currentWidget() is destination
        assert workflow.context_ticker.text() == "SPY"
        assert SID in workflow.context_strategy.text()
    assert analysis_requests == ["SPY"]
