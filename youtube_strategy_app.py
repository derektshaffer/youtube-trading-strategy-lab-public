"""Streamlit entrypoint with a cleaner Strategy Library layout.

The original application source is preserved in youtube_strategy_app_core.py.
This lightweight entrypoint only reorganizes the Strategy Library UI before
executing the original app, so the rest of the Trading Lab stays unchanged.
"""

from pathlib import Path
from textwrap import dedent


core_path = Path(__file__).with_name("youtube_strategy_app_core.py")
source = core_path.read_text(encoding="utf-8")
strategies_start = source.index("with strategies_tab:")
strategies_end = source.index("\n\nwith backtest_tab:", strategies_start)
strategy_section = source[strategies_start:strategies_end]

# Put the strategy selector first and hide the large saved-strategy table by default.
if "View all saved strategies" not in strategy_section:
    old_start = source.index('    else:\n        rows = []', strategies_start)
    old_end = source.index('\n        st.markdown(\n            status_pill', old_start)

    top_raw = dedent('''
    else:
        options = selected_strategy_options(library["strategies"])
        selection = st.selectbox(
            "Choose a strategy to inspect or edit",
            list(options),
            key="strategy_inspector",
        )
        selected = options[selection]
        rules = normalize_machine_rules(selected.get("machine_rules"))

        st.markdown("#### Live-use approval")
        approval_columns = st.columns([3, 1])
        with approval_columns[0]:
            approved_for_live = st.checkbox(
                "Enable this strategy for the Live Strategy Runner and live scanner",
                value=bool(selected.get("approved")),
                key=f"strategy_approval_{selected['id']}",
                help=(
                    "Signal Only can inspect any saved strategy. Approval is required before this strategy can "
                    "be used by the live scanner or Paper Auto in the Live Strategy Runner."
                ),
            )
            st.caption(
                "This approval does not place a trade. Paper Auto still has its own separate ARM switch and "
                "uses Alpaca's paper-trading endpoint only."
            )
        if approval_columns[1].button(
            "Save approval",
            key=f"save_strategy_approval_{selected['id']}",
            use_container_width=True,
        ):
            store.update_strategy(selected["id"], {"approved": approved_for_live})
            st.success("Live-use approval saved.")
            st.rerun()

        with st.expander(f'View all saved strategies ({len(library["strategies"])})', expanded=False):
            rows = []
            for strategy in library["strategies"]:
                rows.append(
                    {
                        "Strategy": strategy.get("name"),
                        "Type": (
                            "Master" if strategy.get("is_master_strategy")
                            else "Stock optimized" if strategy.get("optimized_for_symbol")
                            else "Video lesson"
                        ),
                        "Creator": strategy.get("creator"),
                        "Category": strategy.get("category"),
                        "Direction": strategy.get("direction"),
                        "Target stock": strategy.get("optimized_for_symbol") or "Any stock",
                        "Extraction clarity": f'{safe_float(strategy.get("confidence"), 0):.0f}%',
                        "Unresolved rules": len(strategy.get("unresolved_rules") or []),
                        "Live status": "Approved" if strategy.get("approved") else "Needs review",
                        "Last backtest P/L": money((strategy.get("last_backtest") or {}).get("net_pnl")),
                        "Holdout P/L": money((strategy.get("last_backtest") or {}).get("holdout_net_pnl")),
                    }
                )
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    ''').strip("\n")
    top_block = "\n".join("    " + line if line else "" for line in top_raw.splitlines()) + "\n"
    source = source[:old_start] + top_block + source[old_end:]

# Keep optional machine-readable thresholds out of the main review flow.
strategies_start = source.index("with strategies_tab:")
strategies_end = source.index("\n\nwith backtest_tab:", strategies_start)
strategy_section = source[strategies_start:strategies_end]

if "Advanced strategy rules" not in strategy_section:
    advanced_start = source.index('        section("Edit measurable rules"', strategies_start)
    advanced_end = source.index('        confirm_strategy_deletion =', advanced_start)

    advanced_raw = dedent('''
    with st.expander("Advanced strategy rules", expanded=False):
        st.caption(
            "Optional scanner and backtest thresholds. Blank means no requirement. You do not need to fill "
            "these fields in to approve a strategy."
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
    advanced_block = "\n".join("        " + line if line else "" for line in advanced_raw.splitlines()) + "\n\n"
    source = source[:advanced_start] + advanced_block + source[advanced_end:]

code = compile(source, str(Path(__file__)), "exec")
exec(code, globals(), globals())
