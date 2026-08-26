"""Advanced wrapper around the original Machine Learning Lab."""

from pathlib import Path

import streamlit as st

source_path = Path(__file__).with_name("Machine_Learning_Lab.py")
source = source_path.read_text(encoding="utf-8")
code = compile(source, str(source_path), "exec")
exec(code, globals(), globals())

with st.sidebar:
    st.divider()
    if st.button("← Simple Trading Dashboard", key="ml_back_simple", use_container_width=True):
        st.switch_page("youtube_strategy_app.py")
