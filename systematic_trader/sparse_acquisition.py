"""Fixed development/warm-up tape acquisition; no survivor or outcome selection."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from .data_acquisition import acquire, verified_pages
from .events import ContractError, digest
from .research_check import save
from .research_split import ResearchSplit

SPARSE_SYMBOLS = ('ASPI', 'AVR', 'DGICB', 'FMBH', 'MDXH', 'POWL', 'WSBC')


def plan(calendar):
    requests = []
    for day, c in sorted(calendar.items()):
        if day > '2025-02-28':
            continue
        ResearchSplit().authorize(c['open'], c['close'], purpose='discovery_development')
        start = datetime.fromtimestamp(c['open']//10**9, timezone.utc).isoformat()
        # Inclusive native query; keep the closing auction at 16:00 outside RTH.
        end = datetime.fromtimestamp(c['close']//10**9-1, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+'.999999999Z'
        requests.append(dict(day=day, kind='trades', params=dict(symbols=','.join(SPARSE_SYMBOLS),
            start=start, end=end, feed='sip', asof='-', sort='asc', limit=10000), max_pages=40))
    body = dict(version='sparse-native-tape-plan-v1', requests=requests, symbols=list(SPARSE_SYMBOLS),
        selection='all_seven_non_infrastructure_series_with_pilot_bars_no_outcome_selection',
        window='all_regular_warmup_and_development_sessions_only', holdout_access=False,
        execution_authority='none', failure_policy='preserve_raw_and_record_failure_no_retry')
    return {**body, 'plan_hash': digest(body)}


def run(access, directory, calendar):
    root = Path(directory); root.mkdir(parents=True, exist_ok=True, mode=0o700)
    protocol = plan(calendar); target = root/'plan.json'
    if target.exists():
        if json.loads(target.read_text()) != protocol: raise ContractError('sparse_acquisition_plan_changed')
    else: save(target, protocol)
    results = []
    for req in protocol['requests']:
        out = root/req['day']/'trades'
        try:
            manifest = acquire(access, out, kind=req['kind'], params=req['params'], max_pages=req['max_pages'])
            counts = {s: 0 for s in SPARSE_SYMBOLS}
            for page, meta in verified_pages(out):
                for s, rows in page['trades'].items():
                    if s not in counts: raise ContractError('unrequested_sparse_symbol')
                    counts[s] += len(rows)
            row = dict(day=req['day'], status='complete', counts=counts, manifest=manifest)
        except ContractError as exc:
            row = dict(day=req['day'], status='blocked', reason=str(exc))
            p = out/'safe-error.json'
            if not p.exists(): save(p, row)
        results.append(row)
        print(json.dumps({k:v for k,v in row.items() if k!='manifest'}), flush=True)
        time.sleep(.4)
    body = dict(version='sparse-native-tape-acquisition-v1', plan_hash=protocol['plan_hash'], results=results)
    result = {**body, 'result_hash': digest(body)}
    save(root/'results.json', result)
    return result
