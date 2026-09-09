"""Synthetic raw-receipt intake and provider-neutral execution bundle contract.

Real provider intake is deliberately NOT inferred from these invented fixtures.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .events import ContractError, digest, timestamp_ns
from .features import MINUTE, Session, Universe, feature_snapshot
from .ledger import Ledger, Recorder
from .market_state import MarketState
from .research_fixture import populate, history, KEY

DAY='2025-01-08'
OPEN=timestamp_ns(DAY+'T09:30:00-05:00')
SESSION=Session(DAY,OPEN,OPEN+390*MINUTE,digest('synthetic-calendar-v1'))


def fixture_material(scenario):
    """Generate prescribed DATA scenarios, never strategy parameters or results."""
    with TemporaryDirectory(prefix='lab-performance-fixture-') as temp:
        with Ledger(temp,min_free_bytes=0) as ledger:
            journal=populate(ledger,session=SESSION,scenario=scenario)
            receipts=[dict(metadata=json.loads(r['metadata_json']),raw=r['raw'].decode())
                      for r in ledger.connection.execute('SELECT * FROM receipts ORDER BY id')]
    u=Universe();u.observe([dict(instrument_id=KEY[3],symbol='AAPL',active=True,asset_type='common_stock')],
        known_ns=OPEN-MINUTE,effective_ns=OPEN-MINUTE,source_hash=digest('synthetic-universe'))
    return dict(version='performance-evidence-fixture-v1',origin='synthetic-fixture',scenario=scenario,
        receipts=receipts,journal=journal,session=asdict(SESSION),keys=[list(KEY)],
        history=history(session=SESSION),universe=u.snapshots)


def write_evidence(directory, *, scenario='positive'):
    from .research_check import save
    root=Path(directory);root.mkdir(parents=True,exist_ok=False,mode=0o700)
    save(root/'performance-evidence.json',fixture_material(scenario))
    return root


def normalize_receipts(raw):
    # Receipts have already been persisted by intake. Rebuild the original
    # journal with original clocks; never use current time as availability.
    with TemporaryDirectory(prefix='lab-performance-normalize-') as temp:
        with Ledger(temp,min_free_bytes=0) as ledger:
            recorder=Recorder(ledger,origin='fixture')
            for receipt in raw['receipts']:
                identifier=ledger.receipt(receipt['raw'].encode(),receipt['metadata'])
                recorder.clock=lambda:receipt['metadata']['received_ns']
                recorder.process(identifier)
            if ledger.verify()!=raw['journal']:raise ContractError('performance_raw_journal_mismatch')
            events=list(ledger.replay(through_seq=ledger.watermark()))
    return dict(version='performance-bundle-v1',session=raw['session'],keys=raw['keys'],
        history=raw['history'],universe=raw['universe'],events=events,journal=raw['journal'])


def validate_fixture(directory):
    path=Path(directory)/'performance-evidence.json';raw_bytes=path.read_bytes();raw=json.loads(raw_bytes)
    if raw.get('scenario') not in {'positive','losing','no_trade'}:
        raise ContractError('performance_fixture_scenario_invalid')
    if raw!=fixture_material(raw['scenario']):
        raise ContractError('performance_fixture_scope_or_receipts_corrupt')
    bundle=normalize_receipts(raw)
    # Admission exercises only point-in-time input readiness, no strategy/P&L.
    market=MarketState()
    for event in bundle['events']:
        if event['received_ns']>OPEN+5*MINUTE:break
        market.apply(event)
    snapshot=feature_snapshot(market,KEY,SESSION,raw['history'],as_of_ns=OPEN+5*MINUTE,research=True)
    if snapshot['missing']:raise ContractError('performance_fixture_input_readiness_failed')
    from .certification_inputs import cell, protocol
    cells=[cell('AAPL',DAY,'fixture:AAPL')]
    return dict(origin='synthetic-fixture',performance_input_version='performance-bundle-v1',
        raw_receipt_hashes=[hashlib.sha256(raw_bytes).hexdigest()],manifest_hash=digest(raw),
        normalized_hash=digest([bundle]),normalized=[bundle],raw_evidence=raw,
        identities={'AAPL':'fixture:AAPL'},correction_state='synthetic_original_only',
        selection=dict(eligible=True,cells=cells),cells=cells,
        admission=dict(admitted=True,tier='synthetic-only',protocol_hash=protocol()['protocol_hash'],
            validator='performance-fixture-input-v1',readiness_feature_hash=snapshot['feature_hash']),
        limitations=['Invented AAPL observations; not historical evidence or profitability validation',
                     'Synthetic reference/conditions are fixture-only; no original arrival or revision coverage claim'],
        source_paths=['performance-evidence.json'])
