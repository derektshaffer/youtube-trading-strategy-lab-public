"""Read-only Research UX contracts using fixtures, never cloud execution."""
import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QPushButton

from desktop.trading_intelligence.research_ml_page import ResearchMLPage
from hybrid_runtime.research_ml_summary import build_research_ml_summary
from test_desktop_discovery_lifecycle import app, window
from test_desktop_research_ml_summary import fixture_library


@pytest.fixture
def page(app):
    widget = ResearchMLPage()
    yield widget
    widget.close()


def test_unloaded_state_does_not_claim_empty_library(page):
    assert page.load_state == "unloaded"
    assert [page.tabs.tabText(i) for i in range(6)] == [
        "Research Runs", "Sources", "Hypotheses", "Experiments", "Predictive ML", "Cloud Queue"]
    assert all(card.value.text() == "Not loaded" for card in page.metric_cards.values())
    assert all(section["table"].isHidden() for section in page.sections.values())
    assert all("Not loaded" in section["state"].text() for section in page.sections.values())


@pytest.mark.parametrize("key", ["research_runs", "sources", "hypotheses", "experiments",
                                "predictive_ml_runs", "ready_shadow_models", "queue"])
def test_empty_sections_explain_next_step_without_blank_table(page, key):
    page.render_summary(build_research_ml_summary({}))
    section = page.sections[key]
    assert section["table"].isHidden()
    assert section["details"].isHidden()
    assert section["empty"] in section["state"].text()
    assert section["next"] in section["state"].text()
    assert "\n" in section["state"].text()
    assert "\\n" not in section["state"].text()


def test_known_zero_is_zero_and_unknown_is_not_fabricated(page):
    page.render_summary(build_research_ml_summary({}))
    assert all(card.value.text() == "0" and not card.isHidden() for card in page.metric_cards.values())
    page.render_summary({})
    assert all(card.isHidden() and card.value.text() == "Unavailable" for card in page.metric_cards.values())
    assert all("not reported" in section["state"].text() for section in page.sections.values())


@pytest.mark.parametrize("state", ["working", "error"])
def test_loading_and_error_do_not_present_old_counts_as_current(page, state):
    page.render_summary(build_research_ml_summary(fixture_library()))
    if state == "working":
        page.set_working("Loading", "Reading saved records")
    else:
        page.set_error("Library unavailable")
    assert all(card.value.text() == ("Loading" if state == "working" else "Unavailable")
               for card in page.metric_cards.values())
    assert all(section["table"].isHidden() for section in page.sections.values())
    assert page.refresh.isEnabled() == (state == "error")


def test_populated_tables_and_exact_details_are_read_only(page):
    result = build_research_ml_summary(fixture_library())
    result["ready_shadow_models"] = [{"id": "shadow-1", "target": "continuation",
        "session_mode": "regular", "model_type": "logistic", "shadow_scoring_enabled": True}]
    page.render_summary(result)
    for section in page.sections.values():
        table = section["table"]
        assert not table.isHidden() and table.rowCount() > 0
        assert table.height() <= 260
        assert table.editTriggers() == table.EditTrigger.NoEditTriggers
        table.selectRow(0)
        assert "Id: " in section["details"].text()
        assert "\n" in section["details"].text() and "\\n" not in section["details"].text()
        assert "Option" in table.toolTip()


def test_only_library_refresh_and_saved_file_read_actions_and_safety_is_explicit(page):
    assert [button.text() for button in page.findChildren(QPushButton)] == [
        "Refresh Research", "Open saved research result"]
    assert "cannot place trades" in page.safety.text()
    assert "change live ranking" in page.safety.text()
    assert "bypass validation" in page.safety.text()
    assert "not production-approved" in page.safety.text()
    requests = []
    page.refresh_requested.connect(lambda: requests.append("read"))
    page.refresh.click()
    assert requests == ["read"]


def test_refresh_controller_and_real_handler_only_read_library(window, monkeypatch):
    from hybrid_runtime.engine_adapter import research_ml_summary_handler
    from hybrid_runtime.library_source import LoadedLibrary
    from hybrid_runtime.worker import LocalWorker
    source = fixture_library()
    monkeypatch.setattr("hybrid_runtime.library_source.load_library_for_job",
                        lambda *a, **k: LoadedLibrary(source, {"source": "inline_fixture"}))
    window.refresh_research_ml()
    requests = [payload for method, path, payload in window.runtime.calls if method == "POST" and path == "/v1/jobs"]
    assert len(requests) == 1
    assert requests[0]["job_type"] == "library.research_ml_summary"
    assert requests[0]["requested_target"] == "local"
    assert requests[0]["payload"] == {"limit": 30}
    worker = LocalWorker(window.runtime.service, worker_id="research-ui-test",
                         handlers={"library.research_ml_summary": research_ml_summary_handler})
    worker.run_once()
    window.poll_active_job()
    jobs = window.runtime.service.list()
    assert len(jobs) == 1
    assert jobs[0].as_dict()["status"] == "complete"
    assert jobs[0].as_dict()["result"]["affects_execution"] is False
    assert jobs[0].as_dict()["result"]["affects_live_ranking"] is False
    assert window.research_ml.load_state == "ready"
    assert window.research_ml.refresh.isEnabled()
    assert {path for method, path, _ in window.runtime.calls if method == "POST"} <= {"/v1/route", "/v1/jobs"}


def test_global_scope_preserves_stock_without_mislabeling_research(window):
    window.workflow.adopt("SPY", "exact-id", "Exact strategy")
    window.workflow.go(window.research_ml)
    assert window.workflow.context_ticker.text() == "Research Library"
    assert "not filtered" in window.workflow.context_strategy.text()
    assert "SPY" in window.workflow.context_status.text()
    assert window.workflow.next.isHidden()
    assert window.workflow.sidebar_safety.isHidden()
    assert window.workflow.utilities.toggle.text() == "Tools && Connections"
    assert window.workflow.utilities.toggle.accessibleName() == "Tools & Connections"
    window.workflow.go(window.analysis)
    assert window.workflow.context_ticker.text() == "SPY"
    assert window.workflow.strategy_id == "exact-id"
    assert not window.workflow.next.isHidden()
    assert not window.workflow.sidebar_safety.isHidden()


@pytest.mark.parametrize("size", [(900, 660), (1180, 808), (1470, 922)])
def test_research_layout_and_tabs_fit_supported_sizes(window, app, size):
    window.resize(*size)
    window.show()
    page = window.research_ml
    page.render_summary(build_research_ml_summary(fixture_library()))
    window.workflow.go(page)
    area = window.stack.scroll_area(page)
    for index in range(page.tabs.count()):
        page.tabs.setCurrentIndex(index)
        app.processEvents()
        assert area.horizontalScrollBar().maximum() == 0
        assert area.verticalScrollBar().isVisible()
        assert page.refresh.isVisible() and page.refresh.isEnabled()
        assert page.refresh.width() >= page.refresh.fontMetrics().horizontalAdvance(page.refresh.text())


def test_research_table_wheel_preserves_page_first_and_option_scroll(window, app):
    window.resize(900, 660)
    window.show()
    source = fixture_library()
    source["research_worker_runs"] = [dict(source["research_worker_runs"][0], id=f"run-{i}") for i in range(30)]
    page = window.research_ml
    page.render_summary(build_research_ml_summary(source))
    window.workflow.go(page)
    app.processEvents()
    area = window.stack.scroll_area(page)
    table = page.run_table
    def wheel(modifiers):
        return QWheelEvent(QPointF(20,20), QPointF(20,20), QPoint(0,-60), QPoint(0,-120),
                           Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.ScrollUpdate, False)
    QApplication.sendEvent(table.viewport(), wheel(Qt.KeyboardModifier.NoModifier))
    assert area.verticalScrollBar().value() > 0 and table.verticalScrollBar().value() == 0
    before = area.verticalScrollBar().value()
    QApplication.sendEvent(table.viewport(), wheel(Qt.KeyboardModifier.AltModifier))
    assert table.verticalScrollBar().value() > 0
    assert area.verticalScrollBar().value() == before


@pytest.mark.parametrize("field,value,expected", [
    ("hypothesis_count", 0, "0"),
    ("confidence", 0.0, "0.0"),
    ("shadow_scoring_enabled", False, "False"),
    ("shadow_scoring_enabled", True, "True"),
    ("model", None, "Not recorded"),
    ("source_count", 7, "7"),
    ("confidence", 0.73, "0.73"),
    ("confidence", -1.0, "Not recorded"),
    ("other_number", -1, "-1"),
    ("status", "complete", "complete"),
    ("model", "", "Not recorded"),
    ("unsupported_collection", [], "Not recorded"),
    ("unsupported_collection", {}, "Not recorded"),
])
def test_detail_formatter_preserves_present_scalars_and_missing_contract(field, value, expected):
    from desktop.trading_intelligence.research_ml_page import _detail_display
    assert _detail_display(field, value) == expected


def test_row_detail_zero_none_newlines_and_tables_cards_unchanged(page):
    from copy import deepcopy
    result = build_research_ml_summary({})
    result["research_runs"] = [{"id": "zero-run", "kind": "worker", "status": "complete",
        "topic": "Recorded activity", "model": None, "hypothesis_count": 0, "source_count": 0}]
    original = deepcopy(result)
    page.render_summary(result)
    page.run_table.selectRow(0)
    detail = page.sections["research_runs"]["details"].text()
    assert "Hypothesis Count: 0" in detail
    assert "Source Count: 0" in detail
    assert "Model: Not recorded" in detail
    assert "\\n" not in detail
    assert len(detail.splitlines()) == len(result["research_runs"][0])
    assert "When:" not in detail  # Absent fields are not invented.
    assert page.run_table.item(0, 5).text() == page.run_table.item(0, 6).text() == "0"
    assert all(card.value.text() == "0" for card in page.metric_cards.values())
    assert result == original
