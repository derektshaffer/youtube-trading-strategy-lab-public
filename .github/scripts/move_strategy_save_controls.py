from pathlib import Path
from textwrap import dedent, indent

path = Path("youtube_strategy_app_core.py")
text = path.read_text(encoding="utf-8")

strategies_start = text.index("with strategies_tab:")
strategies_end = text.index("\n\nwith backtest_tab:", strategies_start)

# Restore the earlier, clearer live-use approval control near the top of the
# selected strategy view. Approval is saved independently from advanced rules.
approval_marker = '        rules = normalize_machine_rules(selected.get("machine_rules"))\n\n'
approval_insert = dedent('''
        st.markdown("#### Live-use approval")
        approval_columns = st.columns([3, 1])
        with approval_columns[0]:
            approved = st.checkbox(
                "Approved for Live Strategy Runner and live scanner",
                value=bool(selected.get("approved")),
                key=f"strategy_approval_{selected['id']}",
                help=(
                    "Approval allows this strategy to be used by the live scanner and enables Paper Auto "
                    "in the Live Strategy Runner. Signal Only does not require approval."
                ),
            )
            st.caption(
                "Approval alone does not place a trade. Paper Auto has a separate arm switch and uses "
                "Alpaca's paper endpoint only."
            )
        if approval_columns[1].button(
            "Save approval",
            key=f"save_strategy_approval_{selected['id']}",
            use_container_width=True,
        ):
            store.update_strategy(selected["id"], {"approved": approved})
            st.success("Approval status saved.")
            st.rerun()

''').strip("\n")
approval_insert = indent(approval_insert, "        ") + "\n\n"

strategy_section = text[strategies_start:strategies_end]
if 'st.markdown("#### Live-use approval")' not in strategy_section:
    marker_pos = text.index(approval_marker, strategies_start, strategies_end) + len(approval_marker)
    text = text[:marker_pos] + approval_insert + text[marker_pos:]

# Replace the always-open measurable-rules form with the earlier collapsed
# Advanced strategy rules editor. It has its own save button and does not mix
# approval state with rule edits.
strategies_start = text.index("with strategies_tab:")
strategies_end = text.index("\n\nwith backtest_tab:", strategies_start)
advanced_start = text.index('        section("Edit measurable rules"', strategies_start, strategies_end)
advanced_end = text.index('        confirm_strategy_deletion =', advanced_start, strategies_end)

advanced_raw = dedent('''
with st.expander("Advanced strategy rules", expanded=False):
    st.caption(
        "Optional scanner/backtest thresholds. Leave a field blank when the video did not specify it "
        "or when you do not want that condition required. You do not need to fill these in to approve a strategy."
    )

    def text_rule(label: str, name: str, help_text: str = "") -> str:
        existing = rules.get(name)
        return st.text_input(
            label,
            value="" if existing is None else str(existing),
            key=f"edit_{selected['id']}_{name}",
            help=help_text,
        )

    with st.form(f"edit_strategy_{selected['id']}"):
        column_one, column_two, column_three = st.columns(3)
        updated: dict[str, Any] = {}
        with column_one:
            updated["min_price"] = text_rule("Minimum stock price ($)", "min_price")
            updated["max_price"] = text_rule("Maximum stock price ($)", "max_price")
            updated["min_day_change_pct"] = text_rule("Minimum move today (%)", "min_day_change_pct")
            updated["min_relative_volume"] = text_rule("Minimum relative volume (x)", "min_relative_volume")
            updated["min_dollar_volume"] = text_rule("Minimum dollar volume ($)", "min_dollar_volume")
            updated["max_spread_pct"] = text_rule("Maximum bid/ask spread (%)", "max_spread_pct")
        with column_two:
            vwap_values = {"No requirement": None, "Must be above VWAP": True, "Must be below VWAP": False}
            current_vwap = next(
                (label for label, value in vwap_values.items() if value is rules.get("above_vwap")),
                "No requirement",
            )
            vwap_choice = st.selectbox(
                "VWAP position",
                list(vwap_values),
                index=list(vwap_values).index(current_vwap),
            )
            updated["above_vwap"] = vwap_values[vwap_choice]
            updated["max_vwap_distance_pct"] = text_rule("Maximum distance above VWAP (%)", "max_vwap_distance_pct")
            updated["breakout_lookback_bars"] = text_rule("Breakout lookback (candles)", "breakout_lookback_bars")
            updated["opening_range_minutes"] = text_rule("Opening range (minutes)", "opening_range_minutes")
            updated["volume_surge_ratio"] = text_rule("Current-candle volume surge (x)", "volume_surge_ratio")
            updated["minimum_green_bars"] = text_rule("Consecutive green candles", "minimum_green_bars")
        with column_three:
            updated["stop_loss_pct"] = text_rule("Stop loss (%)", "stop_loss_pct")
            updated["reward_risk"] = text_rule("Target reward/risk (x)", "reward_risk")
            updated["max_hold_minutes"] = text_rule("Maximum holding time (minutes)", "max_hold_minutes")
            updated["session_start"] = text_rule("Earliest entry, Eastern (HH:MM)", "session_start")
            updated["session_end"] = text_rule("Latest entry, Eastern (HH:MM)", "session_end")
            updated["vwap_reclaim"] = st.checkbox(
                "Require a VWAP reclaim",
                value=bool(rules.get("vwap_reclaim")),
            )
            updated["catalyst_required"] = st.checkbox(
                "Require recent news",
                value=bool(rules.get("catalyst_required")),
            )
        save_strategy = st.form_submit_button(
            "Save advanced strategy rules",
            use_container_width=True,
        )
    if save_strategy:
        store.update_strategy(selected["id"], {"machine_rules": updated})
        st.success("Advanced strategy rules saved.")
        st.rerun()

''').strip("\n")
advanced_block = indent(advanced_raw, "        ") + "\n"
text = text[:advanced_start] + advanced_block + text[advanced_end:]

# Safety checks for the requested UI structure.
strategies_start = text.index("with strategies_tab:")
strategies_end = text.index("\n\nwith backtest_tab:", strategies_start)
strategy_section = text[strategies_start:strategies_end]
required = (
    'st.markdown("#### Live-use approval")',
    '"Save approval"',
    'with st.expander("Advanced strategy rules", expanded=False):',
    '"Save advanced strategy rules"',
)
for marker in required:
    if marker not in strategy_section:
        raise SystemExit(f"Missing expected restored layout marker: {marker}")
for old_marker in ('"Save strategy rules"', 'save_controls = st.columns([3.0, 1.35])'):
    if old_marker in strategy_section:
        raise SystemExit(f"Old confusing save layout still remains: {old_marker}")

path.write_text(text, encoding="utf-8")
print("Restored separate Live-use approval and collapsed Advanced strategy rules layout.")
