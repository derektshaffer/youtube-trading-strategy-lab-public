import pytest
from systematic_trader.dataset_audit import calendar_sessions,validate_bar
from systematic_trader.events import ContractError,timestamp_ns


def row(date):return dict(date=date,open='09:30',close='16:00',session_open='0400',session_close='2000')


def test_real_provider_calendar_dst_and_early_close():
    before=row('2025-03-07');after=row('2025-03-10');early=row('2024-12-24');early['close']='13:00'
    sessions=calendar_sessions([before,after,early])
    assert sessions['2025-03-07']['open']==timestamp_ns('2025-03-07T14:30:00Z')
    assert sessions['2025-03-10']['open']==timestamp_ns('2025-03-10T13:30:00Z')
    assert sessions['2024-12-24']['close']==timestamp_ns('2024-12-24T18:00:00Z')


def test_missing_extended_hours_or_duplicate_calendar_not_guessed():
    with pytest.raises(ContractError,match='duplicate'):calendar_sessions([row('2025-03-07')]*2)
    r=row('2025-03-07');r.pop('session_open')
    with pytest.raises(ContractError,match='missing'):calendar_sessions([r])


def test_native_bar_preserves_precision_and_reuses_canonical_validation():
    b=dict(t='2025-03-07T14:30:00Z',o='1.001',h='1.01',l='1',c='1.005',v=200,n=10,vw='1.003')
    _,p=validate_bar('TEST',b)
    assert p['close']=='1.005'
    with pytest.raises(ContractError):validate_bar('TEST',{**b,'h':'0.5'})
    with pytest.raises(ContractError):validate_bar('TEST',{**b,'t':'2025-03-07T14:30:00.1Z'})


def test_premarket_only_day_audit_serializes_and_cannot_certify(tmp_path,monkeypatch):
    import json
    from systematic_trader.dataset_audit import audit
    from systematic_trader.events import digest,canonical_json
    protocol=dict(selection=[dict(symbol='TEST',status='active')]);protocol['protocol_hash']=digest(protocol)
    (tmp_path/'study-protocol.v2.json').write_text(canonical_json(protocol))
    for kind in ('assets','calendar','bars','actions','quotes','trades'):
        (tmp_path/kind).mkdir();(tmp_path/kind/'complete.json').write_text('{}')
    payloads=dict(assets=[dict(symbol='TEST',status='active')],calendar=[row('2025-03-07')],
        bars={'bars':{'TEST':[dict(t='2025-03-07T09:00:00Z',o='1',h='1',l='1',c='1',v=100,n=1,vw='1')]}},
        actions={'corporate_actions':{}},quotes={'quotes':{}},trades={'trades':{}})
    monkeypatch.setattr('systematic_trader.dataset_audit.verified_pages',lambda path:iter([(payloads[path.name],{'sha256':'0'*64})]))
    result=audit(tmp_path)
    assert result['bar_count']==1 and not result['certified']
    assert result['by_symbol']['TEST']['missing_regular_minutes']==390
    assert '2025-03-07' in result['by_symbol']['TEST']['missing_sessions']
    json.dumps(result)
