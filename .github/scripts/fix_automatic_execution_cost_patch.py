from pathlib import Path

path = Path('.github/scripts/add_automatic_execution_costs.py')
text = path.read_text(encoding='utf-8')
old = '''# Candidate/report metadata.
if old_candidate not in val:
    raise SystemExit("validated candidate output anchor not found")
val = val.replace(old_candidate, new_candidate, 1)
old_val_report = '''
new = '''# Candidate/report metadata. Validated mode uses a nested dict with four extra spaces.
validated_old_candidate = """                \\"optimized_backtest_settings\\": asdict(chosen_settings),\n                \\"changed_backtest_settings\\": changed_backtest_settings,\n"""
validated_new_candidate = """                \\"optimized_backtest_settings\\": asdict(chosen_settings),\n                \\"automatic_slippage_enabled\\": bool(optimizer.automatic_slippage),\n                \\"estimated_slippage_bps\\": chosen_settings.slippage_bps if optimizer.automatic_slippage else None,\n                \\"changed_backtest_settings\\": changed_backtest_settings,\n"""
if validated_old_candidate not in val:
    raise SystemExit("validated candidate output anchor not found")
val = val.replace(validated_old_candidate, validated_new_candidate, 1)
old_val_report = '''
if old not in text:
    raise SystemExit('validated metadata patch block not found')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
