#!/usr/bin/env python3
"""Simple explicit local collector controls. Never installs an unattended service."""
from pathlib import Path
import fcntl
import json
import os
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from systematic_trader.service import assert_canonical_source
from systematic_trader.collection_service import status
from systematic_trader.certificate_audit import _atomic

DATA=ROOT/'.desktop-dev/data/collection-service'


def control(action):
    assert_canonical_source()
    if action=='status':return status(DATA)
    if action=='stop':
        if not DATA.exists():return {'state':'Stopped','orders_enabled':False}
        _atomic(DATA/'stop.request',{'requested_at_ns':time.time_ns()})
        return {'state':'Stop requested; pending worker shutdown','orders_enabled':False}
    if action=='restart':
        control('stop')
        DATA.mkdir(parents=True,exist_ok=True)
        for _ in range(300):
            with (DATA/'owner.lock').open('a') as owner:
                try:fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:pass
                else:break
            time.sleep(.1)
        else:return {'state':'Restart refused: existing worker has not stopped','orders_enabled':False}
        return control('start')
    if action!='start':raise ValueError('Choose start, stop, restart or status')
    DATA.mkdir(parents=True,exist_ok=True)
    with (DATA/'launch.lock').open('a') as launch:
        fcntl.flock(launch,fcntl.LOCK_EX)
        with (DATA/'owner.lock').open('a') as owner:
            try:fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return {'state':'Collector already running','orders_enabled':False}
            (DATA/'stop.request').unlink(missing_ok=True)
        with (DATA/'service.log').open('ab') as log:
            process=subprocess.Popen([str(ROOT/'.venv-dev/bin/python'),'-m','systematic_trader.collection_runtime',str(DATA)],cwd=ROOT,
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True)
        # Avoid a second launcher racing before the child acquires ownership.
        for _ in range(50):
            if process.poll() is not None:return {'state':'Start failed; inspect safe health status','orders_enabled':False}
            state=status(DATA)
            if state.get('running'):return state
            time.sleep(.1)
        return {'state':'Starting; refresh status for confirmation','orders_enabled':False}


if __name__=='__main__':
    try:print(json.dumps(control(sys.argv[1] if len(sys.argv)>1 else 'status'),indent=2))
    except Exception:print('Collector control failed closed. No trading authority enabled.');sys.exit(1)
