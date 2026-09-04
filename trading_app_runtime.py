"""Stable shared runtime helpers for Streamlit trading pages.

Keep settings and market-client construction outside UI page modules. Streamlit
can rerun or hot-reload pages independently; importing one page from another
couples their partially initialized module state and caused deploy-time errors.
"""

from __future__ import annotations

import os
from typing import Any

import streamlit as st

from alpaca_iex_momentum import IexCompatibleAlpacaMarketData
from research_cached_market import CachedResearchMarket


def setting(name: str, default: str = "") -> str:
    try:
        if name in st.secrets and str(st.secrets[name]).strip():
            return str(st.secrets[name]).strip()
    except (FileNotFoundError, KeyError, RuntimeError, AttributeError):
        pass
    return str(os.environ.get(name, default)).strip()


def market_client() -> Any:
    """Return the authoritative Alpaca client behind a narrow research cache proxy.

    The proxy only intercepts single-symbol bar requests with explicit frozen
    start/end/timeframe boundaries and standard arguments. Live/batch/provider-
    specific requests delegate directly to Alpaca. Finalized exact windows are
    reusable across reconnects and process restarts without changing backtest rows.

    Live momentum discovery uses the configured Alpaca feed. On the default IEX
    plan, the client derives movers/most-active rankings from batched IEX snapshots
    instead of calling Alpaca's SIP-backed screener endpoints. If the primary
    Alpaca key pair is rejected, an explicitly configured paper-account key pair
    is available as a one-time authentication fallback.
    """

    market = IexCompatibleAlpacaMarketData(
        setting("ALPACA_API_KEY"),
        setting("ALPACA_SECRET_KEY"),
        setting("ALPACA_LIVE_FEED", "iex"),
        setting("ALPACA_HISTORICAL_FEED", "sip"),
        fallback_api_key=setting("ALPACA_PAPER_API_KEY"),
        fallback_secret_key=setting("ALPACA_PAPER_SECRET_KEY"),
    )
    return CachedResearchMarket(market)
