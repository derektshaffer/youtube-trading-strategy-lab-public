"""Rendered geometry checks for short windows and pages with long forms."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QWidget, QLabel, QLineEdit, QComboBox, QAbstractSpinBox, QPushButton, QCheckBox
from test_desktop_web_workflow import app, window
from desktop.trading_intelligence.workflow_widgets import Disclosure


@pytest.mark.parametrize('size', [(900, 620), (1180, 780), (1600, 1000)])
def test_all_pages_keep_readable_controls_and_scroll_excess_content(window, app, size):
    window.resize(*size)
    window.show()
    window.strategy_lab.set_options({'strategies': [{'id': 'fixture', 'name': 'Faithful breakout strategy'}], 'faithful_count': 1})
    window.workflow.adopt('SPY', 'fixture', 'Faithful breakout strategy')
    for index in range(window.stack.count()):
        page = window.stack.widget(index)
        window.show_page(index)
        app.processEvents()
        area = window.stack.scroll_area(page)
        assert window.stack.indexOf(page) == index
        assert window.stack.currentWidget() is page
        assert page.height() >= page.minimumSizeHint().height()
        for disclosure in page.findChildren(Disclosure):
            assert disclosure.toggle.isVisibleTo(page) and disclosure.toggle.isEnabled()
            area.ensureWidgetVisible(disclosure.toggle)
            app.processEvents()
            point = disclosure.toggle.mapTo(area.viewport(), disclosure.toggle.rect().center())
            assert area.viewport().rect().contains(point)
            before = (window.workflow.ticker, window.workflow.strategy_id,
                      window.strategy_lab.ticker.text(), window.strategy_lab.strategy.currentData())
            disclosure.toggle.click()
            app.processEvents()
            assert disclosure.body.isVisibleTo(page)
            disclosure.toggle.click()
            app.processEvents()
            assert not disclosure.body.isVisibleTo(page)
            assert before == (window.workflow.ticker, window.workflow.strategy_id,
                              window.strategy_lab.ticker.text(), window.strategy_lab.strategy.currentData())
            disclosure.toggle.click()
            app.processEvents()
            assert disclosure.body.isVisibleTo(page)
        for control in page.findChildren(QWidget):
            if not isinstance(control, (QLineEdit, QComboBox, QAbstractSpinBox, QPushButton, QCheckBox)):
                continue
            if not control.isVisibleTo(page):
                continue
            # Spin-box editors belong to the outer control, not a form row.
            if isinstance(control, QLineEdit) and isinstance(control.parentWidget(), QAbstractSpinBox):
                continue
            assert control.height() >= control.fontMetrics().height() + 8, (
                type(page).__name__, type(control).__name__, control.height(), control.fontMetrics().height())
        for label in page.findChildren(QLabel):
            if not label.isVisibleTo(page) or not label.text():
                continue
            required = label.heightForWidth(label.width()) if label.hasHeightForWidth() else label.minimumSizeHint().height()
            assert label.height() >= required, (type(page).__name__, label.text(), label.height(), required)
        if page.height() > area.viewport().height():
            assert area.verticalScrollBar().maximum() > 0
    window.show_page(window.stack.indexOf(window.strategy_lab))
    app.processEvents()
    area = window.stack.scroll_area(window.strategy_lab)
    assert area.verticalScrollBar().maximum() > 0
    area.ensureWidgetVisible(window.strategy_lab.run)
    app.processEvents()
    assert window.strategy_lab.run.isVisibleTo(window.strategy_lab)
    assert window.strategy_lab.run.isEnabled()
    assert window.strategy_lab.ticker.text() == 'SPY'
    assert window.strategy_lab.strategy.currentData() == 'fixture'
    point = window.strategy_lab.run.mapTo(area.viewport(), window.strategy_lab.run.rect().center())
    assert area.viewport().rect().contains(point)


def test_page_navigation_preserves_scroll_position_and_field_values(window, app):
    window.resize(900, 620)
    window.show()
    lab = window.strategy_lab
    index = window.stack.indexOf(lab)
    window.show_page(index)
    app.processEvents()
    lab.ticker.setText('CHPT')
    area = window.stack.scroll_area(lab)
    area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
    position = area.verticalScrollBar().value()
    window.show_page(window.stack.indexOf(window.market_discovery))
    app.processEvents()
    window.show_page(index)
    app.processEvents()
    assert lab.ticker.text() == 'CHPT'
    assert area.verticalScrollBar().value() == position
