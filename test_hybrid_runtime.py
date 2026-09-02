from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading

import pytest

from hybrid_runtime.contracts import (
    ExecutionTarget,
    JobRequest,
    JobStatus,
    transition_allowed,
)
from hybrid_runtime.engine_adapter import system_health_handler
from hybrid_runtime.keychain import KeychainUnavailable, MacOSKeychain
from hybrid_runtime.router import RoutingPolicy
from hybrid_runtime.security import (
    ServiceSecurityError,
    assert_loopback_host,
    generate_service_token,
    token_matches,
    write_private_token_file,
)
from hybrid_runtime.service import HybridService
from hybrid_runtime.storage import HybridStore, InvalidJobTransition
from hybrid_runtime.worker import LocalWorker


def build_service(directory: str) -> HybridService:
    return HybridService(HybridStore(Path(directory) / "hybrid.sqlite3"))


def test_request_fingerprint_is_deterministic_and_data_sensitive():
    first = JobRequest(
        "analysis.stock",
        {"symbol": "SDOT", "history_days": 30},
        code_fingerprint="abc",
    )
    reordered = JobRequest(
        "analysis.stock",
        {"history_days": 30, "symbol": "SDOT"},
        code_fingerprint="abc",
    )
    changed = JobRequest(
        "analysis.stock",
        {"symbol": "SDOT", "history_days": 31},
        code_fingerprint="abc",
    )
    assert first.fingerprint() == reordered.fingerprint()
    assert first.fingerprint() != changed.fingerprint()


def test_request_rejects_non_finite_payloads():
    with pytest.raises(ValueError):
        JobRequest("analysis.stock", {"bad": float("nan")})


def test_router_keeps_fast_analysis_local_and_heavy_search_cloud():
    policy = RoutingPolicy()
    quick = policy.decide(JobRequest("analysis.stock", {"estimated_seconds": 3}))
    deep = policy.decide(
        JobRequest(
            "backtest.custom",
            {"configurations": 42_000, "continue_after_app_exit": True},
        )
    )
    forced = policy.decide(JobRequest("strategy.very_deep", {}, requested_target="local"))
    assert quick.target == ExecutionTarget.LOCAL
    assert deep.target == ExecutionTarget.CLOUD
    assert "42,000 configurations" in deep.reason
    assert forced.target == ExecutionTarget.CLOUD


def test_explicit_cloud_override_is_visible():
    decision = RoutingPolicy().decide(
        JobRequest("analysis.stock", {}, requested_target=ExecutionTarget.CLOUD)
    )
    assert decision.target == ExecutionTarget.CLOUD
    assert decision.automatic is False
    assert "explicitly" in decision.reason.lower()


def test_store_submission_is_idempotent_and_active_requests_are_deduplicated():
    with TemporaryDirectory() as directory:
        service = build_service(directory)
        request = JobRequest(
            "system.health",
            {"checks": ["runtime"]},
            idempotency_key="same-request",
        )
        first, created_first = service.submit(request)
        second, created_second = service.submit(request)
        assert created_first is True
        assert created_second is False
        assert first.id == second.id

        active_request = JobRequest("system.health", {"checks": ["sqlite"]})
        third, created_third = service.submit(active_request)
        fourth, created_fourth = service.submit(active_request)
        assert created_third is True
        assert created_fourth is False
        assert third.id == fourth.id


def test_claim_is_transactional_and_prefers_higher_priority():
    with TemporaryDirectory() as directory:
        service = build_service(directory)
        low, _ = service.submit(JobRequest("system.health", {}, priority=1))
        high, _ = service.submit(
            JobRequest("system.health", {"checks": ["high"]}, priority=20)
        )
        claimed = service.claim_local("worker-a")
        assert claimed is not None
        assert claimed.id == high.id
        assert service.claim_local("worker-b").id == low.id
        assert service.claim_local("worker-c") is None


def test_concurrent_workers_cannot_claim_the_same_job():
    with TemporaryDirectory() as directory:
        service = build_service(directory)
        job, _ = service.submit(JobRequest("system.health", {}))
        barrier = threading.Barrier(3)
        claimed_ids: list[str | None] = []
        lock = threading.Lock()

        def claim(worker_id: str) -> None:
            barrier.wait()
            claimed = service.claim_local(worker_id)
            with lock:
                claimed_ids.append(claimed.id if claimed else None)

        threads = [
            threading.Thread(target=claim, args=("worker-a",)),
            threading.Thread(target=claim, args=("worker-b",)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()

        assert claimed_ids.count(job.id) == 1
        assert claimed_ids.count(None) == 1


def test_progress_cannot_regress_and_terminal_jobs_cannot_reopen():
    with TemporaryDirectory() as directory:
        service = build_service(directory)
        job, _ = service.submit(JobRequest("system.health", {}))
        claimed = service.claim_local("worker")
        assert claimed and claimed.id == job.id
        service.store.transition_job(
            job.id,
            JobStatus.PREPARING_FEATURES,
            progress=0.6,
            worker_id="worker",
        )
        with pytest.raises(InvalidJobTransition):
            service.store.transition_job(
                job.id,
                JobStatus.SEARCHING,
                progress=0.4,
                worker_id="worker",
            )
        service.complete(job.id, {"ok": True}, worker_id="worker")
        with pytest.raises(InvalidJobTransition):
            service.store.transition_job(job.id, JobStatus.QUEUED)


def test_transition_contract_rejects_stage_rewind():
    assert transition_allowed(JobStatus.SEARCHING, JobStatus.OPTIMIZING)
    assert not transition_allowed(JobStatus.OPTIMIZING, JobStatus.SEARCHING)
    assert not transition_allowed(JobStatus.COMPLETE, JobStatus.QUEUED)


def test_local_worker_executes_handler_and_persists_events():
    with TemporaryDirectory() as directory:
        service = build_service(directory)
        job, _ = service.submit(JobRequest("system.health", {"checks": ["runtime"]}))
        worker = LocalWorker(
            service,
            worker_id="test-worker",
            handlers={"system.health": system_health_handler},
        )
        assert worker.run_once() is True
        finished = service.get(job.id)
        assert finished.status == JobStatus.COMPLETE
        assert finished.result["status"] == "ok"
        events = service.events(job.id)
        assert [event["status"] for event in events] == [
            "queued",
            "claimed",
            "preparing_features",
            "complete",
        ]


def test_local_worker_fails_closed_for_unknown_handler():
    with TemporaryDirectory() as directory:
        service = build_service(directory)
        job, _ = service.submit(JobRequest("custom.local", {}))
        worker = LocalWorker(service, worker_id="test-worker", handlers={})
        assert worker.run_once() is True
        failed = service.get(job.id)
        assert failed.status == JobStatus.FAILED
        assert failed.error["type"] == "UnsupportedJob"


def test_cancel_queued_job_is_terminal_without_claiming():
    with TemporaryDirectory() as directory:
        service = build_service(directory)
        job, _ = service.submit(JobRequest("system.health", {}))
        cancelled = service.cancel(job.id)
        assert cancelled.status == JobStatus.CANCELLED
        assert service.claim_local("worker") is None


def test_stale_worker_lease_is_requeued_without_losing_progress():
    with TemporaryDirectory() as directory:
        service = build_service(directory)
        job, _ = service.submit(JobRequest("system.health", {}))
        claimed = service.claim_local("dead-worker")
        assert claimed is not None
        service.store.transition_job(
            job.id,
            JobStatus.PREPARING_FEATURES,
            progress=0.4,
            worker_id="dead-worker",
        )
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        connection = sqlite3.connect(service.store.path)
        try:
            connection.execute(
                "UPDATE jobs SET heartbeat_at = ?, claimed_at = ?, updated_at = ? WHERE id = ?",
                (old, old, old, job.id),
            )
            connection.commit()
        finally:
            connection.close()
        assert service.store.requeue_stale_jobs(stale_after_seconds=60) == 1
        recovered = service.get(job.id)
        assert recovered.status == JobStatus.QUEUED
        assert recovered.progress == pytest.approx(0.4)
        assert recovered.worker_id is None


def test_cache_metadata_round_trip_and_expiry():
    with TemporaryDirectory() as directory:
        store = HybridStore(Path(directory) / "hybrid.sqlite3")
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        entry = store.upsert_cache_entry(
            cache_key="bars:SDOT:1Min",
            namespace="bars",
            fingerprint="feed=sip|raw|2026-09-01",
            artifact_path="/tmp/sdot.parquet",
            byte_size=1234,
            metadata={"symbol": "SDOT"},
            expires_at=future,
        )
        assert entry["metadata"]["symbol"] == "SDOT"
        assert store.get_cache_entry("bars:SDOT:1Min")["byte_size"] == 1234

        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        store.upsert_cache_entry(
            cache_key="expired",
            namespace="bars",
            fingerprint="old",
            artifact_path="/tmp/old",
            expires_at=past,
        )
        assert store.get_cache_entry("expired") is None
        assert store.delete_expired_cache_entries() == 1


def test_loopback_security_and_private_token_file():
    token = generate_service_token()
    assert token_matches("Bearer " + token, token)
    assert not token_matches("Bearer wrong", token)
    assert assert_loopback_host("127.0.0.1") == "127.0.0.1"
    with pytest.raises(ServiceSecurityError):
        assert_loopback_host("0.0.0.0")
    with TemporaryDirectory() as directory:
        path = write_private_token_file(Path(directory) / "token", token)
        assert path.read_text(encoding="utf-8") == token
        assert path.stat().st_mode & 0o777 == 0o600


def test_keychain_fails_explicitly_off_macos(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    with pytest.raises(KeychainUnavailable):
        MacOSKeychain().get_secret("ALPACA_API_KEY")
