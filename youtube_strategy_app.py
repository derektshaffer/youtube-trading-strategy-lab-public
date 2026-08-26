"""Simplified Trading Dashboard entrypoint with access to the full original lab."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
source_path = ROOT / "simple_dashboard_core.py"
source = source_path.read_text(encoding="utf-8")

# Keep the simplified dashboard as Home while making the original research tools easy to reach.
source = source.replace(
    'if st.button("Full Trading Lab", use_container_width=True):\n            st.switch_page("pages/Advanced_Trading_Lab.py")',
    'if st.button("Upload / Analyze Videos + Full Lab", use_container_width=True):\n            st.switch_page("pages/Full_Trading_Lab.py")',
)
source = source.replace(
    'st.switch_page("pages/Advanced_Machine_Learning.py")',
    'st.switch_page("pages/Machine_Learning_Lab.py")',
)

# Streamlit number_input format strings accept printf-style numeric formats only;
# put the currency symbol in the plain-English label instead of in `format`.
source = source.replace(
    '"Max loss per trade",\n            min_value=1.0,',
    '"Max loss per trade ($)",\n            min_value=1.0,',
)
source = source.replace('format="$%.0f",', 'format="%.0f",')

# Put the useful strategy identifier first so similar names are easier to distinguish.
source = source.replace(
'''def strategy_label(strategy: dict[str, Any]) -> str:
    name = str(strategy.get("name") or "Unnamed strategy")
    optimized = str(strategy.get("optimized_for_symbol") or "").strip().upper()
    approval = "Approved" if strategy.get("approved") else "Review only"
    suffix = f" · {optimized} only" if optimized else ""
    return f"{name}{suffix} · {approval}"
''',
'''def strategy_label(strategy: dict[str, Any]) -> str:
    name = str(strategy.get("name") or "Unnamed strategy")
    optimized = str(strategy.get("optimized_for_symbol") or "").strip().upper()
    scope = f"[{optimized} optimized]" if optimized else "[Any stock]"
    approval = "✓ Approved" if strategy.get("approved") else "Review only"
    return f"{scope} {name} · {approval}"
''',
)

# Give the strategy chooser a full-width row instead of squeezing it between other controls.
old_controls = '''top = st.columns([1.0, 2.2, 1.15, 1.0])
with top[0]:
    ticker = st.text_input(
        "Stock",
        value=str(st.session_state.get("simple_ticker", "")),
        placeholder="SDOT",
    ).strip().upper()
    st.caption("Technical: ticker symbol")

available = applicable_strategies(strategies, ticker)
with top[1]:
    if available:
        labels = {strategy_label(item): item for item in available}
        selected_label = st.selectbox("Strategy", list(labels), key="simple_strategy")
        strategy = labels[selected_label]
    else:
        st.selectbox("Strategy", ["No compatible saved strategy"], disabled=True)
        strategy = None
    st.caption("Technical: deterministic strategy rules")

with top[2]:
    mode = st.radio(
        "What should it do?",
        ["Analyze only", "Simulated trading"],
        horizontal=False,
    )
    st.caption("Simulated trading = Alpaca paper account")

with top[3]:
    risk_dollars = float(
        st.number_input(
            "Max loss per trade",
            min_value=1.0,
            max_value=10000.0,
            value=25.0,
            step=5.0,
            format="$%.0f",
        )
    )
    st.caption("Technical: position risk budget")
'''

new_controls = '''top = st.columns([1.0, 1.25, 1.0])
with top[0]:
    ticker = st.text_input(
        "Stock",
        value=str(st.session_state.get("simple_ticker", "")),
        placeholder="SDOT",
    ).strip().upper()
    st.caption("Technical: ticker symbol")

with top[1]:
    mode = st.radio(
        "What should it do?",
        ["Analyze only", "Simulated trading"],
        horizontal=True,
    )
    st.caption("Simulated trading = Alpaca paper account")

with top[2]:
    risk_dollars = float(
        st.number_input(
            "Max loss per trade",
            min_value=1.0,
            max_value=10000.0,
            value=25.0,
            step=5.0,
            format="$%.0f",
        )
    )
    st.caption("Technical: position risk budget")

available = applicable_strategies(strategies, ticker)
if available:
    labels = {strategy_label(item): item for item in available}
    selected_label = st.selectbox(
        "Strategy",
        list(labels),
        key="simple_strategy",
        help="The strategy selector is full width so the complete saved strategy name and ticker scope are easier to read.",
    )
    strategy = labels[selected_label]
else:
    st.selectbox("Strategy", ["No compatible saved strategy"], disabled=True)
    strategy = None
st.caption("Technical: deterministic strategy rules")
if strategy is not None:
    st.caption(f"Selected: **{strategy_label(strategy)}**")
'''
source = source.replace(old_controls, new_controls)

code = compile(source, str(source_path), "exec")
exec(code, globals(), globals())
