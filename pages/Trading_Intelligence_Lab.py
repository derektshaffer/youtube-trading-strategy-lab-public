"""Trading Intelligence Lab page for the existing Streamlit deployment."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import trading_intelligence_app  # noqa: F401
