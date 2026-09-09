"""Non-certifying append-only research/evidence journals. Never an authority vault."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import shutil

from .events import ContractError, digest
from .validation import ExperimentStore
from .certificate_audit import _atomic


class EvidenceStore:
    def __init__(self, directory, domain):
        if domain not in {'preliminary-research-v1','prospective-evidence-v1','collection-service-v1','bounded-campaign-v1','research-inventory-v1','rvol-feasibility-v1'}:raise ContractError('evidence_store_domain_invalid')
        self.root=Path(directory).resolve();self.domain=domain
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (self.root/'writer.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            path=self.root/'audit.sqlite3';fresh=not path.exists();store=ExperimentStore(path)
            try:
                if fresh:
                    store.append('domain','domain',dict(domain=domain,certification_authority=False,orders_enabled=False))
                    _atomic(self.root/'head.json',store.verify())
                self._verify(store)
            finally:store.close()

    def _verify(self,store):
        if json.loads((self.root/'head.json').read_text())!=store.verify():raise ContractError('evidence_store_incomplete_or_truncated')
        first=store.db.execute('SELECT body FROM audit WHERE seq=1').fetchone()
        if json.loads(first[0]).get('domain')!=self.domain:raise ContractError('evidence_store_domain_mismatch')

    @contextmanager
    def locked(self):
        with (self.root/'writer.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX);store=ExperimentStore(self.root/'audit.sqlite3')
            try:self._verify(store);yield store
            finally:store.close();fcntl.flock(lock,fcntl.LOCK_UN)

    def append(self,store,kind,key,body):
        self._verify(store)
        if shutil.disk_usage(self.root).free<1_073_741_824:raise ContractError('evidence_disk_reserve_reached')
        result=store.append(kind,key,body)
        _atomic(self.root/'head.json',store.verify())
        return result

    @staticmethod
    def records(store):
        return [dict(seq=s,kind=k,key=key,body=json.loads(b),hash=h) for s,k,key,b,p,h in store.db.execute('SELECT * FROM audit ORDER BY seq')]

    def replay(self):return read_records(self.root,self.domain)


def read_records(directory,domain):
    root=Path(directory).resolve();path=root/'audit.sqlite3'
    if not path.exists():return []
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        records=[];previous='0'*64
        for s,k,key,b,p,h in db.execute('SELECT * FROM audit ORDER BY seq'):
            if s!=len(records)+1 or p!=previous or h!=digest(dict(seq=s,kind=k,key=key,body=b,previous=p)):
                raise ContractError('evidence_store_integrity_failure')
            records.append(dict(seq=s,kind=k,key=key,body=json.loads(b),hash=h));previous=h
        expected=dict(records=len(records),head=previous,execution_authority='none')
        if json.loads((root/'head.json').read_text())!=expected:raise ContractError('evidence_store_incomplete_or_truncated')
        if not records or records[0]['body'].get('domain')!=domain:raise ContractError('evidence_store_domain_mismatch')
        return records
    finally:db.close()
