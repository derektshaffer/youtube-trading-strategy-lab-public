"""One metadata-only RVOL feasibility audit. No historical feature/label runner.

Reuse the verified panel and ResearchSplit, but never call panel.session(): that
would materialize prices. The fixed SQL projection returns clocks, field-validity
flags and provenance hashes only. Arithmetic below accepts synthetic fixtures only.
"""
from datetime import datetime, timedelta
from fractions import Fraction
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .events import ContractError, digest, timestamp_ns
from .features import MINUTE
from .momentum import MomentumPolicy
from .research_split import ResearchSplit
from .research_panel import ResearchPanel
from .evidence_store import EvidenceStore, read_records

ROOT = Path(__file__).resolve().parent.parent
PANEL = ROOT / '.systematic-trader/real-history/certification-v2/panel'
NY = ZoneInfo('America/New_York')
DOMAIN = 'rvol-feasibility-v1'
SYMBOLS = ('ASPI','AVR','DGICB','FMBH','MDXH')
DAYS = ('2025-01-02','2025-01-03','2025-01-06','2025-01-07','2025-01-08',
        '2025-01-10','2025-01-13','2025-01-14','2025-01-15','2025-01-16')
PRIOR_SESSIONS = 14
PREFIX = 15


def specification():
    return dict(version=DOMAIN, family_id='hyp-report1-851a399c253c40d377f2',
        parent_inventory_checkpoint='77441ff', state='PRELIMINARY_DESIGN_ONLY',
        symbols=list(SYMBOLS), sessions=list(DAYS), dataset='owned-panel-v1',
        purpose='strategy_development', history_sessions=PRIOR_SESSIONS,
        history_basis='Last 14 scheduled completed sessions; no skipping incomplete or unauthorized dates',
        history_minutes=list(range(PREFIX)), cumulative_endpoints=[13,14,15],
        decision=dict(offset_minutes=15, delay_seconds=1, information_cutoff_offset_minutes=15,
                      timezone='America/New_York', decisions_per_symbol_session=1),
        target=dict(id='forward_max_native_high_30m', start_offset_minutes=16,
                    end_offset_minutes=46, denominator='native open at open+16m',
                    formula='max(native high in [open+16m, open+46m)) / native open at open+16m - 1',
                    interpretation='Predictive raw excursion only; no fill or achievable return'),
        formulas=dict(baseline='B(k)=sum(C_s(k) for the same 14 prior sessions)/14; C_s(k)=sum(native volume minutes 0..k-1)',
                      level='L=R(15); R(k)=C_current(k)/B(k)',
                      slope='S=(R(15)-R(14))/1 minute',
                      acceleration='A=(R(15)-2*R(14)+R(13))/(1 minute)^2'),
        variants=[dict(id='A',features=['L']),dict(id='B',features=['L','S']),dict(id='C',features=['L','S','A'])],
        controls=['fixed exchange-local clock and same-clock baseline',
                  'log1p(prefix native share volume)', 'log1p(prefix native dollar volume=sum(volume*vwap))',
                  'prefix close/open return', 'sqrt(sum of squared adjacent log-close returns in prefix)'],
        controls_notes='Same controls in all arms, train-only scaling if a future fit is authorized. Dollar volume is only a liquidity proxy; VWAP/condition basis must be reconciled. Trade count is quality metadata, not a predictor; no average-trade-size predictor.',
        estimator='No estimator fitted or execution design released. Future separately reviewed fixed nested linear comparison must freeze fitting/chronological partition and dependent uncertainty before any outcomes.',
        dependence='One point per stock-session; all symbols on a date grouped together; repeated-stock dependence retained. No adjacent-minute pseudo-replication.',
        accounting=dict(families=1,variants=3,targets=1,decision_boundaries=1,data_scopes=1,
                        planned_pairwise_comparisons=['B minus A','C minus B'],executed_trials=0,
                        threshold_searches=0,lookback_searches=0,performance_jobs=0),
        sample_floor=dict(session_groups=30, meaning='Conservative planning floor aligned with existing QualificationProtocol.minimum_sessions; not a power calculation or statistical guarantee'),
        assumptions=['Final export is not historical first arrival. A hypothetical one-second bar delay is not certified latency.',
                     'Literal archive ticker does not establish continuous identity or comparable share units across actions.',
                     'All three variants use the exact same frozen eligible-cell intersection.',
                     'Missing minutes are unknown, never zero. Explicit native zero volume is recorded separately and is invalid under the existing panel exclusionary finding.',
                     'No December warmup computation is authorized by its discovery label.',
                     'Predictive information cannot automatically become a trading strategy or override certification.'],
        falsification=['B adds no incremental information over A','C adds no incremental information over B',
                       'Effect vanishes on complete native observations or after clock/volume controls',
                       'Effect depends on missing minutes or unreconciled revisions/conditions',
                       'One stock/date drives the comparison','Too few independent groups or unusable warmup',
                       'Failed feasibility is a valid negative outcome; no scope expansion'],
        execution_authorized=False, historical_feature_computation=False, certified=False, orders_enabled=False)


def validate_spec(value):
    if digest(value) != digest(specification()):
        raise ContractError('rvol_design_frozen_no_extra_threshold_lookback_scope_target_or_boundary')
    if MomentumPolicy().minimum_history_sessions != PRIOR_SESSIONS:
        raise ContractError('rvol_existing_history_contract_changed')


def authorize_day(day):
    dt = datetime.fromisoformat(day).replace(tzinfo=NY)
    ResearchSplit().authorize(timestamp_ns(dt.isoformat()), timestamp_ns((dt+timedelta(days=1)).isoformat()), purpose='strategy_development')
    if day not in DAYS: raise ContractError('rvol_day_outside_frozen_scope')


def schedule(calendar, day):
    if day not in calendar: raise ContractError('rvol_holiday_or_session_missing')
    c = calendar[day]
    opening, closing = c['open'], c['close']
    if type(opening) is not int or type(closing) is not int or opening >= closing:
        raise ContractError('rvol_invalid_calendar')
    local = datetime.fromtimestamp(opening//1_000_000_000, NY)
    if local.date().isoformat()!=day or local.strftime('%H:%M:%S')!='09:30:00' or local.weekday()>=5:
        raise ContractError('rvol_invalid_open_date_or_clock')
    if opening+46*MINUTE>closing: raise ContractError('rvol_target_exceeds_early_close')
    return dict(open=opening,close=closing,cutoff=opening+15*MINUTE,
                decision=opening+15*MINUTE+1_000_000_000,
                target_start=opening+16*MINUTE,target_end=opening+46*MINUTE)


def prior_days(calendar, day):
    current=schedule(calendar,day)
    candidates=[]
    for d in sorted(calendar):
        if d>=day: continue
        # Calendar metadata may describe December; that grants no data access.
        c=calendar[d]
        if c['close'] < current['open']: candidates.append(d)
        else: raise ContractError('rvol_prior_session_not_completed')
    return candidates[-PRIOR_SESSIONS:]


# SQL never returns OHLC, VWAP, volume or trade-count values. JSON checks only
# expose field types/validity and native provenance; no labels or price features.
PROJECTION = '''SELECT stamp, day, segment,
    json_type(payload,'$.volume_shares')='integer' AND json_extract(payload,'$.volume_shares')>0,
    json_type(payload,'$.trade_count')='integer' AND json_extract(payload,'$.trade_count')>0,
    json_extract(payload,'$.volume_shares')=0,
    json_extract(payload,'$.interval_ns')=?,
    json_type(payload,'$.revision')='false',
    json_type(payload,'$.open')='text' AND json_type(payload,'$.high')='text'
      AND json_type(payload,'$.low')='text' AND json_type(payload,'$.close')='text'
      AND json_type(payload,'$.vwap')='text',
    json_extract(lineage,'$.raw_page_sha256'),
    json_extract(lineage,'$.native_row_hash'),
    json_extract(lineage,'$.normalized_payload_hash')
    FROM bars WHERE symbol=? AND day=? ORDER BY stamp'''


def project_cell(connection, symbol, day):
    authorize_day(day)
    if symbol not in SYMBOLS: raise ContractError('rvol_symbol_outside_frozen_scope')
    rows=[]
    for stamp, stored_day, segment, volume_ok, count_ok, zero, interval_ok, original, fields_ok, raw, native, normalized in connection.execute(PROJECTION,(MINUTE,symbol,day)):
        rows.append(dict(stamp=stamp,day=stored_day,segment=segment,
            volume_valid=volume_ok==1,trade_count_valid=count_ok==1,native_zero_volume=zero==1,
            interval_valid=interval_ok==1,original=original==1,price_field_types_present=fields_ok==1,
            raw_page_sha256=raw,native_row_hash=native,normalized_payload_hash=normalized))
    return rows


def coverage(rows, day, calendar):
    c=schedule(calendar,day); by_stamp={}; anomalies=[]
    for row in rows:
        t=row['stamp']
        if type(t) is not int or t%MINUTE or row['day']!=day or datetime.fromtimestamp(t//1_000_000_000,NY).date().isoformat()!=day:
            anomalies.append('invalid_timestamp_or_day');continue
        if t in by_stamp: anomalies.append('duplicate_native_minute')
        by_stamp[t]=row
        expected='regular' if c['open']<=t<c['close'] else 'pre' if t<c['open'] else 'post'
        if row['segment']!=expected:anomalies.append('session_classification_mismatch')
    def window(start,end):
        missing=[];invalid=[];present=0;zero=[]
        for t in range(start,end,MINUTE):
            row=by_stamp.get(t)
            clock=datetime.fromtimestamp(t//1_000_000_000,NY).strftime('%H:%M')
            if row is None:missing.append(clock);continue
            present+=1;reasons=[]
            for flag in ('volume_valid','trade_count_valid','interval_valid','original','price_field_types_present'):
                if not row[flag]:reasons.append(flag+'_failed')
            if row['segment']!='regular':reasons.append('not_regular')
            if not all(isinstance(row[k],str) and re.fullmatch('[0-9a-f]{64}',row[k]) for k in ('raw_page_sha256','native_row_hash','normalized_payload_hash')):
                reasons.append('native_lineage_missing')
            if row['native_zero_volume']:zero.append(clock)
            if reasons:invalid.append(dict(clock=clock,reasons=reasons))
        return dict(expected_minutes=(end-start)//MINUTE,native_minutes=present,missing_minutes=missing,
                    invalid_minutes=invalid,native_zero_minutes=zero,complete=not missing and not invalid and not anomalies)
    return dict(prefix=window(c['open'],c['cutoff']),target_metadata=window(c['target_start'],c['target_end']),
                regular=window(c['open'],c['close']),anomalies=sorted(set(anomalies)),
                metadata_hash=digest(rows),rows_projected=len(rows))


def fixture_terms(current, histories, *, decision_ns):
    """Exact formula regression only. Real-history inputs are deliberately denied."""
    if current.get('origin')!='fixture' or any(h.get('origin')!='fixture' for h in histories):
        raise ContractError('rvol_historical_computation_not_authorized')
    current_day=datetime.fromisoformat(current['day']).replace(tzinfo=NY)
    ResearchSplit().authorize(timestamp_ns(current_day.isoformat()),timestamp_ns((current_day+timedelta(days=1)).isoformat()),purpose='strategy_development')
    if len(histories)!=PRIOR_SESSIONS or len({h['day'] for h in histories})!=PRIOR_SESSIONS:
        raise ContractError('rvol_exact_fourteen_prior_sessions_required')
    for h in histories:
        dt=datetime.fromisoformat(h['day']).replace(tzinfo=NY)
        ResearchSplit().authorize(timestamp_ns(dt.isoformat()),timestamp_ns((dt+timedelta(days=1)).isoformat()),purpose='strategy_development')
        if h['day']>=current['day'] or h['close_ns']>=current['open_ns'] or h['available_ns']>decision_ns:
            raise ContractError('rvol_future_history_forbidden')
    for row in [current,*histories]:
        opening=datetime.fromtimestamp(row['open_ns']//1_000_000_000,NY)
        if opening.date().isoformat()!=row['day'] or opening.strftime('%H:%M:%S')!='09:30:00':
            raise ContractError('rvol_same_clock_open_required')
        if row['available_ns']<row['open_ns']+PREFIX*MINUTE:
            raise ContractError('rvol_incomplete_bar_available_too_early')
        if row['available_ns']>decision_ns:raise ContractError('rvol_future_row_forbidden')
        if set(row['volumes'])!=set(range(PREFIX)) or any(type(v) is not int or v<=0 for v in row['volumes'].values()):
            raise ContractError('rvol_missing_or_invalid_minute_not_zero_filled')
    if current['open_ns']+PREFIX*MINUTE+1_000_000_000!=decision_ns:
        raise ContractError('rvol_single_decision_boundary_required')
    ratios=[]
    for k in (13,14,15):
        baseline=sum(Fraction(sum(h['volumes'][i] for i in range(k)),PRIOR_SESSIONS) for h in histories)
        if baseline<=0:raise ContractError('rvol_zero_baseline')
        ratios.append(Fraction(sum(current['volumes'][i] for i in range(k)),1)/baseline)
    return dict(L=str(ratios[2]),S=str(ratios[2]-ratios[1]),A=str(ratios[2]-2*ratios[1]+ratios[0]))


def common_cells(cells):
    keys=[(c['symbol'],c['day']) for c in cells]
    if len(keys)!=len(set(keys)):raise ContractError('rvol_overlapping_stock_session')
    if set(keys)!={(s,d) for s in SYMBOLS for d in DAYS}:raise ContractError('rvol_cell_scope_changed')
    if any(type(c['eligible']) is not bool for c in cells):raise ContractError('rvol_eligibility_must_be_explicit')
    eligible=sorted(s+'|'+d for (s,d),c in zip(keys,cells) if c['eligible'])
    return {variant:list(eligible) for variant in ('A','B','C')}


def evaluate_metadata(manifest, metadata):
    validate_spec(specification());calendar=manifest['calendar'];cells=[]
    if set(metadata)!={(s,d) for s in SYMBOLS for d in DAYS}:raise ContractError('rvol_metadata_scope_changed')
    for day in DAYS:
        authorize_day(day)
        prior=prior_days(calendar,day);authorized=[];denied=[]
        for d in prior:
            try:authorize_day(d);authorized.append(d)
            except ContractError as exc:denied.append(dict(day=d,reason=str(exc)))
        for symbol in SYMBOLS:
            own=metadata[(symbol,day)];reasons=[]
            if denied:reasons.append('warmup_contains_unauthorized_dates')
            if len(authorized)<PRIOR_SESSIONS:reasons.append('fewer_than_14_authorized_completed_prior_sessions')
            bad_prior=[d for d in authorized if not metadata[(symbol,d)]['prefix']['complete']]
            if bad_prior:reasons.append('required_prior_same_clock_minutes_incomplete_or_invalid')
            if not own['prefix']['complete']:reasons.append('current_prefix_incomplete_or_invalid')
            if not own['target_metadata']['complete']:reasons.append('target_native_coverage_incomplete_or_invalid_metadata_only')
            # The verified panel explicitly states these unknowns. Do not infer
            # identity/adjustment continuity merely from a literal ticker.
            reasons.extend(['cross_session_identity_and_share_unit_comparability_unresolved',
                            'original_bar_availability_and_revision_timing_unverified',
                            'vwap_condition_basis_for_dollar_volume_control_unreconciled'])
            cells.append(dict(symbol=symbol,day=day,eligible=False,reasons=reasons,
                required_prior_sessions=prior,authorized_prior_sessions=authorized,unauthorized_prior_sessions=denied,
                clean_authorized_same_clock_prior_sessions=[d for d in authorized if d not in bad_prior],
                incomplete_authorized_prior_sessions=bad_prior,coverage=own,
                preliminary_identity_qualification='Unresolved for cross-session share-volume normalization; not a demand for a new Tier-1 certificate',
                target_domain='archive-conditional candidate; historical small-cap/float membership not certified'))
    cohorts=common_cells(cells)
    regular_expected=sum(c['coverage']['regular']['expected_minutes'] for c in cells)
    regular_missing=sum(len(c['coverage']['regular']['missing_minutes']) for c in cells)
    result=dict(version=DOMAIN,verdict='NOT FEASIBLE',state='PRELIMINARY_DESIGN_ONLY',
        design_status='FROZEN_FEASIBILITY_REQUIREMENTS_EXECUTION_DESIGN_WITHHELD',design_hash=digest(specification()),
        manifest_hash=manifest['manifest_hash'],database_hash=manifest['normalized_database_sha256'],
        cells=cells,variant_cells=cohorts,metadata_only=True,outcomes_inspected=False,
        counts=dict(candidate_symbol_sessions=len(cells),eligible_symbol_sessions=0,eligible_decision_points=0,
            eligible_session_groups=0,maximum_pre_attrition_session_groups=len(DAYS),candidate_target_domain_cells=len(cells),
            certified_small_cap_membership_cells=0,warmup_attrition_cells=len(cells),
            complete_current_prefix_cells=sum(c['coverage']['prefix']['complete'] for c in cells),
            complete_target_metadata_cells=sum(c['coverage']['target_metadata']['complete'] for c in cells),
            complete_current_prefix_and_target_cells=sum(c['coverage']['prefix']['complete'] and c['coverage']['target_metadata']['complete'] for c in cells),
            regular_expected_minutes=regular_expected,regular_missing_minutes=regular_missing,
            regular_missing_rate=str(Fraction(regular_missing,regular_expected))),
        blockers=['All 50 cells lack 14 authorized prior sessions; zero to nine earlier in-scope January sessions exist.',
                  'The last 14 scheduled sessions require December for every cell; discovery warmup does not grant strategy access.',
                  'Same-clock gaps cannot be zero-filled or replaced with older/midday observations.',
                  'Historical identity/action share-unit comparability and original availability are unresolved.',
                  'At most 10 date clusters even before attrition; below the conservative 30-session planning floor, not a formal power calculation.'],
        authorized_run=False,executed_trials=0,certified=False,orders_enabled=False)
    result['result_hash']=digest(result)
    return result


def run(directory):
    spec=specification();validate_spec(spec)
    audit=EvidenceStore(directory,DOMAIN)
    with audit.locked() as journal:
        records=audit.records(journal)
        if not any(r['kind']=='design' for r in records):
            audit.append(journal,'design','design',dict(specification=spec,design_hash=digest(spec),execution_authorized=False))
        elif next(r['body']['specification'] for r in records if r['kind']=='design')!=spec:
            raise ContractError('rvol_registered_design_changed')
        if any(r['kind']=='feasibility' for r in records):return replay(directory)
        pending=next((r['body'] for r in records if r['kind']=='metadata'),None)
        if pending is not None:
            # Resume from committed metadata after an interruption, without
            # re-querying the archive or overwriting the original projection.
            result=_metadata_result(pending)
            audit.append(journal,'feasibility','feasibility',result)
            return replay(directory)
        # Freeze requirements before even metadata inspection. Do not instantiate
        # PreliminaryResearch, TargetCampaign, order clients or a strategy runner.
        panel=ResearchPanel(PANEL)
        try:
            projected={(s,d):project_cell(panel.connection,s,d) for d in DAYS for s in SYMBOLS}
            metadata={key:coverage(rows,key[1],panel.manifest['calendar']) for key,rows in projected.items()}
            result=evaluate_metadata(panel.manifest,metadata)
            audit.append(journal,'metadata','metadata',dict(cells=[dict(symbol=s,day=d,projection=projected[(s,d)]) for d in DAYS for s in SYMBOLS],
                manifest_projection={k:panel.manifest[k] for k in ('manifest_hash','normalized_database_sha256','calendar','raw_adjustment','series_identity','availability','admission_scope')},
                query=PROJECTION,outcomes_inspected=False))
            audit.append(journal,'feasibility','feasibility',result)
        finally:panel.close()
    return replay(directory)


def _metadata_result(raw):
    manifest=raw['manifest_projection']
    keys=[(x['symbol'],x['day']) for x in raw['cells']]
    if len(keys)!=len(set(keys)):raise ContractError('rvol_duplicate_metadata_cell')
    metadata={(x['symbol'],x['day']):coverage(x['projection'],x['day'],manifest['calendar']) for x in raw['cells']}
    return evaluate_metadata(manifest,metadata)


def replay(directory):
    rows=read_records(directory,DOMAIN)
    spec=next(r['body']['specification'] for r in rows if r['kind']=='design');validate_spec(spec)
    raw=next(r['body'] for r in rows if r['kind']=='metadata')
    saved=next(r['body'] for r in rows if r['kind']=='feasibility')
    computed=_metadata_result(raw)
    if computed!=saved:raise ContractError('rvol_metadata_replay_mismatch')
    return {**computed,'journal_head':rows[-1]['hash'],'journal_records':len(rows)}


def review_packet(directory, created_at):
    """Prepare the existing review contract; do not submit or call any model."""
    import hashlib
    from ai_review.contracts import canonical, validate_packet
    result=replay(directory)
    content=canonical(dict(design=specification(),metadata_feasibility=result))
    packet=dict(artifact_id=DOMAIN,checkpoint='new_hypothesis',
        evidence=[dict(id='frozen-design-and-metadata',content=content,sha256=hashlib.sha256(content.encode()).hexdigest(),
            provenance=dict(uri='local-rvol-feasibility:'+result['result_hash'],retrieved_at=created_at,publisher='Trading Lab metadata-only audit'))],
        assumptions=specification()['assumptions'],specification=canonical(specification()),
        deterministic_context=dict(verdict=result['verdict'],state='PRELIMINARY_DESIGN_ONLY',
                                   certified=False,runner_authorized=False,orders_enabled=False,review_status='NOT_SUBMITTED'),resolutions=[])
    validate_packet(packet)
    return packet


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Metadata feasibility only; no performance-run option')
    parser.add_argument('operation',choices=['audit','replay']);parser.add_argument('--directory',required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.directory) if args.operation=='audit' else replay(args.directory),sort_keys=True))
