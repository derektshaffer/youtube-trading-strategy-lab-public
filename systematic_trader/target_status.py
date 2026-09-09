"""Read-only target campaign disposition, including failed-before-results attempts."""
from pathlib import Path
from .events import digest,ContractError
from .evidence_store import read_records
from .target_campaign import code_hash
from .preliminary import STATE


def summary(directory):
    root=Path(directory);rows=read_records(root/'target','bounded-campaign-v1')
    if not rows:return dict(state=STATE,candidates=[],orders_enabled=False)
    reg=next(r['body'] for r in rows if r['kind']=='target_registered')
    if reg['code_hash']!=code_hash():raise ContractError('target_campaign_code_changed')
    result=next((r['body'] for r in rows if r['kind']=='target_result'),None)
    if result:
        if result['report_hash']!=digest({k:v for k,v in result.items() if k!='report_hash'}):raise ContractError('target_result_changed')
        return result
    failures=[r['body'] for r in rows if r['kind']=='target_failed']
    preliminary=read_records(root/'preliminary','preliminary-research-v1')
    failed_before_inputs=bool(failures and not any(r['kind'] in {'preliminary_inputs','preliminary_result'} for r in preliminary))
    candidates=[dict(id=c['id'],classification='Blocked by data' if failures else 'Needs more research',
        reason=c.get('reason') or (failures[-1]['reason'] if failures else 'Not run'),metrics=None,
        state=STATE,certified=False,orders_enabled=False) for c in reg['spec']['candidates']]
    return dict(state=STATE,candidates=candidates,failed_before_inputs=failed_before_inputs,
        performance_results=0,engine_evaluations=0 if failed_before_inputs else None,
        reason=failures[-1]['reason'] if failures else 'Not run',
        audit_head=rows[-1]['hash'],registration_hash=digest(reg),preliminary_head=preliminary[-1]['hash'] if preliminary else None,
        reruns_remaining=0,orders_enabled=False,certified=False)
