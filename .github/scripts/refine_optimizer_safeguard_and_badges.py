from pathlib import Path

path = Path("youtube_strategy_app.py")
text = path.read_text(encoding="utf-8")

old_css = '''/* Make the historical sample safeguard visually distinct without making every checkbox huge. */
.st-key-eight_trade_safeguard {border:2px solid rgba(255,186,99,.48) !important;
 background:linear-gradient(115deg,rgba(255,186,99,.075),rgba(16,26,43,.82)) !important;
 border-radius:14px !important; padding:13px 15px 10px !important; margin:9px 0 15px !important}
.st-key-eight_trade_safeguard [data-testid="stMarkdownContainer"] h4 {font-size:18px !important;
 margin:0 0 2px !important; color:#ffe0ad !important}
.st-key-optimizer_eight_trade_filter label,
.st-key-optimizer_eight_trade_filter label p,
.st-key-optimizer_eight_trade_filter [data-testid="stWidgetLabel"] p {font-size:17px !important; font-weight:850 !important}
.st-key-optimizer_eight_trade_filter input[type="checkbox"] {transform:scale(1.35); transform-origin:left center}
.st-key-optimizer_eight_trade_filter [role="checkbox"] {transform:scale(1.18); transform-origin:left center}
'''
new_css = '''/* Compact historical sample safeguard: noticeable, but it should not split the form in half. */
.st-key-eight_trade_safeguard {border:1px solid rgba(255,186,99,.38) !important;
 background:rgba(255,186,99,.045) !important; border-radius:11px !important;
 padding:7px 11px 5px !important; margin:8px 0 10px !important}
.st-key-eight_trade_safeguard [data-testid="stMarkdownContainer"] p {margin-bottom:2px !important}
.st-key-optimizer_eight_trade_filter label,
.st-key-optimizer_eight_trade_filter label p,
.st-key-optimizer_eight_trade_filter [data-testid="stWidgetLabel"] p {font-size:15px !important; font-weight:800 !important}
.st-key-optimizer_eight_trade_filter input[type="checkbox"] {transform:scale(1.12); transform-origin:left center}
.st-key-optimizer_eight_trade_filter [role="checkbox"] {transform:scale(1.08); transform-origin:left center}
'''
if old_css not in text:
    raise SystemExit("safeguard CSS anchor not found")
text = text.replace(old_css, new_css, 1)

old_guard = '''            with st.container(border=True, key="eight_trade_safeguard"):
                st.markdown("#### 8-trade sample safeguard")
                st.caption(
                    "This controls whether very small-sample results are allowed to win Maximum historical P/L optimization."
                )
                require_eight_historical_trades = st.checkbox(
                    "Require at least 8 completed trades before a historical result can win",
                    value=True,
                    disabled=not historical_pnl_mode,
                    key="optimizer_eight_trade_filter",
                    help=(
                        "ON: configurations with fewer than 8 completed trades cannot beat configurations that meet the minimum. "
                        "OFF: Maximum historical P/L can select a result based on only a few trades, which can make overfitting much easier. "
                        "This setting applies only to Maximum historical P/L mode."
                    ),
                )
                if historical_pnl_mode:
                    if require_eight_historical_trades:
                        st.success("✓ ON — results need at least 8 completed trades to qualify ahead of tiny samples.")
                    else:
                        st.warning("OFF — results with fewer than 8 trades are allowed to rank first for this run.")
                else:
                    st.caption("Not used in Validated edge mode; that mode has separate training and validation trade thresholds.")

'''
if old_guard not in text:
    raise SystemExit("existing safeguard block not found")
text = text.replace(old_guard, "", 1)

insert_anchor = '''            sizing_depth = protection_row[1].selectbox(
                "Position-size search depth · SEARCH",
                ["Quick — 8 sizing combinations", "Balanced — 24 sizing combinations", "Comprehensive — 48 sizing combinations", "Exhaustive — 64 sizing combinations"],
                index=2,
            )
            optimization_requested = st.form_submit_button(
'''
replacement = '''            sizing_depth = protection_row[1].selectbox(
                "Position-size search depth · :blue-background[SEARCH]",
                ["Quick — 8 sizing combinations", "Balanced — 24 sizing combinations", "Comprehensive — 48 sizing combinations", "Exhaustive — 64 sizing combinations"],
                index=2,
            )

            with st.container(border=True, key="eight_trade_safeguard"):
                st.markdown("**8-trade sample safeguard** · Maximum historical P/L only")
                require_eight_historical_trades = st.checkbox(
                    "Require at least 8 completed trades before a historical result can win",
                    value=True,
                    disabled=not historical_pnl_mode,
                    key="optimizer_eight_trade_filter",
                    help=(
                        "ON: configurations with fewer than 8 completed trades cannot beat configurations that meet the minimum. "
                        "OFF: Maximum historical P/L can select a result based on only a few trades, which can make overfitting much easier. "
                        "This setting applies only to Maximum historical P/L mode."
                    ),
                )
                if historical_pnl_mode:
                    st.caption(
                        ":green-background[ON] At least 8 completed trades required to qualify ahead of tiny samples."
                        if require_eight_historical_trades
                        else ":orange-background[OFF] Tiny-sample results are allowed to rank first for this run."
                    )
                else:
                    st.caption(":gray-background[NOT USED] Validated edge uses its separate training and validation thresholds.")

            optimization_requested = st.form_submit_button(
'''
if insert_anchor not in text:
    raise SystemExit("safeguard insertion anchor not found")
text = text.replace(insert_anchor, replacement, 1)

# Use Streamlit's built-in colored-background Markdown directives inside widget labels.
# This preserves normal widget labels and their built-in help tooltips.
replacements = {
    '"Historical calendar days · FIXED"': '"Historical calendar days · :gray-background[FIXED]"',
    '"Candle intervals to test · SEARCH"': '"Candle intervals to test · :blue-background[SEARCH]"',
    '"Strategies to compare · SEARCH"': '"Strategies to compare · :blue-background[SEARCH]"',
    '"Optimization goal · SEARCH"': '"Optimization goal · :blue-background[SEARCH]"',
    '"Search depth · SEARCH"': '"Search depth · :blue-background[SEARCH]"',
    '"Minimum training trades · THRESHOLD"': '"Minimum training trades · :yellow-background[THRESHOLD]"',
    '"Minimum validation trades · THRESHOLD"': '"Minimum validation trades · :yellow-background[THRESHOLD]"',
    '"Starting cash ($) · FIXED"': '"Starting cash ($) · :gray-background[FIXED]"',
    '"Risk per trade (%) · CEILING"': '"Risk per trade (%) · :orange-background[CEILING]"',
    '"Position size (% of account) · CEILING"': '"Position size (% of account) · :orange-background[CEILING]"',
    '"Stop loss (fallback seed %) · AUTO"': '"Stop loss (fallback seed %) · :green-background[AUTO]"',
    '"Reward/risk (fallback seed) · AUTO"': '"Reward/risk (fallback seed) · :green-background[AUTO]"',
    '"Spread (bps) · FLOOR" if automatic_execution_costs else "Spread (bps) · FIXED"': '"Spread (bps) · :violet-background[FLOOR]" if automatic_execution_costs else "Spread (bps) · :gray-background[FIXED]"',
    '"Slippage per fill (bps) · FLOOR" if automatic_execution_costs else "Slippage per fill (bps) · FIXED"': '"Slippage per fill (bps) · :violet-background[FLOOR]" if automatic_execution_costs else "Slippage per fill (bps) · :gray-background[FIXED]"',
    '"Fee per order ($) · FIXED"': '"Fee per order ($) · :gray-background[FIXED]"',
    '"Maximum acceptable drawdown (%) · THRESHOLD"': '"Maximum acceptable drawdown (%) · :yellow-background[THRESHOLD]"',
}
for old, new in replacements.items():
    if old not in text:
        raise SystemExit(f"label anchor not found: {old}")
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
print("Refined optimizer safeguard placement and behavior badges")
