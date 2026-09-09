"""Offline acceptance artifacts, then independent stored-provenance replay.

Run: python -m systematic_trader.performance_check NEW_OUTPUT_DIRECTORY
Replay: python -m systematic_trader.performance_check --replay OUTPUT_DIRECTORY
"""
import argparse
import json
from pathlib import Path

from .certification import CertificateAuthority
from .certification_inputs import ROOT, experiment_template, validate_inputs
from .certified_runner import RunnerVerifier
from .events import ContractError, digest
from .performance_inputs import write_evidence
from .performance_results import replay_result, result_packet
from .research_check import save


def run(directory):
    root=Path(directory);root.mkdir(parents=True,exist_ok=False,mode=0o700);results=[]
    for scenario in ('positive','losing','no_trade'):
        base=root/scenario;source=write_evidence(base/'evidence',scenario=scenario)
        authority=CertificateAuthority.initialize(base/'authority',domain='synthetic-fixture')
        certificate=authority.issue(source)
        experiment=authority.freeze_experiment(certificate,experiment_template(certificate))
        runner=RunnerVerifier(authority.audit.root)
        result=runner.run(certificate,experiment,source,certificate['body']['cells'])
        save(base/'result.json',result);save(base/'review-packet.json',result_packet(authority.audit.root,experiment['experiment_hash']))
        replay=replay_result(authority.audit.root,experiment['experiment_hash'])
        results.append(dict(scenario=scenario,experiment_id=experiment['experiment_hash'],result_hash=result['result_hash'],
            metrics=result['performance']['metrics'],replay=replay,audit=authority.audit.replay()['replay_hash']))
    rejected=validate_inputs(ROOT/'.systematic-trader/real-history/admission-v4','historical-v4')
    authority=CertificateAuthority.initialize(root/'historical-rejection-authority',domain='historical-v4')
    try:authority.issue(ROOT/'.systematic-trader/real-history/admission-v4')
    except ContractError as exc:
        if str(exc)!='deterministic_admission_rejected':raise
    else:raise AssertionError('historical certificate issued')
    report=dict(version='performance-consumer-check-v1',scenarios=results,
        historical_tier1='REJECTED',historical_admitted_cells=len(rejected['cells']),real_performance_experiments=0,
        ai_api_calls=0,provider_requests_changed=False,execution_authority='none',orders_enabled=False)
    save(root/'check.json',report)
    return report


def replay(directory):
    root=Path(directory);report=json.loads((root/'check.json').read_text());results=[]
    for item in report['scenarios']:
        authority=root/item['scenario']/'authority'
        result=replay_result(authority,item['experiment_id'])
        if result!=item['replay']:raise ContractError('independent_performance_replay_mismatch')
        results.append(result)
    return dict(exact_match=True,scenarios=results,replay_hash=digest(results),orders_enabled=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay',action='store_true');parser.add_argument('directory');args=parser.parse_args()
    print(json.dumps(replay(args.directory) if args.replay else run(args.directory),sort_keys=True))
