"""One preregistered preliminary campaign. No optimizer, certificate or orders."""
from datetime import datetime
from pathlib import Path
import hashlib
import json

from .events import ContractError,canonical_json,digest
from .evidence_store import EvidenceStore
from .preliminary import PreliminaryResearch,STATE,WARNING,_request,engine_hash

CAMPAIGN='bounded-preliminary-strategy-v1'
BOUNDS=dict(hypotheses=4,strategy_variants_per_hypothesis=1,parameter_variants=1,
            research_jobs=1,reruns=0,live_model_calls=0,per_symbol_engine_evaluations=12)
DAYS=['2025-01-02','2025-01-03','2025-01-06','2025-01-07','2025-01-08',
      '2025-01-10','2025-01-13','2025-01-14','2025-01-15','2025-01-16']


def specification():
    base=dict(min_price=1,stop_loss_pct=1,reward_risk=2,max_hold_minutes=60,session_start='09:45',session_end='14:55')
    definitions=[('opening-range-15','Opening range breakout',dict(opening_range_minutes=15)),
                 ('volume-breakout-20','Abnormal-volume momentum breakout',dict(breakout_lookback_bars=20,min_relative_volume=1.5)),
                 ('ema-trend-9-20','Intraday trend continuation',dict(fast_ema_period=9,slow_ema_period=20,require_price_above_slow_ema=True,require_fast_ema_rising=True)),
                 ('ema-pullback-9-20','Trend pullback breakout',dict(fast_ema_period=9,slow_ema_period=20,require_price_above_slow_ema=True,require_fast_ema_rising=True,require_fast_ema_pullback=True,pullback_touch_tolerance_pct=0.25,max_pullback_number=2,require_pullback_breakout=True))]
    hypotheses=[dict(id=i,strategy=dict(id=i,name=n,direction='long',machine_rules={**base,**rules},unresolved_rules=[])) for i,n,rules in definitions]
    return dict(campaign_id=CAMPAIGN,bounds=BOUNDS,request=dict(dataset='owned-panel-v1',symbols=['AAPL','AMD','AMZN'],sessions=DAYS,hypotheses=hypotheses),
        selection='First three alphabetically sorted preexisting single-stock infrastructure controls; first ten January development sessions. No outcome selection.',
        lifecycle='DEVELOPMENT_ELIGIBLE_existing_research_split',state=STATE,
        disposition_policy='Data failure blocks; material review disagreement blocks; negative net rejects; no trades or <20 trades weak; positive with all three symbols positive and >=20 trades is only a provisional candidate pending independent review; all other outcomes need more research.',
        known_limitations=['Small retrospective panel, no OOS claim','No certified identity/admin/lot chain',
          'RVOL has no prior-session baseline on first day; warm-up is not filled',
          'Existing bar engine exits at session end; overlapping modeled position minutes are not portfolio exposure',
          'First-five bar checks here are research diagnostics, not V4 admission changes'],orders_enabled=False)


def campaign_hash():
    return digest(dict(engine=engine_hash(),campaign=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))


def review_packet(spec,hypothesis,when):
    from ai_review.contracts import digest as ai_digest
    content=canonical_json(dict(hypothesis=hypothesis,bounds=spec['bounds'],scope=spec['request']['sessions'],symbols=spec['request']['symbols']))
    return dict(artifact_id=spec['campaign_id']+':'+hypothesis['id'],checkpoint='new_hypothesis',
        evidence=[dict(id='registered-rules',content=content,sha256=hashlib.sha256(content.encode()).hexdigest(),
                       provenance=dict(uri='local-campaign:'+ai_digest(spec),retrieved_at=when,publisher='Lab bounded research protocol'))],
        assumptions=[WARNING,*spec.get('known_limitations',[])],specification=content,
        deterministic_context=dict(state=STATE,certified=False,runner_authorized=False),resolutions=[])


def disabled_review(directory, packets):
    # Reuse the actual review gate. Disabled routes cannot invoke the legacy paid APIs.
    from ai_review.providers import Route,OpenAIPrimaryAdapter,GeminiReviewerAdapter
    from ai_review.gate import ReviewGate
    from ai_review.storage import ReviewStorage
    from hybrid_runtime.storage import HybridStore
    store=HybridStore(Path(directory)/'review.sqlite3')
    gate=ReviewGate(ReviewStorage(store),OpenAIPrimaryAdapter(Route('openai','gpt-unconfigured','unconfigured')),
                    GeminiReviewerAdapter(Route('gemini','gemini-unconfigured','unconfigured')))
    results={}
    for packet in packets:
        gate.submit(packet)
        record=gate.run(packet['artifact_id'])
        results[packet['artifact_id']]=dict(state=record['snapshot']['state'],event_hash=record['event_hash'],
            primary=None,reviewer=None,mode='disabled_no_live_model_calls',errors=record['snapshot']['errors'])
    return results


def quality(data):
    problems=[]; counts={}
    for cell,rows in data['cells'].items():
        times=[r['t'] for r in rows]; day=cell.split('|')[1]
        required={day+'T14:'+str(m)+':00Z' for m in range(30,35)}
        counts[cell]=dict(observations=len(rows),missing_first_five=len(required-set(times)))
        if len(set(times))!=len(times) or times!=sorted(times):problems.append(cell+':duplicate_or_unsorted_bars')
        if not required<=set(times):problems.append(cell+':missing_native_opening_observations')
    return dict(cells=counts,problems=problems,certifies_nothing=True)


def classifications(result,data,review):
    findings=quality(data);outputs=result['performance']['outputs'];classified=[]
    for h in result['registration']['request']['hypotheses']:
        rows=[r['output'] for r in outputs if r['hypothesis_id']==h['id']]
        trades=[t for r in rows for t in r['trades']];net=round(sum(r['metrics']['net_pnl'] for r in rows),6)
        metrics=dict(trade_count=len(trades),winners=sum(t['pnl']>0 for t in trades),losers=sum(t['pnl']<0 for t in trades),
                     rough_sum_net_pnl=net,per_symbol={r['symbol']:r['metrics'] for r in rows},
                     maximum_individual_account_drawdown_pct=max((r['metrics']['max_drawdown_pct'] for r in rows),default=0),
                     summed_position_minutes=round(sum((datetime.fromisoformat(t['exit_time'].replace('Z','+00:00'))-datetime.fromisoformat(t['entry_time'].replace('Z','+00:00'))).total_seconds()/60 for t in trades),3),
                     skipped_or_rejected_trades=None,skipped_metric_reason='Existing bar engine does not expose rejection counts; not invented')
        reviews=[v for k,v in review.items() if k.endswith(':'+h['id'])]
        disagreement=any(v['state']=='AI_REVIEW_DISAGREEMENT' for v in reviews)
        pending=not reviews or any(v['state']!='CLEARED_FOR_NEXT_VALIDATION_STAGE' or v.get('mode')!='live_verified' for v in reviews)
        suggested=False
        if findings['problems']:decision='Blocked by data quality'
        elif disagreement:decision='Blocked by AI disagreement'
        elif net<0:decision='Reject'
        elif len(trades)<20:decision='Weak'
        elif all(r['metrics']['net_pnl']>0 for r in rows):
            decision='Needs more research' if pending else 'Promising preliminary candidate';suggested=True
        else:decision='Needs more research'
        classified.append(dict(hypothesis_id=h['id'],classification=decision,metrics=metrics,state=STATE,
            review_pending=pending,disagreement=disagreement,provisional_candidate_for_future_review=suggested,
            promoted=False,certified=False,runner_authorized=False,paper_eligible=False,live_eligible=False,
            warnings=[*result['warnings'],*[x for r in rows for x in r.get('limitations',[])]],
            position_minutes_meaning='Sum of overlapping modeled position durations, not portfolio exposure or risk-adjusted return'))
    return dict(state=STATE,quality=findings,candidates=classified,orders_enabled=False,certified=False)


class BoundedCampaign:
    def __init__(self,directory):
        self.root=Path(directory);self.audit=EvidenceStore(self.root/'campaign','bounded-campaign-v1')
        self.research=PreliminaryResearch(self.root/'preliminary')

    def register(self,spec=None):
        spec=specification() if spec is None else json.loads(canonical_json(spec))
        # Only the exact preregistered real campaign is launchable. Fixture scope
        # may exercise the same limits offline, never arbitrary real scope overrides.
        request=_request(spec['request'])
        if spec['bounds']!=BOUNDS or spec['campaign_id']!=CAMPAIGN:raise ContractError('campaign_bounds_frozen')
        if request['dataset']=='owned-panel-v1' and spec!=specification():raise ContractError('campaign_real_scope_frozen')
        if len(request['hypotheses'])*len(request['symbols'])>BOUNDS['per_symbol_engine_evaluations']:raise ContractError('campaign_engine_budget')
        with self.audit.locked() as store:
            if any(r['kind']=='campaign_registered' for r in self.audit.records(store)):raise ContractError('campaign_already_registered_no_expansion')
            self.audit.append(store,'campaign_registered','registration',dict(spec=spec,code_hash=campaign_hash(),at=datetime.now().astimezone().isoformat(),state=STATE))
        return digest(spec)

    def run(self):
        with self.audit.locked() as store:
            records=self.audit.records(store)
            if any(r['kind']=='campaign_claimed' for r in records):raise ContractError('campaign_run_budget_exhausted')
            registration=next((r['body'] for r in records if r['kind']=='campaign_registered'),None)
            if not registration:raise ContractError('campaign_registration_required')
            if registration['code_hash']!=campaign_hash():raise ContractError('campaign_code_changed')
            self.audit.append(store,'campaign_claimed','claim',dict(state=STATE,run_count=1,reruns_remaining=0))
        spec=registration['spec'];packets=[review_packet(spec,h,registration['at']) for h in spec['request']['hypotheses']]
        try:
            review=disabled_review(self.root,packets)
            with self.audit.locked() as store:self.audit.append(store,'hypothesis_review','review',dict(packets=packets,review=review,primary_origin='Codex-authored preregistered rules; no API-model receipt'))
            result=self.research.run(spec['request'])
            data=next(r['body']['data'] for r in self.research.audit.replay() if r['key']=='inputs:'+result['run_id'])
            report=classifications(result,data,review)
            interpretation_packet=self.research.review_packet(result['run_id'])
            interpretation_review=disabled_review(self.root,[interpretation_packet])
            with self.audit.locked() as store:
                self.audit.append(store,'interpretation_review','interpretation',dict(packet=interpretation_packet,review=interpretation_review))
            report.update(run_id=result['run_id'],result_hash=result['result_hash'],review=review,run_count=1,code_hash=registration['code_hash'])
            report['interpretation_review']=interpretation_review
            report['report_hash']=digest(report)
            with self.audit.locked() as store:self.audit.append(store,'campaign_result','result',report)
            return report
        except Exception as exc:
            reason=str(exc) if isinstance(exc,ContractError) else 'campaign_failed_no_automatic_retry'
            with self.audit.locked() as store:self.audit.append(store,'campaign_failed','failure',dict(state=STATE,reason=reason,run_count=1))
            raise ContractError(reason) from None

    def replay(self):
        rows=self.audit.replay();report=next(r['body'] for r in rows if r['kind']=='campaign_result')
        if report['code_hash']!=campaign_hash():raise ContractError('campaign_code_changed')
        replay=self.research.replay(report['run_id'])
        result=next(r['body'] for r in self.research.audit.replay() if r['key']=='result:'+report['run_id'])
        data=next(r['body']['data'] for r in self.research.audit.replay() if r['key']=='inputs:'+report['run_id'])
        computed=classifications(result,data,report['review'])
        if any(computed[k]!=report[k] for k in computed) or digest({k:v for k,v in report.items() if k!='report_hash'})!=report['report_hash']:
            raise ContractError('campaign_replay_mismatch')
        return dict(exact_match=True,report_hash=report['report_hash'],preliminary=replay,orders_enabled=False)


def summary(directory):
    from .evidence_store import read_records
    records=read_records(Path(directory)/'campaign','bounded-campaign-v1')
    results=[r['body'] for r in records if r['kind']=='campaign_result']
    return dict(state=STATE,completed_campaigns=len(results),
        candidates=[{'hypothesis_id':c['hypothesis_id'],'classification':c['classification']} for r in results for c in r['candidates']],
        certified=False,orders_enabled=False)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('directory');parser.add_argument('action',choices=['register','run','replay']);args=parser.parse_args()
    campaign=BoundedCampaign(args.directory)
    print(canonical_json(getattr(campaign,args.action)()))
