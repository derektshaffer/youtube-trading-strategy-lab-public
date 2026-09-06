"""Exercise the real window signals, durable transitions, and stalled-worker recovery."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from desktop.trading_intelligence.parity_window import MainWindow
from hybrid_runtime.contracts import JobStatus
from hybrid_runtime.service import HybridService
from hybrid_runtime.storage import HybridStore
from hybrid_runtime.worker import LocalWorker
from hybrid_runtime.engine_adapter import market_discovery_handler


OPTIONS = {'strategies': [{'id': str(i), 'name': f'Family {i}'} for i in range(26)],
           'faithful_count': 26, 'blocked_count': 111}
RESULT = {'results': [{'symbol': 'TEST', 'status': 'MATCH', 'score': 90}],
          'match_count': 1, 'validated_match_count': 0, 'strategy_count': 26}


class Runtime:
    def __init__(self, directory):
        self.data_dir = directory
        self.service = HybridService(HybridStore(directory / 'hybrid.sqlite3'))
        self.calls = []
        self.read_error = None
        self.override = None

    def request_json(self, method, path, payload=None, **kwargs):
        self.calls.append((method, path, payload))
        if path == '/v1/route':
            return {'target': 'local', 'reason': 'fixture'}
        if path == '/v1/jobs':
            job, created = self.service.submit(payload)
            return {'job': job.as_dict(), 'created': created}
        if path.startswith('/v1/jobs?'):
            return {'jobs': [job.as_dict() for job in self.service.list()]}
        job_id = path.split('/')[3]
        if path.endswith('/cancel'):
            return self.service.cancel(job_id).as_dict()
        if self.read_error:
            raise self.read_error
        return self.override if self.override is not None else self.service.get(job_id).as_dict()


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    # No timers, credentials, provider calls or cloud research in these tests.
    monkeypatch.setattr(QTimer, 'singleShot', lambda *args: None)
    monkeypatch.setattr('desktop.trading_intelligence.onboarding_window.configuration_status', lambda _: {})
    runtime = Runtime(tmp_path)
    w = MainWindow(runtime, smoke=True)
    for timer in w.findChildren(QTimer):
        timer.stop()
    yield w
    w.close()


def finish(window, result=RESULT):
    service = window.runtime.service
    service.claim_local('test')
    service.complete(window._discovery_job_id, result, worker_id='test')
    window.poll_active_job()


def assert_released(window):
    assert window.active_job_id == window.active_purpose == ''
    assert window._discovery_job_id == window._discovery_purpose == ''
    assert window.active_route == {}
    assert window.market_discovery.operation == ''
    assert window.market_discovery.scan.isEnabled()
    assert window.market_discovery.count.isEnabled()
    assert window.market_discovery.cancel.isHidden()


def test_real_window_options_at_15_percent_then_native_scan_click_and_repeat(window):
    page = window.market_discovery
    page.refresh_options.click()
    service = window.runtime.service
    options_id = window._discovery_job_id
    service.claim_local('test')
    service.store.transition_job(options_id, JobStatus.DOWNLOADING_DATA, progress=.15)
    window.poll_active_job()
    assert page.progress.value() == 150
    assert not page.progress.isHidden()
    assert 'Loading faithful strategies' in page.status.text()
    assert 'has not started' in page.detail.text()
    service.complete(options_id, OPTIONS, worker_id='test')
    window.poll_active_job()
    assert page.status.text() == '26 faithful strategy families ready'
    assert page.progress.isHidden()
    assert_released(window)

    page.scan.click()  # Exercise the full window connection, not just a signal spy.
    first = window._discovery_job_id
    assert service.get(first).job_type == 'market.discovery'
    service.claim_local('test')
    service.store.transition_job(first, JobStatus.DOWNLOADING_DATA, progress=.15)
    window.poll_active_job()
    page.render_options(OPTIONS)  # Delayed options must not hide this scan.
    assert page.status.text() == 'Downloading Data'
    assert not page.progress.isHidden() and not page.scan.isEnabled()
    window.refresh_market_discovery_options()
    assert window._discovery_job_id == first
    service.complete(first, RESULT, worker_id='test')
    window.poll_active_job()
    assert_released(window)
    assert page.stock_metric.value.text() == '1'
    assert page.strategy_metric.value.text() == '26'
    assert page.table.rowCount() == 1
    page.scan.click()
    assert window._discovery_job_id and window._discovery_job_id != first
    assert page.table.rowCount() == 0
    finish(window, {**RESULT, 'results': [], 'match_count': 0})
    assert_released(window)
    assert page.stock_metric.value.text() == '0'


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
def test_terminal_failure_and_cancellation_release_every_flag_and_allow_retry(window, status):
    window.market_discovery.scan.click()
    service = window.runtime.service
    job_id = window._discovery_job_id
    if status == 'failed':
        service.claim_local('test')
        service.store.transition_job(job_id, JobStatus.FAILED,
            error={'type': 'ProviderError', 'message': 'Snapshot download failed'})
    else:
        window.market_discovery.cancel.click()
    window.poll_active_job()
    assert_released(window)
    assert ('Snapshot download failed' if status == 'failed' else 'cancelled') in window.market_discovery.detail.text()
    window.market_discovery.scan.click()
    assert window._discovery_job_id != job_id


def test_transient_disconnect_preserves_progress_then_recovers(window):
    window.market_discovery.scan.click()
    job_id = window._discovery_job_id
    window.runtime.read_error = TimeoutError('sidecar temporarily unavailable')
    window.poll_active_job()
    assert window._discovery_job_id == job_id
    assert not window.market_discovery.progress.isHidden()
    assert not window.market_discovery.scan.isEnabled()
    window.runtime.read_error = None
    finish(window)
    assert_released(window)


@pytest.mark.parametrize('failure', ['missing', 'unreachable', 'no_attachment', 'stale_worker', 'deadline', 'bad_result', 'wrong_job'])
def test_lost_or_stale_jobs_always_release_ui(window, failure):
    window.market_discovery.scan.click()
    service = window.runtime.service
    job_id = window._discovery_job_id
    if failure == 'missing':
        window.runtime.read_error = HTTPError('local', 404, 'Job not found', {}, None)
    elif failure == 'unreachable':
        window.runtime.read_error = TimeoutError('sidecar offline')
        window._discovery_unreachable_at = time.monotonic() - 31
    elif failure == 'no_attachment':
        window._discovery_job_id = ''
    elif failure == 'stale_worker':
        window.runtime.override = {**service.get(job_id).as_dict(),
            'heartbeat_at': (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()}
    elif failure == 'deadline':
        window._discovery_started = time.monotonic() - 961
    elif failure == 'bad_result':
        service.claim_local('test')
        service.complete(job_id, {}, worker_id='test')
    else:
        window.runtime.override = {**service.get(job_id).as_dict(), 'job_type': 'library.strategy_lab_options'}
    window.poll_active_job()
    assert_released(window)
    assert 'again' in window.market_discovery.detail.text()
    window.runtime.read_error = None
    window.runtime.override = None
    window.market_discovery.scan.click()
    assert window._discovery_job_id and window._discovery_job_id != job_id


def test_retained_attachment_survives_foreground_flags_being_lost(window):
    window.market_discovery.scan.click()
    window.active_job_id = window.active_purpose = ''
    finish(window)
    assert_released(window)
    assert window.market_discovery.table.rowCount() == 1


def test_submission_exception_and_silent_noop_are_visible_and_retryable(window, monkeypatch):
    original = window.submit_job
    def failed_submit(*args):
        raise TimeoutError('Submission timed out')
    monkeypatch.setattr(window, 'submit_job', failed_submit)
    window.market_discovery.scan.click()
    assert_released(window)
    assert 'Submission timed out' in window.market_discovery.detail.text()
    monkeypatch.setattr(window, 'submit_job', lambda *args: None)
    window.market_discovery.scan.click()
    assert_released(window)
    assert 'not attached' in window.market_discovery.detail.text()
    monkeypatch.setattr(window, 'submit_job', original)
    window.market_discovery.scan.click()
    assert window._discovery_job_id


def test_unconnected_scan_signal_is_reported_instead_of_silently_stalling(window):
    window.market_discovery.run_requested.disconnect(window.run_market_discovery)
    window.market_discovery.scan.click()
    assert not window.market_discovery.progress.isHidden()
    window.poll_active_job()
    assert_released(window)
    assert 'attachment was lost' in window.market_discovery.detail.text()


def test_capability_failure_has_actionable_scan_error(window, monkeypatch):
    monkeypatch.setattr(window, '_require_capabilities', lambda *args: False)
    window.market_discovery.scan.click()
    assert_released(window)
    assert 'Settings' in window.market_discovery.detail.text()


@pytest.fixture
def providers(monkeypatch, tmp_path):
    import hybrid_runtime.library_source as library
    import hybrid_runtime.market_cache as cache
    import hybrid_runtime.desktop_settings as settings
    import trading_intelligence_core as core
    import trading_market_discovery as market
    import youtube_strategy_engine as engine
    monkeypatch.setenv('TRADING_INTELLIGENCE_DESKTOP_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(library, 'load_library_for_job', lambda *a, **k: SimpleNamespace(
        library={'strategies': [{'id': 'one', 'name': 'Family'}]}, metadata={}))
    monkeypatch.setattr(core, 'strategy_integrity_report', lambda _: {'status': 'faithful'})
    monkeypatch.setattr(cache, 'load_alpaca_credentials', lambda: ('fixture', 'fixture'))
    monkeypatch.setattr(settings, 'load_desktop_settings', lambda _: SimpleNamespace(market_feed='sip'))
    monkeypatch.setattr(engine, 'AlpacaMarketData', lambda *a, **k: SimpleNamespace(
        movers=lambda **k: ['TEST'], most_active=lambda **k: ['TEST']))
    def scan(*args, progress):
        progress('Batch 1/1 intraday')
        progress('Batch 1/1 snapshot')  # callback phase revisits must not regress
        return RESULT['results']
    monkeypatch.setattr(market, 'scan_market_strategies', scan)


def test_actual_discovery_handler_completes_through_durable_worker(window, providers):
    window.market_discovery.scan.click()
    worker = LocalWorker(window.runtime.service, worker_id='test',
                         handlers={'market.discovery': market_discovery_handler})
    assert worker.run_once()
    record = window.runtime.service.get(window._discovery_job_id)
    assert record.status == JobStatus.COMPLETE, record.error
    window.poll_active_job()
    assert_released(window)
    assert window.market_discovery.stock_metric.value.text() == '1'


@pytest.mark.parametrize('stop', ['timeout', 'cancel'])
def test_stalled_provider_releases_worker_and_late_completion_cannot_overwrite(window, monkeypatch, stop):
    from hybrid_runtime.bounded_discovery import DISCOVERY_TIMEOUTS
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    def stalled(payload, progress, cancelled):
        entered.set()
        try:
            release.wait(5)
            progress(.7, 'preparing_features', 'Late progress must not be published')
            return RESULT
        finally:
            exited.set()
    if stop == 'timeout':
        monkeypatch.setitem(DISCOVERY_TIMEOUTS, 'market.discovery', .05)
    window.market_discovery.scan.click()
    old_id = window._discovery_job_id
    service = window.runtime.service
    worker = LocalWorker(service, worker_id='test', handlers={'market.discovery': stalled})
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    try:
        assert entered.wait(2)
        if stop == 'cancel':
            window.market_discovery.cancel.click()
        thread.join(2)
        assert not thread.is_alive()
        record = service.get(old_id)
        assert record.status == (JobStatus.FAILED if stop == 'timeout' else JobStatus.CANCELLED)
        if stop == 'timeout':
            assert record.error['type'] == 'TimeoutError'
        window.poll_active_job()
        assert_released(window)
        window.market_discovery.scan.click()
        worker.handlers['market.discovery'] = lambda *args: RESULT
        assert worker.run_once()
        window.poll_active_job()
        assert_released(window)
        assert window.market_discovery.table.rowCount() == 1
    finally:
        release.set()
        thread.join(2)
        assert exited.wait(2)
    assert service.get(old_id).status == record.status
    assert service.get(old_id).result is None
