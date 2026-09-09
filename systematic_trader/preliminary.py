"""Explicit cheap historical research, isolated from trusted certification."""
from dataclasses import asdict
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .events import ContractError, canonical_json, digest, timestamp_ns
from .evidence_store import EvidenceStore, read_records
from .research_split import ResearchSplit

STATE='PRELIMINARY_RESEARCH_ONLY'
WARNING='Preliminary research only — not certified evidence of profitability'
ROOT=Path(__file__).resolve().parent.parent
PANEL=ROOT/'.systematic-trader/real-history/certification-v2/panel'


def freeze(value):return json.loads(canonical_json(value))


def _request(request):
    request=freeze(request)
    if set(request)!={'dataset','symbols','sessions','hypotheses'}:raise ContractError('preliminary_request_schema')
    if request['dataset'] not in {'owned-panel-v1','fixture-positive','fixture-losing','fixture-no-trade'}:raise ContractError('unregistered_preliminary_dataset')
    for name,limit in [('symbols',10),('sessions',20)]:
        values=request[name]
        if not isinstance(values,list) or not 1<=len(values)<=limit or len(set(values))!=len(values):raise ContractError('preliminary_scope_required')
    for day in request['sessions']:
        start=timestamp_ns(day+'T00:00:00-05:00');end=timestamp_ns((datetime.fromisoformat(day)+timedelta(days=1)).date().isoformat()+'T00:00:00-05:00')
        ResearchSplit().authorize(start,end,purpose='strategy_development')
    hypotheses=request['hypotheses']
    if not isinstance(hypotheses,list) or not 1<=len(hypotheses)<=4:raise ContractError('predeclared_hypothesis_budget_exceeded')
    if any(set(h)!={'id','strategy'} for h in hypotheses) or len({h['id'] for h in hypotheses})!=len(hypotheses):raise ContractError('preliminary_hypothesis_schema')
    for h in hypotheses:
        if not isinstance(h['id'],str) or not h['id'] or not isinstance(h['strategy'],dict):raise ContractError('preliminary_hypothesis_invalid')
        if any(k in h['strategy'] for k in ('certificate','certificate_id','runner_authorized','production_eligible','promotion_gate_passed')):raise ContractError('preliminary_cannot_assert_certification')
    return request


def _data(request):
    cells={};lineage={};warnings=['Final exports have unverified original availability/revision timing',
        'Historical identity continuity, corporate actions, status/LULD and native lot history are not certified',
        'Sparse observations are not filled; bar-model fills cannot bound execution across unknown halts',
        'Fixed retrospective archive panel; no unbiased universe or validated out-of-sample claim']
    if request['dataset'].startswith('fixture-'):
        if request['symbols']!=['FIXTURE'] or request['sessions']!=['2025-01-08']:raise ContractError('preliminary_fixture_scope')
        end=110 if request['dataset']=='fixture-positive' else 90 if request['dataset']=='fixture-losing' else 100
        rows=[dict(t=f'2025-01-08T14:{30+i:02d}:00Z',o=100,h=101,l=99,c=100,v=1000) for i in range(5)]
        rows[-1].update(o=end,h=end+1,l=end-1,c=end)
        cells['FIXTURE|2025-01-08']=rows;lineage['fixture']=digest(rows)
        return dict(cells=cells,lineage=lineage,source_kind='fixture',warnings=warnings)
    from .research_panel import ResearchPanel
    from .research_fixture import stamp
    panel=ResearchPanel(PANEL)
    try:
        if not set(request['symbols'])<=set(panel.manifest['symbols']):raise ContractError('preliminary_symbol_not_in_owned_panel')
        for day in request['sessions']:
            data=panel.session(day,purpose='strategy_development')
            for symbol in request['symbols']:
                observations=[r for r in data.get(symbol,[]) if r['segment']=='regular']
                if not observations:raise ContractError('preliminary_requested_cell_has_no_observations')
                key=symbol+'|'+day
                cells[key]=[dict(t=stamp(r['stamp']),o=r['payload']['open'],h=r['payload']['high'],l=r['payload']['low'],c=r['payload']['close'],v=r['payload']['volume_shares']) for r in observations]
                lineage[key]=[r['lineage'] for r in observations]
        return dict(cells=cells,lineage=lineage,source_kind='owned_historical_export',
            manifest_hash=panel.manifest['manifest_hash'],database_hash=panel.manifest['normalized_database_sha256'],
            archive_identity='source-manifest + literal archive series; NOT a stable security identity',warnings=warnings)
    finally:panel.close()


def _calculate(request,data):
    from youtube_strategy_engine import run_backtest,BacktestSettings
    from .preliminary_scope import _authorize,_TOKEN
    settings=BacktestSettings(allow_extended_hours=False,ignore_strategy_session_end=False)
    outputs=[]
    for hypothesis in request['hypotheses']:
        for symbol in request['symbols']:
            rows=[row for day in sorted(request['sessions']) for row in data['cells'][symbol+'|'+day]]
            with _authorize(rows,hypothesis['strategy'],symbol,settings,_TOKEN):
                result=run_backtest(rows,hypothesis['strategy'],symbol,settings)
            # Old engine partition names are descriptive within DEVELOPMENT only.
            def label(x):
                names={'in_sample':'development_earlier_partition','out_of_sample':'development_later_partition','holdout_start':'development_partition_start'}
                if isinstance(x,dict):return {names.get(k,k):label(v) for k,v in x.items()}
                if isinstance(x,list):return [label(v) for v in x]
                return names.get(x,x) if isinstance(x,str) else x
            outputs.append(dict(hypothesis_id=hypothesis['id'],symbol=symbol,state=STATE,warning=WARNING,output=label(result)))
    rankings=sorted([dict(hypothesis_id=h['id'],rough_net_pnl=sum(r['output']['metrics']['net_pnl'] for r in outputs if r['hypothesis_id']==h['id']),state=STATE)
        for h in request['hypotheses']],key=lambda h:(-h['rough_net_pnl'],h['hypothesis_id']))
    return dict(outputs=outputs,research_rankings=rankings,ranking_meaning='rough sum of independently funded symbol accounts; not portfolio or validation',settings=asdict(settings))


def engine_hash():
    names=['youtube_strategy_engine.py','systematic_trader/preliminary.py','systematic_trader/preliminary_scope.py']
    return digest({n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names})


class PreliminaryResearch:
    def __init__(self,directory):self.audit=EvidenceStore(directory,'preliminary-research-v1')

    def run(self,request):
        attempt=uuid4().hex
        with self.audit.locked() as store:
            self.audit.append(store,'preliminary_attempt',attempt,dict(state=STATE,orders_enabled=False))
            try:
                request=_request(request)
                registration=dict(request=request,protocol='bounded-preliminary-v1',state=STATE,engine_hash=engine_hash(),
                    allowed_meaning='hypothesis screening/rejection only',certification_authority=False,orders_enabled=False)
                self.audit.append(store,'preliminary_registered','registered:'+attempt,registration)
                data=_data(request)
                self.audit.append(store,'preliminary_inputs','inputs:'+attempt,dict(state=STATE,data=data,input_hash=digest(data)))
                output=_calculate(request,data)
                result=dict(version='preliminary-result-v1',state=STATE,warning=WARNING,run_id=attempt,
                    registration=registration,input_hash=digest(data),performance=output,warnings=data['warnings'],
                    certified=False,tier1_satisfied=False,runner_authorized=False,paper_eligible=False,live_eligible=False,
                    review_required=True,review_checkpoint='evidence_interpretation',production_eligible=False,orders_enabled=False,execution_authority='none')
                result['result_hash']=digest(result)
                self.audit.append(store,'preliminary_result','result:'+attempt,result)
                return result
            except Exception as exc:
                reason=str(exc) if isinstance(exc,ContractError) else 'preliminary_input_or_engine_failure'
                self.audit.append(store,'preliminary_failed','failed:'+attempt,dict(state=STATE,reason=reason,orders_enabled=False))
                raise ContractError(reason) from None

    def replay(self,run_id):
        rows=self.audit.replay();result=next((r['body'] for r in rows if r['key']=='result:'+run_id),None)
        if result is None or result['state']!=STATE:raise ContractError('preliminary_result_unavailable')
        if result['registration']['engine_hash']!=engine_hash():raise ContractError('preliminary_replay_code_changed')
        data=next(r['body']['data'] for r in rows if r['key']=='inputs:'+run_id)
        if digest(data)!=result['input_hash'] or result['result_hash']!=digest({k:v for k,v in result.items() if k!='result_hash'}):raise ContractError('preliminary_provenance_changed')
        repeated=_calculate(_request(result['registration']['request']),data)
        if repeated!=result['performance']:raise ContractError('preliminary_replay_mismatch')
        return dict(state=STATE,exact_match=True,result_hash=result['result_hash'],orders_enabled=False)

    def review_packet(self,run_id):
        result=next(r['body'] for r in self.audit.replay() if r['key']=='result:'+run_id)
        content=canonical_json(dict(result_hash=result['result_hash'],input_hash=result['input_hash'],rankings=result['performance']['research_rankings'],state=STATE))
        return dict(artifact_id='preliminary:'+run_id,checkpoint='evidence_interpretation',
            evidence=[dict(id='preliminary-result',content=content,sha256=hashlib.sha256(content.encode()).hexdigest(),
                provenance=dict(uri='local-preliminary:'+result['result_hash'],retrieved_at=datetime.now().astimezone().isoformat(),publisher='Trading Lab preliminary research'))],
            assumptions=[WARNING,*result['warnings']],specification=canonical_json(result['registration']['request']),
            deterministic_context=dict(state=STATE,certified=False,runner_authorized=False,result_hash=result['result_hash']),resolutions=[])


def summary(directory):
    rows=read_records(directory,'preliminary-research-v1')
    results=[r['body'] for r in rows if r['kind']=='preliminary_result']
    return dict(state=STATE,warning=WARNING,runs=len(results),last_result_hash=results[-1]['result_hash'] if results else None,
        certified=False,orders_enabled=False)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('directory');parser.add_argument('request_json');args=parser.parse_args()
    print(canonical_json(PreliminaryResearch(args.directory).run(json.loads(Path(args.request_json).read_text()))))
