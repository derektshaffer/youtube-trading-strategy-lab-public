"""Official retrospective halt intervals, never a certificate of tradability."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import os
import time
import urllib.request
import xml.etree.ElementTree as ET
from .events import ContractError,digest,timestamp_ns
from .research_check import save

NS='{http://www.nasdaqtrader.com/}'


def parse_halts(raw, *, requested_date):
    root=ET.fromstring(raw);result=[]
    if root.tag!='rss' or root.find('channel') is None:raise ContractError('halt_response_not_rss')
    for item in root.findall('.//item'):
        def value(name):return (item.findtext(NS+name) or '').strip()
        day=datetime.strptime(value('HaltDate'),'%m/%d/%Y').date().isoformat()
        if day!=requested_date:raise ContractError('halt_feed_wrong_requested_date')
        def stamp(date,clock):
            if not date or not clock:return None
            dt=datetime.strptime(date+' '+clock,'%m/%d/%Y %H:%M:%S').replace(tzinfo=ZoneInfo('America/New_York'))
            return timestamp_ns(dt.isoformat())
        start=stamp(value('HaltDate'),value('HaltTime'))
        end=stamp(value('ResumptionDate'),value('ResumptionTradeTime'))
        if start is None or (end is not None and end<start):raise ContractError('halt_interval_time_order')
        body=dict(symbol=value('IssueSymbol'),market=value('Mkt'),reason=value('ReasonCode'),
            halt_ns=start,trade_resumption_ns=end,quote_resumption_ns=stamp(value('ResumptionDate'),value('ResumptionQuoteTime')),
            raw_sha256=hashlib.sha256(raw).hexdigest(),original_fields={c.tag:c.text for c in item if c.tag.startswith(NS)},
            availability='retrospective_effective_times_original_delivery_unknown',
            outside_interval_status='UNKNOWN',execution_authority='none')
        result.append({**body,'record_hash':digest(body)})
    if len({r['record_hash'] for r in result})!=len(result):raise ContractError('duplicate_halt_feed_record')
    return result


def status_bound(rows,symbol,when_ns):
    """A known interval can exclude fills; its absence cannot authorize them."""
    active=[r for r in rows if r['symbol']==symbol and r['halt_ns']<=when_ns and
            (r['trade_resumption_ns'] is None or when_ns<r['trade_resumption_ns'])]
    return dict(halted=True if active else None,reason='known_historical_halt' if active else 'historical_tradability_unverified',
                sources=[r['record_hash'] for r in active],fill_allowed=False)


def acquire_days(directory,days):
    """Respect Nasdaq's at-most-once-per-minute RSS request guidance."""
    root=Path(directory);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    plan=dict(version='official-halt-acquisition-v1',days=days,minimum_request_spacing_seconds=61,
        coverage='halt_start_date_only_prior_ongoing_halts_and_delivery_not_certified')
    target=root/'plan.json'
    if target.exists():
        if json.loads(target.read_text())!=plan:raise ContractError('halt_acquisition_plan_mismatch')
    else:save(target,plan)
    last=max((json.loads(p.read_text()).get('retrieved_ns',0) for p in root.glob('*.meta.json')),default=0)
    for day in days:
        path=root/(day+'.raw.xml');meta_path=root/(day+'.meta.json')
        if path.exists() and meta_path.exists():
            raw=path.read_bytes();meta=json.loads(meta_path.read_text())
            if hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise ContractError('halt_archive_hash_mismatch')
        elif path.exists() or meta_path.exists():raise ContractError('halt_archive_orphan_requires_review')
        else:
            delay=61-(time.time_ns()-last)/1e9
            if delay>0:time.sleep(delay)
            query=datetime.fromisoformat(day).strftime('%m%d%Y')
            url='https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts&haltdate='+query
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'TradingLabHistoricalAudit/1.0'}),timeout=40) as response:
                raw=response.read(2_000_001)
                if response.status!=200 or len(raw)>2_000_000:raise ContractError('halt_response_invalid')
            last=time.time_ns()
            with path.open('xb') as handle:
                path.chmod(0o600);handle.write(raw);handle.flush();os.fsync(handle.fileno())
            meta=dict(url=url,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),retrieved_ns=last)
            save(meta_path,meta)
        rows=parse_halts(raw,requested_date=day)
        print(json.dumps(dict(day=day,records=len(rows),sha256=meta['sha256'])),flush=True)
    summary=dict(plan=plan,days=len(days),records=sum(len(parse_halts((root/(d+'.raw.xml')).read_bytes(),requested_date=d)) for d in days),
        heads={d:json.loads((root/(d+'.meta.json')).read_text())['sha256'] for d in days},status_outside_observed_intervals='UNKNOWN')
    result={**summary,'manifest_hash':digest(summary)}
    if (root/'complete.json').exists():
        if json.loads((root/'complete.json').read_text())!=result:raise ContractError('halt_completion_changed')
    else:save(root/'complete.json',result)
    return result


def import_archive(directory,journal_directory):
    """Use the existing Ledger/Event/MarketState path with actual import clocks.

    Original XML receipts and Nasdaq provenance remain intact. Security joins
    remain unresolved; no Alpaca identity or historical arrival is fabricated.
    A recorded resumption ends a halt mask but does not certify tradability.
    """
    from .events import Event,SYMBOL
    from .ledger import Ledger
    from .market_state import MarketState
    root=Path(directory);target=Path(journal_directory)
    if target.exists():raise ContractError('halt_import_requires_new_journal')
    manifest=json.loads((root/'complete.json').read_text())
    if manifest['manifest_hash']!=digest({k:v for k,v in manifest.items() if k!='manifest_hash'}):raise ContractError('halt_manifest_hash_mismatch')
    with Ledger(target) as ledger:
        for day,sha in sorted(manifest['heads'].items()):
            raw=(root/(day+'.raw.xml')).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=sha:raise ContractError('halt_archive_hash_mismatch')
            parsed=parse_halts(raw,requested_date=day);now=time.time_ns();events=[]
            raw_id=ledger.receipt(raw,dict(adapter_version='nasdaq-halt-history-v1',provider='nasdaq',feed='listed_halts_rss',origin='import',received_ns=now,source_sha256=sha))
            observations=[]
            for row in parsed:
                observations.append((row['halt_ns'],'H',row))
                if row['trade_resumption_ns'] is not None:observations.append((row['trade_resumption_ns'],'UNKNOWN',row))
            for index,(stamp,code,row) in enumerate(sorted(observations,key=lambda x:(x[0],x[2]['symbol'],x[1]))):
                p=dict(status_code=code,reason_code=row['reason'],tape=None,provider_details=dict(
                    original_record=row,original_publication_ns=None,actual_import_ns=now,normalized_status='known_halt' if code=='H' else 'resumed_but_tradability_unverified'))
                kind='market.status';symbol=row['symbol']
                if not SYMBOL.fullmatch(symbol):
                    kind='recorder.lifecycle';symbol=None
                    p=dict(state='unsupported_halt_symbol_preserved_without_security_join',original_record=row,normalized_status=code)
                events.append(Event(event_id=f'halt-import:{raw_id}:{index}',run_id='nasdaq-halt-history-v1',connection_id=day,
                    provider='nasdaq',feed='listed_halts_rss',origin='import',received_ns=now,received_monotonic_ns=0,normalized_ns=now,
                    event_type=kind,source_time_ns=stamp,source_event_id=row['record_hash'],source_sequence=None,
                    symbol=symbol,instrument_id=None,identity_event_id=None,raw_id=raw_id,raw_index=index,payload=p,
                    schema_version=2,adapter_version='nasdaq-halt-history-v1',content_hash=digest([row['record_hash'],stamp,code]),
                    quality_flags=('historical_backfill','historical_identity_unresolved','original_arrival_unverified','not_live_evidence')))
            if not events:
                events.append(Event(event_id=f'halt-import:{raw_id}:0',run_id='nasdaq-halt-history-v1',connection_id=day,
                    provider='nasdaq',feed='listed_halts_rss',origin='import',received_ns=now,received_monotonic_ns=0,normalized_ns=now,
                    event_type='recorder.lifecycle',source_time_ns=None,source_event_id=None,source_sequence=None,symbol=None,
                    instrument_id=None,identity_event_id=None,raw_id=raw_id,raw_index=0,payload=dict(state='empty_halt_start_date_query_not_tradability_evidence'),
                    schema_version=2,adapter_version='nasdaq-halt-history-v1',content_hash=digest([sha,day]),quality_flags=('not_live_evidence',)))
            ledger.append_events(raw_id,events)
        verify=ledger.verify();first=list(ledger.replay(through_seq=ledger.watermark()))
    with Ledger(target,read_only=True) as ledger:
        second=list(ledger.replay(through_seq=ledger.watermark()))
        if first!=second or verify!=ledger.verify():raise ContractError('halt_import_replay_mismatch')
        state=MarketState()
        for event in second:state.apply(event)
    result=dict(version='native-halt-import-v1',journal=verify,replay_hash=digest(second),replay_identical=True,
        state_hash=state.fingerprint(),resolved_security_identities=len(state.instruments),
        status='native_canonical_import_original_delivery_and_identity_unresolved',execution_authority='none')
    return result
