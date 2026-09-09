"""Trusted certificate verification and single-use, exact-scope runner handoff.

No arbitrary executor callback is accepted. Performance uses only a registered
context after a durable single-use claim. Historical V4 remains rejected.
"""
from uuid import uuid4
from weakref import WeakKeyDictionary

from .certification import CertificateAuthority, verify_envelope
from .certification_inputs import validate_inputs, context, freeze, now
from .events import ContractError, digest

_HANDOFF=object()
_PERFORMANCE_CONTEXTS=WeakKeyDictionary()


class _VerifiedInput:
    def __init__(self, authority, data, cells, experiment_hash):
        if authority is not _HANDOFF:raise ContractError('verified_runner_handoff_required')
        self._data=freeze(data);self._cells=freeze(cells);self._experiment_hash=experiment_hash


def _take_performance_handoff(handoff):
    if type(handoff) is not _VerifiedInput or handoff not in _PERFORMANCE_CONTEXTS:
        raise ContractError('verified_runner_performance_context_required')
    saved=_PERFORMANCE_CONTEXTS.pop(handoff)
    if (handoff._data!=saved['data'] or handoff._cells!=saved['data']['cells'] or
        handoff._experiment_hash!=saved['experiment']['experiment_hash']):
        raise ContractError('performance_context_mutated')
    return freeze(saved)


def _scope_probe(handoff):
    if type(handoff) is not _VerifiedInput:raise ContractError('verified_runner_handoff_required')
    if handoff._data['origin']!='synthetic-fixture':
        raise ContractError('historical_performance_backend_requires_validated_intake')
    scope={(c['symbol'],c['session']) for c in handoff._cells}
    rows=[r for r in handoff._data['normalized'] if (r['symbol'],r['session']) in scope]
    return dict(kind='synthetic_scope_probe',cells=handoff._cells,consumed_rows=len(rows),
        consumed_hash=digest(rows),experiment_hash=handoff._experiment_hash,
        performance_calculated=False,execution_authority='none',orders_enabled=False)


class RunnerVerifier:
    """Trust configuration is established at service construction, never per request."""
    def __init__(self, authority_directory):
        self._authority=CertificateAuthority(authority_directory)
        self._audit=self._authority.audit

    def _verify(self, store, certificate, experiment, evidence_directory, cells):
        body=verify_envelope(self._audit,certificate)
        self._authority._active(store,certificate)
        if body['context']!=context():raise ContractError('certificate_code_or_configuration_stale')
        if body['status']!='ADMITTED' or body['admission'].get('admitted') is not True:
            raise ContractError('deterministic_admission_rejected')
        if not isinstance(experiment,dict) or set(experiment)!={'specification','certificate_id','preregistered_at','version','experiment_hash'}:
            raise ContractError('experiment_missing_or_malformed')
        if experiment['experiment_hash']!=digest({k:v for k,v in experiment.items() if k!='experiment_hash'}):
            raise ContractError('experiment_hash_mismatch')
        saved=[r for r in self._audit.records(store,'experiment_frozen') if r['body']==experiment]
        if len(saved)!=1 or experiment['certificate_id']!=certificate['certificate_id']:
            raise ContractError('registered_experiment_mismatch')
        spec=experiment['specification']
        if (spec['designation']!='development_only' or spec['holdout']!=body['holdout']
                or cells!=spec['cells'] or cells!=body['cells'] or not cells):
            raise ContractError('runner_cell_scope_or_holdout_mismatch')
        admission=[r for r in self._audit.records(store,'admission_completed') if r['hash']==body['admission_record_hash']]
        if len(admission)!=1:raise ContractError('deterministic_validation_record_missing')
        # Load once, deterministically revalidate, and pass this in-memory snapshot
        # to the consumer. It must never reopen caller-controlled price paths.
        data=validate_inputs(evidence_directory,self._audit.anchor['domain'])
        if data!=admission[0]['body']['data'] or data['dataset_hash']!=body['dataset_hash']:
            raise ContractError('certified_evidence_or_revision_changed')
        if data['normalized_hash']!=body['normalized_hash'] or data['cells']!=cells:
            raise ContractError('certified_replay_or_cells_mismatch')
        # Cross-certificate/data aliases cannot reset an execution claim.
        if any(r['body']['data_reuse_key']==data['data_reuse_key'] for r in self._audit.records(store,'execution_claimed')):
            raise ContractError('experiment_already_consumed_or_interrupted')
        return _VerifiedInput(_HANDOFF,data,cells,experiment['experiment_hash'])

    def _attempt(self, certificate, experiment, evidence_directory, cells, *, consume):
        attempt=uuid4().hex
        with self._audit.locked() as store:
            self._audit.append(store,'runner_verification_attempt',attempt,dict(at=now(),
                certificate_hash=digest(certificate),experiment_hash=digest(experiment),cells=freeze(cells),consume=consume))
            try:
                handoff=self._verify(store,certificate,experiment,evidence_directory,cells)
                self._audit.append(store,'runner_verification_passed','verified:'+attempt,
                    dict(at=now(),certificate_id=certificate['certificate_id'],experiment_hash=experiment['experiment_hash'],
                         dataset_hash=certificate['body']['dataset_hash'],cells=freeze(cells),runner_authorization='READY'))
                if not consume:
                    return dict(verified=True,certificate_id=certificate['certificate_id'],
                        experiment_hash=experiment['experiment_hash'],scope=freeze(cells),
                        runner_authorization='READY',execution_authority='none',orders_enabled=False)
                self._audit.append(store,'execution_claimed','claim:'+handoff._data['data_reuse_key'],
                    dict(at=now(),dataset_hash=certificate['body']['dataset_hash'],
                         data_reuse_key=handoff._data['data_reuse_key'],
                         certificate_id=certificate['certificate_id'],experiment_hash=experiment['experiment_hash']))
                if experiment['specification']['consumer']=='performance-consumer-v1':
                    from .performance_consumer import consume
                    _PERFORMANCE_CONTEXTS[handoff]=freeze(dict(data=handoff._data,certificate=certificate,
                        experiment=experiment,run_timestamp=now()))
                    try:result=consume(handoff)
                    finally:_PERFORMANCE_CONTEXTS.pop(handoff,None)
                else:result=_scope_probe(handoff)
                self._audit.append(store,'execution_finished','finished:'+attempt,result)
                return result
            except Exception as exc:
                reason=str(exc) if isinstance(exc,ContractError) else 'runner_verification_failed'
                claims=[r for r in self._audit.records(store,'execution_claimed')
                    if isinstance(experiment,dict) and r['body']['experiment_hash']==experiment.get('experiment_hash')]
                finished=[r for r in self._audit.records(store,'execution_finished')
                    if r['body'].get('experiment_id',r['body'].get('experiment_hash'))==experiment.get('experiment_hash')] if isinstance(experiment,dict) else []
                if claims and not finished:
                    self._audit.append(store,'execution_failed','failed:'+attempt,dict(at=now(),state='PERFORMANCE_FAILED',
                        experiment_id=experiment.get('experiment_hash'),reason=reason,claim_hash=claims[0]['hash'],
                        retry_allowed=False,execution_authority='none',orders_enabled=False))
                self._audit.append(store,'runner_refused','runner-refused:'+attempt,
                    dict(at=now(),reason=reason,runner_authorization='BLOCKED',execution_authority='none'))
                raise ContractError(reason) from None

    def verify(self, certificate, experiment, evidence_directory, cells):
        return self._attempt(certificate,experiment,evidence_directory,cells,consume=False)

    def run(self, certificate, experiment, evidence_directory, cells):
        return self._attempt(certificate,experiment,evidence_directory,cells,consume=True)
