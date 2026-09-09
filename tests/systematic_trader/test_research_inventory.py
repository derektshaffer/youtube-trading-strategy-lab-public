"""Research ingestion contracts only: no price engine, provider or model calls."""
from copy import deepcopy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from systematic_trader import research_inventory as ri
from systematic_trader.evidence_store import read_records
from systematic_trader.events import ContractError


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('network forbidden'))


@pytest.fixture
def intake(tmp_path, monkeypatch):
    from pypdf import PdfWriter
    writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
    data = io.BytesIO(); writer.write(data)
    source = tmp_path/'report.pdf'; source.write_bytes(data.getvalue())
    text = 'Fixture claim only. No strategy execution or evidence of market performance.'
    monkeypatch.setattr('pypdf.PdfReader', lambda *_: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda:text)]))
    catalog = deepcopy(json.loads(ri.CATALOG.read_text()))
    # Same real contracts, small synthetic provenance. No local user PDFs in CI.
    catalog['reports'] = [dict(id='R1',sha256=ri.sha(data.getvalue()),page_count=1,title='Fixture')]
    def rewrite(value):
        if isinstance(value,dict):
            if 'report_id' in value and 'page' in value:
                value.update(report_id='R1',page=1,source_sha256=ri.sha(data.getvalue()),page_text_sha256=ri.sha(text.encode()),literal_excerpt='Fixture claim only.')
            for child in value.values(): rewrite(child)
        elif isinstance(value,list):
            for child in value:rewrite(child)
    rewrite(catalog)
    path = tmp_path/'catalog.json'; path.write_text(json.dumps(catalog))
    return tmp_path/'inventory', {'R1':source}, path, catalog, {'R1':[dict(page=1,text=text)]}


def test_raw_first_idempotent_restart_and_deterministic_read(intake):
    root,paths,path,_,_=intake
    first = ri.ingest(root,paths,path)
    assert first == ri.ingest(root,paths,path) == ri.summary(root)
    rows=read_records(root,ri.DOMAIN)
    assert [r['kind'] for r in rows]==['domain','raw_report','page_extraction','research_catalog']
    assert first['orders_enabled'] is False and first['authority']=='inventory_only'
    assert first['independently_verified_report_claims']==0


def test_parse_crash_retains_raw_then_recovers(intake,monkeypatch):
    root,paths,path,_,_=intake
    import pypdf
    reader=pypdf.PdfReader
    monkeypatch.setattr(pypdf,'PdfReader',lambda *_: (_ for _ in ()).throw(ValueError('parser failed')))
    with pytest.raises(ValueError):ri.ingest(root,paths,path)
    assert [r['kind'] for r in read_records(root,ri.DOMAIN)]==['domain','raw_report']
    assert ri.summary(root)['status']=='not_ingested'
    monkeypatch.setattr(pypdf,'PdfReader',reader)
    assert ri.ingest(root,paths,path)['journal_records']==4


@pytest.mark.parametrize('suffix',['.pdf','.json'])
def test_modified_preserved_artifact_fails_closed(intake,suffix):
    root,paths,path,_,_=intake
    ri.ingest(root,paths,path)
    target=next((root/'artifacts').glob('*'+suffix));target.write_bytes(b'changed')
    with pytest.raises((ContractError,ValueError)):ri.summary(root)


def test_truncated_external_head_fails_closed(intake):
    root,paths,path,_,_=intake;ri.ingest(root,paths,path)
    (root/'head.json').write_text('{}')
    with pytest.raises(ContractError):ri.summary(root)


def test_wrong_pdf_not_recorded_as_expected_source(intake):
    root,paths,path,_,_=intake
    paths['R1'].write_bytes(b'wrong PDF')
    with pytest.raises(ContractError,match='source_mismatch'):ri.ingest(root,paths,path)
    assert len(read_records(root,ri.DOMAIN))==1


def test_untrusted_catalog_stays_raw_evidence_not_registered(intake):
    root,paths,path,catalog,_=intake
    catalog['hypotheses'][0]['sources'][0]['literal_excerpt']='fabricated quotation'
    path.write_text(json.dumps(catalog))
    with pytest.raises(ContractError,match='claim_not_found'):ri.ingest(root,paths,path)
    assert ri.summary(root)['status']=='not_ingested'
    assert [r['kind'] for r in read_records(root,ri.DOMAIN)]==['domain','raw_report','page_extraction']


@pytest.mark.parametrize('change,error',[
    (lambda c:c.update(authority='certified'),'authority'),
    (lambda c:c['hypotheses'][0].update(uses_first_leg=True),'post_move'),
    (lambda c:c['hypotheses'][0].update(independently_verified=True),'escalation'),
    (lambda c:c['hypotheses'][0].update(research_status='validated'),'escalation'),
    (lambda c:c['hypotheses'][0].update(testability='probably ready'),'contract'),
    (lambda c:c['hypotheses'].append(deepcopy(c['hypotheses'][0])),'duplicate'),
    (lambda c:c['candidate_queue'][0].update(execution_authorized=True),'authority'),
    (lambda c:c['candidate_queue'][0].update(ranking_basis='historical P&L'),'ranking'),
    (lambda c:c['candidate_queue'][0].update(rank=99),'queue'),
    (lambda c:c['candidate_queue'].clear(),'queue'),
    (lambda c:c['hypotheses'][0]['sources'][0].update(page=999),'page'),
    (lambda c:c['hypotheses'][0]['sources'][0].update(source_sha256='bad'),'hash'),
])
def test_adversarial_catalog_rejected(intake,change,error):
    _,_,_,catalog,pages=intake;change(catalog)
    with pytest.raises(ContractError,match=error):ri.validate_catalog(catalog,pages)


def test_missing_inventory_read_does_not_create_files(tmp_path):
    root=tmp_path/'absent';assert ri.summary(root)['status']=='not_ingested'
    assert not root.exists()


def test_curated_inventory_scope_reuses_twelve_ids_without_evidence_inflation():
    c=json.loads(ri.CATALOG.read_text())
    assert len(c['hypotheses'])==24 and len(c['candidate_queue'])==8
    assert len(c['method_controls'])==21 and len(c['adaptive_capabilities'])==12
    reused=[h for h in c['hypotheses'] if h.get('existing_library_id')]
    assert len(reused)==12 and all(h['id']==h['existing_library_id'] for h in reused)
    assert c['deduplication'][0]['identical_pages']==45
    assert {h['prediction_class'] for h in c['hypotheses']}==ri.CLASSES
    assert sum(h['testability']=='TESTABLE NOW WITH OWNED DATA' for h in c['hypotheses'])==0
    assert [r['id'] for r in c['reports'] if r['classification']=='background_scoping_only']==['R5']
    assert all(x['independent_confirmations']==0 for x in c['method_controls'])
    assert all(not q['current_data_sufficient'] for q in c['candidate_queue'])
    assert all(not q['execution_authorized'] for q in c['candidate_queue'])


def test_catalog_revision_keeps_previous_version(intake):
    root,paths,path,catalog,_=intake;first=ri.ingest(root,paths,path)
    catalog['hypotheses'][0]['testing_cost']='Updated planning estimate; no purchase'
    path.write_text(json.dumps(catalog));second=ri.ingest(root,paths,path)
    assert second['journal_records']==first['journal_records']+1
    assert second['catalog_sha256']!=first['catalog_sha256']
    assert len([r for r in read_records(root,ri.DOMAIN) if r['kind']=='raw_report'])==1


def test_existing_pdf_artifact_is_not_silently_repaired(intake):
    root,paths,path,_,_=intake;ri.ingest(root,paths,path)
    next((root/'artifacts').glob('*.pdf')).write_bytes(b'bad')
    with pytest.raises(ContractError,match='integrity'):ri.ingest(root,paths,path)


def test_same_pdf_under_two_report_aliases_does_not_duplicate_receipt(intake):
    root,paths,path,catalog,_=intake
    catalog['reports'].append(dict(catalog['reports'][0],id='R2'))
    paths['R2']=paths['R1'];path.write_text(json.dumps(catalog))
    result=ri.ingest(root,paths,path)
    assert result['journal_records']==4
    assert result['independently_verified_report_claims']==0
    assert result==ri.ingest(root,paths,path)


def test_claimed_code_reuse_points_resolve_in_canonical_source():
    import ast
    import re
    root=Path(__file__).resolve().parents[2]
    points=set(re.findall(r'([\w/]+\.py):([\w_]+)',ri.CATALOG.read_text()))
    assert len(points)>=8
    for path,name in points:
        tree=ast.parse((root/path).read_text())
        defined={getattr(node,'name',None) for node in ast.walk(tree)}
        defined.update(node.id for node in ast.walk(tree) if isinstance(node,ast.Name))
        assert name in defined, (path,name)
