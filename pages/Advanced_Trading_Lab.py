"""Advanced version of the original full YouTube Trading Strategy Lab."""

from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
core_path = ROOT / "youtube_strategy_app_core.py"
source = core_path.read_text(encoding="utf-8")
code = compile(source, str(core_path), "exec")
exec(code, globals(), globals())

with st.sidebar:
    st.divider()
    if st.button("← Simple Trading Dashboard", key="advanced_back_simple", use_container_width=True):
        st.switch_page("youtube_strategy_app.py")
