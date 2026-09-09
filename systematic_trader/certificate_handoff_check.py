"""Offline handoff acceptance; only invented data and the saved V4 rejection."""
import argparse
import json
from pathlib import Path
from .certificate_audit import CertificateAudit
from .certificate_fixtures import write_evidence
from .certification import CertificateAuthority
from .certification_inputs import ROOT, experiment_template, validate_inputs
from .certified_runner import RunnerVerifier
from .events import ContractError,digest
from .research_check import save


def replay(directory):
    root=Path(directory)
    audits={name:CertificateAudit(root/name).replay() for name in ('fixture-authority','v4-authority')}
    return dict(audits=audits,replay_hash=digest(audits),execution_authority='none')


def run(directory):
    root=Path(directory);root.mkdir(parents=True,exist_ok=False,mode=0o700)
    evidence=write_evidence(root/'fixture-evidence')
    fixture=CertificateAuthority.initialize(root/'fixture-authority',domain='synthetic-fixture')
    certificate=fixture.issue(evidence)
    experiment=fixture.freeze_experiment(certificate,experiment_template(certificate))
    runner=RunnerVerifier(fixture.audit.root)
    verified=runner.verify(certificate,experiment,evidence,certificate['body']['cells'])
    consumed=runner.run(certificate,experiment,evidence,certificate['body']['cells'])
    try:runner.run(certificate,experiment,evidence,certificate['body']['cells'])
    except ContractError as exc:repeated=str(exc)
    else:raise AssertionError('single_use_claim_bypassed')
    fixture.invalidate(certificate['certificate_id'],reason='Offline invalidation durability fixture')
    try:RunnerVerifier(fixture.audit.root).verify(certificate,experiment,evidence,certificate['body']['cells'])
    except ContractError as exc:invalidated=str(exc)
    else:raise AssertionError('invalidation_bypassed')
    historical=CertificateAuthority.initialize(root/'v4-authority',domain='historical-v4')
    existing=ROOT/'.systematic-trader/real-history/admission-v4'
    data=validate_inputs(existing,'historical-v4')
    try:historical.issue(existing)
    except ContractError as exc:issuance_refusal=str(exc)
    else:raise AssertionError('rejected_v4_certificate_issued')
    try:RunnerVerifier(historical.audit.root).run(None,None,existing,[])
    except ContractError as exc:runner_refusal=str(exc)
    else:raise AssertionError('rejected_v4_runner_bypassed')
    audits=replay(root)
    result=dict(version='certificate-handoff-check-v1',fixture_verified=verified,
        fixture_consumption=consumed,repeated_refusal=repeated,invalidation_refusal=invalidated,
        v4=dict(tier1='REJECTED',eligible=len(data['cells']),requested=len(data['selection']['cells']),
            issuance_refusal=issuance_refusal,runner_refusal=runner_refusal),
        audit_replay_hash=audits['replay_hash'],
        audit_counts={name:len(value['records']) for name,value in audits['audits'].items()},
        real_data_performance_experiments=0,orders='DISABLED')
    save(root/'fixture-certificate.json',certificate);save(root/'fixture-experiment.json',experiment)
    save(root/'replay.json',audits);save(root/'result.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory');parser.add_argument('--replay',action='store_true')
    args=parser.parse_args()
    print(json.dumps(replay(args.directory) if args.replay else run(args.directory),indent=2))


if __name__=='__main__':main()
