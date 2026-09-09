"""Persist a reproducible engineering exercise; no performance certification."""
import os
from pathlib import Path

from .events import canonical_json, digest
from .ledger import Ledger
from .observability import capture_health
from .research_fixture import populate, evaluate, SESSION
from .research_engine import ExecutionPolicy
from .service import source_manifest
from .validation import ExperimentStore, evaluate_holdout, walk_forward


def validation_fixture():
    # These prescribed outcomes test validation mechanics independently of fills.
    # They are not ten actual market sessions or claimed strategy performance.
    return [dict(session_id=f"fixture-day-{i:02d}",start_ns=i*1000+100,end_ns=i*1000+200,
                 label_end_ns=i*1000+250,available_ns=i*1000+300,evidence="fixture",
                 policy_net_r={"orb":"1" if i%2 else "-0.5","no_trade":"0"}) for i in range(10)]


def save(path,data):
    path=Path(path)
    with path.open("x",encoding="utf-8") as handle:
        handle.write(canonical_json(data)+"\n");handle.flush();os.fsync(handle.fileno())
    path.chmod(0o600)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def run(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False,mode=0o700)
    rows=validation_fixture();manifest=dict(origin="fixture",sha256=digest(rows))
    protocol=dict(version="validation-fixture-protocol-v1",policies=["orb","no_trade"],train_sessions=3,
                  holdout_sessions=["fixture-day-08","fixture-day-09"],embargo_ns=0)
    store=ExperimentStore(directory/"experiments.sqlite3")
    try:
        experiment=store.register(protocol,manifest)  # Before selection or held-out evaluation.
        with Ledger(directory/"fixture-journal",min_free_bytes=0) as ledger:
            journals=populate(ledger)
            first=evaluate(ledger.replay(through_seq=ledger.watermark()))
        with Ledger(directory/"fixture-journal",read_only=True) as ledger:
            second=evaluate(ledger.replay(through_seq=ledger.watermark()))
            if first!=second or journals!=ledger.verify():raise RuntimeError("research_replay_mismatch")
            health=capture_health(ledger,now_ns=SESSION.close_ns)
            stress={name:evaluate(ledger.replay(through_seq=ledger.watermark()),execution_policy=policy)["simulation"]
                    for name,policy in {
                        "double_costs":ExecutionPolicy(fee_per_share="0.01",slippage_per_share="0.02"),
                        "one_second_arrival":ExecutionPolicy(arrival_latency_ns=1_000_000_000)}.items()}
        walk=walk_forward(rows,policies=protocol["policies"],train_sessions=3,holdout_sessions=2)
        # Policy frozen from development only, before the single holdout claim.
        chosen=walk["folds"][-1]["selected_policy"]
        held=evaluate_holdout(store,experiment,rows[-2:],policy=chosen,session_ids=protocol["holdout_sessions"])
        store.append("research_replay_verified","fixture-replay",dict(result_hash=first["result_hash"],journal=journals))
        report=dict(version="engineering-research-check-v1",evidence="fixture_only",source=source_manifest(),
                    replay_identical=True,fixture_session=first,validation_fixture=walk,holdout_fixture=held,
                    health=health,fixture_stress=stress,audit=store.verify(),production_eligible=False,execution_authority="none")
        save(directory/"validation-input.fixture.json",rows)
        save(directory/"research-report.json",report)
        return dict(output_dir=str(directory.resolve()),report=str((directory/"research-report.json").resolve()),
                    replay_identical=True,research_result_hash=first["result_hash"],journal=journals,
                    audit=store.verify(),evidence="fixture_only",production_eligible=False,execution_authority="none")
    finally:store.close()
