"""Versioned research evidence review. This never issues execution authority.

The old admission records and runner gates stay immutable. This policy separates
an explicitly conditional research question from production calibration, while
retaining identity, status, action and execution evidence requirements. Review
facts must be derived by an evidence reviewer; a policy decision is not a signed
dataset certificate and cannot be passed to the existing performance runner.
"""
from pathlib import Path
import json
from .events import ContractError, digest, timestamp_ns
from .research_split import ResearchSplit
from .research_check import save
from .security_master import read
from .sparse_benchmark import verified_json

POLICY=Path(__file__).with_name('protocols')/'research-tiers-v1.json'


def decide(request, evidence, *, tier):
    policy=json.loads(POLICY.read_text())
    if type(tier) is not int or tier not in range(4):raise ContractError('research_tier_unknown')
    if evidence['evidence_hash']!=digest({k:v for k,v in evidence.items() if k!='evidence_hash'}):raise ContractError('tier_evidence_integrity')
    if evidence['request_hash']!=digest(request):raise ContractError('tier_request_scope_changed')
    ResearchSplit().authorize(*request['requested_window'],purpose='strategy_development')
    required=['source_integrity','development_isolation'] if tier==0 else policy['requirements_all_performance_tiers']+policy['requirements_tier'+str(tier)]
    rejected=[]
    for key in required:
        fact=evidence['facts'].get(key,{})
        # A timing condition is allowed only at Tier 1, by the named policy.
        conditional=(tier==1 and key=='explicit_final_export_conditioning' and fact.get('state')=='conditional')
        if (fact.get('state')!='verified' and not conditional) or not fact.get('source_hashes'):
            rejected.append(key)
    body=dict(version='research-tier-decision-v1',policy_hash=digest(policy),request=request,
        request_hash=digest(request),evidence_hash=evidence['evidence_hash'],tier=tier,
        admitted=not rejected,admission_level=('DIAGNOSTIC_ONLY' if tier==0 else 'CONDITIONAL_RESEARCH' if tier==1 else 'HIGH_FIDELITY_REVIEW' if tier==2 else 'PRODUCTION_EVIDENCE_REVIEW') if not rejected else 'REJECTED',
        missing_requirements=rejected,requirements={k:evidence['facts'].get(k,dict(state='unknown')) for k in required},
        actual_arrival_required=tier>=2,runner_certificate_issued=False,production_qualified=False,execution_authority='none')
    return {**body,'decision_hash':digest(body)}


def build_reviews(history_root, directory):
    """Assess only acquired evidence; no arbitrary caller-provided eligibility flags."""
    h=Path(history_root);a=h/'admission-v3';out=Path(directory);out.mkdir(parents=True,exist_ok=False,mode=0o700)
    protocol=verified_json(Path(__file__).with_name('protocols')/'historical-admission-v3.json','protocol_hash')
    execution=verified_json(a/'execution-audit/execution-evidence.json','result_hash')
    status=verified_json(a/'status-review.json','result_hash')
    reference=verified_json(a/'reference-review.json','result_hash')
    panel=verified_json(h/'certification-v2/panel/manifest.json','manifest_hash')
    old=verified_json(h/'sparse-v1/calendar-strategy-admissions.json','result_hash')
    if execution['protocol_hash']!=protocol['protocol_hash']:raise ContractError('tier_execution_protocol_mismatch')
    for symbol,bundle in reference['bundles'].items():
        if read(a/'security-claims'/symbol)!=bundle:raise ContractError('tier_security_claims_changed')
    # This reviewer implements the evidence types actually obtained. Future
    # continuous identity/status sources need a validating reviewer, never a flag.
    if any(c['identity_scope']!='issuer_claim' for b in reference['bundles'].values() for c in b['claims']):
        raise ContractError('tier_new_security_evidence_requires_scope_validation')
    if status['continuous_tradability']:raise ContractError('tier_new_status_evidence_requires_interval_validation')
    policy=json.loads(POLICY.read_text());policy_hash=digest(policy)
    sources=dict(execution=execution['result_hash'],status=status['result_hash'],reference=reference['result_hash'],panel=panel['manifest_hash'],protocol=protocol['protocol_hash'],policy=policy_hash)
    def assess(request, bounded):
        facts={}
        def fact(name,state,reason,*refs):facts[name]=dict(state=state,reason=reason,source_hashes=[sources[r] for r in refs])
        fact('source_integrity','verified','Raw page chains and normalized evidence hashes verified; source hashes bind this review','execution','reference','status')
        fact('development_isolation','verified','Request authorization before any price reads; development only','protocol')
        fact('non_outcome_scope','verified' if bounded else 'unknown','Preregistered original pilot symbols and first five sessions' if bounded else 'Original fixed panel is conditional; no historical universe certification','protocol','panel')
        fact('security_identity','unknown','Two dated issuer claims; no complete security/listing IDs, symbol history or certified price-series join','reference')
        fact('action_basis','unknown','Saved action types and dated claims do not cover every action, security conversion and strategy lookback','reference')
        fact('positive_status','unknown','Start-date and resumption-date positives only; archive silence is UNKNOWN; no complete LULD/state sequence','status')
        complete=bounded and execution['symbols']==protocol['symbols'] and execution['days']==protocol['development_days'] and all(x['query_exhausted'] for d in execution['sessions'].values() for x in d.values())
        fact('complete_quote_trade_scope','verified' if complete else 'unknown','All 15 preregistered symbol-days have exhausted quote/trade queries' if complete else 'Five sessions/three symbols do not cover the original 19-symbol development window','execution')
        fact('native_quote_units','unknown','Native round lots retained; historical security-specific share conversion not established','execution')
        fact('quote_condition_rules','unknown','Native quote conditions retained; no validated execution eligibility mapping','execution')
        fact('strategy_history','unknown','No family-specific, identity/action-qualified warmup and feature certificate issued','panel','reference')
        fact('frozen_execution_assumptions','unknown','Clock diagnostics frozen; executable fees/quantity/slippage/partial-fill experiment not frozen or admitted','policy')
        fact('explicit_final_export_conditioning','conditional','Tier 1 permits the named final-export estimand and fixed delay/revision cases; neither bounds actual historical revisions','policy')
        fact('source_availability_and_revision_chain','unknown','Native event time and current retrieval known; original arrivals/correction/cancel chain absent','execution')
        fact('execution_calibration','unknown','No observed fill/queue or actual latency calibration','execution')
        fact('live_data_certification','unknown','Alpaca live SIP BLOCKED; Tradier UNVERIFIED','protocol')
        fact('operational_qualification','unknown','No production qualification or brokerage authority granted','protocol')
        e=dict(version='research-evidence-review-v1',request_hash=digest(request),facts=facts)
        return {**e,'evidence_hash':digest(e)}
    original=[];bounded=[]
    for prior in old['admissions']:
        request={k:prior[k] for k in ('experiment','requested_window','requested_symbols','conservative_tier1_enabled')}
        e=assess(request,False);decision=decide(request,e,tier=prior['tier'])
        original.append(dict(prior_admission_hash=prior['admission_hash'],evidence=e,decision=decision))
        request={**request,'requested_symbols':protocol['symbols'],'requested_window':[
            timestamp_ns(protocol['development_days'][0]+'T09:30:00-05:00'),timestamp_ns(protocol['development_days'][-1]+'T16:00:00-05:00')]}
        e=assess(request,True);decision=decide(request,e,tier=prior['tier']);bounded.append(dict(evidence=e,decision=decision))
    diagnostic=decide(bounded[0]['decision']['request'],bounded[0]['evidence'],tier=0)
    body=dict(version='historical-admission-review-v3',sources=sources,original_requests=original,bounded_subset_requests=bounded,
        diagnostic=diagnostic,requirements_changed=dict(tier1_original_application_arrival='replaced with explicit conditional estimand, not an observed arrival claim',
            tier1_original_revision_time='unknown; final-export conditioning and revision exclusion sensitivity required',
            scope='bounded quote/trade coverage improved to 15 symbol-days; original request scope unchanged',
            status='additional resumption-date evidence; no continuous-tradability inference',identity='two raw-linked issuer claims; security identity gate unchanged'),
        production_qualified=False,execution_authority='none',performance_experiments=0)
    result={**body,'result_hash':digest(body)};save(out/'tier-reviews.json',result);return result
