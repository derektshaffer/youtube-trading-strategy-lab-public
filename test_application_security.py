import unittest
from pathlib import Path

import app_access


ROOT = Path(__file__).resolve().parent


class FakeStreamlit:
    def __init__(self):
        self.secrets = {}
        self.session_state = {}


class AccessControlTests(unittest.TestCase):
    def test_shared_access_hook_allows_immediate_render(self):
        fake = FakeStreamlit()
        self.assertIsNone(app_access.require_app_access(fake))

    def test_every_rendering_core_uses_the_shared_access_hook(self):
        modules = (
            "simple_dashboard_core.py",
            "youtube_strategy_app_core.py",
            "machine_learning_lab_core.py",
            "trading_intelligence_app.py",
            "live_strategy_runner_page.py",
        )
        for name in modules:
            with self.subTest(name=name):
                source = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn("require_app_access", source)

    def test_password_secret_is_not_required_or_documented(self):
        secrets = (ROOT / "secrets.example.toml").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        full_lab = (ROOT / "youtube_strategy_app_core.py").read_text(encoding="utf-8")
        access = (ROOT / "app_access.py").read_text(encoding="utf-8")
        self.assertNotIn("APP_ACCESS_PASSWORD", secrets)
        self.assertNotIn("APP_ACCESS_PASSWORD", readme)
        self.assertNotIn("APP_ACCESS_PASSWORD", full_lab)
        self.assertNotIn("APP_ACCESS_PASSWORD", access)
        self.assertIn("does not use a separate in-app password", readme)

    def test_app_copy_does_not_deny_its_paper_order_capability(self):
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                ROOT / "README.md",
                ROOT / "simple_dashboard_core.py",
                ROOT / "live_strategy_runner_page.py",
                ROOT / "youtube_strategy_app_core.py",
            )
        ).lower()
        self.assertNotIn("never places live or paper brokerage orders", sources)


if __name__ == "__main__":
    unittest.main()
