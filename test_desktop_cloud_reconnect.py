import os
import queue
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget

from desktop.trading_intelligence.pages import JobsPage
from desktop.trading_intelligence.window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def finder(job_id="failed", **changes):
    return {"id": job_id, "job_type": "strategy.stock_finder", "execution_target": "cloud",
            "status": "failed", "terminal": True, **changes}


def test_button_only_emits_for_selected_failed_cloud_finder_and_preserves_selection(app):
    page = JobsPage()
    records = [finder(), finder("complete", status="complete"), finder("cancel", status="cancelled"),
               finder("local", execution_target="local"), finder("other", job_type="system.health"),
               finder("running", status="optimizing", terminal=False), finder("result", result={"saved": True})]
    emitted = []
    page.reconnect_requested.connect(emitted.append)
    page.render_jobs(records)
    assert not page.reconnect.isEnabled()
    for row in range(1, len(records)):
        page.table.selectRow(row)
        assert not page.reconnect.isEnabled()
    page.table.selectRow(0)
    assert page.reconnect.isEnabled()
    page.render_jobs(list(reversed(records)))
    assert page._selected_job_id() == "failed"
    page.reconnect.click()
    assert emitted == ["failed"]
    page.reconnect_busy = True
    page.render_jobs(records)
    assert not page.reconnect.isEnabled()
    page.close()


@pytest.mark.parametrize("fail", [False, True])
def test_network_reconnect_is_nonblocking_and_does_not_submit_research(app, fail):
    gate = threading.Event()
    entered = threading.Event()
    calls = []

    class Runtime:
        def request_json(self, method, path, body, *, timeout):
            calls.append((method, path, body, timeout))
            entered.set()
            assert gate.wait(5)
            if fail:
                raise RuntimeError("The cloud job has not recovered")
            return finder(status="optimizing", terminal=False)

    class Harness(QWidget):
        reconnect_cloud_job = MainWindow.reconnect_cloud_job
        _finish_cloud_reconnect = MainWindow._finish_cloud_reconnect

        def refresh_jobs(self):
            self.jobs.render_jobs([finder(status="failed" if fail else "optimizing")])

    window = Harness()
    window.runtime = Runtime()
    window.jobs = JobsPage()
    window.jobs.render_jobs([finder()])
    window.jobs.table.selectRow(0)
    window.finder_job_id = ""
    window._reconnect_results = queue.Queue()
    window._reconnect_timer = QTimer(window)
    window._reconnect_timer.timeout.connect(window._finish_cloud_reconnect)
    started = time.monotonic()
    window.reconnect_cloud_job("failed")
    assert time.monotonic() - started < 0.5
    assert entered.wait(1)
    window.reconnect_cloud_job("failed")  # repeated click must not issue a second request
    tick = []
    QTimer.singleShot(0, lambda: tick.append(True))
    app.processEvents()
    assert tick and window.jobs.reconnect_busy
    gate.set()
    deadline = time.monotonic() + 3
    while window.jobs.reconnect_busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not window.jobs.reconnect_busy
    assert calls == [("POST", "/v1/jobs/failed/reconnect-cloud", {}, 180.0)]
    assert window.finder_job_id == ("" if fail else "failed")
    assert ("not confirmed" if fail else "Failure history preserved") in window.jobs.reconnect_status.text()
    window.jobs.close()
    window.close()
