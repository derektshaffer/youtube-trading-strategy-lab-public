"""Machine Learning Lab with clean navigation back to the simplified dashboard."""

from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
source_path = ROOT / "machine_learning_lab_core.py"
source = source_path.read_text(encoding="utf-8")

# Avoid Streamlit magic rendering a DeltaGenerator object in the sidebar.
source = source.replace(
    '    st.success("Alpaca market data connected") if market_ready else st.error("Alpaca credentials needed")',
    '    if market_ready:\n        st.success("Alpaca market data connected")\n    else:\n        st.error("Alpaca credentials needed")',
)

code = compile(source, str(source_path), "exec")
exec(code, globals(), globals())

with st.sidebar:
    st.divider()
    if st.button("← Trading Dashboard", key="ml_back_dashboard", use_container_width=True):
        st.switch_page("youtube_strategy_app.py")
    if st.button("Open Full Trading Lab", key="ml_open_full_lab", use_container_width=True):
        st.switch_page("pages/Full_Trading_Lab.py")
