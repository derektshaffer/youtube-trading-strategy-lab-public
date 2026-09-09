import json
import sqlite3
from dataclasses import replace

import pytest

from ai_review.contracts import CHECKS, REQUIRED, TRIVIAL, ReviewError, digest, freeze
from ai_review.fixtures import packet, source, validator, adapters, response, review_response
from ai_review.gate import ReviewGate
from ai_review.providers import Route, GeminiReviewerAdapter, GeminiSpecialistAdapter, FixtureTransport
from ai_review.storage import ReviewStorage, read_summary
from hybrid_runtime.storage import HybridStore, InvalidJobTransition
from hybrid_runtime.contracts import JobRequest, ExecutionTarget, JobStatus


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def reject(*args, **kwargs):
        raise AssertionError('Offline acceptance must not access network')
    monkeypatch.setattr(socket, 'create_connection', reject)


@pytest.fixture
def setup(tmp_path):
    store = HybridStore(tmp_path / 'hybrid.sqlite3')
    storage = ReviewStorage(store)
    primary, reviewer = adapters()
    return store, storage, ReviewGate(storage, primary, reviewer, validator)


def run(gate, p=None):
    p = p or packet()
    gate.submit(p)
    return gate.run(p['artifact_id'])


def advance(gate, p=None, **kwargs):
    p = p or packet()
    return gate.advance(p['artifact_id'], packet_hash=digest(p), checkpoint=p['checkpoint'],
                        target=kwargs.get('target', 'next_validation_stage'))


@pytest.mark.parametrize('checkpoint', sorted(REQUIRED))
def test_required_checkpoint_independent_fresh_context(setup, checkpoint):
    _, storage, gate = setup
    p = packet(checkpoint)
    s = run(gate, p)['snapshot']
    assert s['state'] == 'CLEARED_FOR_NEXT_VALIDATION_STAGE'
    req = s['review']['request']
    assert req['fresh_context'] is True
    assert req['packet'] == p and req['primary']['output'] == s['primary']['output']
    assert req['route']['provider'] == 'gemini'
    assert s['primary']['request']['route']['provider'] == 'openai'
    assert 'look-ahead' in req['prompt'] and 'corporate-action' in req['prompt']
    assert advance(gate, p)['kind'] == 'advanced'


@pytest.mark.parametrize('checkpoint', sorted(TRIVIAL))
def test_trivial_skips_second_model(setup, checkpoint):
    _, storage, gate = setup
    gate.reviewer = None
    result = run(gate, packet(checkpoint))
    assert result['kind'] == 'review_not_required'
    assert result['snapshot']['review'] is None
    with storage.store._reader() as conn:
        assert conn.execute('SELECT count(*) FROM ai_review_calls').fetchone()[0] == 1


def test_distinct_provider_enforced(setup):
    _, storage, gate = setup
    with pytest.raises(ReviewError, match='distinct'):
        ReviewGate(storage, gate.primary, gate.primary, validator)


def test_material_disagreement_survives_restart_and_blocks(setup):
    store, storage, gate = setup
    gate.primary, gate.reviewer = adapters(lambda r: review_response(r, 'material'))
    before = run(gate)
    reopened = ReviewStorage(HybridStore(store.path))
    after = reopened.latest('fixture-hypothesis')
    assert before == after
    assert after['snapshot']['state'] == 'AI_REVIEW_DISAGREEMENT'
    objection = after['snapshot']['open_objections'][0]
    assert objection['primary_route']['provider'] == 'openai'
    assert objection['reviewer_route']['provider'] == 'gemini'
    assert objection['source_hashes'] and objection['review_hash']
    with pytest.raises(ReviewError, match='blocked'):
        advance(gate)


@pytest.mark.parametrize('field', ['evidence', 'specification', 'assumptions', 'deterministic_context'])
def test_changed_inputs_invalidate_clearance(setup, field):
    _, _, gate = setup
    run(gate)
    changed = packet()
    if field == 'evidence': changed[field].append(source('new', 'New invented receipt'))
    elif field == 'specification': changed[field] += ' Revised rule.'
    elif field == 'assumptions': changed[field].append('Another assumption')
    else: changed[field]['note'] = 'changed'
    saved = gate.submit(changed)
    assert saved['snapshot']['review'] is None
    with pytest.raises(ReviewError): advance(gate)
    with pytest.raises(ReviewError): advance(gate, changed)
    gate.run(changed['artifact_id'])
    assert advance(gate, changed)['kind'] == 'advanced'


def test_material_cannot_be_erased_by_agreement_or_spec_edit(setup):
    _, _, gate = setup
    gate.primary, gate.reviewer = adapters(lambda r: review_response(r, 'material'))
    original = run(gate)
    gate.primary, gate.reviewer = adapters()
    p = packet(); p['specification'] += ' Claimed resolved.'
    revised = run(gate, p)
    assert revised['snapshot']['state'] == 'AI_REVIEW_DISAGREEMENT'
    assert revised['snapshot']['review']['output']['classification'] == 'agree'
    with pytest.raises(ReviewError): advance(gate, p)
    # Even relabelling the old source cannot count as new evidence.
    p['evidence'].append(source('relabelled'))
    p['resolutions'] = [dict(objection_id=original['snapshot']['open_objections'][0]['id'],
        evidence_id='relabelled', sha256=p['evidence'][-1]['sha256'])]
    assert run(gate, p)['snapshot']['state'] == 'AI_REVIEW_DISAGREEMENT'


def test_linked_new_evidence_then_fresh_review_resolves(setup):
    _, storage, gate = setup
    gate.primary, gate.reviewer = adapters(lambda r: review_response(r, 'material'))
    old = run(gate)
    objection = old['snapshot']['open_objections'][0]
    p = packet(); p['evidence'].append(source('receipt', 'New invented receipt timestamp check'))
    p['resolutions'] = [dict(objection_id=objection['id'], evidence_id='receipt', sha256=p['evidence'][-1]['sha256'])]
    frozen = gate.submit(p)
    assert frozen['snapshot']['state'] == 'AI_REVIEW_DISAGREEMENT'
    gate.primary, gate.reviewer = adapters()
    updated = gate.run(p['artifact_id'])
    assert updated['snapshot']['state'] == 'CLEARED_FOR_NEXT_VALIDATION_STAGE'
    assert updated['snapshot']['resolved_objections'][0]['disposition'] == 'resolved_after_new_review'
    assert storage.history(p['artifact_id'])[3]['snapshot']['open_objections']
    assert advance(gate, p)['kind'] == 'advanced'


def test_deterministic_resolution_requires_link_and_new_review(setup):
    _, _, gate = setup
    gate.primary, gate.reviewer = adapters(lambda r: review_response(r, 'material'))
    old = run(gate)
    check = dict(objection_id=old['snapshot']['open_objections'][0]['id'], result='passed', evidence_hash=digest('fixture verification'))
    p = packet(); p['resolutions'] = [dict(objection_id=check['objection_id'], verification_hash=digest(check))]
    def verified(p):
        return {**validator(p), 'verifications': [check]}
    gate.validator = verified
    gate.primary, gate.reviewer = adapters()
    assert run(gate, p)['snapshot']['state'] == 'CLEARED_FOR_NEXT_VALIDATION_STAGE'


@pytest.mark.parametrize('check', sorted(CHECKS))
def test_deterministic_failure_beats_consensus(setup, check):
    _, _, gate = setup
    def failed(p):
        r = validator(p); r['checks'][check] = 'failed'; return r
    gate.validator = failed
    s = run(gate)['snapshot']
    assert s['review']['output']['classification'] == 'agree'
    assert s['state'] == 'DETERMINISTIC_VALIDATION_FAILED'
    with pytest.raises(ReviewError): advance(gate)


def test_context_cannot_claim_deterministic_success(setup):
    _, _, gate = setup
    gate.validator = None
    p = packet(); p['deterministic_context'] = {k: 'passed' for k in CHECKS}
    assert run(gate, p)['snapshot']['state'] == 'DETERMINISTIC_VALIDATION_PENDING'
    with pytest.raises(ReviewError): advance(gate, p)


@pytest.mark.parametrize('failure', ['disabled', 'unavailable', 'rate_limited', 'malformed', 'stale', 'version', 'schema', 'conflict'])
def test_reviewer_failures_preserve_primary_and_job(setup, failure):
    store, storage, gate = setup
    def broken(req):
        if failure in {'unavailable', 'rate_limited'}: raise RuntimeError('secret must not enter audit')
        if failure == 'malformed': return response('not a review')
        r = review_response(req)
        if failure == 'version': r['model_version'] = 'wrong'
        o = json.loads(r['text'])
        if failure == 'stale': o['request_hash'] = 'old'
        if failure == 'schema': o['unknown'] = True
        if failure == 'conflict': o['classification'] = 'material'
        r['text'] = json.dumps(o)
        return r
    gate.primary, gate.reviewer = adapters(broken)
    if failure == 'disabled': gate.reviewer = None
    record = run(gate)
    s = record['snapshot']
    assert s['primary']['output']['proposal']
    assert s['state'] == 'INDEPENDENT_REVIEW_PENDING'
    assert store.get_job(record['job_id']).status == JobStatus.RETRY_WAIT
    assert 'secret must not' not in json.dumps(storage.history('fixture-hypothesis'))
    with pytest.raises(ReviewError): advance(gate)
    unrelated, _ = store.create_or_get_job(JobRequest('system.health'), execution_target=ExecutionTarget.LOCAL, route_reason='test')
    assert store.claim_next('fixture-worker').id == unrelated.id


def test_stale_clearance_blocked_at_transition(setup):
    _, storage, gate = setup
    run(gate)
    storage.clock = lambda: '2030-01-01T00:00:00+00:00'
    with pytest.raises(ReviewError): advance(gate)
    assert storage.latest('fixture-hypothesis')['snapshot']['state'] == 'INDEPENDENT_REVIEW_PENDING'


def test_configuration_change_requires_new_review(setup):
    _, _, gate = setup
    run(gate)
    gate.reviewer.route = replace(gate.reviewer.route, configuration_version='changed')
    with pytest.raises(ReviewError, match='configuration'): advance(gate)


def test_deterministic_change_invalidates_review_at_transition(setup):
    _, storage, gate = setup
    run(gate)
    gate.validator = lambda p: {**validator(p), 'validator_version': 'fixture-validator-v2'}
    with pytest.raises(ReviewError): advance(gate)
    assert storage.latest('fixture-hypothesis')['snapshot']['review'] is None


@pytest.mark.parametrize('target', ['backtest', 'runner', 'paper', 'live', 'orders', 'data_certificate', 'holdout'])
def test_consensus_never_authorizes_runner_or_certification(setup, target):
    _, _, gate = setup
    s = run(gate)['snapshot']
    assert s['execution_authority'] == 'none' and not s['runner_authorized']
    assert not s['data_certification_authority']
    with pytest.raises(ReviewError, match='not_authorized'): advance(gate, target=target)


def test_direct_transition_bypass_and_trivial_relabel_rejected(setup):
    store, _, gate = setup
    record = run(gate)
    for status in ('complete', 'validating', 'queued', 'saving'):
        with pytest.raises(InvalidJobTransition, match='review gate'):
            store.transition_job(record['job_id'], status, stage='cleared', result={'agreed': True})
    p = packet('formatting', 'trivial')
    run(gate, p)
    with pytest.raises(ReviewError, match='checkpoint'):
        gate.advance('trivial', packet_hash=digest(p), checkpoint='final_strategy_spec', target='next_validation_stage')
    p['checkpoint'] = 'final_strategy_spec'
    with pytest.raises(ReviewError, match='checkpoint'): gate.submit(p)


def test_replay_audit_and_append_only(setup):
    store, storage, gate = setup
    record = run(gate)
    advance(gate)
    reopened = ReviewStorage(HybridStore(store.path))
    assert reopened.history('fixture-hypothesis') == storage.history('fixture-hypothesis')
    assert read_summary(store.path)[0]['state'] == 'CLEARED_FOR_NEXT_VALIDATION_STAGE'
    with pytest.raises(sqlite3.IntegrityError, match='append_only'):
        with store._transaction(immediate=True) as conn:
            conn.execute("UPDATE ai_review_events SET body='{}'")
    with pytest.raises(sqlite3.IntegrityError, match='append_only'):
        with store._transaction(immediate=True) as conn:
            conn.execute('DELETE FROM ai_review_events')


def test_compare_and_swap_rejects_stale_writer(setup):
    _, storage, gate = setup
    before = gate.submit(packet())
    gate.run('fixture-hypothesis')
    from ai_review.storage import _WORKFLOW_WRITE
    with pytest.raises(ReviewError, match='concurrently'):
        storage.append('fixture-hypothesis', before['event_hash'], 'packet_frozen', before['snapshot'], authority=_WORKFLOW_WRITE)
    with pytest.raises(ReviewError, match='gate_write_required'):
        storage.append('fixture-hypothesis', before['event_hash'], 'advanced', before['snapshot'])


def test_full_input_configuration_cache_survives_restart(setup):
    store, storage, gate = setup
    first = run(gate)
    second = gate.run('fixture-hypothesis')
    assert first['snapshot']['review']['call_id'] == second['snapshot']['review']['call_id']
    other = ReviewGate(ReviewStorage(HybridStore(store.path)), *adapters(), validator)
    assert other.run('fixture-hypothesis')['snapshot']['review']['call_id'] == first['snapshot']['review']['call_id']
    with store._reader() as conn:
        assert conn.execute('SELECT count(*) FROM ai_review_calls').fetchone()[0] == 2


def test_crash_after_primary_preserves_work_and_does_not_retry_inflight(setup):
    store, storage, gate = setup
    def crash(req): raise KeyboardInterrupt()
    gate.primary, gate.reviewer = adapters(crash)
    gate.submit(packet())
    with pytest.raises(KeyboardInterrupt): gate.run('fixture-hypothesis')
    reopened = ReviewGate(ReviewStorage(HybridStore(store.path)), *adapters(), validator)
    s = reopened.run('fixture-hypothesis')['snapshot']
    assert s['primary'] and s['state'] == 'INDEPENDENT_REVIEW_PENDING'
    assert 'provider_call_in_flight_or_interrupted' in s['errors']


def test_cancelled_job_stays_cancelled(setup):
    store, _, gate = setup
    r = gate.submit(packet())
    store.request_cancel(r['job_id'])
    with pytest.raises(ReviewError, match='cancelled'): gate.run('fixture-hypothesis')


def test_missing_and_tampered_sources_rejected(setup):
    _, _, gate = setup
    p = packet(); p['evidence'][0]['content'] = 'tampered'
    with pytest.raises(ReviewError, match='hash'): gate.submit(p)
    p = packet(); p['evidence'] = []
    with pytest.raises(ReviewError): gate.submit(p)


@pytest.mark.parametrize('purpose', ['review', 'video', 'document'])
def test_new_gemini_routes_no_paid_fallback_or_live_transport(setup, purpose, monkeypatch):
    _, storage, _ = setup
    monkeypatch.setenv('GEMINI_PAID_API_KEY', 'DO-NOT-READ')
    cls = GeminiReviewerAdapter if purpose == 'review' else GeminiSpecialistAdapter
    called = []
    for tier in ('unconfigured', 'free'):
        adapter = cls(Route('gemini', 'explicit-model', 'explicit-version', mode='live', tier=tier),
                      FixtureTransport(lambda x: called.append(x)))
        with pytest.raises(ReviewError): adapter.invoke({'purpose': purpose}, storage)
    assert called == []
    with pytest.raises(ReviewError): Route('gemini', 'm', 'v', tier='paid')


def test_budget_persists_and_concurrency_reservation_blocks(setup):
    store, storage, gate = setup
    gate.primary, gate.reviewer = adapters(max_calls=1)
    run(gate)
    p = packet(artifact_id='another')
    r = run(gate, p)
    assert 'daily_call_budget_exhausted' in r['snapshot']['errors']
    from dataclasses import asdict
    route = asdict(Route('gemini', 'm', 'v'))
    request = {'route': route, 'test': 1}
    reservation = storage.reserve_call(digest(request), route, request)
    with pytest.raises(ReviewError, match='in_flight'):
        ReviewStorage(HybridStore(store.path)).reserve_call(digest(request), route, request)
    storage.fail_call(reservation)


@pytest.mark.parametrize('options', [{'retries': 1}, {'concurrency': 2}, {'max_calls': 11}, {'max_output_tokens': 2049}, {'max_input_bytes': 65537}])
def test_cannot_raise_hard_caps(options):
    with pytest.raises(ReviewError): Route('gemini', 'm', 'v', **options)


def test_input_budget_does_not_call_provider(setup):
    _, _, gate = setup
    gate.primary, gate.reviewer = adapters(max_input_bytes=50)
    s = run(gate)['snapshot']
    assert s['primary'] is None and 'input_budget_exceeded' in s['errors']


def test_mutated_provider_identity_cannot_substitute_second_openai(setup):
    _, _, gate = setup
    run(gate)
    gate.reviewer.route = replace(gate.reviewer.route, provider='openai')
    with pytest.raises(ReviewError, match='distinct'): gate.run('fixture-hypothesis')
    with pytest.raises(ReviewError, match='distinct'): advance(gate)


def test_receipt_forgery_rejected(setup):
    _, storage, gate = setup
    record = run(gate)
    receipt = freeze(record['snapshot']['review'])
    receipt['output']['summary'] = 'Forged agreement'
    with pytest.raises(ReviewError, match='receipt_mismatch'): storage.verify_receipt(receipt)
    receipt = freeze(record['snapshot']['review']); receipt['call_id'] = 'nonexistent'
    with pytest.raises(ReviewError, match='receipt_required'): storage.verify_receipt(receipt)
