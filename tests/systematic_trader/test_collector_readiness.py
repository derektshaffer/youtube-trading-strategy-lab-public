import os
import subprocess
import threading

import pytest

from systematic_trader.collector_power import CollectorAwake
from systematic_trader.collection_service import CollectionService, status
from systematic_trader.collection_transport import reconnecting_stream
from systematic_trader.events import ContractError
from systematic_trader.service import CaptureFailure
from systematic_trader.prospective_fixtures import ID, OPEN, CLOSE


class Child:
    pid = 12345
    terminated = False
    killed = False
    failed = False

    def poll(self):
        return 1 if self.failed else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout):
        if not self.killed and self.failed:
            raise subprocess.TimeoutExpired('fixture', timeout)
        return 0


def test_sleep_assertion_scoped_and_released_on_exception(monkeypatch):
    child = Child(); calls = []
    def spawn(args, **kw):
        calls.append((args, kw)); return child
    monkeypatch.setattr('systematic_trader.collector_power.subprocess.Popen', spawn)
    with pytest.raises(ValueError), CollectorAwake():
        raise ValueError('fixture work failed')
    assert calls[0][0] == ['/usr/bin/caffeinate', '-i', '-s', '-w', str(os.getpid())]
    assert child.terminated
    assert calls[0][1]['stdin'] == subprocess.DEVNULL


def test_sleep_assertion_loss_fails_closed(monkeypatch):
    child = Child()
    monkeypatch.setattr('systematic_trader.collector_power.subprocess.Popen', lambda *a, **k: child)
    with CollectorAwake() as power:
        child.failed = True
        with pytest.raises(ContractError, match='collector_sleep_assertion_lost'):
            power.check()


def test_disabled_assertion_has_no_process(monkeypatch):
    def forbidden(*a, **k):raise AssertionError('unexpected child')
    monkeypatch.setattr('systematic_trader.collector_power.subprocess.Popen', forbidden)
    with CollectorAwake(enabled=False) as power:
        power.check(); assert power.process is None


def registered(path):
    service = CollectionService(path)
    service.register('day', OPEN, CLOSE, {'FIXTURE': ID}, provenance={'source': 'fixture-calendar'})
    return service


class Stop:
    def __init__(self, halt_cooldown=False):
        self.waits = []; self.halt_cooldown = halt_cooldown
    def is_set(self):return False
    def wait(self, seconds):
        self.waits.append(seconds)
        return seconds == 300 and self.halt_cooldown


def test_persistent_transport_recovers_after_cooldown_without_clearing_gaps(tmp_path):
    service = registered(tmp_path); stop = Stop(); calls = []
    def runner(*args):
        calls.append(1)
        if len(calls) <= 6:raise ConnectionError('fixture offline')
    reconnecting_stream(service, 'day', 'alpaca_iex', ['fixture'], ['FIXTURE'], stop,
                        runner=runner, clock=lambda: OPEN, persistent=True)
    assert len(calls) == 7 and stop.waits == [1, 2, 4, 8, 16, 300]
    result = status(tmp_path, OPEN)
    assert result['detected_gaps'] == 7
    assert result['completeness'] == 'Source unavailable'
    assert result['certification'] == 'Not certifiable' and not result['orders_enabled']
    assert CollectionService(tmp_path).replay()['evidence_hash'] == service.replay()['evidence_hash']


def test_stop_interrupts_cooldown(tmp_path):
    service = registered(tmp_path); stop = Stop(True); calls = []
    def runner(*args):calls.append(1); raise ConnectionError('fixture offline')
    reconnecting_stream(service, 'day', 'alpaca_iex', ['fixture'], ['FIXTURE'], stop,
                        runner=runner, persistent=True)
    assert len(calls) == 6 and stop.waits[-1] == 300


@pytest.mark.parametrize('status_code', [401, 403, 429])
def test_http_handshake_rejection_is_not_retried(tmp_path, status_code):
    from websockets.exceptions import InvalidStatus
    from websockets.http11 import Response
    from websockets.datastructures import Headers
    service = registered(tmp_path); stop = Stop(); calls = []
    def runner(*args):
        calls.append(1)
        raise InvalidStatus(Response(status_code, 'fixture rejection', Headers()))
    reconnecting_stream(service, 'day', 'alpaca_iex', ['fixture'], ['FIXTURE'], stop,
                        runner=runner, persistent=True)
    assert calls == [1] and stop.waits == []
    assert status(tmp_path)['sources']['alpaca_iex']['reason'].endswith('no_retry')


@pytest.mark.parametrize('error', [CaptureFailure('provider rejection'), ContractError('integrity')])
def test_persistent_mode_never_retries_provider_rejection_or_integrity(tmp_path, error):
    service = registered(tmp_path); calls = []
    def runner(*args):calls.append(1); raise error
    if isinstance(error, ContractError):
        with pytest.raises(ContractError):
            reconnecting_stream(service, 'day', 'alpaca_iex', ['fixture'], ['FIXTURE'],
                                threading.Event(), runner=runner, persistent=True)
    else:
        reconnecting_stream(service, 'day', 'alpaca_iex', ['fixture'], ['FIXTURE'],
                            threading.Event(), runner=runner, persistent=True)
    assert calls == [1]


def test_power_failure_stops_service_before_reference_access(tmp_path, monkeypatch):
    import systematic_trader.collection_runtime as runtime
    class Power:
        process = None
        def __init__(self, **kw):pass
        def __enter__(self):return self
        def check(self):raise ContractError('collector_sleep_assertion_lost')
        def __exit__(self, *args):pass
    monkeypatch.setattr(runtime, 'CollectorAwake', Power)
    def forbidden(*a, **k):raise AssertionError('source accessed after assertion failure')
    with pytest.raises(ContractError, match='collector_sleep_assertion_lost'):
        runtime.serve(tmp_path, cycle=forbidden, keep_awake=True, clock=lambda: OPEN)
    assert status(tmp_path, OPEN)['state'] == 'Stopped'
