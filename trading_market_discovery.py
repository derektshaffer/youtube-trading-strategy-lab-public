"""Live market discovery helpers for Trading Intelligence Lab."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from youtube_strategy_engine import (
    ET,
    AlpacaMarketData,
    AppError,
    average_completed_daily_volume,
    chart_trigger_checks,
    match_strategy,
    normalize_machine_rules,
    parse_symbols,
    snapshot_metrics,
    utc_now,
)


def _needs_chart_data(strategy: dict[str, Any]) -> bool:
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    return any(
        rules.get(name) is not None and rules.get(name) is not False
        for name in (
            "vwap_reclaim",
            "breakout_lookback_bars",
            "opening_range_minutes",
            "volume_surge_ratio",
            "minimum_green_bars",
        )
    )


def _session_start() -> datetime:
    now_et = utc_now().astimezone(ET)
    session_day = now_et.date()
    if now_et.hour * 60 + now_et.minute < 4 * 60:
        session_day -= timedelta(days=1)
    while session_day.weekday() >= 5:
        session_day -= timedelta(days=1)
    return datetime.combine(session_day, datetime.min.time(), tzinfo=ET).replace(hour=4)


def scan_strategy_universe(
    market: AlpacaMarketData,
    symbols: list[str],
    strategy: dict[str, Any],
    *,
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Apply one saved strategy to a small live universe with batched market requests."""
    clean = parse_symbols(symbols)
    if not clean:
        return []
    if len(clean) > 30:
        clean = clean[:30]

    if progress:
        progress("Loading current snapshots…")
    snapshots = market.snapshots(clean)

    historical_end = utc_now() - timedelta(
        minutes=16 if market.historical_feed == "sip" and market.live_feed != "sip" else 1
    )
    if progress:
        progress("Building relative-volume baselines…")
    daily = market.bars(
        clean,
        start=historical_end - timedelta(days=45),
        end=historical_end,
        timeframe="1Day",
        max_pages=8,
    )

    rules = normalize_machine_rules(strategy.get("machine_rules"))
    news_by_symbol: dict[str, list[dict[str, Any]]] = {}
    if rules.get("catalyst_required"):
        if progress:
            progress("Checking current catalysts…")
        news_by_symbol = market.news(clean, hours=24)

    chart_rows: dict[str, list[dict[str, Any]]] = {}
    if _needs_chart_data(strategy):
        if progress:
            progress("Checking intraday chart triggers…")
        try:
            chart_rows = market.bars(
                clean,
                start=_session_start(),
                end=utc_now(),
                timeframe="1Min",
                feed=market.live_feed,
                max_pages=10,
            )
        except AppError:
            chart_rows = {}

    results: list[dict[str, Any]] = []
    for symbol in clean:
        snapshot = snapshots.get(symbol)
        if not snapshot:
            continue
        average_volume = average_completed_daily_volume(daily.get(symbol, []))
        metrics = snapshot_metrics(symbol, snapshot, average_daily_volume=average_volume)
        if metrics is None:
            continue
        enriched = dict(metrics)
        if rules.get("catalyst_required"):
            enriched["has_catalyst"] = bool(news_by_symbol.get(symbol))
        if chart_rows.get(symbol):
            enriched["chart_checks"] = chart_trigger_checks(chart_rows[symbol], strategy)

        signal = match_strategy(enriched, strategy)
        results.append(
            {
                "symbol": symbol,
                "status": signal.get("status") or "UNKNOWN",
                "score": signal.get("score") or 0,
                "unknown": signal.get("unknown") or 0,
                "metrics": metrics,
                "signal": signal,
                "has_catalyst": enriched.get("has_catalyst"),
            }
        )

    status_rank = {"MATCH": 4, "WATCH": 3, "VERIFY": 2, "NO MATCH": 1, "UNKNOWN": 0}
    results.sort(
        key=lambda item: (
            status_rank.get(str(item.get("status") or "").upper(), 0),
            float(item.get("score") or 0),
            float((item.get("metrics") or {}).get("relative_volume") or 0),
            float((item.get("metrics") or {}).get("day_change_pct") or 0),
        ),
        reverse=True,
    )
    return results
