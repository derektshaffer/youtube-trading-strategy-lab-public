from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from systematic_trader.events import ContractError, canonical_json, digest, timestamp_ns
from systematic_trader.features import MINUTE
from systematic_trader.historical import HistoricalImporter, historical_view, parse_bundle, VERSION
from systematic_trader.ledger import Ledger, Recorder
from systematic_trader.research_fixture import stamp

START = timestamp_ns('2026-03-09T09:30:00-04:00')
IMPORTED = START + 30 * 1440 * MINUTE
KEY = ('example_vendor', 'export_minutes', 'import', 'stable:one')


def bundle():
    rows = [
        dict(kind='instrument', known_at=stamp(START-MINUTE), symbol='TEST', instrument_id='stable:one',
             effective_from=stamp(START-1440*MINUTE), effective_to=stamp(IMPORTED), tick_size='0.01'),
        dict(kind='calendar', known_at=stamp(START-MINUTE), session_id='day-one', exchange='TEST',
             pre_open=stamp(START-MINUTE), open=stamp(START), close=stamp(START+6*MINUTE), post_close=stamp(START+7*MINUTE)),
        dict(kind='universe', known_at=stamp(START-MINUTE), effective_at=stamp(START-MINUTE), entries=[
            dict(instrument_id='stable:one', symbol='TEST', active=True, asset_type='common_stock')]),
        dict(kind='coverage', known_at=stamp(START-MINUTE), instrument_id='stable:one', session_id='day-one', segments=['regular']),
    ]
    for minute in range(6):
        rows.append(dict(kind='bar', symbol='TEST', instrument_id='stable:one', start=stamp(START+minute*MINUTE),
                         known_at=stamp(START+(minute+1)*MINUTE), revision=0, open='10', high='12', low='9', close='11',
                         volume=100+minute, trade_count=10, vwap='10.5'))
    return dict(version=VERSION, dataset_id='test-dataset', provider=KEY[0], feed=KEY[1], source='synthetic-only',
                evidence='fixture', adjustment='unadjusted', availability=dict(mode='vendor_timestamp', delay_ns=0),
                universe_coverage='point_in_time', records=rows)


def ingest(ledger, data):
    return HistoricalImporter(ledger, clock=lambda: IMPORTED).ingest(canonical_json(data).encode())


def view(ledger, when=START+6*MINUTE, watermark=None):
    return historical_view(ledger, through_seq=ledger.watermark() if watermark is None else watermark,
                           as_of_ns=when, dataset_id='test-dataset')


def test_raw_bytes_real_import_clocks_and_explicit_research_projection(tmp_path):
    raw=json.dumps(bundle(), indent=2).encode()
    with Ledger(tmp_path, min_free_bytes=0) as ledger:
        HistoricalImporter(ledger, clock=lambda:IMPORTED).ingest(raw)
        assert ledger.connection.execute('SELECT raw FROM receipts').fetchone()[0] == raw
        saved=list(ledger.replay(through_seq=ledger.watermark()))
        assert all(e['received_ns']==IMPORTED and e['origin']=='import' for e in saved)
        v=view(ledger)
        assert v['market'].instruments[KEY].bars[(START,MINUTE)]['received_ns']==START+MINUTE
        assert v['events'][-1]['payload']['provider_details']['import_received_ns']==IMPORTED
        assert not v['manifest']['gaps']
        assert not v['manifest']['production_eligible']
        assert list(ledger.replay(through_seq=ledger.watermark()))==saved
        original=v['manifest']
    with Ledger(tmp_path, read_only=True) as ledger:
        assert view(ledger)['manifest']==original


def test_future_data_and_future_revision_cannot_change_prefix(tmp_path):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        b=bundle();ingest(ledger,b)
        old=view(ledger,START+3*MINUTE);watermark=ledger.watermark()
        revision=deepcopy(b['records'][4]);revision.update(revision=1,known_at=stamp(START+5*MINUTE),high='100')
        b['records']=[revision];ingest(ledger,b)
        # Manifest archive boundary changes, but actual historical state is identical.
        assert view(ledger,START+3*MINUTE)['market'].fingerprint()==old['market'].fingerprint()
        assert view(ledger,START+6*MINUTE,watermark)['market'].instruments[KEY].bars[(START,MINUTE)]['payload']['high']=='12'
        assert view(ledger)['market'].instruments[KEY].bars[(START,MINUTE)]['payload']['high']=='100'


def test_duplicate_file_is_idempotent_and_logical_duplicate_bar_not_reused(tmp_path):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        b=bundle();first=ingest(ledger,b);before=ledger.verify()
        assert ingest(ledger,b)==first and ledger.verify()==before
        b['records']=[deepcopy(b['records'][4])];b['records'][0]['known_at']=stamp(START+6*MINUTE)
        ingest(ledger,b)
        assert len(view(ledger)['market'].instruments[KEY].bars)==6
        assert not view(ledger)['market'].instruments[KEY].problems


def test_revision_ordinals_resist_late_old_bars_and_conflicts(tmp_path):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        b=bundle();ingest(ledger,b)
        first=deepcopy(b['records'][4]);first.update(revision=2,known_at=stamp(START+3*MINUTE),high='14')
        older=deepcopy(first);older.update(revision=1,known_at=stamp(START+4*MINUTE),high='13')
        b['records']=[first,older];ingest(ledger,b)
        assert view(ledger)['market'].instruments[KEY].bars[(START,MINUTE)]['payload']['high']=='14'
        conflict=deepcopy(first);conflict.update(known_at=stamp(START+5*MINUTE),high='15')
        b['records']=[conflict];ingest(ledger,b)
        assert 'conflicting_bar_revision' in view(ledger)['market'].instruments[KEY].problems


def test_missing_bar_is_explicit_and_future_bars_do_not_create_premature_gaps(tmp_path):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        b=bundle();del b['records'][6];ingest(ledger,b)
        assert not view(ledger,START+2*MINUTE)['manifest']['gaps']
        gaps=view(ledger)['manifest']['gaps']
        assert gaps[0]['starts_ns']==[START+2*MINUTE]
        assert view(ledger)['market'].gaps


def test_pending_raw_recovers_with_original_import_time_and_exactly_once(tmp_path):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        with patch.object(ledger,'append_events',side_effect=RuntimeError('crash')):
            with pytest.raises(RuntimeError):ingest(ledger,bundle())
        assert len(ledger.pending())==1 and ledger.watermark()==0
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        # The existing recorder dispatches recovery by persisted adapter version.
        Recorder(ledger,clock=lambda:IMPORTED+MINUTE).recover()
        assert ledger.verify()['pending_receipts']==0
        assert all(e['received_ns']==IMPORTED for e in ledger.replay(through_seq=ledger.watermark()))
        before=ledger.verify();ingest(ledger,bundle());assert ledger.verify()==before


@pytest.mark.parametrize('change', [
    lambda b:b.update(adjustment='split_adjusted'),
    lambda b:b['records'][4].update(known_at=stamp(START)),
    lambda b:b['records'][4].update(high='NaN'),
    lambda b:b['records'][4].update(volume=True),
    lambda b:b['records'][0].update(instrument_id='TEST'),
    lambda b:b['records'][0].update(effective_to=stamp(START-1440*MINUTE)),
    lambda b:b['records'][1].update(close=stamp(START)),
    lambda b:b['records'][1].update(open='2026-03-09T09:30:00'),
    lambda b:b.update(execution_authority='live'),
    lambda b:b['records'][4].update(known_at=stamp(IMPORTED+MINUTE)),
])
def test_bad_chunk_is_raw_preserved_atomically_quarantined(tmp_path,change):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        b=bundle();change(b);raw=canonical_json(b).encode();HistoricalImporter(ledger,clock=lambda:IMPORTED).ingest(raw)
        assert ledger.connection.execute('SELECT raw FROM receipts').fetchone()[0]==raw
        events=list(ledger.replay(through_seq=ledger.watermark()))
        assert len(events)==1 and events[0]['event_type']=='recorder.reject'
        assert ledger.verify()['pending_receipts']==0
        with pytest.raises(ContractError,match='rejected_chunk'):view(ledger)


def test_assumed_availability_labeled_and_revisions_cannot_backdate(tmp_path):
    b=bundle();b['availability']=dict(mode='assumed_bar_close',delay_ns=MINUTE)
    for r in b['records']:
        if r['kind']=='bar':r.pop('known_at')
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        ingest(ledger,b)
        v=view(ledger,START+7*MINUTE)
        assert 'historical_availability_assumed' in v['market'].instruments[KEY].problems
        assert v['events'][4]['received_ns']==START+2*MINUTE
    b['records'][4]['revision']=1
    with pytest.raises(ContractError,match='assumed_availability'):parse_bundle(canonical_json(b).encode())


def test_delisting_and_symbol_change_are_effective_and_known_time_scoped(tmp_path):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        b=bundle();b['records'][0]['effective_to']=stamp(START+3*MINUTE)
        new=deepcopy(b['records'][0]);new.update(symbol='RENAMED',effective_from=stamp(START+3*MINUTE),effective_to=stamp(IMPORTED))
        new['known_at']=stamp(START+3*MINUTE)
        b['records'].append(new)
        for r in b['records']:
            if r['kind']=='bar' and timestamp_ns(r['start'])>=START+3*MINUTE:r['symbol']='RENAMED'
        delisted=dict(kind='universe',known_at=stamp(START+5*MINUTE),effective_at=stamp(START+4*MINUTE),entries=[])
        b['records'].append(delisted);ingest(ledger,b)
        assert view(ledger,START+4*MINUTE)['universe'].as_of(START+4*MINUTE)[0]
        assert not view(ledger)['universe'].as_of(START+6*MINUTE)[0]
        assert len(view(ledger)['market'].instruments[KEY].bars)==6
        assert not view(ledger)['market'].gaps


def test_late_identity_cannot_resolve_earlier_historical_observation(tmp_path):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        b=bundle();b['records'][0]['known_at']=stamp(START+3*MINUTE);ingest(ledger,b)
        v=view(ledger)
        assert v['market'].gaps
        assert (START,MINUTE) not in v['market'].instruments[KEY].bars


@pytest.mark.parametrize('coverage',['unknown','survivors_only'])
def test_survivor_universe_never_silently_qualifies(tmp_path,coverage):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        b=bundle();b['universe_coverage']=coverage;ingest(ledger,b)
        assert 'survivorship_coverage_unverified' in view(ledger)['market'].instruments[KEY].problems


@pytest.mark.parametrize('open_stamp',['2026-03-06T09:30:00-05:00','2026-03-09T09:30:00-04:00',
                                      '2026-11-02T09:30:00-05:00'])
def test_calendar_uses_supplied_exact_offsets_not_fixed_utc_hours(open_stamp):
    b=bundle();r=b['records'][1];ns=timestamp_ns(open_stamp)
    r.update(pre_open=stamp(ns-10*MINUTE),open=open_stamp,close=stamp(ns+210*MINUTE),post_close=stamp(ns+300*MINUTE))
    _,rows=parse_bundle(canonical_json(b).encode())
    assert timestamp_ns(rows[1]['payload']['open'])==ns
    assert rows[1]['payload']['historical']['original_timestamps']['open']==open_stamp


def test_extended_sessions_and_early_close_coverage_are_explicit(tmp_path):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        b=bundle();b['records'][3]['segments']=['pre','regular','post'];ingest(ledger,b)
        gaps=view(ledger,START+7*MINUTE)['manifest']['gaps']
        assert [(g['segment'],g['starts_ns']) for g in gaps]==[('pre',[START-MINUTE]),('post',[START+6*MINUTE])]


def test_missing_calendar_or_coverage_fails_closed(tmp_path):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        b=bundle();b['records']=[r for r in b['records'] if r['kind']!='calendar'];ingest(ledger,b)
        assert view(ledger)['manifest']['gaps'][0]['reason']=='historical_calendar_unavailable'


def test_future_corporate_action_does_not_enter_past_state(tmp_path):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        b=bundle();ingest(ledger,b);old=view(ledger,START+3*MINUTE)['market'].fingerprint()
        b['records']=[dict(kind='corporate_action',known_at=stamp(START+5*MINUTE),instrument_id='stable:one',
                           action_id='split-one',action_type='split',effective_at=stamp(START+1440*MINUTE),details={'ratio':'2'})]
        ingest(ledger,b)
        assert view(ledger,START+3*MINUTE)['market'].fingerprint()==old
        assert any(r['event_type']=='reference.corporate_action' for r in view(ledger)['market'].references.values())


def test_daily_aggregation_requires_complete_closed_asof_session(tmp_path):
    from systematic_trader.historical import daily_history
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        ingest(ledger,bundle())
        assert daily_history(view(ledger,START+5*MINUTE))==[]
        rows=daily_history(view(ledger))
        assert len(rows)==1 and rows[0]['volume']=='615' and rows[0]['open_volume']=='510'
        assert rows[0]['minute_volumes']==[100,101,102,103,104,105]
        assert rows[0]['known_ns']==START+6*MINUTE
        b=bundle();revision=deepcopy(b['records'][4]);revision.update(revision=1,volume=200,known_at=stamp(START+7*MINUTE))
        b['records']=[revision];ingest(ledger,b)
        assert daily_history(view(ledger))==rows
        assert daily_history(view(ledger,START+7*MINUTE))[0]['volume']=='715'


def test_bar_outside_declared_session_is_not_assumed_regular(tmp_path):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        b=bundle();extra=deepcopy(b['records'][-1]);extra.update(start=stamp(START+6*MINUTE),known_at=stamp(START+7*MINUTE))
        b['records'].append(extra);ingest(ledger,b)
        assert any(g['reason']=='historical_bar_session_coverage_ambiguous' for g in view(ledger,START+7*MINUTE)['manifest']['gaps'])


def test_mixed_provider_chunks_cannot_share_dataset_identity(tmp_path):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        b=bundle();ingest(ledger,b);b['provider']='another_vendor';ingest(ledger,b)
        with pytest.raises(ContractError,match='mixed_historical'):view(ledger)


def test_reference_type_error_quarantines_instead_of_leaving_unrecoverable_receipt(tmp_path):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        b=bundle();b['records'][1]['exchange']=123;ingest(ledger,b)
        assert ledger.verify()['pending_receipts']==0
        assert ledger.watermark()==1
        assert list(ledger.replay(through_seq=1))[0]['event_type']=='recorder.reject'


def test_historical_import_cannot_contaminate_live_capture_journal(tmp_path):
    with Ledger(tmp_path,min_free_bytes=0) as ledger:
        Recorder(ledger,origin='live').internal('recorder.lifecycle',dict(state='starting'))
        before=ledger.verify()
        with pytest.raises(ContractError,match='separate_live_archive'):ingest(ledger,bundle())
        assert ledger.verify()==before


def test_cli_rejects_relative_alias_of_default_live_data_directory(capsys):
    from systematic_trader.__main__ import main
    assert main(['--data-dir','.systematic-trader/capture','import-history','--file','unread-file.json'])==2
    assert 'explicit_data_directory' in capsys.readouterr().err
