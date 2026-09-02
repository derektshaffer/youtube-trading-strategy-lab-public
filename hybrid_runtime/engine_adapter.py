"""Lazy adapters from the hybrid job service to existing Trading Lab engines."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
from typing import Any, Callable, Mapping


ProgressCallback = Callable[[float, str, str], None]
CancellationCheck = Callable[[], bool]
JobHandler = Callable[[Mapping[str, Any], ProgressCallback, CancellationCheck], Mapping[str, Any]]


class JobCancelled(RuntimeError):
    pass


def _check_cancelled(cancelled: CancellationCheck) -> None:
    if cancelled():
        raise JobCancelled("Job cancellation was requested")


def system_health_handler(
    payload: Mapping[str, Any],
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> Mapping[str, Any]:
    _check_cancelled(cancelled)
    progress(0.5, "preparing_features", "Inspecting the local runtime")
    _check_cancelled(cancelled)
    client_metrics = payload.get("client_metrics")
    return {
        "status": "ok",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "requested_checks": list(payload.get("checks") or []),
        "client_metrics": (
            dict(client_metrics)
            if isinstance(client_metrics, Mapping)
            else {}
        ),
    }


def _positive_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def chart_framework_fixture_handler(
    payload: Mapping[str, Any],
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> Mapping[str, Any]:
    """Create deterministic OHLCV data for desktop-framework interaction tests.

    This is intentionally synthetic and clearly labeled. It lets both desktop
    candidates render exactly the same payload without spending provider quota
    or confusing a framework benchmark with real trading evidence.
    """

    symbol = str(payload.get("symbol") or "SDOT").strip().upper()[:12] or "SDOT"
    timeframe = str(payload.get("timeframe") or "5Min").strip()
    step_minutes = {
        "1Min": 1,
        "5Min": 5,
        "15Min": 15,
        "1Hour": 60,
    }.get(timeframe, 5)
    timeframe = {
        1: "1Min",
        5: "5Min",
        15: "15Min",
        60: "1Hour",
    }[step_minutes]
    bars = _positive_int(payload.get("bars"), default=220, minimum=80, maximum=500)

    progress(0.12, "downloading_data", "Preparing shared chart-fixture data")
    _check_cancelled(cancelled)

    seed_bytes = hashlib.sha256(f"{symbol}|{timeframe}|v1".encode("utf-8")).digest()
    state = int.from_bytes(seed_bytes[:8], "big") or 1
    mask = (1 << 64) - 1

    def random_unit() -> float:
        nonlocal state
        state = (
            6364136223846793005 * state + 1442695040888963407
        ) & mask
        return ((state >> 11) & ((1 << 53) - 1)) / float(1 << 53)

    start = datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc)
    price = 15.8 + random_unit() * 0.8
    ema_9: float | None = None
    alpha = 2.0 / 10.0
    cumulative_volume = 0.0
    cumulative_price_volume = 0.0
    candles: list[dict[str, Any]] = []

    for index in range(bars):
        if index % 24 == 0:
            _check_cancelled(cancelled)
        cycle = math.sin(index / 10.5) * 0.055
        slower_cycle = math.sin(index / 41.0) * 0.035
        trend = 0.0045 if index < bars * 0.62 else -0.0015
        impulse = (
            0.16
            if index in {round(bars * 0.28), round(bars * 0.64)}
            else 0.0
        )
        noise = (random_unit() - 0.5) * 0.15
        opening = price
        closing = max(0.5, opening + trend + cycle + slower_cycle + impulse + noise)
        high = max(opening, closing) + 0.025 + random_unit() * 0.11
        low = max(0.1, min(opening, closing) - (0.025 + random_unit() * 0.10))
        volume = int(
            45_000
            + random_unit() * 170_000
            + abs(closing - opening) * 850_000
            + (1.0 + math.sin(index / 7.0)) * 24_000
        )
        typical = (high + low + closing) / 3.0
        cumulative_volume += volume
        cumulative_price_volume += typical * volume
        vwap = cumulative_price_volume / cumulative_volume
        ema_9 = closing if ema_9 is None else alpha * closing + (1.0 - alpha) * ema_9
        candles.append(
            {
                "time": int((start + timedelta(minutes=index * step_minutes)).timestamp()),
                "open": round(opening, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(closing, 4),
                "volume": volume,
                "vwap": round(vwap, 4),
                "ema_9": round(ema_9, 4),
            }
        )
        price = closing

    progress(0.74, "preparing_features", "Calculating shared VWAP and EMA 9")
    _check_cancelled(cancelled)
    progress(0.92, "saving", "Serializing chart payload")
    return {
        "status": "ok",
        "symbol": symbol,
        "timeframe": timeframe,
        "bars": len(candles),
        "candles": candles,
        "synthetic": True,
        "source": "desktop_framework_fixture_v1",
        "warning": "Synthetic framework-comparison data. Never use for trading.",
    }


def _load_library(payload: Mapping[str, Any]) -> dict[str, Any]:
    inline = payload.get("library")
    if isinstance(inline, dict):
        return dict(inline)
    raw_path = str(payload.get("library_path") or "").strip()
    if not raw_path:
        raise ValueError("strategy.profit_first_plan requires library or library_path")
    path = Path(raw_path).expanduser().resolve()
    decoded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("The Trading Intelligence library must be a JSON object")
    return decoded


def profit_first_plan_handler(
    payload: Mapping[str, Any],
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> Mapping[str, Any]:
    progress(0.15, "preparing_features", "Loading the research library")
    library = _load_library(payload)
    _check_cancelled(cancelled)
    progress(0.55, "searching", "Ranking strict Profit First candidates")
    # Lazy import keeps the hybrid core independent of Streamlit and heavy engine
    # modules until this real Trading Lab operation is requested.
    from profit_first_queue import profit_first_validation_batch

    maximum = max(1, min(3, int(payload.get("maximum_candidates") or 2)))
    result = profit_first_validation_batch(library, maximum_candidates=maximum)
    _check_cancelled(cancelled)
    progress(0.9, "saving", "Preparing the candidate plan")
    return dict(result)


def default_handlers() -> dict[str, JobHandler]:
    return {
        "system.health": system_health_handler,
        "chart.framework_fixture": chart_framework_fixture_handler,
        "strategy.profit_first_plan": profit_first_plan_handler,
    }
