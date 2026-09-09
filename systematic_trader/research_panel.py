"""Versioned raw-symbol research panel with enforced chronological readers.

Archive series are NOT historical instrument identities or market membership.
This admits conditional algorithm diagnostics, never fabricated live state.
"""
from collections import Counter
from datetime import date,datetime,timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from .data_acquisition import verified_pages
from .dataset_audit import NY,calendar_sessions,validate_bar
from .events import ContractError,canonical_json,digest,timestamp_ns
from .features import MINUTE
from .research_check import save
from .research_split import ResearchSplit,HOLDOUT_START,HOLDOUT_END

SOURCE_HASH='f28ce02f516148dde69d13e61512e7e64ce1cef2c8ccde525e244f14191c042e'


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
    return h.hexdigest()


def independent_calendar_check(sessions):
    # Exchange holiday schedules plus Nasdaq ETA2024-87 (Carter closure).
    holidays={'2024-12-25','2025-01-01','2025-01-09','2025-01-20','2025-02-17','2025-04-18'}
    expected=set();d=date(2024,12,2)
    while d<=date(2025,4,30):
        if d.weekday()<5 and d.isoformat() not in holidays:expected.add(d.isoformat())
        d+=timedelta(days=1)
    issues=[]
    if set(sessions)!=expected:issues.append('regular_session_dates_disagree_with_exchange_notices')
    for day,c in sessions.items():
        offset='-05:00' if day<'2025-03-09' else '-04:00'
        close='13:00:00' if day=='2024-12-24' else '16:00:00'
        if c['open']!=timestamp_ns(day+'T09:30:00'+offset) or c['close']!=timestamp_ns(day+'T'+close+offset):
            issues.append('regular_session_clock_disagreement:'+day)
    return dict(expected_sessions=len(expected),issues=issues,regular_passed=not issues,
        extended_hours='provider_generic_boundaries_not_exchange_specific_early_close_certification',
        sources=['https://www.nyse.com/markets/hours-calendars','https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2024-87'])


def bar_findings(payload):
    findings=[]
    if payload['volume_shares']==0:findings.append(('zero_volume_price_bar','exclusionary'))
    if payload['trade_count']==0:findings.append(('zero_trade_count_price_bar','exclusionary'))
    if Decimal(payload['vwap'])<=0:findings.append(('nonpositive_vwap','exclusionary'))
    # VWAP and high/low use different sale-condition rules. Flag for inspection,
    # never clip VWAP to the OHLC range or assert the source is corrupt.
    elif not Decimal(payload['low'])<=Decimal(payload['vwap'])<=Decimal(payload['high']):
        findings.append(('vwap_outside_price_forming_range','repairable_requires_condition_reconciliation'))
    return findings


def build(source,directory):
    source,root=Path(source),Path(directory)
    certificate=json.loads((source/'dataset-certification.json').read_text())
    if certificate.get('dataset_manifest_hash')!=SOURCE_HASH or digest({k:v for k,v in certificate.items() if k!='dataset_manifest_hash'})!=SOURCE_HASH:
        raise ContractError('research_panel_source_certificate_mismatch')
    root.mkdir(parents=True,exist_ok=False,mode=0o700)
    sessions=calendar_sessions([r for p,m in verified_pages(source/'calendar') for r in p])
    calendar=independent_calendar_check(sessions)
    if not calendar['regular_passed']:raise ContractError('research_panel_calendar_rejected')
    protocol=json.loads((source/'study-protocol.v2.json').read_text())
    ResearchSplit(protocol['protocol_hash'])
    symbols=[s['symbol'] for s in protocol['selection'] if s['symbol'] not in {'SPY','QQQ','IWM'}]
    db=root/'panel.sqlite3';conn=sqlite3.connect(db)
    conn.executescript('CREATE TABLE bars(symbol TEXT, stamp INTEGER, day TEXT, segment TEXT, payload TEXT, lineage TEXT, PRIMARY KEY(symbol,stamp)); CREATE INDEX by_day ON bars(day,symbol,stamp);')
    chain='0'*64;counts=Counter();findings=Counter();examples=[];source_heads={};batch=[]
    try:
        for page,meta in verified_pages(source/'bars'):
            for symbol,rows in page['bars'].items():
                for row in rows:
                    stamp,p=validate_bar(symbol,row)
                    if HOLDOUT_START<=stamp<HOLDOUT_END:raise ContractError('holdout_found_in_panel_source')
                    day=datetime.fromtimestamp(stamp//1_000_000_000,NY).date().isoformat();c=sessions.get(day)
                    segment='outside' if c is None or not c['pre']<=stamp<c['post'] else 'pre' if stamp<c['open'] else 'regular' if stamp<c['close'] else 'post'
                    issues=bar_findings(p)
                    if segment=='outside':issues.append(('outside_declared_session','exclusionary'))
                    for reason,classification in issues:
                        findings[reason+':'+classification]+=1
                        if len(examples)<50:examples.append(dict(symbol=symbol,stamp=stamp,reason=reason,classification=classification,source_sha256=meta['sha256']))
                    lineage=dict(raw_page_sha256=meta['sha256'],native_row_hash=digest(row),normalized_payload_hash=digest(p))
                    chain=digest([chain,symbol,stamp,segment,lineage])
                    batch.append((symbol,stamp,day,segment,canonical_json(p),canonical_json(lineage)))
                    counts[symbol]+=1
            conn.executemany('INSERT INTO bars VALUES(?,?,?,?,?,?)',batch);batch=[]
        conn.commit()
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ContractError('normalized_panel_database_integrity')
    finally:conn.close()
    db.chmod(0o600)
    for kind,manifest in certificate['sources'].items():
        current=json.loads((source/kind/'complete.json').read_text())
        if current!=manifest:raise ContractError('research_panel_source_manifest_mismatch')
        source_heads[kind]=current
    actions=certificate['corporate_actions']
    body=dict(version='bounded-research-panel-v1',source_provider='alpaca',feed='sip',raw_adjustment='raw',
        source_manifest_hash=SOURCE_HASH,sources=source_heads,normalized_database_sha256=file_hash(db),normalized_chain_hash=chain,
        normalization='existing_alpaca_market_bar_adapter_v1',bar_counts=dict(counts),bar_count=sum(counts.values()),
        symbols=symbols,requested_symbols=22,symbols_with_records=len(counts),calendar=sessions,calendar_check=calendar,
        session_counts=dict(total=len(sessions),warmup=sum(d<'2025-01-01' for d in sessions),development=sum('2025-01-02'<=d<='2025-02-28' for d in sessions),
            validation=sum('2025-03-03'<=d<='2025-03-31' for d in sessions),oos=sum('2025-04-01'<=d<='2025-04-30' for d in sessions)),
        missing_data={s:dict(missing_regular_minutes=r['missing_regular_minutes'],missing_sessions=len(r['missing_sessions'])) for s,r in certificate['by_symbol'].items()},
        findings=dict(findings),finding_examples=examples,corporate_actions=actions,halt_coverage='separate_official_archive_not_complete_tradability',
        quote_trade_coverage=certificate['microstructure'],split=ResearchSplit().manifest(),
        symbol_selection=protocol['selection_rule'],membership='fixed_retrospective_archive_panel_no_current_status_filter_no_market_universe_claim',
        series_identity='literal_archive_ticker_asof_dash_not_stable_security_identity',
        availability='final_export_original_receipt_and_revision_times_unverified',
        admission_level='RESEARCH-ADMISSIBLE',admission_scope='conditional_prefix_discovery_diagnostics_only',
        limitations=['retrospective_panel_selection','unverified_first_publication_and_revisions','unresolved_stable_identity',
            'incomplete_halt_and_corporate_action_known_time','sparse_minutes_not_imputed','no_unbiased_market_recall','no_execution_returns'],
        validation_admissible=False,production_calibration_admissible=False,execution_authority='none')
    result={**body,'manifest_hash':digest(body)};save(root/'manifest.json',result);return result


class ResearchPanel:
    def __init__(self,directory):
        root=Path(directory);self.manifest=json.loads((root/'manifest.json').read_text());m=self.manifest
        if m['manifest_hash']!=digest({k:v for k,v in m.items() if k!='manifest_hash'}):raise ContractError('panel_manifest_integrity')
        if m['source_manifest_hash']!=SOURCE_HASH or m['split']!=ResearchSplit().manifest():raise ContractError('panel_source_or_split_mismatch')
        if file_hash(root/'panel.sqlite3')!=m['normalized_database_sha256']:raise ContractError('panel_normalized_hash_mismatch')
        self.connection=sqlite3.connect((root/'panel.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)

    def close(self):self.connection.close()

    def session(self,day,*,purpose='discovery_development'):
        # Authorize BEFORE executing any data query, even for missing sessions.
        dt=datetime.fromisoformat(day).replace(tzinfo=NY)
        a=timestamp_ns(dt.isoformat());b=timestamp_ns((dt+timedelta(days=1)).isoformat())
        ResearchSplit().authorize(a,b,purpose=purpose)
        rows=self.connection.execute('SELECT symbol,stamp,segment,payload,lineage FROM bars WHERE day=? ORDER BY symbol,stamp',(day,))
        result={}
        for symbol,stamp,segment,payload,lineage in rows:
            result.setdefault(symbol,[]).append(dict(stamp=stamp,segment=segment,payload=json.loads(payload),lineage=json.loads(lineage)))
        return result


def admission(manifest, *, experiment, purpose, tier=None, explicit_export_assumptions=False, conservative_tier1=False,
              window=None, symbols=None):
    if manifest['manifest_hash']!=digest({k:v for k,v in manifest.items() if k!='manifest_hash'}):raise ContractError('admission_manifest_hash_mismatch')
    reasons=[]
    if manifest['source_manifest_hash']!=SOURCE_HASH or manifest['split']!=ResearchSplit().manifest():reasons.append('source_or_split_mismatch')
    if not manifest['calendar_check']['regular_passed']:reasons.append('calendar_rejected')
    if purpose=='conditional_discovery':
        if not explicit_export_assumptions:reasons.append('explicit_final_export_and_panel_limitations_required')
    elif purpose=='strategy':
        if window is None:reasons.append('exact_experiment_window_required')
        else:
            try:ResearchSplit().authorize(window[0],window[1],purpose='strategy_development')
            except (ContractError,IndexError,TypeError):reasons.append('experiment_window_not_authorized_development')
        if not symbols or not set(symbols)<=set(manifest.get('symbols',[])):reasons.append('experiment_symbol_scope_unavailable')
        elif any(manifest.get('bar_counts',{}).get(s,0)==0 for s in symbols):reasons.append('required_symbol_bar_history_missing')
        if tier not in {1,2}:reasons.append('explicit_execution_tier_required')
        if tier==1 and not conservative_tier1:reasons.append('conservative_tier1_assumptions_not_enabled')
        reasons.extend(['historical_identity_and_universe_inadequate','corporate_action_original_availability_unresolved',
            'original_bar_revision_availability_unverified','historical_tradability_not_established'])
        if tier==2:reasons.append('complete_quote_trade_status_inputs_for_experiment_missing')
        if tier==1:reasons.append('minute_bars_cannot_bound_fills_across_unknown_halts')
    else:reasons.append('purpose_not_admitted_by_bounded_dataset')
    result=dict(version='experiment-data-admission-v2',experiment=experiment,purpose=purpose,tier=tier,
        dataset_manifest_hash=manifest['manifest_hash'],admitted=not reasons,reasons=sorted(reasons),
        admission_level='RESEARCH-ADMISSIBLE' if not reasons else 'REJECTED',
        scope='conditional_archive_diagnostic_only' if not reasons else None,
        production_qualified=False,execution_authority='none')
    if purpose=='strategy':result.update(requested_window=window,requested_symbols=symbols,conservative_tier1_enabled=conservative_tier1)
    return {**result,'admission_hash':digest(result)}
