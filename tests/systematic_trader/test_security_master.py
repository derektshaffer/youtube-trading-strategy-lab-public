from copy import deepcopy
import json
import pytest
from systematic_trader.security_master import ingest,read,resolve,DatabentoReferenceJSON,IssuerInlineXBRL
from systematic_trader.events import ContractError,timestamp_ns


def native(**changes):
    r=dict(listing_id='L-1',security_id='S-1',issuer_id='I-1',symbol='QUERY',nasdaq_symbol='OLD',ts_effective='2025-01-01T00:00:00Z',ts_record='2025-01-02T00:00:00Z',ts_created='2025-01-03T00:00:00Z',listing_status='L',exchange='USNASD',security_type='EQS')
    r.update(changes);return r


def load(tmp,rows,mode='point_in_time'):
    return ingest(json.dumps(rows).encode(),adapter_version=DatabentoReferenceJSON.version,context={'history_mode':mode},directory=tmp)['claims']


def test_creation_clock_prevents_backfill_lookahead(tmp_path):
    rows=load(tmp_path/'a',[native()]);t=timestamp_ns
    assert resolve(rows,symbol='OLD',effective_ns=t('2025-01-02T00:00:00Z'),knowledge_ns=t('2025-01-02T12:00:00Z')) is None
    assert resolve(rows,symbol='OLD',effective_ns=t('2025-01-02T00:00:00Z'),knowledge_ns=t('2025-01-04T00:00:00Z'))['security_id']=='databento:S-1'
    assert read(tmp_path/'a')['claims']==rows


def test_new_symbol_does_not_retain_old_mapping_after_effective_change(tmp_path):
    rows=load(tmp_path/'a',[native(),native(nasdaq_symbol='NEW',ts_effective='2025-01-05T00:00:00Z',ts_record='2025-01-05T00:00:00Z')]);t=timestamp_ns('2025-01-06T00:00:00Z')
    assert resolve(rows,symbol='OLD',effective_ns=t,knowledge_ns=t) is None
    assert resolve(rows,symbol='NEW',effective_ns=t,knowledge_ns=t)['tradability']=='UNKNOWN'


@pytest.mark.parametrize('changes,mode',[({'ts_created':None},'point_in_time'),({},'latest_snapshot')])
def test_unknown_knowledge_and_current_snapshot_do_not_join(tmp_path,changes,mode):
    rows=load(tmp_path/'a',[native(**changes)],mode);t=timestamp_ns('2025-01-06T00:00:00Z')
    assert resolve(rows,symbol='OLD',effective_ns=t,knowledge_ns=t) is None


def test_raw_preserved_on_parse_failure_and_no_overwrite(tmp_path):
    p=tmp_path/'a'
    with pytest.raises(ContractError):ingest(b'bad json',adapter_version=DatabentoReferenceJSON.version,context={},directory=p)
    assert (p/'source.raw').read_bytes()==b'bad json' and (p/'rejection.json').exists()
    with pytest.raises(FileExistsError):load(p,[native()])


def test_tampering_and_ambiguous_revision_rejected(tmp_path):
    rows=load(tmp_path/'a',[native(),native(security_id='S-2')]);t=timestamp_ns('2025-01-06T00:00:00Z')
    with pytest.raises(ContractError):resolve(rows,symbol='OLD',effective_ns=t,knowledge_ns=t)
    (tmp_path/'a/source.raw').write_bytes(b'changed')
    with pytest.raises(ContractError):read(tmp_path/'a')


def test_issuer_cik_is_not_security_identity(tmp_path):
    raw=b'<html><ix:nonNumeric name="dei:TradingSymbol">ASPI</ix:nonNumeric><ix:nonNumeric name="dei:EntityCentralIndexKey">1921865</ix:nonNumeric></html>'
    rows=ingest(raw,adapter_version=IssuerInlineXBRL.version,context={'source_id':'fixture-filing'},directory=tmp_path/'a')['claims']
    assert rows[0]['issuer_id']=='sec-cik:0001921865' and rows[0]['security_id'] is None
    assert rows[0]['known_ns'] is None and rows[0]['effective_ns'] is None


def test_future_acquisition_clock_is_rejected(tmp_path):
    with pytest.raises(ContractError):ingest(json.dumps([native()]).encode(),adapter_version=DatabentoReferenceJSON.version,context={'history_mode':'point_in_time'},observed_ns=1,directory=tmp_path/'a')


def test_inline_void_elements_preserve_security_fact(tmp_path):
    raw=b'<ix:nonNumeric name="dei:TradingSymbol">A<br>SPI</ix:nonNumeric><ix:nonNumeric name="dei:EntityCentralIndexKey">1921865</ix:nonNumeric>'
    assert IssuerInlineXBRL().decode(raw,{'source_id':'fixture'})[0]['symbol']=='ASPI'


def test_manual_document_cannot_invent_security_identifier(tmp_path):
    from systematic_trader.security_master import ReviewedDocument
    context=dict(source_id='fixture',facts=dict(symbol='ASPI',security_class='Common Stock',exchange='Nasdaq',identifiers={'isin':'invented'}),supporting_spans=['ASPI Common Stock Nasdaq'])
    with pytest.raises(ContractError):ingest(b'ASPI Common Stock Nasdaq',adapter_version=ReviewedDocument.version,context=context,directory=tmp_path/'a')


def test_rehashed_normalization_cannot_disagree_with_source(tmp_path):
    from systematic_trader.events import digest
    p=tmp_path/'a';load(p,[native()]);b=json.loads((p/'claims.json').read_text())
    row=b['claims'][0];row['symbol']='FAKE';row['claim_hash']=digest({k:v for k,v in row.items() if k!='claim_hash'})
    b['bundle_hash']=digest({k:v for k,v in b.items() if k!='bundle_hash'});(p/'claims.json').write_text(json.dumps(b))
    with pytest.raises(ContractError,match='normalization_replay'):read(p)


def test_query_echo_is_not_historical_symbol(tmp_path):
    rows=load(tmp_path/'a',[native(symbol='NEW')]);t=timestamp_ns('2025-01-06T00:00:00Z')
    assert resolve(rows,symbol='NEW',effective_ns=t,knowledge_ns=t) is None
    assert resolve(rows,symbol='OLD',effective_ns=t,knowledge_ns=t)['raw_fields']['symbol']=='NEW'


def test_ambiguous_nyse_symbol_requires_identifier_join(tmp_path):
    rows=load(tmp_path/'a',[native(nasdaq_symbol='BRKB',exchange='USNYSE')]);t=timestamp_ns('2025-01-06T00:00:00Z')
    assert resolve(rows,symbol='BRKB',effective_ns=t,knowledge_ns=t) is None
    assert rows[0]['security_id']=='databento:S-1'
