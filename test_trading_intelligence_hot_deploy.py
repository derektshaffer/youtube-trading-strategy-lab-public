"""Regression guard for Streamlit hot-deploy Finder module skew."""

from pathlib import Path
import unittest


APP_PATH = Path(__file__).with_name("trading_intelligence_app.py")


class FinderHotDeployImportTests(unittest.TestCase):
    def test_recent_behavior_switch_applies_profile_before_widget_creation(self):
        source = APP_PATH.read_text(encoding="utf-8")
        pending_index = source.index(
            'st.session_state.pop("til_pending_finder_profile", "")'
        )
        selectbox_index = source.index(
            'key="til_finder_profile"'
        )
        self.assertLess(pending_index, selectbox_index)
        self.assertIn(
            'st.session_state["til_pending_finder_profile"] = "Current Regime"',
            source,
        )
        self.assertNotIn(
            'st.session_state["til_finder_profile"] = "Current Regime"',
            source,
        )
        self.assertIn('"Recent Behavior (faster)"', source)
        self.assertIn('if str(value) == "Current Regime"', source)

    def test_completed_cloud_result_handoff_defers_both_widget_updates(self):
        source = APP_PATH.read_text(encoding="utf-8")
        pending_symbol_index = source.index(
            'st.session_state.pop("til_pending_finder_symbol", "")'
        )
        symbol_widget_index = source.index('key="til_finder_symbol"')
        self.assertLess(pending_symbol_index, symbol_widget_index)
        self.assertIn(
            'st.session_state["til_pending_finder_symbol"] = completed_symbol',
            source,
        )
        self.assertIn(
            'st.session_state["til_pending_finder_profile"] = completed_profile',
            source,
        )
        self.assertNotIn(
            'st.session_state["til_finder_symbol"] = completed_symbol',
            source,
        )

    def test_finder_controls_survive_navigation_away_from_the_page(self):
        source = APP_PATH.read_text(encoding="utf-8")
        self.assertIn(
            'st.session_state["til_finder_symbol_persisted"] = finder_symbol',
            source,
        )
        self.assertIn(
            'st.session_state["til_finder_profile_persisted"] = finder_profile_name',
            source,
        )
        self.assertIn(
            'st.session_state.get("til_finder_symbol_persisted") or "SDOT"',
            source,
        )
        self.assertIn(
            'st.session_state.get("til_finder_profile_persisted") or "Deep"',
            source,
        )

    def test_finder_recent_behavior_screen_uses_plain_language(self):
        source = APP_PATH.read_text(encoding="utf-8")
        self.assertIn(
            '"Recent Behavior"\n        if str(finder_profile.name) == "Current Regime"',
            source,
        )
        self.assertIn(
            "No {finder_profile_display} result exists yet for {finder_symbol}.",
            source,
        )
        self.assertIn(
            "Other cloud research is running in the background:",
            source,
        )
        self.assertNotIn(
            "CURRENT CONTROLS ARE A SEPARATE RESEARCH REQUEST",
            source,
        )
        self.assertIn(
            'finder_action = "Resume" if checkpoint_resumable else "Run"',
            source,
        )
        self.assertIn(
            "{finder_symbol or 'Stock'} — {finder_profile_display} here",
            source,
        )
        self.assertIn(
            "Run {finder_symbol or 'Stock'} — {finder_profile_display} in cloud",
            source,
        )

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