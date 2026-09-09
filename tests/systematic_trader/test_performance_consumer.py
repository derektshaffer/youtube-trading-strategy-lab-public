"""Only invented certified inputs. Network unavailable; no historical outcomes."""
from copy import deepcopy
from decimal import Decimal
import json
import sqlite3
import subprocess
import sys

import pytest

from systematic_trader.certification import CertificateAuthority
from systematic_trader.certification_inputs import experiment_template, context, validate_inputs, ROOT
from systematic_trader.certified_runner import RunnerVerifier, _VerifiedInput
from systematic_trader.events import ContractError, digest
from systematic_trader.performance_inputs import write_evidence, fixture_material, normalize_receipts
from systematic_trader.performance_consumer import consume, _compute
from systematic_trader.performance_results import load_result, replay_result, result_packet, review_status


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def denied(*a,**kw):raise AssertionError('offline-only performance acceptance')
    monkeypatch.setattr(socket,'create_connection',denied)
    monkeypatch.setattr(socket.socket,'connect',denied)


def prepare(tmp_path,scenario='positive'):
    evidence=write_evidence(tmp_path/'evidence',scenario=scenario)
    authority=CertificateAuthority.initialize(tmp_path/'authority',domain='synthetic-fixture')
    cert=authority.issue(evidence);exp=authority.freeze_experiment(cert,experiment_template(cert))
    return authority,evidence,cert,exp,RunnerVerifier(authority.audit.root)


def run(p,**changes):
    a,e,c,x,r=p
    values=dict(certificate=c,experiment=x,evidence_directory=e,cells=c['body']['cells']);values.update(changes)
    return r.run(**values)


@pytest.mark.parametrize('scenario,net,trades,wins,losses',[
    ('positive','2.600',1,1,0),('losing','-5.400',1,0,1),('no_trade','0',0,0,0)])
def test_complete_exact_replay_restart_and_frozen_costs(tmp_path,scenario,net,trades,wins,losses):
    p=prepare(tmp_path,scenario);a,e,c,x,r=p;result=run(p);m=result['performance']['metrics']
    assert result['state']=='PERFORMANCE_COMPLETE' and result['performance_calculated']
    assert (m['net_pnl'],m['trades'],m['wins'],m['losses'])==(net,trades,wins,losses)
    assert Decimal(m['gross_pnl'])-Decimal(m['modeled_fees'])==Decimal(net)
    assert not result['orders_enabled'] and result['execution_authority']=='none'
    assert result['provenance']['experiment']==x and result['authorized_cells']==c['body']['cells']
    assert result['code_version']==context()
    assert m['session_statistics']['uncertainty']=='insufficient_sessions'
    assert m['percentage_return'] is None
    assert load_result(a.audit.root,x['experiment_hash'])==result
    assert replay_result(a.audit.root,x['experiment_hash'])['exact_match']
    before=a.audit.replay()
    # Separate process consumes stored raw receipts/provenance only, not input path.
    (e/'performance-evidence.json').unlink()
    script='from systematic_trader.performance_results import replay_result; import sys,json; print(json.dumps(replay_result(sys.argv[1],sys.argv[2])))'
    other=json.loads(subprocess.check_output([sys.executable,'-c',script,str(a.audit.root),x['experiment_hash']],text=True,cwd=ROOT))
    assert other['result_hash']==result['result_hash'] and other['exact_match']
    assert a.audit.replay()==before
    assert normalize_receipts(result['provenance']['data']['raw_evidence'])==result['provenance']['data']['normalized'][0]
    assert result['review_routing']['state']=='INDEPENDENT_REVIEW_REQUIRED'
    assert ('positive_net_pnl' in result['review_routing']['reasons'])==(scenario=='positive')


@pytest.mark.parametrize('field',['parameters','entry_exit_rules_hash','execution_assumptions','risk_assumptions',
    'cells','holdout','feature_definitions_hash','random_seeds','model_versions','strategy_id','consumer','result_review_policy','statistics'])
def test_spec_mutations_durable_refusal(tmp_path,field):
    p=prepare(tmp_path);x=deepcopy(p[3]);x['specification'][field]='changed'
    x['experiment_hash']=digest({k:v for k,v in x.items() if k!='experiment_hash'})
    with pytest.raises(ContractError):run(p,experiment=x)
    assert p[0].audit.replay()['records'][-1]['kind']=='runner_refused'
    assert not any(r['kind']=='execution_claimed' for r in p[0].audit.replay()['records'])


@pytest.mark.parametrize('bad',[None,{},True,{'certified':True},{'ai_agreement':True}])
def test_missing_forged_consensus_cannot_execute(tmp_path,bad):
    p=prepare(tmp_path)
    with pytest.raises(ContractError):run(p,certificate=bad)
    assert p[0].audit.replay()['records'][-1]['kind']=='runner_refused'


def test_forged_signature_invalidated_and_stale(tmp_path,monkeypatch):
    p=prepare(tmp_path);bad=deepcopy(p[2]);bad['signature']='forged'
    with pytest.raises(ContractError):run(p,certificate=bad)
    import systematic_trader.certified_runner as module
    monkeypatch.setattr(module,'context',lambda:{'changed':'code'})
    with pytest.raises(ContractError,match='stale'):run(p)
    monkeypatch.undo();p[0].invalidate(p[2]['certificate_id'],reason='fixture invalidation')
    with pytest.raises(ContractError,match='invalidated'):run(p)


@pytest.mark.parametrize('change',['ticker','identity','session','extra_cell','missing_cell','march','april','may','june'])
def test_exact_scope_and_holdout(tmp_path,change):
    p=prepare(tmp_path);cells=deepcopy(p[2]['body']['cells'])
    if change=='ticker':cells[0]['symbol']='NVDA'
    elif change=='identity':cells[0]['security_id']='fixture:NVDA'
    elif change=='session':cells[0]['session']='2025-01-09'
    elif change=='extra_cell':cells.append(deepcopy(cells[0]))
    elif change=='missing_cell':cells=[]
    else:cells[0]['session']={'march':'2025-03-03','april':'2025-04-01','may':'2025-05-01','june':'2025-06-02'}[change]
    with pytest.raises(ContractError):run(p,cells=cells)
    assert p[0].audit.replay()['records'][-1]['kind']=='runner_refused'


@pytest.mark.parametrize('change',['receipt','history','universe','session','journal','origin','missing_provenance','outcome_scenario'])
def test_raw_scope_corruption_before_issuance(tmp_path,change):
    e=write_evidence(tmp_path/'e');path=e/'performance-evidence.json';raw=json.loads(path.read_text())
    if change=='receipt':raw['receipts'].pop()
    elif change=='history':raw['history'][0]['known_ns']=raw['session']['close_ns']
    elif change=='universe':raw['universe'][0]['entries'].append({'instrument_id':'fixture:NVDA','symbol':'NVDA'})
    elif change=='session':raw['session']['session_id']='2025-06-02'
    elif change=='journal':raw['journal']['events_head']='0'*64
    elif change=='origin':raw['origin']='historical-v4'
    elif change=='missing_provenance':raw.pop('journal')
    else:raw['scenario']='losing'
    path.write_text(json.dumps(raw));a=CertificateAuthority.initialize(tmp_path/'a',domain='synthetic-fixture')
    with pytest.raises(ContractError):a.issue(e)
    assert not any(r['kind']=='certificate_issued' for r in a.audit.replay()['records'])


def test_changed_evidence_after_certification_and_duplicate_execution(tmp_path):
    p=prepare(tmp_path);path=p[1]/'performance-evidence.json';original=path.read_text()
    path.write_text(json.dumps(fixture_material('losing')))
    with pytest.raises(ContractError,match='changed'):run(p)
    path.write_text(original);run(p)
    with pytest.raises(ContractError,match='consumed'):run(p)
    with pytest.raises(ContractError,match='already_frozen'):p[0].freeze_experiment(p[2],experiment_template(p[2]))
    with pytest.raises(ContractError,match='missing'):load_result(p[0].audit.root,'different-experiment')


@pytest.mark.parametrize('bad',[None,True,{}, {'verified':True,'ai_agreement':True}])
def test_direct_consumer_and_calculation_bypass(bad):
    with pytest.raises(ContractError):consume(bad)
    with pytest.raises(ContractError):_compute(bad)


def test_unregistered_context_and_simulator_import_bypass():
    fake=object.__new__(_VerifiedInput)
    with pytest.raises(ContractError):consume(fake)
    from systematic_trader.research_engine import Simulator
    from systematic_trader.market_state import MarketState
    b=normalize_receipts(fixture_material('positive'));event=deepcopy(b['events'][0]);event['origin']='import'
    with pytest.raises(ContractError,match='trusted_runner'):Simulator().advance(event,MarketState())
    with pytest.raises(ContractError,match='trusted_runner'):Simulator().submit({'key':['alpaca','sip','import','id']})


def test_legacy_bar_engine_is_explicitly_blocked():
    from youtube_strategy_engine import run_backtest, AppError
    with pytest.raises(AppError,match='Legacy performance execution is disabled'):
        run_backtest([],{},'ASPI')


@pytest.mark.parametrize('failure',['exception','crash','mutated_context','incomplete_exposure'])
def test_claim_is_not_retryable_after_failure(tmp_path,monkeypatch,failure):
    p=prepare(tmp_path)
    import systematic_trader.performance_consumer as module
    original=module.consume
    def fail(handoff):
        if failure=='crash':raise KeyboardInterrupt('simulated crash')
        if failure=='exception':raise ContractError('fixture_consumer_failure')
        if failure=='mutated_context':
            handoff._cells=[]
            return original(handoff)
        return original(handoff)
    if failure=='incomplete_exposure':
        real_evaluate=module.evaluate_session
        def incomplete(*args,**kwargs):
            output=real_evaluate(*args,**kwargs)
            output['simulation']['residual_positions']={'fixture:AAPL':1}
            return output
        monkeypatch.setattr(module,'evaluate_session',incomplete)
    monkeypatch.setattr(module,'consume',fail)
    with pytest.raises(KeyboardInterrupt if failure=='crash' else ContractError):run(p)
    monkeypatch.setattr(module,'consume',original)
    records=p[0].audit.replay()['records']
    assert any(r['kind']=='execution_claimed' for r in records)
    assert not any(r['kind']=='execution_finished' for r in records)
    if failure!='crash':assert any(r['kind']=='execution_failed' for r in records)
    with pytest.raises(ContractError,match='consumed'):run(p)
    with pytest.raises(ContractError,match='incomplete_claim'):load_result(p[0].audit.root,p[3]['experiment_hash'])


def test_result_packet_and_review_lifecycle_offline(tmp_path):
    p=prepare(tmp_path/'run');result=run(p);a,e,c,x,r=p
    from ai_review.fixtures import adapters, validator, response
    from ai_review.gate import ReviewGate
    from ai_review.storage import ReviewStorage
    from hybrid_runtime.storage import HybridStore
    packet=result_packet(a.audit.root,x['experiment_hash'])
    assert packet['checkpoint']=='suspicious_results'
    projected=json.loads(packet['evidence'][0]['content'])
    assert projected['trade_ledger']==result['performance']['trade_ledger']
    assert projected['fill_ledger']==result['performance']['fill_ledger']
    assert projected['result_hash']==result['result_hash']
    assert review_status(a.audit.root,x['experiment_hash'])=='INDEPENDENT_REVIEW_REQUIRED'
    primary,reviewer=adapters();storage=ReviewStorage(HybridStore(tmp_path/'review.sqlite3'))
    gate=ReviewGate(storage,primary,None,validator);gate.submit(packet);gate.run(packet['artifact_id'])
    assert review_status(a.audit.root,x['experiment_hash'],gate)=='INDEPENDENT_REVIEW_PENDING'
    gate.reviewer=reviewer;gate.run(packet['artifact_id'])
    assert review_status(a.audit.root,x['experiment_hash'],gate)=='INDEPENDENT_REVIEW_CLEARED'
    # A new material critique is durable and cannot be turned into run authority.
    def material(req):
        return response(dict(request_hash=req['request_hash'],classification='material',summary='Synthetic disagreement',
            objections=[dict(severity='material',primary_claim='Generalization',objection='Synthetic only',
                disputed_evidence=['performance-result'],unresolved_question='Historical support?',required_resolution='Validated provider evidence')]))
    _,gate.reviewer=adapters(review=material,configuration_version='offline-material-review-v2');gate.run(packet['artifact_id'])
    assert review_status(a.audit.root,x['experiment_hash'],gate)=='AI_REVIEW_DISAGREEMENT'
    with pytest.raises(ContractError,match='consumed'):run(p)
    assert load_result(a.audit.root,x['experiment_hash'])==result


def test_results_are_append_only_and_audit_corruption_blocks(tmp_path):
    p=prepare(tmp_path);result=run(p);db=sqlite3.connect(p[0].audit.root/'audit.sqlite3')
    with pytest.raises(sqlite3.IntegrityError,match='append_only'):
        db.execute("UPDATE audit SET body='{}' WHERE kind='execution_finished'")
    db.close()
    head=p[0].audit.root/'audit-head.json';raw=json.loads(head.read_text());raw['head']['records']-=1;head.write_text(json.dumps(raw))
    with pytest.raises(ContractError):replay_result(p[0].audit.root,p[3]['experiment_hash'])


def test_existing_v4_is_rejected_without_reading_price_payloads(tmp_path):
    root=ROOT/'.systematic-trader/real-history/admission-v4'
    if not root.exists():pytest.skip('private canonical rejection artifacts unavailable')
    data=validate_inputs(root,'historical-v4');assert not data['cells'] and not data['admission']['admitted']
    assert data['normalized']==[]
    authority=CertificateAuthority.initialize(tmp_path/'historical',domain='historical-v4')
    with pytest.raises(ContractError,match='admission_rejected'):authority.issue(root)
    assert not any(r['kind']=='certificate_issued' for r in authority.audit.replay()['records'])


def test_legacy_real_outcome_selection_and_holdout_are_blocked():
    from systematic_trader.validation import walk_forward, evaluate_holdout
    with pytest.raises(ContractError,match='selection_disabled'):
        walk_forward([{'evidence':'research_only'}],policies=['orb'],train_sessions=1)
    with pytest.raises(ContractError,match='holdout_execution_disabled'):
        evaluate_holdout(None,'arbitrary',[{'evidence':'research_only'}],policy='orb',session_ids=['2025-06-02'])
