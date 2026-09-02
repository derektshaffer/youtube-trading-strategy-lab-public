from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

import pytest

from hybrid_runtime.contracts import JobRequest
from hybrid_runtime.keychain import KeychainError, MacOSKeychain
from hybrid_runtime.service import HybridService
from hybrid_runtime.storage import HybridStore, HybridStoreError


def test_idempotency_key_reuse_with_different_request_fails_closed():
    with TemporaryDirectory() as directory:
        service = HybridService(HybridStore(Path(directory) / "hybrid.sqlite3"))
        service.submit(
            JobRequest(
                "system.health",
                {"checks": ["runtime"]},
                idempotency_key="stable-key",
            )
        )
        with pytest.raises(HybridStoreError, match="different request"):
            service.submit(
                JobRequest(
                    "system.health",
                    {"checks": ["sqlite"]},
                    idempotency_key="stable-key",
                )
            )


def test_keychain_adapter_uses_native_backend_contract(monkeypatch):
    calls = []

    class FakeBackend:
        def set_password(self, service, account, value):
            calls.append(("set", service, account, value))

        def get_password(self, service, account):
            calls.append(("get", service, account))
            return "secret-value"

        def delete_password(self, service, account):
            calls.append(("delete", service, account))

    keychain = MacOSKeychain("Trading Intelligence Test")
    monkeypatch.setattr(keychain, "_backend", lambda: FakeBackend())
    keychain.set_secret("ALPACA_API_KEY", "secret-value")
    assert keychain.get_secret("ALPACA_API_KEY") == "secret-value"
    keychain.delete_secret("ALPACA_API_KEY")
    assert calls == [
        ("set", "Trading Intelligence Test", "ALPACA_API_KEY", "secret-value"),
        ("get", "Trading Intelligence Test", "ALPACA_API_KEY"),
        ("delete", "Trading Intelligence Test", "ALPACA_API_KEY"),
    ]


def test_keychain_missing_entry_fails_closed(monkeypatch):
    class EmptyBackend:
        def get_password(self, service, account):
            return None

    keychain = MacOSKeychain()
    monkeypatch.setattr(keychain, "_backend", lambda: EmptyBackend())
    with pytest.raises(KeychainError, match="No matching credential"):
        keychain.get_secret("missing")


def test_stale_cancellation_is_terminalized_instead_of_requeued():
    with TemporaryDirectory() as directory:
        service = HybridService(HybridStore(Path(directory) / "hybrid.sqlite3"))
        job, _ = service.submit(JobRequest("system.health", {}))
        claimed = service.claim_local("dead-worker")
        assert claimed is not None
        service.cancel(job.id)
        old = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat().replace("+00:00", "Z")
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
        assert recovered.status.value == "cancelled"
        assert recovered.stage == "cancelled_after_stale_lease"
        assert recovered.completed_at


def test_tauri_origin_is_allowed_but_untrusted_web_origin_is_not():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from hybrid_runtime.api import create_app

    with TemporaryDirectory() as directory:
        service = HybridService(HybridStore(Path(directory) / "hybrid.sqlite3"))
        client = TestClient(create_app(service, expected_token="x" * 48))
        allowed = client.options(
            "/health",
            headers={
                "Origin": "tauri://localhost",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == "tauri://localhost"
        blocked = client.options(
            "/health",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        assert blocked.status_code == 400
        assert "access-control-allow-origin" not in blocked.headers
