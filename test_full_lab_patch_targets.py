import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WRAPPER = ROOT / "pages" / "Full_Trading_Lab.py"
CORE = ROOT / "youtube_strategy_app_core.py"


class FullTradingLabPatchCompatibilityTests(unittest.TestCase):
    def test_all_replace_once_targets_apply_in_order(self):
        wrapper_source = WRAPPER.read_text(encoding="utf-8")
        core_source = CORE.read_text(encoding="utf-8")
        tree = ast.parse(wrapper_source)

        replacements = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Name) or func.id != "replace_once":
                continue
            if len(node.args) < 4:
                continue
            old_node = node.args[1]
            new_node = node.args[2]
            label_node = node.args[3]
            if not (
                isinstance(old_node, ast.Constant)
                and isinstance(old_node.value, str)
                and isinstance(new_node, ast.Constant)
                and isinstance(new_node.value, str)
                and isinstance(label_node, ast.Constant)
                and isinstance(label_node.value, str)
            ):
                continue
            replacements.append((old_node.value, new_node.value, label_node.value))

        self.assertGreater(len(replacements), 0)
        patched = core_source
        for old, new, label in replacements:
            self.assertIn(old, patched, msg=f"Full Lab patch target missing: {label}")
            patched = patched.replace(old, new, 1)

        compile(patched, str(CORE), "exec")


if __name__ == "__main__":
    unittest.main()
