from datetime import datetime, timedelta, timezone
import unittest

from trading_system_health import (
    cloud_job_display_state,
    configuration_checks,
    overall_system_state,
    subsystem_ready,
    workflow_run_display_state,
)


UTC = timezone.utc


def reader(values):
    def get(name, default=""):
        return values.get(name, default)
    return get


class TradingSystemHealthTests(unittest.TestCase):
    def test_ready_configuration(self):
        checks = configuration_checks(
            reader(
                {
                    "GITHUB_BACKUP_REPOSITORY": "owner/private-backup",
                    "GITHUB_BACKUP_TOKEN": "backup-token",
                    "ALPACA_API_KEY": "alpaca-key",
                    "ALPACA_SECRET_KEY": "alpaca-secret",
                    "GEMINI_API_KEY": "gemini-key",
                    "GITHUB_ACTIONS_TOKEN": "actions-token",
                }
            )
        )
        self.assertEqual(overall_system_state(checks)["state"], "READY")
        ready, blocked = subsystem_ready(checks, "stock_finder")
        self.assertTrue(ready)
        self.assertEqual(blocked, [])

    def test_missing_actions_token_is_visible(self):
        checks = configuration_checks(
            reader(
                {
                    "GITHUB_BACKUP_REPOSITORY": "owner/private-backup",
                    "GITHUB_BACKUP_TOKEN": "backup-token",
                    "ALPACA_API_KEY": "alpaca-key",
                    "ALPACA_SECRET_KEY": "alpaca-secret",
                    "GEMINI_API_KEY": "gemini-key",
                }
            )
        )
        self.assertEqual(overall_system_state(checks)["state"], "DEGRADED")
        ready, blocked = subsystem_ready(checks, "stock_finder")
        self.assertFalse(ready)
        self.assertTrue(any("GITHUB_ACTIONS_TOKEN" in item for item in blocked))

    def test_old_unclaimed_cloud_job_is_stalled(self):
        now = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)
        job = {
            "status": "queued",
            "created_at": (now - timedelta(minutes=20)).isoformat(),
            "updated_at": (now - timedelta(minutes=20)).isoformat(),
            "payload": {},
        }
        state = cloud_job_display_state(
            job,
            actions_configured=True,
            now=now,
            stalled_after_minutes=15,
        )
        self.assertEqual(state["state"], "STALLED")
        self.assertIn("No cloud worker", state["detail"])

    def test_fresh_dispatched_job_is_starting(self):
        now = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)
        job = {
            "status": "queued",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "payload": {},
        }
        state = cloud_job_display_state(
            job,
            actions_configured=True,
            launch_result={"ok": True, "detail": "accepted"},
            now=now,
        )
        self.assertEqual(state["state"], "STARTING")

    def test_running_job_is_running(self):
        state = cloud_job_display_state(
            {
                "status": "running",
                "payload": {"distributed_message": "Shard 2 is active."},
            },
            actions_configured=True,
        )
        self.assertEqual(state["state"], "RUNNING")
        self.assertIn("Shard 2", state["detail"])

    def test_smoke_run_failure_is_not_reported_as_ready(self):
        state = workflow_run_display_state(
            {"status": "completed", "conclusion": "failure"}
        )
        self.assertEqual(state["state"], "FAIL")

    def test_app_does_not_fake_online_status_or_reuse_backup_token(self):
        from pathlib import Path

        source = Path("trading_intelligence_app.py").read_text(encoding="utf-8")
        self.assertNotIn("AI RESEARCH SYSTEM <strong>ONLINE</strong>", source)
        self.assertNotIn(
            '"GITHUB_ACTIONS_TOKEN",\n                setting("GITHUB_BACKUP_TOKEN")',
            source,
        )
        self.assertIn('elif module == "System Health":', source)


if __name__ == "__main__":
    unittest.main()
