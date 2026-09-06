"""Presentation-only web workflow regressions; no provider or execution calls."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QTimer, Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QTableWidget, QLabel, QPushButton
from desktop.trading_intelligence.parity_window import MainWindow
from desktop.trading_intelligence.workflow_widgets import PageWheelRouter, page_scroll, WorkflowPageStack, Disclosure
from desktop.trading_intelligence.saved_validation_page import SavedValidationPage
from hybrid_runtime.saved_validation_reader import read_saved_validation
from test_readonly_validation_results import setup, evidence, SID, JID


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    monkeypatch.setattr(QTimer, "singleShot", lambda *a: None)
    monkeypatch.setattr("desktop.trading_intelligence.onboarding_window.configuration_status", lambda _: {})
    runtime = SimpleNamespace(data_dir=tmp_path, request_json=Mock(return_value={}), process=None)
    w = MainWindow(runtime, smoke=True)
    for timer in w.findChildren(QTimer):
        timer.stop()
    yield w
    w.close()


def select(w):
    w.strategy_lab.set_options({"strategies": [{"id": SID, "name": "Exact AVWAP"}], "faithful_count": 1})
    item = {"symbol": "SPY", "best_strategy_id": SID, "best_strategy_name": "Exact AVWAP",
            "validation_status": "unvalidated", "metrics": {"price": 770.1, "trade_timestamp": "2026-09-04T19:59:00Z"},
            "signal": {"checks": [{"label": "AVWAP", "status": "pass", "actual": 770.1, "required": "swing_high"}]}}
    w.market_discovery.render_results({"results": [item]})
    w.market_discovery.table.selectRow(0)
    w.workflow.refresh()


def test_selection_updates_context_without_execution(window):
    select(window)
    assert window.workflow.ticker == "SPY"
    assert window.workflow.strategy_id == SID
    assert window.strategy_lab.ticker.text() == "SPY"
    assert window.strategy_lab.strategy.currentData() == SID
    assert not window.strategy_lab.compare_all.isChecked()
    assert window.market_discovery.analyze.text() == "Analyze SPY"
    assert not window.runtime.request_json.called
    assert "not a stock-specific validation verdict" in window.workflow.discovery_detail.text()


@pytest.mark.parametrize("route", ["Find Stocks", "Analyze", "Strategy", "Validation", "Results", "Research"])
def test_navigation_preserves_exact_context_and_does_not_run(window, route):
    select(window)
    page = dict(window.workflow.routes)[route]
    window.workflow.go(page)
    assert window.stack.currentWidget() is page
    assert window.workflow.context_ticker.text() == "SPY"
    assert SID in window.workflow.context_strategy.text()
    assert window.strategy_lab.strategy.currentData() == SID
    assert not window.runtime.request_json.called


def test_active_cloud_context_is_not_overwritten(window):
    window.finder_job_id = window.strategy_lab_job_id = "active"
    old = (window.finder.symbol.text(), window.strategy_lab.ticker.text())
    window.workflow.adopt("SPY", SID, "Exact")
    assert (window.finder.symbol.text(), window.strategy_lab.ticker.text()) == old


def test_saved_context_is_keyed_to_exact_ticker_and_strategy(window, setup):
    select(window)
    response = read_saved_validation(setup.worker, setup.request)
    window.workflow._saved_loaded(response)
    assert "NO RELIABLE EDGE" in window.workflow.context_status.text()
    window.workflow.adopt("QQQ", SID, "Exact")
    assert "no saved result opened" in window.workflow.context_status.text()


@pytest.mark.parametrize("size", [(900, 640), (1180, 780), (1600, 1000)])
def test_persistent_header_navigation_and_results_geometry(window, app, size):
    select(window)
    window.resize(*size)
    window.show()
    window.workflow.go(window.results)
    app.processEvents()
    assert window.workflow.header.isVisible()
    assert window.workflow.next.isVisible()
    assert window.workflow.sidebar.width() <= 206
    assert window.workflow.context_strategy.width() > 400
    assert window.stack.scroll_area(window.results).verticalScrollBar().isVisible()
    for _, panel in window.search_monitor.panels:
        if hasattr(panel, "open_result"):
            assert panel.open_result.isHidden()
    assert window.workflow.result_panel.parentWidget() is window.results


def test_scroll_routes_to_page_and_option_routes_to_table(app):
    window = QWidget()
    root = QVBoxLayout(window)
    page = QWidget(); layout = QVBoxLayout(page)
    table = QTableWidget(100, 2); table.setFixedHeight(200)
    layout.addWidget(table)
    filler = QLabel("Page content"); filler.setMinimumHeight(1200); layout.addWidget(filler)
    area = page_scroll(page); root.addWidget(area)
    router = PageWheelRouter(window)
    window.resize(600, 400); window.show(); app.processEvents()
    def wheel(modifiers):
        return QWheelEvent(QPointF(20,20), QPointF(20,20), QPoint(0,-60), QPoint(0,-120),
                           Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.ScrollUpdate, False)
    QApplication.sendEvent(table.viewport(), wheel(Qt.KeyboardModifier.NoModifier))
    assert area.verticalScrollBar().value() > 0
    assert table.verticalScrollBar().value() == 0
    before = area.verticalScrollBar().value()
    QApplication.sendEvent(table.viewport(), wheel(Qt.KeyboardModifier.AltModifier))
    assert table.verticalScrollBar().value() > 0
    assert area.verticalScrollBar().value() == before
    window.close()


def test_scrolling_at_page_end_does_not_hijack_table(app):
    window = QWidget(); layout = QVBoxLayout(window); page = QWidget(); content = QVBoxLayout(page)
    table = QTableWidget(100,2); table.setFixedHeight(200); content.addWidget(table)
    page.setMinimumHeight(900)
    area = page_scroll(page); layout.addWidget(area); router = PageWheelRouter(window)
    window.resize(600,400); window.show(); app.processEvents()
    area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
    event = QWheelEvent(QPointF(20,20),QPointF(20,20),QPoint(0,-60),QPoint(0,-120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.ScrollUpdate,False)
    QApplication.sendEvent(table.viewport(),event)
    assert table.verticalScrollBar().value()==0
    window.close()


def test_results_summary_keeps_canonical_numbers_and_no_execution(app, setup):
    response = read_saved_validation(setup.worker, setup.request); before = deepcopy(response)
    page = SavedValidationPage(response)
    assert "FAILED" in page.verdict.text() and "NO RELIABLE EDGE FOUND" in page.verdict.text()
    assert "13/100" in page.strength.text() and "WEAK" in page.strength.text()
    assert "BRITTLE" in page.evidence_summary.text()
    assert "Walk-forward folds: 3" in page.evidence_summary.text()
    assert "Stability checks: 12" in page.evidence_summary.text()
    assert [page.tests.item(i,2).text() for i in range(3)] == ["0","0","0"]
    assert page.checks.isColumnHidden(2)
    assert not page.findChildren(QPushButton)
    assert response == before


def test_disclosure_is_layout_only(app):
    body = QLabel("Exact evidence"); widget = Disclosure("Details", body)
    assert body.isHidden()
    widget.set_expanded(True)
    assert not body.isHidden()


def test_page_stack_preserves_identity(app):
    stack = WorkflowPageStack(); pages = [QWidget(), QWidget()]
    for page in pages: stack.addWidget(page)
    stack.setCurrentWidget(pages[1])
    assert stack.currentWidget() is pages[1] and stack.widget(0) is pages[0]


@pytest.mark.parametrize("size", [(900, 660), (1180, 808), (1470, 922)])
def test_analyzer_responsive_toolbar_stays_inside_page_viewport(window, app, size):
    select(window)
    window.resize(*size)
    window.show()
    window.workflow.go(window.analysis)
    app.processEvents()
    area = window.stack.scroll_area(window.analysis)
    controls = (window.analysis.symbol, window.analysis.run, window.analysis.timeframe,
                window.analysis.vwap, window.analysis.ema)
    assert area.horizontalScrollBar().maximum() == 0
    for control in controls:
        left = control.mapTo(area.viewport(), control.rect().topLeft()).x()
        assert 0 <= left and left + control.width() <= area.viewport().width(), (
            type(control).__name__, left, control.width(), area.viewport().width())
        assert control.height() >= control.fontMetrics().height() + 8
    assert window.workflow.analysis_toolbar.narrow == (size[0] == 900)
    assert window.analysis.symbol.text() == "SPY"
    assert window.strategy_lab.strategy.currentData() == SID
    assert window.analysis.run.isVisible() and window.analysis.run.isEnabled()
    assert not window.runtime.request_json.called


def test_documented_minimum_prevents_unsupported_narrow_window(window, app):
    window.resize(850,660)
    window.show()
    window.workflow.go(window.analysis)
    app.processEvents()
    assert window.minimumWidth() == 900 and window.width() >= 900
    assert window.stack.scroll_area(window.analysis).horizontalScrollBar().maximum() == 0
