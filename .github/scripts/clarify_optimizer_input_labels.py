from pathlib import Path

path = Path("youtube_strategy_app.py")
text = path.read_text(encoding="utf-8")

# Add a clear behavior legend immediately before the optimizer form.
old = '''        manual_optimizer_defaults = st.session_state.get("last_manual_backtest_settings") or {}
        with st.form("stock_optimizer_form"):
'''
new = '''        manual_optimizer_defaults = st.session_state.get("last_manual_backtest_settings") or {}
        st.markdown(
            "**How optimizer inputs work**  "
            "🟢 **AUTO-SEARCH** = tests multiple supported values and refines promising ones · "
            "🟠 **CEILING** = auto-searches below the number you enter, but never above it · "
            "🔒 **FIXED** = uses exactly the value you enter · "
            "🟡 **THRESHOLD** = qualification/ranking rule, not a value being optimized · "
            "🔵 **SEARCH CONTROL** = controls how much or what the optimizer searches."
        )
        st.caption(
            "AUTO-SEARCH does not mean every possible decimal value. The optimizer uses broad grids and adaptive refinement. "
            "Some fields may be prefilled from your most recent manual backtest; the label tells you how the optimizer treats that value."
        )
        with st.form("stock_optimizer_form"):
'''
if old not in text:
    raise SystemExit("Optimizer legend anchor not found")
text = text.replace(old, new, 1)

replacements = {
'''                "Historical calendar days",
                min_value=7,
''': '''                "🔒 FIXED · Historical calendar days",
                min_value=7,
''',
'''                "Candle intervals to test",
                ["Automatically compare 1, 5, and 15 minutes", "1Min only", "5Min only", "15Min only"],
''': '''                "🔵 SEARCH CONTROL · Candle intervals to test",
                ["Automatically compare 1, 5, and 15 minutes", "1Min only", "5Min only", "15Min only"],
''',
'''                "Strategies to compare",
                ["All saved long strategies", "Approved strategies only"],
''': '''                "🔵 SEARCH CONTROL · Strategies to compare",
                ["All saved long strategies", "Approved strategies only"],
''',
'''                "Optimization goal",
                [
''': '''                "🔵 SEARCH CONTROL · Optimization goal",
                [
''',
'''                "Require at least 8 trades before a historical result can win",
                value=True,
''': '''                "🟡 THRESHOLD · Require at least 8 trades before a historical result can win",
                value=True,
''',
'''                "Search depth",
                ["Quick — 48 combinations", "Balanced — 120 combinations", "Comprehensive — 240 combinations", "Exhaustive — 320 combinations"],
''': '''                "🔵 SEARCH CONTROL · Search depth",
                ["Quick — 48 combinations", "Balanced — 120 combinations", "Comprehensive — 240 combinations", "Exhaustive — 320 combinations"],
''',
'''                "Minimum training trades",
                min_value=1,
''': '''                "🟡 THRESHOLD · Minimum training trades",
                min_value=1,
''',
'''                "Minimum validation trades",
                min_value=1,
''': '''                "🟡 THRESHOLD · Minimum validation trades",
                min_value=1,
''',
'''                "Starting cash ($)",
                min_value=100.0,
''': '''                "🔒 FIXED · Starting cash ($)",
                min_value=100.0,
''',
'''                "Maximum risk per trade to test (%)",
                min_value=0.05,
''': '''                "🟠 CEILING · Risk per trade (%)",
                min_value=0.05,
''',
'''                "Maximum position size to test (%)",
                min_value=1.0,
''': '''                "🟠 CEILING · Position size (% of account)",
                min_value=1.0,
''',
'''                "Fallback stop (%)",
                min_value=0.1,
''': '''                "🟢 AUTO-SEARCH · Stop loss (fallback seed %)",
                min_value=0.1,
''',
'''                "Fallback reward/risk",
                min_value=0.2,
''': '''                "🟢 AUTO-SEARCH · Reward/risk (fallback seed)",
                min_value=0.2,
''',
'''                "Spread estimate (bps)",
                min_value=0.0,
''': '''                "🔒 FIXED · Spread estimate (bps)",
                min_value=0.0,
''',
'''                "Slippage per fill (bps)",
                min_value=0.0,
''': '''                "🔒 FIXED · Slippage per fill (bps)",
                min_value=0.0,
''',
'''                "Fee per order ($)",
                min_value=0.0,
''': '''                "🔒 FIXED · Fee per order ($)",
                min_value=0.0,
''',
'''                "Maximum acceptable drawdown (%)",
                min_value=0.5,
''': '''                "🟡 THRESHOLD · Maximum acceptable drawdown (%)",
                min_value=0.5,
''',
'''                "Position-size search depth",
                ["Quick — 8 sizing combinations", "Balanced — 24 sizing combinations", "Comprehensive — 48 sizing combinations", "Exhaustive — 64 sizing combinations"],
''': '''                "🔵 SEARCH CONTROL · Position-size search depth",
                ["Quick — 8 sizing combinations", "Balanced — 24 sizing combinations", "Comprehensive — 48 sizing combinations", "Exhaustive — 64 sizing combinations"],
''',
'''                "Use the stock's actual quoted spread",
                value=False,
''': '''                "🔒 FIXED/OVERRIDE · Use the stock's actual quoted spread",
                value=False,
''',
}

for old_piece, new_piece in replacements.items():
    if old_piece not in text:
        raise SystemExit(f"Label anchor not found: {old_piece.splitlines()[0]!r}")
    text = text.replace(old_piece, new_piece, 1)

# Expand help text on the fields that are easiest to misinterpret.
old = '''                help="The optimizer compares lower risk levels and will never recommend more than this ceiling.",
'''
new = '''                help=(
                    "CEILING: the optimizer automatically tests lower risk-per-trade values and may recommend any supported value below this number, "
                    "but it will never test or recommend a higher risk percentage."
                ),
'''
if old not in text:
    raise SystemExit("Risk help anchor not found")
text = text.replace(old, new, 1)

old = '''                help="The optimizer compares smaller allocations and will never exceed this percentage of your account.",
'''
new = '''                help=(
                    "CEILING: the optimizer automatically tests smaller position allocations and refines promising values, "
                    "but it will never put more than this percentage of the account into a simulated trade."
                ),
'''
if old not in text:
    raise SystemExit("Position help anchor not found")
text = text.replace(old, new, 1)

# Add missing help text to stop/reward/costs/starting cash/threshold fields.
old = '''                value=float(manual_optimizer_defaults.get("starting_cash", 2_000.0)),
                step=100.0,
            )
'''
new = '''                value=float(manual_optimizer_defaults.get("starting_cash", 2_000.0)),
                step=100.0,
                help="FIXED: every candidate in this optimizer run starts with exactly this simulated account size.",
            )
'''
if old not in text:
    raise SystemExit("Starting cash block anchor not found")
text = text.replace(old, new, 1)

old = '''                value=float(manual_optimizer_defaults.get("default_stop_pct", 2.0)),
                step=0.1,
            )
'''
new = '''                value=float(manual_optimizer_defaults.get("default_stop_pct", 2.0)),
                step=0.1,
                help=(
                    "AUTO-SEARCH: this is the fallback/seed stop used when a saved strategy does not specify one. "
                    "The optimizer then tests a broad stop-loss grid and adaptively refines around promising values."
                ),
            )
'''
if old not in text:
    raise SystemExit("Stop block anchor not found")
text = text.replace(old, new, 1)

old = '''                value=float(manual_optimizer_defaults.get("default_reward_risk", 2.0)),
                step=0.1,
            )
'''
new = '''                value=float(manual_optimizer_defaults.get("default_reward_risk", 2.0)),
                step=0.1,
                help=(
                    "AUTO-SEARCH: this is the fallback/seed reward-to-risk target when a saved strategy does not specify one. "
                    "The optimizer tests multiple reward/risk values and refines around promising settings."
                ),
            )
'''
if old not in text:
    raise SystemExit("Reward block anchor not found")
text = text.replace(old, new, 1)

old = '''                value=float(manual_optimizer_defaults.get("spread_bps", 12.0)),
                step=1.0,
            )
'''
new = '''                value=float(manual_optimizer_defaults.get("spread_bps", 12.0)),
                step=1.0,
                help=(
                    "FIXED trading-cost assumption: the optimizer does not search for a better spread. "
                    "If the actual-quoted-spread option is enabled, the app may replace this with a wider live quoted spread."
                ),
            )
'''
if old not in text:
    raise SystemExit("Spread block anchor not found")
text = text.replace(old, new, 1)

old = '''                value=float(manual_optimizer_defaults.get("slippage_bps", 8.0)),
                step=1.0,
            )
'''
new = '''                value=float(manual_optimizer_defaults.get("slippage_bps", 8.0)),
                step=1.0,
                help="FIXED trading-cost assumption: every candidate uses this slippage per simulated fill.",
            )
'''
if old not in text:
    raise SystemExit("Slippage block anchor not found")
text = text.replace(old, new, 1)

old = '''                value=float(manual_optimizer_defaults.get("fee_per_order", 0.0)),
                step=0.1,
            )
'''
new = '''                value=float(manual_optimizer_defaults.get("fee_per_order", 0.0)),
                step=0.1,
                help="FIXED trading-cost assumption: every candidate uses this fee per simulated order.",
            )
'''
if old not in text:
    raise SystemExit("Fee block anchor not found")
text = text.replace(old, new, 1)

old = '''                value=5,
                step=1,
            )
            minimum_validation = second_row[2].number_input(
'''
new = '''                value=5,
                step=1,
                help="THRESHOLD used by Validated edge mode: training results with too few completed trades are treated as insufficient data.",
            )
            minimum_validation = second_row[2].number_input(
'''
if old not in text:
    raise SystemExit("Training threshold anchor not found")
text = text.replace(old, new, 1)

old = '''                value=2,
                step=1,
            )
            optimizer_cash = second_row[3].number_input(
'''
new = '''                value=2,
                step=1,
                help="THRESHOLD used by Validated edge mode: validation results need at least this many completed trades to count as an adequate sample.",
            )
            optimizer_cash = second_row[3].number_input(
'''
if old not in text:
    raise SystemExit("Validation threshold anchor not found")
text = text.replace(old, new, 1)

# Add glossary definitions so the new labels can be looked up in Help & Glossary.
anchor = '''HELP_GLOSSARY: list[dict[str, str]] = [
'''
entries = '''HELP_GLOSSARY: list[dict[str, str]] = [
    {"term": "AUTO-SEARCH optimizer input", "category": "Optimizer", "meaning": "An input where the displayed value is a seed or search control rather than the final answer. The optimizer tests multiple supported values and adaptively refines promising ones; it does not literally test every possible decimal value."},
    {"term": "CEILING optimizer input", "category": "Optimizer", "meaning": "A user-set maximum. The optimizer may test and recommend values below it but is not allowed to test or recommend values above it."},
    {"term": "FIXED optimizer input", "category": "Optimizer", "meaning": "A value the optimizer holds constant for every candidate in that run, such as starting cash or a trading-cost assumption."},
    {"term": "THRESHOLD optimizer input", "category": "Optimizer", "meaning": "A qualification or ranking rule, such as minimum trades or maximum acceptable drawdown. It is not itself optimized."},
    {"term": "SEARCH CONTROL optimizer input", "category": "Optimizer", "meaning": "A setting that controls the scope or amount of optimizer work, such as search depth, strategies to compare, or candle intervals to test."},
'''
if anchor not in text:
    raise SystemExit("Glossary anchor not found")
if '"term": "CEILING optimizer input"' not in text:
    text = text.replace(anchor, entries, 1)

path.write_text(text, encoding="utf-8")
