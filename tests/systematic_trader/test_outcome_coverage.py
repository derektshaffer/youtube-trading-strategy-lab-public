from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from systematic_trader.events import ContractError, digest, timestamp_ns
from systematic_trader.features import MINUTE
from systematic_trader.outcome_coverage import audit_day, classify, summarize
from systematic_trader.sparse_observations import minute_state, TapeCoverage
from systematic_trader.research_split import ResearchSplit

OPEN=timestamp_ns('2025-01-02T14:30:00Z')
CLOSE=OPEN+390*MINUTE
UNKNOWN=dict(state='unknown',reason='no_observed_crossing_in_incomplete_session')


def fixture(changes=None, *, coverage=True):
    cov=TapeCoverage('TEST',OPEN,CLOSE,CLOSE+MINUTE,'a'*64) if coverage else None
    tape={OPEN:[dict(t='2025-01-02T14:30:00Z',p='10',s=100,z='C',c=['@'])]}
    if changes:tape.update(changes)
    states=[]
    for t in range(OPEN,CLOSE,MINUTE):
        rows=tape.get(t,[]);eligible=[r for r in rows if r['c']==['@']]
        bar=None
        if eligible:
            prices=[Decimal(r['p']) for r in eligible]
            p=dict(open=str(prices[0]),close=str(prices[-1]),high=str(max(prices)),low=str(min(prices)),vwap=str(prices[0]),
                   volume_shares=sum(r['s'] for r in rows),trade_count=len(rows))
            bar=dict(stamp=t,segment='regular',payload=p,lineage=dict(normalized_payload_hash=digest(p)))
        states.append(minute_state('TEST',t,bar=bar,trades=rows,coverage=cov))
    return states,tape,cov


def evidence(parts):return audit_day('TEST',*parts,OPEN,CLOSE)


def print_at(minute,price='10',condition='@'):
    t=OPEN+minute*MINUTE
    return t,[dict(t=datetime.fromtimestamp(t//10**9,timezone.utc).isoformat(),p=price,s=100,z='C',c=[condition])]


def test_documented_389_quiet_minutes_certify_negative_without_prices():
    parts=fixture();e=evidence(parts)
    assert e['full_price_coverage'] and e['documented_absent_minutes']==389
    assert classify(UNKNOWN,e,'0.05')['state']=='negative'
    assert sum(s['bar'] is not None for s in parts[0])==1


def test_absent_bars_without_complete_tape_cannot_certify_negative():
    assert classify(UNKNOWN,evidence(fixture(coverage=False)),'0.05')['state']=='unknown'


@pytest.mark.parametrize('threshold,price',[('0.05','10.5'),('0.10','11'),('0.20','12')])
def test_inclusive_frozen_thresholds(threshold,price):
    e=evidence(fixture(dict([print_at(10,price)])))
    assert classify(UNKNOWN,e,threshold)['state']=='positive'


def test_future_outcome_is_not_discovery_input():
    first=fixture();later=fixture(dict([print_at(389,'12')]))
    assert first[0][:389]==later[0][:389]
    assert classify(UNKNOWN,evidence(first),'0.20')['state']=='negative'
    assert classify(UNKNOWN,evidence(later),'0.20')['state']=='positive'


def test_non_price_print_above_threshold_does_not_create_positive():
    parts=fixture(dict([print_at(10,'100','I')]))
    e=evidence(parts)
    assert classify(UNKNOWN,e,'0.20')['state']=='negative'
    assert parts[0][10]['bar'] is None and e['observed_peak']=='10'


def test_unknown_condition_prevents_negative():
    e=evidence(fixture(dict([print_at(10,'100','?')])))
    assert classify(UNKNOWN,e,'0.05')['state']=='unknown'


def test_local_positive_can_survive_other_unresolved_minutes():
    e=evidence(fixture(dict([print_at(10,'12'),print_at(20,'100','?')])))
    assert not e['full_price_coverage']
    assert classify(UNKNOWN,e,'0.20')['state']=='positive'


def test_missing_open_is_not_later_first_trade_open():
    parts=fixture();parts[1].pop(OPEN);parts[0][0]=minute_state('TEST',OPEN,coverage=parts[2])
    e=evidence(parts)
    assert e['opening_price'] is None and classify(UNKNOWN,e,'0.05')['state']=='unknown'


def test_ambiguous_same_timestamp_open_is_unknown():
    parts=fixture({OPEN:[dict(t='2025-01-02T14:30:00Z',p=p,s=100,z='C',c=['@']) for p in ('10','10.1')]})
    assert not evidence(parts)['opening_corroborated']


def test_one_bar_tape_disagreement_blocks_negative():
    parts=fixture();parts[0][10]['issues']=['bar_tape_volume_disagreement'];parts[0][10]['coverage']='unresolved'
    assert classify(UNKNOWN,evidence(parts),'0.05')['state']=='unknown'


def test_original_labels_and_action_mask_are_preserved():
    e=evidence(fixture(dict([print_at(10,'12')])))
    original=dict(state='negative',reason='complete_session_label')
    assert classify(original,e,'0.05')['state']=='negative'
    assert original==dict(state='negative',reason='complete_session_label')
    assert classify(dict(state='unknown',reason='corporate_action_quality_exclusion'),e,'0.05')['state']=='unknown'


def test_unknown_tape_hash_and_inventory_refuse_classification():
    parts=fixture();parts[1][OPEN][0]['p']='100'
    with pytest.raises(ContractError,match='link_mismatch'):evidence(parts)
    parts=fixture();parts[0].pop()
    with pytest.raises(ContractError,match='inventory'):evidence(parts)


def test_sourced_expected_absence_distinct_from_quiet_unknown():
    states,tape,cov=fixture();tape={}
    states=[minute_state('TEST',t,coverage=cov,identity='prelisting',identity_source='b'*64) for t in range(OPEN,CLOSE,MINUTE)]
    assert classify(UNKNOWN,evidence((states,tape,cov)),'0.05')['state']=='expected_absent'


def test_lane_precision_and_unknown_bounds_keep_denominators():
    rows=[dict(day=str(i),symbol='TEST',threshold='0.05',label={'state':state}) for i,state in enumerate(['negative','unknown','unknown'])]
    dets={(str(i),'TEST'):dict(lane='cold_start_same_session' if i<2 else 'full_history',decision_ns=OPEN) for i in range(3)}
    s=summarize(rows,dets,lane='cold_start_same_session')
    assert s['signaled_symbol_days']==2 and s['precision_bounds']==[0,.5]
    assert s['counts']['not_surfaced_unknown']==1 and s['precision_if_identified'] is None


def test_frozen_protocol_integrity_and_holdout_denial():
    p=json.loads((Path(__file__).parents[2]/'systematic_trader/protocols/outcome-coverage-v1.json').read_text())
    assert p['protocol_hash']==digest({k:v for k,v in p.items() if k!='protocol_hash'})
    assert p['thresholds']==['0.05','0.10','0.20'] and p['delays']==[0,2,5]
    with pytest.raises(ContractError,match='holdout'):
        ResearchSplit().authorize(timestamp_ns('2025-05-01T12:00:00Z'),timestamp_ns('2025-05-02T12:00:00Z'),purpose='discovery_development')


def test_numeric_omission_candidate_is_not_permission_to_repair():
    from systematic_trader.outcome_diagnostics import explain_discrepancy
    parts=fixture();s=parts[0][0];rows=deepcopy(parts[1][OPEN]);extra={**rows[0],'s':1,'c':['@','I']};rows.append(extra)
    s=deepcopy(s);s['issues']=['bar_tape_volume_disagreement']
    before=deepcopy((s,rows));result=explain_discrepancy(s,rows,[])
    assert result['excess_volume']==1 and len(result['single_record_omission_candidates'])==1
    assert result['classification']=='provider_export_disagreement_cause_unresolved'
    assert not result['prices_changed'] and (s,rows)==before


def test_resumption_date_query_retains_prior_halt_and_unknown_outside():
    from systematic_trader.outcome_diagnostics import parse_resumptions
    from systematic_trader.halt_history import status_bound
    raw=b'''<rss xmlns:n="http://www.nasdaqtrader.com/"><channel><item>
    <n:HaltDate>12/31/2024</n:HaltDate><n:HaltTime>10:00:00</n:HaltTime>
    <n:ResumptionDate>01/02/2025</n:ResumptionDate><n:ResumptionTradeTime>10:00:00</n:ResumptionTradeTime>
    <n:ResumptionQuoteTime>09:55:00</n:ResumptionQuoteTime><n:IssueSymbol>TEST</n:IssueSymbol>
    <n:Mkt>NASDAQ</n:Mkt><n:ReasonCode>T1</n:ReasonCode></item></channel></rss>'''
    rows=parse_resumptions(raw,requested_date='2025-01-02')
    assert rows[0]['halt_ns']<OPEN
    assert status_bound(rows,'TEST',OPEN)['halted'] is True
    assert status_bound(rows,'TEST',OPEN+MINUTE*31)['halted'] is None
    assert not status_bound(rows,'TEST',OPEN+MINUTE*31)['fill_allowed']
    with pytest.raises(ContractError,match='wrong_requested_date'):
        parse_resumptions(raw,requested_date='2025-01-03')
