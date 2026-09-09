"""Read-only certificate projection; absent or damaged authority never looks ready."""
from datetime import datetime, timezone
from pathlib import Path
from .certificate_audit import CertificateAudit
from .certification_inputs import context


def read_status(directory):
    base=dict(dataset_certification='Pending',experiment='Not frozen',runner_authorization='Blocked',
              detail='No trusted authority configured',execution_authority='none',orders_enabled=False)
    if not Path(directory).exists():return base
    try:
        audit=CertificateAudit(directory); replay=audit.replay();rows=replay['records']
        result={**base,'audit_head':replay['head'],'audit_records':len(rows),'trust_domain':audit.anchor['domain']}
        issued=[r for r in rows if r['kind']=='certificate_issued']
        refused=[r for r in rows if r['kind']=='issuance_refused']
        if not issued:
            return {**result,'dataset_certification':'Not eligible' if refused else 'Pending',
                    'detail':refused[-1]['body']['reason'] if refused else 'No certificate issued'}
        certificate=issued[-1]['body'];identifier=certificate['certificate_id'];body=certificate['body']
        invalid=any(r['kind']=='certificate_invalidated' and r['body']['certificate_id']==identifier for r in rows)
        invalid=invalid or body['context']!=context() or datetime.fromisoformat(body['expires_at'])<=datetime.now(timezone.utc)
        result.update(certificate_id=identifier,dataset_certification='Certificate invalid/stale' if invalid else 'Certified',
            detail='Synthetic scope probe only' if audit.anchor['domain']=='synthetic-fixture' else 'Bounded development certificate',
            cells=body['cells'],holdout=body['holdout'],manifest_hash=body['manifest_hash'])
        if any(r['kind']=='experiment_frozen' and r['body']['certificate_id']==identifier for r in rows):result['experiment']='Experiment frozen'
        last=rows[-1] if rows else None
        if not invalid and last and last['kind']=='runner_verification_passed':result['runner_authorization']='Ready'
        if last and last['kind']=='execution_finished':result['detail']+='; single-use execution already consumed'
        if last and last['kind']=='runner_refused':result['detail']+='; '+last['body']['reason']
        return result
    except Exception:
        return {**base,'dataset_certification':'Certificate invalid/stale','detail':'Trusted audit unavailable or invalid; execution blocked'}
