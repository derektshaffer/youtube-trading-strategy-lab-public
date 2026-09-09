"""Purged chronological research evaluation with durable, single-use holdouts."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
import sqlite3

from .events import ContractError, canonical_json, digest


def session_groups(records):
    groups={}
    for row in records:
        if row["evidence"] not in {"fixture","research_only"}:
            raise ContractError("validation_requires_research_evidence")
        if not row["start_ns"] < row["end_ns"] <= row["label_end_ns"] <= row["available_ns"]:
            raise ContractError("invalid_label_availability")
        if row.get("residual_exposure") or row.get("ambiguities") or row.get("critical_gaps"):
            raise ContractError("incomplete_simulation_outcome")
        for value in row["policy_net_r"].values():
            if not Decimal(value).is_finite():raise ContractError("invalid_validation_outcome")
        groups.setdefault(row["session_id"],[]).append(row)
    ordered=sorted(groups.values(),key=lambda g:min(r["start_ns"] for r in g))
    for previous,current in zip(ordered,ordered[1:]):
        if max(r["end_ns"] for r in previous)>min(r["start_ns"] for r in current):
            raise ContractError("overlapping_session_groups")
    return ordered


def walk_forward(records, *, policies, train_sessions, test_sessions=1, embargo_ns=0, holdout_sessions=1):
    """Expanding past-only training; correlated symbols stay in one session fold.

    Each row contains separately simulated outcomes for preregistered policies.
    Selection reads only labels available before that fold's test interval.
    Final holdout rows are excluded from every selection and OOS summary.
    """
    if any(r.get("evidence")!="fixture" for r in records):
        raise ContractError("real_outcome_selection_disabled_pending_separate_authorization")
    if min(train_sessions,test_sessions,holdout_sessions)<1 or embargo_ns<0:
        raise ContractError("invalid_walk_forward_windows")
    if not policies or len(set(policies))!=len(policies):raise ContractError("invalid_policy_set")
    groups=session_groups(records)
    development=groups[:-holdout_sessions]
    if len(development)<train_sessions+test_sessions:raise ContractError("insufficient_sessions")
    folds=[];oos=[]
    for start in range(train_sessions,len(development)-test_sessions+1,test_sessions):
        testing=development[start:start+test_sessions]
        cutoff=min(r["start_ns"] for g in testing for r in g)-embargo_ns
        training=[g for g in development[:start] if max(r["available_ns"] for r in g)<cutoff]
        if len(training)<train_sessions:continue
        train=[r for g in training for r in g];test=[r for g in testing for r in g]
        if any(set(r["policy_net_r"])!=set(policies) for r in train+test):
            raise ContractError("policy_outcome_coverage_incomplete")
        scores={p:sum(Decimal(r["policy_net_r"][p]) for r in train) for p in policies}
        selected=sorted(policies,key=lambda p:(-scores[p],p))[0]
        sessions=[dict(session_id=g[0]["session_id"],net_r=str(sum(Decimal(r["policy_net_r"][selected]) for r in g))) for g in testing]
        oos.extend(sessions)
        folds.append(dict(training_sessions=[g[0]["session_id"] for g in training],test_sessions=[g[0]["session_id"] for g in testing],
                          cutoff_ns=cutoff,selected_policy=selected,training_hash=digest(train),test_hash=digest(test),outcomes=sessions))
    if not folds:raise ContractError("no_purged_folds")
    data=dict(version="purged-walk-forward-v1",folds=folds,policy_trials=len(policies),
              holdout_session_ids=[g[0]["session_id"] for g in groups[-holdout_sessions:]],
              statistics=session_statistics(oos),production_eligible=False,execution_authority="none")
    return {**data,"result_hash":digest(data)}


def session_statistics(outcomes, *, seed=0, draws=1000):
    if not outcomes or draws<100:raise ContractError("insufficient_bootstrap_input")
    # Resample sessions, never individual correlated stock trades.
    totals={}
    for r in outcomes:totals[r["session_id"]]=totals.get(r["session_id"],Decimal(0))+Decimal(r["net_r"])
    values=list(totals.values())
    rng=random.Random(seed)
    means=sorted(sum(rng.choices(values,k=len(values)))/len(values) for _ in range(draws))
    equity=peak=drawdown=Decimal(0)
    for value in values:
        equity+=value;peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
    return dict(sessions=len(values),mean_net_r=str(sum(values)/len(values)),max_drawdown_r=str(drawdown),
                session_bootstrap_95=[str(means[int(draws*.025)]),str(means[int(draws*.975)-1])],seed=seed,
                uncertainty="insufficient_sessions" if len(values)<30 else "research_estimate_only")


class ExperimentStore:
    """Local audit chain; deleting/copying this database cannot confer authority."""
    def __init__(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.db=sqlite3.connect(path)
        path.chmod(0o600)
        self.db.execute("PRAGMA journal_mode=WAL");self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA fullfsync=ON")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY,kind TEXT NOT NULL,key TEXT NOT NULL UNIQUE,body TEXT NOT NULL,previous TEXT NOT NULL,hash TEXT NOT NULL);
          CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'append_only'); END;
          CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'append_only'); END;
        """)
        self.verify()

    def append(self,kind,key,body):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            last=self.db.execute("SELECT seq,hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            seq,previous=(last[0]+1,last[1]) if last else (1,"0"*64)
            serialized=canonical_json(body)
            checksum=digest(dict(seq=seq,kind=kind,key=key,body=serialized,previous=previous))
            self.db.execute("INSERT INTO audit VALUES(?,?,?,?,?,?)",(seq,kind,key,serialized,previous,checksum))
            self.db.commit();return checksum
        except BaseException:
            self.db.rollback();raise

    def register(self,protocol,dataset_manifest):
        if dataset_manifest.get("origin") not in {"fixture","import"} or not dataset_manifest.get("sha256"):
            raise ContractError("dataset_provenance_required")
        if not protocol.get("policies") or not protocol.get("holdout_sessions"):
            raise ContractError("incomplete_validation_protocol")
        identifier=digest(dict(protocol=protocol,dataset=dataset_manifest))
        self.append("experiment_registered",identifier,dict(protocol=protocol,dataset=dataset_manifest,execution_authority="none"))
        return identifier

    def claim_holdout(self,experiment_id,*,policy,session_ids):
        row=self.db.execute("SELECT body FROM audit WHERE key=? AND kind='experiment_registered'",(experiment_id,)).fetchone()
        if row is None:raise ContractError("experiment_not_registered")
        registration=json.loads(row[0]);protocol=registration["protocol"]
        if policy not in protocol["policies"] or session_ids!=protocol["holdout_sessions"]:
            raise ContractError("holdout_protocol_mismatch")
        # Dataset + partition, independent of trial/protocol id: no reuse by renaming a trial.
        key="holdout:"+registration["dataset"]["sha256"]
        try:
            return self.append("holdout_consumed",key,dict(experiment_id=experiment_id,policy=policy,sessions=session_ids))
        except sqlite3.IntegrityError:
            raise ContractError("holdout_already_consumed") from None

    def verify(self):
        previous="0"*64;count=0
        for seq,kind,key,body,prior,checksum in self.db.execute("SELECT * FROM audit ORDER BY seq"):
            count+=1
            if seq!=count or prior!=previous or checksum!=digest(dict(seq=seq,kind=kind,key=key,body=body,previous=prior)):
                raise ContractError("experiment_audit_integrity_failure")
            previous=checksum
        return dict(records=count,head=previous,execution_authority="none")

    def close(self):self.db.close()


def evaluate_holdout(store,experiment_id,records,*,policy,session_ids):
    if any(r.get("evidence")!="fixture" for r in records):
        raise ContractError("real_holdout_execution_disabled")
    # Consume BEFORE calculation. Interrupted or failed evaluations remain used.
    claim=store.claim_holdout(experiment_id,policy=policy,session_ids=session_ids)
    groups=session_groups(records)
    if [g[0]["session_id"] for g in groups]!=session_ids:
        raise ContractError("holdout_data_mismatch")
    outcomes=[dict(session_id=g[0]["session_id"],net_r=str(sum(Decimal(r["policy_net_r"][policy]) for r in g))) for g in groups]
    result=dict(claim_hash=claim,data_hash=digest(records),statistics=session_statistics(outcomes),
                production_eligible=False,execution_authority="none")
    store.append("holdout_result","holdout-result:"+claim,result)
    return result
