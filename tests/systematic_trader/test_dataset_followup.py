import json
import pytest
from systematic_trader.dataset_followup import microstructure_followup
from systematic_trader.events import ContractError


def test_missing_minute_has_distinct_no_trade_odd_lot_and_unknown_reasons(tmp_path,monkeypatch):
    params=dict(symbols='TEST',start='2025-03-03T14:30:00Z',end='2025-03-03T14:33:00Z')
    for kind in ('quotes','trades','bars'):
        (tmp_path/kind).mkdir();(tmp_path/kind/'complete.json').write_text(json.dumps(dict(params=params,page_chain_head='0'*64)))
    payloads=dict(quotes={'quotes':{'TEST':[]}},bars={'bars':{'TEST':[]}},trades={'trades':{'TEST':[
        dict(t='2025-03-03T14:31:01Z',c=['@','I']),dict(t='2025-03-03T14:32:01Z',c=['@']),
        dict(t='2025-03-03T14:33:00Z',c=['@'])]}})
    monkeypatch.setattr('systematic_trader.dataset_followup.verified_pages',lambda path:iter([(payloads[path.name],{})]))
    report=microstructure_followup(tmp_path,symbols=('TEST',),start=params['start'],end=params['end'])
    assert [m['absence_diagnostic'] for m in report['symbols']['TEST']['minutes']]==[
        'no_trades_in_complete_export_window','all_trades_have_odd_lot_condition','absence_unresolved']
    assert sum(m['trades'] for m in report['symbols']['TEST']['minutes'])==2
    assert not report['certified']
    with pytest.raises(ContractError,match='incomplete'):
        microstructure_followup(tmp_path,symbols=('TEST',),start=params['start'],end='2025-03-03T14:34:00Z')
