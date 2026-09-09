"""Evidence follow-up; does not edit prices, labels, prefixes, or strategy gates."""
from collections import Counter
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from .events import ContractError, digest, timestamp_ns
from .features import MINUTE
from .halt_history import NS, parse_halts
from .research_panel import ResearchPanel, admission, file_hash
from .research_check import save
from .sparse_observations import SparsePanel, read_trade_day, trade_rule
from .sparse_benchmark import verified_json
from .validation import ExperimentStore


def explain_discrepancy(state, trades, actions):
    bar=state['bar'];p=bar['payload']
    eligible=[r for r in trades if trade_rule(r)[0] is True]
    volume=sum(r['s'] for r in trades if trade_rule(r)[1] is True)
    excess=volume-p['volume_shares']
    candidates=[]
    # Diagnose whether a single extra record explains the arithmetic. Never
    # delete that record or infer that it was canceled/late without evidence.
    for i,row in enumerate(trades):
        if trade_rule(row)[1] is not True or row['s']!=excess:continue
        remaining=[r for j,r in enumerate(trades) if j!=i and trade_rule(r)[0] is True]
        if remaining and (min(Decimal(str(r['p'])) for r in remaining)==Decimal(p['low']) and
                          max(Decimal(str(r['p'])) for r in remaining)==Decimal(p['high'])):
            candidates.append(dict(native_record=row,native_hash=digest(row),
                inference='arithmetic_omission_candidate_only_not_cancellation_or_late_arrival_proof'))
    identities=Counter((r.get('x'),r.get('i')) for r in trades if r.get('i') is not None)
    body=dict(symbol=state['symbol'],minute_ns=state['minute_start_ns'],issues=state['issues'],
        classification='provider_export_disagreement_cause_unresolved',
        condition_filter_resolves=False,corporate_action_on_session=actions,
        raw_bar_lineage=bar['lineage'],native_bar=p,trade_rows_hash=digest(sorted(trades,key=lambda r:(timestamp_ns(r['t']),digest(r)))),
        tape_source_hash=state['source_hash'],native_print_count=len(trades),native_bar_count=p['trade_count'],
        raw_print_volume=sum(r['s'] for r in trades),documented_eligible_volume=volume,native_bar_volume=p['volume_shares'],
        excess_volume=excess,eligible_range=[str(min(Decimal(str(r['p'])) for r in eligible)),str(max(Decimal(str(r['p'])) for r in eligible))] if eligible else None,
        exact_duplicate_count=len(trades)-len({digest(r) for r in trades}),
        repeated_exchange_trade_ids=[dict(exchange=x,trade_id=i,count=n) for (x,i),n in identities.items() if n>1],
        single_record_omission_candidates=candidates,
        conclusion='Current documented conditions do not reconcile both exports. Arrival/revision/cancel history is unavailable; no authority is selected.',
        original_issues_preserved=True,prices_changed=False,execution_authority='none')
    return {**body,'diagnostic_hash':digest(body)}


def discrepancy_review(history_root):
    root=Path(history_root);panel=ResearchPanel(root/'certification-v2/panel')
    sparse=SparsePanel(root/'sparse-v1/observations-with-state');cases=[]
    try:
        # All six previously documented native conflicts, including warmup.
        for day in ('2024-12-19','2025-01-21','2025-01-22','2025-02-06','2025-02-10'):
            states=sparse.session(day)
            tape,coverage,manifest=read_trade_day(root/'sparse-v1/tape'/day/'trades',day,panel.manifest['calendar'])
            if manifest!=sparse.manifest['tape_sources'][day]:raise ContractError('diagnostic_tape_source_changed')
            for symbol,minutes in states.items():
                for state in minutes:
                    if state['bar'] and state['issues']:
                        cases.append(dict(day=day,**explain_discrepancy(state,tape[(symbol,state['minute_start_ns'])],state['action_sources'])))
        if len(cases)!=6:raise ContractError('known_discrepancy_inventory_changed')
        body=dict(version='native-discrepancy-followup-v1',cases=cases,
            hypothesis='extra historical prints or bar revisions may differ; no original correction/arrival lineage to select a cause',
            resolved=0,unresolved=len(cases),frozen_discovery_unchanged=True,execution_authority='none')
        return {**body,'result_hash':digest(body)}
    finally:panel.close()


def parse_resumptions(raw, *, requested_date):
    """A resumption-date query can reveal halts that began before the query day."""
    root=ET.fromstring(raw)
    if root.tag!='rss' or root.find('channel') is None:raise ContractError('resumption_response_not_rss')
    output=[]
    for item in root.findall('.//item'):
        value=lambda key:(item.findtext(NS+key) or '').strip()
        actual=datetime.strptime(value('ResumptionDate'),'%m/%d/%Y').date().isoformat()
        if actual!=requested_date:raise ContractError('resumption_wrong_requested_date')
        haltday=datetime.strptime(value('HaltDate'),'%m/%d/%Y').date().isoformat()
        singleton=ET.Element('rss');channel=ET.SubElement(singleton,'channel');channel.append(deepcopy(item))
        row=parse_halts(ET.tostring(singleton),requested_date=haltday)[0]
        row.pop('record_hash');row['raw_sha256']=hashlib.sha256(raw).hexdigest()
        row['record_hash']=digest(row);output.append(row)
    if len({r['record_hash'] for r in output})!=len(output):raise ContractError('duplicate_resumption_record')
    return output


def refresh_admissions(history_root, reference_review, status_review, directory):
    root=Path(history_root);target=Path(directory);target.mkdir(parents=True,exist_ok=False,mode=0o700)
    old=json.loads((root/'sparse-v1/calendar-strategy-admissions.json').read_text())
    manifest=verified_json(root/'certification-v2/panel/manifest.json','manifest_hash')
    outcome=verified_json(root/'outcome-v1/certification/selectivity.json','result_hash')
    store=ExperimentStore(target/'admissions.sqlite3');rows=[]
    try:
        store.append('evidence_review','outcome-certification',dict(outcome_hash=outcome['result_hash'],
            reference_review_hash=digest(reference_review),status_review_hash=digest(status_review),performance_experiments=0))
        for prior in old['admissions']:
            result=admission(manifest,experiment=prior['experiment'],purpose='strategy',tier=prior['tier'],
                conservative_tier1=prior['conservative_tier1_enabled'],window=prior['requested_window'],symbols=prior['requested_symbols'])
            if result['admitted']:raise ContractError('unexpected_strategy_admission_requires_evidence_review')
            store.append('strategy_admission',prior['experiment']+':'+str(prior['tier']),result);rows.append(result)
        screening=admission(manifest,experiment='outcome_certified_fixed_panel_screening',purpose='conditional_discovery',explicit_export_assumptions=True)
        body=dict(version='outcome-certification-strategy-admissions-v1',admissions=rows,screening_only=screening,
            requirements=dict(historical_security_identity=False,historical_universe_integrity=False,
                original_revision_publication_timing=False,complete_quote_execution_evidence=False,
                complete_trading_status=False,continuous_tradability=False,corporate_action_original_known_time=False,
                final_holdout_isolation=True),audit=store.verify(),performance_experiments=0,parameter_searches=0,
            ml=False,holdout='LOCKED_UNREQUESTED',execution_authority='none')
        result={**body,'result_hash':digest(body)};save(target/'admissions.json',result);return result
    finally:store.close()


def reference_status_review(history_root):
    """Uniform fixed-panel inventory; archived identifiers remain evidence claims."""
    from .data_acquisition import verified_pages
    from .sparse_benchmark import load_halts
    root=Path(history_root);old=root/'certification-v2';new=root/'outcome-v1/public-reference'
    manifest=verified_json(old/'panel/manifest.json','manifest_hash')
    acquired=verified_json(new/'complete.json','manifest_hash')
    sources={r['name']:r for r in acquired['records']}
    for r in sources.values():
        if r.get('sha256'):
            path=new/(r['name']+('.raw.xml' if r['name'].startswith('resumed-') else '.raw'))
            if file_hash(path)!=r['sha256']:raise ContractError('reference_raw_hash_mismatch')
    prior=json.loads((old/'public-reference/membership-evidence.json').read_text())
    matrix={s:dict(symbol=s,listing_delisting_evidence=[],security_identifier_claims=[],
        historical_identity_complete=False,historical_membership_complete=False,
        current_survivor_filter=False,tradability='UNKNOWN') for s in manifest['symbols']}
    for r in prior['records']:
        if r['symbol'] not in matrix:continue
        item=deepcopy(r)
        if r.get('archive_status')=='obtained':
            if file_hash(old/'public-reference'/(r['symbol']+'-membership.raw.html'))!=r['raw_sha256']:
                raise ContractError('previous_membership_raw_hash_mismatch')
            item['raw_hash_reverified']=True
        matrix[r['symbol']]['listing_delisting_evidence'].append(item)
    claims=[('ASPI','listed_from','2022-11-10','ASPI',dict(security_class='common_stock')),
            ('MDXH','ADS_listed_from','2021-11-04','MDXH',dict(ADS_ratio='10 ordinary per ADS before 2023 consolidation')),
            ('MDXH','ordinary_share_consolidation','2023-11-13','MDXH',dict(ratio='1-for-10')),
            ('MDXH','mandatory_ADS_exchange','2023-11-27','MDXH',dict(ratio='one ordinary per then-existing ADS')),
            ('MDXH','European_delisting_sole_Nasdaq_listing','2023-12-18','MDXH',dict(security_class='ordinary_shares'))]
    for symbol,kind,effective,source,extra in claims:
        if sources[source]['status']=='obtained':
            matrix[symbol]['listing_delisting_evidence'].append(dict(evidence_kind=kind,effective_date=effective,
                url=sources[source]['url'],raw_sha256=sources[source]['sha256'],observed_ns=sources[source]['observed_ns'],
                original_version_publication_ns=None,scope='archived_issuer_claim_not_continuous_membership',**extra))
    if sources['MDXH-identity']['status']=='obtained':
        matrix['MDXH']['security_identifier_claims'].append(dict(identifier='BE0974461940',kind='ISIN',
            document_date='2023-11-21',operation_from='2023-11-27',source=sources['MDXH-identity'],
            native_price_series_join_verified=False,scope='Euroclear_security_identifier_at_document_date'))
    # Recheck all saved wider actions, not a return-selected subset. Retain native
    # security identifiers only as dated action claims; no automatic symbol mapping.
    action_claims=[]
    for page,meta in verified_pages(old/'coverage-probes/actions-pilot-wide'):
        for kind,rows in page.get('corporate_actions',{}).items():
            for r in rows:
                effective=r.get('ex_date') or r.get('effective_date') or r.get('process_date')
                if not effective or effective>'2025-02-28':continue
                matches=sorted(s for s in matrix if s in [v for k,v in r.items() if 'symbol' in k])
                if not matches:continue
                claim=dict(kind=kind,record=r,symbols=matches,source_sha256=meta['sha256'],
                    original_available_ns=None,auto_join_allowed=False)
                action_claims.append(claim)
                identifiers={k:v for k,v in r.items() if 'cusip' in k}
                for s in matches:
                    if identifiers:matrix[s]['security_identifier_claims'].append(dict(identifiers=identifiers,
                        effective_date=effective,source_sha256=meta['sha256'],claim_hash=digest(claim),native_price_series_join_verified=False))
    references=dict(version='fixed-panel-reference-review-v1',symbols=matrix,action_claims=action_claims,
        source_manifest_hash=acquired['manifest_hash'],scope='CONDITIONAL_ON_FIXED_PANEL',complete_exchange_universe=False,
        auto_symbol_mapping=False,outcomes_used_for_cohort_selection=False,raw_tonx_source_unavailable=sources['TONX']['status']!='obtained',
        tonx_additional_web_reference=dict(url=sources['TONX']['url'],note='OCC memo 57177 corroborates VERB to TONX effective 2025-09-02; raw HTTP retrieval returned 403. Not used to revise labels or join prices.'),
        execution_authority='none')
    old_halts=load_halts(old/'halts-development');new_halts=[]
    for day in ('2025-01-02','2025-02-28'):
        if sources['resumed-'+day]['status']=='obtained':
            new_halts.extend(parse_resumptions((new/('resumed-'+day+'.raw.xml')).read_bytes(),requested_date=day))
    identity=lambda r:(r['symbol'],r['halt_ns'],r['trade_resumption_ns'],r['quote_resumption_ns'])
    known={identity(r) for r in old_halts};added=[r for r in new_halts if identity(r) not in known]
    start=timestamp_ns('2025-01-02T00:00:00-05:00')
    status=dict(version='resumption-boundary-status-review-v1',prior_start_date_records=len(old_halts),
        boundary_resumption_records=len(new_halts),additional_effective_intervals=len(added),additional_records=added,
        additional_halts_begun_before_development=sum(r['halt_ns']<start for r in added),
        fixed_panel_matches=[r for r in new_halts if r['symbol'] in matrix],
        covered_resumption_dates=['2025-01-02','2025-02-28'],
        full_resumption_date_coverage=False,unresumed_prior_halt_coverage=False,continuous_tradability=False,
        absence_means='UNKNOWN',source_manifest_hash=acquired['manifest_hash'],execution_authority='none')
    return {**references,'result_hash':digest(references)},{**status,'result_hash':digest(status)}
