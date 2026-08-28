import os
import unittest
from pathlib import Path
from unittest.mock import patch

import distributed_stock_finder


class DistributedStockFinderReliabilityTests(unittest.TestCase):
    def test_cloud_backup_uses_intelligence_path_from_environment(self):
        with patch.dict(
            os.environ,
            {
                "GITHUB_BACKUP_REPOSITORY": "owner/private-backup",
                "GITHUB_BACKUP_TOKEN": "token",
                "GITHUB_BACKUP_PATH": "trading-intelligence-lab/intelligence_library.json",
            },
            clear=False,
        ):
            backup = distributed_stock_finder.build_cloud_backup()
        self.assertEqual(
            backup.path,
            "trading-intelligence-lab/intelligence_library.json",
        )

    def test_explicit_artifact_path_still_wins(self):
        with patch.dict(
            os.environ,
            {
                "GITHUB_BACKUP_REPOSITORY": "owner/private-backup",
                "GITHUB_BACKUP_TOKEN": "token",
                "GITHUB_BACKUP_PATH": "trading-intelligence-lab/intelligence_library.json",
            },
            clear=False,
        ):
            backup = distributed_stock_finder.build_cloud_backup(path="custom/run.json")
        self.assertEqual(backup.path, "custom/run.json")

    def test_cloud_workflows_default_to_intelligence_library(self):
        root = Path(__file__).resolve().parent
        expected = "trading-intelligence-lab/intelligence_library.json"
        for relative in (
            ".github/workflows/distributed-stock-finder.yml",
            ".github/workflows/continuous-trading-research.yml",
            ".github/workflows/cloud-research-smoke-test.yml",
        ):
            content = (root / relative).read_text(encoding="utf-8")
            self.assertIn(expected, content, msg=f"{relative} points at the wrong durable queue.")


if __name__ == "__main__":
    unittest.main()
