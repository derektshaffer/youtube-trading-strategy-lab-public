"""Regression guard for Streamlit hot-deploy Finder module skew."""

from pathlib import Path
import unittest


APP_PATH = Path(__file__).with_name("trading_intelligence_app.py")


class FinderHotDeployImportTests(unittest.TestCase):
    def test_app_falls_back_to_current_finder_source_when_cached_module_is_stale(self):
        source = APP_PATH.read_text(encoding="utf-8")
        self.assertIn(
            'any(not hasattr(_finder_module, name) for name in _required_finder_attributes)',
            source,
        )
        self.assertIn(
            '"Current Regime" not in _finder_profiles',
            source,
        )
        self.assertIn(
            '_finder_module = load_current_source_module("stock_strategy_finder")',
            source,
        )
        self.assertIn(
            'finder_evidence_verdict = _finder_module.finder_evidence_verdict',
            source,
        )
        self.assertNotIn(
            'finder_evidence_verdict = _stock_strategy_finder.finder_evidence_verdict',
            source,
        )


if __name__ == "__main__":
    unittest.main()
