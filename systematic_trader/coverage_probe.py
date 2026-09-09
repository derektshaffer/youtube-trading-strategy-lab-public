"""Preregistered account coverage probes; never strategy or holdout inputs."""
from collections import Counter
from datetime import datetime,timedelta
from pathlib import Path
import json
import time
from zoneinfo import ZoneInfo
from .data_acquisition import acquire,verified_pages
from .events import ContractError,digest
from .research_check import save


def plan():
    requests=[]
    symbols='AAPL,POWL,MDXH,NSEC,REGI,TRMT,NEOS'
    for day in ('2015-12-31','2016-01-04','2016-01-05','2020-03-16','2021-01-04','2022-01-03','2024-12-02','2025-02-28','2026-09-04'):
        for hour in ('09:30','04:00','19:59') if day in {'2016-01-04','2026-09-04'} else ('09:30',):
            dt=datetime.fromisoformat(day+'T'+hour).replace(tzinfo=ZoneInfo('America/New_York'))
            for kind in ('bars','quotes','trades'):
                params=dict(symbols=symbols,start=dt.isoformat(),end=(dt+timedelta(seconds=59,microseconds=999999)).isoformat(),feed='sip',asof='-',sort='asc',limit=10000)
                if kind=='bars':params.update(timeframe='1Min',adjustment='raw')
                requests.append(dict(name=day+'-'+hour.replace(':','')+'-'+kind,kind=kind,params=params,max_pages=10))
    requests.extend([
        dict(name='calendar-coverage',kind='calendar',params=dict(start='2015-12-31',end='2026-09-04'),max_pages=1),
        dict(name='actions-pilot-wide',kind='actions',params=dict(start='2024-11-01',end='2025-04-30',types='reverse_split,forward_split,name_change,worthless_removal,reorganization,cash_merger,stock_merger',limit=1000,data_quality='all',sort='asc'),max_pages=20),
    ])
    body=dict(version='historical-account-probes-v1',purpose='coverage_only_not_strategy_selection',requests=requests,
        sampled_coverage_not_global_reliability=True,holdout_access=False,execution_authority='none')
    return {**body,'plan_hash':digest(body)}


def run(access,directory):
    root=Path(directory);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    protocol=plan();saved=root/'probe-plan.json'
    if saved.exists():
        if json.loads(saved.read_text())!=protocol:raise ContractError('coverage_probe_plan_mismatch')
    else:save(saved,protocol)
    results=[]
    for req in protocol['requests']:
        output=root/req['name']
        try:
            manifest=acquire(access,output,kind=req['kind'],params=req['params'],max_pages=req['max_pages'])
            counts=Counter();fields=set();first=last=None
            for page,meta in verified_pages(output):
                groups={'reference':page} if isinstance(page,list) else page['corporate_actions' if req['kind']=='actions' else req['kind']]
                for symbol,rows in groups.items():
                    counts[symbol]+=len(rows)
                    for r in rows:
                        fields.update(r)
                        if 't' in r:
                            first=min(first,r['t']) if first else r['t'];last=max(last,r['t']) if last else r['t']
            result=dict(name=req['name'],status='complete',counts=dict(counts),fields=sorted(fields),first_source_time=first,last_source_time=last,manifest=manifest)
        except ContractError as exc:
            result=dict(name=req['name'],status='blocked',reason=str(exc))
            error=output/'safe-error.json'
            if not error.exists():save(error,result)
        results.append(result)
        print(json.dumps({k:v for k,v in result.items() if k not in {'manifest','fields'}}),flush=True)
        time.sleep(.4)
    body=dict(version='historical-coverage-results-v1',plan_hash=protocol['plan_hash'],probes=results,
        reliable_continuous_coverage='not_established_by_spot_probes',execution_authority='none')
    result={**body,'result_hash':digest(body)}
    save(root/'probe-results.json',result)
    return result
