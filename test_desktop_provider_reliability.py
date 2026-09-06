"""Offline Alpaca transport -> desktop worker -> Qt failure/recovery matrix."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import socket
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, parse_qs

import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QLabel
from test_desktop_discovery_lifecycle import app, window
from hybrid_runtime.desktop_market_data import DesktopMarketData, MarketDataUnavailable
from hybrid_runtime.engine_adapter import market_discovery_handler, stock_analysis_handler
from hybrid_runtime.market_cache import PersistentMarketDataCache, MarketCacheError
from hybrid_runtime.worker import LocalWorker
from hybrid_runtime.contracts import JobStatus
from desktop.trading_intelligence.market_data_labels import snapshot_label, timestamp_label
import youtube_strategy_engine as engine

UTC = timezone.utc
SID = 'webresearch-c238a2839213bb33d9'
SECRET = 'synthetic-secret-must-not-appear'


def bar(stamp):
    return {'t': stamp.isoformat(), 'o': 500, 'h': 501, 'l': 499, 'c': 500, 'v': 100}


def snapshot(stamp):
    return {'latestTrade': {'p': 500, 't': stamp.isoformat()},
            'latestQuote': {'bp': 499.9, 'ap': 500.1, 't': stamp.isoformat()},
            'dailyBar': {'o': 499, 'h': 501, 'l': 498, 'c': 500, 'v': 10000, 'vw': 499.5, 't': stamp.isoformat()},
            'prevDailyBar': {'c': 499, 'h': 500, 'v': 9000}}


@pytest.fixture
def transport(monkeypatch, tmp_path):
    import hybrid_runtime.market_cache as cache
    import hybrid_runtime.desktop_settings as settings
    import hybrid_runtime.library_source as library
    import trading_intelligence_core as core
    from collections import OrderedDict
    monkeypatch.setattr(engine, '_ALPACA_BAR_HISTORY_CACHE', OrderedDict())
    monkeypatch.setenv('TRADING_INTELLIGENCE_DESKTOP_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(cache, 'load_alpaca_credentials', lambda: ('synthetic-key', SECRET))
    monkeypatch.setattr(cache, 'load_desktop_settings', lambda _: SimpleNamespace(market_feed='iex'))
    monkeypatch.setattr(settings, 'load_desktop_settings', lambda _: SimpleNamespace(market_feed='iex'))
    monkeypatch.setattr(core, 'strategy_integrity_report', lambda _: {'status': 'faithful'})
    monkeypatch.setattr(library, 'load_library_for_job', lambda *a, **k: SimpleNamespace(
        library={'strategies': [{'id': SID, 'name': 'Offline provider fixture', 'direction': 'long',
                                'machine_rules': {'min_price': 1}, 'validation_status': 'unvalidated'}]}, metadata={}))
    state = {'failure': None, 'calls': []}
    def request(req, **kwargs):
        url = urlparse(req.full_url)
        state['calls'].append({'path': url.path, 'params': parse_qs(url.query)})
        failure = state['failure']
        if isinstance(failure, int):
            raise HTTPError(req.full_url, failure, 'fixture', {}, BytesIO(json.dumps({'message': 'api_key='+SECRET}).encode()))
        if failure == 'timeout':
            raise TimeoutError(SECRET)
        if failure == 'network':
            raise URLError(socket.gaierror(-2, SECRET))
        if failure == 'connection':
            raise ConnectionResetError(SECRET)
        if failure == 'malformed':
            return BytesIO(b'not json '+SECRET.encode())
        now = datetime.now(UTC) - timedelta(minutes=10)
        rows = [] if failure == 'empty' else [bar(now)]
        if failure == 'partial':
            rows[0].pop('h')
        if '/snapshots' in req.full_url:
            result = {} if failure == 'empty' else {'SPY': snapshot(now)}
        else:
            result = {'bars': {'SPY': rows}, 'next_page_token': None}
        return BytesIO(json.dumps(result).encode())
    monkeypatch.setattr(engine, 'urlopen', request)
    return state


@pytest.mark.parametrize('failure', [401, 403, 429, 500, 502, 503, 'timeout', 'network', 'connection', 'malformed', 'empty', 'partial'])
@pytest.mark.parametrize('mode', ['find', 'analyze'])
def test_real_provider_failure_to_durable_job_and_native_controls_recovers(window, transport, failure, mode):
    window.workflow.adopt('SPY', SID, 'Offline provider fixture')
    page = window.market_discovery if mode == 'find' else window.analysis
    if mode == 'find':
        page.strategy.addItem('Offline provider fixture', SID)
        page.strategy.setCurrentIndex(page.strategy.findData(SID))
        page.universe.setCurrentIndex(page.universe.findData('custom'))
        page.custom_symbols.setText('SPY')
        button = page.scan
        kind, handler = 'market.discovery', market_discovery_handler
    else:
        button = page.run
        kind, handler = 'analysis.stock', stock_analysis_handler
    transport['failure'] = failure
    button.click()
    first = window.active_job_id
    assert first and not button.isEnabled()
    button.click()  # disabled: no duplicate dispatch
    worker = LocalWorker(window.runtime.service, worker_id='offline-provider', handlers={kind: handler})
    assert worker.run_once()
    failed = window.runtime.service.get(first)
    assert failed.status == JobStatus.FAILED, failed.result
    assert failed.result is None
    assert SECRET not in str(failed.error)
    window.poll_active_job()
    assert button.isEnabled() and not window.active_job_id
    assert SECRET not in page.detail.text()
    assert 'strategy failed' not in page.detail.text().lower()
    assert page.status.text() in ('Analysis could not load', 'Stock discovery could not continue')
    assert window.workflow.ticker == 'SPY' and window.workflow.strategy_id == SID
    if isinstance(failure, int):
        assert str(failure) in page.detail.text() or 'auth' in page.detail.text().lower()
    if failure == 'empty' and mode == 'analyze':
        historical, current = transport['calls']
        assert historical['path'] == current['path'] == '/v2/stocks/bars'
        for call in (historical, current):
            assert call['params']['symbols'] == ['SPY']
            assert call['params']['feed'] == ['iex']
            assert call['params']['timeframe'] == ['5Min']
        assert historical['params']['start'] < historical['params']['end']
        assert historical['params']['end'] == current['params']['start']
        assert current['params']['start'] < current['params']['end']
        assert historical['params'] != current['params']
    elif failure not in ('partial',):
        assert len(transport['calls']) == 1  # no auth/rate/network automatic retry
    before_retry = list(transport['calls'])
    if failure == 'partial':
        assert not engine._ALPACA_BAR_HISTORY_CACHE  # rejected prefix cannot poison retry
    transport['failure'] = None
    button.click()
    second = window.active_job_id
    assert second and second != first
    assert worker.run_once()
    completed = window.runtime.service.get(second)
    assert completed.status == JobStatus.COMPLETE, completed.error
    if failure == 'partial' and mode == 'find':
        historical = before_retry[-1]
        fresh_requests = transport['calls'][len(before_retry):]
        assert fresh_requests.count(historical) == 1  # one fresh retrieval of rejected range
    window.poll_active_job()
    assert button.isEnabled() and not window.active_job_id
    assert page.banner.property('state') == 'ready'
    submissions = [call for call in window.runtime.calls if call[:2] == ('POST', '/v1/jobs')]
    assert len(submissions) == 2
    assert all(call[2]['job_type'] == kind for call in submissions)
    assert window.runtime.service.get(first).status == JobStatus.FAILED
    assert window.workflow.ticker == 'SPY' and window.workflow.strategy_id == SID


@pytest.mark.parametrize('stop', ['timeout', 'cancel'])
def test_analysis_supervisor_releases_owner_and_fences_late_provider(window, monkeypatch, stop):
    from hybrid_runtime.bounded_discovery import DISCOVERY_TIMEOUTS
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    def blocked(payload, progress, cancelled):
        entered.set()
        try:
            release.wait(5)
            progress(.8, 'preparing_features', 'Late data must not replace a newer job')
            return {'symbol': 'WRONG'}
        finally:
            exited.set()
    monkeypatch.setitem(DISCOVERY_TIMEOUTS, 'analysis.stock', .05 if stop == 'timeout' else 180)
    window.analysis.symbol.setText('SPY');window.analysis.run.click()
    first = window.active_job_id
    service = window.runtime.service
    worker = LocalWorker(service, worker_id='offline', handlers={'analysis.stock': blocked})
    thread = threading.Thread(target=worker.run_once);thread.start()
    try:
        assert entered.wait(2)
        if stop == 'cancel':service.cancel(first)
        thread.join(2);assert not thread.is_alive()
        old = service.get(first)
        assert old.status in (JobStatus.FAILED, JobStatus.CANCELLED)
        window.poll_active_job();assert window.analysis.run.isEnabled()
        window.analysis.run.click();second = window.active_job_id
        worker.handlers['analysis.stock'] = lambda *a: {'symbol': 'SPY', 'summary': {}, 'candles': []}
        assert worker.run_once();window.poll_active_job()
        assert service.get(second).status == JobStatus.COMPLETE
    finally:
        release.set();thread.join(2);assert exited.wait(2)
    assert service.get(first).result is None
    assert service.get(first).status == old.status


def test_analysis_disconnect_retry_reconnects_instead_of_duplicating(window):
    window.analysis.symbol.setText('SPY');window.analysis.run.click()
    first = window.active_job_id
    window.runtime.read_error = TimeoutError('local link interrupted')
    window._analysis_unreachable_at = time.monotonic()-31
    window.poll_active_job()
    assert window.analysis.run.isEnabled()
    window.analysis.run.click()
    assert window._analysis_job_id == first
    assert len([c for c in window.runtime.calls if c[:2] == ('POST', '/v1/jobs')]) == 1
    window.runtime.read_error = None
    window.runtime.service.claim_local('offline')
    window.runtime.service.complete(first, {'symbol': 'SPY', 'summary': {}, 'candles': []}, worker_id='offline')
    window.poll_active_job()
    assert window.analysis.run.isEnabled() and not window._analysis_job_id


@pytest.mark.parametrize('bad', ['job', 'ticker', 'state'])
def test_analysis_rejects_wrong_identity_or_state(window, bad):
    window.analysis.symbol.setText('SPY');window.analysis.run.click()
    record = window.runtime.service.get(window.active_job_id).as_dict()
    record.update(status='complete', terminal=True, result={'symbol': 'SPY', 'summary': {'latest_bar_close': 999}, 'candles': []})
    if bad == 'job':record['id'] = 'unrelated-job'
    elif bad == 'ticker':record['result']['symbol'] = 'AAPL'
    else:record['status'] = 'unknown-provider-state'
    window.runtime.override = record;window.poll_active_job()
    assert window.analysis.banner.property('state') == 'error'
    assert window.analysis.price_metric.value.text() != '$999.00'


def test_failed_refresh_clears_previous_price_and_chart(window):
    page = window.analysis;page.symbol.setText('SPY')
    page.render_analysis({'symbol': 'SPY', 'summary': {'latest_bar_close': 500, 'as_of': '2026-09-04T19:55:00Z'}, 'candles': []})
    assert page.price_metric.value.text() == '$500.00'
    page.set_error('Alpaca timeout')
    assert page.price_metric.value.text() == '—'
    assert page.run.isEnabled()


def test_forming_bar_excluded_and_empty_overlap_does_not_refresh_history(tmp_path):
    now = datetime(2026,9,8,15,2,tzinfo=UTC)
    class Provider:
        rows = [bar(now-timedelta(minutes=7)), bar(now-timedelta(minutes=2))]
        def bars(self, *a, **k):return {'SPY': self.rows}
    provider = Provider();cache = PersistentMarketDataCache(tmp_path)
    first = cache.refresh(provider, symbol='SPY', now=now, max_cache_age_seconds=0)
    assert first['summary']['as_of'] == '2026-09-08T14:55:00Z'
    provider.rows=[]
    second = cache.refresh(provider, symbol='SPY', now=now+timedelta(seconds=30), max_cache_age_seconds=0)
    assert second['refreshed_at'] == first['refreshed_at']
    assert 'previous historical cache' in second['refresh_warning']
    assert second['summary']['data_age_seconds'] == 450


@pytest.mark.parametrize('invalid', ['future', 'missing-time', 'partial', 'negative', 'wrong-symbol'])
def test_malformed_candles_cannot_silently_refresh_old_cache(tmp_path, invalid):
    now = datetime(2026,9,8,15,2,tzinfo=UTC)
    row = bar(now-timedelta(minutes=10))
    if invalid == 'future':row['t'] = (now+timedelta(minutes=5)).isoformat()
    if invalid == 'missing-time':row.pop('t')
    if invalid == 'partial':row.pop('h')
    if invalid == 'negative':row.update(o=-1,h=-1,l=-2,c=-1)
    provider=SimpleNamespace(bars=lambda *a, **k: {('AAPL' if invalid=='wrong-symbol' else 'SPY'): [row]})
    with pytest.raises(MarketCacheError):PersistentMarketDataCache(tmp_path).refresh(provider,symbol='SPY',now=now,max_cache_age_seconds=0)


def test_weekend_stale_quote_and_daily_fallback_keep_exact_price_timestamp():
    friday = datetime(2026,9,4,19,55,tzinfo=UTC);sunday = datetime(2026,9,6,20,tzinfo=UTC)
    snap=snapshot(friday);snap.pop('latestTrade');snap['latestQuote']['t']=sunday.isoformat()
    provider=DesktopMarketData(SimpleNamespace(live_feed='iex',historical_feed='sip',snapshots=lambda s:{'SPY':snap}))
    provider.snapshots(['SPY'])
    evidence=provider.snapshot_provenance['SPY']
    assert evidence['price_timestamp'] == friday.isoformat()
    assert evidence['price_source'] == 'historical daily-close fallback'
    label=snapshot_label(evidence,now=sunday)
    assert '173,100s' in label and 'IEX' in label and 'not a live quote' in label
    assert provider.historical_feed=='sip'  # historical SIP does not become live SIP


def test_missing_timestamp_is_unknown_not_zero_age():
    assert 'unknown' in timestamp_label(None)
    assert '0s' not in timestamp_label(None)
    assert 'future' in timestamp_label('2099-01-01T00:00:00Z')
    assert 'unknown' in snapshot_label({'price':500,'quote_timestamp':'2026-09-08T15:00:00Z'})


def test_intraday_failure_is_not_swallowed_as_complete_discovery():
    from trading_market_discovery import _load_intraday_context
    class Provider:
        live_feed='iex';historical_feed='sip'
        def bars(self,*a,**k):raise engine.AppError('503 '+SECRET)
    with pytest.raises(MarketDataUnavailable,match='Alpaca'):
        _load_intraday_context(DesktopMarketData(Provider()),['SPY'],max_pages=1)


def test_live_and_historical_feed_failures_do_not_substitute_each_other():
    class Provider:
        live_feed='iex';historical_feed='sip'
        def snapshots(self,s):return {'SPY':snapshot(datetime.now(UTC)-timedelta(minutes=1))}
        def bars(self,*a,**k):raise HTTPError('https://data.alpaca.markets/bars',403,'denied',{},None)
    guarded=DesktopMarketData(Provider())
    assert guarded.snapshots(['SPY'])
    with pytest.raises(MarketDataUnavailable,match='403'):guarded.bars(['SPY'])
    assert guarded.live_feed=='iex' and guarded.historical_feed=='sip'


def test_cache_rejection_preserves_valid_entries_and_reuses_valid_retry(transport):
    raw = engine.AlpacaMarketData('synthetic-key', SECRET, live_feed='iex', historical_feed='iex')
    guarded = DesktopMarketData(raw)
    end = raw._history_cache_cutoff_utc()
    request = dict(start=end-timedelta(days=3), end=end, timeframe='5Min', feed='iex')
    unrelated_key = raw._history_cache_key(['AAPL'], **request, adjustment='split', max_pages=15)
    valid = {'AAPL': [bar(end-timedelta(minutes=5))]}
    raw._history_cache_put(unrelated_key, valid)
    transport['failure'] = 'partial'
    with pytest.raises(MarketDataUnavailable):guarded.bars(['SPY'], **request)
    assert list(engine._ALPACA_BAR_HISTORY_CACHE) == [unrelated_key]
    assert raw._history_cache_get(unrelated_key) == valid
    transport['failure'] = None
    assert guarded.bars(['SPY'], **request)['SPY']
    requests_after_retry = len(transport['calls'])
    assert requests_after_retry == 2
    assert guarded.bars(['SPY'], **request)['SPY']
    assert len(transport['calls']) == requests_after_retry
