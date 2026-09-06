from __future__ import annotations

from pathlib import Path
import sys

from desktop.trading_intelligence.dev import SourceRuntime, seed_settings
from hybrid_runtime.keychain import MacOSKeychain


def test_dev_engine_ignores_packaged_sidecar_override(tmp_path, monkeypatch):
    executable = tmp_path / "old-packaged-engine"
    executable.touch()
    monkeypatch.setenv("TRADING_INTELLIGENCE_SIDECAR_PATH", str(executable))
    monkeypatch.setattr("desktop.trading_intelligence.runtime.available_port", lambda: 53111)
    runtime = SourceRuntime(data_dir=tmp_path / "development data")
    command = runtime._command()
    assert command[:3] == [sys.executable, "-m", "hybrid_runtime.server"]
    assert command[-1] == str(tmp_path / "development data")


def test_settings_seed_is_one_time_and_does_not_copy_jobs_or_tokens(tmp_path):
    stable, dev = tmp_path / "stable", tmp_path / "dev"
    stable.mkdir()
    dev.mkdir()
    for name in ("desktop-settings.json", "momentum-scanner-launcher.json", "hybrid.sqlite3", "local-service.token"):
        (stable / name).write_text("original")
    before = {p.name: p.read_bytes() for p in stable.iterdir()}
    seed_settings(stable, dev)
    assert {p.name for p in dev.iterdir()} == {"desktop-settings.json", "momentum-scanner-launcher.json"}
    assert (dev / "desktop-settings.json").stat().st_mode & 0o777 == 0o600
    (dev / "desktop-settings.json").write_text("dev changes")
    seed_settings(stable, dev)
    assert (dev / "desktop-settings.json").read_text() == "dev changes"
    assert {p.name: p.read_bytes() for p in stable.iterdir()} == before


class MemoryKeychain:
    def __init__(self):
        self.values = {("Trading Intelligence Lab", "test-account"): "stable-value"}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


def test_dev_reads_existing_credentials_but_writes_and_deletes_only_dev(monkeypatch):
    backend = MemoryKeychain()
    monkeypatch.setattr(MacOSKeychain, "_backend", lambda self: backend)
    monkeypatch.setenv("TRADING_INTELLIGENCE_DEV_MODE", "1")
    keychain = MacOSKeychain()
    assert keychain.get_secret("test-account") == "stable-value"
    keychain.set_secret("test-account", "dev-value")
    assert keychain.get_secret("test-account") == "dev-value"
    keychain.delete_secret("test-account")
    assert keychain.get_secret("test-account") == "stable-value"
    assert backend.values == {("Trading Intelligence Lab", "test-account"): "stable-value"}


def test_normal_keychain_keeps_original_service(monkeypatch):
    monkeypatch.delenv("TRADING_INTELLIGENCE_DEV_MODE", raising=False)
    backend = MemoryKeychain()
    monkeypatch.setattr(MacOSKeychain, "_backend", lambda self: backend)
    keychain = MacOSKeychain()
    assert keychain.service == "Trading Intelligence Lab"
    keychain.set_secret("test-account", "updated-stable-value")
    assert backend.values[("Trading Intelligence Lab", "test-account")] == "updated-stable-value"
