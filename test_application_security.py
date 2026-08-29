import unittest
from pathlib import Path

import app_access


ROOT = Path(__file__).resolve().parent


class AccessControlTests(unittest.TestCase):
    def test_password_comparison_rejects_empty_and_wrong_values(self):
        self.assertFalse(app_access.access_password_matches("", "secret"))
        self.assertFalse(app_access.access_password_matches("secret", ""))
        self.assertFalse(app_access.access_password_matches("wrong", "secret"))
        self.assertTrue(app_access.access_password_matches("secret", "secret"))

    def test_remembered_access_token_expires_and_rejects_tampering(self):
        token = app_access.issue_access_token("secret", now=1_000)
        self.assertTrue(app_access.access_token_valid(token, "secret", now=1_001))
        self.assertFalse(
            app_access.access_token_valid(
                token,
                "secret",
                now=1_000 + app_access.ACCESS_TOKEN_TTL_SECONDS + 1,
            )
        )
        self.assertFalse(app_access.access_token_valid(token + "x", "secret", now=1_001))
        self.assertFalse(app_access.access_token_valid(token, "wrong", now=1_001))

    def test_remembered_access_window_is_about_twice_per_day(self):
        self.assertEqual(app_access.ACCESS_TOKEN_TTL_SECONDS, 12 * 60 * 60)

    def test_every_rendering_core_uses_the_shared_access_gate(self):
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

    def test_password_secret_and_remembered_login_are_documented(self):
        secrets = (ROOT / "secrets.example.toml").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        full_lab = (ROOT / "youtube_strategy_app_core.py").read_text(encoding="utf-8")
        access = (ROOT / "app_access.py").read_text(encoding="utf-8")
        self.assertIn("APP_ACCESS_PASSWORD", secrets)
        self.assertIn("APP_ACCESS_PASSWORD", readme)
        self.assertIn("APP_ACCESS_PASSWORD", full_lab)
        self.assertIn("APP_ACCESS_PASSWORD", access)
        self.assertIn("12 hours", readme)
        self.assertIn("12 hours", access)

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
