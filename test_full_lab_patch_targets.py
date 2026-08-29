import ast
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent


class ApplicationEntrypointStructureTests(unittest.TestCase):
    def test_streamlit_entrypoints_do_not_rewrite_or_exec_source(self):
        entrypoints = [
            ROOT / "youtube_strategy_app.py",
            ROOT / "pages" / "Full_Trading_Lab.py",
            ROOT / "pages" / "Live_Strategy_Runner.py",
            ROOT / "pages" / "Machine_Learning_Lab.py",
            ROOT / "pages" / "Trading_Intelligence_Lab.py",
        ]
        for path in entrypoints:
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source)
                calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
                self.assertFalse(any(isinstance(node.func, ast.Name) and node.func.id == "exec" for node in calls))
                self.assertNotIn("read_text", source)
                self.assertNotIn("source.replace", source)

    def test_side_effect_core_entrypoints_render_on_every_streamlit_rerun(self):
        entrypoints = {
            ROOT / "youtube_strategy_app.py": "trading_intelligence_app",
            ROOT / "pages" / "Full_Trading_Lab.py": "youtube_strategy_app_core",
            ROOT / "pages" / "Machine_Learning_Lab.py": "machine_learning_lab_core",
            ROOT / "pages" / "Trading_Intelligence_Lab.py": "trading_intelligence_app",
        }
        for path, module_name in entrypoints.items():
            with self.subTest(path=path.name), patch.object(runpy, "run_module") as run_module:
                runpy.run_path(str(path), run_name="__main__")
                runpy.run_path(str(path), run_name="__main__")
                self.assertEqual(run_module.call_count, 2)
                run_module.assert_called_with(module_name, run_name="__main__")

    def test_trading_intelligence_sidebar_can_always_be_reopened(self):
        source = (ROOT / "trading_glass_theme.py").read_text(encoding="utf-8")
        self.assertIn('data-testid="stSidebarCollapsedControl"', source)
        self.assertIn('data-testid="collapsedControl"', source)
        self.assertIn('data-testid="stSidebarCollapseButton"', source)
        self.assertNotIn(
            '[data-testid="stHeader"] {\n            height: 0 !important;',
            source,
        )

    def test_trading_intelligence_recovers_known_cloud_divergence_without_blocking_ui(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        self.assertIn("store.restore_cloud_backup()", source)
        self.assertIn("_til_cloud_conflict_recovered", source)
        self.assertIn(
            "Both the local Trading Lab library and the private GitHub library changed",
            source,
        )

    def test_full_lab_features_are_integrated_into_the_core_module(self):
        source = (ROOT / "youtube_strategy_app_core.py").read_text(encoding="utf-8")
        for marker in (
            "Backtest run history — reproduce an earlier test",
            "Fixed dates — reproducible",
            "backtest_run_context",
            "Actual settings this backtest will use",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)
        compile(source, str(ROOT / "youtube_strategy_app_core.py"), "exec")

    def test_deprecated_streamlit_width_argument_is_not_used(self):
        paths = [
            path
            for path in [*ROOT.glob("*.py"), *(ROOT / "pages").glob("*.py")]
            if not path.name.startswith("test_")
        ]
        for path in paths:
            with self.subTest(path=path.name):
                self.assertNotIn("use_container_width=", path.read_text(encoding="utf-8"))

    def test_full_lab_does_not_invoke_deprecated_inline_component(self):
        source = (ROOT / "youtube_strategy_app_core.py").read_text(encoding="utf-8")
        self.assertNotIn("components.html", source)
        self.assertNotIn("install_technical_tooltips", source)

    def test_every_static_page_navigation_target_exists(self):
        source_paths = [*ROOT.glob("*.py"), *(ROOT / "pages").glob("*.py")]
        targets = []
        for source_path in source_paths:
            if source_path.name.startswith("test_"):
                continue
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr != "switch_page" or not node.args:
                    continue
                if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    targets.append((source_path, node.args[0].value))
        self.assertTrue(targets)
        for source_path, target in targets:
            with self.subTest(source=source_path.name, target=target):
                self.assertTrue((ROOT / target).is_file())

    def test_machine_lab_back_navigation_precedes_credential_stop(self):
        source = (ROOT / "machine_learning_lab_core.py").read_text(encoding="utf-8")
        self.assertLess(source.index("ml_back_dashboard"), source.index("st.stop()"))


if __name__ == "__main__":
    unittest.main()
