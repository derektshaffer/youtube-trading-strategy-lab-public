from pathlib import Path

path = Path('youtube_strategy_app.py')
text = path.read_text(encoding='utf-8')

old_spread = '                value=float(manual_optimizer_defaults.get("spread_bps", 12.0)),\n                step=1.0,\n                disabled=automatic_execution_costs,\n'
new_spread = '                value=(12.0 if automatic_execution_costs else float(manual_optimizer_defaults.get("spread_bps", 12.0))),\n                step=1.0,\n                disabled=automatic_execution_costs,\n'
if old_spread not in text:
    raise SystemExit('automatic spread floor anchor not found')
text = text.replace(old_spread, new_spread, 1)

old_slippage = '                value=float(manual_optimizer_defaults.get("slippage_bps", 8.0)),\n                step=1.0,\n                disabled=automatic_execution_costs,\n'
new_slippage = '                value=(8.0 if automatic_execution_costs else float(manual_optimizer_defaults.get("slippage_bps", 8.0))),\n                step=1.0,\n                disabled=automatic_execution_costs,\n'
if old_slippage not in text:
    raise SystemExit('automatic slippage floor anchor not found')
text = text.replace(old_slippage, new_slippage, 1)

path.write_text(text, encoding='utf-8')
