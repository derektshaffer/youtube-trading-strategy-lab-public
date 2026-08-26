"""Streamlit multipage entrypoint for the Live Strategy Runner."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live_strategy_runner_page import render

st.set_page_config(
    page_title="Live Strategy Runner",
    page_icon="🟢",
    layout="wide",
    initial_sidebar_state="expanded",
)

render()
