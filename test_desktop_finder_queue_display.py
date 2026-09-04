import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from types import SimpleNamespace
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from desktop.trading_intelligence.finder_page import StockFinderPage
from desktop.trading_intelligence.finder_window import MainWindow


def test_finder_poll_names_sls_blocker_and_restores_chpt_progress_when_started(monkeypatch):
    app = QApplication.instance() or QApplication([])
    page = StockFinderPage()
    page.symbol.setText('CHPT')
    job = {'terminal': False, 'progress': .01, 'stage': 'cloud_queued'}
    link = {'remote_status': 'queued', 'metadata': {'symbol': 'CHPT', 'queue_blockers': [{
        'symbol': 'SLS', 'profile': 'Very Deep', 'stage': 'walk_forward',
        'progress': .94, 'shards_total': 12, 'shards_completed': 12,
        'message': 'Validating six candidates', 'updated_at': '2026-09-04T00:10:44Z',
    }]}}
    window = SimpleNamespace(finder_job_id='chpt-job', finder=page,
        runtime=SimpleNamespace(request_json=lambda *args: job),
        _cloud_link=lambda job_id: link)
    MainWindow._poll_stock_finder(window)
    assert page.status.text() == 'CHPT queued · waiting for SLS Very Deep'
    assert '94%' in page.detail.text() and '12/12 batches complete' in page.detail.text()
    assert 'walk forward' in page.detail.text() and '2026-09-04 00:10 UTC' in page.detail.text()
    assert '2026-09-04T' not in page.detail.text()
    assert page.progress.value() == 0
    assert page.progress.format() == 'Queued — CHPT has not started'
    assert window.finder_job_id == 'chpt-job'
    assert not page.run.isEnabled()

    # An unchanged poll must not flash a percentage/title or reapply the style.
    def unexpected(*args):
        raise AssertionError('unchanged queued display was rewritten')
    with monkeypatch.context() as patch:
        patch.setattr(page.progress, 'setFormat', unexpected)
        patch.setattr(page.progress, 'setValue', unexpected)
        patch.setattr(page.status, 'setText', unexpected)
        patch.setattr(page.detail, 'setText', unexpected)
        patch.setattr(page.banner, 'setProperty', unexpected)
        for _ in range(5):
            MainWindow._poll_stock_finder(window)

    link['metadata']['queue_blockers'] = []
    MainWindow._poll_stock_finder(window)
    assert page.status.text() == 'CHPT queued'
    assert 'SLS' not in page.detail.text()

    job.update(progress=.2, stage='distributed_optimization')
    link.update(remote_status='running')
    link['metadata']['distributed_message'] = 'CHPT optimization started'
    MainWindow._poll_stock_finder(window)
    assert page.progress.format() == '%p%' and page.progress.value() == 200
    assert 'CHPT optimization started' in page.detail.text()
    assert 'SLS' not in page.status.text()
    job.update(terminal=True, status='cancelled')
    window.refresh_jobs = lambda: None
    MainWindow._poll_stock_finder(window)
    assert page.status.text() == 'Cloud Finder · Cancelled'
    assert page.progress.format() == 'Cancelled'
    assert page.run.isEnabled() and not window.finder_job_id
    page.close()


@pytest.mark.parametrize('state,title', [('pending', 'Cancellation pending'), ('unconfirmed', 'Cancellation not confirmed'), ('confirmed', 'Cancelled')])
def test_finder_cancellation_feedback_overrides_old_queued_poll_until_local_terminal(state, title):
    app = QApplication.instance() or QApplication([])
    page = StockFinderPage()
    page.symbol.setText('ABDT')
    job = {'terminal': False, 'status': 'claimed', 'stage': 'cloud_queued'}
    action = {'state': state, 'title': title, 'message': 'Exact ABDT request'}
    window = SimpleNamespace(finder_job_id='abdt-job', finder=page,
        runtime=SimpleNamespace(request_json=lambda *args: job),
        _cloud_link=lambda _: {'remote_status': 'queued'},
        search_monitor=SimpleNamespace(finder_cancellation=lambda *args: action),
        refresh_jobs=lambda: None)
    for _ in range(3):
        MainWindow._poll_stock_finder(window)
        assert page.status.text() == title and page.progress.format() == title
        assert not page.run.isEnabled() and window.finder_job_id == 'abdt-job'
    job.update(terminal=True, status='cancelled', stage='cancelled')
    MainWindow._poll_stock_finder(window)
    assert page.status.text() == 'Cloud Finder · Cancelled' and page.run.isEnabled()
    assert not window.finder_job_id and page.symbol.text() == 'ABDT'
    page.close()
