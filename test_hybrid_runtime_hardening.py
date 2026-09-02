from pathlib import Path
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
