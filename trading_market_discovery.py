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
    resample_intraday_bars,
    snapshot_metrics,
    utc_now,
)

LIVE_SCAN_BATCH_SIZE = 20
MAX_LIVE_SCAN_SYMBOLS = 200


def _symbol_batches(
    symbols: list[str],
    batch_size: int = LIVE_SCAN_BATCH_SIZE,
) -> list[list[str]]:
    clean = parse_symbols(symbols)
    size = max(1, int(batch_size))
    return [clean[index : index + size] for index in range(0, len(clean), size)]



def merge_momentum_candidate_universe(
    gainers: list[str],
    most_active: list[str],
    *,
    limit: int,
) -> list[str]:
    """Interleave ranked gainers and active names without duplicates."""
    first = parse_symbols(gainers)
    second = parse_symbols(most_active)
    cap = max(1, min(MAX_LIVE_SCAN_SYMBOLS, int(limit)))
    result: list[str] = []
    seen: set[str] = set()
    longest = max(len(first), len(second))
    for index in range(longest):
        for source in (first, second):
            if index >= len(source):
                continue
            symbol = source[index]
            if symbol in seen:
                continue
            seen.add(symbol)
            result.append(symbol)
            if len(result) >= cap:
                return result
    return result


def _completed_daily_reference(
    rows: list[dict[str, Any]],
    *,
    today_et=None,
) -> dict[str, float | None]:
    """Derive prior-session metrics from completed daily bars only."""
    today = today_et or utc_now().astimezone(ET).date()
    by_day: dict[Any, dict[str, float]] = {}
    for row in rows:
        try:
            timestamp = datetime.fromisoformat(str(row.get("t") or "").replace("Z", "+00:00"))
            local_day = timestamp.astimezone(ET).date()
            close = float(row.get("c"))
            high = float(row.get("h"))
            volume = float(row.get("v"))
        except (TypeError, ValueError):
            continue
        if local_day >= today or close <= 0 or high <= 0 or volume <= 0:
            continue
        by_day[local_day] = {"close": close, "high": high, "volume": volume}

    completed = [by_day[day] for day in sorted(by_day)]
    if not completed:
        return {
            "previous_day_high": None,
            "previous_day_change_pct": None,
            "previous_day_volume_ratio": None,
        }

    previous = completed[-1]
    previous_change = None
    if len(completed) >= 2 and completed[-2]["close"] > 0:
        previous_change = (previous["close"] / completed[-2]["close"] - 1.0) * 100.0

    baseline_rows = completed[:-1][-20:]
    previous_volume_ratio = None
    if len(baseline_rows) >= 3:
        baseline = sum(item["volume"] for item in baseline_rows) / len(baseline_rows)
        if baseline > 0:
            previous_volume_ratio = previous["volume"] / baseline

    return {
        "previous_day_high": previous["high"],
        "previous_day_change_pct": previous_change,
        "previous_day_volume_ratio": previous_volume_ratio,
    }


def _latest_previous_day_high_breakout(
    rows: list[dict[str, Any]],
    previous_day_high: float | None,
) -> bool | None:
    """Confirm a fresh cross above yesterday's high from current intraday closes."""
    if previous_day_high is None:
        return None
    ordered = sorted(
        (row for row in rows if isinstance(row, dict) and row.get("t")),
        key=lambda row: str(row.get("t")),
    )
    if len(ordered) < 2:
        return None
    try:
        prior_close = float(ordered[-2].get("c"))
        current_close = float(ordered[-1].get("c"))
    except (TypeError, ValueError):
        return None
    return prior_close <= previous_day_high and current_close > previous_day_high


def _strategy_live_timeframe(strategy: dict[str, Any]) -> str:
    """Use the same candle interval that produced the live/validated candidate."""
    last_validation = (
        strategy.get("last_validation")
        if isinstance(strategy.get("last_validation"), dict)
        else {}
    )
    for value in (
        strategy.get("_finder_timeframe"),
        strategy.get("validated_timeframe"),
        last_validation.get("timeframe"),
        strategy.get("timeframe"),
    ):
        timeframe = str(value or "").strip()
        if timeframe in {"1Min", "5Min", "15Min"}:
            return timeframe
    return "1Min"


def _strategy_allows_extended_hours(strategy: dict[str, Any]) -> bool:
    """Mirror the backtest execution-hours setting for live chart evaluation."""
    settings = None
    if bool(strategy.get("using_validated_rules")):
        settings = strategy.get("validated_backtest_settings")
    if not isinstance(settings, dict):
        settings = strategy.get("optimized_backtest_settings")
    if not isinstance(settings, dict):
        settings = strategy.get("validated_backtest_settings")
    if isinstance(settings, dict) and "allow_extended_hours" in settings:
        return bool(settings.get("allow_extended_hours"))
    return True


def _prepare_strategy_intraday_rows(
    rows: list[dict[str, Any]],
    strategy: dict[str, Any],
) -> list[dict[str, Any]]:
    return resample_intraday_bars(
        rows,
        _strategy_live_timeframe(strategy),
        include_extended_hours=_strategy_allows_extended_hours(strategy),
    )


def _strategy_chart_checks(
    rows: list[dict[str, Any]],
    strategy: dict[str, Any],
    daily_reference: dict[str, float | None],
    *,
    prepared_rows: bool = False,
) -> dict[str, Any]:
    """Use strategy-aligned candles plus completed daily references without extra downloads."""
    effective_rows = rows if prepared_rows else _prepare_strategy_intraday_rows(rows, strategy)
    checks = chart_trigger_checks(
        effective_rows,
        strategy,
        include_extended_hours=_strategy_allows_extended_hours(strategy),
    )
    if daily_reference.get("previous_day_volume_ratio") is not None:
        checks["previous_day_volume_ratio"] = daily_reference["previous_day_volume_ratio"]
    if daily_reference.get("previous_day_change_pct") is not None:
        checks["previous_day_change_pct"] = daily_reference["previous_day_change_pct"]
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    if rules.get("previous_day_high_breakout"):
        checks["previous_day_high_breakout"] = _latest_previous_day_high_breakout(
            effective_rows,
            daily_reference.get("previous_day_high"),
        )
    return checks


def _needs_chart_data(strategy: dict[str, Any]) -> bool:
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    return any(
        rules.get(name) is not None and rules.get(name) is not False
        for name in (
            "vwap_reclaim",
            "previous_day_high_breakout",
            "min_previous_day_volume_ratio",
            "min_previous_day_change_pct",
            "breakout_lookback_bars",
            "opening_range_minutes",
            "volume_surge_ratio",
            "minimum_green_bars",
            "avwap_anchor_mode",
            "require_price_above_avwap",
            "avwap_reclaim",
            "require_avwap_rising",
            "require_avwap_pullback",
            "max_avwap_distance_pct",
            "stop_below_avwap",
            "require_price_above_fast_ema",
            "require_price_above_slow_ema",
            "require_price_above_trend_ema",
            "max_fast_ema_distance_pct",
            "require_fast_ema_rising",
            "require_fast_ema_pullback",
            "max_pullback_number",
            "require_pullback_breakout",
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


def _scan_strategy_universe_batch(
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
        daily_rows = daily.get(symbol, [])
        average_volume = average_completed_daily_volume(daily_rows)
        metrics = snapshot_metrics(symbol, snapshot, average_daily_volume=average_volume)
        if metrics is None:
            continue
        daily_reference = _completed_daily_reference(daily_rows)
        enriched = dict(metrics)
        enriched.update(
            {
                key: value
                for key, value in daily_reference.items()
                if key != "previous_day_high" and value is not None
            }
        )
        if rules.get("catalyst_required"):
            enriched["has_catalyst"] = bool(news_by_symbol.get(symbol))
        if chart_rows.get(symbol) and _needs_chart_data(strategy):
            enriched["chart_checks"] = _strategy_chart_checks(
                chart_rows[symbol],
                strategy,
                daily_reference,
            )

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



def _scan_market_strategies_batch(
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
        daily_rows = daily.get(symbol, [])
        average_volume = average_completed_daily_volume(daily_rows)
        metrics = snapshot_metrics(symbol, snapshot, average_daily_volume=average_volume)
        if metrics is None:
            continue
        daily_reference = _completed_daily_reference(daily_rows)

        market_features = build_market_features(chart_rows.get(symbol, []))
        comparisons: list[dict[str, Any]] = []
        for raw, strategy in usable:
            rules = normalize_machine_rules(strategy.get("machine_rules"))
            enriched = dict(metrics)
            enriched.update(
                {
                    key: value
                    for key, value in daily_reference.items()
                    if key != "previous_day_high" and value is not None
                }
            )
            if rules.get("catalyst_required"):
                enriched["has_catalyst"] = bool(news_by_symbol.get(symbol))
            if chart_rows.get(symbol) and _needs_chart_data(strategy):
                enriched["chart_checks"] = _strategy_chart_checks(
                    chart_rows[symbol],
                    strategy,
                    daily_reference,
                )
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


def scan_strategy_universe(
    market: AlpacaMarketData,
    symbols: list[str],
    strategy: dict[str, Any],
    *,
    progress: Callable[[str], None] | None = None,
    batch_size: int = LIVE_SCAN_BATCH_SIZE,
) -> list[dict[str, Any]]:
    """Apply one saved strategy across a broader universe in bounded API batches."""
    clean = parse_symbols(symbols)
    if not clean:
        return []
    if len(clean) > MAX_LIVE_SCAN_SYMBOLS:
        raise AppError(
            f"Live strategy scans support up to {MAX_LIVE_SCAN_SYMBOLS} stocks per run. "
            "Use a smaller candidate universe or split the scan into multiple runs."
        )

    batches = _symbol_batches(clean, batch_size=batch_size)
    combined: list[dict[str, Any]] = []
    total = len(batches)
    for index, batch in enumerate(batches, start=1):
        def batch_progress(message: str, *, _index: int = index) -> None:
            if progress:
                progress(f"Batch {_index}/{total} · {message}")

        combined.extend(
            _scan_strategy_universe_batch(
                market,
                batch,
                strategy,
                progress=batch_progress if progress else None,
            )
        )

    status_rank = {"MATCH": 4, "WATCH": 3, "VERIFY": 2, "NO MATCH": 1, "UNKNOWN": 0}
    combined.sort(
        key=lambda item: (
            status_rank.get(str(item.get("status") or "").upper(), 0),
            float(item.get("score") or 0),
            float((item.get("metrics") or {}).get("relative_volume") or 0),
            float((item.get("metrics") or {}).get("day_change_pct") or 0),
        ),
        reverse=True,
    )
    return combined


def scan_market_strategies(
    market: AlpacaMarketData,
    symbols: list[str],
    strategies: list[dict[str, Any]],
    *,
    progress: Callable[[str], None] | None = None,
    batch_size: int = LIVE_SCAN_BATCH_SIZE,
) -> list[dict[str, Any]]:
    """Rank a broader live universe while preserving one shared data pass per batch.

    The public scanner intentionally chunks API work instead of sending a huge
    symbol list through snapshots, daily bars, intraday bars, and news at once.
    Results are merged and ranked globally after every batch completes.
    """
    clean = parse_symbols(symbols)
    if not clean:
        return []
    if len(clean) > MAX_LIVE_SCAN_SYMBOLS:
        raise AppError(
            f"Live market discovery supports up to {MAX_LIVE_SCAN_SYMBOLS} stocks per run. "
            "Narrow the universe or split it into multiple scans."
        )

    batches = _symbol_batches(clean, batch_size=batch_size)
    combined: list[dict[str, Any]] = []
    total = len(batches)
    for index, batch in enumerate(batches, start=1):
        def batch_progress(message: str, *, _index: int = index) -> None:
            if progress:
                progress(f"Batch {_index}/{total} · {message}")

        combined.extend(
            _scan_market_strategies_batch(
                market,
                batch,
                strategies,
                progress=batch_progress if progress else None,
            )
        )

    status_rank = {"MATCH": 4, "WATCH": 3, "VERIFY": 2, "NO MATCH": 1, "UNKNOWN": 0}
    combined.sort(
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
    return combined


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
    daily_rows = daily.get(ticker, [])
    average_volume = average_completed_daily_volume(daily_rows)
    metrics = snapshot_metrics(ticker, snapshot, average_daily_volume=average_volume)
    if metrics is None:
        raise AppError(f"Alpaca returned an incomplete snapshot for {ticker}.")
    daily_reference = _completed_daily_reference(daily_rows)

    if progress:
        progress("Loading recent catalyst context…")
    news_items: list[dict[str, Any]] = market.news([ticker], hours=24).get(ticker, [])

    intraday_rows = _load_intraday_context(
        market,
        [ticker],
        max_pages=8,
        progress=progress,
        message="Loading intraday market features…",
    ).get(ticker, [])

    comparisons: list[dict[str, Any]] = []
    for strategy in usable:
        strategy_rows = _prepare_strategy_intraday_rows(intraday_rows, strategy)
        strategy_market_features = build_market_features(strategy_rows)
        rules = normalize_machine_rules(strategy.get("machine_rules"))
        enriched = dict(metrics)
        enriched.update(
            {
                key: value
                for key, value in daily_reference.items()
                if key != "previous_day_high" and value is not None
            }
        )
        if rules.get("catalyst_required"):
            enriched["has_catalyst"] = bool(news_items)
        if strategy_rows and _needs_chart_data(strategy):
            enriched["chart_checks"] = _strategy_chart_checks(
                strategy_rows,
                strategy,
                daily_reference,
                prepared_rows=True,
            )
        enriched["market_features"] = dict(strategy_market_features.get("features") or {})
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
                "market_features": strategy_market_features,
                "timeframe": _strategy_live_timeframe(strategy),
                "allow_extended_hours": _strategy_allows_extended_hours(strategy),
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
    primary_market_features = (
        comparisons[0].get("market_features")
        if comparisons and isinstance(comparisons[0].get("market_features"), dict)
        else build_market_features(intraday_rows)
    )
    return {
        "symbol": ticker,
        "metrics": metrics,
        "market_features": primary_market_features,
        "timeframe": comparisons[0].get("timeframe") if comparisons else "1Min",
        "allow_extended_hours": comparisons[0].get("allow_extended_hours") if comparisons else True,
        "news_count": len(news_items),
        "news_items": news_items,
        "comparisons": comparisons,
    }
