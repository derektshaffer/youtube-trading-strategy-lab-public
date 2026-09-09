"""Offline, non-executable research map. No strategy, model or queue authority.

PDF bytes are committed before extraction. A separate curated catalog references
literal page spans; report assertions never become independently verified facts.
The existing evidence journal provides append-only history and fail-closed replay.
"""
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import re

from .events import ContractError, digest
from .evidence_store import EvidenceStore, read_records

DOMAIN = 'research-inventory-v1'
CATALOG = Path(__file__).with_name('research') / 'reconciled-v1.json'
TESTABILITY = (
    'TESTABLE NOW WITH OWNED DATA', 'TESTABLE PRELIMINARILY ONLY',
    'NEEDS PROSPECTIVE DATA', 'NEEDS CHEAP/EXISTING PUBLIC DATA',
    'NEEDS PREMIUM DATA', 'NOT CURRENTLY PRACTICAL',
)
CLASSES = {'initial_move', 'continuation', 'failure_reversal', 'execution_quality', 'regime'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalized(text):
    return ' '.join(text.split())


def _blob(root, data, suffix):
    key = sha(data)
    target = root / 'artifacts' / (key + suffix)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists():
        if sha(target.read_bytes()) != key:
            raise ContractError('research_artifact_integrity_failure')
        return key
    # A crash leaves an unreferenced temp file, never a partially committed blob.
    import tempfile
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.pending-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return key


def _read_blob(root, key, suffix):
    if not re.fullmatch(r'[0-9a-f]{64}', key):
        raise ContractError('research_artifact_key_invalid')
    data = (root / 'artifacts' / (key + suffix)).read_bytes()
    if sha(data) != key:
        raise ContractError('research_artifact_integrity_failure')
    return data


def validate_catalog(catalog, pages):
    if catalog.get('schema_version') != 1 or catalog.get('authority') != 'inventory_only':
        raise ContractError('research_catalog_authority_invalid')
    reports = {r['id']: r for r in catalog['reports']}
    hypotheses = {h['id']: h for h in catalog['hypotheses']}
    if len(reports) != len(catalog['reports']) or len(hypotheses) != len(catalog['hypotheses']):
        raise ContractError('research_duplicate_id')
    required = {'id','name','sources','research_claim','evidence_strength','target_universe',
                'target_horizon','timing_boundary','required_raw_data','required_derived_features',
                'expected_interactions','confounders','known_failure_modes','prediction_class',
                'testability','missing_data','implementation_complexity','testing_cost',
                'leakage_risk','family_id','research_status','uses_first_leg'}
    for h in hypotheses.values():
        if not required <= h.keys() or not h['sources'] or h['testability'] not in TESTABILITY or h['prediction_class'] not in CLASSES:
            raise ContractError('research_hypothesis_contract_invalid')
        if h['uses_first_leg'] and h['prediction_class'] == 'initial_move':
            raise ContractError('research_post_move_precursor_forbidden')
        if h['research_status'] != 'unrun_unvalidated' or h.get('independently_verified') is not False:
            raise ContractError('research_unverified_claim_escalation')
    queue = catalog['candidate_queue']
    if not 5 <= len(queue) <= 10 or [q['rank'] for q in queue] != list(range(1, len(queue)+1)) or len({q['hypothesis_id'] for q in queue}) != len(queue):
        raise ContractError('research_queue_contract_invalid')
    for q in queue:
        if q['hypothesis_id'] not in hypotheses or q.get('execution_authorized') is not False:
            raise ContractError('research_queue_authority_invalid')
        if q['ranking_basis'] != 'ex_ante_evidence_data_chronology_cost_not_performance':
            raise ContractError('research_outcome_ranking_forbidden')
    # Validate every nested literal citation, including controls and disagreements.
    def walk(value):
        if isinstance(value, dict):
            if 'report_id' in value and 'page' in value:
                rid, page = value['report_id'], value['page']
                if rid not in reports or rid not in pages or not 1 <= page <= len(pages[rid]):
                    raise ContractError('research_source_page_invalid')
                original = pages[rid][page-1]['text']
                if value['source_sha256'] != reports[rid]['sha256'] or value['page_text_sha256'] != sha(original.encode()):
                    raise ContractError('research_source_hash_mismatch')
                if not value.get('literal_excerpt') or normalized(value['literal_excerpt']) not in normalized(original):
                    raise ContractError('research_source_claim_not_found')
            for item in value.values(): walk(item)
        elif isinstance(value, list):
            for item in value: walk(item)
    walk(catalog)
    return catalog


def ingest(directory, source_paths, catalog_path=CATALOG):
    """Explicit offline intake. Idempotent by raw bytes and catalog content."""
    catalog = json.loads(Path(catalog_path).read_text())
    store = EvidenceStore(directory, DOMAIN)
    pages = {}
    with store.locked() as journal:
        records = store.records(journal)
        seen = {(r['kind'], r['key']) for r in records}
        for report in catalog['reports']:
            rid = report['id']
            raw = Path(source_paths[rid]).read_bytes()
            if sha(raw) != report['sha256']:
                raise ContractError('research_pdf_source_mismatch')
            key = _blob(store.root, raw, '.pdf')
            if ('raw_report', key) not in seen:
                store.append(journal, 'raw_report', key, dict(sha256=key, filename=Path(source_paths[rid]).name, orders_enabled=False))
                seen.add(('raw_report', key))
            # The receipt above is durable even if parsing or curation fails.
            from pypdf import PdfReader
            import pypdf
            extracted = [{'page': i+1, 'text': page.extract_text() or ''} for i, page in enumerate(PdfReader(io.BytesIO(raw)).pages)]
            if len(extracted) != report['page_count']:
                raise ContractError('research_page_count_mismatch')
            data = json.dumps(extracted, ensure_ascii=False, sort_keys=True).encode()
            page_key = _blob(store.root, data, '.json')
            extraction_key = digest(dict(raw=key, pages=page_key))
            if ('page_extraction', extraction_key) not in seen:
                store.append(journal, 'page_extraction', extraction_key, dict(raw_sha256=key, pages_sha256=page_key, extractor='pypdf', extractor_version=pypdf.__version__))
                seen.add(('page_extraction', extraction_key))
            pages[rid] = extracted
        validate_catalog(catalog, pages)
        data = json.dumps(catalog, ensure_ascii=False, sort_keys=True).encode()
        key = _blob(store.root, data, '.json')
        if not any(r['kind']=='research_catalog' and r['key']==key for r in records):
            store.append(journal, 'research_catalog', key, dict(catalog_sha256=key, authority='inventory_only', orders_enabled=False))
    return summary(directory)


def summary(directory):
    """Read-only verified projection. No import, scheduling, or model side effects."""
    root = Path(directory).resolve()
    records = read_records(root, DOMAIN)
    catalogs = [r for r in records if r['kind']=='research_catalog']
    if not catalogs:
        return dict(status='not_ingested', authority='inventory_only', orders_enabled=False)
    record = catalogs[-1]
    catalog = json.loads(_read_blob(root, record['body']['catalog_sha256'], '.json'))
    pages = {}
    for report in catalog['reports']:
        key = report['sha256']
        raw_records = [r for r in records if r['kind']=='raw_report' and r['key']==key and r['seq']<record['seq']]
        extractions = [r for r in records if r['kind']=='page_extraction' and r['body']['raw_sha256']==key and r['seq']<record['seq']]
        if not raw_records or not extractions or extractions[-1]['seq'] <= raw_records[0]['seq']:
            raise ContractError('research_raw_before_normalization_missing')
        _read_blob(root, key, '.pdf')
        pages[report['id']] = json.loads(_read_blob(root, extractions[-1]['body']['pages_sha256'], '.json'))
    validate_catalog(catalog, pages)
    counts = Counter(h['testability'] for h in catalog['hypotheses'])
    return dict(status='verified_inventory', authority='inventory_only', orders_enabled=False,
                catalog_sha256=record['key'], journal_head=records[-1]['hash'],
                journal_records=len(records), counts={k:counts[k] for k in TESTABILITY},
                independently_verified_report_claims=0, **{k:catalog[k] for k in (
                    'reports','hypotheses','method_controls','adaptive_capabilities','gap_matrix',
                    'candidate_queue','deferred_queue','deduplication','contradictions')})


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Offline research inventory; never runs candidates')
    parser.add_argument('operation', choices=['ingest','summary'])
    parser.add_argument('--directory', required=True)
    parser.add_argument('--sources', help='JSON object mapping report IDs to local PDF paths')
    args = parser.parse_args()
    result = ingest(args.directory, json.loads(Path(args.sources).read_text())) if args.operation=='ingest' else summary(args.directory)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
