"""Local validation-owned signing and durable, sealed append-only audit.

The OS account and validation process are trusted. No key material or alternate
trust anchor is accepted from a certificate, experiment, UI or model response.
"""
import base64
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile

from .events import ContractError, canonical_json, digest
from .research_check import save
from .validation import ExperimentStore

OPENSSL = '/usr/bin/openssl'


def _crypto(arguments, data=None):
    result = subprocess.run([OPENSSL, *arguments], input=data, capture_output=True, timeout=30)
    if result.returncode:
        raise ContractError('certificate_crypto_operation_failed')
    return result.stdout


def _sign(root, domain, body):
    raw = (domain + '\n' + canonical_json(body)).encode()
    signature = _crypto(['dgst', '-sha256', '-sign', str(root/'issuer-private.pem')], raw)
    return base64.b64encode(signature).decode()


def verify_signature(public_key, domain, body, signature):
    try:
        raw = base64.b64decode(signature, validate=True)
        with tempfile.TemporaryDirectory(prefix='lab-certificate-verify-') as directory:
            public = Path(directory)/'public.pem'; sig = Path(directory)/'signature'
            public.write_bytes(public_key); sig.write_bytes(raw)
            _crypto(['dgst', '-sha256', '-verify', str(public), '-signature', str(sig)],
                    (domain + '\n' + canonical_json(body)).encode())
    except Exception:
        raise ContractError('certificate_signature_invalid') from None


def _atomic(path, body):
    temporary = path.with_suffix('.pending')
    with temporary.open('w') as handle:
        os.chmod(temporary, 0o600)
        handle.write(canonical_json(body)+'\n'); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


class CertificateAudit:
    """Dedicated registry using the existing ExperimentStore's audit chain.

An exclusive process lock spans verification, claim and result. A separately
signed head detects incomplete writes or a truncated DB. Crash mismatch blocks;
there is no auto-repair that could forget a previous execution claim.
"""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.public = (self.root/'issuer-public.pem').read_bytes()
        self.anchor = json.loads((self.root/'trust-anchor.json').read_text())
        if (self.anchor.get('version')!='local-validation-authority-v1'
                or self.anchor.get('issuer')!='systematic-trader-deterministic-validation-v1'
                or self.anchor.get('domain') not in {'synthetic-fixture','historical-v4'}):
            raise ContractError('certificate_trust_configuration_invalid')
        import hashlib
        if self.anchor['public_key_sha256'] != hashlib.sha256(self.public).hexdigest():
            raise ContractError('certificate_trust_anchor_mismatch')

    @classmethod
    def create(cls, root, *, domain):
        if domain not in {'synthetic-fixture', 'historical-v4'}:
            raise ContractError('certificate_trust_domain_unknown')
        root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=False, mode=0o700)
        key = root/'issuer-private.pem'
        # Restrict permissions before generation, not after exposing a key.
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(fd)
        _crypto(['genrsa', '-out', str(key), '2048'])
        public = _crypto(['rsa', '-in', str(key), '-pubout'])
        (root/'issuer-public.pem').write_bytes(public)
        import hashlib
        save(root/'trust-anchor.json', dict(version='local-validation-authority-v1',
            issuer='systematic-trader-deterministic-validation-v1', domain=domain,
            public_key_sha256=hashlib.sha256(public).hexdigest()))
        store = ExperimentStore(root/'audit.sqlite3'); head = store.verify(); store.close()
        _atomic(root/'audit-head.json', dict(head=head, signature=_sign(root, 'audit-head-v1', head)))
        return cls(root)

    @contextmanager
    def locked(self):
        with (self.root/'authority.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            store = ExperimentStore(self.root/'audit.sqlite3')
            try:
                self.verify(store)
                yield store
            finally:
                store.close(); fcntl.flock(lock, fcntl.LOCK_UN)

    def verify(self, store):
        seal = json.loads((self.root/'audit-head.json').read_text())
        verify_signature(self.public, 'audit-head-v1', seal['head'], seal['signature'])
        if store.verify() != seal['head']:
            raise ContractError('certificate_audit_incomplete_or_truncated')
        return seal['head']

    def append(self, store, kind, key, body):
        # Never let a second append silently repair an interrupted head update.
        self.verify(store)
        checksum = store.append(kind, key, body)
        head = store.verify()
        _atomic(self.root/'audit-head.json', dict(head=head, signature=_sign(self.root, 'audit-head-v1', head)))
        return checksum

    @staticmethod
    def records(store, kind=None):
        return [dict(seq=seq, kind=k, key=key, body=json.loads(body), hash=checksum)
                for seq,k,key,body,prior,checksum in store.db.execute('SELECT * FROM audit ORDER BY seq')
                if kind is None or kind == k]

    def replay(self):
        # Read-only independent replay; does not open a writable ExperimentStore.
        conn = sqlite3.connect((self.root/'audit.sqlite3').as_uri()+'?mode=ro', uri=True)
        try:
            previous='0'*64; rows=[]
            for seq,kind,key,body,prior,checksum in conn.execute('SELECT * FROM audit ORDER BY seq'):
                if seq != len(rows)+1 or prior != previous or checksum != digest(dict(seq=seq,kind=kind,key=key,body=body,previous=prior)):
                    raise ContractError('certificate_audit_integrity_failure')
                rows.append(dict(seq=seq,kind=kind,key=key,body=json.loads(body),hash=checksum)); previous=checksum
            seal=json.loads((self.root/'audit-head.json').read_text())
            verify_signature(self.public,'audit-head-v1',seal['head'],seal['signature'])
            if seal['head'] != dict(records=len(rows),head=previous,execution_authority='none'):
                raise ContractError('certificate_audit_incomplete_or_truncated')
            return dict(records=rows,head=previous,replay_hash=digest(rows),execution_authority='none')
        finally:conn.close()
