"""Frozen August 25 historical functions; research parity only.

Verbatim definitions from c08fe5f; see legacy_aug25_manifest.json.
This intentionally retains old entry-candle behavior. Never a live engine.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any
from statistics import mean
import pandas as pd
import math
import re

ET = ZoneInfo("America/New_York")


UTC = timezone.utc


class AppError(RuntimeError):
    """An actionable error appropriate for displaying inside the application."""


def isoformat_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None or isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def safe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.strip().lower() in {"true", "yes", "1"}:
            return True
        if value.strip().lower() in {"false", "no", "0"}:
            return False
    return None


NULLABLE_NUMBER = {"type": ["number", "null"]}


NULLABLE_INTEGER = {"type": ["integer", "null"]}


NULLABLE_BOOLEAN = {"type": ["boolean", "null"]}


NULLABLE_STRING = {"type": ["string", "null"]}


MACHINE_RULE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "min_price": NULLABLE_NUMBER,
        "max_price": NULLABLE_NUMBER,
        "min_day_change_pct": NULLABLE_NUMBER,
        "min_relative_volume": NULLABLE_NUMBER,
        "min_dollar_volume": NULLABLE_NUMBER,
        "max_spread_pct": NULLABLE_NUMBER,
        "above_vwap": NULLABLE_BOOLEAN,
        "vwap_reclaim": NULLABLE_BOOLEAN,
        "max_vwap_distance_pct": NULLABLE_NUMBER,
        "breakout_lookback_bars": NULLABLE_INTEGER,
        "opening_range_minutes": NULLABLE_INTEGER,
        "volume_surge_ratio": NULLABLE_NUMBER,
        "minimum_green_bars": NULLABLE_INTEGER,
        "stop_loss_pct": NULLABLE_NUMBER,
        "reward_risk": NULLABLE_NUMBER,
        "max_hold_minutes": NULLABLE_INTEGER,
        "session_start": NULLABLE_STRING,
        "session_end": NULLABLE_STRING,
        "catalyst_required": NULLABLE_BOOLEAN,
    },
}


def normalize_machine_rules(raw_rules: dict[str, Any] | None) -> dict[str, Any]:
    raw_rules = raw_rules if isinstance(raw_rules, dict) else {}
    result: dict[str, Any] = {}
    number_fields = {
        "min_price", "max_price", "min_day_change_pct", "min_relative_volume", "min_dollar_volume",
        "max_spread_pct", "max_vwap_distance_pct", "volume_surge_ratio", "stop_loss_pct", "reward_risk",
    }
    integer_fields = {"breakout_lookback_bars", "opening_range_minutes", "minimum_green_bars", "max_hold_minutes"}
    boolean_fields = {"above_vwap", "vwap_reclaim", "catalyst_required"}
    for name in MACHINE_RULE_SCHEMA["properties"]:
        value = raw_rules.get(name)
        if name in number_fields:
            result[name] = safe_float(value)
        elif name in integer_fields:
            numeric = safe_float(value)
            result[name] = max(1, int(numeric)) if numeric is not None and numeric >= 1 else None
        elif name in boolean_fields:
            result[name] = safe_bool(value)
        else:
            text = str(value).strip() if value is not None else ""
            result[name] = text if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text) else None

    for name in {"min_price", "max_price", "min_relative_volume", "min_dollar_volume", "max_spread_pct", "max_vwap_distance_pct", "volume_surge_ratio", "stop_loss_pct", "reward_risk"}:
        if result[name] is not None and result[name] < 0:
            result[name] = None
    if result["stop_loss_pct"] is not None and not 0 < result["stop_loss_pct"] < 100:
        result["stop_loss_pct"] = None
    if result["reward_risk"] is not None and result["reward_risk"] <= 0:
        result["reward_risk"] = None
    if result["min_price"] is not None and result["max_price"] is not None and result["min_price"] > result["max_price"]:
        result["min_price"], result["max_price"] = result["max_price"], result["min_price"]
    return result


def bars_to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    columns = ["open", "high", "low", "close", "volume", "timestamp", "session", "session_minute"]
    if not rows:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(rows).rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "t": "timestamp"})
    required = {"open", "high", "low", "close", "volume", "timestamp"}
    if not required.issubset(frame.columns):
        return pd.DataFrame(columns=columns)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    for name in ("open", "high", "low", "close", "volume"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame.dropna(subset=list(required)).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    frame = frame[(frame["open"] > 0) & (frame["high"] > 0) & (frame["low"] > 0) & (frame["close"] > 0) & (frame["volume"] >= 0)].copy()
    local = frame["timestamp"].dt.tz_convert(ET)
    minute = local.dt.hour * 60 + local.dt.minute
    mask = (minute >= 9 * 60 + 30) & (minute < 16 * 60)
    frame = frame.loc[mask].copy().reset_index(drop=True)
    local = frame["timestamp"].dt.tz_convert(ET)
    frame["session"] = local.dt.date.astype(str)
    frame["session_minute"] = local.dt.hour * 60 + local.dt.minute - (9 * 60 + 30)
    return frame


def resample_intraday_bars(rows: list[dict[str, Any]], timeframe: str) -> list[dict[str, Any]]:
    """Derive session-safe 1-, 5-, or 15-minute candles from one-minute history."""
    interval = {"1Min": 1, "5Min": 5, "15Min": 15}.get(str(timeframe or ""))
    if interval is None:
        raise AppError("Choose a supported candle interval: 1Min, 5Min, or 15Min.")
    frame = bars_to_frame(rows)
    if frame.empty:
        return []
    grouped = frame.copy()
    grouped["bucket"] = grouped["session_minute"] // interval
    combined = (
        grouped.groupby(["session", "bucket"], sort=False, as_index=False)
        .agg(
            timestamp=("timestamp", "first"),
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .sort_values("timestamp")
    )
    return [
        {
            "t": isoformat_utc(row.timestamp.to_pydatetime()),
            "o": float(row.open),
            "h": float(row.high),
            "l": float(row.low),
            "c": float(row.close),
            "v": float(row.volume),
        }
        for row in combined.itertuples(index=False)
    ]


def add_indicators(frame: pd.DataFrame, strategy: dict[str, Any]) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    data = frame.copy().sort_values("timestamp").reset_index(drop=True)
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    session = data.groupby("session", sort=False)
    typical = (data["high"] + data["low"] + data["close"]) / 3.0
    data["cum_volume"] = session["volume"].cumsum()
    data["cum_dollar_volume"] = (typical * data["volume"]).groupby(data["session"], sort=False).cumsum()
    data["vwap"] = data["cum_dollar_volume"].div(data["cum_volume"].replace(0, float("nan")))
    data["vwap_distance_pct"] = (data["close"].div(data["vwap"]) - 1.0) * 100.0
    data["previous_close"] = data.groupby("session", sort=False)["close"].shift(1)
    data["previous_vwap"] = data.groupby("session", sort=False)["vwap"].shift(1)
    daily_close = data.groupby("session", sort=False)["close"].last()
    previous_daily_close = daily_close.shift(1).to_dict()
    data["previous_daily_close"] = data["session"].map(previous_daily_close)
    data["day_change_pct"] = (data["close"].div(data["previous_daily_close"]) - 1.0) * 100.0
    historical_session_volume = data.groupby("session", sort=False)["volume"].sum().shift(1).rolling(20, min_periods=1).mean().to_dict()
    data["avg_daily_volume"] = data["session"].map(historical_session_volume)
    session_fraction = ((data["session_minute"] + 1) / 390.0).clip(lower=1 / 390.0, upper=1.0)
    data["relative_volume"] = data["cum_volume"].div(data["avg_daily_volume"] * session_fraction)
    rolling_volume = data.groupby("session", sort=False)["volume"].transform(lambda series: series.shift(1).rolling(20, min_periods=3).mean())
    data["volume_surge"] = data["volume"].div(rolling_volume.replace(0, float("nan")))
    lookback = int(rules.get("breakout_lookback_bars") or 20)
    data["prior_breakout_high"] = data.groupby("session", sort=False)["high"].transform(
        lambda series: series.shift(1).rolling(lookback, min_periods=lookback).max()
    )
    opening_minutes = int(rules.get("opening_range_minutes") or 15)
    opening_only = data["high"].where(data["session_minute"] < opening_minutes)
    opening_high = opening_only.groupby(data["session"], sort=False).transform("max")
    data["opening_range_high"] = opening_high.where(data["session_minute"] >= opening_minutes)
    green = (data["close"] > data["open"]).astype(int)
    run_lengths = green.groupby([data["session"], (green == 0).cumsum()]).cumsum()
    data["green_streak"] = run_lengths
    return data


def parse_clock_minutes(value: str | None) -> int | None:
    if not value:
        return None
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(value)):
        return None
    hours, minutes = map(int, str(value).split(":"))
    return hours * 60 + minutes


def backtest_limitations(strategy: dict[str, Any]) -> list[str]:
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    limitations = list(dict.fromkeys(str(item) for item in strategy.get("unresolved_rules") or [] if str(item).strip()))
    if rules.get("catalyst_required"):
        limitations.append("Historical, point-in-time news catalysts are not included in this backtest.")
    if rules.get("max_spread_pct") is not None:
        limitations.append("Historical bid/ask quotes are unavailable; the spread limit is estimated through configured trading costs.")
    if str(strategy.get("direction", "long")).lower() not in {"long", "both"}:
        limitations.append("This release evaluates long trades only; short-only strategies cannot be backtested.")
    if rules.get("stop_loss_pct") is None:
        limitations.append("The video did not specify an exact stop; the editable default stop is a research assumption.")
    if rules.get("reward_risk") is None:
        limitations.append("The video did not specify an exact target; the editable reward/risk setting is a research assumption.")
    return list(dict.fromkeys(limitations))


def evaluate_signal(row: pd.Series, rules: dict[str, Any]) -> bool:
    def has_number(name: str) -> bool:
        return pd.notna(row.get(name))

    close = safe_float(row.get("close"))
    if close is None or close <= 0:
        return False
    comparisons = [
        ("min_price", "close", lambda actual, target: actual >= target),
        ("max_price", "close", lambda actual, target: actual <= target),
        ("min_day_change_pct", "day_change_pct", lambda actual, target: actual >= target),
        ("min_relative_volume", "relative_volume", lambda actual, target: actual >= target),
        ("min_dollar_volume", "cum_dollar_volume", lambda actual, target: actual >= target),
        ("max_vwap_distance_pct", "vwap_distance_pct", lambda actual, target: actual <= target),
        ("volume_surge_ratio", "volume_surge", lambda actual, target: actual >= target),
        ("minimum_green_bars", "green_streak", lambda actual, target: actual >= target),
    ]
    for rule_name, field_name, comparator in comparisons:
        threshold = rules.get(rule_name)
        if threshold is None:
            continue
        if not has_number(field_name) or not comparator(float(row[field_name]), float(threshold)):
            return False

    if rules.get("above_vwap") is True and (not has_number("vwap") or close <= float(row["vwap"])):
        return False
    if rules.get("above_vwap") is False and (not has_number("vwap") or close >= float(row["vwap"])):
        return False
    if rules.get("vwap_reclaim"):
        if not all(has_number(name) for name in ("previous_close", "previous_vwap", "vwap")):
            return False
        if not (float(row["previous_close"]) <= float(row["previous_vwap"]) and close > float(row["vwap"])):
            return False
    if rules.get("breakout_lookback_bars") is not None:
        if not has_number("prior_breakout_high") or close <= float(row["prior_breakout_high"]):
            return False
    if rules.get("opening_range_minutes") is not None:
        if not has_number("opening_range_high") or close <= float(row["opening_range_high"]):
            return False

    clock_minute = 9 * 60 + 30 + int(row.get("session_minute", 0))
    session_start = parse_clock_minutes(rules.get("session_start"))
    session_end = parse_clock_minutes(rules.get("session_end"))
    if session_start is not None and clock_minute < session_start:
        return False
    if session_end is not None and clock_minute > session_end:
        return False
    return True


@dataclass
class BacktestSettings:
    starting_cash: float = 10_000.0
    risk_per_trade_pct: float = 0.5
    max_position_pct: float = 20.0
    default_stop_pct: float = 2.0
    default_reward_risk: float = 2.0
    spread_bps: float = 12.0
    slippage_bps: float = 8.0
    fee_per_order: float = 0.0
    train_fraction: float = 0.7

    def validate(self) -> None:
        if self.starting_cash <= 0:
            raise AppError("Starting cash must be greater than zero.")
        if not 0 < self.risk_per_trade_pct <= 100:
            raise AppError("Risk per trade must be greater than zero and no more than 100%.")
        if not 0 < self.max_position_pct <= 100:
            raise AppError("Maximum position size must be greater than zero and no more than 100%.")
        if not 0 < self.default_stop_pct < 100 or self.default_reward_risk <= 0:
            raise AppError("The default stop and reward/risk settings must be positive.")
        if min(self.spread_bps, self.slippage_bps, self.fee_per_order) < 0:
            raise AppError("Spread, slippage, and fees cannot be negative.")
        if not 0 < self.train_fraction < 1:
            raise AppError("The in-sample fraction must be between zero and one.")


def _empty_backtest(settings: BacktestSettings, strategy: dict[str, Any], symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "strategy_id": strategy.get("id"),
        "strategy_name": strategy.get("name", "Unnamed strategy"),
        "settings": asdict(settings),
        "limitations": backtest_limitations(strategy),
        "trades": [],
        "equity_curve": [{"timestamp": None, "equity": settings.starting_cash}],
        "metrics": summarize_trades([], settings.starting_cash),
        "in_sample": summarize_trades([], settings.starting_cash),
        "out_of_sample": summarize_trades([], settings.starting_cash),
        "sessions": 0,
    }


def summarize_trades(trades: list[dict[str, Any]], starting_cash: float) -> dict[str, Any]:
    if not trades:
        return {
            "trade_count": 0,
            "win_rate_pct": 0.0,
            "net_pnl": 0.0,
            "return_pct": 0.0,
            "average_trade": 0.0,
            "profit_factor": None,
            "max_drawdown_pct": 0.0,
            "average_winner": 0.0,
            "average_loser": 0.0,
        }
    pnl = [safe_float(item.get("pnl"), 0.0) or 0.0 for item in trades]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    equity = starting_cash
    peak = starting_cash
    max_drawdown = 0.0
    for value in pnl:
        equity += value
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100.0)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    return {
        "trade_count": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100.0, 2),
        "net_pnl": round(sum(pnl), 2),
        "return_pct": round(sum(pnl) / starting_cash * 100.0, 2) if starting_cash else 0.0,
        "average_trade": round(sum(pnl) / len(trades), 2),
        "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
        "max_drawdown_pct": round(max_drawdown, 2),
        "average_winner": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "average_loser": round(sum(losses) / len(losses), 2) if losses else 0.0,
    }


def run_backtest(
    rows: list[dict[str, Any]],
    strategy: dict[str, Any],
    symbol: str,
    settings: BacktestSettings | None = None,
    *,
    prepared_indicators: pd.DataFrame | None = None,
) -> dict[str, Any]:
    settings = settings or BacktestSettings()
    settings.validate()
    if str(strategy.get("direction", "long")).lower() not in {"long", "both"}:
        raise AppError("Short-only and unclear-direction strategies cannot be backtested in this long-only release.")
    result = _empty_backtest(settings, strategy, symbol)
    if prepared_indicators is not None:
        if len(prepared_indicators) < 3:
            return result
        data = prepared_indicators
    else:
        base = bars_to_frame(rows)
        if len(base) < 3:
            return result
        data = add_indicators(base, strategy)
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    stop_pct = rules.get("stop_loss_pct") or settings.default_stop_pct
    reward_risk = rules.get("reward_risk") or settings.default_reward_risk
    max_hold = rules.get("max_hold_minutes")
    sessions = list(dict.fromkeys(data["session"].tolist()))
    split_index = max(1, min(len(sessions) - 1, int(len(sessions) * settings.train_fraction))) if len(sessions) > 1 else 1
    holdout_sessions = set(sessions[split_index:]) if len(sessions) > 1 else set()
    cash = settings.starting_cash
    position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = [{"timestamp": str(data.iloc[0]["timestamp"]), "equity": round(cash, 2)}]
    execution_friction = (settings.spread_bps / 2.0 + settings.slippage_bps) / 10_000.0
    records = data.to_dict("records")

    for index in range(1, len(records)):
        current = records[index]
        previous = records[index - 1]
        if position is not None:
            reason: str | None = None
            raw_exit: float | None = None
            if current["session"] != position["session"]:
                raw_exit = float(previous["close"])
                exit_time = previous["timestamp"]
                reason = "End of session"
            else:
                exit_time = current["timestamp"]
                low = float(current["low"])
                high = float(current["high"])
                bar_open = float(current["open"])
                # Stops win ambiguous same-bar touches. Adverse opening gaps fill at the gap.
                if low <= position["stop_price"]:
                    raw_exit = min(bar_open, position["stop_price"])
                    reason = "Stop loss"
                elif high >= position["target_price"]:
                    raw_exit = max(bar_open, position["target_price"])
                    reason = "Profit target"
                elif max_hold is not None:
                    held_minutes = (current["timestamp"] - position["entry_time"]).total_seconds() / 60.0
                    if held_minutes >= max_hold:
                        raw_exit = float(current["close"])
                        reason = "Time limit"
                elif index == len(records) - 1:
                    raw_exit = float(current["close"])
                    reason = "End of available data"

            if reason and raw_exit is not None:
                fill_exit = raw_exit * (1.0 - execution_friction)
                gross = (fill_exit - position["entry_price"]) * position["quantity"]
                pnl = gross - settings.fee_per_order * 2.0
                cash += pnl
                trade = {
                    "symbol": symbol,
                    "entry_time": isoformat_utc(position["entry_time"].to_pydatetime()),
                    "exit_time": isoformat_utc(exit_time.to_pydatetime()),
                    "entry_price": round(position["entry_price"], 4),
                    "exit_price": round(fill_exit, 4),
                    "stop_price": round(position["stop_price"], 4),
                    "target_price": round(position["target_price"], 4),
                    "quantity": position["quantity"],
                    "pnl": round(pnl, 2),
                    "return_pct": round((fill_exit / position["entry_price"] - 1.0) * 100.0, 3),
                    "reason": reason,
                    "sample": "out_of_sample" if position["session"] in holdout_sessions else "in_sample",
                }
                trades.append(trade)
                curve.append({"timestamp": trade["exit_time"], "equity": round(cash, 2)})
                position = None
                # If a previous-session position was closed, the current bar can still
                # serve as the next open for yesterday's signal only when sessions match,
                # so the normal guard below prevents an overnight entry.
                if reason != "End of session":
                    continue

        if position is not None or previous["session"] != current["session"]:
            continue
        if not evaluate_signal(previous, rules):
            continue
        entry = float(current["open"]) * (1.0 + execution_friction)
        if entry <= 0 or cash <= 0:
            continue
        stop_price = entry * (1.0 - stop_pct / 100.0)
        risk_per_share = entry - stop_price
        if risk_per_share <= 0:
            continue
        risk_budget = cash * settings.risk_per_trade_pct / 100.0
        allocation_cap = cash * settings.max_position_pct / 100.0
        quantity = int(min(risk_budget / risk_per_share, allocation_cap / entry))
        if quantity < 1:
            continue
        position = {
            "entry_time": current["timestamp"],
            "entry_price": entry,
            "quantity": quantity,
            "stop_price": stop_price,
            "target_price": entry + risk_per_share * reward_risk,
            "session": current["session"],
        }

    # Close any remaining position using the final bar; never leave an invisible trade.
    if position is not None:
        final_row = records[-1]
        raw_exit = float(final_row["close"])
        fill_exit = raw_exit * (1.0 - execution_friction)
        pnl = (fill_exit - position["entry_price"]) * position["quantity"] - settings.fee_per_order * 2.0
        cash += pnl
        trade = {
            "symbol": symbol,
            "entry_time": isoformat_utc(position["entry_time"].to_pydatetime()),
            "exit_time": isoformat_utc(final_row["timestamp"].to_pydatetime()),
            "entry_price": round(position["entry_price"], 4),
            "exit_price": round(fill_exit, 4),
            "stop_price": round(position["stop_price"], 4),
            "target_price": round(position["target_price"], 4),
            "quantity": position["quantity"],
            "pnl": round(pnl, 2),
            "return_pct": round((fill_exit / position["entry_price"] - 1.0) * 100.0, 3),
            "reason": "End of available data",
            "sample": "out_of_sample" if position["session"] in holdout_sessions else "in_sample",
        }
        trades.append(trade)
        curve.append({"timestamp": trade["exit_time"], "equity": round(cash, 2)})

    in_sample = [trade for trade in trades if trade["sample"] == "in_sample"]
    out_sample = [trade for trade in trades if trade["sample"] == "out_of_sample"]
    holdout_start_cash = settings.starting_cash + sum(float(trade["pnl"]) for trade in in_sample)
    result.update(
        {
            "trades": trades,
            "equity_curve": curve,
            "metrics": summarize_trades(trades, settings.starting_cash),
            "in_sample": summarize_trades(in_sample, settings.starting_cash),
            "out_of_sample": summarize_trades(out_sample, max(holdout_start_cash, 0.01)),
            "sessions": len(sessions),
            "holdout_start": min(holdout_sessions) if holdout_sessions else None,
        }
    )
    return result


