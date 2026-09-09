"""Segmented, collection-only session service. A seal is never a certificate.

Control decisions and segment registration are append-only. Raw segments use the
existing recorder unchanged. Corruption is quarantined by refusal, never repair.
"""
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
import fcntl
import hashlib
import json
import time

from .events import ContractError, digest
from .evidence_store import EvidenceStore, read_records
from .prospective import ProspectiveRecorder, reconstruct
from .prospective_adapters import CATEGORIES, SOURCES
from .prospective_sources import SYMBOLS

DOMAIN = 'collection-service-v1'
CAPABILITIES = {
    'fixture': dict(identity=True, round_lot=True, corporate_actions=True, trading_status=True,
                    luld=True, quotes=True, trades=True, corrections=True, sequence='fixture_per_security_contiguous'),
    'nasdaq_directory': dict(identity='unbound_snapshot', round_lot='unbound_snapshot'),
    'alpaca_assets': dict(identity='observed_uuid_no_effective_interval'),
    'alpaca_calendar': dict(calendar='schedule_only'),
    'alpaca_actions': dict(corporate_actions='publication_and_revision_coverage_unverified'),
    'alpaca_sip': dict(quotes=True, trades=True, corrections=True, trading_status=True, luld=True,
                       sequence='not_exposed', access='BLOCKED_existing_entitlement_failure'),
    'tradier': dict(quotes=True, trades=True, corrections='unresolved_target',
                    sequence='numeric_scope_and_contiguity_unverified', access='UNVERIFIED'),
}
for _value in CAPABILITIES.values():
    for _field in ('identity','round_lot','corporate_actions','trading_status','luld','quotes','trades','corrections'):
        _value.setdefault(_field, False)
    _value.setdefault('sequence', 'unavailable')


def capabilities():
    return json.loads(json.dumps(CAPABILITIES))


def code_hash():
    names = ('collection_service.py','collection_runtime.py','collection_transport.py','collector_power.py',
             'prospective.py','prospective_adapters.py','evidence_store.py',
             'collection_operations.py','iex_collection.py','providers.py','source_coverage.py')
    root = Path(__file__).parent
    return digest({n: hashlib.sha256((root/n).read_bytes()).hexdigest() for n in names})


class CollectionService:
    def __init__(self, directory, *, segment_receipts=128):
        if type(segment_receipts) is not int or not 1 <= segment_receipts <= 128:
            raise ContractError('bounded_segment_size_required')
        self.root = Path(directory).resolve()
        self.audit = EvidenceStore(self.root/'control', DOMAIN)
        self.limit = segment_receipts
        with self.transaction():
            rows = self.records()
            config = dict(version=DOMAIN, symbols=list(SYMBOLS), segment_receipts=segment_receipts,
                          capabilities=capabilities(), orders_enabled=False)
            prior = [r['body'] for r in rows if r['kind']=='configuration']
            if prior and prior != [config]:
                raise ContractError('collection_configuration_changed')
            if not prior:
                self.append('configuration', config)

    @contextmanager
    def transaction(self):
        with (self.root/'state.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def records(self):
        return self.audit.replay()

    def append(self, kind, body):
        with self.audit.locked() as store:
            return self.audit.append(store, kind, kind+':'+uuid4().hex, body)

    def registrations(self):
        return {r['body']['session_id']:r['body'] for r in self.records() if r['kind']=='session'}

    def register(self, session_id, open_ns, close_ns, identities, *, provenance):
        if (not isinstance(session_id,str) or not session_id or type(open_ns) is not int
            or type(close_ns) is not int or open_ns>=close_ns or not provenance):
            raise ContractError('authoritative_session_registration_required')
        if (not isinstance(identities,dict) or not identities or len(identities)>10
            or any(not isinstance(s,str) or not __import__('re').fullmatch(r'[A-Z][A-Z0-9.]{0,9}',s) for s in identities)
            or len(set(identities.values()))!=len(identities)
            or any(not isinstance(v,str) or ':' not in v for v in identities.values())):
            raise ContractError('bounded_stable_identity_scope_required')
        body = dict(session_id=session_id, open_ns=open_ns, close_ns=close_ns,
                    identities=identities, provenance=provenance, lifecycle='COLLECTION_ONLY')
        with self.transaction():
            prior = self.registrations().get(session_id)
            if prior and prior != body:
                raise ContractError('session_identity_or_schedule_changed')
            if not prior:
                self.append('session', body)

    def segments(self, sid):
        return [r['body'] for r in self.records() if r['kind']=='segment_open' and r['body']['session_id']==sid]

    def segment(self, entry):
        # Segment names are internally generated digests/counters, never provider paths.
        path = self.root/'segments'/entry['name']
        return ProspectiveRecorder(path)

    def _segment(self, sid):
        entries = self.segments(sid)
        closed = {r['body']['name'] for r in self.records() if r['kind']=='segment_closed'}
        if entries and entries[-1]['name'] not in closed:
            entry = entries[-1]; rec = self.segment(entry)
            rows = rec.audit.replay()
            if sum(r['kind']=='raw_receipt' for r in rows)<self.limit and sum(r['body']['bytes'] for r in rows if r['kind']=='raw_receipt')<16*1024*1024:
                return entry, rec
            self._close(entry)
        entry = dict(session_id=sid,name=digest(sid)[:24]+'-'+str(len(entries)+1))
        # Intent precedes directory creation: restart can finish interrupted rotation.
        self.append('segment_open', entry)
        rec = self.segment(entry); reg = self.registrations()[sid]
        rec.register(sid, reg['open_ns'], reg['close_ns'], list(reg['identities'].values()))
        return entry, rec

    def _close(self, entry):
        rec = self.segment(entry); rec.recover()
        rows = rec.audit.replay()
        self.append('segment_closed', {**entry, 'head':rows[-1]['hash'], 'records':len(rows),
                    'raw_hashes':[r['body']['sha256'] for r in rows if r['kind']=='raw_receipt']})

    def gap(self, sid, source, reason, start_ns, end_ns=None):
        if sid not in self.registrations():
            raise ContractError('collection_session_unknown')
        self.append('gap', dict(session_id=sid,source=source,reason=reason,start_ns=start_ns,
                               end_ns=end_ns,unresolved=True))

    def health(self, sid, source, state, reason, at_ns):
        if state not in {'Healthy','Degraded','Source unavailable','Stopped'}:
            raise ContractError('source_health_state_invalid')
        self.append('health', dict(session_id=sid,source=source,state=state,reason=reason,at_ns=at_ns))

    def recover(self, now_ns):
        with self.transaction():
            self._replay_segments()  # Verify closed segments BEFORE any mutable recovery.
            closed = {r['body']['name'] for r in self.records() if r['kind']=='segment_closed'}
            for sid, reg in self.registrations().items():
                for entry in self.segments(sid):
                    if entry['name'] not in closed:
                        rec = self.segment(entry)
                        rec.register(sid, reg['open_ns'], reg['close_ns'], list(reg['identities'].values()))
                        rec.recover()
            self.append('recovery',dict(at_ns=now_ns,semantics='pending_normalization_only_no_continuity_claim'))

    def start(self, now_ns):
        self.recover(now_ns)
        with self.transaction():
            last = next((r['body'] for r in reversed(self.records()) if r['kind'] in {'heartbeat','started','stopped'}),None)
            for sid, reg in self.registrations().items():
                if reg['open_ns']<=now_ns and (last is None or last['at_ns']<reg['close_ns']):
                    self.gap(sid,'service','startup_or_restart_coverage_unknown',
                             max(reg['open_ns'],last['at_ns'] if last else reg['open_ns']),min(now_ns,reg['close_ns']))
            self.append('started',dict(at_ns=now_ns,pid=__import__('os').getpid(),code_hash=code_hash()))

    def heartbeat(self, now_ns, monotonic_ns):
        with self.transaction():
            last = next((r['body'] for r in reversed(self.records()) if r['kind']=='heartbeat'),None)
            if last and (now_ns<last['at_ns'] or (last.get('pid')==__import__('os').getpid()
                and abs((now_ns-last['at_ns'])-(monotonic_ns-last['monotonic_ns']))>5*10**9)):
                for sid, reg in self.registrations().items():
                    if reg['close_ns']>=min(now_ns,last['at_ns']):
                        self.gap(sid,'clock','clock_discontinuity',min(now_ns,last['at_ns']),max(now_ns,last['at_ns']))
            self.append('heartbeat',dict(at_ns=now_ns,monotonic_ns=monotonic_ns,pid=__import__('os').getpid()))

    def stop(self, now_ns):
        with self.transaction():
            for sid, reg in self.registrations().items():
                if reg['open_ns']<=now_ns<reg['close_ns']:
                    self.gap(sid,'service','stopped_during_session',now_ns)
            self.append('stopped',dict(at_ns=now_ns))

    def ingest(self, sid, source, raw, *, received_ns, connection_id, provenance=None):
        if sid not in self.registrations() or source not in SOURCES or not connection_id:
            raise ContractError('registered_source_session_connection_required')
        with self.transaction():
            entry, rec = self._segment(sid)
            # Arrival metadata is committed in the SAME raw record before interpretation.
            return rec.ingest(sid,source,raw,received_ns=received_ns,
                              provenance={**(provenance or {}),'connection_id':connection_id,'segment':entry['name']})

    def _replay_segments(self, session_id=None):
        rows = self.records(); closed = {r['body']['name']:r['body'] for r in rows if r['kind']=='segment_closed'}
        merged=[]; registered=set(); manifests=[]
        for r in rows:
            if r['kind']!='segment_open':continue
            entry=r['body']; path=self.root/'segments'/entry['name']
            if session_id is not None and entry['session_id']!=session_id:continue
            records=read_records(path,'prospective-evidence-v1')
            seal=closed.get(entry['name'])
            if seal and (not records or records[-1]['hash']!=seal['head'] or len(records)!=seal['records']
                         or [x['body']['sha256'] for x in records if x['kind']=='raw_receipt']!=seal['raw_hashes']):
                raise ContractError('closed_segment_changed')
            manifests.append({**entry,'head':records[-1]['hash'] if records else None,'records':len(records)})
            for item in records:
                if item['kind']=='lifecycle':
                    raise ContractError('segment_role_change_requires_governed_session_intake')
                if item['kind']=='session':
                    sid=item['body']['session_id']
                    if sid in registered:continue
                    registered.add(sid)
                merged.append(item)
        return merged,manifests

    def replay(self, session_id=None):
        control=self.records(); merged,manifests=self._replay_segments(session_id)
        # Registration exists even if no byte ever arrived.
        existing={r['body']['session_id'] for r in merged if r['kind']=='session'}
        for sid,reg in self.registrations().items():
            if session_id is not None and sid!=session_id:continue
            if sid not in existing:
                b=dict(session_id=sid,open_ns=reg['open_ns'],close_ns=reg['close_ns'],security_ids=list(reg['identities'].values()),lifecycle='COLLECTION_ONLY',orders_enabled=False)
                merged.append(dict(kind='session',key='session:'+sid,body=b,hash=digest(b)))
        state=reconstruct(merged)
        checkpoints={}; raw_index={r['key']:r['body'] for r in merged if r['kind']=='raw_receipt'}
        for sid,s in state['sessions'].items():
            gaps=[r['body'] for r in control if r['kind']=='gap' and r['body']['session_id']==sid]
            seen={}; sequences={}; clocks={};duplicates=0;out_of_order=0;last_receipt=None
            for r in merged:
                if r['kind']=='raw_receipt' and r['body']['session_id']==sid:
                    stamp=r['body']['received_ns']
                    if last_receipt is not None and stamp<last_receipt:
                        gaps.append(dict(source='clock',reason='receipt_clock_regression_across_segments',start_ns=stamp,end_ns=last_receipt,unresolved=True))
                    last_receipt=max(stamp,last_receipt or stamp)
            for r in merged:
                b=r['body']
                if b.get('session_id')!=sid or r['kind']!='normalized':continue
                receipt=raw_index[b['receipt_key']];source=receipt['source'];conn=receipt['provenance']['connection_id']
                duplicate_key=(source,conn,receipt['sha256'])
                if duplicate_key in seen:
                    duplicates+=1;continue
                seen[duplicate_key]=True
                for f in b['facts']:
                    key=source+'|'+str(f['security_id'] or f['symbol']);value=f['values']
                    stamp=f['effective_ns']
                    if stamp is not None:
                        if stamp<clocks.get(key,stamp):out_of_order+=1
                        clocks[key]=max(stamp,clocks.get(key,stamp))
                    seq=value.get('sequence')
                    if seq is not None:
                        old=sequences.get(key)
                        # Only the fixture explicitly guarantees contiguous per-ID sequence.
                        if source=='fixture':
                            if type(seq) is not int or (old is not None and seq!=old+1):
                                gaps.append(dict(source=source,reason='sequence_gap_or_reorder',start_ns=receipt['received_ns'],end_ns=None,unresolved=True))
                        if type(seq) is int:sequences[key]=max(seq,old if old is not None else seq)
            if gaps:
                s['completeness_state']='Incomplete'
                for checks in s['completeness'].values():
                    for k in checks:checks[k]='Incomplete'
            s['service_gaps']=gaps
            if not any(r['kind']=='raw_receipt' and r['body']['session_id']==sid for r in merged):
                s['completeness_state']='Source unavailable' if gaps else 'Unknown'
            raws=[r for r in merged if r['kind']=='raw_receipt' and r['body']['session_id']==sid]
            checkpoints[sid]=dict(last_received_ns=raws[-1]['body']['received_ns'] if raws else None,
                last_raw_hash=raws[-1]['body']['sha256'] if raws else None,sequences=sequences,
                sequence_semantics='fixture verified only; other numeric cursors do not prove continuity',
                duplicates=duplicates,out_of_order=out_of_order,gaps=gaps,
                entering_state={i:dict(c) for i,c in s['completeness'].items()},
                correction_state_hash=digest(s['interpreted']))
        state.pop('state_hash',None)
        return dict(state=state,checkpoints=checkpoints,segments=manifests,
                    evidence_hash=digest(dict(state=state,checkpoints=checkpoints,segments=manifests)))

    def seal(self, sid, now_ns):
        with self.transaction():
            reg=self.registrations()[sid]
            if now_ns<reg['close_ns']:raise ContractError('cannot_seal_before_close')
            closed={r['body']['name'] for r in self.records() if r['kind']=='segment_closed'}
            for entry in self.segments(sid):
                if entry['name'] not in closed:self._close(entry)
            evidence=self.replay(sid); session=evidence['state']['sessions'][sid]
            segments=[x for x in evidence['segments'] if x['session_id']==sid]
            body=dict(session_id=sid,at_ns=now_ns,session_hash=digest(session),segments=segments,
                      checkpoint=evidence['checkpoints'][sid],classification=session['completeness_state'],
                      reasons=sorted(set(session['errors']+[g['reason'] for g in session['service_gaps']]
                              +[k+':'+v for c in session['completeness'].values() for k,v in c.items() if v!='Complete'])),
                      certification='Certification not yet evaluated',certified=False,orders_enabled=False)
            prior=[r['body'] for r in self.records() if r['kind']=='seal' and r['body']['session_id']==sid]
            body['revision']=len(prior)+1
            self.append('seal',body)
            return body


def status(directory, now_ns=None):
    root=Path(directory); now_ns=time.time_ns() if now_ns is None else now_ns
    if not (root/'control/audit.sqlite3').exists():
        return dict(running=False,state='Stopped',session=None,symbols=list(SYMBOLS),orders_enabled=False)
    try:
        rows=read_records(root/'control',DOMAIN)
        config=next(r['body'] for r in rows if r['kind']=='configuration')
        # Read-only construction: status does not initialize/recover/mutate evidence.
        service=object.__new__(CollectionService);service.root=root.resolve();service.limit=config['segment_receipts']
        service.audit=object.__new__(EvidenceStore);service.audit.root=root/'control';service.audit.domain=DOMAIN
        retired={r['body']['session_id'] for r in rows if r['kind']=='session_retired'}
        regs={sid:reg for sid,reg in service.registrations().items() if sid not in retired}
        current=sorted(regs,key=lambda sid:(regs[sid]['close_ns']<now_ns,abs(regs[sid]['open_ns']-now_ns)))
        sid=current[0] if current else None
        evidence=service.replay(sid)
        activity=[r['body']|{'kind':r['kind']} for r in rows if r['kind'] in {'started','heartbeat','stopped'}]
        running=bool(activity and activity[-1]['kind']!='stopped' and 0<=now_ns-activity[-1]['at_ns']<120*10**9)
        s=evidence['state']['sessions'].get(sid,{})
        seals=[r['body'] for r in rows if r['kind']=='seal' and r['body']['session_id']==sid]
        sealed=bool(seals and seals[-1]['session_hash']==digest(s) and seals[-1]['segments']==[x for x in evidence['segments'] if x['session_id']==sid])
        health={r['body']['source']:{k:r['body'][k] for k in ('state','reason','at_ns')} for r in rows if r['kind']=='health' and r['body']['session_id']==sid}
        for item in health.values():
            if item['state']=='Healthy' and (not running or now_ns-item['at_ns']>120*10**9):item['state']='Degraded'
        segment_rows=service._replay_segments(sid)[0] if sid else []
        raw_records=[r for r in segment_rows if r['kind']=='raw_receipt']
        facts=[f for r in segment_rows if r['kind']=='normalized' for f in r['body']['facts']]
        market=[f for f in facts if f['kind']=='market_observation']
        from .source_coverage import matrix
        iex_ack=any(f['values'].get('feed')=='iex' and f['values'].get('message',{}).get('T')=='subscription' for f in facts)
        from collections import Counter
        iex_types=Counter(f['values'].get('event_type') for f in market if 'IEX_only_not_consolidated' in f['values'].get('flags',[]))
        source_matrix=matrix(iex_event_types=dict(iex_types),iex_subscription=iex_ack,iex_market_events=sum('IEX_only_not_consolidated' in f['values'].get('flags',[]) for f in market),tradier_configured=health.get('tradier',{}).get('reason')=='token_available_adapter_retained_not_auto_activated')
        from .collection_operations import phase
        return dict(running=running,state='Collecting' if running else 'Stopped',session=sid,
            symbols=list(regs[sid]['identities']) if sid else list(SYMBOLS),sources=health,
            completeness=s.get('completeness_state','Unknown'),coverage={k:sorted({c[k] for c in s.get('completeness',{}).values()}) for k in CATEGORIES},
            detected_gaps=len(s.get('service_gaps',[])),seal='Sealed' if sealed else 'Unsealed or revised',
            phase=phase(regs[sid],now_ns) if sid else 'Awaiting session',
            completeness_by_symbol={symbol:s.get('completeness',{}).get(identity,{}) for symbol,identity in regs[sid]['identities'].items()} if sid else {},
            source_matrix=source_matrix,market_events=len(market),last_market_source_ns=max((f['effective_ns'] for f in market if f['effective_ns'] is not None),default=None),
            raw_receipts=len(raw_records),last_event_time_ns=raw_records[-1]['body']['received_ns'] if raw_records else None,
            lifecycle='COLLECTION_ONLY',certification='Not certifiable',orders_enabled=False)
    except Exception:
        return dict(running=False,state='Degraded',error='Evidence integrity unavailable; collector must stop',orders_enabled=False)
