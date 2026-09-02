from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd

import youtube_strategy_engine as engine


UTC = timezone.utc


def raw_rows(count: int = 80) -> list[dict]:
    start = datetime(2026, 8, 31, 13, 30, tzinfo=UTC)
    rows: list[dict] = []
    price = 10.0
    for index in range(count):
        opening = price
        closing = opening + (0.03 if index % 5 else -0.01)
        rows.append(
            {
                "t": (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
                "o": opening,
                "h": max(opening, closing) + 0.04,
                "l": min(opening, closing) - 0.03,
                "c": closing,
                "v": 100_000 + index * 250,
                "vw": (opening + closing) / 2.0,
                "n": 100 + index,
            }
        )
        price = closing
    return rows


def strategy(**updates) -> dict:
    rules = {
        "min_price": 1.0,
        "max_price": 50.0,
        "stop_loss_pct": 3.0,
        "reward_risk": 1.5,
        "breakout_lookback_bars": 10,
        "opening_range_minutes": 5,
        "fast_ema_period": 9,
        "pullback_touch_tolerance_pct": 0.5,
    }
    rules.update(updates)
    return {
        "id": "feature-reuse-test",
        "name": "Feature reuse test",
        "direction": "long",
        "machine_rules": rules,
    }


def test_indicator_signature_preserves_fail_closed_ema_tolerance():
    missing = strategy(pullback_touch_tolerance_pct=None)
    explicit = strategy(pullback_touch_tolerance_pct=0.5)
    assert engine.strategy_indicator_signature(missing) != engine.strategy_indicator_signature(explicit)


def test_indicator_signature_tracks_anchored_vwap_inputs():
    baseline = strategy(
        avwap_anchor_mode="swing_low",
        avwap_pivot_confirm_bars=2,
        avwap_pullback_tolerance_pct=0.5,
    )
    confirm_change = strategy(
        avwap_anchor_mode="swing_low",
        avwap_pivot_confirm_bars=4,
        avwap_pullback_tolerance_pct=0.5,
    )
    tolerance_change = strategy(
        avwap_anchor_mode="swing_low",
        avwap_pivot_confirm_bars=2,
        avwap_pullback_tolerance_pct=0.8,
    )
    assert engine.strategy_indicator_signature(baseline) != engine.strategy_indicator_signature(confirm_change)
    assert engine.strategy_indicator_signature(baseline) != engine.strategy_indicator_signature(tolerance_change)


def test_prepared_record_payload_is_backtest_equivalent():
    rows = raw_rows()
    candidate = strategy()
    settings = engine.BacktestSettings(
        starting_cash=2_000.0,
        risk_per_trade_pct=10.0,
        max_position_pct=100.0,
        allow_extended_hours=True,
    )
    frame = engine.bars_to_frame(rows, include_extended_hours=True)
    prepared = engine.add_indicators(frame, candidate)
    prepared_records, prepared_sessions = engine.prepare_backtest_payload(prepared)

    baseline = engine.run_backtest(
        [],
        candidate,
        "TEST",
        settings,
        prepared_indicators=prepared,
    )
    reused = engine.run_backtest(
        [],
        candidate,
        "TEST",
        settings,
        prepared_indicators=prepared,
        prepared_records=prepared_records,
        prepared_sessions=prepared_sessions,
    )

    assert reused["metrics"] == baseline["metrics"]
    assert reused["trades"] == baseline["trades"]
    assert reused["equity_curve"] == baseline["equity_curve"]


def test_screening_reuses_invariant_indicator_preparation():
    rows = raw_rows()
    candidate = strategy(
        avwap_anchor_mode="session_open",
        avwap_pullback_tolerance_pct=0.5,
    )
    settings = engine.BacktestSettings(
        starting_cash=2_000.0,
        risk_per_trade_pct=10.0,
        max_position_pct=100.0,
        allow_extended_hours=True,
    )
    original_add = engine.add_indicators
    original_specific = engine.apply_strategy_specific_indicators

    fake_result = {
        "metrics": {
            "trade_count": 8,
            "win_rate_pct": 50.0,
            "net_pnl": 10.0,
            "return_pct": 0.5,
            "average_trade": 1.25,
            "profit_factor": 1.2,
            "max_drawdown_pct": 1.0,
            "average_winner": 2.0,
            "average_loser": -1.0,
        }
    }

    with patch.object(engine, "add_indicators", wraps=original_add) as add_mock, patch.object(
        engine,
        "apply_strategy_specific_indicators",
        wraps=original_specific,
    ) as specific_mock, patch.object(engine, "run_backtest", return_value=fake_result):
        results = engine._screen_historical_strategies(
            rows,
            [candidate],
            "TEST",
            settings,
            maximum_drawdown_pct=20.0,
            minimum_historical_trades=3,
            automatic_slippage=False,
        )

    assert results
    # The stop/reward screening grid has dozens of trials. Invariant features
    # should be prepared once per session mode, not once per trial.
    assert add_mock.call_count <= 2
    assert specific_mock.call_count <= 2
