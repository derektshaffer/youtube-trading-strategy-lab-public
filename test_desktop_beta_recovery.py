from __future__ import annotations

from pathlib import Path

from hybrid_runtime.desktop_settings import DesktopSettings, save_desktop_settings
from hybrid_runtime.onboarding_state import (
    configuration_status,
    mark_setup_pending,
    mark_setup_probe_result,
)


ROOT = Path(__file__).resolve().parent


class FakeKeychain:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = dict(values)

    def get_secret(self, account: str) -> str:
        from hybrid_runtime.keychain import KeychainError

        if account not in self.values:
            raise KeychainError("missing")
        return self.values[account]


def configured_root(tmp_path: Path) -> tuple[Path, FakeKeychain]:
    root = tmp_path / "desktop"
    root.mkdir()
    library = root / "library.json"
    library.write_text("{}", encoding="utf-8")
    save_desktop_settings(
        DesktopSettings(
            library_source="local_file",
            local_library_path=str(library),
            github_repository="owner/private-library",
            github_branch="main",
            github_path="trading-intelligence-lab/intelligence_library.json",
            market_feed="sip",
        ),
        root,
    )
    keychain = FakeKeychain(
        {
            "github-backup-token": "github-secret",
            "alpaca-api-key": "alpaca-key",
            "alpaca-secret-key": "alpaca-secret",
        }
    )
    return root, keychain


def test_partial_setup_verification_preserves_only_passing_capabilities(tmp_path):
    root, keychain = configured_root(tmp_path)
    mark_setup_probe_result(
        root,
        {
            "library": {"ready": True},
            "cloud": {"ready": False},
            "market": {"ready": True},
        },
    )
    status = configuration_status(root, keychain=keychain)
    assert status["setup_verification"] == "pending"
    assert status["launch_ready"] is False
    assert status["capabilities"] == {
        "library": True,
        "cloud": False,
        "market": True,
    }
    assert status["library_verified"] is True
    assert status["cloud_verified"] is False
    assert status["market_verified"] is True


def test_configuration_change_invalidates_all_previous_capability_proofs(tmp_path):
    root, keychain = configured_root(tmp_path)
    mark_setup_probe_result(
        root,
        {"library": True, "cloud": True, "market": True},
    )
    before = configuration_status(root, keychain=keychain)
    assert before["launch_ready"] is True

    settings = DesktopSettings(
        library_source="local_file",
        local_library_path=str(root / "library.json"),
        github_repository="owner/private-library",
        github_branch="main",
        github_path="trading-intelligence-lab/intelligence_library.json",
        market_feed="iex",
    )
    save_desktop_settings(settings, root)
    after = configuration_status(root, keychain=keychain)
    assert after["setup_verification"] == "pending"
    assert after["launch_ready"] is False
    assert after["capabilities"] == {
        "library": False,
        "cloud": False,
        "market": False,
    }


def test_mark_setup_pending_clears_partial_capability_proofs(tmp_path):
    root, keychain = configured_root(tmp_path)
    mark_setup_probe_result(root, {"library": True, "cloud": False, "market": True})
    assert configuration_status(root, keychain=keychain)["market_verified"] is True
    mark_setup_pending(root)
    status = configuration_status(root, keychain=keychain)
    assert status["capabilities"] == {
        "library": False,
        "cloud": False,
        "market": False,
    }


def test_recovery_wrapper_gates_real_work_but_bypasses_ci_smoke():
    wrapper = (ROOT / "desktop/trading_intelligence/beta_recovery_window.py").read_text(
        encoding="utf-8"
    )
    ui = (ROOT / "desktop/trading_intelligence/ui.py").read_text(encoding="utf-8")

    assert "from .beta_recovery_window import MainWindow" in ui
    assert "if self.smoke:" in wrapper
    assert '"library": True, "cloud": True, "market": True' in wrapper
    assert 'self._require_capabilities(("market",), "Quick Analysis")' in wrapper
    assert 'self._require_capabilities(("library", "cloud"), "Stock Strategy Finder")' in wrapper
    assert 'self._require_capabilities(("library", "cloud"), "Strategy Lab")' in wrapper
    assert 'self._require_capabilities(("library", "cloud"), "Strict cloud validation")' in wrapper
    assert 'self._require_capabilities(("library",), "Results")' in wrapper
    assert 'self._require_capabilities(("library",), "Research + ML")' in wrapper
    assert "mark_setup_probe_result" in wrapper
    assert "show_page(self.stack.indexOf(self.onboarding))" in wrapper
