"""Full version of the original YouTube Trading Strategy Lab."""

from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
core_path = ROOT / "youtube_strategy_app_core.py"
source = core_path.read_text(encoding="utf-8")
code = compile(source, str(core_path), "exec")
exec(code, globals(), globals())

with st.sidebar:
    st.divider()
    if st.button("← Trading Dashboard", key="full_lab_back_dashboard", use_container_width=True):
        st.switch_page("youtube_strategy_app.py")
