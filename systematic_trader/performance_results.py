"""Read-only sealed result loading/replay and downstream Independent Review packets."""
import hashlib
from .certificate_audit import CertificateAudit
from .certification import verify_envelope
from .certification_inputs import context, freeze
from .events import ContractError, canonical_json, digest


def load_result(authority_directory, experiment_id):
    audit=CertificateAudit(authority_directory);records=audit.replay()['records']
    results=[r['body'] for r in records if r['kind']=='execution_finished' and r['body'].get('experiment_id')==experiment_id]
    if len(results)!=1:raise ContractError('performance_result_missing_or_incomplete_claim')
    result=results[0]
    if result['result_hash']!=digest({k:v for k,v in result.items() if k!='result_hash'}):raise ContractError('performance_result_hash_invalid')
    p=result['provenance'];cert=p['certificate'];exp=p['experiment'];data=p['data']
    body=verify_envelope(audit,cert)
    def exists(kind, value):return sum(r['kind']==kind and r['body']==value for r in records)==1
    if not exists('certificate_issued',cert) or not exists('experiment_frozen',exp):raise ContractError('performance_registration_missing')
    admission=[r for r in records if r['kind']=='admission_completed' and r['hash']==body['admission_record_hash']]
    if len(admission)!=1 or admission[0]['body']['data']!=data:raise ContractError('performance_admission_provenance_mismatch')
    if (exp['experiment_hash']!=experiment_id or result['certificate_id']!=cert['certificate_id'] or
        exp['certificate_id']!=cert['certificate_id'] or result['evidence_replay_hash']!=data['normalized_hash'] or
        body['dataset_hash']!=data['dataset_hash'] or result['authorized_cells']!=body['cells'] or
        result['frozen_specification_hash']!=digest(exp['specification'])):
        raise ContractError('performance_result_binding_mismatch')
    claims=[r for r in records if r['kind']=='execution_claimed' and r['body']['experiment_hash']==experiment_id]
    finish=next(r for r in records if r['kind']=='execution_finished' and r['body']==result)
    if len(claims)!=1 or claims[0]['seq']>=finish['seq'] or claims[0]['body']['data_reuse_key']!=data['data_reuse_key']:
        raise ContractError('performance_execution_claim_mismatch')
    return freeze(result)


def replay_result(authority_directory, experiment_id):
    """Exact completed fixture replay, no fresh claim, authorization or variant."""
    result=load_result(authority_directory,experiment_id);p=result['provenance']
    if p['data']['context']!=context():raise ContractError('performance_replay_requires_exact_code_and_configuration')
    if p['data']['origin']!='synthetic-fixture':raise ContractError('historical_replay_requires_validated_intake')
    from .performance_inputs import normalize_receipts
    if [normalize_receipts(p['data']['raw_evidence'])]!=p['data']['normalized']:
        raise ContractError('performance_raw_normalization_replay_mismatch')
    from .performance_consumer import _compute, _COMPUTE_TOKEN
    output=_compute(p,_token=_COMPUTE_TOKEN)
    if output!=result['performance']:raise ContractError('performance_replay_mismatch')
    return dict(experiment_id=experiment_id,result_hash=result['result_hash'],performance_hash=digest(output),
        exact_match=True,execution_authority='none',orders_enabled=False,new_experiment=False)


def result_packet(authority_directory, experiment_id):
    from ai_review.contracts import validate_packet
    result=load_result(authority_directory,experiment_id)
    # Keep the review interface within the existing gate's hard input budget.
    # The complete immutable artifact remains in the sealed registry; this exact
    # projection includes every trade/fill, never a favorable sample.
    output=result['performance']
    projection=dict(result_hash=result['result_hash'],provenance_hash=digest(result['provenance']),
        performance_hash=digest(output),code_hash=result['code_version']['code_hash'],
        policy_hash=result['code_version']['policy_hash'],execution_model=result['execution_model_version'],
        metrics=output['metrics'],trade_ledger=output['trade_ledger'],fill_ledger=output['fill_ledger'],
        rejections_and_skips=output['rejections_and_skips'],
        exposure=[dict(session=e['session'],residual_positions=e['residual_positions'],pending_entries=e['pending_entries']) for e in output['exposure']])
    content=canonical_json(projection)
    packet=dict(artifact_id='performance:'+experiment_id,checkpoint='suspicious_results',
        evidence=[dict(id='performance-result',content=content,sha256=hashlib.sha256(content.encode()).hexdigest(),
            provenance=dict(uri='local-sealed-audit:'+result['result_hash'],retrieved_at=result['run_timestamp'],publisher='deterministic systematic trader'))],
        assumptions=result['performance']['warnings'],specification=canonical_json(result['provenance']['experiment']['specification']),
        deterministic_context=dict(result_hash=result['result_hash'],experiment_id=experiment_id,
            certificate_id=result['certificate_id'],scope=result['authorized_cells'],
            evidence_replay_hash=result['evidence_replay_hash'],execution_authority='none',
            review_does_not_authorize_execution=True),resolutions=[])
    validate_packet(packet)
    return packet


def review_status(authority_directory, experiment_id, gate=None):
    """Projection through the existing gate; no cached UI state can clear review."""
    packet=result_packet(authority_directory,experiment_id)
    if gate is None:return 'INDEPENDENT_REVIEW_REQUIRED'
    from ai_review.gate import ReviewGate
    if type(gate) is not ReviewGate:raise ContractError('trusted_independent_review_gate_required')
    record=gate.storage.latest(packet['artifact_id'])
    if record is None:return 'INDEPENDENT_REVIEW_REQUIRED'
    s=record['snapshot']
    if s['packet_hash']!=digest(packet):raise ContractError('performance_review_packet_mismatch')
    if s['open_objections']:return 'AI_REVIEW_DISAGREEMENT'
    if s['state']=='CLEARED_FOR_NEXT_VALIDATION_STAGE':
        # Reverify receipts, fresh deterministic checks and configuration. This
        # gate transition ONLY authorizes another validation stage, never a run.
        from ai_review.contracts import ReviewError
        try:gate.advance(packet['artifact_id'],packet_hash=digest(packet),checkpoint=packet['checkpoint'],target='next_validation_stage')
        except ReviewError:return 'INDEPENDENT_REVIEW_PENDING'
        return 'INDEPENDENT_REVIEW_CLEARED'
    return 'INDEPENDENT_REVIEW_PENDING'
