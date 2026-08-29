"""Live market discovery helpers for Trading Intelligence Lab."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from market_features import build_market_features
from trading_intelligence_core import effective_strategy_for_live
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


def _load_intraday_context(
    market: AlpacaMarketData,
    symbols: list[str],
    *,
    max_pages: int,
    progress: Callable[[str], None] | None = None,
    message: str = "Loading intraday market features…",
) -> dict[str, list[dict[str, Any]]]:
    """Load one shared intraday pass for observational features and strategy checks."""
    if progress:
        progress(message)
    try:
        return market.bars(
            symbols,
            start=_session_start(),
            end=utc_now(),
            timeframe="1Min",
            feed=market.live_feed,
            max_pages=max_pages,
        )
    except AppError:
        return {}


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
    strategy = effective_strategy_for_live(strategy)
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

    chart_rows = _load_intraday_context(
        market,
        clean,
        max_pages=10,
        progress=progress,
        message="Loading intraday market features and chart context…",
    )

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
        if chart_rows.get(symbol) and _needs_chart_data(strategy):
            enriched["chart_checks"] = chart_trigger_checks(chart_rows[symbol], strategy)

        market_features = build_market_features(chart_rows.get(symbol, []))
        enriched["market_features"] = dict(market_features.get("features") or {})
        signal = match_strategy(enriched, strategy)
        results.append(
            {
                "symbol": symbol,
                "status": signal.get("status") or "UNKNOWN",
                "score": signal.get("score") or 0,
                "unknown": signal.get("unknown") or 0,
                "metrics": metrics,
                "market_features": market_features,
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



def scan_market_strategies(
    market: AlpacaMarketData,
    symbols: list[str],
    strategies: list[dict[str, Any]],
    *,
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Rank a live stock universe against every usable strategy with shared data calls.

    Market data is downloaded once per stock universe, then every compatible
    strategy is evaluated against that same snapshot/chart/catalyst context.
    Causal market features are calculated once per stock and shared with every
    strategy so live matching uses the same feature vocabulary as backtesting.
    """
    clean = parse_symbols(symbols)
    if not clean:
        return []
    if len(clean) > 30:
        clean = clean[:30]

    usable: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw in strategies:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        effective = effective_strategy_for_live(raw)
        if str(effective.get("direction", "long")).lower() not in {"long", "both"}:
            continue
        rules = normalize_machine_rules(effective.get("machine_rules"))
        if not any(value is not None for value in rules.values()):
            continue
        usable.append((raw, effective))
    if not usable:
        raise AppError("No measurable long strategy families are available for market discovery.")

    if progress:
        progress(f"Loading current snapshots for {len(clean)} stocks…")
    snapshots = market.snapshots(clean)

    historical_end = utc_now() - timedelta(
        minutes=16 if market.historical_feed == "sip" and market.live_feed != "sip" else 1
    )
    if progress:
        progress("Building shared relative-volume baselines…")
    daily = market.bars(
        clean,
        start=historical_end - timedelta(days=45),
        end=historical_end,
        timeframe="1Day",
        max_pages=8,
    )

    any_catalyst = any(
        normalize_machine_rules(strategy.get("machine_rules")).get("catalyst_required")
        for _, strategy in usable
    )
    news_by_symbol: dict[str, list[dict[str, Any]]] = {}
    if any_catalyst:
        if progress:
            progress("Checking catalysts needed by any strategy…")
        news_by_symbol = market.news(clean, hours=24)

    chart_rows = _load_intraday_context(
        market,
        clean,
        max_pages=10,
        progress=progress,
        message="Loading shared intraday market features…",
    )

    status_rank = {"MATCH": 4, "WATCH": 3, "VERIFY": 2, "NO MATCH": 1, "UNKNOWN": 0}
    results: list[dict[str, Any]] = []
    for symbol in clean:
        snapshot = snapshots.get(symbol)
        if not snapshot:
            continue
        average_volume = average_completed_daily_volume(daily.get(symbol, []))
        metrics = snapshot_metrics(symbol, snapshot, average_daily_volume=average_volume)
        if metrics is None:
            continue

        market_features = build_market_features(chart_rows.get(symbol, []))
        comparisons: list[dict[str, Any]] = []
        for raw, strategy in usable:
            rules = normalize_machine_rules(strategy.get("machine_rules"))
            enriched = dict(metrics)
            if rules.get("catalyst_required"):
                enriched["has_catalyst"] = bool(news_by_symbol.get(symbol))
            if chart_rows.get(symbol) and _needs_chart_data(strategy):
                enriched["chart_checks"] = chart_trigger_checks(chart_rows[symbol], strategy)
            enriched["market_features"] = dict(market_features.get("features") or {})

            signal = match_strategy(enriched, strategy)
            validation_status = str(
                raw.get("validation_status")
                or strategy.get("validation_status")
                or "unvalidated"
            )
            validation = (
                raw.get("last_validation") or strategy.get("last_validation") or {}
                if validation_status.lower() == "validated"
                else {}
            )
            robustness = validation.get("robustness_score")
            comparisons.append(
                {
                    "strategy_id": raw.get("id") or strategy.get("id"),
                    "strategy_name": raw.get("name") or strategy.get("name") or "Unnamed strategy",
                    "validation_status": validation_status,
                    "robustness_score": robustness,
                    "source_type": raw.get("source_type") or strategy.get("source_type") or "",
                    "status": signal.get("status") or "UNKNOWN",
                    "score": signal.get("score") or 0,
                    "unknown": signal.get("unknown") or 0,
                    "signal": signal,
                    "has_catalyst": enriched.get("has_catalyst"),
                }
            )

        comparisons.sort(
            key=lambda item: (
                status_rank.get(str(item.get("status") or "").upper(), 0),
                str(item.get("validation_status") or "").lower() == "validated",
                float(item.get("robustness_score") or 0),
                float(item.get("score") or 0),
            ),
            reverse=True,
        )
        if not comparisons:
            continue

        best = comparisons[0]
        actionable = [
            item
            for item in comparisons
            if str(item.get("status") or "").upper() in {"MATCH", "WATCH", "VERIFY"}
        ]
        results.append(
            {
                "symbol": symbol,
                "metrics": metrics,
                "market_features": market_features,
                "best_strategy_id": best.get("strategy_id"),
                "best_strategy_name": best.get("strategy_name"),
                "validation_status": best.get("validation_status"),
                "robustness_score": best.get("robustness_score"),
                "status": best.get("status"),
                "score": best.get("score"),
                "unknown": best.get("unknown"),
                "signal": best.get("signal"),
                "has_catalyst": best.get("has_catalyst"),
                "matching_strategy_count": len(actionable),
                "strategy_matches": comparisons[:8],
            }
        )

    results.sort(
        key=lambda item: (
            status_rank.get(str(item.get("status") or "").upper(), 0),
            str(item.get("validation_status") or "").lower() == "validated",
            float(item.get("robustness_score") or 0),
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
        effective_strategy_for_live(item) for item in strategies
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

    intraday_rows = _load_intraday_context(
        market,
        [ticker],
        max_pages=8,
        progress=progress,
        message="Loading intraday market features…",
    ).get(ticker, [])
    market_features = build_market_features(intraday_rows)

    comparisons: list[dict[str, Any]] = []
    for strategy in usable:
        rules = normalize_machine_rules(strategy.get("machine_rules"))
        enriched = dict(metrics)
        if rules.get("catalyst_required"):
            enriched["has_catalyst"] = bool(news_items)
        if intraday_rows and _needs_chart_data(strategy):
            enriched["chart_checks"] = chart_trigger_checks(intraday_rows, strategy)
        enriched["market_features"] = dict(market_features.get("features") or {})
        signal = match_strategy(enriched, strategy)
        validation_status = str(strategy.get("validation_status") or "unvalidated")
        validation = (
            strategy.get("last_validation") or {}
            if validation_status.lower() == "validated"
            else {}
        )
        comparisons.append(
            {
                "strategy_id": strategy.get("id"),
                "strategy_name": strategy.get("name") or "Unnamed strategy",
                "source_type": strategy.get("source_type") or "legacy",
                "source_title": strategy.get("source_title") or "",
                "validation_status": validation_status,
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
        "market_features": market_features,
        "news_count": len(news_items),
        "comparisons": comparisons,
    }
