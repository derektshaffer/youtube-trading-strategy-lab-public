"""Live shadow-observation learning for the Trading Intelligence Lab.

This module is deliberately research-only. It records causal market features that
were available when a stock was evaluated, then fills in future outcomes later.
Nothing here changes live scanner rankings, strategy matching, or trade execution.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import math
from typing import Any, Iterable
from zoneinfo import ZoneInfo


DEFAULT_HORIZONS_MINUTES: tuple[int, ...] = (5, 15, 30, 60)
DEFAULT_PROFIT_TARGET_PCT = 1.0
DEFAULT_STOP_LOSS_PCT = 0.75
DEFAULT_BUCKET_MINUTES = 5
DEFAULT_MAX_OBSERVATIONS = 2500
EASTERN = ZoneInfo("America/New_York")


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value is None:
        return None
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _bar_time(row: dict[str, Any]) -> datetime | None:
    for key in ("t", "timestamp", "time"):
        parsed = _parse_time(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _bar_number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(row.get(key))
        if value is not None:
            return value
    return None


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        try:
            return _safe_scalar(value.item())
        except Exception:
            return None
    return str(value)


def _bucket_start(value: datetime, minutes: int) -> datetime:
    minutes = max(1, int(minutes))
    minute = value.minute - (value.minute % minutes)
    return value.replace(minute=minute, second=0, microsecond=0)


def _observation_id(symbol: str, observed_at: datetime, bucket_minutes: int) -> str:
    bucket = _bucket_start(observed_at, bucket_minutes)
    identity = f"{symbol.upper()}|{_iso(bucket)}"
    return hashlib.sha1(identity.encode("utf-8")).hexdigest()[:18]


def build_shadow_observation(
    result: dict[str, Any],
    *,
    source: str,
    observed_at: datetime | None = None,
    bucket_minutes: int = DEFAULT_BUCKET_MINUTES,
) -> dict[str, Any] | None:
    """Create one research-only causal observation from a scanner/analyzer result."""
    symbol = str(result.get("symbol") or "").strip().upper()
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    market_report = (
        result.get("market_features")
        if isinstance(result.get("market_features"), dict)
        else {}
    )
    features = (
        market_report.get("features")
        if isinstance(market_report.get("features"), dict)
        else {}
    )
    price = _number(metrics.get("price"))
    if not symbol or price is None or price <= 0:
        return None

    observed = observed_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    observed = observed.astimezone(timezone.utc)

    feature_row = {
        f"feature__{name}": _safe_scalar(value)
        for name, value in features.items()
    }
    strategy_matches = result.get("strategy_matches")
    if not isinstance(strategy_matches, list):
        strategy_matches = result.get("comparisons")
    if not isinstance(strategy_matches, list):
        strategy_matches = []

    best_strategy_id = result.get("best_strategy_id")
    best_strategy_name = result.get("best_strategy_name")
    best_status = result.get("status")
    best_score = result.get("score")
    if strategy_matches and not best_strategy_id:
        first = strategy_matches[0] if isinstance(strategy_matches[0], dict) else {}
        best_strategy_id = first.get("strategy_id")
        best_strategy_name = first.get("strategy_name")
        best_status = first.get("status")
        best_score = first.get("score")

    return {
        "id": _observation_id(symbol, observed, bucket_minutes),
        "symbol": symbol,
        "observed_at": _iso(observed),
        "feature_cutoff": _iso(observed),
        "session": observed.astimezone(EASTERN).date().isoformat(),
        "sources": [str(source or "live").strip() or "live"],
        "price": price,
        "features": feature_row,
        "market_metrics": {
            key: _safe_scalar(metrics.get(key))
            for key in (
                "price",
                "day_change_pct",
                "volume",
                "dollar_volume",
                "relative_volume",
                "vwap",
                "vwap_distance_pct",
                "above_vwap",
                "spread_pct",
                "high",
                "distance_from_high_pct",
                "quote_timestamp",
                "trade_timestamp",
            )
            if key in metrics
        },
        "context": {
            "best_strategy_id": best_strategy_id,
            "best_strategy_name": best_strategy_name,
            "strategy_status": best_status,
            "strategy_score": _number(best_score),
            "validation_status": result.get("validation_status"),
            "robustness_score": _number(result.get("robustness_score")),
            "has_catalyst": result.get("has_catalyst"),
            "news_count": int(result.get("news_count") or 0),
        },
        "outcomes": {},
        "outcome_status": "PENDING",
        "research_only": True,
        "affects_live_ranking": False,
    }


def build_scan_shadow_observations(
    results: Iterable[dict[str, Any]],
    *,
    source: str,
    observed_at: datetime | None = None,
    max_items: int = 50,
) -> list[dict[str, Any]]:
    """Build a bounded set of shadow observations from already-ranked live results."""
    records: list[dict[str, Any]] = []
    for item in list(results)[: max(1, int(max_items))]:
        if not isinstance(item, dict):
            continue
        record = build_shadow_observation(item, source=source, observed_at=observed_at)
        if record is not None:
            records.append(record)
    return records


def merge_shadow_observations(
    existing: Iterable[dict[str, Any]],
    incoming: Iterable[dict[str, Any]],
    *,
    max_records: int = DEFAULT_MAX_OBSERVATIONS,
) -> list[dict[str, Any]]:
    """Deduplicate same-stock/same-bucket observations and keep a bounded history."""
    merged: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []

    def add(raw: dict[str, Any]) -> None:
        if not isinstance(raw, dict):
            return
        record = deepcopy(raw)
        record_id = str(record.get("id") or "").strip()
        if not record_id:
            symbol = str(record.get("symbol") or "").strip().upper()
            observed = _parse_time(record.get("observed_at"))
            if not symbol or observed is None:
                return
            record_id = _observation_id(symbol, observed, DEFAULT_BUCKET_MINUTES)
            record["id"] = record_id
        if record_id not in merged:
            ordered_ids.append(record_id)
            merged[record_id] = record
            return
        prior = merged[record_id]
        sources = []
        for value in list(prior.get("sources") or []) + list(record.get("sources") or []):
            text = str(value or "").strip()
            if text and text not in sources:
                sources.append(text)
        prior["sources"] = sources
        if isinstance(record.get("outcomes"), dict) and record.get("outcomes"):
            prior["outcomes"] = deepcopy(record["outcomes"])
            prior["outcome_status"] = record.get("outcome_status") or prior.get("outcome_status")
        prior_context = prior.get("context") if isinstance(prior.get("context"), dict) else {}
        new_context = record.get("context") if isinstance(record.get("context"), dict) else {}
        for key, value in new_context.items():
            if prior_context.get(key) in (None, "", 0) and value not in (None, ""):
                prior_context[key] = value
        prior["context"] = prior_context

    for item in existing:
        add(item)
    for item in incoming:
        add(item)

    values = [merged[record_id] for record_id in ordered_ids]
    values.sort(key=lambda item: str(item.get("observed_at") or ""), reverse=True)
    return values[: max(1, int(max_records))]


def pending_symbols(
    observations: Iterable[dict[str, Any]],
    *,
    only_symbols: Iterable[str] | None = None,
) -> list[str]:
    allowed = None
    if only_symbols is not None:
        allowed = {str(symbol or "").strip().upper() for symbol in only_symbols}
    symbols = {
        str(item.get("symbol") or "").strip().upper()
        for item in observations
        if isinstance(item, dict)
        and str(item.get("outcome_status") or "PENDING").upper() != "COMPLETE"
        and (allowed is None or str(item.get("symbol") or "").strip().upper() in allowed)
    }
    return sorted(symbol for symbol in symbols if symbol)


def earliest_pending_observed_at(
    observations: Iterable[dict[str, Any]],
    *,
    only_symbols: Iterable[str] | None = None,
) -> datetime | None:
    allowed = None
    if only_symbols is not None:
        allowed = {str(symbol or "").strip().upper() for symbol in only_symbols}
    times: list[datetime] = []
    for item in observations:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if allowed is not None and symbol not in allowed:
            continue
        if str(item.get("outcome_status") or "PENDING").upper() == "COMPLETE":
            continue
        parsed = _parse_time(item.get("observed_at"))
        if parsed is not None:
            times.append(parsed)
    return min(times) if times else None


def _regular_session_close(observed_at: datetime) -> datetime:
    local = observed_at.astimezone(EASTERN)
    close_local = local.replace(hour=16, minute=0, second=0, microsecond=0)
    return close_local.astimezone(timezone.utc)


def _barrier_outcome(
    rows: list[dict[str, Any]],
    *,
    entry_price: float,
    profit_target_pct: float,
    stop_loss_pct: float,
) -> tuple[bool | None, str | None, int | None]:
    target = entry_price * (1.0 + profit_target_pct / 100.0)
    stop = entry_price * (1.0 - stop_loss_pct / 100.0)
    for index, row in enumerate(rows, start=1):
        high = _bar_number(row, "h", "high")
        low = _bar_number(row, "l", "low")
        touched_target = high is not None and high >= target
        touched_stop = low is not None and low <= stop
        if touched_stop:
            return False, "STOP_FIRST", index
        if touched_target:
            return True, "TARGET_FIRST", index
    return None, "NEITHER", None


def mature_shadow_observations(
    observations: Iterable[dict[str, Any]],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    now: datetime | None = None,
    horizons_minutes: tuple[int, ...] = DEFAULT_HORIZONS_MINUTES,
    profit_target_pct: float = DEFAULT_PROFIT_TARGET_PCT,
    stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
    only_symbols: Iterable[str] | None = None,
    bar_tolerance_seconds: int = 90,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Fill future outcomes for observations whose intraday horizons have elapsed."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    horizons = tuple(sorted({max(1, int(value)) for value in horizons_minutes}))
    allowed = None
    if only_symbols is not None:
        allowed = {str(symbol or "").strip().upper() for symbol in only_symbols}

    prepared_bars: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
    for symbol, rows in (bars_by_symbol or {}).items():
        clean_symbol = str(symbol or "").strip().upper()
        values = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            stamp = _bar_time(row)
            if stamp is not None:
                values.append((stamp, row))
        values.sort(key=lambda pair: pair[0])
        prepared_bars[clean_symbol] = values

    output: list[dict[str, Any]] = []
    summary = {"updated": 0, "completed": 0, "partial": 0, "pending": 0}

    for raw in observations:
        record = deepcopy(raw)
        if not isinstance(record, dict):
            continue
        symbol = str(record.get("symbol") or "").strip().upper()
        if allowed is not None and symbol not in allowed:
            output.append(record)
            continue
        observed = _parse_time(record.get("observed_at"))
        entry_price = _number(record.get("price"))
        if observed is None or entry_price is None or entry_price <= 0:
            output.append(record)
            continue

        outcomes = record.get("outcomes") if isinstance(record.get("outcomes"), dict) else {}
        before = deepcopy(outcomes)
        session_close = _regular_session_close(observed)
        symbol_bars = prepared_bars.get(symbol, [])

        for horizon in horizons:
            key = str(horizon)
            existing = outcomes.get(key)
            if isinstance(existing, dict) and existing.get("status") in {"EVALUATED", "SESSION_TRUNCATED"}:
                continue
            horizon_end = observed + timedelta(minutes=horizon)
            if horizon_end > session_close:
                outcomes[key] = {
                    "status": "SESSION_TRUNCATED",
                    "horizon_minutes": horizon,
                    "reason": "The requested intraday horizon extends beyond the 16:00 ET regular-session close.",
                }
                continue
            if current < horizon_end:
                continue

            window = [
                (stamp, row)
                for stamp, row in symbol_bars
                if observed < stamp <= horizon_end + timedelta(seconds=bar_tolerance_seconds)
            ]
            if not window:
                continue
            last_stamp, last_row = window[-1]
            if last_stamp < horizon_end - timedelta(seconds=bar_tolerance_seconds):
                continue
            rows = [row for _, row in window]
            final_close = _bar_number(last_row, "c", "close")
            highs = [value for value in (_bar_number(row, "h", "high") for row in rows) if value is not None]
            lows = [value for value in (_bar_number(row, "l", "low") for row in rows) if value is not None]
            if final_close is None or not highs or not lows:
                continue
            target_before_stop, barrier_outcome, touch_bar = _barrier_outcome(
                rows,
                entry_price=entry_price,
                profit_target_pct=float(profit_target_pct),
                stop_loss_pct=float(stop_loss_pct),
            )
            outcomes[key] = {
                "status": "EVALUATED",
                "horizon_minutes": horizon,
                "window_end": _iso(horizon_end),
                "last_bar_at": _iso(last_stamp),
                "forward_return_pct": ((final_close / entry_price) - 1.0) * 100.0,
                "max_favorable_excursion_pct": ((max(highs) / entry_price) - 1.0) * 100.0,
                "max_adverse_excursion_pct": ((min(lows) / entry_price) - 1.0) * 100.0,
                "target_before_stop": target_before_stop,
                "barrier_outcome": barrier_outcome,
                "barrier_touch_bar": touch_bar,
                "profit_target_pct": float(profit_target_pct),
                "stop_loss_pct": float(stop_loss_pct),
                "same_bar_policy": "stop_first_conservative",
            }

        record["outcomes"] = outcomes
        terminal = sum(
            1
            for horizon in horizons
            if isinstance(outcomes.get(str(horizon)), dict)
            and outcomes[str(horizon)].get("status") in {"EVALUATED", "SESSION_TRUNCATED"}
        )
        if terminal == len(horizons):
            record["outcome_status"] = "COMPLETE"
            summary["completed"] += 1
        elif terminal > 0:
            record["outcome_status"] = "PARTIAL"
            summary["partial"] += 1
        else:
            record["outcome_status"] = "PENDING"
            summary["pending"] += 1
        if outcomes != before:
            record["outcomes_updated_at"] = _iso(current)
            summary["updated"] += 1
        output.append(record)

    return output, summary
