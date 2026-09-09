from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import sqlite3

import pytest

from systematic_trader.events import ContractError, digest
from systematic_trader.features import Discovery, Universe, MINUTE, feature_snapshot
from systematic_trader.ledger import Ledger, Recorder
from systematic_trader.market_state import MarketState
from systematic_trader.research_fixture import KEY, OPEN, SESSION, evaluate, history, populate, universe
from systematic_trader.research_engine import ExecutionPolicy, OpeningRangeBreakout, RiskBook, RiskPolicy, Simulator
from systematic_trader.validation import ExperimentStore, evaluate_holdout, walk_forward


@pytest.fixture
def events(tmp_path):
    with Ledger(tmp_path/"capture",min_free_bytes=0) as ledger:
        populate(ledger)
        return list(ledger.replay(through_seq=ledger.watermark()))


def state(events):
    market=MarketState()
    for e in events:market.apply(e)
    return market


def snapshot(events,daily=None):
    market=state(events)
    return feature_snapshot(market,KEY,SESSION,history() if daily is None else daily,
                            as_of_ns=events[-1]["received_ns"],research=True)


def test_replay_reopen_and_full_vertical_is_deterministic(tmp_path):
    with Ledger(tmp_path/"journal",min_free_bytes=0) as ledger:
        hashes=populate(ledger);a=evaluate(ledger.replay(through_seq=ledger.watermark()))
    with Ledger(tmp_path/"journal",read_only=True) as ledger:
        assert hashes==ledger.verify()
        b=evaluate(ledger.replay(through_seq=ledger.watermark()))
    assert a==b
    assert a["simulation"]["net_pnl"]=="2.600"  # Fixture arithmetic, never profitability evidence.
    assert not a["simulation"]["residual_positions"]
    assert a["no_trade"]==dict(net_pnl="0",trades=0)
    assert not a["production_eligible"] and a["execution_authority"]=="none"


def test_future_receipts_and_nonmonotonic_replay_rejected(events):
    market=state(events[:8])
    with pytest.raises(ContractError,match="future_receipts"):
        feature_snapshot(market,KEY,SESSION,history(),as_of_ns=OPEN,research=True)
    with pytest.raises(ContractError,match="nonmonotonic"):
        market.apply(events[0])
    with pytest.raises(ContractError,match="missing_committed"):
        state(events[:3]+events[4:])


def test_late_history_and_other_origin_do_not_change_past_features(events):
    past=snapshot(events[:8]);late=deepcopy(history()[0]);late.update(known_ns=SESSION.close_ns,volume="999999999")
    wrong=deepcopy(history()[1]);wrong.update(origin="live",volume="999999999")
    assert snapshot(events[:8],history()+[late,wrong])==past


def test_history_requires_real_availability_and_prices(events):
    h=history();h[0]["known_ns"]=h[0]["close_ns"]-1
    with pytest.raises(ContractError,match="availability"):snapshot(events[:8],h)
    h=history();h[0]["high"]="NaN"
    with pytest.raises(ContractError,match="historical_value"):snapshot(events[:8],h)


def test_opening_range_waits_and_freeze_is_immutable(events):
    d=Discovery()
    with pytest.raises(ContractError,match="before_range_end"):
        d.freeze(SESSION,[snapshot(events[:6])],universe(),as_of_ns=OPEN+4*MINUTE)
    s=snapshot(events[:8]);frozen=d.freeze(SESSION,[s],universe(),as_of_ns=s["as_of_ns"])
    s["values"]["opening_high"]="999"
    assert d.freeze(SESSION,[s],universe(),as_of_ns=s["as_of_ns"])[0]["values"]["opening_high"]=="100.20"
    frozen[0]["values"]["opening_high"]="888"
    assert d.frozen[SESSION.session_id][0]["values"]["opening_high"]=="100.20"


def test_discovery_requires_complete_universe_and_stable_membership(events):
    u=universe();old=u.as_of(OPEN)
    u.observe([],known_ns=SESSION.close_ns,effective_ns=OPEN,source_hash=digest("delisted-later"))
    assert u.as_of(OPEN)==old and u.as_of(SESSION.close_ns)[0]=={}
    with pytest.raises(ContractError,match="coverage_incomplete"):
        Discovery().freeze(SESSION,[],u,as_of_ns=OPEN+5*MINUTE)


def test_stale_quotes_and_unknown_lot_units_block_features(events):
    modified=deepcopy(events[:8]);modified[0]["payload"].pop("round_lot_size")
    assert "quote_size_units_unverified" in snapshot(modified)["missing"]
    market=state(events[:8]);s=feature_snapshot(market,KEY,SESSION,history(),as_of_ns=OPEN+5*MINUTE+3_000_000_000,research=True)
    assert "quote_not_fresh" in s["missing"]


def test_gap_and_amendment_uncertainty_taints_state(events):
    market=state(events[:8]);q=deepcopy(events[8]);q.update(event_type="recorder.gap",payload=dict(reason="fixture_gap",unresolved=True),source_time_ns=None)
    market.apply(q)
    assert "unresolved_capture_gap" in feature_snapshot(market,KEY,SESSION,history(),as_of_ns=q["received_ns"],research=True)["missing"]


def test_later_quote_cannot_roll_back_state_or_duplicate_liquidity(events):
    market=state(events[:10]);latest=market.instruments[KEY].quote
    q=deepcopy(events[7]);q["ledger_seq"]=11;q["event_id"]="repeat";q["received_ns"]=events[10]["received_ns"]
    q["normalized_ns"]=q["received_ns"]
    market.apply(q)
    assert market.instruments[KEY].quote==latest


def test_baseline_has_one_attempt_and_no_same_event_fill(events):
    s=snapshot(events[:8]);b=OpeningRangeBreakout([s]);now=snapshot(events[:9])
    decision=b.observe(events[8],now)
    assert b.observe(events[8],now) is None
    sim=Simulator();sim.submit(decision,market=state(events[:9]));market=state(events[:9]);sim.advance(events[8],market)
    assert not sim.positions
    with pytest.raises(ContractError,match="cohort"):
        wrong=deepcopy(events[8]);wrong["instrument_id"]="wrong"
        OpeningRangeBreakout([s]).observe(wrong,now)


def test_independent_risk_rejects_live_gaps_duplicates_and_loss(events):
    d=evaluate(events)["decisions"][0];book=RiskBook()
    assert book.reserve(d,market=state(events[:9]))["quantity"]==10
    assert "existing_exposure" in book.reserve(d,market=state(events[:9]))["rejections"]
    d=deepcopy(d);d["key"][2]="live"
    assert "no_live_authority" in RiskBook().reserve(d,market=state(events[:9]))["rejections"]
    book=RiskBook();book.realized=Decimal("-30")
    assert "daily_loss_limit" in book.reserve(d,market=state(events[:9]))["rejections"]
    d["key"][2]="fixture";d["rejections"]=["unresolved_capture_gap"]
    assert RiskBook().reserve(d,market=state(events[:9]))["quantity"]==0


def test_partial_fills_expiry_and_cancel_latency(events):
    d=evaluate(events)["decisions"][0]
    sim=Simulator(policy=ExecutionPolicy(quote_participation="0.05"));sim.submit(d,market=state(events[:9]))
    market=state(events[:9]);market.apply(events[9]);sim.advance(events[9],market)
    assert sim.positions[KEY[3]]["qty"]==5 and sim.pending[KEY[3]]["remaining"]==5
    q=deepcopy(events[10]);q["received_ns"]=d["expires_ns"]+50_000_000;q["source_time_ns"]=q["received_ns"];q["content_hash"]=digest("new-quote")
    q["normalized_ns"]=q["received_ns"]
    market.apply(q);sim.advance(q,market)
    assert sim.audit[-1]["during_cancel_race"] is True
    assert not sim.pending


def test_cancel_ack_never_forges_fill_and_residual_is_visible(events):
    d=evaluate(events)["decisions"][0];sim=Simulator();sim.submit(d,market=state(events[:9]))
    q=deepcopy(events[9]);q["received_ns"]=d["expires_ns"]+100_000_000;q["source_time_ns"]=q["received_ns"]
    q["normalized_ns"]=q["received_ns"]
    market=state(events[:9]);market.apply(q);sim.advance(q,market)
    assert not sim.positions and not sim.pending and not sim.risk.reservations
    partial=Simulator(policy=ExecutionPolicy(quote_participation="0.05"));partial.submit(d,market=state(events[:9]))
    market=state(events[:10]);partial.advance(events[9],market)
    assert partial.summary()["residual_positions"]=={KEY[3]:5}


def test_cost_budget_and_leverage_cannot_be_relaxed():
    with pytest.raises(ContractError,match="cost_allowance"):
        Simulator(policy=ExecutionPolicy(slippage_per_share="1"))
    with pytest.raises(ContractError,match="leverage"):
        RiskPolicy(gross_notional="10001")


def validation_rows():
    return [dict(session_id=f"day-{i:02d}",start_ns=i*1000+100,end_ns=i*1000+200,label_end_ns=i*1000+250,
                 available_ns=i*1000+300,evidence="fixture",policy_net_r={"orb":"1" if i%2 else "-0.5","no_trade":"0"}) for i in range(10)]


def test_walk_forward_does_not_select_on_test_or_holdout():
    rows=validation_rows();a=walk_forward(rows,policies=["orb","no_trade"],train_sessions=3,holdout_sessions=2)
    changed=deepcopy(rows);changed[3]["policy_net_r"]["orb"]="1000000";changed[-1]["policy_net_r"]["orb"]="9999999"
    b=walk_forward(changed,policies=["orb","no_trade"],train_sessions=3,holdout_sessions=2)
    assert a["folds"][0]["selected_policy"]==b["folds"][0]["selected_policy"]
    assert a["holdout_session_ids"]==["day-08","day-09"]
    assert all(not set(a["holdout_session_ids"])&set(f["training_sessions"]+f["test_sessions"]) for f in a["folds"])


def test_purge_whole_correlated_sessions_with_late_labels():
    rows=validation_rows();late=deepcopy(rows[0]);late["available_ns"]=5000;rows.append(late)
    result=walk_forward(rows,policies=["orb","no_trade"],train_sessions=2,holdout_sessions=1)
    assert "day-00" not in result["folds"][0]["training_sessions"]
    assert result==walk_forward(rows,policies=["orb","no_trade"],train_sessions=2,holdout_sessions=1)


def test_unresolved_simulation_cannot_be_validation_input():
    rows=validation_rows();rows[0]["residual_exposure"]=True
    with pytest.raises(ContractError,match="incomplete_simulation"):
        walk_forward(rows,policies=["orb","no_trade"],train_sessions=2)


def test_holdout_single_use_survives_restart_and_failed_result(tmp_path):
    path=tmp_path/"experiments.sqlite3";s=ExperimentStore(path)
    p=dict(policies=["orb","no_trade"],holdout_sessions=["day-09"])
    identifier=s.register(p,dict(origin="fixture",sha256=digest(validation_rows())))
    with pytest.raises(ContractError,match="data_mismatch"):
        evaluate_holdout(s,identifier,validation_rows()[-2:],policy="orb",session_ids=["day-09"])
    head=s.verify();s.close();s=ExperimentStore(path)
    assert s.verify()==head
    with pytest.raises(ContractError,match="already_consumed"):
        s.claim_holdout(identifier,policy="no_trade",session_ids=["day-09"])
    with pytest.raises(sqlite3.IntegrityError):s.db.execute("DELETE FROM audit")
    s.db.rollback();s.close()


def test_holdout_audit_integrity_and_no_trial_renaming_bypass(tmp_path):
    s=ExperimentStore(tmp_path/"experiment.db");manifest=dict(origin="fixture",sha256=digest(validation_rows()))
    p=dict(policies=["orb","no_trade"],holdout_sessions=["day-09"])
    a=s.register(p,manifest);result=evaluate_holdout(s,a,validation_rows()[-1:],policy="orb",session_ids=["day-09"])
    assert not result["production_eligible"]
    b=s.register({**p,"trial_name":"renamed"},manifest)
    with pytest.raises(ContractError,match="already_consumed"):s.claim_holdout(b,policy="orb",session_ids=["day-09"])
    s.db.execute("DROP TRIGGER audit_no_update");s.db.execute("UPDATE audit SET body='{}' WHERE seq=1");s.db.commit()
    with pytest.raises(ContractError,match="audit_integrity"):s.verify()
    s.close()


def test_corrections_cancels_and_revisions_preserve_prior_view(tmp_path):
    import json
    from systematic_trader.research_fixture import stamp
    with Ledger(tmp_path/"amendments",min_free_bytes=0) as ledger:
        populate(ledger);prior=list(ledger.replay(through_seq=ledger.watermark()));before=state(prior).fingerprint()
        r=Recorder(ledger,origin="fixture",clock=lambda:SESSION.close_ns,monotonic=lambda:10)
        r.ingest(json.dumps([dict(T="c",S="AAPL",x="Q",oi=1,op="100.21",os=50,oc=["@"],
            ci=2,cp="100.22",cs=55,cc=["@"],t=stamp(SESSION.close_ns),z="C")]))
        market=state(list(ledger.replay(through_seq=ledger.watermark())))
        assert list(market.instruments[KEY].trades.values())[0]["payload"]["size_shares"]==55
        assert "bar_reconciliation_required" in market.instruments[KEY].problems
        assert state(list(ledger.replay(through_seq=len(prior)))).fingerprint()==before
        r.ingest(json.dumps([dict(T="x",S="AAPL",i=2,p="100.22",s=55,x="Q",a="C",t=stamp(SESSION.close_ns),z="C")]))
        assert not state(list(ledger.replay(through_seq=ledger.watermark()))).instruments[KEY].trades
        r.ingest(json.dumps([dict(T="u",S="AAPL",o="100",h="101",l="99.90",c="100.5",v=11000,n=120,vw="100.2",t=stamp(OPEN))]))
        revised=state(list(ledger.replay(through_seq=ledger.watermark())))
        assert revised.instruments[KEY].bars[(OPEN,MINUTE)]["payload"]["high"]=="101"
        assert snapshot(prior[:8])["values"]["opening_high"]=="100.20"


def test_older_status_cannot_resume_new_halt(events):
    market=state(events[:8]);halt=deepcopy(events[1]);halt.update(ledger_seq=9,event_id="halt",content_hash=digest("halt"),
        received_ns=OPEN+5*MINUTE,normalized_ns=OPEN+5*MINUTE,source_time_ns=OPEN+MINUTE)
    halt["payload"]["status_code"]="H";market.apply(halt)
    resume=deepcopy(events[1]);resume.update(ledger_seq=10,event_id="old-resume",content_hash=digest("old-resume"),
        received_ns=OPEN+5*MINUTE,normalized_ns=OPEN+5*MINUTE)
    market.apply(resume)
    assert market.instruments[KEY].halted is True


def test_ambiguous_entry_cancel_exit_race_is_ineligible(events):
    d=evaluate(events)["decisions"][0];sim=Simulator(policy=ExecutionPolicy(quote_participation="0.05"));sim.submit(d,market=state(events[:9]))
    market=state(events[:10]);sim.advance(events[9],market)
    q=deepcopy(events[10]);q["payload"]["bid"]="99.80";q["payload"]["ask"]="99.81";q["content_hash"]=digest("stop-race")
    market.apply(q);sim.advance(q,market)
    assert sim.summary()["ambiguities"]==["entry_cancel_exit_race"]
    assert sim.summary()["critical_gaps"] and sim.summary()["residual_positions"]


def test_research_command_saves_verified_durable_artifacts(tmp_path):
    import json
    from systematic_trader.research_check import run
    output=run(tmp_path/"research-run")
    report=json.loads((tmp_path/"research-run/research-report.json").read_text())
    assert output["replay_identical"] and report["evidence"]=="fixture_only"
    assert report["audit"]["records"]==4
    assert report["health"]["market_origins"]=={"fixture":12}
    assert report["fixture_stress"]["one_second_arrival"]["net_pnl"]=="0"
    assert not report["fixture_stress"]["double_costs"]["production_eligible"]
    with pytest.raises(FileExistsError):run(tmp_path/"research-run")
