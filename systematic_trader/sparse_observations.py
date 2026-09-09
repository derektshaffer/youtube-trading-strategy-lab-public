"""Versioned research observation states. No synthetic bars or execution authority."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
from .data_acquisition import verified_pages
from .events import ContractError, digest, timestamp_ns
from .features import MINUTE
from .research_panel import ResearchPanel, bar_findings
from .research_check import save
from .research_split import ResearchSplit
from .halt_history import parse_halts
from .research_panel import file_hash

VERSION = 'sparse-observation-v2'
TRADE_RULE_VERSION = 'alpaca-final-export-trade-rule-v2'
CONDITION_SOURCE = 'https://docs.alpaca.markets/us/docs/market-data-faq'
# Documented minute aggregation only, not SIP execution eligibility.
NO_PRICE = {'A': set('BCHIMNPQRUVZ479'), 'B': set('BCHIMNPQRUVZ479'),
            'C': set('CGHIMNPQRUVWZ479'), 'O': set('CINPRUW')}
YES_PRICE = {'A': set(' EFKLOTX56'), 'B': set(' EFKLOTX56'),
             'C': set('@ABDFKLOTXY56'), 'O': set('@T')}
NO_VOLUME = set('MQ9')


def trade_rule(row):
    # Historical exports retain invalidated originals. Apply their final update
    # status before the unchanged sale-condition matrix. This is NOT the time
    # at which a live application learned about a correction or cancellation.
    if 'u' in row:
        if row['u'] in ('canceled', 'incorrect'):
            return False, False
        if row['u'] != 'corrected':
            return None, None  # Unknown/null update states cannot certify absence.
    tape, conditions = row.get('z'), row.get('c')
    if tape not in NO_PRICE or not isinstance(conditions, list) or not conditions:
        return None, None
    if not all(isinstance(c, str) for c in conditions): return None, None
    codes = set(conditions)
    unknown = codes - NO_PRICE[tape] - YES_PRICE[tape]
    price = False if codes & NO_PRICE[tape] else None if unknown else True
    volume = False if tape in {'A','B','C'} and codes & NO_VOLUME else None if unknown else True
    return price, volume


@dataclass(frozen=True)
class TapeCoverage:
    symbol: str
    start_ns: int
    end_ns: int  # half-open
    retrieved_ns: int
    source_hash: str

    def __post_init__(self):
        if (not self.symbol or self.start_ns >= self.end_ns or self.retrieved_ns <= 0 or
                len(self.source_hash) != 64):
            raise ContractError('invalid_sparse_tape_coverage')

    def covers(self, start): return self.start_ns <= start and start+MINUTE <= self.end_ns


def minute_state(symbol, start, *, bar=None, trades=(), quotes=(), coverage=None,
                 identity='archive_series_unverified', identity_source=None, action_sources=(), halted=None, bar_observed_ns=None):
    if start % MINUTE: raise ContractError('sparse_minute_not_aligned')
    if coverage and coverage.symbol != symbol: raise ContractError('sparse_coverage_symbol_mismatch')
    complete = bool(coverage and coverage.covers(start)); rows = []
    for row in trades:
        t = timestamp_ns(row['t'])
        if not start <= t < start+MINUTE: raise ContractError('sparse_trade_outside_minute')
        p = Decimal(str(row['p'])); v = row['s']
        if not p.is_finite() or p <= 0 or type(v) is not int or v <= 0:
            raise ContractError('sparse_invalid_native_trade')
        rows.append(row)
    # Timestamp ordering is explicit; it is NOT original arrival reconstruction.
    rows.sort(key=lambda r:(timestamp_ns(r['t']), digest(r)))
    rules = [trade_rule(r) for r in rows]
    eligible = [r for r, rule in zip(rows, rules) if rule[0] is True]
    unknown = any(rule[0] is None for rule in rules)
    issues = []
    if bar:
        if bar['stamp'] != start or bar_findings(bar['payload']): issues.append('invalid_native_bar')
        if bar['lineage']['normalized_payload_hash'] != digest(bar['payload']):
            raise ContractError('sparse_native_bar_hash_mismatch')
        if complete and not eligible: issues.append('bar_without_documented_eligible_print')
        if complete and unknown: issues.append('unresolved_trade_conditions')
        if complete and eligible:
            high, low = max(Decimal(str(r['p'])) for r in eligible), min(Decimal(str(r['p'])) for r in eligible)
            p = bar['payload']
            # Equal-source-time order cannot prove open/close order. Check ranges.
            if high != Decimal(p['high']) or low != Decimal(p['low']): issues.append('bar_tape_price_range_disagreement')
            if all(rule[1] is not None for rule in rules):
                volume = sum(r['s'] for r,rule in zip(rows,rules) if rule[1])
                if volume != p['volume_shares']: issues.append('bar_tape_volume_disagreement')
        activity = 'observed_price_forming_bar'
        coverage_state = 'unresolved' if issues else 'native_bar_observed'
    elif complete and not rows:
        activity, coverage_state = 'documented_no_trades', 'documented_absence'
    elif complete and all(rule[0] is False for rule in rules):
        activity, coverage_state = 'documented_non_price_forming_trades', 'documented_absence'
    else:
        activity, coverage_state = 'unresolved_missing_price_bar', 'unresolved'
        if rows and unknown: issues.append('unresolved_trade_conditions')
        if eligible: issues.append('eligible_trades_without_native_bar')
        if not complete: issues.append('complete_trade_coverage_unavailable')
    quote_rows = []
    for q in quotes:
        if not start <= timestamp_ns(q['t']) < start+MINUTE: raise ContractError('sparse_quote_outside_minute')
        quote_rows.append(q)
    if not bar and quote_rows:
        activity = 'quote_only_observation'  # Trade coverage remains independently unresolved or documented.
    last_trade = max((timestamp_ns(r['t']) for r in eligible), default=None)
    # Only claim an exact last price time when the eligible last timestamp's prices agree with the bar close.
    exact = last_trade if bar and complete and eligible and not issues and all(
        Decimal(str(r['p'])) == Decimal(bar['payload']['close']) for r in eligible if timestamp_ns(r['t']) == last_trade) else None
    body = dict(version=VERSION, symbol=symbol, minute_start_ns=start, minute_end_ns=start+MINUTE,
        activity=activity, coverage=coverage_state, issues=sorted(issues), bar=bar,
        provider_timestamp_ns=start if bar else None, exact_last_price_trade_ns=exact,
        actual_observed_ns=coverage.retrieved_ns if coverage else bar_observed_ns, bar_actual_observed_ns=bar_observed_ns, original_available_ns=None,
        availability='final_export_only_original_arrival_unverified', assumed_available_ns=None,
        trade_count=len(rows), eligible_price_prints=len(eligible), unknown_price_rules=sum(r[0] is None for r in rules),
        trade_rule_version=TRADE_RULE_VERSION,
        trade_update_counts=dict(Counter(str(r['u']) if 'u' in r else 'not_provided' for r in rows)),
        invalidated_prints=sum(r.get('u') in ('canceled', 'incorrect') for r in rows),
        native_print_volume=sum(r['s'] for r in rows),
        native_eligible_volume=sum(r['s'] for r,rule in zip(rows,rules) if rule[1]) if complete and all(rule[1] is not None for rule in rules) else None,
        emitted_bar_volume=bar['payload']['volume_shares'] if bar else 0 if coverage_state=='documented_absence' else None,
        # This zero is a count of volume in EMITTED bars, never an imputed bar or a claim of no trading.
        conditions=dict(Counter(str(r.get('z'))+':'+','.join(r.get('c') or []) for r in rows)),
        quote_count=len(quote_rows), quote_hash=digest(quote_rows) if quote_rows else None,
        trade_rows_hash=digest(rows), source_hash=coverage.source_hash if coverage else None,
        identity=identity, identity_source=identity_source, action_sources=list(action_sources),
        tradability='HALTED' if halted else 'UNKNOWN', execution_authority='none', fill_allowed=False)
    return {**body, 'state_hash':digest(body)}


def read_trade_day(directory, day, calendar):
    c = calendar[day]
    ResearchSplit().authorize(c['open'], c['close'], purpose='discovery_development')
    path = Path(directory); m = json.loads((path/'complete.json').read_text()); p = m['params']
    if m['kind'] != 'trades' or p.get('feed') != 'sip' or p.get('asof') != '-' or p.get('sort') != 'asc':
        raise ContractError('sparse_tape_source_mismatch')
    start, end = timestamp_ns(p['start']), timestamp_ns(p['end'])+1
    ResearchSplit().authorize(start, end, purpose='discovery_development')
    if start > c['open'] or end < c['close']: raise ContractError('sparse_tape_window_incomplete')
    symbols = p['symbols'].split(','); grouped = defaultdict(list); last = {}; retrieved = 0
    for page, meta in verified_pages(path):
        retrieved = max(retrieved, meta['retrieved_ns'])
        for s, rows in page['trades'].items():
            if s not in symbols: raise ContractError('sparse_unrequested_trade_symbol')
            for r in rows:
                t = timestamp_ns(r['t'])
                if not start <= t < end: raise ContractError('sparse_trade_outside_query')
                if t < last.get(s, t): raise ContractError('sparse_source_order_violation')
                last[s] = t
                grouped[(s,t//MINUTE*MINUTE)].append(r)
    # Reached iterator exhaustion: final page chain and manifest now verified.
    evidence = {s:TapeCoverage(s,start,end,retrieved,m['page_chain_head']) for s in symbols}
    return grouped, evidence, m


def identity_bound(symbol, day, records):
    for r in records:
        if r['symbol'] != symbol: continue
        kind, effective = r['evidence_kind'], r['effective_date']
        if kind=='listed_from' and day < effective: return 'prelisting', digest(r)
        if kind=='last_trading_date' and day > effective: return 'post_delisting', digest(r)
        if kind=='renamed_from_VERB' and day < effective: return 'wrong_era_literal_symbol', digest(r)
    return 'archive_series_unverified', None


def build(panel_directory, tape_directory, membership_path, directory):
    panel = ResearchPanel(panel_directory); root = Path(directory)
    root.mkdir(parents=True,exist_ok=False,mode=0o700)
    references=json.loads(Path(membership_path).read_text()); days={}; totals=Counter(); source_manifests={}
    raw_meta=Path(panel_directory).parent.parent/'pilot-2025/bars'
    observed={r['sha256']:r['retrieved_ns'] for p in raw_meta.glob('*.meta.json') for r in [json.loads(p.read_text())]}
    halt_root=Path(panel_directory).parent/'halts-development';halt_manifest=json.loads((halt_root/'complete.json').read_text());halts=defaultdict(list)
    if halt_manifest['manifest_hash']!=digest({k:v for k,v in halt_manifest.items() if k!='manifest_hash'}):raise ContractError('sparse_halt_manifest_integrity')
    for d,sha in halt_manifest['heads'].items():
        if file_hash(halt_root/(d+'.raw.xml'))!=sha:raise ContractError('sparse_halt_source_integrity')
        for row in parse_halts((halt_root/(d+'.raw.xml')).read_bytes(),requested_date=d):halts[row['symbol']].append(row)
    try:
        for day,c in sorted(panel.manifest['calendar'].items()):
            if day > '2025-02-28': continue
            data=panel.session(day); path=Path(tape_directory)/day/'trades'; grouped={}; evidence={}
            if (path/'complete.json').exists():
                grouped,evidence,m=read_trade_day(path,day,panel.manifest['calendar']);source_manifests[day]=m
            states={}
            for s in panel.manifest['symbols']:
                bars={r['stamp']:r for r in data.get(s,[]) if r['segment']=='regular'}
                identity,source=identity_bound(s,day,references['records'])
                states[s]=[minute_state(s,t,bar=bars.get(t),trades=grouped.get((s,t),()),coverage=evidence.get(s),
                    identity=identity,identity_source=source,bar_observed_ns=observed.get(bars[t]['lineage']['raw_page_sha256']) if t in bars else None) for t in range(c['open'],c['close'],MINUTE)]
                states[s]=reference_overlay(states[s],day,panel.manifest['corporate_actions'],halts[s])
                for state in states[s]:
                    totals[state['activity']]+=1
                    for issue in state['issues']:totals[issue]+=1
            body=dict(version=VERSION,day=day,states=states,panel_manifest_hash=panel.manifest['manifest_hash'])
            result={**body,'result_hash':digest(body)};save(root/(day+'.json'),result);days[day]=result['result_hash']
            print(json.dumps(dict(day=day,states=sum(map(len,states.values())))),flush=True)
        body=dict(version=VERSION,panel_manifest_hash=panel.manifest['manifest_hash'],days=days,
            tape_sources=source_manifests,membership_source_hash=digest(references),halt_manifest_hash=halt_manifest['manifest_hash'],reference_overlay_included=True,counts=dict(totals),
            original_arrival_verified=False,execution_authority='none',synthetic_bars=0)
        result={**body,'manifest_hash':digest(body)};save(root/'manifest.json',result);return result
    finally:panel.close()


class SparsePanel:
    def __init__(self, directory):
        self.root=Path(directory);self.manifest=json.loads((self.root/'manifest.json').read_text())
        if self.manifest['manifest_hash']!=digest({k:v for k,v in self.manifest.items() if k!='manifest_hash'}):
            raise ContractError('sparse_manifest_integrity')

    def session(self,day):
        # Protect chronology before any price-file read.
        a=timestamp_ns(day+'T00:00:00-05:00')
        ResearchSplit().authorize(a,a+24*60*MINUTE,purpose='discovery_development')
        row=json.loads((self.root/(day+'.json')).read_text())
        if row['result_hash']!=self.manifest['days'][day] or digest({k:v for k,v in row.items() if k!='result_hash'})!=row['result_hash']:
            raise ContractError('sparse_session_integrity')
        return row['states']


def reference_overlay(states, day, actions, halts):
    """Retrospective exclusion-only references; never backdated publication."""
    from copy import deepcopy
    from .halt_history import status_bound
    output=[];last=None
    for original in sorted(states,key=lambda s:s['minute_start_ns']):
        s=deepcopy(original);symbol=s['symbol'];sources=[]
        for action in actions:
            r=action['record'];effective=r.get('ex_date') or r.get('effective_date') or r.get('process_date')
            if effective==day and symbol in r.values():sources.append(digest(action))
        status=status_bound(halts,symbol,s['minute_end_ns']-1)
        s.update(action_sources=sources,tradability='HALTED' if status['halted'] else 'UNKNOWN',
            halt_sources=status['sources'],reference_availability='retrospective_quality_mask_not_original_delivery')
        if s['bar'] and not s['issues']:
            last=dict(price=s['bar']['payload']['close'],provider_bar_start_ns=s['minute_start_ns'],
                provider_interval_end_ns=s['minute_end_ns'],exact_trade_ns=s['exact_last_price_trade_ns'],
                actual_observed_ns=s['actual_observed_ns'],bar_actual_observed_ns=s['bar_actual_observed_ns'],
                raw_bar_lineage=s['bar']['lineage'],trade_source_hash=s['source_hash'])
        s['last_observation']=deepcopy(last)
        s['price_age_upper_bound_ns']=s['minute_end_ns']-last['provider_bar_start_ns'] if last else None
        s['price_knowledge']='unknown_current_price' if s['coverage']=='unresolved' or last is None else 'observed_price' if s['bar'] else 'stale_valid_last_observation'
        s['state_hash']=digest({k:v for k,v in s.items() if k!='state_hash'});output.append(s)
    return output
