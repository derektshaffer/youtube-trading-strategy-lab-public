"""Read-only allowlisted current sources. No subscription or order endpoints."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import json
import time
from urllib.request import Request,build_opener
from urllib.error import HTTPError

from .events import ContractError,timestamp_ns
from .prospective import ProspectiveRecorder
from .tradier import NoRedirect

SYMBOLS=('SPY','QQQ','IWM','AAPL','NVDA','AMD','TSLA','AMZN','META','MSFT')
DIRECTORIES=('https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt',
             'https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt')


def public_get(url):
    if url not in DIRECTORIES:raise ContractError('prospective_public_url_not_allowlisted')
    try:
        with build_opener(NoRedirect()).open(Request(url,headers={'User-Agent':'Trading-Lab-Prospective-Evidence/1.0'}),timeout=30) as response:
            raw=response.read(8*1024*1024+1)
            if len(raw)>8*1024*1024:raise ContractError('prospective_public_payload_too_large')
            return raw
    except HTTPError as exc:raise ContractError('prospective_public_http_'+str(exc.code)) from None
    except ContractError:raise
    except Exception:raise ContractError('prospective_public_source_unavailable') from None


def collect_once(directory,*,credentials_source='lab-dev-keychain'):
    recorder=ProspectiveRecorder(directory);now=time.time_ns();sid='collection:'+str(now)
    # This is explicitly a collection window, not an inferred exchange session.
    # Unresolved watch handles are never security identities or ticker crosswalks.
    recorder.register(sid,now,now+24*60*60*10**9,['unresolved-watch:'+s for s in SYMBOLS])
    recorder.activity(sid,'started')
    receipts=[];errors=[]
    for url in DIRECTORIES:
        try:
            raw=public_get(url);known=time.time_ns()
            recorder.ingest(sid,'nasdaq_directory',raw,received_ns=known,provenance={'url':url,'method':'GET'});receipts.append(('nasdaq_directory',raw,known,{'url':url,'method':'GET'}))
        except ContractError as exc:
            errors.append(str(exc));recorder.source_gap(sid,'nasdaq_directory',str(exc))
    assets=None;calendar=None
    try:
        from .__main__ import get_credentials
        from .data_acquisition import HistoricalAccess
        credentials=get_credentials(credentials_source)
        access=HistoricalAccess(credentials)
        today=datetime.now(ZoneInfo('America/New_York')).date()
        queries=[('assets','alpaca_assets',{'status':'active','asset_class':'us_equity'}),
                 ('calendar','alpaca_calendar',{'start':today.isoformat(),'end':(today+timedelta(days=7)).isoformat()}),
                 ('actions','alpaca_actions',{'start':today.isoformat(),'end':today.isoformat(),'limit':1000})]
        for kind,source,params in queries:
            try:
                raw=access.get(kind,params);known=time.time_ns()
                recorder.ingest(sid,source,raw,received_ns=known,provenance={'kind':kind,'params':params,'method':'GET'});receipts.append((source,raw,known,{'kind':kind,'params':params,'method':'GET'}))
                if kind=='assets':assets=json.loads(raw)
                if kind=='calendar':calendar=json.loads(raw)
            except ContractError as exc:errors.append(str(exc));recorder.source_gap(sid,source,str(exc))
        del credentials,access
    except Exception:
        errors.append('existing_lab_credentials_unavailable');recorder.source_gap(sid,'alpaca','existing_lab_credentials_unavailable')
    target=None
    if assets is not None and calendar is not None:
        ids={r['symbol']:'alpaca-asset:'+r['id'] for r in assets if r.get('symbol') in SYMBOLS and r.get('id')}
        upcoming=[]
        for row in calendar:
            zone=ZoneInfo('America/New_York')
            opened=timestamp_ns(datetime.fromisoformat(row['date']+'T'+row['open']).replace(tzinfo=zone).isoformat())
            closed=timestamp_ns(datetime.fromisoformat(row['date']+'T'+row['close']).replace(tzinfo=zone).isoformat())
            if closed>now:upcoming.append((row['date'],opened,closed))
        if len(ids)==len(SYMBOLS) and upcoming:
            target,opened,closed=sorted(upcoming)[0]
            recorder.register(target,opened,closed,[ids[s] for s in SYMBOLS])
            for source,raw,known,provenance in receipts:recorder.ingest(target,source,raw,received_ns=known,provenance={**provenance,'original_collection_window':sid})
            for source,reason in [('identity','continuous_listing_and_effective_intervals_unverified'),
                ('corporate_actions','publication_completeness_revisions_and_crosswalk_unverified'),
                ('round_lot','directory_to_stable_identity_crosswalk_unverified'),
                ('alpaca_sip','live_SIP_entitlement_BLOCKED_no_success_claim'),
                ('tradier','real_time_capture_UNVERIFIED'),
                ('administrative','entering_status_LULD_and_transition_coverage_unavailable')]:
                recorder.source_gap(target,source,reason)
    if target is None:recorder.source_gap(sid,'session','authoritative_future_session_or_identity_unavailable')
    recorder.activity(sid,'stopped')
    return dict(collection_window=sid,monitored_session=target,successful_raw_requests=len(receipts),errors=errors,
        lifecycle='COLLECTION_ONLY',continuous_service_running=False,certification=False,orders_enabled=False)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('directory');parser.add_argument('--credentials',default='lab-dev-keychain',choices=['environment','lab-keychain','lab-dev-keychain'])
    parser.add_argument('--cycles',type=int,default=1);parser.add_argument('--interval',type=int,default=300);args=parser.parse_args()
    if not 1<=args.cycles<=288 or args.interval<60:parser.error('Bounded 1..288 cycles with interval >=60 seconds required')
    for i in range(args.cycles):
        if i:time.sleep(args.interval)
        print(json.dumps(collect_once(args.directory,credentials_source=args.credentials)),flush=True)
