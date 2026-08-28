"""Machine Learning Lab with navigation back to the main dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import machine_learning_lab_core  # noqa: F401
