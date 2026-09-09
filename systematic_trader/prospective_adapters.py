"""Pure normalization AFTER raw persistence. Source semantics are explicit."""
import csv
from io import StringIO
import json
from .events import ContractError, digest

CATEGORIES=('identity','corporate_actions','round_lot','trading_status','luld','market','source_coverage')
SOURCES={
 'alpaca_iex':dict(version='alpaca-iex-prospective-v1',complete_coverage=False,semantics='IEX_only_not_consolidated_no_status_LULD_or_sequence_guarantee'),
 'alpaca_calendar':dict(version='alpaca-calendar-observed-v1',complete_coverage=False,semantics='scheduled_calendar_not_actual_market_status'),
 'fixture':dict(version='prospective-fixture-v1',complete_coverage=True,semantics='explicit_fixture_interval'),
 'nasdaq_directory':dict(version='nasdaq-directory-observed-v1',complete_coverage=False,semantics='current_snapshot_not_identity_crosswalk'),
 'alpaca_assets':dict(version='alpaca-assets-observed-v1',complete_coverage=False,semantics='broker_asset_snapshot_not_exchange_status'),
 'alpaca_actions':dict(version='alpaca-actions-observed-v1',complete_coverage=False,semantics='publication_completeness_not_guaranteed'),
 'alpaca_sip':dict(version='alpaca-sip-prospective-v1',complete_coverage=False,semantics='entering_state_and_stream_completeness_unverified'),
 'tradier':dict(version='tradier-prospective-v1',complete_coverage=False,semantics='amendment_links_and_sequence_scope_unverified'),
}


def normalize(source,raw):
    if source not in SOURCES:raise ContractError('prospective_source_not_configured')
    if source=='fixture':
        data=json.loads(raw)
        if set(data)!={'version','facts'} or data['version']!='prospective-fixture-v1' or not isinstance(data['facts'],list):raise ContractError('prospective_fixture_schema')
        return [validate_fact(f) for f in data['facts']]
    if source=='nasdaq_directory':
        rows=list(csv.DictReader(StringIO(raw.decode()),delimiter='|'));facts=[]
        for row in rows:
            symbol=row.get('Symbol') or row.get('ACT Symbol') or row.get('NASDAQ Symbol')
            if not symbol or symbol.startswith('File Creation Time'):continue
            # No stable identifier is supplied by this file. NEVER join it into
            # an asset/listing merely because its ticker matches another feed.
            facts.append(dict(kind='unbound_reference',symbol=symbol,security_id=None,
                entity_id=digest(row),operation='original',effective_ns=None,effective_to_ns=None,
                values=dict(source_row=row,round_lot_observed=row.get('Round Lot Size'),identity_crosswalk='UNRESOLVED_SOURCE_GAP')))
        if not facts:raise ContractError('directory_rows_unavailable')
        return facts
    if source=='alpaca_calendar':
        return [dict(kind='calendar_observation',symbol=None,security_id=None,entity_id=r['date'],operation='original',effective_ns=None,effective_to_ns=None,values=r) for r in json.loads(raw)]
    if source=='alpaca_assets':
        rows=json.loads(raw)
        if not isinstance(rows,list):raise ContractError('asset_snapshot_shape')
        return [dict(kind='identity_observation',symbol=r['symbol'],security_id='alpaca-asset:'+r['id'],entity_id=r['id'],
            operation='original',effective_ns=None,effective_to_ns=None,values={k:r.get(k) for k in ('id','symbol','class','exchange','name','status','tradable')})
            for r in rows if r.get('id') and r.get('symbol')]
    if source=='alpaca_actions':
        payload=json.loads(raw);actions=payload.get('corporate_actions')
        if not isinstance(actions,dict):raise ContractError('actions_snapshot_shape')
        return [dict(kind='action_observation',symbol=r.get('symbol') or r.get('initiating_symbol'),security_id=None,
            entity_id=str(r.get('id') or digest(r)),operation='original',effective_ns=None,effective_to_ns=None,
            values=dict(action_type=kind,source_row=r,identity_crosswalk='UNRESOLVED_SOURCE_GAP')) for kind,rows in actions.items() for r in rows]
    from .providers import adapter_for
    adapter=adapter_for('alpaca' if source in {'alpaca_sip','alpaca_iex'} else 'tradier',
                        'iex' if source=='alpaca_iex' else 'sip' if source=='alpaca_sip' else 'tradier_consolidated')
    facts=[]
    for message in adapter.decode(raw):
        if source=='alpaca_iex' and message.get('T') in {'success','subscription','error'}:
            facts.append(dict(kind='source_control_observation',symbol=None,security_id=None,
                entity_id=digest(message),operation='original',effective_ns=None,effective_to_ns=None,
                values=dict(message=message,feed='iex',not_market_evidence=True)))
            continue
        observation=adapter.normalize(message)
        facts.append(dict(kind='market_observation',symbol=observation.symbol,security_id=None,
            entity_id=digest([observation.symbol,observation.source_id or message]),operation='original',effective_ns=observation.source_ns,effective_to_ns=None,
            values=dict(event_type=observation.kind,payload=observation.payload,sequence=observation.sequence,
                flags=list(observation.flags),identity_crosswalk='UNRESOLVED_SOURCE_GAP')))
    return facts


def validate_fact(f):
    required={'kind','symbol','security_id','entity_id','operation','effective_ns','effective_to_ns','values'}
    if not isinstance(f,dict) or set(f)!=required:raise ContractError('prospective_fact_fields')
    if f['kind'] not in {*CATEGORIES,'trade','quote','coverage','gap'} or f['operation'] not in {'original','revise','void'}:raise ContractError('prospective_fact_semantics')
    if not isinstance(f['security_id'],str) or not f['security_id'].startswith('fixture:') or not f['entity_id']:raise ContractError('fixture_identity_required')
    if type(f['effective_ns']) is not int or not isinstance(f['values'],dict):raise ContractError('prospective_effective_time_required')
    if f['effective_to_ns'] is not None and (type(f['effective_to_ns']) is not int or f['effective_to_ns']<=f['effective_ns']):raise ContractError('prospective_effective_interval')
    return f
