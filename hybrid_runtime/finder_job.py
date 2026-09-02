"""Native desktop Stock Strategy Finder execution adapters.

Quick Finder runs locally against the same strategy families, raw split-safe
history, point-in-time catalyst enrichment, optimizer, walk-forward, robustness,
paper-fidelity, and real historical-spread audit used by the Streamlit Finder.
Heavier profiles are published to the existing distributed cloud Finder instead.
"""

from __future__ import annotations

from datetime import timedelta
import os
from pathlib import Path
from typing import Any, Mapping

from .desktop_settings import load_desktop_settings
from .library_source import load_library_for_job, mutate_library_for_job
from .market_cache import load_alpaca_credentials


STARTING_CASH = 2_000.0
RISK_PER_TRADE_PCT = 10.0
MAX_POSITION_PCT = 100.0
LOCAL_QUICK_PROFILE = "Quick"


class FinderJobError(RuntimeError):
    pass


def _desktop_data_dir() -> Path:
    raw = str(os.environ.get("TRADING_INTELLIGENCE_DESKTOP_DATA_DIR") or "").strip()
    if not raw:
        raise FinderJobError("The desktop data directory is unavailable.")
    path = Path(raw).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _compact_cache_events(market: Any) -> dict[str, Any]:
    events = [
        dict(item)
        for item in getattr(market, "research_cache_events", []) or []
        if isinstance(item, Mapping)
    ]
    return {
        "request_count": len(events),
        "reused_request_count": sum(1 for item in events if bool(item.get("cache_hit"))),
        "network_request_count": sum(1 for item in events if bool(item.get("network_request"))),
        "reuse_modes": [str(item.get("reuse_mode") or "") for item in events],
        "rows": [
            {
                "row_count": int(item.get("row_count") or 0),
                "reused_row_count": int(item.get("reused_row_count") or 0),
                "provider_row_count": int(item.get("provider_row_count") or 0),
                "reuse_mode": str(item.get("reuse_mode") or ""),
                "finalized": bool(item.get("finalized")),
                "fingerprint": str(item.get("fingerprint") or ""),
            }
            for item in events[-4:]
        ],
    }


def _maximum_stress_multiplier(report: Mapping[str, Any]) -> float:
    optimization = report.get("optimization") if isinstance(report.get("optimization"), Mapping) else {}
    settings = (
        optimization.get("optimization_settings")
        if isinstance(optimization.get("optimization_settings"), Mapping)
        else {}
    )
    values: list[float] = []
    for raw in settings.get("execution_sensitivity_multipliers") or (1.25, 1.5, 1.75, 2.0):
        try:
            values.append(float(raw))
        except (TypeError, ValueError, OverflowError):
            continue
    return max(values or [2.0])


def run_quick_finder(
    payload: Mapping[str, Any],
    progress,
    cancelled,
) -> dict[str, Any]:
    """Run one strict local Quick Finder search and persist its revealed holdout."""

    from finder_report_persistence import latest_completed_finder_report
    from research_cached_market import CachedResearchMarket
    from stock_strategy_finder import (
        apply_historical_spread_integrity_guard,
        merge_finder_report_into_library,
        run_stock_strategy_finder,
        search_profile,
        selected_strategies_for_profile,
        stock_finder_strategy_families,
    )
    from trading_catalyst_core import (
        enrich_bars_with_point_in_time_catalysts,
        historical_news,
    )
    from youtube_strategy_engine import (
        AlpacaMarketData,
        AppError,
        BacktestSettings,
        historical_entry_spread_audit,
        normalize_machine_rules,
        safe_float,
        split_safe_raw_research_rows,
        utc_now,
    )

    symbol = str(payload.get("symbol") or "").strip().upper()
    if not symbol:
        raise FinderJobError("Enter a stock ticker before running Finder.")
    profile_name = str(payload.get("profile") or LOCAL_QUICK_PROFILE).strip()
    profile = search_profile(profile_name)
    if profile.name != LOCAL_QUICK_PROFILE:
        raise FinderJobError(
            "Only Quick Finder is allowed on the local engine. Current Regime, Deep, and Very Deep use durable cloud compute."
        )

    data_dir = _desktop_data_dir()
    progress(0.03, "preparing_features", "Loading the authoritative strategy library")
    loaded = load_library_for_job(payload, data_dir=data_dir)
    if cancelled():
        raise FinderJobError("Finder was cancelled.")
    families = stock_finder_strategy_families(list(loaded.library.get("strategies") or []))
    selected, technical_skips = selected_strategies_for_profile(families, symbol, profile)
    if not selected:
        raise FinderJobError(
            f"No machine-testable long strategy families are available for {symbol}."
        )

    progress(0.08, "preparing_features", "Loading secure Alpaca credentials")
    api_key, secret_key = load_alpaca_credentials()
    settings = load_desktop_settings(data_dir)
    historical_feed = str(settings.market_feed or "sip").strip().lower()
    live_feed = str(os.environ.get("ALPACA_LIVE_FEED") or historical_feed).strip().lower()
    base_market = AlpacaMarketData(
        api_key,
        secret_key,
        live_feed=live_feed,
        historical_feed=historical_feed,
    )
    market = CachedResearchMarket(base_market, data_dir=str(data_dir))

    research_end = utc_now()
    if market.historical_feed == "sip" and market.live_feed != "sip":
        research_end -= timedelta(minutes=16)
    research_start = research_end - timedelta(days=profile.history_days)

    history_pages = {"count": 0}

    def history_progress(page: int) -> None:
        history_pages["count"] = max(history_pages["count"], int(page))
        if cancelled():
            raise FinderJobError("Finder was cancelled.")
        progress(
            min(0.24, 0.10 + min(0.14, float(page) * 0.006)),
            "downloading_data",
            f"Preparing {symbol} 1-minute research history · page {page}",
        )

    rows_by_symbol = market.bars(
        [symbol],
        start=research_start,
        end=research_end,
        timeframe="1Min",
        adjustment="raw",
        max_pages=300,
        progress=history_progress,
    )
    rows = list(rows_by_symbol.get(symbol) or [])
    if not rows:
        raise AppError(f"No usable historical bars were returned for {symbol}.")

    progress(0.25, "preparing_features", "Checking split and corporate-action integrity")
    split_actions = market.research_reset_actions(
        [symbol],
        start=research_start,
        end=research_end,
    )
    rows, split_guard = split_safe_raw_research_rows(rows, split_actions, symbol)
    if not rows:
        raise AppError(f"No split-safe raw-price history remained for {symbol}.")
    if cancelled():
        raise FinderJobError("Finder was cancelled.")

    needs_catalyst_history = any(
        bool(normalize_machine_rules(item.get("machine_rules")).get("catalyst_required"))
        for item in selected
    )
    catalyst_summary: dict[str, Any] = {
        "articles": 0,
        "specific_catalysts": 0,
        "positive_catalysts": 0,
        "negative_catalysts": 0,
    }
    if needs_catalyst_history:
        progress(0.28, "downloading_data", "Loading point-in-time catalyst history")
        articles = historical_news(
            market,
            [symbol],
            start=research_start - timedelta(hours=24),
            end=research_end,
            max_pages=120,
        )
        rows, catalyst_summary = enrich_bars_with_point_in_time_catalysts(
            rows,
            articles,
            lookback_hours=24.0,
        )
    progress(0.32, "preparing_features", "Historical evidence is ready")

    backtest_settings = BacktestSettings(
        starting_cash=float(payload.get("starting_cash") or STARTING_CASH),
        risk_per_trade_pct=float(payload.get("risk_per_trade_pct") or RISK_PER_TRADE_PCT),
        max_position_pct=float(payload.get("max_position_pct") or MAX_POSITION_PCT),
        train_fraction=0.70,
    )
    local_workers = max(1, min(4, int(payload.get("parallel_workers") or 2)))

    def finder_progress(completed: int, total: int, message: str) -> None:
        if cancelled():
            raise FinderJobError("Finder was cancelled.")
        fraction = float(completed) / max(1.0, float(total))
        progress(
            0.34 + fraction * 0.50,
            "optimizing",
            str(message or f"Testing Finder configuration {completed}/{max(1, total)}"),
        )

    report = run_stock_strategy_finder(
        rows,
        list(loaded.library.get("strategies") or []),
        symbol,
        profile_name=profile.name,
        backtest_settings=backtest_settings,
        progress=finder_progress,
        parallel_workers=local_workers,
    )
    report["market_data_integrity"] = dict(split_guard or {})
    report["historical_catalyst_summary"] = dict(catalyst_summary or {})
    report["research_window"] = {
        "start": research_start.isoformat(),
        "end": research_end.isoformat(),
        "historical_feed": historical_feed,
    }
    report["research_history_cache"] = _compact_cache_events(market)
    report["desktop_execution"] = {
        "target": "local",
        "parallel_workers": local_workers,
        "research_only": True,
    }

    if cancelled():
        raise FinderJobError("Finder was cancelled.")
    progress(0.87, "validating", "Auditing real historical spreads at untouched holdout entries")
    optimization = report.get("optimization") if isinstance(report.get("optimization"), Mapping) else {}
    winner = optimization.get("winner") if isinstance(optimization.get("winner"), Mapping) else {}
    winning_backtest = (
        optimization.get("winning_backtest")
        if isinstance(optimization.get("winning_backtest"), Mapping)
        else {}
    )
    optimized_settings = (
        winner.get("optimized_backtest_settings")
        if isinstance(winner.get("optimized_backtest_settings"), Mapping)
        else {}
    )
    spread_audit = historical_entry_spread_audit(
        market,
        symbol,
        list(winning_backtest.get("trades") or []),
        list(optimization.get("holdout_sessions") or []),
        modeled_spread_bps=(safe_float(optimized_settings.get("spread_bps"), 12.0) or 12.0),
        maximum_stress_multiplier=_maximum_stress_multiplier(report),
    )
    report = apply_historical_spread_integrity_guard(report, spread_audit)

    if cancelled():
        raise FinderJobError("Finder was cancelled.")
    progress(0.94, "saving", "Recording the Finder run and holdout exposure")
    saved = mutate_library_for_job(
        lambda latest: merge_finder_report_into_library(latest, report),
        payload,
        data_dir=data_dir,
    )
    compact = latest_completed_finder_report(saved.library, symbol, profile.name)
    if not compact:
        raise FinderJobError("Finder completed but its durable summary could not be reloaded.")
    compact["library"] = dict(saved.metadata)
    compact["research_history_cache"] = dict(report.get("research_history_cache") or {})
    compact["historical_catalyst_summary"] = dict(catalyst_summary or {})
    compact["research_window"] = dict(report.get("research_window") or {})
    compact["research_only"] = True
    compact["affects_execution"] = False
    compact["source_engine"] = "stock_strategy_finder_v1"
    progress(0.99, "saving", "Finder result saved")
    return compact
