from pathlib import Path

path = Path("youtube_strategy_app.py")
text = path.read_text(encoding="utf-8")

css_anchor = '''.action-success {
 border:1px solid rgba(53,213,151,.65); border-radius:10px; padding:12px 14px;
 background:linear-gradient(115deg,rgba(19,114,79,.95),rgba(29,142,101,.92));
 color:#f4fff9; font-weight:850; text-align:center; margin-top:8px; margin-bottom:8px;
}
div[data-baseweb="tab-list"] {gap:14px}
'''
css_replacement = '''.action-success {
 border:1px solid rgba(53,213,151,.65); border-radius:10px; padding:12px 14px;
 background:linear-gradient(115deg,rgba(19,114,79,.95),rgba(29,142,101,.92));
 color:#f4fff9; font-weight:850; text-align:center; margin-top:8px; margin-bottom:8px;
}
.optimizer-legend {display:flex; flex-wrap:wrap; gap:7px 14px; align-items:center;
 margin:8px 0 7px; color:var(--muted); font-size:12px; line-height:1.5}
.optimizer-legend-item {display:inline-flex; align-items:center; gap:6px}
.optimizer-badge {display:inline-flex; align-items:center; border-radius:999px; padding:2px 7px;
 font-size:9px; letter-spacing:.055em; font-weight:900; line-height:1.45; border:1px solid var(--line)}
.optimizer-badge.auto {color:#b9f6db; background:rgba(53,213,151,.09); border-color:rgba(53,213,151,.27)}
.optimizer-badge.ceiling {color:#ffdda7; background:rgba(255,186,99,.09); border-color:rgba(255,186,99,.27)}
.optimizer-badge.fixed {color:#d3deed; background:rgba(169,185,207,.09); border-color:rgba(169,185,207,.25)}
.optimizer-badge.floor {color:#dfd1ff; background:rgba(169,139,255,.09); border-color:rgba(169,139,255,.27)}
.optimizer-badge.threshold {color:#fff0a8; background:rgba(231,205,91,.08); border-color:rgba(231,205,91,.25)}
.optimizer-badge.search {color:#c8e8ff; background:rgba(86,185,255,.08); border-color:rgba(86,185,255,.25)}
/* Make the historical sample safeguard visually distinct without making every checkbox huge. */
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
div[data-baseweb="tab-list"] {gap:14px}
'''
if css_anchor not in text:
    raise SystemExit("CSS anchor not found")
text = text.replace(css_anchor, css_replacement, 1)

legend_old = '''        st.markdown(
            "**How optimizer inputs work**  "
            "🟢 **AUTO-SEARCH** = tests multiple supported values and refines promising ones · "
            "🟠 **CEILING** = auto-searches below the number you enter, but never above it · "
            "🔒 **FIXED** = uses exactly the value you enter · "
            "🟣 **FALLBACK FLOOR** = automatic mode estimates the value, but never assumes less than this floor · "
            "🟡 **THRESHOLD** = qualification/ranking rule, not a value being optimized · "
            "🔵 **SEARCH CONTROL** = controls how much or what the optimizer searches."
        )
'''
legend_new = '''        st.markdown(
            """
            <div><strong>How optimizer inputs work</strong></div>
            <div class="optimizer-legend">
              <span class="optimizer-legend-item"><span class="optimizer-badge auto">AUTO</span> optimizer searches values</span>
              <span class="optimizer-legend-item"><span class="optimizer-badge ceiling">CEILING</span> you set the maximum</span>
              <span class="optimizer-legend-item"><span class="optimizer-badge fixed">FIXED</span> uses exactly your value</span>
              <span class="optimizer-legend-item"><span class="optimizer-badge floor">FLOOR</span> automatic estimate cannot go lower</span>
              <span class="optimizer-legend-item"><span class="optimizer-badge threshold">THRESHOLD</span> qualification rule</span>
              <span class="optimizer-legend-item"><span class="optimizer-badge search">SEARCH</span> controls optimizer scope</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
'''
if legend_old not in text:
    raise SystemExit("Optimizer legend anchor not found")
text = text.replace(legend_old, legend_new, 1)

label_replacements = {
    '"🔒 FIXED · Historical calendar days"': '"Historical calendar days · FIXED"',
    '"🔵 SEARCH CONTROL · Candle intervals to test"': '"Candle intervals to test · SEARCH"',
    '"🔵 SEARCH CONTROL · Strategies to compare"': '"Strategies to compare · SEARCH"',
    '"🔵 SEARCH CONTROL · Optimization goal"': '"Optimization goal · SEARCH"',
    '"🔵 SEARCH CONTROL · Search depth"': '"Search depth · SEARCH"',
    '"🟡 THRESHOLD · Minimum training trades"': '"Minimum training trades · THRESHOLD"',
    '"🟡 THRESHOLD · Minimum validation trades"': '"Minimum validation trades · THRESHOLD"',
    '"🔒 FIXED · Starting cash ($)"': '"Starting cash ($) · FIXED"',
    '"🟠 CEILING · Risk per trade (%)"': '"Risk per trade (%) · CEILING"',
    '"🟠 CEILING · Position size (% of account)"': '"Position size (% of account) · CEILING"',
    '"🟢 AUTO-SEARCH · Stop loss (fallback seed %)"': '"Stop loss (fallback seed %) · AUTO"',
    '"🟢 AUTO-SEARCH · Reward/risk (fallback seed)"': '"Reward/risk (fallback seed) · AUTO"',
    '"🟣 FALLBACK FLOOR · Spread (bps)" if automatic_execution_costs else "🔒 FIXED · Spread (bps)"': '"Spread (bps) · FLOOR" if automatic_execution_costs else "Spread (bps) · FIXED"',
    '"🟣 FALLBACK FLOOR · Slippage per fill (bps)" if automatic_execution_costs else "🔒 FIXED · Slippage per fill (bps)"': '"Slippage per fill (bps) · FLOOR" if automatic_execution_costs else "Slippage per fill (bps) · FIXED"',
    '"🔒 FIXED · Fee per order ($)"': '"Fee per order ($) · FIXED"',
    '"🟡 THRESHOLD · Maximum acceptable drawdown (%)"': '"Maximum acceptable drawdown (%) · THRESHOLD"',
    '"🔵 SEARCH CONTROL · Position-size search depth"': '"Position-size search depth · SEARCH"',
}
for old, new in label_replacements.items():
    if old not in text:
        raise SystemExit(f"Label anchor not found: {old}")
    text = text.replace(old, new, 1)

checkbox_old = '''            require_eight_historical_trades = st.checkbox(
                "🟡 THRESHOLD · Require at least 8 trades before a historical result can win",
                value=True,
                disabled=not historical_pnl_mode,
                help=(
                    "ON: configurations with fewer than 8 completed trades cannot beat configurations that meet the minimum. "
                    "OFF: Maximum historical P/L can select a result based on only a few trades, which can make overfitting much easier. "
                    "This setting applies only to Maximum historical P/L mode."
                ),
            )
            if historical_pnl_mode:
                st.caption(
                    "8-trade sample filter is ON for this run."
                    if require_eight_historical_trades
                    else "Minimum-trade filter is OFF for this run; tiny-sample results are allowed to rank first."
                )
'''
checkbox_new = '''            with st.container(border=True, key="eight_trade_safeguard"):
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
if checkbox_old not in text:
    raise SystemExit("8-trade checkbox block not found")
text = text.replace(checkbox_old, checkbox_new, 1)

path.write_text(text, encoding="utf-8")
