import os
import unittest
from pathlib import Path
from unittest.mock import patch

import app_access


ROOT = Path(__file__).resolve().parent


class StopCalled(RuntimeError):
    pass


class FakeStreamlit:
    def __init__(self):
        self.secrets = {}
        self.session_state = {}
        self.errors = []

    def error(self, message):
        self.errors.append(str(message))

    def stop(self):
        raise StopCalled()


class AccessControlTests(unittest.TestCase):
    def test_password_comparison_rejects_empty_and_wrong_values(self):
        self.assertFalse(app_access.access_password_matches("", "secret"))
        self.assertFalse(app_access.access_password_matches("secret", ""))
        self.assertFalse(app_access.access_password_matches("wrong", "secret"))
        self.assertTrue(app_access.access_password_matches("secret", "secret"))

    def test_missing_password_fails_closed(self):
        fake = FakeStreamlit()
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(StopCalled):
            app_access.require_app_access(fake)
        self.assertTrue(fake.errors)
        self.assertIn("locked", fake.errors[0].lower())

    def test_every_rendering_core_uses_the_shared_access_gate(self):
        protected_modules = (
            "simple_dashboard_core.py",
            "youtube_strategy_app_core.py",
            "machine_learning_lab_core.py",
            "trading_intelligence_app.py",
            "live_strategy_runner_page.py",
        )
        for name in protected_modules:
            with self.subTest(name=name):
                source = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn("require_app_access", source)

    def test_secrets_template_and_readme_require_access_password(self):
        secrets = (ROOT / "secrets.example.toml").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("APP_ACCESS_PASSWORD", secrets)
        self.assertIn("APP_ACCESS_PASSWORD", readme)
        self.assertNotIn("Upload these three files", readme)
        self.assertNotIn("does not place real or simulated brokerage orders", readme)

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
