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



def analyze_stock_strategies(
    market: AlpacaMarketData,
    symbol: str,
    strategies: list[dict[str, Any]],
    *,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Compare one stock against many strategies while sharing the same market-data requests."""
    clean = parse_symbols([symbol])
    if len(clean) != 1:
        raise AppError("Enter exactly one valid ticker.")
    ticker = clean[0]
    usable = [
        item for item in strategies
        if isinstance(item, dict)
        and item.get("id")
        and str(item.get("direction", "long")).lower() in {"long", "both"}
    ]
    if not usable:
        raise AppError("No compatible long strategies are available for this stock analysis.")

    if progress:
        progress("Loading current stock snapshot…")
    snapshot = market.snapshots([ticker]).get(ticker)
    if not snapshot:
        raise AppError(f"No current Alpaca snapshot was available for {ticker}.")

    historical_end = utc_now() - timedelta(
        minutes=16 if market.historical_feed == "sip" and market.live_feed != "sip" else 1
    )
    if progress:
        progress("Calculating relative-volume baseline…")
    daily = market.bars(
        [ticker],
        start=historical_end - timedelta(days=45),
        end=historical_end,
        timeframe="1Day",
        max_pages=5,
    )
    average_volume = average_completed_daily_volume(daily.get(ticker, []))
    metrics = snapshot_metrics(ticker, snapshot, average_daily_volume=average_volume)
    if metrics is None:
        raise AppError(f"Alpaca returned an incomplete snapshot for {ticker}.")

    any_catalyst = any(
        normalize_machine_rules(item.get("machine_rules")).get("catalyst_required")
        for item in usable
    )
    news_items: list[dict[str, Any]] = []
    if any_catalyst:
        if progress:
            progress("Checking recent catalysts…")
        news_items = market.news([ticker], hours=24).get(ticker, [])

    any_chart = any(_needs_chart_data(item) for item in usable)
    intraday_rows: list[dict[str, Any]] = []
    if any_chart:
        if progress:
            progress("Loading intraday chart context…")
        try:
            intraday_rows = market.bars(
                [ticker],
                start=_session_start(),
                end=utc_now(),
                timeframe="1Min",
                feed=market.live_feed,
                max_pages=8,
            ).get(ticker, [])
        except AppError:
            intraday_rows = []

    comparisons: list[dict[str, Any]] = []
    for strategy in usable:
        rules = normalize_machine_rules(strategy.get("machine_rules"))
        enriched = dict(metrics)
        if rules.get("catalyst_required"):
            enriched["has_catalyst"] = bool(news_items)
        if intraday_rows and _needs_chart_data(strategy):
            enriched["chart_checks"] = chart_trigger_checks(intraday_rows, strategy)
        signal = match_strategy(enriched, strategy)
        validation = strategy.get("last_validation") or {}
        comparisons.append(
            {
                "strategy_id": strategy.get("id"),
                "strategy_name": strategy.get("name") or "Unnamed strategy",
                "source_type": strategy.get("source_type") or "legacy",
                "source_title": strategy.get("source_title") or "",
                "validation_status": strategy.get("validation_status") or "unvalidated",
                "robustness_score": validation.get("robustness_score"),
                "status": signal.get("status") or "UNKNOWN",
                "score": signal.get("score") or 0,
                "unknown": signal.get("unknown") or 0,
                "signal": signal,
            }
        )

    status_rank = {"MATCH": 4, "WATCH": 3, "VERIFY": 2, "NO MATCH": 1, "UNKNOWN": 0}
    comparisons.sort(
        key=lambda item: (
            str(item.get("validation_status") or "").lower() == "validated",
            status_rank.get(str(item.get("status") or "").upper(), 0),
            float(item.get("robustness_score") or 0),
            float(item.get("score") or 0),
        ),
        reverse=True,
    )
    return {
        "symbol": ticker,
        "metrics": metrics,
        "news_count": len(news_items),
        "comparisons": comparisons,
    }
