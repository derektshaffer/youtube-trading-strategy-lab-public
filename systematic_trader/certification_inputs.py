"""Validation-owned inputs. No admission flags, models or user callbacks accepted.

The historical reader recognizes the completed, rejected V4 evidence only. New
provider evidence requires the separately reviewed intake/validator milestone.
Fixture admission is deliberately a different, non-historical trust domain.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from .events import ContractError, canonical_json, digest, timestamp_ns
from .bounded_evidence import supported_cells, quote_rule
from .research_engine import BaselinePolicy, ExecutionPolicy, RiskPolicy, NoTrade
from .research_split import ResearchSplit

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL_PATH = ROOT/'systematic_trader/protocols/bounded-admission-v4.json'
V4_RESULT = '6344002642a9bd76ad417008948392d73ee85e472c0ee52ad872e728eb607ea4'
V4_SELECTION = '0e06f85efb261db30cd45fafa1a5906efd4a375467900e02784d7170034f26f1'
VERSION = 'trusted-handoff-v1'
HOLDOUT = dict(march='unused_for_validation', april='incidental_display_recorded_not_wholly_unseen',
               may_june='locked', execution_designation='development_only')


def freeze(value):
    return json.loads(canonical_json(value))


def now():
    return datetime.now(timezone.utc).isoformat()


def load(path, key=None):
    raw=Path(path).read_bytes(); value=json.loads(raw)
    if key and value.get(key) != digest({k:v for k,v in value.items() if k!=key}):
        raise ContractError('certification_input_hash_invalid')
    return value, hashlib.sha256(raw).hexdigest()


def protocol():
    p,_=load(PROTOCOL_PATH,'protocol_hash')
    if p['protocol_hash'] != '78f2dcc14733319150efecef87be964dc0d0c9b109cf78e6d9bed4a31865199f':
        raise ContractError('frozen_admission_protocol_changed')
    return p


def context():
    names=sorted(p.name for p in (ROOT/'systematic_trader').glob('*.py'))
    hashes={name:hashlib.sha256((ROOT/'systematic_trader'/name).read_bytes()).hexdigest() for name in names}
    policies=dict(strategy=asdict(BaselinePolicy()), no_trade=NoTrade.version,
                  execution=asdict(ExecutionPolicy()), risk=asdict(RiskPolicy()))
    return dict(version=VERSION, code_hash=digest(hashes), code_files=hashes,
        git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        runtime=dict(python=sys.version.split()[0],openssl=subprocess.check_output(['/usr/bin/openssl','version'],text=True).strip()),
        protocol_hash=protocol()['protocol_hash'], protocol_version='bounded-admission-v4',
        policy_hash=digest(policies), policies=policies, execution_assumptions_hash=digest(policies['execution']),
        feature_version_hash=hashes['features.py'], holdout=HOLDOUT,
        split=ResearchSplit().manifest(), issuer_version='local-validation-authority-v1')


def cell(symbol, day, identity):
    return dict(symbol=symbol, session=day, segment='regular', security_id=identity,
        start_ns=timestamp_ns(day+'T09:30:00-05:00'), end_ns=timestamp_ns(day+'T16:00:00-05:00'))


def _fixture(directory):
    """Derive predicates from synthetic observations, not supplied eligibility booleans."""
    if (Path(directory)/'performance-evidence.json').exists():
        from .performance_inputs import validate_fixture
        return validate_fixture(directory)
    raw,raw_hash=load(Path(directory)/'fixture-evidence.json')
    if set(raw) != {'origin','version','securities','observations','quotes','revision'} or raw['origin']!='synthetic-fixture' or raw['version']!='certificate-evidence-fixture-v1':
        raise ContractError('synthetic_evidence_schema_invalid')
    p=protocol(); expected=set(p['symbols'])
    if set(raw['securities']) != expected or raw['revision'] != 'original_fixture_no_corrections':
        raise ContractError('fixture_identity_or_revision_unrecognized')
    observations=raw['observations']; findings={}; identities={}; normalized=[]
    if not isinstance(observations,list):raise ContractError('fixture_observations_invalid')
    days=set(p['sessions']) | {d for ds in p['history_sessions'].values() for d in ds}
    keys=set()
    for row in observations:
        if set(row) != {'symbol','session','minute','price','volume','available_minute'} or row['symbol'] not in expected or row['session'] not in days:
            raise ContractError('fixture_observation_scope_invalid')
        if type(row['minute']) is not int or not 0<=row['minute']<5 or row['price']!='100' or row['volume']!=100 or row['available_minute']!=row['minute']+1:
            raise ContractError('fixture_observation_semantics_invalid')
        key=(row['symbol'],row['session'],row['minute'])
        if key in keys:raise ContractError('fixture_duplicate_observation')
        keys.add(key);normalized.append(row)
    normalized.sort(key=lambda r:(r['symbol'],r['session'],r['minute']))
    quote_checks={}
    for item in raw['quotes']:
        if (set(item)!={'symbol','session','quote','available_ns','decision_ns'}
                or item['symbol'] not in expected or item['session'] not in p['sessions']):
            raise ContractError('fixture_quote_scope_invalid')
        key=(item['symbol'],item['session'])
        if key in quote_checks:raise ContractError('fixture_duplicate_quote_cell')
        opened=timestamp_ns(item['session']+'T09:30:00-05:00')
        if not opened<=timestamp_ns(item['quote']['t'])<item['decision_ns']<opened+390*60*10**9:
            raise ContractError('fixture_quote_session_mismatch')
        quote_checks[key]=quote_rule(item['quote'],decision_ns=item['decision_ns'],available_ns=item['available_ns'])
    for symbol in p['symbols']:
        security=raw['securities'][symbol]
        expected_security=dict(identity='fixture:'+symbol,listing='fixture-listing',
            start='2024-12-10',end='2025-01-08',status='fixture-continuously-trading',
            actions='fixture-no-actions-complete',round_lot=100,conditions='fixture-native')
        if security!=expected_security:raise ContractError('fixture_reference_not_verified')
        identities[symbol]=security['identity']
        for day in p['sessions']:
            complete=all((symbol,d,m) in keys for d in [*p['history_sessions'][day],day] for m in range(5))
            findings[(symbol,day)]={k:True for k in p['required_predicates']}
            findings[(symbol,day)]['supported_original_orb_history']=complete
            q=quote_checks.get((symbol,day),{})
            findings[(symbol,day)]['causal_quotes']=q.get('quote_input_eligible') is True
            findings[(symbol,day)]['native_units_and_conditions']=q.get('native_structure_eligible') is True
    selected=supported_cells(p,findings)
    cells=[cell(r['symbol'],r['session'],identities[r['symbol']]) for r in selected['eligible']]
    return dict(origin='synthetic-fixture',raw_receipt_hashes=[raw_hash],manifest_hash=digest(raw),
        normalized_hash=digest(dict(bars=normalized,quotes=sorted(raw['quotes'],key=lambda q:(q['symbol'],q['session'])))),
        normalized=normalized,identities=identities,
        correction_state=raw['revision'],selection=selected,cells=cells,
        admission=dict(admitted=bool(cells),tier=1,protocol_hash=p['protocol_hash'],selection=selected),
        limitations=['Synthetic mechanics only; cannot certify historical or live data'],
        source_paths=['fixture-evidence.json'])


def _historical(directory):
    """Replay the pinned completed V4 decision, without reading price/holdout payloads."""
    root=Path(directory)
    result,rhash=load(root/'audit/result.json','result_hash')
    selection,shash=load(root/'audit/selection.json','result_hash')
    if result['result_hash']!=V4_RESULT or selection['result_hash']!=V4_SELECTION:
        raise ContractError('new_historical_evidence_requires_validated_intake')
    p=protocol()
    findings={(r['symbol'],r['session']):{k:k not in r['missing'] for k in p['required_predicates']} for r in selection['cells']}
    derived=supported_cells(p,findings)
    if derived!={k:v for k,v in selection.items() if k!='result_hash'}:
        raise ContractError('historical_selection_replay_mismatch')
    from .research_tiers import decide
    for entry in result['decisions']:
        old=entry['decision']; repeated=decide(old['request'],entry['evidence'],tier=1)
        if repeated!=old or repeated['admitted']:
            raise ContractError('historical_admission_replay_mismatch')
    # The known completed result is rejected. No new positive schema is inferred.
    if derived['eligible'] or result['dataset_admission']!='REJECTED' or result['admissible_symbol_sessions']!=0:
        raise ContractError('historical_rejection_state_changed')
    return dict(origin='historical-v4',raw_receipt_hashes=[rhash,shash],
        manifest_hash=digest(dict(result=result,selection=selection)),
        normalized_hash=digest(result['source_hashes']),normalized=[],
        identities={'ASPI':'cusip:00218A105','MDXH':'cusip:B5950S113','POWL':'cusip:739128106'},
        correction_state='final_export_original_revision_chain_unverified',selection=derived,cells=[],
        admission=dict(admitted=False,tier=1,protocol_hash=p['protocol_hash'],selection=derived,completed_result=result),
        limitations=['Identity identifiers are dated claims, not certified continuous mappings',
                    'Rejected completed V4 evidence; no execution certificate permitted'],
        source_paths=['audit/result.json','audit/selection.json'])


def validate_inputs(directory, domain):
    # Fixed dispatch owned by validation code, not supplied callbacks or registry entries.
    data=_fixture(directory) if domain=='synthetic-fixture' else _historical(directory) if domain=='historical-v4' else None
    if data is None:raise ContractError('untrusted_evidence_domain')
    for row in data['cells']:
        ResearchSplit().authorize(row['start_ns'],row['end_ns'],purpose='strategy_development')
    data['context']=context()
    data['data_reuse_key']=digest([data['origin'],data['normalized_hash'],data['identities'],data['context']['protocol_hash']])
    data['dataset_hash']=digest({k:v for k,v in data.items() if k!='normalized'})
    return freeze(data)


def experiment_template(certificate):
    """Frozen existing policy values, not an optimizer interface."""
    c=context()
    return dict(version='frozen-certified-experiment-v1',strategy_id=c['policies']['strategy']['version'],
        strategy_rules_hash=c['code_files']['research_engine.py'],parameters=c['policies']['strategy'],
        feature_definitions_hash=c['feature_version_hash'],entry_exit_rules_hash=c['code_files']['research_engine.py'],
        execution_assumptions=c['policies']['execution'],risk_assumptions=c['policies']['risk'],
        no_trade_policy=c['policies']['no_trade'],cells=certificate['body']['cells'],
        designation='development_only',random_seeds=[],model_versions={},
        policy_configuration_hash=c['policy_hash'],parameter_search_budget=0,
        holdout=c['holdout'],certificate_id=certificate['certificate_id'],
        consumer=certificate['body'].get('consumer','scope-probe-v1'),
        result_review_policy=dict(version='performance-review-routing-v1',review_all_completed=True,positive_net_pnl_requires_review=True),
        statistics=dict(version='session-statistics-v1',seed=0,draws=1000))
