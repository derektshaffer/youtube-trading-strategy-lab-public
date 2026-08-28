"""Load one current source module without replacing Streamlit's import cache."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys
from types import ModuleType


def load_current_source_module(module_name: str) -> ModuleType:
    """Load a deploy-time compatibility copy under a versioned private name.

    Streamlit's file watcher and Python's importer both inspect ``sys.modules``.
    Removing or reloading a page dependency in place can expose a partially
    initialized module to another rerun. A versioned alias leaves the stable
    public entry untouched while executing the current file exactly once.
    """
    name = str(module_name or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ImportError(f"Unsafe module name: {name!r}")
    source_path = Path(__file__).resolve().with_name(f"{name}.py")
    if not source_path.is_file():
        raise ImportError(f"Current source for {name} was not found.")
    alias = f"_trading_hot_{name}_{source_path.stat().st_mtime_ns}"
    cached = sys.modules.get(alias)
    if isinstance(cached, ModuleType):
        return cached
    spec = importlib.util.spec_from_file_location(alias, source_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Current source for {name} could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(alias, None)
        raise
    return module
