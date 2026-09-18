"""Raw-bar calibration controls for the Trading Lab backtest/validation path.

Unlike validation_calibration_audit.py, these controls do not inject finished
performance metrics. They generate deterministic OHLCV bars, execute the real
run_backtest path, and then pass those resulting metrics into validation_strength.
This helps distinguish a scoring/gating problem from a signal/backtest problem.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from trading_validation_core import validation_strength
from youtube_strategy_engine import BacktestSettings, normalize_machine_rules, run_backtest


ET = ZoneInfo("America/New_York")


def _bar(timestamp: datetime, opening: float, high: float, low: float, close: float, volume: int) -> dict[str, Any]:
    return {
        "t": timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "o": round(opening, 4),
        "h": round(high, 4),
        "l": round(low, 4),
        "c": round(close, 4),
        "v": volume,
    }


def synthetic_trend_rows(
    *,
    sessions: int = 72,
    bars_per_session: int = 18,
    direction: str = "up",
) -> list[dict[str, Any]]:
    """Build deterministic intraday bars with a planted long edge or anti-edge."""
    if direction not in {"up", "down"}:
        raise ValueError("direction must be 'up' or 'down'")
    rows: list[dict[str, Any]] = []
    day = datetime(2026, 1, 5, 9, 30, tzinfo=ET)
    session_index = 0
    while session_index < sessions:
        if day.weekday() >= 5:
            day += timedelta(days=1)
            continue
        start = 10.0 + session_index * 0.03
        price = start
        for minute in range(bars_per_session):
            timestamp = day + timedelta(minutes=minute * 5)
            opening = price
            move = 0.006 if direction == "up" else -0.006
            close = max(1.0, opening * (1.0 + move))
            if direction == "up":
                high = close * 1.002
                low = opening * 0.998
            else:
                high = opening * 1.002
                low = close * 0.998
            rows.append(
                _bar(
                    timestamp,
                    opening,
                    high,
                    low,
                    close,
                    100_000 + minute * 1_000,
                )
            )
            price = close
        session_index += 1
        day += timedelta(days=1)
    return rows


def calibration_strategy() -> dict[str, Any]:
    return {
        "id": "raw-calibration-long",
        "name": "Raw Calibration Long",
        "direction": "long",
        "machine_rules": normalize_machine_rules(
            {
                "min_price": 1.0,
                "min_day_change_pct": -100.0,
                "stop_loss_pct": 1.0,
                "reward_risk": 1.0,
                "session_start": "09:30",
                "session_end": "15:55",
            }
        ),
        "unresolved_rules": [],
    }


def calibration_settings(*, spread_bps: float = 0.0, slippage_bps: float = 0.0) -> BacktestSettings:
    return BacktestSettings(
        starting_cash=10_000.0,
        risk_per_trade_pct=0.5,
        max_position_pct=20.0,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        allow_extended_hours=False,
    )


def _run(rows: list[dict[str, Any]], *, spread_bps: float = 0.0, slippage_bps: float = 0.0) -> dict[str, Any]:
    return run_backtest(
        rows,
        calibration_strategy(),
        "CAL",
        calibration_settings(spread_bps=spread_bps, slippage_bps=slippage_bps),
    )


def _split_by_sessions(rows: list[dict[str, Any]], parts: int = 3) -> list[list[dict[str, Any]]]:
    sessions: list[str] = []
    by_session: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        timestamp = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).astimezone(ET)
        key = timestamp.date().isoformat()
        if key not in by_session:
            sessions.append(key)
            by_session[key] = []
        by_session[key].append(row)
    width = len(sessions) // parts
    result: list[list[dict[str, Any]]] = []
    for index in range(parts):
        start = index * width
        stop = len(sessions) if index == parts - 1 else (index + 1) * width
        selected = sessions[start:stop]
        result.append([row for key in selected for row in by_session[key]])
    return result


def run_raw_engine_calibration() -> dict[str, Any]:
    favorable_rows = synthetic_trend_rows(direction="up")
    adverse_rows = synthetic_trend_rows(direction="down")

    favorable = _run(favorable_rows)
    adverse = _run(adverse_rows)
    favorable_metrics = favorable.get("metrics") or {}
    adverse_metrics = adverse.get("metrics") or {}

    temporal_results = [_run(part) for part in _split_by_sessions(favorable_rows, 3)]
    temporal_metrics = [item.get("metrics") or {} for item in temporal_results]
    profitable_temporal_blocks = sum(
        1 for metrics in temporal_metrics if float(metrics.get("net_pnl") or 0.0) > 0.0
    )
    temporal_trade_count = sum(int(metrics.get("trade_count") or 0) for metrics in temporal_metrics)

    training, validation, holdout = temporal_metrics
    stress_metrics = (
        _run(_split_by_sessions(favorable_rows, 3)[-1], spread_bps=10.0, slippage_bps=5.0).get("metrics")
        or {}
    )
    optimization_report = {
        "winner": {
            "status": "VALIDATED",
            "training_metrics": training,
            "validation_metrics": validation,
            "holdout_metrics": holdout,
            "stress_metrics": stress_metrics,
        },
        "optimization_settings": {
            "minimum_validation_trades": 2,
            "maximum_drawdown_pct": 15.0,
        },
    }
    walk_forward_summary = {
        "score": 85.0,
        "fold_count": 3,
        "active_fold_count": sum(1 for metrics in temporal_metrics if int(metrics.get("trade_count") or 0) > 0),
        "profitable_fold_count": profitable_temporal_blocks,
        "profitable_fold_pct": round(profitable_temporal_blocks / 3 * 100.0, 1),
        "external_trade_count": temporal_trade_count,
    }
    strength = validation_strength(
        optimization_report,
        {"summary": walk_forward_summary},
    )

    favorable_pass = bool(
        int(favorable_metrics.get("trade_count") or 0) >= 30
        and float(favorable_metrics.get("net_pnl") or 0.0) > 0.0
        and profitable_temporal_blocks == 3
        and int(validation.get("trade_count") or 0) >= 15
        and int(holdout.get("trade_count") or 0) >= 15
        and float(stress_metrics.get("net_pnl") or 0.0) > 0.0
        and bool(strength.get("independently_positive"))
        and float(strength.get("score") or 0.0) >= 70.0
    )
    adverse_rejected = bool(
        float(adverse_metrics.get("net_pnl") or 0.0) <= 0.0
        or int(adverse_metrics.get("trade_count") or 0) == 0
    )

    return {
        "audit": "raw_backtest_validation_calibration",
        "favorable_control_pass": favorable_pass,
        "adverse_control_rejected": adverse_rejected,
        "overall_pass": bool(favorable_pass and adverse_rejected),
        "favorable_metrics": favorable_metrics,
        "adverse_metrics": adverse_metrics,
        "temporal_metrics": temporal_metrics,
        "stress_metrics": stress_metrics,
        "validation_strength": strength,
        "note": (
            "These controls traverse the real OHLCV -> indicator/signal -> trade simulation -> metrics -> "
            "validation-strength path. They are deterministic calibration fixtures, not claims about "
            "a real market strategy."
        ),
    }
