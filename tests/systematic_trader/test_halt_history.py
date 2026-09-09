import pytest
from systematic_trader.events import ContractError,timestamp_ns
from systematic_trader.halt_history import parse_halts,status_bound


def raw(trade='09:35:54'):
    return ('<rss xmlns:ndaq="http://www.nasdaqtrader.com/"><channel><item>'+
        ''.join('<ndaq:'+k+'>'+v+'</ndaq:'+k+'>' for k,v in dict(IssueSymbol='TEST',Mkt='Q',ReasonCode='LUDP',HaltDate='01/02/2025',HaltTime='09:30:54',ResumptionDate='01/02/2025',ResumptionQuoteTime='09:30:54',ResumptionTradeTime=trade).items())+
        '</item></channel></rss>').encode()


def test_quote_resumption_cannot_end_trading_halt_or_authorize_post_halt_fills():
    rows=parse_halts(raw(),requested_date='2025-01-02')
    assert status_bound(rows,'TEST',timestamp_ns('2025-01-02T14:34:00Z'))['halted'] is True
    after=status_bound(rows,'TEST',timestamp_ns('2025-01-02T14:35:54Z'))
    assert after['halted'] is None and not after['fill_allowed']


def test_open_ended_halt_stays_exclusionary_and_wrong_date_is_not_empty_success():
    rows=parse_halts(raw(''),requested_date='2025-01-02')
    assert status_bound(rows,'TEST',timestamp_ns('2025-01-03T14:34:00Z'))['halted'] is True
    with pytest.raises(ContractError,match='wrong_requested_date'):parse_halts(raw(),requested_date='2025-01-03')


def test_error_html_cannot_masquerade_as_empty_halt_history():
    with pytest.raises(ContractError,match='not_rss'):parse_halts(b'<html><body>Unavailable</body></html>',requested_date='2025-01-02')


@pytest.mark.parametrize('symbol',['TEST','RHE$A'])
def test_native_halt_import_preserves_provider_and_never_backdates_arrival(tmp_path,symbol):
    import hashlib
    from systematic_trader.halt_history import import_archive
    from systematic_trader.events import digest
    from systematic_trader.research_check import save
    from systematic_trader.ledger import Ledger
    source=tmp_path/'source';source.mkdir();data=raw().replace(b'TEST',symbol.encode());sha=hashlib.sha256(data).hexdigest()
    (source/'2025-01-02.raw.xml').write_bytes(data)
    m=dict(heads={'2025-01-02':sha});save(source/'complete.json',{**m,'manifest_hash':digest(m)})
    r=import_archive(source,tmp_path/'journal')
    assert r['resolved_security_identities']==0 and r['replay_identical']
    with Ledger(tmp_path/'journal',read_only=True) as l:
        events=list(l.replay(through_seq=l.watermark()))
        assert len(events)==2 and events[0]['provider']=='nasdaq'
        if symbol=='TEST':assert [e['payload']['status_code'] for e in events]==['H','UNKNOWN']
        else:
            assert all(e['event_type']=='recorder.lifecycle' and e['payload']['original_record']['symbol']==symbol for e in events)
        assert all(e['received_ns']>e['source_time_ns'] and e['instrument_id'] is None for e in events)
        assert list(l.replay(through_seq=l.watermark(),received_through_ns=timestamp_ns('2025-01-03T00:00:00Z')))==[]
