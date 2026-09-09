"""Explicit local service scheduler; no install, billing, research access or orders."""
from pathlib import Path
import base64
import fcntl
import json
import os
import signal
import threading
import time
from uuid import uuid4

from .collection_service import CollectionService
from .events import ContractError
from .collector_power import CollectorAwake
from .prospective_sources import collect_once,SYMBOLS
from .evidence_store import read_records
from .service import assert_canonical_source
from .collection_operations import freeze_session,active_sessions,phase,preparation_ns


def current_reference_cycle(service, *, collector=collect_once, now_ns=None):
    now_ns=time.time_ns() if now_ns is None else now_ns
    intake=service.root/'reference-intake'/uuid4().hex
    # Raw reference receipts land before parsing in the existing recorder.
    result=collector(intake,credentials_source='lab-dev-keychain')
    sid=result['monitored_session']
    records=read_records(intake,'prospective-evidence-v1')
    if sid is None:
        service.append('source_failure',dict(at_ns=now_ns,reason='authoritative_session_or_identity_unavailable',intake=intake.name))
        for prior_sid,prior in service.registrations().items():
            if prior['close_ns']>=now_ns:
                service.gap(prior_sid,'reference','reference_cycle_incomplete',now_ns)
        return None
    reg=next(r['body'] for r in records if r['kind']=='session' and r['body']['session_id']==sid)
    assets=[f for r in records if r['kind']=='normalized' and r['body']['session_id']==sid for f in r['body']['facts'] if f['kind']=='identity_observation']
    intake_sid=sid
    # Registration provenance binds the same calendar/identity scope across polls,
    # while every actual source revision remains in its own raw receipt.
    try:sid=freeze_session(service,intake_sid,reg,assets,now_ns)
    except ContractError as exc:
        if str(exc) not in {'target_monitor_pool_unavailable','ambiguous_current_asset_identity','duplicate_target_identity'}:raise
        service.append('source_failure',dict(at_ns=now_ns,reason=str(exc),intake=intake.name))
        for old,prior in active_sessions(service).items():
            if prior['close_ns']>=now_ns:service.gap(old,'identity',str(exc),now_ns)
        return None
    for r in records:
        b=r['body']
        if b.get('session_id')!=intake_sid:continue
        if r['kind']=='raw_receipt':
            service.ingest(sid,b['source'],base64.b64decode(b['raw_base64']),received_ns=b['received_ns'],connection_id='reference:'+intake.name,
                           provenance={**b['provenance'],'intake_receipt_hash':r['hash']})
            service.health(sid,b['source'],'Healthy','snapshot_received_not_complete_interval',b['received_ns'])
        elif r['kind']=='source_gap':
            service.gap(sid,b['source'],b['reason'],reg['open_ns'],reg['close_ns'])
    service.append('reference_cycle',dict(session_id=sid,at_ns=now_ns,intake=intake.name,requests=result['successful_raw_requests']))
    service.gap(sid,'alpaca_iex','IEX_only_not_consolidated_status_LULD_and_continuity_unsourced',reg['open_ns'],reg['close_ns'])
    service.health(sid,'alpaca_sip','Source unavailable','existing_SIP_entitlement_BLOCKED',now_ns)
    try:
        from .tradier import credentials
        token=credentials('lab-dev-keychain');del token
    except Exception:service.health(sid,'tradier','Source unavailable','existing_lab_tradier_credentials_unavailable',now_ns)
    else:service.health(sid,'tradier','Degraded','token_available_adapter_retained_not_auto_activated',now_ns)
    return sid


def serve(directory, *, once=False, cycle=current_reference_cycle, stop=None, clock=time.time_ns, keep_awake=False):
    assert_canonical_source()
    service=CollectionService(directory);stop=stop or threading.Event()
    with (service.root/'owner.lock').open('a') as owner:
        try:fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ContractError('collector_already_running') from None
        with CollectorAwake(enabled=keep_awake) as power:
            service.start(clock());workers={};fatal=[]
            if power.process is not None:
                service.append("power_assertion", dict(at_ns=clock(), collector_pid=os.getpid(), assertion_pid=power.process.pid, flags=["-i", "-s", "-w"], lifetime="collector_process"))
            attempts=[r['body']['at_ns'] for r in service.records() if r['kind']=='reference_attempt']
            last_reference=attempts[-1] if attempts else None
            try:
                while not stop.is_set():
                    power.check()
                    now=clock()
                    if (service.root/'stop.request').exists():break
                    if last_reference is None or now-last_reference>=3600*10**9:
                        service.append('reference_attempt',dict(at_ns=now,cooldown_seconds=3600))
                        cycle(service,now_ns=now);last_reference=now
                    now=clock()
                    if fatal:raise ContractError('market_worker_integrity_failure')
                    for sid,reg in active_sessions(service).items():
                        session_phase=phase(reg,now)
                        prior_phase=next((r['body']['phase'] for r in reversed(service.records()) if r['kind']=='session_phase' and r['body']['session_id']==sid),None)
                        if prior_phase!=session_phase:service.append('session_phase',dict(session_id=sid,at_ns=now,phase=session_phase))
                        if now>=reg['close_ns']+(0 if once else 30*60*10**9):
                            if sid in workers:
                                workers[sid][0].set()
                                if workers[sid][1].ident is not None:workers[sid][1].join(timeout=20)
                                if workers[sid][1].is_alive():raise ContractError('market_worker_shutdown_timeout')
                            seals=[r for r in service.records() if r['kind']=='seal' and r['body']['session_id']==sid]
                            latest=service.replay(sid)['state']['sessions'][sid]
                            from .events import digest
                            if not seals or seals[-1]['body']['session_hash']!=digest(latest):service.seal(sid,now)
                        elif preparation_ns(reg)<=now<reg['close_ns']+30*60*10**9 and sid not in workers and not once:
                            # SIP certification remains blocked. Do not retry entitlement as a service loop.
                            service.health(sid,'alpaca_sip','Source unavailable','existing_SIP_entitlement_BLOCKED',now)
                            try:
                                from .tradier import credentials
                                token=credentials('lab-dev-keychain')
                            except Exception:
                                service.health(sid,'tradier','Source unavailable','existing_lab_tradier_credentials_unavailable',now)
                                service.gap(sid,'tradier','existing_lab_tradier_credentials_unavailable',now)
                            else:
                                del token
                                service.health(sid,'tradier','Degraded','token_available_adapter_retained_not_auto_activated',now)
                            try:
                                from .__main__ import get_credentials
                                pair=get_credentials('lab-dev-keychain')
                            except Exception:
                                service.health(sid,'alpaca_iex','Source unavailable','existing_lab_alpaca_credentials_unavailable',now)
                                service.gap(sid,'alpaca_iex','existing_lab_alpaca_credentials_unavailable',now)
                                workers[sid]=(threading.Event(),threading.Thread());continue
                            from .collection_transport import reconnecting_stream
                            halted=threading.Event()
                            def run(sid=sid,pair=pair,halted=halted,symbols=list(reg['identities'])):
                                try:reconnecting_stream(service,sid,'alpaca_iex',pair,symbols,halted,persistent=True)
                                except Exception:fatal.append('market_worker_integrity_failure')
                            thread=threading.Thread(target=run,daemon=True,name='market-data-only');thread.start();workers[sid]=(halted,thread)
                    service.heartbeat(clock(),time.monotonic_ns())
                    if once:break
                    stop.wait(30)
            finally:
                for halted,thread in workers.values():
                    halted.set()
                    if thread.ident is not None:thread.join(timeout=20)
                service.stop(clock())
    return service


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('directory');parser.add_argument('--once',action='store_true');args=parser.parse_args()
    stop=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set());signal.signal(signal.SIGINT,lambda *_:stop.set())
    try:serve(args.directory,once=args.once,stop=stop,keep_awake=not args.once)
    except Exception:
        # Neither raw HTTP errors nor secrets may enter unattended logs.
        print('Collector stopped: source or evidence failure. Inspect the safe health status.',flush=True)
        raise SystemExit(1)
