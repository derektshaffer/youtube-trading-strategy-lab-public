from __future__ import annotations

from pathlib import Path

import hybrid_runtime.onboarding as onboarding
import hybrid_runtime.onboarding_state as onboarding_state
from hybrid_runtime.contracts import ExecutionTarget, JobRequest
from hybrid_runtime.desktop_settings import DesktopSettings, save_desktop_settings
from hybrid_runtime.router import RoutingPolicy


ROOT = Path(__file__).resolve().parent


class FakeKeychain:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def get_secret(self, account: str) -> str:
        from hybrid_runtime.keychain import KeychainError

        if account not in self.values:
            raise KeychainError("missing")
        return self.values[account]


def test_local_library_setup_distinguishes_local_readiness_from_full_hybrid_readiness(tmp_path):
    root = tmp_path / "desktop"
    root.mkdir()
    library = root / "library.json"
    library.write_text("{}", encoding="utf-8")
    save_desktop_settings(
        DesktopSettings(
            library_source="local_file",
            local_library_path=str(library),
            market_feed="iex",
        ),
        root,
    )
    partial = onboarding_state.configuration_status(
        root,
        keychain=FakeKeychain(
            {
                "alpaca-api-key": "alpaca-key",
                "alpaca-secret-key": "alpaca-secret",
            }
        ),
    )
    assert partial["library_configured"] is True
    assert partial["market_configured"] is True
    assert partial["cloud_configured"] is False
    assert partial["full_configured"] is False

    complete = onboarding_state.configuration_status(
        root,
        keychain=FakeKeychain(
            {
                "github-backup-token": "github-secret",
                "alpaca-api-key": "alpaca-key",
                "alpaca-secret-key": "alpaca-secret",
            }
        ),
    )
    assert complete["full_configured"] is True
    assert complete["market_feed"] == "iex"
    assert "github-secret" not in str(complete)
    assert "alpaca-secret" not in str(complete)


def test_verify_setup_returns_bounded_readiness_without_secret_values(tmp_path, monkeypatch):
    root = tmp_path / "desktop"
    root.mkdir()
    library = root / "library.json"
    library.write_text("{}", encoding="utf-8")
    save_desktop_settings(
        DesktopSettings(
            library_source="local_file",
            local_library_path=str(library),
        ),
        root,
    )
    secrets = FakeKeychain(
        {
            "github-backup-token": "SUPER-GITHUB-SECRET",
            "alpaca-api-key": "SUPER-ALPACA-KEY",
            "alpaca-secret-key": "SUPER-ALPACA-SECRET",
        }
    )
    monkeypatch.setattr(
        onboarding,
        "_library_access",
        lambda settings, data_dir: {
            "ready": True,
            "source": "configured_local_file",
            "strategies": 12,
            "validation_runs": 4,
            "message": "Library verified.",
        },
    )
    monkeypatch.setattr(
        onboarding,
        "_github_access",
        lambda settings, keychain: {
            "ready": True,
            "repository": settings.github_repository,
            "message": "Cloud verified.",
        },
    )
    monkeypatch.setattr(
        onboarding,
        "_market_access",
        lambda settings, keychain: {
            "ready": True,
            "feed": settings.market_feed,
            "probe_symbol": "AAPL",
            "bars": 5,
            "message": "Market verified.",
        },
    )
    progress: list[tuple[float, str]] = []
    result = onboarding.verify_setup(
        root,
        progress=lambda value, stage, _message: progress.append((value, stage)),
        cancelled=lambda: False,
        keychain=secrets,
    )
    assert result["ready"] is True
    assert result["status"] == "ready"
    assert result["checks"]["market"]["probe_symbol"] == "AAPL"
    assert result["research_only"] is True
    assert result["affects_execution"] is False
    encoded = str(result)
    assert "SUPER-GITHUB-SECRET" not in encoded
    assert "SUPER-ALPACA-KEY" not in encoded
    assert "SUPER-ALPACA-SECRET" not in encoded
    assert progress[-1][1] == "saving"


def test_onboarding_probe_is_always_local():
    request = JobRequest.from_mapping(
        {
            "job_type": "system.onboarding_probe",
            "payload": {},
            "requested_target": "auto",
        }
    )
    decision = RoutingPolicy().decide(request)
    assert decision.target == ExecutionTarget.LOCAL


def test_onboarding_ui_keeps_credentials_out_of_durable_job_payload():
    page = (ROOT / "desktop/trading_intelligence/onboarding_page.py").read_text(encoding="utf-8")
    window = (ROOT / "desktop/trading_intelligence/onboarding_window.py").read_text(encoding="utf-8")
    probe = (ROOT / "hybrid_runtime/onboarding.py").read_text(encoding="utf-8")

    assert '"_github_token"' in page
    assert '"_alpaca_api_key"' in page
    assert '"_alpaca_secret_key"' in page
    assert 'payload.pop("_github_token"' in window
    assert 'payload.pop("_alpaca_api_key"' in window
    assert 'payload.pop("_alpaca_secret_key"' in window
    assert '"job_type": "system.onboarding_probe"' in window
    assert '"payload": {}' in window
    assert "keychain.set_secret(settings.keychain_account" in window
    assert "keychain.set_secret(ALPACA_API_KEY_ACCOUNT" in window
    assert "keychain.set_secret(ALPACA_SECRET_KEY_ACCOUNT" in window
    assert '"Authorization": "Bearer " + token' in probe
    assert 'provider.bars(' in probe
    assert '["AAPL"]' in probe


def test_first_run_wrapper_skips_real_credentials_only_for_ci_smoke_and_stays_lightweight():
    source = (ROOT / "desktop/trading_intelligence/onboarding_window.py").read_text(encoding="utf-8")
    state = (ROOT / "hybrid_runtime/onboarding_state.py").read_text(encoding="utf-8")
    assert "if self.smoke:" in source
    assert "super().wait_for_health()" in source
    assert '"First-run setup · connect the library, cloud research, and market data"' in source
    assert "configuration_status(self.runtime.data_dir)" in source
    assert 'merged["market_feed"] = current.market_feed' in source
    assert "from hybrid_runtime.onboarding_state import configuration_status" in source
    assert "youtube_strategy_engine" not in source
    assert "youtube_strategy_engine" not in state
