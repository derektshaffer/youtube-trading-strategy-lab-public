from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import subprocess
import sys
import pytest
from systematic_trader.certificate_audit import CertificateAudit
from systematic_trader.certificate_fixtures import write_evidence
from systematic_trader.certification import CertificateAuthority
from systematic_trader.certification_inputs import experiment_template, validate_inputs, ROOT
from systematic_trader.certified_runner import RunnerVerifier, _scope_probe, _VerifiedInput
from systematic_trader.events import ContractError, digest
from systematic_trader.real_study import require_certified_dataset


@pytest.fixture
def prepared(tmp_path):
    evidence=write_evidence(tmp_path/'evidence')
    authority=CertificateAuthority.initialize(tmp_path/'authority',domain='synthetic-fixture')
    certificate=authority.issue(evidence)
    experiment=authority.freeze_experiment(certificate,experiment_template(certificate))
    runner=RunnerVerifier(tmp_path/'authority')
    return authority,evidence,certificate,experiment,runner


def call(prepared, *, run=False, **changes):
    a,e,c,x,r=prepared
    values=dict(certificate=c,experiment=x,evidence_directory=e,cells=c['body']['cells'])
    values.update(changes)
    return (r.run if run else r.verify)(**values)


def test_valid_certificate_frozen_experiment_exact_scope(prepared):
    a,e,c,x,r=prepared
    assert c['body']['admission']['admitted'] and len(c['body']['cells'])==15
    assert require_certified_dataset(c,verifier=r,experiment=x,evidence_directory=e,cells=c['body']['cells'])['verified']
    result=call(prepared,run=True)
    assert result['consumed_rows']==75 and not result['performance_calculated']
    assert result['cells']==c['body']['cells'] and result['execution_authority']=='none'
    assert result['orders_enabled'] is False
    with pytest.raises(ContractError,match='consumed'):call(prepared,run=True)


def test_v4_completed_rejected_state_cannot_issue_or_run(tmp_path):
    root=ROOT/'.systematic-trader/real-history/admission-v4'
    if not root.exists():pytest.skip('canonical private V4 rejection artifact unavailable')
    a=CertificateAuthority.initialize(tmp_path/'historical-authority',domain='historical-v4')
    data=validate_inputs(root,'historical-v4')
    assert data['admission']['admitted'] is False and len(data['selection']['cells'])==15 and not data['cells']
    with pytest.raises(ContractError,match='admission_rejected'):a.issue(root)
    audit=a.audit.replay();assert not any(r['kind']=='certificate_issued' for r in audit['records'])
    r=RunnerVerifier(a.audit.root)
    with pytest.raises(ContractError):r.run(None,None,root,[])
    assert any(r['kind']=='runner_refused' for r in a.audit.replay()['records'])


@pytest.mark.parametrize('bad',[None,{},True,{'certified':True},{'body':{},'signature':'fake','certificate_id':'fake'}])
def test_missing_malformed_or_caller_certificates(prepared,bad):
    with pytest.raises(ContractError):call(prepared,certificate=bad)
    assert prepared[0].audit.replay()['records'][-1]['kind']=='runner_refused'


@pytest.mark.parametrize('field',['manifest_hash','normalized_hash','security_identities','cells','context','corrections_revisions','issuer','version','status','holdout'])
def test_modified_certificate_fields_rejected_even_after_rehash(prepared,field):
    cert=deepcopy(prepared[2]);cert['body'][field]='forged'
    cert['certificate_id']=digest(cert['body'])
    with pytest.raises(ContractError):call(prepared,certificate=cert)


def test_forged_signature_unknown_trust_root_and_fixture_domain(prepared,tmp_path):
    a,e,c,x,r=prepared
    bad=deepcopy(c);bad['signature']='not base64'
    with pytest.raises(ContractError,match='signature'):call(prepared,certificate=bad)
    other=CertificateAuthority.initialize(tmp_path/'other',domain='synthetic-fixture')
    with pytest.raises(ContractError):RunnerVerifier(other.audit.root).verify(c,x,e,c['body']['cells'])
    production=CertificateAuthority.initialize(tmp_path/'production',domain='historical-v4')
    with pytest.raises(ContractError,match='domain'):RunnerVerifier(production.audit.root).verify(c,x,e,c['body']['cells'])
    with pytest.raises(ContractError):production.issue(e)


@pytest.mark.parametrize('field',['parameters','execution_assumptions','risk_assumptions','feature_definitions_hash',
    'entry_exit_rules_hash','random_seeds','model_versions','policy_configuration_hash','cells','designation','holdout'])
def test_changed_experiment_inputs_cannot_rehash_into_clearance(prepared,field):
    x=deepcopy(prepared[3]);x['specification'][field]='modified'
    x['experiment_hash']=digest({k:v for k,v in x.items() if k!='experiment_hash'})
    with pytest.raises(ContractError,match='registered_experiment'):call(prepared,experiment=x)


@pytest.mark.parametrize('change',['symbol','session','segment','security_id','start_ns','end_ns'])
def test_unauthorized_cell_and_identity(prepared,change):
    cells=deepcopy(prepared[2]['body']['cells']);cells[0][change]='outside'
    with pytest.raises(ContractError,match='scope'):call(prepared,cells=cells)
    with pytest.raises(ContractError,match='scope'):call(prepared,cells=cells[1:])


@pytest.mark.parametrize('day',['2025-03-03','2025-04-01','2025-05-01','2025-06-02'])
def test_development_certificate_never_unlocks_holdout(prepared,day):
    cells=deepcopy(prepared[2]['body']['cells']);cells[0]['session']=day
    with pytest.raises(ContractError):call(prepared,cells=cells)
    state=prepared[2]['body']['holdout']
    assert state['may_june']=='locked' and 'not_wholly_unseen' in state['april']


@pytest.mark.parametrize('change',['bar','identity','actions','lot','status','revision'])
def test_changed_data_after_certification(prepared,change):
    p=prepared[1]/'fixture-evidence.json';raw=json.loads(p.read_text())
    if change=='bar':raw['observations'].pop()
    elif change=='revision':raw['revision']='corrected'
    else:raw['securities']['ASPI'][{'identity':'identity','actions':'actions','lot':'round_lot','status':'status'}[change]]='changed'
    p.write_text(json.dumps(raw))
    with pytest.raises(ContractError):call(prepared)
    assert not any(r['kind']=='execution_claimed' for r in prepared[0].audit.replay()['records'])


@pytest.mark.parametrize('field',['git_commit','code_hash','policy_hash','protocol_hash','execution_assumptions_hash','feature_version_hash','holdout'])
def test_stale_code_configuration_protocol_or_holdout(prepared,monkeypatch,field):
    import systematic_trader.certified_runner as module
    original=module.context()
    monkeypatch.setattr(module,'context',lambda:{**original,field:'changed'})
    with pytest.raises(ContractError,match='stale'):call(prepared)


def test_expiration_is_enforced(prepared,monkeypatch):
    import systematic_trader.certification as module
    monkeypatch.setattr(module,'now',lambda:(datetime.now(timezone.utc)+timedelta(days=2)).isoformat())
    with pytest.raises(ContractError,match='expired'):call(prepared)


def test_one_experiment_and_one_issuance_per_dataset(prepared):
    a,e,c,x,r=prepared
    with pytest.raises(ContractError,match='already_frozen'):a.freeze_experiment(c,experiment_template(c))
    with pytest.raises(ContractError,match='already_certified'):a.issue(e)
    spec=experiment_template(c);spec['parameters']['entry_window_minutes']=30
    with pytest.raises(ContractError):a.freeze_experiment(c,spec)


def test_no_caller_flags_model_agreement_or_direct_runner_bypass(prepared):
    a,e,c,x,r=prepared
    with pytest.raises(ContractError,match='flags'):require_certified_dataset(c,certified=True)
    with pytest.raises(ContractError,match='flags'):require_certified_dataset(c,ai_agreement=True)
    class Fake:
        def verify(self,*args):return {'verified':True}
    with pytest.raises(ContractError,match='trusted_runner'):require_certified_dataset(c,verifier=Fake())
    with pytest.raises(ContractError):_scope_probe({'certified':True})
    with pytest.raises(ContractError):_VerifiedInput(None,{},[],None)
    with pytest.raises(ContractError):a.issue({'admitted':True,'ai_review':'agree'})


def test_invalidation_survives_restart(prepared):
    a,e,c,x,r=prepared
    a.invalidate(c['certificate_id'],reason='A correction requires new validation')
    restarted=RunnerVerifier(a.audit.root)
    with pytest.raises(ContractError,match='invalidated'):restarted.verify(c,x,e,c['body']['cells'])
    assert any(r['kind']=='certificate_invalidated' for r in CertificateAudit(a.audit.root).replay()['records'])


def test_restart_and_independent_replay(prepared):
    a,e,c,x,r=prepared
    assert RunnerVerifier(a.audit.root).verify(c,x,e,c['body']['cells'])['verified']
    original=a.audit.replay()
    script='from systematic_trader.certificate_audit import CertificateAudit; import json,sys; print(json.dumps(CertificateAudit(sys.argv[1]).replay()))'
    replay=json.loads(subprocess.check_output([sys.executable,'-c',script,str(a.audit.root)],text=True))
    assert replay==original


def test_audit_incomplete_or_truncated_blocks_even_valid_signature(prepared):
    a,e,c,x,r=prepared
    conn=sqlite3.connect(a.audit.root/'audit.sqlite3')
    with pytest.raises(sqlite3.IntegrityError):conn.execute('DELETE FROM audit')
    conn.rollback()
    conn.execute('DROP TRIGGER audit_no_delete');conn.execute('DELETE FROM audit WHERE seq=(SELECT max(seq) FROM audit)');conn.commit();conn.close()
    with pytest.raises(ContractError,match='incomplete'):call(prepared)


def test_subset_rules_are_existing_supported_cells_not_outcome_selection(tmp_path):
    e=write_evidence(tmp_path/'e',missing=('MDXH','2025-01-08',1))
    a=CertificateAuthority.initialize(tmp_path/'a',domain='synthetic-fixture');c=a.issue(e)
    assert len(c['body']['cells'])==14
    assert not any(r['symbol']=='MDXH' and r['session']=='2025-01-08' for r in c['body']['cells'])
    assert c['body']['admission']['selection']['selection_uses_outcomes'] is False
    x=a.freeze_experiment(c,experiment_template(c));r=RunnerVerifier(a.audit.root)
    assert r.run(c,x,e,c['body']['cells'])['consumed_rows']==70


def test_interrupted_execution_claim_is_not_retried(prepared,monkeypatch):
    import systematic_trader.certified_runner as module
    def crash(handoff):raise KeyboardInterrupt()
    monkeypatch.setattr(module,'_scope_probe',crash)
    with pytest.raises(KeyboardInterrupt):call(prepared,run=True)
    a,e,c,x,r=prepared
    with pytest.raises(ContractError,match='consumed'):RunnerVerifier(a.audit.root).run(c,x,e,c['body']['cells'])

def test_missing_validation_record_blocks(prepared,monkeypatch):
    a,e,c,x,r=prepared
    original=r._audit.records
    monkeypatch.setattr(r._audit,'records',lambda store,kind=None: [] if kind=='admission_completed' else original(store,kind))
    with pytest.raises(ContractError,match='validation_record_missing'):call(prepared)


def test_incomplete_head_cannot_lose_execution_claim(prepared):
    a,e,c,x,r=prepared
    old=(a.audit.root/'audit-head.json').read_bytes()
    call(prepared,run=True)
    (a.audit.root/'audit-head.json').write_bytes(old)
    with pytest.raises(ContractError,match='incomplete'):call(prepared,run=True)


def test_whitespace_or_alias_cannot_reset_consumed_data(prepared):
    a,e,c,x,r=prepared
    call(prepared,run=True)
    file=e/'fixture-evidence.json'
    file.write_text(json.dumps(json.loads(file.read_text()),indent=3))
    replacement=a.issue(e)
    fresh=a.freeze_experiment(replacement,experiment_template(replacement))
    with pytest.raises(ContractError,match='consumed'):
        r.run(replacement,fresh,e,replacement['body']['cells'])


def test_snapshot_consumer_does_not_reopen_changed_source(prepared,monkeypatch):
    a,e,c,x,r=prepared
    import systematic_trader.certified_runner as module
    original=module._scope_probe
    def consume(snapshot):
        (e/'fixture-evidence.json').write_text('{}')
        return original(snapshot)
    monkeypatch.setattr(module,'_scope_probe',consume)
    result=call(prepared,run=True)
    assert result['consumed_rows']==75 and not result['performance_calculated']


def test_empty_subset_not_admitted(tmp_path):
    e=write_evidence(tmp_path/'e')
    path=e/'fixture-evidence.json';raw=json.loads(path.read_text());raw['observations']=[]
    path.write_text(json.dumps(raw))
    a=CertificateAuthority.initialize(tmp_path/'a',domain='synthetic-fixture')
    with pytest.raises(ContractError,match='admission_rejected'):a.issue(e)


def test_status_is_read_only_and_invalidations_visible(prepared,tmp_path):
    from systematic_trader.certificate_status import read_status
    a,e,c,x,r=prepared
    missing=tmp_path/'missing'
    assert read_status(missing)['runner_authorization']=='Blocked' and not missing.exists()
    call(prepared)
    assert read_status(a.audit.root)['runner_authorization']=='Ready'
    a.invalidate(c['certificate_id'],reason='fixture correction')
    s=read_status(a.audit.root)
    assert s['dataset_certification']=='Certificate invalid/stale' and s['runner_authorization']=='Blocked'


def test_two_consumers_cannot_both_claim(prepared):
    import concurrent.futures
    a,e,c,x,r=prepared
    def attempt():
        try:
            return RunnerVerifier(a.audit.root).run(c,x,e,c['body']['cells'])['consumed_rows']
        except ContractError:
            return 'blocked'
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:attempt(),range(2)))
    assert sorted(map(str,results))==['75','blocked']


def test_native_certificate_status_has_no_override_controls(prepared):
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication, QPushButton
    from desktop.trading_intelligence.certificate_section import CertificateSection
    from systematic_trader.certificate_status import read_status
    app=QApplication.instance() or QApplication([])
    widget=CertificateSection()
    try:
        widget.render(read_status(prepared[0].audit.root))
        assert not widget.toggle.isChecked()
        assert widget.findChildren(QPushButton)==[]
        assert 'Runner authorization: Blocked' in widget.label.text()
        assert 'No live or paper orders' in widget.label.text()
    finally:widget.close()

def test_real_study_entrypoint_consumes_only_verified_snapshot(prepared):
    from systematic_trader.real_study import run_certified_experiment
    a,e,c,x,r=prepared
    with pytest.raises(ContractError,match='handoff'):
        run_certified_experiment(c,x,verifier=r,evidence_directory=e,cells=c['body']['cells'],certified=True)
    result=run_certified_experiment(c,x,verifier=r,evidence_directory=e,cells=c['body']['cells'])
    assert result['consumed_rows']==75 and not result['performance_calculated']


def test_summary_handler_reads_fixed_authority_without_initializing(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import hybrid_runtime.engine_adapter as engine
    import hybrid_runtime.library_source as source
    monkeypatch.setenv('TRADING_INTELLIGENCE_DESKTOP_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(source,'load_library_for_job',lambda *a,**k:SimpleNamespace(library={},metadata={}))
    result=engine.research_ml_summary_handler({},lambda *a:None,lambda:False)
    assert result['dataset_certificate']['runner_authorization']=='Blocked'
    assert not (tmp_path/'systematic-certificate-authority').exists()

def test_causal_quote_predicate_is_derived_not_asserted(tmp_path):
    e=write_evidence(tmp_path/'e')
    p=e/'fixture-evidence.json';raw=json.loads(p.read_text())
    raw['quotes'][0]['available_ns']=raw['quotes'][0]['decision_ns']
    p.write_text(json.dumps(raw))
    data=validate_inputs(e,'synthetic-fixture')
    failed=next(c for c in data['selection']['cells'] if c['symbol']=='ASPI' and c['session']=='2025-01-02')
    assert 'causal_quotes' in failed['missing'] and not failed['eligible']

def test_head_write_failure_cannot_be_silently_repaired(prepared,monkeypatch):
    import systematic_trader.certificate_audit as module
    original=module._atomic
    calls=[]
    def fail_once(path,body):
        calls.append(True)
        if len(calls)==1:raise OSError('fixture head write failure')
        return original(path,body)
    monkeypatch.setattr(module,'_atomic',fail_once)
    with pytest.raises(OSError):call(prepared)
    with pytest.raises(ContractError,match='incomplete'):call(prepared)
    assert len(calls)==1
