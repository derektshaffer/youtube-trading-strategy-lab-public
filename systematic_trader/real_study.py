"""Admit real datasets through existing research gates; retain blocked trials.

This is not an alternate backtester. No unqualified dataset reaches the
existing simulator, and no synthetic quote/status/identity is substituted.
"""
from dataclasses import asdict
import json
from pathlib import Path

from .data_acquisition import verified_pages
from .events import ContractError, canonical_json, digest
from .ledger import Ledger, Recorder
from .market_state import MarketState
from .momentum import MomentumPolicy
from .research_check import save
from .research_engine import BaselinePolicy, NoTrade
from .validation import ExperimentStore


def require_certified_dataset(certificate, *, verifier=None, experiment=None, evidence_directory=None, cells=None, **untrusted):
    if untrusted:
        raise ContractError('caller_certification_flags_forbidden')
    if verifier is not None:
        from .certified_runner import RunnerVerifier
        if type(verifier) is not RunnerVerifier:
            raise ContractError('trusted_runner_verifier_required')
        return verifier.verify(certificate, experiment, evidence_directory, cells)
    if not isinstance(certificate,dict):
        raise ContractError('dataset_certificate_missing_or_malformed')
    if 'signature' in certificate:
        raise ContractError('independent_dataset_certificate_authority_unconfigured')
    body={k:v for k,v in certificate.items() if k!="dataset_manifest_hash"}
    if certificate.get("dataset_manifest_hash")!=digest(body):
        raise ContractError("dataset_certificate_hash_mismatch")
    if (certificate.get("classification")!="CERTIFIED" or certificate.get("certified") is not True or
        certificate.get("blocking_findings") or certificate.get("profitability_evaluation_allowed") is not True):
        raise ContractError("real_dataset_not_certified")
    # The legacy report-only path remains blocked. A configured trusted verifier
    # above is the only new handoff; changing report flags cannot activate it.
    raise ContractError("independent_dataset_certificate_authority_unconfigured")


def run_certified_experiment(certificate, experiment, *, verifier, evidence_directory, cells, **untrusted):
    """Only certified snapshot consumption; no caller-provided executor or flags."""
    from .certified_runner import RunnerVerifier
    if untrusted or type(verifier) is not RunnerVerifier:
        raise ContractError('trusted_runner_handoff_required')
    return verifier.run(certificate, experiment, evidence_directory, cells)


def import_native_sample(root, output, *, sample_per_symbol=12):
    """Normalize a documented sample through the EXISTING recorder and state.

    Exact REST responses stay in the acquisition archive with verified hashes.
    Journal receipts explicitly contain a derived streaming-shaped JSON array.
    Import clocks remain actual import clocks, never historical first arrival.
    No historical identity is manufactured from today's catalog or ticker.
    """
    if type(sample_per_symbol) is not int or not 1<=sample_per_symbol<=100:
        raise ContractError("invalid_native_history_sample_size")
    directory=Path(output)
    if directory.exists():raise ContractError("native_history_sample_requires_new_directory")
    selected={}
    for page,meta in verified_pages(Path(root)/"bars"):
        for symbol,rows in page["bars"].items():
            needed=sample_per_symbol-len(selected.get(symbol,[]))
            if needed>0:
                selected.setdefault(symbol,[]).extend((row,meta["sha256"]) for row in rows[:needed])
    with Ledger(directory) as ledger:
        recorder=Recorder(ledger,origin="import")
        recorder.internal("recorder.lifecycle",dict(state="historical_native_sample_import",provider="alpaca",feed="sip",
            transform="rest-bars-to-stream-shape-v1",raw_archive=str(Path(root).resolve()),
            availability="actual_local_import_only",historical_first_arrival="unverified",identity="unresolved_not_inferred_from_current_catalog"))
        for symbol,rows in sorted(selected.items()):
            for row,sha in rows:
                recorder.internal("recorder.lifecycle",dict(state="derived_bar_source_link",source_sha256=sha,
                    native_row_hash=digest(row),symbol=symbol,transform="rest-bars-to-stream-shape-v1"))
                recorder.ingest(canonical_json([dict(T="b",S=symbol,**row)]))
        journal=ledger.verify()
        first=list(ledger.replay(through_seq=ledger.watermark()))
    with Ledger(directory,read_only=True) as ledger:
        second=list(ledger.replay(through_seq=ledger.watermark()))
        if first!=second or ledger.verify()!=journal:raise ContractError("real_history_sample_replay_mismatch")
        state=MarketState()
        for event in second:state.apply(event)
    return dict(real_market_bars=sum(e["event_type"]=="market.bar" for e in second),
                rejects=sum(e["event_type"]=="recorder.reject" for e in second),journal=journal,
                replay_identical=True,replay_hash=digest(second),state_hash=state.fingerprint(),
                resolved_instruments=len(state.instruments),
                historical_admission="BLOCKED_IDENTITY_AND_ORIGINAL_AVAILABILITY",evidence="real_historical_export",execution_authority="none")


def record_admission(directory, *, sample=True):
    root=Path(directory)
    certificate=json.loads((root/"dataset-certification.json").read_text())
    protocol=json.loads((root/"study-protocol.v2.json").read_text())
    if protocol["protocol_hash"]!=digest({k:v for k,v in protocol.items() if k!="protocol_hash"}):
        raise ContractError("study_protocol_hash_mismatch")
    if certificate["protocol_hash"]!=protocol["protocol_hash"]:
        raise ContractError("study_dataset_protocol_mismatch")
    # Re-read complete raw chains before registering their certificate. A report
    # copied from another dataset cannot silently stand in for these bytes.
    for kind,manifest in certificate["sources"].items():
        current=json.loads((root/kind/"complete.json").read_text())
        if current!=manifest:raise ContractError("study_source_manifest_mismatch")
        for _ in verified_pages(root/kind):pass
    try:
        require_certified_dataset(certificate)
    except ContractError as exc:
        if str(exc)!="real_dataset_not_certified":raise
    else:
        raise ContractError("certified_study_runner_not_implemented")
    output=root/"admission";output.mkdir(mode=0o700,exist_ok=False)
    store=ExperimentStore(output/"experiments.sqlite3")
    try:
        registered=dict(version=protocol["version"],policies=protocol["policies"],
                        holdout_sessions=["reserved-period:2025-05-01/2025-06-30"],protocol_hash=protocol["protocol_hash"],
                        baseline_policy=asdict(BaselinePolicy()),no_trade_version=NoTrade.version,momentum_feature_policy=asdict(MomentumPolicy()),
                        parameter_search_budget=0)
        experiment=store.register(registered,dict(origin="import",sha256=certificate["dataset_manifest_hash"]))
        metrics={name:None for name in ("trades","win_rate","average_win","average_loss","expectancy_per_trade","profit_factor",
                 "total_return","max_drawdown","sharpe","after_cost_performance","by_period","by_regime","by_ticker","parameter_sensitivity","walk_forward_oos")}
        attempts=[]
        for policy in protocol["policies"]:
            result=dict(policy=policy,experiment_id=experiment,status="NOT_EVALUATED_DATA_GATE_BLOCKED",
                        dataset_hash=certificate["dataset_manifest_hash"],reasons=certificate["blocking_findings"],metrics=metrics,
                        simulator_run=False,qualified=False,execution_authority="none")
            store.append("candidate_admission_blocked",experiment+":"+policy,result);attempts.append(result)
        replay=import_native_sample(root,output/"native-sample-journal") if sample else None
        store.append("study_admission_result",experiment+":admission",dict(blocked=True,native_sample=replay))
        data=dict(version="real-study-admission-v1",experiment_id=experiment,protocol=registered,
            dataset_manifest_hash=certificate["dataset_manifest_hash"],registered_families=len(attempts),
            performance_experiments_run=0,parameter_combinations_searched=0,strategies_qualified=0,
            discovery_result=dict(status="NOT_ESTABLISHED_DATA_GATE_BLOCKED",recall=None,precision=None,
                detection_timing=None,false_positive_rate=None,liquidity_spread_evidence="bounded samples only; see dataset audit"),
            validation_result=dict(status="NOT_EVALUATED",reason="dataset_not_certified"),
            holdout_result=dict(status="NOT_REQUESTED_NOT_EVALUATED",period=protocol["final_holdout"]),
            candidates=attempts,native_sample=replay,audit=store.verify(),evidence="real_historical_data_not_certified",
            certified=False,qualified=False,execution_authority="none")
        save(output/"study-results.json",data)
        return data
    finally:store.close()
