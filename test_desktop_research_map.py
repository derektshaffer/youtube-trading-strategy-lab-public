import json
import pytest
from PySide6.QtWidgets import QApplication, QPushButton, QAbstractItemView
from systematic_trader.research_inventory import CATALOG
from desktop.trading_intelligence.research_map_section import ResearchMapSection
from desktop.trading_intelligence.research_ml_page import ResearchMLPage


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_all_saved_views_are_read_only_and_have_literal_provenance(app):
    widget=ResearchMapSection();catalog=json.loads(CATALOG.read_text())
    widget.render(dict(status='verified_inventory',**catalog))
    assert not widget.findChildren(QPushButton)
    assert widget.table.editTriggers()==QAbstractItemView.EditTrigger.NoEditTriggers
    for index,(key,_,_) in enumerate(widget.VIEWS):
        widget.view.setCurrentIndex(index)
        assert widget.table.rowCount()==len(catalog[key])
        assert widget.detail.toPlainText()
    widget.view.setCurrentIndex(1)
    assert 'Literal source:' in widget.detail.toPlainText()
    assert 'page 9' in widget.detail.toPlainText()
    assert '\n\n' in widget.detail.toPlainText()
    assert widget.detail.toPlainText().startswith('Name\n')
    widget.close()


def test_failed_refresh_clears_old_inventory(app):
    page=ResearchMLPage();catalog=json.loads(CATALOG.read_text())
    page.render_summary(dict(research_inventory=dict(status='verified_inventory',**catalog)))
    assert page.tabs.tabText(6)=='Research map'
    assert page.research_map.table.rowCount()==5
    page.set_error('Offline')
    assert page.research_map.table.rowCount()==0
    assert 'No clearance inferred' in page.research_map.status.text()
    page.close()
