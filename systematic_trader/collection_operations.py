"""Frozen target-pool scheduling and explicit partial-feed operating policy."""
from datetime import datetime,timedelta
import calendar
from zoneinfo import ZoneInfo
from .events import ContractError,timestamp_ns,digest

VERSION='live-collector-operations-v1'
# Preexisting non-control archive names, selected without examining performance.
POOL=('ASPI','AVR','DGICB','FMBH','MDXH','POWL','WSBC')
LIMIT=5
POLICY=dict(version=VERSION,pool=list(POOL),limit=LIMIT,order='alphabetical',
    eligibility='observed active us_equity asset with unique UUID and exchange; monitoring only',
    historical_small_cap_claim=False,late_additions=False,source='alpaca_iex',orders_enabled=False)


def preparation_ns(reg):
    opened=datetime.fromtimestamp(reg['open_ns']//10**9,ZoneInfo('America/New_York'))
    return calendar.timegm(opened.replace(hour=4,minute=0,second=0,microsecond=0).utctimetuple())*10**9


def phase(reg,now_ns):
    if now_ns<preparation_ns(reg):return 'Awaiting session'
    if now_ns<reg['open_ns']:return 'Premarket'
    if now_ns<reg['close_ns']:return 'RTH'
    if now_ns<reg['close_ns']+30*60*10**9:return 'Post-close'
    return 'Seal due'


def select_pool(assets):
    found={}
    for symbol in POOL:
        matches=[a for a in assets if a.get('symbol')==symbol and a.get('values',{}).get('status')=='active'
                 and a.get('values',{}).get('class')=='us_equity' and a.get('values',{}).get('exchange')]
        if len(matches)>1:raise ContractError('ambiguous_current_asset_identity')
        if matches:found[symbol]=matches[0]['security_id']
    selected=dict(list(found.items())[:LIMIT])
    if not selected:raise ContractError('target_monitor_pool_unavailable')
    if len(set(selected.values()))!=len(selected):raise ContractError('duplicate_target_identity')
    return selected


def active_sessions(service):
    retired={r['body']['session_id'] for r in service.records() if r['kind']=='session_retired'}
    return {sid:r for sid,r in service.registrations().items() if sid not in retired}


def freeze_session(service,date,reg,assets,now_ns):
    policy=[r['body'] for r in service.records() if r['kind']=='operational_policy']
    if not policy:service.append('operational_policy',POLICY)
    elif policy!=[POLICY]:raise ContractError('operational_policy_changed')
    sid=date+'|target-v1'
    existing=service.registrations().get(sid)
    if existing:
        # Never replace the frozen universe with later survivors or new movers.
        identities=existing['identities']
        current={f['security_id']:f for f in assets}
        if any(i not in current or current[i]['symbol']!=s for s,i in identities.items()):
            service.gap(sid,'identity','frozen_identity_changed_or_missing',now_ns)
        return sid
    identities=select_pool(assets)
    service.register(sid,reg['open_ns'],reg['close_ns'],identities,
        provenance={'source':'recorded_alpaca_calendar_assets','policy_hash':digest(POLICY),'calendar_date':date})
    service.append('universe_frozen',dict(session_id=sid,at_ns=now_ns,identities=identities,policy_hash=digest(POLICY)))
    if now_ns>preparation_ns(reg):service.gap(sid,'service','premarket_observation_started_late',preparation_ns(reg),now_ns)
    # Retire only a legacy registration that has never captured market data and
    # whose RTH has not begun; its raw evidence and original registration remain.
    for old,prior in active_sessions(service).items():
        if old==sid or prior['open_ns']!=reg['open_ns'] or now_ns>=prior['open_ns']:continue
        rows=service._replay_segments(old)[0]
        if any(r['kind']=='raw_receipt' and r['body']['source'] in {'alpaca_sip','tradier','alpaca_iex'} for r in rows):continue
        service.append('session_retired',dict(session_id=old,replacement=sid,at_ns=now_ns,reason='preopen_target_policy_supersedes_unstarted_legacy_monitoring'))
    return sid
