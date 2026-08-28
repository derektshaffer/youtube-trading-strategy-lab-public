from __future__ import annotations

import sys
import unittest
from pathlib import Path

import cloud_research_worker
import trading_research_orchestrator
from hot_deploy_imports import load_current_source_module
from youtube_strategy_engine import AppError


class StockFinderExecutorTests(unittest.TestCase):
    def test_direct_worker_recognizes_stock_finder_executor_path(self):
        with self.assertRaisesRegex(AppError, "missing a symbol"):
            cloud_research_worker.execute_job(
                None,
                None,
                {"type": "stock_finder", "payload": {}},
                "test-worker",
            )
        self.assertIn(
            "stock_finder",
            trading_research_orchestrator.SUPPORTED_RESEARCH_JOB_TYPES,
        )
        self.assertNotIn(
            "stock_finder",
            cloud_research_worker.CONTINUOUS_WORKER_JOB_TYPES,
        )


class HotDeployImportTests(unittest.TestCase):
    def test_versioned_loader_does_not_replace_public_module(self):
        original = sys.modules["trading_research_orchestrator"]
        fresh = load_current_source_module("trading_research_orchestrator")
        self.assertIs(sys.modules["trading_research_orchestrator"], original)
        self.assertIsNot(fresh, original)
        self.assertIn("stock_finder", fresh.SUPPORTED_RESEARCH_JOB_TYPES)

    def test_intelligence_page_does_not_import_or_reload_another_page_module(self):
        source = (Path(__file__).resolve().parent / "trading_intelligence_app.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("from live_strategy_runner_page import", source)
        self.assertNotIn("importlib.reload", source)


if __name__ == "__main__":
    unittest.main()
