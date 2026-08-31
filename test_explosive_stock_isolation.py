from __future__ import annotations

from pathlib import Path
import unittest

from explosive_stock_storage import DEFAULT_EXPLOSIVE_BACKUP_PATH


class ExplosiveStockIsolationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent

    def test_explosive_app_does_not_import_trading_intelligence_app(self):
        router = (self.root / "explosive_stock_app.py").read_text(encoding="utf-8")
        page = (self.root / "explosive_stock_page.py").read_text(encoding="utf-8")
        for source in (router, page):
            self.assertNotIn("import trading_intelligence_app", source)
            self.assertNotIn("from trading_intelligence_app import", source)

    def test_explosive_router_hides_shared_pages_directory(self):
        source = (self.root / "explosive_stock_app.py").read_text(encoding="utf-8")
        self.assertIn('st.navigation([page], position="hidden").run()', source)
        self.assertIn('"explosive_stock_page.py"', source)

    def test_trading_intelligence_app_does_not_import_explosive_runtime(self):
        source = (self.root / "trading_intelligence_app.py").read_text(encoding="utf-8")
        self.assertNotIn("from explosive_stock_core import", source)
        self.assertNotIn("from explosive_stock_app import", source)
        self.assertNotIn("from explosive_stock_prescreen_worker import", source)

    def test_explosive_storage_path_is_not_trading_library_path(self):
        self.assertEqual(
            DEFAULT_EXPLOSIVE_BACKUP_PATH,
            "explosive-stock-lab/prescreen_library.json",
        )
        self.assertNotIn("trading-intelligence-lab/intelligence_library.json", DEFAULT_EXPLOSIVE_BACKUP_PATH)

    def test_explosive_workflow_has_independent_concurrency_group(self):
        source = (
            self.root / ".github" / "workflows" / "explosive-stock-prescreen.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("group: explosive-stock-prescreen-writer", source)
        self.assertNotIn("group: trading-intelligence-library-writer", source)

    def test_explosive_app_uses_its_own_session_state_namespace(self):
        source = (self.root / "explosive_stock_page.py").read_text(encoding="utf-8")
        self.assertIn('st.session_state["explosive_scan_results"]', source)
        self.assertIn('st.session_state["explosive_analysis_result"]', source)
        self.assertNotIn('st.session_state["til_stock_analysis"]', source)

    def test_explosive_page_names_its_own_access_gate(self):
        source = (self.root / "explosive_stock_page.py").read_text(encoding="utf-8")
        self.assertIn('require_app_access(st, app_name="Explosive Stock Lab")', source)


if __name__ == "__main__":
    unittest.main()
