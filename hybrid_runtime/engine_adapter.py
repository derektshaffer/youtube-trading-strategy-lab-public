"""Lazy adapters from the hybrid job service to existing Trading Lab engines."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import math
import os
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


def onboarding_probe_handler(
    payload: Mapping[str, Any],
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> Mapping[str, Any]:
    """Verify saved first-run connections while keeping all credentials in Keychain."""

    data_dir = _desktop_data_dir()
    if not data_dir:
        raise RuntimeError("The desktop data directory is unavailable")
    from .onboarding import verify_setup

    return verify_setup(
        data_dir,
        progress=progress,
        cancelled=cancelled,
    )


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
    """Create deterministic OHLCV data for desktop-framework interaction tests."""

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
        state = (6364136223846793005 * state + 1442695040888963407) & mask
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
        impulse = 0.16 if index in {round(bars * 0.28), round(bars * 0.64)} else 0.0
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


def _desktop_data_dir() -> str | None:
    value = str(os.environ.get("TRADING_INTELLIGENCE_DESKTOP_DATA_DIR") or "").strip()
    return value or None


def library_configuration_handler(
    payload: Mapping[str, Any],
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> Mapping[str, Any]:
    _check_cancelled(cancelled)
    progress(0.6, "preparing_features", "Reading non-secret desktop settings")
    from .desktop_settings import load_desktop_settings

    settings = load_desktop_settings(_desktop_data_dir() or ".")
    _check_cancelled(cancelled)
    return settings.as_dict()


def library_summary_handler(
    payload: Mapping[str, Any],
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> Mapping[str, Any]:
    progress(0.15, "downloading_data", "Resolving the authoritative research library")
    from .library_source import library_connection_summary

    summary = library_connection_summary(payload, data_dir=_desktop_data_dir())
    _check_cancelled(cancelled)
    progress(0.9, "saving", "Preparing the library summary")
    return summary


def results_summary_handler(
    payload: Mapping[str, Any],
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> Mapping[str, Any]:
    """Read bounded result summaries while keeping full evidence in durable storage."""

    progress(0.12, "downloading_data", "Loading the authoritative research library")
    from .library_source import load_library_for_job

    loaded = load_library_for_job(payload, data_dir=_desktop_data_dir())
    _check_cancelled(cancelled)
    progress(0.48, "preparing_features", "Loading the small Strategy Lab checkpoint")
    combined_library = dict(loaded.library)
    try:
        from .library_source import load_strategy_lab_checkpoint_library

        checkpoint = load_strategy_lab_checkpoint_library(data_dir=_desktop_data_dir())
        checkpoint_runs = [
            dict(item)
            for item in checkpoint.library.get("validation_runs") or []
            if isinstance(item, Mapping)
        ]
        if checkpoint_runs:
            combined_library["validation_runs"] = [
                *checkpoint_runs,
                *[
                    dict(item)
                    for item in combined_library.get("validation_runs") or []
                    if isinstance(item, Mapping)
                    and str(item.get("record_type") or "") != "strategy_lab_checkpoint"
                ],
            ]
    except Exception:
        # Results remains usable from the authoritative main library even when
        # the optional small Strategy Lab checkpoint cannot refresh.
        pass
    progress(0.62, "preparing_features", "Compacting recent durable research evidence")
    from .results_summary import build_results_summary

    limit = _positive_int(payload.get("limit"), default=30, minimum=5, maximum=100)
    result = dict(build_results_summary(combined_library, limit=limit))
    _check_cancelled(cancelled)
    result["library"] = dict(loaded.metadata)
    result["research_only"] = True
    result["affects_live_ranking"] = False
    result["affects_execution"] = False
    progress(0.92, "saving", "Preparing bounded Results payload")
    return result


def research_ml_summary_handler(
    payload: Mapping[str, Any],
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> Mapping[str, Any]:
    """Read bounded cloud-research/model status without loading artifacts into the UI."""

    progress(0.14, "downloading_data", "Loading the authoritative research library")
    from .library_source import load_library_for_job

    loaded = load_library_for_job(payload, data_dir=_desktop_data_dir())
    _check_cancelled(cancelled)
    progress(0.62, "preparing_features", "Compacting research queue and predictive ML status")
    from .research_ml_summary import build_research_ml_summary

    limit = _positive_int(payload.get("limit"), default=30, minimum=5, maximum=100)
    result = dict(
        build_research_ml_summary(
            loaded.library,
            metadata=loaded.metadata,
            limit=limit,
        )
    )
    _check_cancelled(cancelled)
    progress(0.92, "saving", "Preparing bounded Research + ML payload")
    return result


def strategy_lab_options_handler(
    payload: Mapping[str, Any],
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> Mapping[str, Any]:
    progress(0.15, "downloading_data", "Loading the authoritative strategy library")
    from .library_source import load_library_for_job

    loaded = load_library_for_job(payload, data_dir=_desktop_data_dir())
    _check_cancelled(cancelled)
    progress(0.58, "preparing_features", "Applying the Strategy Lab fidelity gate")
    from .strategy_lab_options import build_strategy_lab_options

    limit = _positive_int(payload.get("limit"), default=300, minimum=1, maximum=500)
    result = dict(build_strategy_lab_options(loaded.library, limit=limit))
    result["library"] = dict(loaded.metadata)
    _check_cancelled(cancelled)
    progress(0.92, "saving", "Preparing faithful Strategy Lab choices")
    return result


def profit_first_plan_handler(
    payload: Mapping[str, Any],
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> Mapping[str, Any]:
    progress(0.12, "downloading_data", "Loading the authoritative research library")
    from .library_source import load_library_for_job

    loaded = load_library_for_job(payload, data_dir=_desktop_data_dir())
    _check_cancelled(cancelled)
    progress(0.55, "searching", "Ranking strict Profit First candidates")
    from profit_first_queue import profit_first_validation_batch

    maximum = max(1, min(3, int(payload.get("maximum_candidates") or 2)))
    result = dict(
        profit_first_validation_batch(
            loaded.library,
            maximum_candidates=maximum,
        )
    )
    _check_cancelled(cancelled)
    progress(0.9, "saving", "Preparing the candidate plan")
    result["library"] = loaded.metadata
    result["research_only"] = True
    result["affects_live_ranking"] = False
    result["affects_execution"] = False
    return result


def stock_analysis_handler(
    payload: Mapping[str, Any],
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> Mapping[str, Any]:
    """Run quick real-market analysis using the persistent desktop candle cache."""

    from .market_job import run_bounded_stock_analysis

    data_dir = _desktop_data_dir()
    if not data_dir:
        raise RuntimeError("The desktop data directory is unavailable")
    return run_bounded_stock_analysis(
        payload,
        data_dir=data_dir,
        progress=progress,
        cancelled=cancelled,
    )


def market_discovery_handler(
    payload: Mapping[str, Any],
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> Mapping[str, Any]:
    """Run the web-parity strategy-to-stock discovery engine in the sidecar."""

    from .market_discovery_job import (
        MarketDiscoveryCancelled,
        desktop_market_discovery_handler,
    )

    try:
        return desktop_market_discovery_handler(payload, progress, cancelled)
    except MarketDiscoveryCancelled as exc:
        raise JobCancelled(str(exc)) from exc


def default_handlers() -> dict[str, JobHandler]:
    return {
        "system.health": system_health_handler,
        "system.onboarding_probe": onboarding_probe_handler,
        "chart.framework_fixture": chart_framework_fixture_handler,
        "library.configuration": library_configuration_handler,
        "library.summary": library_summary_handler,
        "library.results_summary": results_summary_handler,
        "library.research_ml_summary": research_ml_summary_handler,
        "library.strategy_lab_options": strategy_lab_options_handler,
        "strategy.profit_first_plan": profit_first_plan_handler,
        "analysis.stock": stock_analysis_handler,
        "market.discovery": market_discovery_handler,
    }
