"""Streamlit multipage entrypoint for the Live Strategy Runner.

This wrapper keeps the original runner implementation intact while allowing a
stock-optimized strategy to be inspected on another ticker. Cross-ticker paper
auto-entry requires a separate explicit acknowledgement.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

source_path = ROOT / "live_strategy_runner_page.py"
source = source_path.read_text(encoding="utf-8")

# A stock-specific optimization is a recommendation/warning, not a hard lock.
source = source.replace(
    '    if optimized_symbol and optimized_symbol != ticker:\n'
    '        raise AppError(f"This optimized strategy is locked to {optimized_symbol}.")\n',
    '    # Cross-ticker inspection is allowed; warn below after the warning list exists.\n',
)
source = source.replace(
    '    warnings: list[str] = []\n'
    '    historical_end = utc_now() - timedelta(',
    '    warnings: list[str] = []\n'
    '    if optimized_symbol and optimized_symbol != ticker:\n'
    '        warnings.append(\n'
    '            f"This strategy was optimized/backtested for {optimized_symbol}, but you are evaluating {ticker}. "\n'
    '            "Its historical results may not transfer to this stock."\n'
    '        )\n'
    '    historical_end = utc_now() - timedelta(',
)

# Keep the optimized ticker as the default, but let the user type another stock.
source = source.replace(
    '        placeholder="SDOT",\n'
    '        disabled=bool(optimized_symbol),\n'
    '        help="Stock-optimized strategies stay locked to the stock they were optimized for.",\n',
    '        placeholder="SDOT",\n'
    '        key=f"runner_ticker_{strategy.get(\'id\')}",\n'
    '        disabled=False,\n'
    '        help="The optimized ticker is filled in by default, but you can test the strategy on another stock. A warning will appear when you do.",\n',
)

# Clearly flag cross-ticker use before the risk/order controls.
source = source.replace(
    '    controls[2].caption(f\'Direction: {strategy.get("direction") or "Unclear"} · Optimized ticker: {optimized_symbol or "Any"}\')\n\n'
    '    if mode == "Alpaca paper auto-entry" and not approved:',
    '    controls[2].caption(f\'Direction: {strategy.get("direction") or "Unclear"} · Optimized ticker: {optimized_symbol or "Any"}\')\n\n'
    '    ticker_mismatch = bool(optimized_symbol and ticker and optimized_symbol != ticker)\n'
    '    if ticker_mismatch:\n'
    '        st.warning(\n'
    '            f"Cross-ticker test: this strategy was optimized for {optimized_symbol}, not {ticker}. "\n'
    '            "Signal Only is allowed, but the optimized historical performance should not be assumed to apply to this stock."\n'
    '        )\n\n'
    '    if mode == "Alpaca paper auto-entry" and not approved:',
)

# Require a second acknowledgement before Paper Auto can be armed off-ticker.
source = source.replace(
    '    armed = False\n'
    '    if mode == "Alpaca paper auto-entry":\n'
    '        armed = st.checkbox(',
    '    cross_ticker_confirmed = not ticker_mismatch\n'
    '    if mode == "Alpaca paper auto-entry" and ticker_mismatch:\n'
    '        cross_ticker_confirmed = st.checkbox(\n'
    '            f"I understand this strategy was optimized for {optimized_symbol} and want to PAPER-test it on {ticker}",\n'
    '            value=False,\n'
    '            help="This acknowledgement applies only to simulated Alpaca paper trading. It does not change the saved strategy or its original optimization.",\n'
    '        )\n\n'
    '    armed = False\n'
    '    if mode == "Alpaca paper auto-entry":\n'
    '        armed = st.checkbox(',
)
source = source.replace(
    '            disabled=not approved or not paper_ready or not is_long_strategy(strategy),',
    '            disabled=not approved or not paper_ready or not is_long_strategy(strategy) or not cross_ticker_confirmed,',
)

code = compile(source, str(source_path), "exec")
exec(code, globals(), globals())

st.set_page_config(
    page_title="Live Strategy Runner",
    page_icon="🟢",
    layout="wide",
    initial_sidebar_state="expanded",
)

render()
