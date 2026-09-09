import json
import pytest
from systematic_trader.bounded_evidence import cusip_valid,SEC13FExtract,action_review,quote_rule,supported_cells
from systematic_trader.events import ContractError,timestamp_ns

T=timestamp_ns('2025-01-02T14:30:00Z')
def quote(**changes):
    q=dict(t='2025-01-02T14:30:00Z',bp='10',ap='11',bs=10,**{'as':20},z='C',c=['R']);q.update(changes);return q

def test_cusip_checksum_and_issuer_identity_remain_distinct():
    assert all(cusip_valid(s) for s in ['00218A105','B5950S113','739128106'])
    assert not cusip_valid('739128107')
    url='https://www.sec.gov/files/investment/13flist2024q3.pdf'
    raw=json.dumps(url+'\n739128 10 6 * POWELL INDS INC COM\n').encode()
    row=SEC13FExtract().decode(raw,dict(source_url=url,cusip='739128106',issuer_label='POWELL INDS INC',class_label='COM',symbol='POWL'))[0]
    assert row['security_id']=='cusip:739128106' and row['effective_ns'] is None and row['known_ns'] is None
    assert row['identity_scope']=='dated_identifier_claim' and row['raw_fields']['symbol_is_investigation_label']

def test_document_row_cannot_be_mapped_to_another_class():
    url='https://www.sec.gov/files/investment/13flist2024q3.pdf'
    raw=json.dumps(url+'\n739128 10 6 * POWELL INDS INC COM\n').encode()
    with pytest.raises(ContractError):SEC13FExtract().decode(raw,dict(source_url=url,cusip='739128106',issuer_label='POWELL INDS INC',class_label='PREFERRED',symbol='POWL'))

def test_dividend_process_and_pay_dates_do_not_become_ex_date():
    row=dict(kind='cash_dividends',record=dict(ex_date='2024-11-20',process_date='2024-12-18',payable_date='2024-12-18'))
    r=action_review([row],start='2024-12-10',end='2025-01-08',query_clock='process_date')
    assert len(r['known_actions_outside_window'])==1 and not r['relevant_known_actions']
    assert not r['complete_effective_date_coverage']

def test_missing_effective_date_cannot_be_inferred_from_process_date():
    r=action_review([dict(kind='name_changes',record={'process_date':'2025-01-03'})],start='2024-12-10',end='2025-01-08',query_clock='process_date')
    assert len(r['unknown_effective_actions'])==1

def test_regular_quote_never_certifies_security_tradability_or_exact_shares():
    r=quote_rule(quote(),decision_ns=T+1,available_ns=T)
    assert r['quote_input_eligible'] and r['security_trading_status']=='UNKNOWN' and not r['fill_allowed']
    assert r['minimum_bid_shares']==10 and r['exact_share_conversion'] is None

@pytest.mark.parametrize('changes',[{'bp':'11'},{'bp':'12'},{'bp':'NaN'},{'c':['L']},{'c':['R','N']},{'bs':True},{'z':'A'}])
def test_ineligible_native_quotes_cannot_supply_input(changes):
    assert not quote_rule(quote(**changes),decision_ns=T+1,available_ns=T)['quote_input_eligible']

def test_equal_future_unknown_and_stale_arrivals_reject():
    for available in [None,T+1,T+2]:assert not quote_rule(quote(),decision_ns=T+1,available_ns=available)['quote_input_eligible']
    assert not quote_rule(quote(),decision_ns=T+3*10**9,available_ns=T)['quote_input_eligible']
    r=quote_rule(quote(),decision_ns=T,available_ns=None)
    assert r['native_structure_eligible'] and not r['quote_input_eligible']


def test_raw_identifier_claim_replay_cannot_create_listing_interval(tmp_path):
    from systematic_trader.bounded_evidence import register_security_adapter
    from systematic_trader.security_master import ingest,read,resolve
    register_security_adapter();url='https://www.sec.gov/files/investment/13flist2024q3.pdf'
    raw=json.dumps(url+'\n739128 10 6 * POWELL INDS INC COM\n').encode()
    context=dict(source_url=url,cusip='739128106',issuer_label='POWELL INDS INC',class_label='COM',symbol='POWL')
    r=ingest(raw,adapter_version=SEC13FExtract.version,context=context,directory=tmp_path/'claims')
    assert read(tmp_path/'claims')==r
    assert resolve(r['claims'],symbol='POWL',effective_ns=T,knowledge_ns=T) is None

def test_no_outcome_substitution_or_empty_subset_admission():
    protocol=dict(symbols=['ASPI'],sessions=['2025-01-02'],required_predicates=['identity','status'])
    r=supported_cells(protocol,{('ASPI','2025-01-02'):{'identity':True,'status':None}})
    assert not r['eligible'] and not r['empty_subset_is_admitted']
    with pytest.raises(ContractError):supported_cells(protocol,{('AAPL','2025-01-02'):{'identity':True,'status':True}})
    with pytest.raises(ContractError):supported_cells(protocol,{('ASPI','2025-01-02'):{'identity':True,'status':True,'return':True}})
