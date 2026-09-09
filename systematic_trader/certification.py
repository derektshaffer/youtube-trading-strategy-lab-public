"""The validation layer's sole certificate issuer and experiment registrar."""
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .certificate_audit import CertificateAudit, _sign, verify_signature
from .certification_inputs import (validate_inputs, context, freeze, now, experiment_template, VERSION)
from .events import ContractError, digest

ISSUER='systematic-trader-deterministic-validation-v1'
CERTIFICATE_DOMAIN='dataset-admission-certificate-v1'


def verify_envelope(audit, certificate):
    if not isinstance(certificate,dict) or set(certificate)!={'body','certificate_id','signature'}:
        raise ContractError('dataset_certificate_missing_or_malformed')
    body=certificate['body']
    if not isinstance(body,dict) or body.get('issuer')!=ISSUER or body.get('version')!=VERSION:
        raise ContractError('dataset_certificate_issuer_or_version_invalid')
    if body.get('trust_domain')!=audit.anchor['domain'] or body.get('issuer_key')!=audit.anchor['public_key_sha256']:
        raise ContractError('dataset_certificate_untrusted_domain_or_key')
    if certificate['certificate_id']!=digest(body):raise ContractError('dataset_certificate_hash_mismatch')
    verify_signature(audit.public,CERTIFICATE_DOMAIN,body,certificate['signature'])
    return body


class CertificateAuthority:
    """Accept evidence locations only; never caller-supplied admission verdicts."""
    def __init__(self, root):
        self.audit=CertificateAudit(root)

    @classmethod
    def initialize(cls, root, *, domain):
        CertificateAudit.create(root,domain=domain)
        return cls(root)

    def issue(self, evidence_directory):
        attempt=uuid4().hex
        with self.audit.locked() as store:
            self.audit.append(store,'issuance_attempt',attempt,dict(at=now(),
                evidence_directory=str(evidence_directory) if isinstance(evidence_directory,(str,Path)) else 'invalid_input_type'))
            try:
                if not isinstance(evidence_directory,(str,Path)):
                    raise ContractError('evidence_location_required_not_admission_flags')
                data=validate_inputs(evidence_directory,self.audit.anchor['domain'])
                admission=dict(data=data,at=now())
                admission_hash=self.audit.append(store,'admission_completed','admission:'+attempt,admission)
                if not data['admission']['admitted'] or not data['cells']:
                    raise ContractError('deterministic_admission_rejected')
                prior=self.audit.records(store,'certificate_issued')
                if any(r['body']['body']['dataset_hash']==data['dataset_hash'] for r in prior):
                    raise ContractError('dataset_already_certified_new_issuance_forbidden')
                issued=now()
                body=dict(version=VERSION,issuer=ISSUER,issuer_key=self.audit.anchor['public_key_sha256'],
                    trust_domain=self.audit.anchor['domain'],status='ADMITTED',
                    dataset_hash=data['dataset_hash'],manifest_hash=data['manifest_hash'],
                    raw_receipt_hashes=data['raw_receipt_hashes'],normalized_hash=data['normalized_hash'],
                    security_identities=data['identities'],cells=data['cells'],context=data['context'],
                    admission=data['admission'],admission_record_hash=admission_hash,
                    corrections_revisions=data['correction_state'],limitations=data['limitations'],
                    issued_at=issued,expires_at=(datetime.fromisoformat(issued)+timedelta(hours=24)).isoformat(),
                    consumer='performance-consumer-v1' if data.get('performance_input_version') else 'scope-probe-v1',
                    validity_scope=('synthetic_performance_only' if data.get('performance_input_version') else 'synthetic_scope_probe_only') if data['origin']=='synthetic-fixture' else 'bounded_development_experiment_only',
                    holdout=data['context']['holdout'],execution_authority='none',orders_enabled=False)
                certificate=dict(body=body,certificate_id=digest(body),signature=_sign(self.audit.root,CERTIFICATE_DOMAIN,body))
                self.audit.append(store,'certificate_issued','certificate:'+certificate['certificate_id'],certificate)
                return certificate
            except Exception as exc:
                reason=str(exc) if isinstance(exc,ContractError) else 'certification_input_or_storage_failure'
                self.audit.append(store,'issuance_refused','refused:'+attempt,dict(reason=reason,at=now()))
                raise ContractError(reason) from None

    def freeze_experiment(self, certificate, specification):
        with self.audit.locked() as store:
            identifier=uuid4().hex
            self.audit.append(store,'experiment_freeze_attempt',identifier,dict(certificate_id=certificate.get('certificate_id') if isinstance(certificate,dict) else None,at=now()))
            try:
                body=verify_envelope(self.audit,certificate)
                self._active(store,certificate)
                if body['context']!=context():raise ContractError('certificate_code_or_configuration_stale')
                expected=experiment_template(certificate)
                if specification!=expected:raise ContractError('experiment_specification_not_frozen_policy')
                prior=[r for r in self.audit.records(store,'experiment_frozen') if r['body']['certificate_id']==certificate['certificate_id']]
                if prior:raise ContractError('certificate_experiment_already_frozen')
                frozen=dict(specification=freeze(specification),certificate_id=certificate['certificate_id'],
                            preregistered_at=now(),version='experiment-preregistration-v1')
                frozen['experiment_hash']=digest(frozen)
                self.audit.append(store,'experiment_frozen','experiment:'+frozen['experiment_hash'],frozen)
                return frozen
            except Exception as exc:
                reason=str(exc) if isinstance(exc,ContractError) else 'experiment_freeze_invalid'
                self.audit.append(store,'experiment_freeze_refused','freeze-refused:'+identifier,dict(reason=reason,at=now()))
                raise ContractError(reason) from None

    def _active(self, store, certificate):
        identifier=certificate['certificate_id']
        issued=[r for r in self.audit.records(store,'certificate_issued') if r['body']['certificate_id']==identifier]
        if len(issued)!=1 or issued[0]['body']!=certificate:
            raise ContractError('certificate_issuance_record_missing')
        if any(r['body']['certificate_id']==identifier for r in self.audit.records(store,'certificate_invalidated')):
            raise ContractError('certificate_invalidated')
        b=certificate['body']
        try:
            current=datetime.fromisoformat(now())
            valid=datetime.fromisoformat(b['issued_at'])<=current<datetime.fromisoformat(b['expires_at'])
        except (ValueError,TypeError):raise ContractError('certificate_time_invalid') from None
        if not valid:raise ContractError('certificate_expired_or_future')

    def invalidate(self, certificate_id, *, reason):
        if not isinstance(reason,str) or not reason.strip():raise ContractError('invalidation_reason_required')
        with self.audit.locked() as store:
            if not any(r['body']['certificate_id']==certificate_id for r in self.audit.records(store,'certificate_issued')):
                raise ContractError('certificate_unknown')
            return self.audit.append(store,'certificate_invalidated','invalidate:'+uuid4().hex,
                dict(certificate_id=certificate_id,reason=reason,at=now()))
