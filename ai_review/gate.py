"""Frozen-packet review workflow. No market readers, strategy runners or order clients."""
from dataclasses import asdict

from .contracts import (ReviewError, VERSION, REQUIRED, CHECKS, PRIMARY_PROMPT, REVIEW_PROMPT,
                        validate_packet, validate_review, digest, freeze, fresh, text)
from .storage import _WORKFLOW_WRITE


def disposition(snapshot, clock):
    if snapshot.get('open_objections'):
        return 'AI_REVIEW_DISAGREEMENT'
    checks = snapshot.get('deterministic', {}).get('checks', {})
    if any(value == 'failed' for value in checks.values()):
        return 'DETERMINISTIC_VALIDATION_FAILED'
    if not snapshot.get('primary'):
        return 'PRIMARY_PENDING'
    if snapshot['packet']['checkpoint'] in REQUIRED:
        review = snapshot.get('review')
        if not review or not fresh(review['received_at'], clock):
            return 'INDEPENDENT_REVIEW_PENDING'
    if not CHECKS <= checks.keys() or any(checks[k] != 'passed' for k in CHECKS):
        return 'DETERMINISTIC_VALIDATION_PENDING'
    return 'CLEARED_FOR_NEXT_VALIDATION_STAGE'


class ReviewGate:
    def __init__(self, storage, primary, reviewer, validator=None):
        self.storage, self.primary, self.reviewer, self.validator = storage, primary, reviewer, validator
        if primary.route.provider != 'openai':
            raise ReviewError('openai_primary_required')
        if reviewer is not None and (reviewer.route.provider != 'gemini'
                                     or reviewer.route.provider == primary.route.provider):
            raise ReviewError('distinct_independent_provider_required')

    def submit(self, packet):
        packet = freeze(packet)
        validate_packet(packet)
        identifier = packet['artifact_id']
        prior = self.storage.latest(identifier)
        if prior and prior['snapshot']['packet_hash'] == digest(packet):
            return prior
        if prior and prior['snapshot']['packet']['checkpoint'] != packet['checkpoint']:
            raise ReviewError('checkpoint_immutable_create_linked_artifact')
        old = prior['snapshot'] if prior else {}
        snapshot = dict(packet=packet, packet_hash=digest(packet), primary=None, review=None,
            deterministic={}, open_objections=old.get('open_objections', []),
            resolved_objections=old.get('resolved_objections', []),
            state='INDEPENDENT_REVIEW_PENDING', execution_authority='none',
            data_certification_authority=False, runner_authorized=False, fixture_only=True,
            errors=[], version=VERSION)
        snapshot['state'] = disposition(snapshot, self.storage.clock())
        return self.storage.append(identifier, prior['event_hash'] if prior else '0'*64,
                                   'packet_frozen', snapshot, authority=_WORKFLOW_WRITE)

    def _save(self, record, snapshot, kind):
        snapshot['state'] = disposition(snapshot, self.storage.clock())
        return self.storage.append(record['artifact_id'], record['event_hash'], kind, snapshot,
                                   authority=_WORKFLOW_WRITE)

    def _deterministic(self, packet):
        # Provider-supplied deterministic_context is context, NEVER a validation verdict.
        if self.validator is None:
            return {'checks': {k: 'unknown' for k in sorted(CHECKS)}, 'validator_version': 'unconfigured',
                    'packet_hash': digest(packet), 'scope': 'offline_review_only', 'verifications': []}
        result = freeze(self.validator(freeze(packet)))
        if (not isinstance(result, dict) or set(result) != {'checks', 'validator_version', 'packet_hash', 'scope', 'verifications'}
                or result['packet_hash'] != digest(packet) or not text(result['validator_version'])
                or result['scope'] != 'offline_review_only' or not isinstance(result['checks'], dict)
                or not CHECKS <= result['checks'].keys()
                or any(x not in {'passed', 'failed', 'unknown'} for x in result['checks'].values())
                or not isinstance(result['verifications'], list)):
            raise ReviewError('deterministic_result_invalid')
        for check in result['verifications']:
            if (not isinstance(check, dict) or set(check) != {'objection_id', 'result', 'evidence_hash'}
                    or check['result'] != 'passed' or not text(check['evidence_hash'])):
                raise ReviewError('resolution_verification_invalid')
        return result

    def _resolution(self, objection, snapshot):
        packet = snapshot['packet']
        if snapshot['packet_hash'] == objection['packet_hash']:
            return None
        sources = {s['id']: s['sha256'] for s in packet['evidence']}
        for resolution in packet['resolutions']:
            if not isinstance(resolution, dict) or resolution.get('objection_id') != objection['id']:
                continue
            if set(resolution) == {'objection_id', 'evidence_id', 'sha256'}:
                source_id = resolution['evidence_id']
                sha = resolution['sha256']
                # A relabelled old source is not new evidence.
                if sources.get(source_id) == sha and sha not in objection['source_hashes'].values():
                    return freeze(resolution)
            if set(resolution) == {'objection_id', 'verification_hash'}:
                for check in snapshot['deterministic']['verifications']:
                    if check['objection_id'] == objection['id'] and digest(check) == resolution['verification_hash']:
                        return freeze(resolution)
        return None

    def run(self, artifact_id):
        self._check_routes()
        record = self.storage.latest(artifact_id)
        if not record:
            raise ReviewError('frozen_packet_required')
        s = freeze(record['snapshot'])
        packet = s['packet']
        validate_packet(packet)
        if digest(packet) != s['packet_hash']:
            raise ReviewError('packet_hash_mismatch')
        # Every retry revalidates deterministic evidence without invoking a runner.
        try:
            s['deterministic'] = self._deterministic(packet)
        except Exception:
            s['deterministic'] = {'checks': {'validator': 'failed'}, 'packet_hash': s['packet_hash']}
            s['errors'].append('deterministic_validator_failed')
        record = self._save(record, s, 'deterministic_checked')
        try:
            expected_route = asdict(self.primary.route)
            if s['primary'] and s['primary']['request']['route'] != expected_route:
                s['primary'] = None
                s['review'] = None
            if s['primary'] is None:
                receipt = self.primary.invoke(dict(purpose='primary', prompt=PRIMARY_PROMPT,
                    prompt_version='primary-v1', packet=packet, packet_hash=s['packet_hash']), self.storage)
                output = receipt['output']
                if not isinstance(output, dict) or set(output) != {'proposal', 'assumptions', 'uncertainties'} or not text(output['proposal']):
                    raise ReviewError('primary_schema_invalid')
                if any(not isinstance(output[k], list) or not all(text(x) for x in output[k])
                       for k in ('assumptions', 'uncertainties')):
                    raise ReviewError('primary_schema_invalid')
                s['primary'] = receipt
                record = self._save(record, s, 'primary_saved')
        except Exception as exc:
            s['errors'].append(str(exc) if isinstance(exc, ReviewError) else 'primary_unavailable')
            return self._save(record, s, 'primary_pending')
        if packet['checkpoint'] not in REQUIRED:
            s['review'] = None
            return self._save(record, s, 'review_not_required')
        try:
            if self.reviewer is None:
                raise ReviewError('reviewer_disabled')
            request = dict(purpose='review', prompt=REVIEW_PROMPT, prompt_version='adversarial-review-v1',
                packet=packet, packet_hash=s['packet_hash'], primary=s['primary'],
                deterministic=s['deterministic'], prior_open_objections=s['open_objections'])
            receipt = self.reviewer.invoke(request, self.storage)
            validate_review(receipt['output'], receipt['request_hash'], {e['id'] for e in packet['evidence']})
            s['review'] = receipt
            unresolved = []
            # Even an agreeing new reviewer cannot close an unsupported resolution.
            for objection in s['open_objections']:
                resolution = self._resolution(objection, s)
                if resolution and receipt['output']['classification'] != 'material':
                    s['resolved_objections'].append({**objection, 'disposition': 'resolved_after_new_review',
                        'resolution': resolution, 'resolved_packet_hash': s['packet_hash'],
                        'resolving_review_hash': receipt['receipt_hash'], 'resolved_at': self.storage.clock()})
                else:
                    unresolved.append(objection)
            for objection in receipt['output']['objections']:
                if objection['severity'] != 'material':
                    continue
                entry = dict(artifact_id=artifact_id, packet_hash=s['packet_hash'], **objection,
                    primary_hash=digest(s['primary']), review_hash=receipt['receipt_hash'],
                    source_hashes={e['id']: e['sha256'] for e in packet['evidence']},
                    primary_route=s['primary']['request']['route'], reviewer_route=receipt['request']['route'],
                    prompt_version=request['prompt_version'], created_at=receipt['received_at'], disposition='open')
                entry['id'] = digest([artifact_id, s['packet_hash'], objection])
                if entry['id'] not in {x['id'] for x in unresolved}:
                    unresolved.append(entry)
            s['open_objections'] = unresolved
            return self._save(record, s, 'review_received')
        except Exception as exc:
            s['review'] = None
            s['errors'].append(str(exc) if isinstance(exc, ReviewError) else 'reviewer_unavailable')
            return self._save(record, s, 'review_pending')

    def advance(self, artifact_id, *, packet_hash, checkpoint, target):
        """Only mark a reviewed packet ready for another validation stage; no dispatch."""
        self._check_routes()
        if target != 'next_validation_stage':
            raise ReviewError('runner_promotion_certification_and_orders_not_authorized')
        record = self.storage.latest(artifact_id)
        if not record or record['snapshot']['packet_hash'] != packet_hash:
            raise ReviewError('current_packet_required')
        s = freeze(record['snapshot'])
        validate_packet(s['packet'])
        if digest(s['packet']) != packet_hash:
            raise ReviewError('packet_hash_mismatch')
        if checkpoint != s['packet']['checkpoint']:
            raise ReviewError('checkpoint_mismatch')
        if s['primary'] is None or s['primary']['request']['route'] != asdict(self.primary.route):
            raise ReviewError('current_primary_configuration_required')
        self.storage.verify_receipt(s['primary'])
        if (s['primary']['request']['packet'] != s['packet']
                or s['primary']['request']['prompt'] != PRIMARY_PROMPT):
            raise ReviewError('primary_packet_or_prompt_mismatch')
        if checkpoint in REQUIRED:
            if self.reviewer is None or s['review'] is None or s['review']['request']['route'] != asdict(self.reviewer.route):
                raise ReviewError('current_reviewer_configuration_required')
            self.storage.verify_receipt(s['review'])
            if (s['review']['request']['packet'] != s['packet']
                    or s['review']['request']['primary'] != s['primary']
                    or s['review']['request']['prompt'] != REVIEW_PROMPT):
                raise ReviewError('review_packet_or_prompt_mismatch')
            validate_review(s['review']['output'], s['review']['request_hash'], {e['id'] for e in s['packet']['evidence']})
        # Recheck at transition time; cached consensus cannot override a new failure.
        s['deterministic'] = self._deterministic(s['packet'])
        if checkpoint in REQUIRED and s['review']['request']['deterministic'] != s['deterministic']:
            s['review'] = None
        record = self._save(record, s, 'transition_checked')
        if record['snapshot']['state'] != 'CLEARED_FOR_NEXT_VALIDATION_STAGE':
            raise ReviewError('review_or_deterministic_gate_blocked')
        return self._save(record, s, 'advanced')

    def _check_routes(self):
        if self.primary.route.provider != 'openai' or (self.reviewer is not None
                and self.reviewer.route.provider != 'gemini'):
            raise ReviewError('distinct_independent_provider_required')
