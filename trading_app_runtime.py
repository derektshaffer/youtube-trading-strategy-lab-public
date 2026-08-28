"""Stable shared runtime helpers for Streamlit trading pages.

Keep settings and market-client construction outside UI page modules. Streamlit
can rerun or hot-reload pages independently; importing one page from another
couples their partially initialized module state and caused deploy-time errors.
"""

from __future__ import annotations

import os

import streamlit as st

from youtube_strategy_engine import AlpacaMarketData


def setting(name: str, default: str = "") -> str:
    try:
        if name in st.secrets and str(st.secrets[name]).strip():
            return str(st.secrets[name]).strip()
    except (FileNotFoundError, KeyError, RuntimeError, AttributeError):
        pass
    return str(os.environ.get(name, default)).strip()


def market_client() -> AlpacaMarketData:
    return AlpacaMarketData(
        setting("ALPACA_API_KEY"),
        setting("ALPACA_SECRET_KEY"),
        setting("ALPACA_LIVE_FEED", "iex"),
        setting("ALPACA_HISTORICAL_FEED", "sip"),
    )
