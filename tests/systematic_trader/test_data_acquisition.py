import json
from unittest.mock import Mock
import pytest
from systematic_trader.data_acquisition import HistoricalAccess,acquire,verified_pages
from systematic_trader.events import ContractError,canonical_json
PARAMS=dict(symbols='AAPL',start='2025-01-01',end='2025-01-02',feed='sip',sort='asc',asof='-',adjustment='raw',timeframe='1Min')


def test_complete_pagination_is_raw_preserved_verified_and_idempotent(tmp_path):
    raw=[b'{"bars":{"AAPL":[]},"next_page_token":"next"}',b'{"bars":{"AAPL":[]},"next_page_token":null}']
    access=Mock();access.get.side_effect=raw
    result=acquire(access,tmp_path,kind='bars',params=PARAMS)
    assert result['complete'] and not result['certified'] and result['pages']==2
    assert (tmp_path/'000000.raw.json').read_bytes()==raw[0]
    assert len(list(verified_pages(tmp_path)))==2
    assert acquire(access,tmp_path,kind='bars',params=PARAMS)==result and access.get.call_count==2


def test_truncated_chain_not_certified_and_can_resume(tmp_path):
    access=Mock();access.get.side_effect=[b'{"bars":{},"next_page_token":"next"}',b'{"bars":{},"next_page_token":null}']
    with pytest.raises(ContractError,match='budget'):acquire(access,tmp_path,kind='bars',params=PARAMS,max_pages=1)
    assert not (tmp_path/'complete.json').exists()
    assert acquire(access,tmp_path,kind='bars',params=PARAMS,max_pages=2)['complete']


def test_page_integrity_failure_cannot_be_ignored(tmp_path):
    access=Mock();access.get.return_value=b'{"bars":{},"next_page_token":null}'
    acquire(access,tmp_path,kind='bars',params=PARAMS)
    (tmp_path/'000000.raw.json').write_text('{}')
    with pytest.raises(ContractError,match='integrity'):list(verified_pages(tmp_path))


def test_fixed_market_endpoints_no_credential_reflection_or_adjustment_fallback():
    response=Mock();response.status=200;response.read.return_value=b'{"message":"secret-key"}'
    response.__enter__=Mock(return_value=response);response.__exit__=Mock()
    opener=Mock();opener.open.return_value=response
    access=HistoricalAccess(('test-key','secret-key'),opener=opener)
    with pytest.raises(ContractError,match='endpoint'):access.get('orders',{})
    with pytest.raises(ContractError,match='adjustment'):access.get('bars',{**PARAMS,'adjustment':'all'})
    with pytest.raises(ContractError,match='reflection'):access.get('bars',PARAMS)


def test_repeat_token_or_changed_resume_request_fails_closed(tmp_path):
    access=Mock();access.get.return_value=b'{"bars":{},"next_page_token":"same"}'
    with pytest.raises(ContractError,match='repeated'):acquire(access,tmp_path,kind='bars',params=PARAMS)
    with pytest.raises(ContractError,match='mismatch'):acquire(access,tmp_path,kind='bars',params={**PARAMS,'feed':'iex'})


@pytest.mark.parametrize('change',[
    {'params':{**PARAMS,'end':'2030-01-01'}},
    {'bytes':1},
    {'certified':True},
    {'pages':0},
])
def test_offline_completion_cannot_relabel_query_coverage_or_evidence(tmp_path,change):
    access=Mock();access.get.return_value=b'{"bars":{},"next_page_token":null}'
    result=acquire(access,tmp_path,kind='bars',params=PARAMS)
    (tmp_path/'complete.json').write_text(json.dumps({**result,**change}))
    with pytest.raises(ContractError,match='completion'):
        list(verified_pages(tmp_path))
