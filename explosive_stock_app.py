"""Dedicated Streamlit Cloud entrypoint for Explosive Stock Lab.

Using st.navigation here is intentional: it makes Streamlit ignore the repository's
shared pages/ directory, so this deployment cannot expose Trading Lab pages.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="Explosive Stock Lab",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

page = st.Page(
    "explosive_stock_page.py",
    title="Explosive Stock Lab",
    icon="⚡",
    default=True,
)
st.navigation([page], position="hidden").run()
