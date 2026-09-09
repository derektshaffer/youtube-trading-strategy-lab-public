"""Fixed historical calibration. No optimization, validation or order authority.

Historical definitions are frozen verbatim from hash-pinned repository history.
Current execution uses the existing one-use, exact-input preliminary capability.
Neither execution gate nor the current fill model is changed.
"""
from __future__ import annotations

import csv
from dataclasses import asdict
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import types

import pandas as pd
import youtube_strategy_engine as engine
from systematic_trader.preliminary_scope import _authorize, _TOKEN

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / 'fixtures/backtest_parity'
LEGACY_COMMIT = 'c08fe5f7a169b3a544e9f08037699be131704ba0'
LEGACY_SHA256 = '2633ccc097b67c191c66b6fbf65363eae605e4ed77d52f127c5b8deb9d1f14b5'
FROZEN_LEGACY_SHA256 = '55843eb10e65cc50524889cea7f562150cec646d45c74c77417a42dce3662160'
CALIBRATION_FAILED = 'DISCOVERY / BACKTESTER CALIBRATION FAILED'
LEGACY_FLAGS = dict(max_concurrent_positions=1, allow_extended_hours=False,
                    extended_hours_position_scale=0.25, ignore_strategy_session_end=False,
                    allow_price_extension_after_qualification=False,
                    require_pullback_breakout_for_pullback_strategies=False)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False, default=str).encode()).hexdigest()


def historical_engine():
    name = '_trading_lab_pinned_aug25'
    if name not in sys.modules:
        source = (FIXTURES/'legacy_aug25_engine.py').read_bytes()
        if hashlib.sha256(source).hexdigest() != FROZEN_LEGACY_SHA256:
            raise ValueError('Historical engine provenance mismatch')
        module = types.ModuleType(name)
        sys.modules[name] = module
        try:
            exec(compile(source, str(FIXTURES/'legacy_aug25_engine.py'), 'exec'), module.__dict__)
        except BaseException:
            del sys.modules[name]
            raise
    return sys.modules[name]


def fixed_settings(values):
    settings = engine.BacktestSettings(**{**LEGACY_FLAGS, **values})
    if any(isinstance(v, (int, float)) and not math.isfinite(v) for v in asdict(settings).values()):
        raise ValueError('Settings must be finite')
    settings.validate()
    if settings.max_concurrent_positions != 1:
        raise ValueError('This historical parity path freezes one position; layered execution is a separate experiment')
    if settings.require_pullback_breakout_for_pullback_strategies:
        raise ValueError('Historical parity freezes the old executable rules, without inferred pullback conditions')
    return settings


def validate_rows(rows):
    """Bar-horizon checks only: no quote-ordering/microstructure requirement."""
    if not isinstance(rows, list) or not 3 <= len(rows) <= 20000:
        raise ValueError('A fixed input of 3 to 20000 bars is required')
    previous = None
    for row in rows:
        stamp = pd.Timestamp(row['t'])
        if stamp.tzinfo is None or (previous is not None and stamp <= previous):
            raise ValueError('Bars must have unique, increasing timezone-aware timestamps')
        previous = stamp
        values = [float(row[k]) for k in ('o', 'h', 'l', 'c', 'v')]
        if any(not math.isfinite(v) for v in values):
            raise ValueError('Nonfinite candle')
        o, h, l, c, v = values
        if min(o, h, l, c) <= 0 or v < 0 or l > min(o, c) or h < max(o, c) or h < l:
            raise ValueError('Invalid OHLCV candle')


def control_inputs():
    saved = json.loads((FIXTURES / 'sdot_20260825_strategy.json').read_text())
    reconstructed = json.loads((ROOT / 'reports/sdot_20260825_initial_replay.json').read_text())
    return saved['strategy'], reconstructed['bars'], reconstructed


def current_fixed(rows, strategy, symbol, settings):
    validate_rows(rows)
    with _authorize(rows, strategy, symbol, settings, _TOKEN):
        return engine.run_backtest(rows, strategy, symbol, settings)


def _trace(module, rows, strategy, settings, result, *, legacy=False):
    frame = module.bars_to_frame(rows) if legacy else module.bars_to_frame(rows, include_extended_hours=settings.allow_extended_hours)
    data = module.add_indicators(frame, strategy)
    rules = module.normalize_machine_rules(strategy.get('machine_rules'))
    records = json.loads(data.to_json(orient='records', date_format='iso'))
    for record, (_, row) in zip(records, data.iterrows()):
        record['timestamp'] = engine.isoformat_utc(row['timestamp'].to_pydatetime())
        record['signal'] = bool(module.evaluate_signal(row, rules) if legacy else module.evaluate_signal(
            row, rules, ignore_session_end=settings.ignore_strategy_session_end,
            allow_price_extension=settings.allow_price_extension_after_qualification,
            price_extension_unlocked=False, require_pullback_breakout=False))
        record['entries'] = [dict(trade=i + 1, price=t['entry_price'], shares=t['quantity'],
                                  stop=t['stop_price'], target=t['target_price'])
                             for i, t in enumerate(result['trades']) if t['entry_time'] == record['timestamp']]
        record['exits'] = [dict(trade=i + 1, reason=t['reason'], price=t['exit_price'])
                           for i, t in enumerate(result['trades']) if t['exit_time'] == record['timestamp']]
        record['signal_note'] = 'Signal uses this completed candle; entry requires a next available same-session bar and a free position'
    return records


def detailed_ledger(module, rows, strategy, settings, result, *, legacy=False):
    """Recover unrounded raw/cost components from fixed single-position fills.

Do not infer fills for modern dynamic exits; this path explicitly rejects them.
The rounded result P/L is independently reconciled to exact-price arithmetic.
"""
    rules = module.normalize_machine_rules(strategy.get('machine_rules'))
    unsupported = set(rules) - set(historical_engine().normalize_machine_rules({}))
    if any(rules[k] is not None and rules[k] is not False for k in unsupported):
        raise ValueError('Detailed legacy parity ledger supports historical fixed-exit rules only')
    frame = module.bars_to_frame(rows) if legacy else module.bars_to_frame(rows, include_extended_hours=settings.allow_extended_hours)
    bars = list(frame.to_dict('records'))
    by_time = {engine.isoformat_utc(r['timestamp'].to_pydatetime()): i for i, r in enumerate(bars)}
    friction = (settings.spread_bps / 2 + settings.slippage_bps) / 10000
    stop_pct = rules.get('stop_loss_pct') or settings.default_stop_pct
    reward = rules.get('reward_risk') or settings.default_reward_risk
    equity = settings.starting_cash
    ledger = []
    for number, t in enumerate(result['trades'], 1):
        entry_i, exit_i = by_time[t['entry_time']], by_time[t['exit_time']]
        raw_entry = float(bars[entry_i]['open'])
        entry = raw_entry * (1 + friction)
        stop = entry * (1 - stop_pct / 100)
        target = entry + (entry - stop) * reward
        exit_bar = bars[exit_i]
        if t['reason'] == 'Stop loss':
            raw_exit = min(float(exit_bar['open']), stop)
        elif t['reason'] == 'Profit target':
            raw_exit = max(float(exit_bar['open']), target)
        elif t['reason'] == 'Time limit':
            raw_exit = float(exit_bar['close' if legacy else 'open'])
        elif t['reason'] in ('End of session', 'End of available data'):
            raw_exit = float(exit_bar['close'])
        else:
            raise ValueError('Unsupported exit in historical ledger: ' + t['reason'])
        effective_exit = raw_exit * (1 - friction)
        shares = t['quantity']
        gross = (raw_exit - raw_entry) * shares
        costs = ((entry - raw_entry) + (raw_exit - effective_exit)) * shares + 2 * settings.fee_per_order
        net = (effective_exit - entry) * shares - 2 * settings.fee_per_order
        if round(net, 2) != t['pnl']:
            raise ValueError('Unreconciled fill at trade ' + str(number))
        equity += net
        ledger.append(dict(trade_number=number, session=bars[entry_i]['session'],
            signal_timestamp=engine.isoformat_utc(bars[entry_i - 1]['timestamp'].to_pydatetime()),
            entry_timestamp=t['entry_time'], raw_entry_price=raw_entry, effective_entry_price=entry,
            shares=shares, stop=stop, target=target, exit_timestamp=t['exit_time'], exit_reason=t['reason'],
            raw_exit_price=raw_exit, effective_exit_price=effective_exit, gross_pnl=gross,
            transaction_costs=costs, fees=2 * settings.fee_per_order, net_pnl=net,
            displayed_net_pnl=t['pnl'], account_equity_after_trade=equity))
    return ledger


def first_divergence(reference, current):
    """Compare bars, legacy indicators, signal and execution in temporal order."""
    for index in range(max(len(reference), len(current))):
        a = reference[index] if index < len(reference) else None
        b = current[index] if index < len(current) else None
        if a is None or b is None:
            return dict(stage='bar', index=index, reference=a, current=b)
        for stage, fields in (
            ('bar', ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'session']),
            ('indicator', [k for k in a if k in b and k not in {'timestamp', 'open', 'high', 'low', 'close', 'volume', 'session', 'entries', 'exits', 'signal', 'signal_note'}]),
            ('signal', ['signal']), ('execution', ['entries', 'exits'])):
            for field in fields:
                if a.get(field) != b.get(field):
                    return dict(stage=stage, timestamp=a['timestamp'], field=field,
                                reference=a.get(field), current=b.get(field), index=index)
    return None


def run_parity(rows, strategy, symbol, settings_values, *, provenance):
    if not provenance or not provenance.get('source_kind'):
        raise ValueError('Explicit candle provenance is required')
    validate_rows(rows)
    settings = fixed_settings(settings_values)
    old = historical_engine()
    raw_rules = strategy.get('machine_rules')
    if not isinstance(raw_rules, dict):
        raise ValueError('Exact executable rules must be an object')
    effective_rules = old.normalize_machine_rules(raw_rules)
    for key, value in raw_rules.items():
        if value is not None and (key not in effective_rules or effective_rules[key] != value):
            raise ValueError('Historical normalization would change or ignore rule: ' + key)
    old_values = {k: v for k, v in asdict(settings).items() if k in old.BacktestSettings.__dataclass_fields__}
    old_settings = old.BacktestSettings(**old_values)
    legacy_result = old.run_backtest(rows, strategy, symbol, old_settings)
    current_result = current_fixed(rows, strategy, symbol, settings)
    legacy_trace = _trace(old, rows, strategy, old_settings, legacy_result, legacy=True)
    current_trace = _trace(engine, rows, strategy, settings, current_result)
    divergence = first_divergence(legacy_trace, current_trace)
    legacy_ledger = detailed_ledger(old, rows, strategy, old_settings, legacy_result, legacy=True)
    current_ledger = detailed_ledger(engine, rows, strategy, settings, current_result)
    ledger_match = legacy_ledger == current_ledger
    return dict(version='manual-parity-v1', research_only=True, orders_enabled=False,
        execution_authority='none', production_eligible=False, fixed_configuration=True,
        symbol=symbol, strategy=strategy, settings=asdict(settings), provenance=provenance,
        effective_rules=effective_rules,
        input_hash=digest(dict(rows=rows, strategy=strategy, settings=asdict(settings))),
        current_engine_sha256=hashlib.sha256((ROOT/'youtube_strategy_engine.py').read_bytes()).hexdigest(),
        legacy_engine_commit=LEGACY_COMMIT, legacy_engine_sha256=LEGACY_SHA256,
        execution_parity='PASS' if ledger_match and divergence is None else 'FAIL',
        first_divergence=divergence, legacy_result=legacy_result, current_result=current_result,
        legacy_ledger=legacy_ledger, current_ledger=current_ledger,
        legacy_bar_trace=legacy_trace, current_bar_trace=current_trace)


def ledger_csv(ledger):
    output = io.StringIO()
    if ledger:
        writer = csv.DictWriter(output, fieldnames=list(ledger[0]))
        writer.writeheader()
        writer.writerows(ledger)
    return output.getvalue()


def guarded_verdict(verdict):
    """A failed candidate test is not a calibrated market-wide conclusion.

No current saved discovery result carries authenticated, scope-bound execution
and blinded-coverage evidence. Fail closed; never trust caller-supplied PASS flags.
Positive research descriptions and all promotion/validation gates stay intact.
"""
    negative = {'no_robust_strategy', 'no_reliable_edge', 'no_profitable_strategy'}
    text = str(verdict.get('label', '')).lower()
    if verdict.get('code') not in negative and not any(s in text for s in (
        'no reliable edge', 'no profitable strategy', 'no robust strategy')):
        return dict(verdict)
    return dict(code='calibration_failed', label=CALIBRATION_FAILED, tone='warning',
                research_tier='calibration_failed', paper_ready=False,
                reason='No strategy conclusion permitted: scope-matched backtester and blinded discovery calibration have not both passed. The recorded candidate validation results remain available.',
                candidate_test_verdict=dict(verdict), strategy_conclusion_permitted=False)
