import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time
from types import SimpleNamespace

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from fastapi.testclient import TestClient

from desktop.trading_intelligence.parity_window import MainWindow
from hybrid_runtime.api import create_app
from test_search_monitor import setup, remote
from copy import deepcopy


@pytest.fixture(scope="module")
def app():
    from PySide6.QtCore import QLibraryInfo
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(setup, app, monkeypatch, tmp_path):
    monkeypatch.setattr(QTimer, "singleShot", lambda *args: None)
    monkeypatch.setattr("desktop.trading_intelligence.onboarding_window.configuration_status", lambda _: {})
    api = TestClient(create_app(setup.service, expected_token="fixture-token", search_monitor=setup.monitor))
    calls = []
    def request(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        result = api.request(method, path, json=body, headers={"Authorization": "Bearer fixture-token"})
        if result.status_code >= 400:
            raise RuntimeError(result.json()["detail"])
        return result.json()
    runtime = SimpleNamespace(data_dir=tmp_path, request_json=request)
    w = MainWindow(runtime, smoke=True)
    for timer in w.findChildren(QTimer):
        timer.stop()
    w.monitor_fixture = setup
    w.monitor_calls = calls
    yield w
    w.close()
    api.close()


def settle(window, app):
    deadline = time.monotonic() + 4
    while window.search_monitor.busy and time.monotonic() < deadline:
        app.processEvents()
        window.search_monitor.tick()
        time.sleep(.01)
    assert not window.search_monitor.busy


def panel(window, page):
    return next(panel for owner, panel in window.search_monitor.panels if owner is page)


def test_open_strategy_lab_automatically_lists_remote_runs_and_selection_survives_refresh(window, app):
    s = window.monitor_fixture
    s.cloud.document["research_queue"] = [remote("running"), remote("waiting", "queued")]
    window.show_page(window.stack.indexOf(window.strategy_lab))
    window.search_monitor.tick()
    settle(window, app)
    p = panel(window, window.strategy_lab)
    assert p.table.rowCount() == 2 and "2 unfinished" in p.status.text()
    index = next(i for i, row in enumerate(p.rows) if row["id"] == "running")
    p.table.selectRow(index)
    assert "SDOT" in p.detail.text() and "cannot be interrupted" in p.detail.text()
    assert not p.cancel.isEnabled()
    s.cloud.document["research_queue"][0]["progress"] = .62
    p.refresh.click()
    settle(window, app)
    assert p.selected()["id"] == "running" and p.selected()["progress"] == .62
    assert panel(window, window.research_ml).table.rowCount() == 2
    assert panel(window, window.finder).table.rowCount() == 2
    assert s.cloud.write_count == 0 and s.cloud.dispatches == []
    assert not any(path == "/v1/jobs" for _, path, _ in window.monitor_calls)


@pytest.mark.parametrize("confirm", [True, False])
def test_click_selected_queued_cancel_requires_confirmation_and_preserves_other_runs(window, app, monkeypatch, confirm):
    s = window.monitor_fixture
    s.cloud.document["research_queue"] = [remote("cancel-me", "queued"), remote("keep-me")]
    window.search_monitor.refresh()
    settle(window, app)
    p = panel(window, window.strategy_lab)
    p.table.selectRow(next(i for i, row in enumerate(p.rows) if row["id"] == "cancel-me"))
    assert p.cancel.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes if confirm else QMessageBox.StandardButton.No)
    p.cancel.click()
    settle(window, app)
    assert s.cloud.document["research_queue"][0]["status"] == ("cancelled" if confirm else "queued")
    assert s.cloud.document["research_queue"][1]["status"] == "running"
    assert s.cloud.write_count == int(confirm)
    if confirm:
        assert p.selected()["id"] == "cancel-me" and p.selected()["status"] == "cancelled"
        assert not p.cancel.isEnabled()


def test_long_refresh_does_not_block_qt_or_duplicate_calls(window, app, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = window.runtime.request_json
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(4)
        return original(*args, **kwargs)
    monkeypatch.setattr(window.runtime, "request_json", slow)
    start = time.monotonic()
    window.search_monitor.refresh()
    assert time.monotonic() - start < .5
    assert entered.wait(1)
    window.search_monitor.refresh()
    fired = []
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: fired.append(True))
    timer.start(0)
    app.processEvents()
    assert fired and window.search_monitor.busy
    release.set()
    settle(window, app)
    assert sum(path == "/v1/searches" for _, path, _ in window.monitor_calls) == 1


def test_read_error_is_explicit_and_disables_stale_cancel(window, app, monkeypatch):
    s = window.monitor_fixture
    s.cloud.document["research_queue"] = [remote("queued", "queued")]
    window.search_monitor.refresh()
    settle(window, app)
    p = panel(window, window.strategy_lab)
    p.table.selectRow(0)
    assert p.cancel.isEnabled()
    def fail(*args, **kwargs):
        raise TimeoutError("Disconnected")
    monkeypatch.setattr(window.runtime, "request_json", fail)
    p.refresh.click()
    settle(window, app)
    assert p.table.rowCount() == 1 and "stale" in p.status.text()
    assert not p.cancel.isEnabled() and p.refresh.isEnabled()


@pytest.mark.parametrize('failure', ['busy', 'timeout'])
def test_cancellation_error_survives_refresh_and_navigation_without_auto_retry(window, app, monkeypatch, failure):
    s = window.monitor_fixture
    s.cloud.document['research_queue'] = [remote('cancel-me', 'queued')]
    window.search_monitor.refresh()
    settle(window, app)
    p = panel(window, window.finder)
    p.table.selectRow(0)
    original = window.runtime.request_json
    posts = []
    def request(method, path, *args, **kwargs):
        if method == 'POST':
            posts.append(path)
            raise RuntimeError('Cloud sync is busy. No cancellation was sent; try again shortly.' if failure == 'busy' else 'Response timed out')
        return original(method, path, *args, **kwargs)
    monkeypatch.setattr(window.runtime, 'request_json', request)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    p.cancel.click()
    settle(window, app)
    for _ in range(3):
        window.search_monitor.refresh(force=True)
        settle(window, app)
    window.show_page(window.stack.indexOf(window.strategy_lab))
    for _, card in window.search_monitor.panels:
        assert 'Cancellation not confirmed' in card.action_notice.text()
        assert 'cancel-me' in card.action_notice.text()
        assert 'Cancellation not confirmed' in card.table.item(0, 3).text()
    assert len(posts) == 1 and s.cloud.write_count == 0
    assert p.cancel.isEnabled()  # only a new explicit click can retry


def test_confirmed_cancellation_survives_stale_queued_missing_and_failed_refresh(window, app, monkeypatch):
    s = window.monitor_fixture
    s.cloud.document['research_queue'] = [remote('cancel-me', 'queued')]
    window.search_monitor.refresh()
    settle(window, app)
    p = panel(window, window.finder)
    p.table.selectRow(0)
    stale = deepcopy(s.monitor.snapshot())
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    p.cancel.click()
    settle(window, app)
    assert 'Cancelled' in p.action_notice.text()
    stale['stale'] = True
    monkeypatch.setattr(window.runtime, 'request_json', lambda *args, **kwargs: stale)
    window.search_monitor.refresh()
    settle(window, app)
    assert 'Cancelled' in p.table.item(0, 3).text() and not p.cancel.isEnabled()
    stale['rows'] = []
    window.search_monitor.refresh()
    settle(window, app)
    assert 'Cancelled' in p.action_notice.text()
    def fail(*args, **kwargs):
        raise TimeoutError('Disconnected')
    monkeypatch.setattr(window.runtime, 'request_json', fail)
    window.search_monitor.refresh()
    settle(window, app)
    assert 'Cancelled' in p.action_notice.text() and 'stale' in p.status.text()
    assert s.cloud.write_count == 1


def test_pending_stop_waits_for_exact_confirmation_and_does_not_disable_other_run(window, app, monkeypatch):
    from test_search_monitor import stoppable
    s = window.monitor_fixture
    run, calls = stoppable(s)
    s.cloud.document['research_queue'].append(remote('other', 'queued'))
    window.search_monitor.refresh()
    settle(window, app)
    p = panel(window, window.strategy_lab)
    p.table.selectRow(next(i for i, row in enumerate(p.rows) if row['id'] == 'cloud-lab'))
    row = dict(p.selected())
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    p.cancel.click()
    settle(window, app)
    assert 'Cancellation pending' in p.action_notice.text() and not p.cancel.isEnabled()
    window.search_monitor.confirm_cancel(row)
    assert calls == ['123']
    p.table.selectRow(next(i for i, row in enumerate(p.rows) if row['id'] == 'other'))
    assert p.cancel.isEnabled() and 'Cancellation pending' in p.action_notice.text()
    run.update(status='completed', conclusion='cancelled')
    window.search_monitor.refresh(force=True)
    settle(window, app)
    assert 'Cancelled' in p.action_notice.text() and p.cancel.isEnabled()
    wrong = dict(row, binding={'repository': 'other/library'})
    window.search_monitor._action(wrong, 'pending', 'Different library')
    link = dict(row['binding'], remote_job_id=row['id'])
    assert window.search_monitor.finder_cancellation('local-id', link)['state'] == 'confirmed'
    assert window.search_monitor.finder_cancellation('other-id', {}) is None
