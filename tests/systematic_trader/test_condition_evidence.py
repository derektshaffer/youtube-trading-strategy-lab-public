from systematic_trader.condition_evidence import excludes_minute_price,excludes_volume


def test_tape_specific_minute_exclusions_do_not_approve_unknown_conditions():
    assert excludes_minute_price(dict(z='C',c=['@','4','W']))
    assert excludes_minute_price(dict(z='C',c=['@','I','F']))
    assert not excludes_minute_price(dict(z='B',c=['W']))
    assert not excludes_minute_price(dict(z='C',c=['@']))
    assert not excludes_minute_price(dict(z='UNKNOWN',c=['I']))
    assert excludes_volume(dict(z='C',c=['@','Q']))
    assert not excludes_volume(dict(z='C',c=['@','I']))


def test_partial_export_cannot_explain_absence_for_unrequested_hours(tmp_path,monkeypatch):
    import json
    import pytest
    from systematic_trader import condition_evidence as module
    from systematic_trader.events import ContractError,timestamp_ns
    class Panel:
        manifest={'calendar':{'2025-01-02':dict(pre=timestamp_ns('2025-01-02T09:00:00Z'),post=timestamp_ns('2025-01-03T01:00:00Z'))}}
        def __init__(self,p):pass
        def session(self,d):return {}
        def close(self):pass
    monkeypatch.setattr(module,'ResearchPanel',Panel)
    (tmp_path/'trades').mkdir()
    (tmp_path/'trades/complete.json').write_text(json.dumps(dict(params=dict(start='2025-01-02T14:30:00Z',end='2025-01-02T14:40:00Z',symbols='TEST'))))
    with pytest.raises(ContractError,match='window_incomplete'):module.reconcile('fixture',tmp_path)
