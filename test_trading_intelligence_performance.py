"""Performance-regression guards for the large Trading Intelligence library."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


APP_PATH = Path(__file__).with_name("trading_intelligence_app.py")


class TradingIntelligenceRenderPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_render_cache_does_not_deepcopy_large_library_on_read(self):
        load_fn = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "load_library"
        )
        deepcopies = [
            node
            for node in ast.walk(load_fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "deepcopy"
        ]
        # Exactly one copy remains, behind the explicit mutable=True copy-on-write path.
        self.assertEqual(len(deepcopies), 1)
        self.assertIn(
            "return deepcopy(value) if mutable else value",
            ast.get_source_segment(self.source, load_fn),
        )

    def test_every_non_render_load_opts_into_copy_on_write(self):
        allowed_read_only_lines = set()
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "library"
                for target in node.targets
            ):
                continue
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "load_library"
            ):
                allowed_read_only_lines.add(node.value.lineno)

        violations = []
        for node in ast.walk(self.tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "load_library"
            ):
                continue
            if node.lineno in allowed_read_only_lines:
                continue
            mutable = next(
                (kw.value for kw in node.keywords if kw.arg == "mutable"),
                None,
            )
            if not (
                isinstance(mutable, ast.Constant)
                and mutable.value is True
            ):
                violations.append(node.lineno)
        self.assertEqual(violations, [])

    def test_cold_start_uses_local_git_blob_revision_before_full_cloud_reconcile(self):
        load_fn = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "load_library"
        )
        segment = ast.get_source_segment(self.source, load_fn)
        self.assertIn("store.local_library_revision()", segment)
        self.assertIn("local_matches_remote", segment)


if __name__ == "__main__":
    unittest.main()
