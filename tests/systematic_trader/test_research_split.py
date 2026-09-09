from unittest.mock import Mock
import pytest
from systematic_trader.events import ContractError,timestamp_ns
from systematic_trader.research_split import ResearchSplit,protect_holdout,HOLDOUT_START,HOLDOUT_END
from systematic_trader.data_acquisition import acquire,HistoricalAccess


@pytest.mark.parametrize('purpose',['discovery_development','parameter_search','strategy_development','frozen_validation','frozen_oos'])
def test_holdout_cannot_be_read_by_any_routine_research_purpose(purpose):
    with pytest.raises(ContractError,match='holdout'):
        ResearchSplit().authorize(HOLDOUT_START,HOLDOUT_END,purpose=purpose)


def test_development_cannot_read_validation_and_protocol_cannot_be_replaced():
    with pytest.raises(ContractError,match='period'):
        ResearchSplit().authorize(timestamp_ns('2025-03-03T14:30:00Z'),timestamp_ns('2025-03-03T21:00:00Z'),purpose='parameter_search')
    with pytest.raises(ContractError,match='protocol'):ResearchSplit('0'*64)


def test_holdout_inclusive_network_boundary_and_before_network_refusal(tmp_path):
    protect_holdout('2025-04-30T00:00:00-04:00','2025-05-01T03:59:59.999999999Z')
    params=dict(start='2025-04-30',end='2025-05-01T04:00:00Z',feed='sip',sort='asc',asof='-',adjustment='raw')
    access=Mock()
    with pytest.raises(ContractError,match='holdout'):acquire(access,tmp_path/'denied',kind='bars',params=params)
    access.get.assert_not_called();assert not (tmp_path/'denied').exists()
    opener=Mock()
    with pytest.raises(ContractError,match='holdout'):HistoricalAccess(('key','secret'),opener=opener).get('bars',params)
    opener.open.assert_not_called()


def test_offline_price_export_reader_also_refuses_holdout_before_page_read(tmp_path):
    import json
    from systematic_trader.data_acquisition import verified_pages
    (tmp_path/'request.json').write_text(json.dumps(dict(kind='bars',params=dict(start='2025-05-01',end='2025-06-30'))))
    (tmp_path/'complete.json').write_text('{}')
    # No raw page exists: the rejection must come from the holdout guard first.
    with pytest.raises(ContractError,match='holdout'):list(verified_pages(tmp_path))
