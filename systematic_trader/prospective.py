"""Raw-first prospective evidence; collection never confers admission or access."""
import base64
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

from .events import ContractError, canonical_json, digest
from .evidence_store import EvidenceStore, read_records
from .prospective_adapters import SOURCES,CATEGORIES,normalize

LIFECYCLES={'COLLECTION_ONLY','RESEARCH_VISIBLE','DEVELOPMENT_ELIGIBLE','VALIDATION_LOCKED','HOLDOUT_LOCKED'}
LOCKED={'VALIDATION_LOCKED','HOLDOUT_LOCKED'}


@dataclass(frozen=True)
class ResearchView:
    session_id: str
    revision: str
    payload: str


class ProspectiveRecorder:
    def __init__(self,directory):self.audit=EvidenceStore(directory,'prospective-evidence-v1')

    def register(self,session_id,open_ns,close_ns,security_ids):
        if not isinstance(session_id,str) or not session_id or type(open_ns) is not int or type(close_ns) is not int or open_ns>=close_ns:raise ContractError('prospective_session_invalid')
        if not security_ids or len(set(security_ids))!=len(security_ids) or any(not isinstance(i,str) or ':' not in i for i in security_ids):raise ContractError('stable_security_identifiers_required')
        with self.audit.locked() as store:
            body=dict(session_id=session_id,open_ns=open_ns,close_ns=close_ns,security_ids=security_ids,lifecycle='COLLECTION_ONLY',orders_enabled=False)
            prior=[r['body'] for r in self.audit.records(store) if r['key']=='session:'+session_id]
            if prior:
                if prior!=[body]:raise ContractError('prospective_session_registration_changed')
                return
            self.audit.append(store,'session','session:'+session_id,body)

    def ingest(self,session_id,source,raw,*,received_ns=None,provenance=None):
        if source not in SOURCES:raise ContractError('prospective_source_not_configured')
        if isinstance(raw,str):raw=raw.encode()
        if not isinstance(raw,bytes) or len(raw)>32*1024*1024:raise ContractError('prospective_raw_size_invalid')
        received_ns=time.time_ns() if received_ns is None else received_ns
        if type(received_ns) is not int or received_ns<0:raise ContractError('prospective_receipt_clock_invalid')
        with self.audit.locked() as store:
            rows=self.audit.records(store)
            if not any(r['key']=='session:'+session_id for r in rows):raise ContractError('prospective_session_unregistered')
            prior_clocks=[r['body']['received_ns'] for r in rows if r['kind']=='raw_receipt' and r['body']['session_id']==session_id]
            clock_regression=bool(prior_clocks and received_ns<max(prior_clocks))
            key='raw:'+uuid4().hex
            self.audit.append(store,'raw_receipt',key,dict(session_id=session_id,source=source,received_ns=received_ns,
                source_contract=SOURCES[source],provenance=provenance or {'source':source},clock_regression=clock_regression,raw_base64=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw)))
            self._normalize(store,key)
            return key

    def _normalize(self,store,key):
        rows=self.audit.records(store);receipt=next(r for r in rows if r['key']==key);b=receipt['body']
        if any(r['key']=='normalized:'+key for r in rows):return
        raw=base64.b64decode(b['raw_base64'],validate=True)
        if hashlib.sha256(raw).hexdigest()!=b['sha256']:raise ContractError('prospective_raw_hash_mismatch')
        try:
            facts=normalize(b['source'],raw)
            output=dict(session_id=b['session_id'],receipt_key=key,receipt_hash=receipt['hash'],facts=facts,errors=['receipt_clock_regression'] if b.get('clock_regression') else [])
        except (ValueError,KeyError,TypeError) as exc:
            output=dict(session_id=b['session_id'],receipt_key=key,receipt_hash=receipt['hash'],facts=[],errors=[str(exc) if isinstance(exc,ContractError) else 'unknown_source_semantics'])
        self.audit.append(store,'normalized','normalized:'+key,output)

    def recover(self):
        with self.audit.locked() as store:
            for r in self.audit.records(store):
                if r['kind']=='raw_receipt':self._normalize(store,r['key'])
        return self.state()

    def activity(self,session_id,state):
        if state not in {'started','heartbeat','stopped'}:raise ContractError('prospective_activity_invalid')
        with self.audit.locked() as store:
            self.audit.append(store,'activity','activity:'+uuid4().hex,dict(session_id=session_id,state=state,at_ns=time.time_ns()))

    def source_gap(self,session_id,source,reason):
        with self.audit.locked() as store:
            self.audit.append(store,'source_gap','gap:'+uuid4().hex,dict(session_id=session_id,source=source,reason=reason,state='UNRESOLVED_SOURCE_GAP'))

    def state(self):return reconstruct(self.audit.replay())

    def fact_at(self,session_id,security_id,kind,*,effective_ns,as_of_ns):
        session=self.state()['sessions'][session_id]
        if session['errors']:raise ContractError('prospective_unresolved_evidence_errors')
        selected={}
        for fact in session['facts']:
            if fact['security_id']!=security_id or fact['kind']!=kind or fact['known_ns']>as_of_ns:continue
            key=(fact['source'],fact['entity_id'])
            if fact['operation']=='void':selected.pop(key,None)
            else:selected[key]=fact
        eligible=[f for f in selected.values() if f['effective_ns'] is not None and f['effective_to_ns'] is not None
            and f['effective_ns']<=effective_ns<f['effective_to_ns']]
        # No ticker join, nearest-date fallback, or ambiguous overlapping intervals.
        if len(eligible)!=1:raise ContractError('prospective_fact_interval_unknown_or_ambiguous')
        return eligible[0]

    def transition(self,session_id,target,*,expected_revision,reason):
        if target not in LIFECYCLES or not isinstance(reason,str) or not reason.strip():raise ContractError('explicit_lifecycle_decision_required')
        with self.audit.locked() as store:
            state=reconstruct(self.audit.records(store));session=state['sessions'][session_id]
            if expected_revision!=session['revision']:raise ContractError('stale_prospective_eligibility')
            previous=session['lifecycle']
            allowed={'COLLECTION_ONLY':{'RESEARCH_VISIBLE','VALIDATION_LOCKED','HOLDOUT_LOCKED'},'RESEARCH_VISIBLE':{'DEVELOPMENT_ELIGIBLE'},'DEVELOPMENT_ELIGIBLE':set(),'VALIDATION_LOCKED':set(),'HOLDOUT_LOCKED':set()}
            if target not in allowed[previous]:raise ContractError('prospective_lifecycle_transition_forbidden')
            self.audit.append(store,'lifecycle','lifecycle:'+uuid4().hex,dict(session_id=session_id,previous=previous,target=target,reason=reason,prior_revision=expected_revision))

    def read_for_research(self,session_id):
        state=self.state();session=state['sessions'][session_id]
        if session['lifecycle'] not in {'RESEARCH_VISIBLE','DEVELOPMENT_ELIGIBLE'}:raise ContractError('prospective_research_access_denied')
        return ResearchView(session_id,session['revision'],canonical_json(session))

    def validate_view(self,view):
        if type(view) is not ResearchView:raise ContractError('prospective_research_view_required')
        latest=self.read_for_research(view.session_id)
        if latest!=view:raise ContractError('stale_prospective_eligibility')
        return json.loads(view.payload)


def reconstruct(records):
    sessions={};raw={};normalized=set()
    for r in records:
        b=r['body'];sid=b.get('session_id')
        if r['kind']=='session':sessions[sid]={**b,'facts':[],'errors':[],'source_gaps':[],'revision':r['hash']}
        elif sid in sessions:
            sessions[sid]['revision']=r['hash']
            if r['kind']=='lifecycle':sessions[sid]['lifecycle']=b['target']
            elif r['kind']=='raw_receipt':raw[r['key']]=r
            elif r['kind']=='source_gap':sessions[sid]['source_gaps'].append(b)
            elif r['kind']=='normalized':
                receipt=raw.get(b['receipt_key'])
                if receipt is None or receipt['hash']!=b['receipt_hash']:raise ContractError('prospective_normalized_raw_link')
                rb=receipt['body']
                if rb['source_contract']!=SOURCES.get(rb['source']):raise ContractError('prospective_source_contract_changed')
                again=base64.b64decode(rb['raw_base64'],validate=True)
                if hashlib.sha256(again).hexdigest()!=rb['sha256']:raise ContractError('prospective_raw_hash_mismatch')
                try:expected=normalize(rb['source'],again);errors=['receipt_clock_regression'] if rb.get('clock_regression') else []
                except (ValueError,KeyError,TypeError) as exc:expected=[];errors=[str(exc) if isinstance(exc,ContractError) else 'unknown_source_semantics']
                if expected!=b['facts'] or errors!=b['errors']:raise ContractError('prospective_normalization_replay_mismatch')
                normalized.add(b['receipt_key']);sessions[sid]['errors'].extend(errors)
                sessions[sid]['facts'].extend(dict(**f,source=rb['source'],known_ns=rb['received_ns'],receipt_hash=receipt['hash']) for f in expected)
    for key,r in raw.items():
        if key not in normalized:sessions[r['body']['session_id']]['errors'].append('pending_raw_normalization')
    for session in sessions.values():
        entities={};versions={};errors=session['errors']
        for fact in session['facts']:
            if fact['kind']=='gap':errors.append('unresolved_source_gap')
            key=(fact['source'],fact['security_id'],fact['kind'],fact['entity_id'])
            versions.setdefault(key,[]).append(fact)
            prior=entities.get(key)
            if fact['operation']=='original':
                if prior is not None and {k:v for k,v in prior.items() if k not in {'known_ns','receipt_hash'}}!={k:v for k,v in fact.items() if k not in {'known_ns','receipt_hash'}}:
                    errors.append('conflicting_original_fact');continue
                if prior is None:entities[key]=fact
            elif prior is None:errors.append('unresolved_correction_or_void_target')
            elif fact['operation']=='void':entities[key]={**fact,'voided':True}
            else:entities[key]=fact
        session['interpreted']=[entities[k] for k in sorted(entities,key=str)]
        session['completeness']={}
        for identifier in session['security_ids']:
            checks={}
            for category in CATEGORIES:
                entering=[f for f in session['interpreted'] if not f.get('voided') and f['security_id']==identifier and f['kind']==category and f['operation']=='original'
                    and f['known_ns']<=session['open_ns'] and f['effective_ns'] is not None and f['effective_ns']<=session['open_ns']
                    and f['effective_to_ns'] is not None and f['effective_to_ns']>session['open_ns']]
                coverage=[f for f in session['interpreted'] if f['security_id']==identifier and f['kind']=='coverage' and not f.get('voided')
                    and f['source']=='fixture' and f['values'].get('category')==category and f['values'].get('semantics')=='explicit_fixture_interval'
                    and f['effective_ns']<=session['open_ns'] and f['effective_to_ns'] is not None and f['effective_to_ns']>=session['close_ns']
                    and f['known_ns']>=session['close_ns']]
                adequate=bool(coverage) and (bool(entering) or category in {'market','source_coverage'})
                if category=='round_lot':adequate=adequate and all(type(f['values'].get('shares_per_round_lot')) is int and f['values']['shares_per_round_lot']>0 for f in entering)
                if category=='identity':adequate=adequate and all(f['values'].get('security_class') and f['values'].get('listing_venue') for f in entering)
                if category=='trading_status':adequate=adequate and all(f['values'].get('state') in {'trading','halted','quote_only','suspended'} for f in entering)
                if category=='corporate_actions':adequate=adequate and all(f['values'].get('state') in {'explicit_no_actions','complete_action_snapshot'} for f in entering)
                if category=='luld':
                    def valid_bands(f):
                        try:
                            low,high=Decimal(f['values']['lower']),Decimal(f['values']['upper'])
                            return f['values'].get('state') in {'normal','limit','straddle','paused'} and low.is_finite() and high.is_finite() and 0<low<high
                        except (KeyError,ValueError,TypeError,InvalidOperation):return False
                    adequate=adequate and all(valid_bands(f) for f in entering)
                checks[category]='Complete' if adequate and not errors and not session['source_gaps'] else 'Source unavailable' if session['source_gaps'] else 'Incomplete'
            session['completeness'][identifier]=checks
        session['completeness_state']='Complete' if all(v=='Complete' for c in session['completeness'].values() for v in c.values()) else 'Incomplete'
        session['certifiable']=False;session['orders_enabled']=False
    state=dict(version='prospective-state-v1',sessions=sessions,orders_enabled=False,certification_authority=False)
    return {**state,'state_hash':digest(state)}


def summary(directory):
    records=read_records(directory,'prospective-evidence-v1');state=reconstruct(records)
    activity=[r['body'] for r in records if r['kind']=='activity']
    collecting=bool(activity and activity[-1]['state']!='stopped' and 0<=time.time_ns()-activity[-1]['at_ns']<120*10**9)
    # No prices, bands, identities, trade counts or source payloads from locked
    # sessions leak into UI/API summaries. Only lifecycle/completeness counts.
    all_sessions=list(state['sessions'].values())
    sessions=[s for s in all_sessions if not s['session_id'].startswith('collection:')]
    return dict(sessions=len(sessions),collection_windows=len(all_sessions)-len(sessions),complete=sum(s['completeness_state']=='Complete' for s in sessions),
        incomplete=sum(s['completeness_state']!='Complete' for s in sessions),
        collecting=collecting,lifecycle_counts={k:sum(s['lifecycle']==k for s in sessions) for k in sorted(LIFECYCLES)},
        state='Prospective evidence incomplete' if any(s['completeness_state']!='Complete' for s in sessions) else 'Prospective evidence complete' if sessions else 'Not collecting',
        certification='Separate admission required',orders_enabled=False)
