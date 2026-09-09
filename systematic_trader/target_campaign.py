"""One frozen source-derived, archive-conditional momentum screen. No promotion."""
from datetime import datetime
from pathlib import Path
import hashlib
import json
from .events import ContractError,digest,canonical_json
from .evidence_store import EvidenceStore
from .preliminary import PreliminaryResearch,STATE,engine_hash,_request
from .bounded_campaign import disabled_review,review_packet,DAYS
from .campaign_interpretation import equivalent_timestamps
from .bounded_campaign import quality

CAMPAIGN='target-domain-preliminary-v1'
SYMBOLS=['ASPI','AVR','DGICB','FMBH','MDXH']
SOURCE_IDS=['webresearch-65bab2a6ff63b0f4f4','8b2b56cf85199a229734','webresearch-4e8b594de19dfcaf2f',
            'webresearch-eda5e0922d36687c8e','webresearch-543efa1d89a487d43f','webresearch-722c519e1e59bcc7c6']
BOUNDS=dict(hypotheses=6,executable_projections=3,variants_each=1,research_jobs=1,reruns=0,
            symbols=5,sessions=10,engine_evaluations=15,live_model_calls=0)

def specification():
    # Source-literal entry rules. Uniform execution assumptions are experimental,
    # not claimed to be verbatim source strategies or historical fill evidence.
    base=dict(min_price=1,max_price=20,stop_loss_pct=1,reward_risk=2,max_hold_minutes=60,
              session_start='10:00',session_end='14:30')
    definitions=[('morning-volume-consolidation',SOURCE_IDS[0],dict(min_price=10,min_relative_volume=1.5,min_volume_acceleration_ratio=1.2,minimum_breakout_hold_bars=2,opening_range_minutes=30,session_end='10:30')),
      ('high-rvol-vwap-retest',SOURCE_IDS[2],dict(min_price=5,min_relative_volume=2,min_volume_acceleration_ratio=1.5,minimum_vwap_hold_bars=2,require_volume_accelerating=True,require_vwap_retest_held=True,vwap_reclaim=False)),
      ('volume-acceleration-reclaim',SOURCE_IDS[4],dict(above_vwap=True,min_volume_acceleration_ratio=2,volume_surge_ratio=2,vwap_reclaim=True))]
    hypotheses=[dict(id=i,strategy=dict(id=i,name=i,direction='long',machine_rules={**base,**r},unresolved_rules=[])) for i,source,r in definitions]
    return dict(campaign_id=CAMPAIGN,bounds=dict(BOUNDS),state=STATE,
      request=dict(dataset='owned-panel-v1',symbols=list(SYMBOLS),sessions=list(DAYS),hypotheses=hypotheses),
      candidates=[dict(id=i,source_id=source,mode='bar_mechanism_projection_not_full_source_strategy') for i,source,r in definitions]+[
        dict(id='first-pullback-bull-flag',source_id=SOURCE_IDS[1],mode='Blocked by data',reason='Historical float, news catalyst, top-gainer rank and Level 2 absent; discretionary tape and subminute rules unresolved'),
        dict(id='reclaim-two-bar-hold',source_id=SOURCE_IDS[3],mode='Blocked by data',reason='Saved current-crossover rule conflicts with text requiring two-bar hold; sequential interpretation needs independent review'),
        dict(id='spread-distance-catalyst',source_id=SOURCE_IDS[5],mode='Blocked by data',reason='Missing historical spreads/catalyst; saved rule omits described caps and percentage units ambiguous')],
      universe='First five alphabetically sorted non-control names in already-owned archive, frozen before results. Price 1-20 (source-specific minimum) enforced causally at entry; no cap/float/current membership substitution. Archive-conditional approximation, NOT unbiased historical small-cap membership.',
      assumptions=['RTH native one-minute bars only; no filled sparse minutes; existing engine causal bar features and modeled costs',
        'Source entry-rule projections only: omitted AVWAP, catalyst, CVD/Hurst, index alignment, spread, and discretionary components remain unresolved; no full source-strategy test claim',
        '10:00-14:30 common experimental window (morning source 10:00-10:30); 1% stop, 2R target, 60-minute hold are frozen experiment assumptions',
        'No true premarket-high/gap, historical float/cap or complete discovery claim; RVOL warm-up absent on first session',
        'Volume-reclaim citations include two unrelated papers; no established scientific support claimed'],
      known_limitations=['All dates January development only; no OOS or regime diversity','Missing opening observations prevent strategy qualification','Independent review pending; no paid models'],
      selection_policy='No outcome-driven expansion or rerun. No prospective input. Uniform bar-price eligibility only; source RVOL/volume filters apply only where present in literal rules.',
      classification_policy='Missing native openings blocks data qualification even when mechanics are calculated. Negative diagnostics reject only if quality permits; <20 trades weak. Any net-positive outcome triggers review and never auto-promotes.',
      orders_enabled=False,certified=False)


def audit_library(library):
    value=json.loads(Path(library).read_text())
    strategies={s['id']:s for s in value['strategies']}
    projection=[]
    for id in SOURCE_IDS:
        if id not in strategies:raise ContractError('source_strategy_missing')
        s=strategies[id]
        h=next((x for x in value['research_hypotheses'] if x['id']==s.get('research_hypothesis_id')),None)
        entry=dict(strategy={k:s.get(k) for k in ('id','name','source_id','machine_rules','unresolved_rules')})
        if h:
            entry['hypothesis']={k:h.get(k) for k in ('id','statement','why_it_might_work','machine_rules','supporting_source_ids','research_run_id','unresolved_rules')}
            ids=set(h.get('supporting_source_ids') or [])
        else:ids={s.get('source_id')}
        entry['sources']=[{k:source.get(k) for k in ('id','title','source_url','author','excerpt')} for source in value['knowledge_sources'] if source.get('id') in ids]
        projection.append(entry)
    return dict(strategies=projection,source_count=len(projection),library_sha256=hashlib.sha256(Path(library).read_bytes()).hexdigest(),
        holdout_ledger_hash=digest(value.get('holdout_exposure_ledger')),outcome_fields_used=False,
        caveat='Stored source assertions are unverified hypotheses. Source-quality flags do not use outcomes.')


def code_hash():
    return digest(dict(engine=engine_hash(),target=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))


def concentrations(trades):
    def grouped(key):
        out={}
        for t in trades:
            k=key(t);out[k]=round(out.get(k,0)+t['pnl'],6)
        return out
    total_abs=sum(abs(t['pnl']) for t in trades)
    def shares(key):
        out={}
        for t in trades:
            k=key(t);out[k]=out.get(k,0)+abs(t['pnl'])
        return max(out.values(),default=0)/total_abs if total_abs else None
    return dict(by_ticker=grouped(lambda t:t['symbol']),by_date=grouped(lambda t:t['entry_time'][:10]),
        largest_symbol_share_absolute_pnl=shares(lambda t:t['symbol']),largest_date_share_absolute_pnl=shares(lambda t:t['entry_time'][:10]),
        largest_trade_share_absolute_pnl=max((abs(t['pnl']) for t in trades),default=0)/total_abs if total_abs else None,
        market_regime='Unclassified single short January development window; regime concentration unresolved')


def evaluate_report(result,data,spec):
    q=quality(equivalent_timestamps(data));candidates=[]
    for h in spec['request']['hypotheses']:
        outputs=[r['output'] for r in result['performance']['outputs'] if r['hypothesis_id']==h['id']]
        trades=[dict(t,symbol=r['symbol']) for r in outputs for t in r['trades']]
        net=round(sum(r['metrics']['net_pnl'] for r in outputs),6)
        decision='Blocked by data' if q['problems'] else 'Reject' if net<0 else 'Weak' if len(trades)<20 else 'Needs more research'
        candidates.append(dict(id=h['id'],classification=decision,state=STATE,
          metrics=dict(trades=len(trades),rough_modeled_pnl=net,wins=sum(t['pnl']>0 for t in trades),losses=sum(t['pnl']<0 for t in trades),
            average_trade=net/len(trades) if trades else None,per_symbol={o['symbol']:o['metrics'] for o in outputs},
            max_individual_account_drawdown_pct=max((o['metrics']['max_drawdown_pct'] for o in outputs),default=0),
            summed_position_minutes=sum((datetime.fromisoformat(t['exit_time'].replace('Z','+00:00'))-datetime.fromisoformat(t['entry_time'].replace('Z','+00:00'))).total_seconds()/60 for t in trades),
            skipped_rejected_trades=None,skipped_reason='Existing authoritative engine does not expose rejection counts; not invented'),
          concentration=concentrations(trades),review_flags=review_flags(net),
          certified=False,promoted=False,runner_authorized=False,paper_eligible=False,live_eligible=False,orders_enabled=False))
    candidates += [{**c,'classification':c['mode'],'state':STATE,'metrics':None,'certified':False,'orders_enabled':False} for c in spec['candidates'] if c['mode']=='Blocked by data']
    return dict(state=STATE,candidates=candidates,quality=q,warnings=data['warnings']+spec['assumptions'],
        review_mode='Offline independent review pending; no paid adapters invoked',orders_enabled=False,certified=False,
        pnl_meaning='Rough sum of separately funded modeled accounts; position minutes overlap, not portfolio exposure')


def review_flags(net):
    return ['independent_AI_critique','leakage_review','parameter_neighborhood_review_no_automatic_runs','concentration_analysis','execution_realism_review'] if net>0 else ['independent_review_pending']


class TargetCampaign:
    def __init__(self,directory):
        self.root=Path(directory);self.audit=EvidenceStore(self.root/'target','bounded-campaign-v1')
        self.research=PreliminaryResearch(self.root/'preliminary')

    def register(self,source_audit,spec=None):
        spec=specification() if spec is None else spec
        if spec!=specification():raise ContractError('target_scope_frozen_no_expansion')
        _request(spec['request'])
        if [s['strategy']['id'] for s in source_audit.get('strategies',[])]!=SOURCE_IDS:raise ContractError('source_audit_required')
        with self.audit.locked() as store:
            if self.audit.records(store)[1:]:raise ContractError('target_already_registered')
            self.audit.append(store,'target_registered','registration',dict(spec=spec,source_audit=source_audit,source_hash=digest(source_audit),code_hash=code_hash(),at=datetime.now().astimezone().isoformat()))
        return digest(spec)

    def run(self):
        with self.audit.locked() as store:
            rows=self.audit.records(store)
            reg=next((r['body'] for r in rows if r['kind']=='target_registered'),None)
            if not reg or reg['code_hash']!=code_hash():raise ContractError('target_registration_or_code_mismatch')
            if any(r['kind']=='target_claimed' for r in rows):raise ContractError('target_run_budget_exhausted')
            self.audit.append(store,'target_claimed','claim',dict(runs=1,reruns=0,state=STATE))
        spec=reg['spec']
        try:
            review=disabled_review(self.root,[review_packet(spec,h,reg['at']) for h in spec['request']['hypotheses']])
            with self.audit.locked() as store:self.audit.append(store,'target_review','review',review)
            result=self.research.run(spec['request'])
            data=next(r['body']['data'] for r in self.research.audit.replay() if r['key']=='inputs:'+result['run_id'])
            report=evaluate_report(result,data,spec)
            report.update(run_id=result['run_id'],result_hash=result['result_hash'],code_hash=code_hash(),spec_hash=digest(spec),review=review)
            report['interpretation_review']=disabled_review(self.root,[self.research.review_packet(result['run_id'])])
            report['report_hash']=digest(report)
            with self.audit.locked() as store:self.audit.append(store,'target_result','result',report)
            return report
        except Exception as exc:
            with self.audit.locked() as store:self.audit.append(store,'target_failed','failure',dict(reason=str(exc) if isinstance(exc,ContractError) else 'target_failed_no_retry'))
            raise

    def replay(self):
        rows=self.audit.replay();reg=next(r['body'] for r in rows if r['kind']=='target_registered');report=next(r['body'] for r in rows if r['kind']=='target_result')
        if reg['code_hash']!=code_hash() or report['report_hash']!=digest({k:v for k,v in report.items() if k!='report_hash'}):raise ContractError('target_replay_changed')
        check=self.research.replay(report['run_id'])
        data=next(r['body']['data'] for r in self.research.audit.replay() if r['key']=='inputs:'+report['run_id'])
        result=next(r['body'] for r in self.research.audit.replay() if r['key']=='result:'+report['run_id'])
        computed=evaluate_report(result,data,reg['spec'])
        if any(report[k]!=v for k,v in computed.items()):raise ContractError('target_report_replay_mismatch')
        return dict(exact_match=True,report_hash=report['report_hash'],preliminary=check,orders_enabled=False)
