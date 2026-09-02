from __future__ import annotations

from pathlib import Path

from hybrid_runtime.desktop_settings import DesktopSettings, save_desktop_settings
from hybrid_runtime.storage import HybridStore
from hybrid_runtime.system_health_summary import build_system_health_summary


class FakeKeychain:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def get_secret(self, account: str) -> str:
        from hybrid_runtime.keychain import KeychainError

        if account not in self.values:
            raise KeychainError("missing")
        return self.values[account]


def initialized_data_dir(tmp_path: Path) -> Path:
    root = tmp_path / "desktop-data"
    root.mkdir()
    HybridStore(root / "hybrid.sqlite3").list_jobs(limit=10)
    return root


def test_local_library_can_be_healthy_without_github_credential(tmp_path):
    root = initialized_data_dir(tmp_path)
    library_path = root / "library.json"
    library_path.write_text("{}", encoding="utf-8")
    save_desktop_settings(
        DesktopSettings(
            library_source="local_file",
            local_library_path=str(library_path),
            market_feed="sip",
        ),
        root,
    )
    keychain = FakeKeychain(
        {
            "alpaca-api-key": "alpaca-key-secret-value",
            "alpaca-secret-key": "alpaca-secret-value",
        }
    )
    result = build_system_health_summary(
        root,
        library_summary={
            "source": "configured_local_file",
            "source_detail": str(library_path),
        },
        runtime_health={"status": "ok"},
        keychain=keychain,
    )
    assert result["status"] == "ready"
    assert result["connection"]["library_mode"] == "local"
    assert result["connection"]["github_required_for_library"] is False
    assert result["checks"]["library_connection"] is True
    assert result["checks"]["github_library_credential"] is False
    assert result["checks"]["runtime_service"] is True
    assert "alpaca-key-secret-value" not in str(result)
    assert "alpaca-secret-value" not in str(result)


def test_cached_github_library_without_credential_is_readable_but_needs_attention(tmp_path):
    root = initialized_data_dir(tmp_path)
    save_desktop_settings(
        DesktopSettings(library_source="github_backup", market_feed="iex"),
        root,
    )
    keychain = FakeKeychain(
        {
            "alpaca-api-key": "key",
            "alpaca-secret-key": "secret",
        }
    )
    result = build_system_health_summary(
        root,
        library_summary={
            "source": "local_cache",
            "source_detail": str(root / "library-cache" / "strategy_library.json"),
            "warning": "GitHub refresh unavailable",
        },
        runtime_health={"status": "ok"},
        keychain=keychain,
    )
    assert result["status"] == "attention"
    assert result["checks"]["library_readable"] is True
    assert result["checks"]["library_connection"] is False
    assert result["connection"]["library_mode"] == "github"
    assert result["connection"]["github_required_for_library"] is True


def test_live_sidecar_health_is_required(tmp_path):
    root = initialized_data_dir(tmp_path)
    library_path = root / "library.json"
    library_path.write_text("{}", encoding="utf-8")
    save_desktop_settings(
        DesktopSettings(
            library_source="local_file",
            local_library_path=str(library_path),
        ),
        root,
    )
    result = build_system_health_summary(
        root,
        library_summary={"source": "configured_local_file"},
        runtime_health={"status": "failed"},
        keychain=FakeKeychain(
            {
                "alpaca-api-key": "key",
                "alpaca-secret-key": "secret",
            }
        ),
    )
    assert result["status"] == "attention"
    assert result["checks"]["runtime_service"] is False
    assert "runtime_service" in result["required_checks"]


def test_market_cache_is_informational_not_required(tmp_path):
    root = initialized_data_dir(tmp_path)
    library_path = root / "library.json"
    library_path.write_text("{}", encoding="utf-8")
    save_desktop_settings(
        DesktopSettings(
            library_source="local_file",
            local_library_path=str(library_path),
        ),
        root,
    )
    result = build_system_health_summary(
        root,
        library_summary={"source": "configured_local_file"},
        runtime_health={"status": "ok"},
        keychain=FakeKeychain(
            {
                "alpaca-api-key": "key",
                "alpaca-secret-key": "secret",
            }
        ),
    )
    assert result["checks"]["market_cache_present"] is False
    assert "market_cache_present" not in result["required_checks"]
    assert result["status"] == "ready"
