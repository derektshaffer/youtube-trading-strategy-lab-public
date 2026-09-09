"""Bounded source/status follow-up. Documentation cannot certify market evidence."""
from pathlib import Path
from .events import ContractError,digest,timestamp_ns
from .research_check import save
from .research_panel import file_hash
from .sparse_benchmark import verified_json,load_halts
from .outcome_diagnostics import parse_resumptions,refresh_admissions


def review(history_root, directory):
    h=Path(history_root);root=Path(directory);root.mkdir(parents=True,exist_ok=False,mode=0o700)
    source=h/'selectivity-v1/evidence';acquired=verified_json(source/'complete.json','manifest_hash')
    plan=verified_json(source/'plan.json','plan_hash')
    if acquired['plan_hash']!=plan['plan_hash']:raise ContractError('admission_source_plan_changed')
    sources={}
    for record in acquired['records']:
        name=record['name']
        if record['url']!=plan['urls'].get(name):raise ContractError('admission_unplanned_source')
        if record.get('sha256') and file_hash(source/(name+'.raw'))!=record['sha256']:
            raise ContractError('admission_reference_hash_mismatch')
        body=(source/(name+'.raw')).read_bytes() if record.get('sha256') else b''
        shell=name.startswith('databento-') and b'ts_recv' not in body and b'listing_date' not in body
        sources[name]=dict(**record,semantic_archive='javascript_shell_only' if shell else 'raw_obtained_unparsed' if record['status']=='obtained' else 'unavailable',
            actual_historical_security_records_acquired=False)
    oldrefs=verified_json(h/'outcome-v1/reference-review.json','result_hash')
    references=dict(version='historical-evidence-source-review-v2',previous_reference_hash=oldrefs['result_hash'],
        fixed_panel=oldrefs['symbols'],source_manifest_hash=acquired['manifest_hash'],sources=sources,
        candidate_sources={
            'Nasdaq Daily List':dict(capability='archived additions/deletions/symbol changes/first traded date and optional CUSIP; documented periodically updated files',
                blocker='No configured or acquired authorized historical Daily List files; a specification is not a security master; initial universe plus complete event chain and publication versions required',historical_dataset_obtained=False),
            'Databento':dict(capability='public web documentation describes PIT security master, timestamped instrument definitions and capture receipt clocks',
                blocker='No historical export or verified dataset access acquired. Direct raw documentation requests yielded JavaScript shells; web-readable documentation was reviewed separately. Coverage, licensing, per-dataset status and correction semantics still need verification.',historical_dataset_obtained=False),
            'Alpaca saved exports':dict(capability='trade/quote event timestamps and fields; live corrections/cancels/updated bars documented separately',
                blocker='No original delivery or revision chain in saved exports; 0/2/5 minute delay assumptions do not bound retrospective revisions',historical_dataset_obtained=True)},
        full_identity=False,full_universe=False,original_revision_history=False,orders_enabled=False)
    references={**references,'result_hash':digest(references)};save(root/'reference-review.json',references)
    prior_status=verified_json(h/'outcome-v1/status-review.json','result_hash')
    all_previous=load_halts(h/'certification-v2/halts-development')+prior_status['additional_records']
    identity=lambda r:(r['symbol'],r['halt_ns'],r['trade_resumption_ns'],r['quote_resumption_ns'])
    previous={identity(r) for r in all_previous};new=[];coverage=[]
    for day in ('2025-01-03','2025-01-06','2025-01-07'):
        name='resumed-'+day;record=sources[name]
        if record['status']!='obtained':continue
        rows=parse_resumptions((source/(name+'.raw')).read_bytes(),requested_date=day);new.extend(rows);coverage.append(day)
    added={identity(r):r for r in new if identity(r) not in previous}
    body=dict(version='development-resumption-evidence-v2',previous_status_hash=prior_status['result_hash'],
        source_manifest_hash=acquired['manifest_hash'],new_resumption_dates=coverage,new_records=len(new),
        additional_effective_intervals=len(added),additional_records=list(added.values()),
        prior_start_date_records=prior_status['prior_start_date_records'],
        resumption_dates_covered=sorted(coverage+prior_status['covered_resumption_dates']),
        fixed_panel_matches=[r for r in new if r['symbol'] in oldrefs['symbols']],
        full_status_coverage=False,prior_unresumed_coverage=False,continuous_tradability=False,absence_means='UNKNOWN',execution_authority='none')
    status={**body,'result_hash':digest(body)};save(root/'status-review.json',status)
    execution=verified_json(h/'selectivity-v1/execution-v2/execution-audit.json','result_hash')
    steps=[('execution',dict(previous_reference=oldrefs,execution=execution),prior_status),
           ('status',dict(previous_reference=oldrefs,execution=execution),status),
           ('reference',dict(reference=references,execution=execution),status)]
    admissions={}
    for name,context,s in steps:
        result=refresh_admissions(h,context,s,root/('admission-after-'+name))
        admissions[name]=dict(result_hash=result['result_hash'],admitted=sum(x['admitted'] for x in result['admissions']),
            rejected=len(result['admissions']),audit=result['audit'],exact_rejections=[dict(experiment=x['experiment'],tier=x['tier'],reasons=x['reasons']) for x in result['admissions']])
    report=dict(version='admission-evidence-followup-v2',references_hash=references['result_hash'],status_hash=status['result_hash'],
        execution_hash=execution['result_hash'],admission_refreshes=admissions,gate_implementation_unchanged=True,
        valid_research_scope='conditional fixed-panel final-export screening diagnostics only; no strategy performance tier certified',
        original_availability_uncertainty='No finite maximum original delivery/revision lag established; synthetic delay scenarios cannot certify that bound',
        performance_experiments=0,execution_authority='none')
    final={**report,'result_hash':digest(report)};save(root/'track-a-report.json',final);return final
