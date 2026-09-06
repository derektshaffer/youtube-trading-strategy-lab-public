"""Certified saved evidence never invokes unpublished execution paths."""
from copy import deepcopy
import pytest
pytest.importorskip("PySide6")
from test_search_monitor import setup
from test_desktop_search_monitor import app, window, panel
from test_readonly_validation_results import setup as saved_setup, evidence, SID


@pytest.mark.parametrize('case', ['complete', 'timeout', 'missing'])
def test_certified_saved_validation_is_read_only_and_fail_closed(window, app, saved_setup, monkeypatch, case):
    import time
    from PySide6.QtWidgets import QPushButton
    from hybrid_runtime.contracts import JobStatus
    from hybrid_runtime.saved_validation_reader import read_saved_validation
    s = saved_setup
    if case == 'timeout':
        s.job.id = 'cc39e172544844d8b654135e3479298c'
        s.request.update(id=s.job.id, key='local:' + s.job.id)
        s.link['local_job_id'] = s.job.id
        s.job.status = JobStatus.FAILED
        s.job.stage = 'failed'
        s.job.result = None
        s.job.error = {'kind': 'execution_timeout', 'message': 'Execution timed out; no strategy verdict.'}
        s.remote['status'] = 'failed'
    elif case == 'missing':
        s.job.result = {}
    before = (deepcopy(vars(s.job)), deepcopy(s.library), deepcopy(s.link))
    calls = []
    def request(method, path, body=None, **kwargs):
        calls.append((method, path, deepcopy(body)))
        assert (method, path) == ('POST', '/v1/saved-validations/result')
        return read_saved_validation(s.worker, body)
    monkeypatch.setattr(window.runtime, 'request_json', request)
    p = panel(window, window.results)
    state = 'failed' if case == 'timeout' else 'complete'
    p.render({'rows': [{**s.request, 'symbol': 'SPY', 'kind': 'Strategy Validation',
              'target': 'Cloud', 'status': state, 'stage': state, 'progress': 1,
              'updated_at': s.job.updated_at, 'run_id': s.job.payload['run_id'],
              'can_cancel': False, 'message': 'Saved terminal evidence'}]})
    p.table.selectRow(0)
    window.saved_validation.update_buttons()
    button = next(b for owner, b in window.saved_validation.buttons if owner is p)
    assert button.isEnabled() and not p.cancel.isEnabled()
    assert p.selected()['id'] == s.job.id and s.job.payload['run_id'] in p.detail.text()
    button.click()
    deadline = time.monotonic() + 4
    while window.saved_validation.busy and time.monotonic() < deadline:
        app.processEvents()
        window.saved_validation.tick()
        time.sleep(.01)
    assert not window.saved_validation.busy
    controller = window.saved_validation
    if case == 'missing':
        assert getattr(controller, 'dialog', None) is None
        assert 'Saved validation unavailable:' in window.top_status.text()
    else:
        assert controller.dialog.isVisible()
        assert s.job.id in controller.page.identity.text() and SID in controller.page.identity.text()
        assert [b.text() for b in controller.dialog.findChildren(QPushButton)] == ['Close']
        assert not hasattr(controller.page, 'run_requested')
        assert not hasattr(controller.page, 'backtest_requested')
        if case == 'timeout':
            assert 'failed (not running)' in controller.page.verdict.text()
            assert 'No strategy-validation verdict' in controller.page.verdict.text()
            assert not hasattr(controller.page, 'strength')
        else:
            assert 'FAILED (execution completed)' in controller.page.verdict.text()
            assert '13/100' in controller.page.strength.text()
        controller.dialog.close()
    for _ in range(3):
        controller.tick()
        app.processEvents()
    assert calls == [('POST', '/v1/saved-validations/result', {**s.request, 'binding': None})]
    assert (vars(s.job), s.library, s.link) == before
    for forbidden in s.prohibited.values():
        forbidden.assert_not_called()
    assert window.monitor_fixture.cloud.write_count == 0
    assert window.monitor_fixture.cloud.dispatches == []
