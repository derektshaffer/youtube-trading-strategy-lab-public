"""Autonomous historical opportunity discovery and research orchestration.

This module deliberately separates candidate discovery from outcome testing:
daily history identifies stocks that exhibited the kind of conditions a strategy
expects, then intraday backtests/validation decide whether the strategy had edge.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime, time, timedelta, timezone
import math
from statistics import median
from typing import Any, Callable

from trading_catalyst_core import enrich_bars_with_point_in_time_catalysts, historical_news
from trading_intelligence_core import effective_strategy_for_research, research_readiness
from trading_universe_research import cross_stock_generalization
from trading_validation_core import validation_strength, walk_forward_validate
from youtube_strategy_engine import (
    AlpacaMarketData,
    AppError,
    BacktestSettings,
    OptimizationSettings,
    normalize_machine_rules,
    optimize_stock_strategies,
    safe_float,
    utc_now,
)


AUTO_UNIVERSE_SAMPLE_SIZE = 500
AUTO_INACTIVE_SAMPLE_SHARE = 0.30
AUTO_MAX_DEEP_STRATEGIES = 3
AUTO_SYMBOLS_PER_STRATEGY = 6
AUTO_DAILY_LOOKBACK_DAYS = 1825
AUTO_EVENT_WINDOW_DAYS = 120
AUTO_EVENT_WINDOW_BUFFER_DAYS = 30
AUTO_TIMEFRAME = "5Min"


def _notify(callback: Callable[[str], None] | None, message: str) -> None:
    if callback:
        callback(message)


def deterministic_symbol_sample(
    symbols: list[str],
    *,
    maximum: int,
    priority: list[str] | None = None,
) -> list[str]:
    """Select a reproducible broad sample while always retaining priority symbols."""
    maximum = max(1, int(maximum))
    clean = sorted({str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()})
    priority_clean: list[str] = []
    for symbol in priority or []:
        value = str(symbol or "").strip().upper()
        if value and value not in priority_clean:
            priority_clean.append(value)

    result = priority_clean[:maximum]
    remaining = maximum - len(result)
    pool = [symbol for symbol in clean if symbol not in result]
    if remaining <= 0 or not pool:
        return result
    if len(pool) <= remaining:
        return result + pool

    if remaining == 1:
        sampled = [pool[len(pool) // 2]]
    else:
        sampled = []
        for index in range(remaining):
            position = round(index * (len(pool) - 1) / (remaining - 1))
            symbol = pool[position]
            if symbol not in sampled:
                sampled.append(symbol)
    for symbol in sampled:
        if symbol not in result:
            result.append(symbol)
    return result[:maximum]


def _batched_bars(
    market: AlpacaMarketData,
    symbols: list[str],
    *,
    start,
    end,
    timeframe: str,
    batch_size: int = 125,
    max_pages: int = 16,
    progress: Callable[[str], None] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    clean = [str(symbol).upper() for symbol in symbols if str(symbol).strip()]
    batches = [clean[index : index + batch_size] for index in range(0, len(clean), batch_size)]
    for batch_index, batch in enumerate(batches, start=1):
        _notify(progress, f"Historical {timeframe} batch {batch_index} of {len(batches)}…")
        chunk = market.bars(
            batch,
            start=start,
            end=end,
            timeframe=timeframe,
            max_pages=max_pages,
        )
        for symbol, rows in chunk.items():
            output[symbol] = list(rows or [])
    return output


def deterministic_catalog_sample(
    catalog: list[dict[str, Any]],
    *,
    maximum: int,
    priority: list[str] | None = None,
    inactive_share: float = AUTO_INACTIVE_SAMPLE_SHARE,
) -> tuple[list[str], dict[str, Any]]:
    """Sample active and inactive exchange-listed equities reproducibly.

    Priority symbols are retained first. The remaining budget explicitly reserves space for
    inactive/delisted names so today's survivors cannot dominate the historical research set.
    """
    maximum = max(1, int(maximum))
    priority_clean = [
        value
        for value in dict.fromkeys(
            str(symbol or "").strip().upper() for symbol in (priority or [])
        )
        if value
    ]
    by_symbol = {
        str(item.get("symbol") or "").strip().upper(): item
        for item in catalog
        if isinstance(item, dict) and str(item.get("symbol") or "").strip()
    }
    active = sorted(
        symbol for symbol, item in by_symbol.items()
        if str(item.get("status") or "").lower() == "active"
    )
    inactive = sorted(
        symbol for symbol, item in by_symbol.items()
        if str(item.get("status") or "").lower() == "inactive"
    )

    result = [symbol for symbol in priority_clean if symbol in by_symbol][:maximum]
    remaining = maximum - len(result)
    inactive_target = min(
        len([symbol for symbol in inactive if symbol not in result]),
        max(0, int(round(maximum * max(0.0, min(0.8, float(inactive_share)))))),
    )
    inactive_pick = deterministic_symbol_sample(
        [symbol for symbol in inactive if symbol not in result],
        maximum=max(1, inactive_target) if inactive_target else 1,
    ) if inactive_target else []
    inactive_pick = inactive_pick[:inactive_target]
    result.extend(symbol for symbol in inactive_pick if symbol not in result)

    remaining = maximum - len(result)
    active_pick = deterministic_symbol_sample(
        [symbol for symbol in active if symbol not in result],
        maximum=max(1, remaining) if remaining else 1,
    ) if remaining else []
    result.extend(symbol for symbol in active_pick if symbol not in result)

    if len(result) < maximum:
        residual = [
            symbol for symbol in sorted(by_symbol)
            if symbol not in result
        ]
        result.extend(residual[: maximum - len(result)])

    sampled_status = {
        "active": sum(
            1 for symbol in result
            if str((by_symbol.get(symbol) or {}).get("status") or "").lower() == "active"
        ),
        "inactive": sum(
            1 for symbol in result
            if str((by_symbol.get(symbol) or {}).get("status") or "").lower() == "inactive"
        ),
    }
    return result[:maximum], {
        "population_size": len(by_symbol),
        "active_population": len(active),
        "inactive_population": len(inactive),
        "active_sampled": sampled_status["active"],
        "inactive_sampled": sampled_status["inactive"],
    }


def build_research_universe(
    market: AlpacaMarketData,
    *,
    maximum: int = AUTO_UNIVERSE_SAMPLE_SIZE,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Build a point-in-time-capable universe using active and inactive Alpaca assets."""
    _notify(progress, "Loading current movers and most-active stocks for priority coverage…")
    priority: list[str] = []
    try:
        priority.extend(market.movers(top=50))
    except AppError:
        pass
    try:
        priority.extend(market.most_active(top=100))
    except AppError:
        pass
    priority = list(dict.fromkeys(priority))

    try:
        _notify(progress, "Loading Alpaca active + inactive U.S. equity master catalog…")
        catalog = market.equity_catalog()
    except AppError as exc:
        if not priority:
            raise
        return {
            "symbols": priority[:maximum],
            "source": "current_screener_fallback",
            "point_in_time_capable": False,
            "catalog_available": False,
            "population_size": len(priority),
            "priority_symbols": len(priority),
            "selection_bias_warning": (
                "The active/inactive equity master catalog was unavailable, so this run used current "
                "movers/most-active stocks only. Full validation is disabled because historical selection "
                f"bias is material. Alpaca detail: {exc}"
            ),
        }

    sampled, sample_stats = deterministic_catalog_sample(
        catalog,
        maximum=maximum,
        priority=priority,
        inactive_share=AUTO_INACTIVE_SAMPLE_SHARE,
    )
    metadata = {
        str(item.get("symbol") or "").upper(): {
            "status": item.get("status"),
            "exchange": item.get("exchange"),
            "name": item.get("name"),
            "tradable": item.get("tradable"),
        }
        for item in catalog
        if str(item.get("symbol") or "").strip()
    }
    return {
        "symbols": sampled,
        "source": "point_in_time_asset_catalog_sample",
        "point_in_time_capable": True,
        "catalog_available": True,
        "priority_symbols": len(priority),
        "asset_metadata": {symbol: metadata.get(symbol, {}) for symbol in sampled},
        **sample_stats,
        "selection_bias_warning": (
            "Historical membership is inferred from dated market bars for a sample drawn from Alpaca's "
            "active + inactive exchange-listed equity catalog. This materially reduces survivorship bias. "
            "Very old symbols missing from Alpaca's retained asset master or market-data history can still be absent."
        ),
    }


def infer_symbol_lifecycle(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Infer a symbol's observed historical trading life from dated daily bars."""
    series = _daily_rows(rows)
    dates = [str(item.get("timestamp") or "")[:10] for item in series if str(item.get("timestamp") or "")]
    return {
        "first_observed_date": dates[0] if dates else None,
        "last_observed_date": dates[-1] if dates else None,
        "observed_sessions": len(dates),
    }


def select_event_research_window(
    opportunities: list[dict[str, Any]],
    *,
    window_days: int = AUTO_EVENT_WINDOW_DAYS,
    buffer_days: int = AUTO_EVENT_WINDOW_BUFFER_DAYS,
) -> dict[str, Any] | None:
    """Pick a bounded historical window containing the densest/highest-quality opportunity cluster."""
    dated: list[tuple[date, dict[str, Any]]] = []
    for item in opportunities or []:
        try:
            event_date = date.fromisoformat(str(item.get("date") or "")[:10])
        except ValueError:
            continue
        dated.append((event_date, item))
    if not dated:
        return None
    dated.sort(key=lambda pair: pair[0])

    best: tuple[float, date, date, list[dict[str, Any]]] | None = None
    for anchor_date, _ in dated:
        start_date = anchor_date - timedelta(days=max(1, int(buffer_days)))
        end_date = start_date + timedelta(days=max(30, int(window_days)))
        included = [item for event_date, item in dated if start_date <= event_date <= end_date]
        quality = sum(
            1.0
            + min(8.0, max(0.0, safe_float(item.get("relative_volume"), 0.0) or 0.0))
            + min(15.0, abs(safe_float(item.get("day_change_pct"), 0.0) or 0.0) / 2.0)
            for item in included
        )
        candidate = (quality, start_date, end_date, included)
        if best is None or candidate[0] > best[0]:
            best = candidate

    if best is None:
        return None
    quality, start_date, end_date, included = best
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "event_count": len(included),
        "event_dates": [str(item.get("date") or "") for item in included],
        "window_quality": round(quality, 2),
    }


def _window_datetimes(window: dict[str, Any]) -> tuple[datetime, datetime]:
    try:
        start_date = date.fromisoformat(str(window.get("start_date") or ""))
        end_date = date.fromisoformat(str(window.get("end_date") or ""))
    except ValueError as exc:
        raise AppError("Historical event window contains an invalid date.") from exc
    start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start, end


def load_point_in_time_intraday(
    market: AlpacaMarketData,
    opportunities_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    timeframe: str = AUTO_TIMEFRAME,
    progress: Callable[[str], None] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Download only bounded historical windows around actual opportunity clusters."""
    rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
    windows: dict[str, dict[str, Any]] = {}
    items = list(opportunities_by_symbol.items())
    for index, (symbol, opportunities) in enumerate(items, start=1):
        window = select_event_research_window(opportunities)
        if not window:
            continue
        start, end = _window_datetimes(window)
        _notify(
            progress,
            f"Point-in-time intraday window {index}/{len(items)}: {symbol} "
            f"{window['start_date']} → {window['end_date']}…",
        )
        try:
            bars = market.bars(
                [symbol],
                start=start,
                end=end,
                timeframe=timeframe,
                max_pages=24,
            ).get(symbol, [])
        except AppError:
            continue
        if bars:
            rows_by_symbol[symbol] = list(bars)
            windows[symbol] = window
    return rows_by_symbol, windows


def _daily_rows(rows: list[dict[str, Any]]) -> list[dict[str, float | str]]:
    normalized: list[dict[str, float | str]] = []
    for raw in rows:
        close = safe_float(raw.get("c"))
        volume = safe_float(raw.get("v"))
        if close is None or close <= 0 or volume is None or volume < 0:
            continue
        normalized.append(
            {
                "timestamp": str(raw.get("t") or ""),
                "close": close,
                "volume": volume,
            }
        )
    normalized.sort(key=lambda item: str(item["timestamp"]))
    return normalized


def score_historical_opportunities(
    rows: list[dict[str, Any]],
    strategy: dict[str, Any],
) -> dict[str, Any]:
    """Score how often daily history exhibits conditions relevant to one strategy.

    This is a candidate-selection score only. It never uses future trade P/L.
    """
    series = _daily_rows(rows)
    rules = normalize_machine_rules(effective_strategy_for_research(strategy).get("machine_rules"))
    direction = str(strategy.get("direction") or "long").lower()

    price_rule_names = ("min_price", "max_price")
    opportunity_rule_names = (
        "min_day_change_pct",
        "min_relative_volume",
        "min_dollar_volume",
    )
    explicit_daily_rules = [
        name
        for name in (*price_rule_names, *opportunity_rule_names)
        if rules.get(name) is not None
    ]
    explicit_opportunity_rules = [
        name for name in opportunity_rule_names if rules.get(name) is not None
    ]

    events: list[dict[str, Any]] = []
    all_moves: list[float] = []
    all_rvol: list[float] = []
    all_dollar_volume: list[float] = []

    for index in range(1, len(series)):
        row = series[index]
        previous = series[index - 1]
        close = float(row["close"])
        previous_close = float(previous["close"])
        if previous_close <= 0:
            continue
        change = (close / previous_close - 1.0) * 100.0
        prior_volumes = [float(item["volume"]) for item in series[max(0, index - 20) : index]]
        average_volume = sum(prior_volumes) / len(prior_volumes) if prior_volumes else 0.0
        rvol = float(row["volume"]) / average_volume if average_volume > 0 else 0.0
        dollar_volume = close * float(row["volume"])
        all_moves.append(change)
        all_rvol.append(rvol)
        all_dollar_volume.append(dollar_volume)

        checks: list[bool] = []
        if rules.get("min_price") is not None:
            checks.append(close >= float(rules["min_price"]))
        if rules.get("max_price") is not None:
            checks.append(close <= float(rules["max_price"]))
        if rules.get("min_day_change_pct") is not None:
            threshold = float(rules["min_day_change_pct"])
            if direction == "short":
                checks.append(change <= -abs(threshold))
            else:
                checks.append(change >= threshold)
        if rules.get("min_relative_volume") is not None:
            checks.append(rvol >= float(rules["min_relative_volume"]))
        if rules.get("min_dollar_volume") is not None:
            checks.append(dollar_volume >= float(rules["min_dollar_volume"]))

        if explicit_opportunity_rules:
            qualifies = all(checks) if checks else False
        else:
            # Price bounds alone are not an "opportunity." For intraday patterns whose defining
            # trigger cannot be seen in daily bars, require a generic momentum/participation event
            # while still honoring any source-derived price bounds.
            directional_move = abs(change) if direction == "both" else (-change if direction == "short" else change)
            price_checks = []
            if rules.get("min_price") is not None:
                price_checks.append(close >= float(rules["min_price"]))
            if rules.get("max_price") is not None:
                price_checks.append(close <= float(rules["max_price"]))
            qualifies = (
                all(price_checks)
                and directional_move >= 3.0
                and rvol >= 1.5
                and dollar_volume >= 1_000_000
            )

        if qualifies:
            events.append(
                {
                    "date": str(row["timestamp"])[:10],
                    "close": round(close, 4),
                    "day_change_pct": round(change, 2),
                    "relative_volume": round(rvol, 2),
                    "dollar_volume": round(dollar_volume, 2),
                }
            )

    peak_move = max(
        [(-value if direction == "short" else abs(value) if direction == "both" else value) for value in all_moves]
        or [0.0]
    )
    peak_rvol = max(all_rvol or [0.0])
    median_dollar_volume = median(all_dollar_volume) if all_dollar_volume else 0.0
    # Event count dominates. Other components only break ties among similarly eligible symbols.
    score = (
        len(events) * 20.0
        + min(25.0, max(0.0, peak_move))
        + min(20.0, max(0.0, peak_rvol) * 4.0)
        + min(15.0, math.log10(max(1.0, median_dollar_volume)) * 2.0)
    )
    return {
        "score": round(score, 2),
        "event_count": len(events),
        "explicit_daily_rule_count": len(explicit_daily_rules),
        "explicit_opportunity_rule_count": len(explicit_opportunity_rules),
        "candidate_selection_mode": (
            "strategy_daily_rules"
            if explicit_opportunity_rules
            else "generic_momentum_proxy_with_price_filters"
        ),
        "peak_directional_move_pct": round(peak_move, 2),
        "peak_relative_volume": round(peak_rvol, 2),
        "median_dollar_volume": round(median_dollar_volume, 2),
        "events": sorted(
            events,
            key=lambda item: (item["relative_volume"], abs(item["day_change_pct"])),
            reverse=True,
        )[:24],
    }


def rank_historical_opportunities(
    rows_by_symbol: dict[str, list[dict[str, Any]]],
    strategy: dict[str, Any],
    *,
    limit: int = AUTO_SYMBOLS_PER_STRATEGY,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for symbol, rows in rows_by_symbol.items():
        if len(rows or []) < 8:
            continue
        metrics = score_historical_opportunities(rows, strategy)
        if metrics["event_count"] <= 0:
            continue
        ranked.append({"symbol": symbol, **metrics})
    ranked.sort(
        key=lambda item: (
            int(item.get("event_count") or 0),
            float(item.get("score") or 0),
            float(item.get("peak_relative_volume") or 0),
        ),
        reverse=True,
    )
    return ranked[: max(1, int(limit))]


def _backtest_settings_from_dict(raw: dict[str, Any]) -> BacktestSettings:
    allowed = {field.name for field in fields(BacktestSettings)}
    kwargs = {key: value for key, value in (raw or {}).items() if key in allowed}
    settings = BacktestSettings(**kwargs)
    settings.validate()
    return settings


def _automatic_backtest_settings(strategy: dict[str, Any]) -> BacktestSettings:
    rules = normalize_machine_rules(effective_strategy_for_research(strategy).get("machine_rules"))
    allow_extended = False
    for field_name in ("session_start", "session_end"):
        value = str(rules.get(field_name) or "")
        if value:
            try:
                hour, minute = (int(part) for part in value.split(":", 1))
                total = hour * 60 + minute
                if total < 9 * 60 + 30 or total > 16 * 60:
                    allow_extended = True
            except Exception:
                pass
    return BacktestSettings(
        starting_cash=10_000.0,
        risk_per_trade_pct=0.5,
        max_position_pct=20.0,
        allow_extended_hours=allow_extended,
    )


def _automatic_optimization_settings() -> OptimizationSettings:
    return OptimizationSettings(
        max_variants_per_strategy=36,
        finalists_per_strategy=6,
        minimum_training_trades=5,
        minimum_validation_trades=2,
        training_fraction=0.60,
        validation_fraction=0.20,
        maximum_drawdown_pct=15.0,
        selection_mode="validated",
    )


def _global_validation_gate(
    *,
    anchor_report: dict[str, Any],
    strength: dict[str, Any],
    generalization: dict[str, Any],
    walk_forward: dict[str, Any] | None,
    broad_universe: bool,
) -> tuple[str, list[str]]:
    winner = anchor_report.get("winner") or {}
    summary = generalization.get("summary") or {}
    reasons: list[str] = []

    if winner.get("status") != "VALIDATED":
        reasons.append("Anchor optimization did not pass its validation/stress gate.")
    if not bool(strength.get("independently_positive")):
        reasons.append("Validation and untouched holdout were not independently positive.")
    if (safe_float(strength.get("score"), 0.0) or 0.0) < 70.0:
        reasons.append("Robustness score is below the autonomous 70/100 gate.")
    if (safe_float(summary.get("score"), 0.0) or 0.0) < 65.0:
        reasons.append("Cross-stock generalization score is below 65/100.")
    if int(summary.get("active_symbols") or 0) < 3:
        reasons.append("Fewer than three different stocks produced trades with the frozen rules.")
    if (safe_float(summary.get("profitable_symbol_pct"), 0.0) or 0.0) < 60.0:
        reasons.append("The frozen strategy was profitable on fewer than 60% of active test stocks.")
    if int(summary.get("total_trades") or 0) < 20:
        reasons.append("Cross-stock evidence contains fewer than 20 trades.")
    if not broad_universe:
        reasons.append("Only a current-screener fallback universe was available, so selection bias is too high.")

    if walk_forward:
        wf = walk_forward.get("summary") or {}
        if (safe_float(wf.get("profitable_fold_pct"), 0.0) or 0.0) < 50.0:
            reasons.append("Fewer than half of rolling walk-forward folds were profitable.")
        if int(wf.get("external_trade_count") or 0) < 4:
            reasons.append("Walk-forward unseen periods contain fewer than four trades.")

    return ("validated" if not reasons else "research_only"), reasons


def run_autonomous_research(
    market: AlpacaMarketData,
    strategies: list[dict[str, Any]],
    *,
    universe_sample_size: int = AUTO_UNIVERSE_SAMPLE_SIZE,
    deep_strategy_limit: int = AUTO_MAX_DEEP_STRATEGIES,
    symbols_per_strategy: int = AUTO_SYMBOLS_PER_STRATEGY,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run the full no-manual-ticker historical research funnel."""
    eligible = []
    for strategy in strategies:
        readiness = strategy.get("research_readiness") or research_readiness(strategy)
        if readiness.get("label") == "ready_for_backtest":
            eligible.append((strategy, readiness))
    if not eligible:
        raise AppError("No extracted strategies are machine-testable enough for autonomous research yet.")

    universe = build_research_universe(
        market,
        maximum=universe_sample_size,
        progress=progress,
    )
    symbols = list(universe.get("symbols") or [])
    if not symbols:
        raise AppError("Automatic research could not build a stock universe.")

    historical_end = utc_now()
    if market.historical_feed == "sip" and market.live_feed != "sip":
        historical_end -= timedelta(minutes=16)

    _notify(
        progress,
        f"Screening {len(symbols)} active + inactive stocks across about "
        f"{AUTO_DAILY_LOOKBACK_DAYS // 365} years of daily history…",
    )
    daily_rows = _batched_bars(
        market,
        symbols,
        start=historical_end - timedelta(days=AUTO_DAILY_LOOKBACK_DAYS),
        end=historical_end,
        timeframe="1Day",
        batch_size=100,
        max_pages=24,
        progress=progress,
    )
    lifecycle_by_symbol = {
        symbol: infer_symbol_lifecycle(rows)
        for symbol, rows in daily_rows.items()
        if rows
    }
    universe["symbols_with_historical_bars"] = len(lifecycle_by_symbol)
    universe["inactive_symbols_with_historical_bars"] = sum(
        1
        for symbol in lifecycle_by_symbol
        if str(((universe.get("asset_metadata") or {}).get(symbol) or {}).get("status") or "").lower()
        == "inactive"
    )

    discovery: list[dict[str, Any]] = []
    for strategy, readiness in eligible:
        ranked = rank_historical_opportunities(
            daily_rows,
            strategy,
            limit=symbols_per_strategy,
        )
        if not ranked:
            continue
        extraction_confidence = safe_float(strategy.get("confidence"), 0.0) or 0.0
        priority_score = (
            (safe_float(readiness.get("score"), 0.0) or 0.0) * 0.55
            + min(30.0, sum(int(item.get("event_count") or 0) for item in ranked) * 1.5)
            + extraction_confidence * 0.15
        )
        discovery.append(
            {
                "strategy": strategy,
                "readiness": readiness,
                "opportunities": ranked,
                "priority_score": round(priority_score, 2),
            }
        )

    if not discovery:
        raise AppError(
            "The broad historical scan did not find stocks with enough strategy-relevant opportunity events."
        )

    discovery.sort(key=lambda item: float(item.get("priority_score") or 0), reverse=True)
    finalists = discovery[: max(1, int(deep_strategy_limit))]

    opportunities_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for finalist in finalists:
        for item in finalist["opportunities"]:
            symbol = str(item.get("symbol") or "")
            if not symbol:
                continue
            existing_dates = {
                str(event.get("date") or "")
                for event in opportunities_by_symbol.get(symbol, [])
                if isinstance(event, dict)
            }
            merged = list(opportunities_by_symbol.get(symbol, []))
            for event in item.get("events") or []:
                if not isinstance(event, dict):
                    continue
                event_date = str(event.get("date") or "")
                if event_date and event_date not in existing_dates:
                    merged.append(dict(event))
                    existing_dates.add(event_date)
            opportunities_by_symbol[symbol] = merged

    _notify(
        progress,
        f"Selecting historical event windows for {len(opportunities_by_symbol)} finalist stocks…",
    )
    intraday_rows, research_windows = load_point_in_time_intraday(
        market,
        opportunities_by_symbol,
        timeframe=AUTO_TIMEFRAME,
        progress=progress,
    )
    intraday_symbols = list(intraday_rows)

    needs_catalysts = any(
        bool(
            normalize_machine_rules(
                effective_strategy_for_research(item["strategy"]).get("machine_rules")
            ).get("catalyst_required")
        )
        for item in finalists
    )
    catalyst_summary_by_symbol: dict[str, Any] = {}
    if needs_catalysts and intraday_symbols:
        _notify(progress, "Loading point-in-time catalyst news inside each historical research window…")
        for index, symbol in enumerate(intraday_symbols, start=1):
            window = research_windows.get(symbol) or {}
            if not window:
                continue
            start, end = _window_datetimes(window)
            _notify(
                progress,
                f"Historical catalyst window {index}/{len(intraday_symbols)}: {symbol}…",
            )
            try:
                symbol_articles = historical_news(
                    market,
                    [symbol],
                    start=start - timedelta(hours=24),
                    end=end,
                    max_pages=40,
                )
            except AppError:
                symbol_articles = []
            enriched, summary = enrich_bars_with_point_in_time_catalysts(
                list(intraday_rows.get(symbol) or []),
                symbol_articles,
                lookback_hours=24.0,
            )
            intraday_rows[symbol] = enriched
            catalyst_summary_by_symbol[symbol] = summary

    research_results: list[dict[str, Any]] = []
    for finalist_number, finalist in enumerate(finalists, start=1):
        strategy = finalist["strategy"]
        opportunities = finalist["opportunities"]
        candidate_symbols = [
            str(item.get("symbol") or "")
            for item in opportunities
            if intraday_rows.get(str(item.get("symbol") or ""))
        ]
        if not candidate_symbols:
            continue
        anchor = candidate_symbols[0]
        rows = list(intraday_rows.get(anchor) or [])
        effective = effective_strategy_for_research(strategy)
        settings = _automatic_backtest_settings(effective)
        optimizer = _automatic_optimization_settings()

        _notify(
            progress,
            f"Deep research {finalist_number}/{len(finalists)}: optimizing {strategy.get('name')} on {anchor}…",
        )
        report = optimize_stock_strategies(
            rows,
            [effective],
            anchor,
            settings,
            optimizer,
            finalize_holdout=True,
        )
        winner = report.get("winner") or {}
        if not winner:
            continue

        walk_report = None
        try:
            _notify(progress, f"Running rolling walk-forward checks for {strategy.get('name')}…")
            walk_report = walk_forward_validate(
                rows,
                [effective],
                anchor,
                settings,
                optimizer,
                minimum_history_sessions=8,
                test_sessions_per_fold=2,
                max_folds=2,
            )
        except AppError:
            walk_report = None

        strength = validation_strength(report, walk_report)
        frozen = {
            **strategy,
            "validation_status": "validated",
            "validated_rules": winner.get("optimized_rules") or {},
        }
        cross_settings = _backtest_settings_from_dict(
            winner.get("optimized_backtest_settings") or {}
        )
        cross_rows = {
            symbol: list(intraday_rows.get(symbol) or [])
            for symbol in candidate_symbols
            if intraday_rows.get(symbol)
        }
        _notify(progress, f"Testing frozen {strategy.get('name')} rules across {len(cross_rows)} stocks…")
        generalization = cross_stock_generalization(
            cross_rows,
            frozen,
            cross_settings,
        )

        validation_status, gate_reasons = _global_validation_gate(
            anchor_report=report,
            strength=strength,
            generalization=generalization,
            walk_forward=walk_report,
            broad_universe=bool(universe.get("point_in_time_capable")),
        )
        global_score = round(
            (safe_float(strength.get("score"), 0.0) or 0.0) * 0.65
            + (safe_float((generalization.get("summary") or {}).get("score"), 0.0) or 0.0) * 0.35,
            1,
        )
        research_results.append(
            {
                "strategy_id": strategy.get("id"),
                "strategy_name": strategy.get("name"),
                "source_title": strategy.get("source_title"),
                "priority_score": finalist.get("priority_score"),
                "opportunities": opportunities,
                "anchor_symbol": anchor,
                "candidate_symbols": candidate_symbols,
                "research_windows": {
                    symbol: research_windows.get(symbol)
                    for symbol in candidate_symbols
                    if symbol in research_windows
                },
                "symbol_lifecycles": {
                    symbol: lifecycle_by_symbol.get(symbol)
                    for symbol in candidate_symbols
                    if symbol in lifecycle_by_symbol
                },
                "asset_status_by_symbol": {
                    symbol: ((universe.get("asset_metadata") or {}).get(symbol) or {}).get("status")
                    for symbol in candidate_symbols
                },
                "optimization_report": report,
                "walk_forward": walk_report,
                "strength": strength,
                "generalization": generalization,
                "global_score": global_score,
                "validation_status": validation_status,
                "gate_reasons": gate_reasons,
                "catalyst_summary_by_symbol": {
                    symbol: catalyst_summary_by_symbol.get(symbol)
                    for symbol in candidate_symbols
                    if symbol in catalyst_summary_by_symbol
                },
            }
        )

    research_results.sort(
        key=lambda item: (
            item.get("validation_status") == "validated",
            safe_float(item.get("global_score"), 0.0) or 0.0,
        ),
        reverse=True,
    )

    return {
        "generated_at": utc_now().isoformat(),
        "universe": universe,
        "daily_lookback_days": AUTO_DAILY_LOOKBACK_DAYS,
        "event_window_days": AUTO_EVENT_WINDOW_DAYS,
        "intraday_lookback_days": AUTO_EVENT_WINDOW_DAYS,
        "point_in_time_horizon_years": round(AUTO_DAILY_LOOKBACK_DAYS / 365.0, 1),
        "timeframe": AUTO_TIMEFRAME,
        "eligible_strategies": len(eligible),
        "strategies_with_opportunities": len(discovery),
        "deep_strategies_tested": len(research_results),
        "discovery": [
            {
                "strategy_id": item["strategy"].get("id"),
                "strategy_name": item["strategy"].get("name"),
                "priority_score": item.get("priority_score"),
                "opportunities": item.get("opportunities"),
            }
            for item in discovery
        ],
        "results": research_results,
        "limitations": [
            str(universe.get("selection_bias_warning") or ""),
            "Historical opportunity ranking uses only information available in each daily bar and prior-volume history; it does not use future trade P/L.",
            "Point-in-time membership is inferred from actual dated bar availability. Symbol renames, mergers, and corporate actions can create separate ticker identities across time.",
            "The scan samples the active + inactive Alpaca asset master rather than exhaustively downloading every listed security in one run; repeated runs can broaden coverage without requiring manual ticker selection.",
            "Autonomous validation is historical evidence, not a guarantee of future profitability.",
        ],
    }


def merge_autonomous_research_into_library(
    library: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Persist autonomous research outcomes without requiring manual save clicks."""
    data = dict(library or {})
    strategies = [dict(item) for item in data.get("strategies") or [] if isinstance(item, dict)]
    by_id = {str(item.get("id") or ""): item for item in strategies if item.get("id")}

    validation_records: list[dict[str, Any]] = []
    for result in report.get("results") or []:
        strategy_id = str(result.get("strategy_id") or "")
        item = by_id.get(strategy_id)
        if item is None:
            continue
        optimization = result.get("optimization_report") or {}
        winner = optimization.get("winner") or {}
        strength = result.get("strength") or {}
        generalization = result.get("generalization") or {}
        walk = result.get("walk_forward") or {}
        status = str(result.get("validation_status") or "research_only")

        item["validation_status"] = status
        item["optimization_status"] = str(winner.get("status") or "not_run").lower().replace(" ", "_")
        item["last_autonomous_research"] = {
            "generated_at": report.get("generated_at"),
            "anchor_symbol": result.get("anchor_symbol"),
            "candidate_symbols": result.get("candidate_symbols") or [],
            "global_score": result.get("global_score"),
            "robustness_score": strength.get("score"),
            "robustness_label": strength.get("label"),
            "generalization_score": (generalization.get("summary") or {}).get("score"),
            "generalization_label": (generalization.get("summary") or {}).get("label"),
            "validation_status": status,
            "gate_reasons": result.get("gate_reasons") or [],
            "universe_source": (report.get("universe") or {}).get("source"),
        }
        if status == "validated":
            item["validated_rules"] = winner.get("optimized_rules") or {}
            item["validated_backtest_settings"] = winner.get("optimized_backtest_settings") or {}
            item["validated_at"] = report.get("generated_at")
        else:
            item.pop("validated_rules", None)
            item.pop("validated_backtest_settings", None)
            item.pop("validated_at", None)

        run_id = f"auto:{strategy_id}:{result.get('anchor_symbol')}:{report.get('generated_at')}"
        validation_records.append(
            {
                "id": run_id,
                "strategy_id": strategy_id,
                "strategy_name": result.get("strategy_name"),
                "symbol": result.get("anchor_symbol"),
                "generated_at": report.get("generated_at"),
                "timeframe": report.get("timeframe"),
                "history_days": report.get("intraday_lookback_days"),
                "robustness": strength,
                "optimizer_status": winner.get("status"),
                "validation_status": status,
                "training_metrics": winner.get("training_metrics") or {},
                "validation_metrics": winner.get("validation_metrics") or {},
                "holdout_metrics": winner.get("holdout_metrics") or {},
                "stress_metrics": winner.get("stress_metrics") or {},
                "walk_forward_summary": walk.get("summary"),
                "optimized_rules": winner.get("optimized_rules") or {},
                "optimized_backtest_settings": winner.get("optimized_backtest_settings") or {},
                "autonomous": True,
                "global_score": result.get("global_score"),
                "generalization_summary": generalization.get("summary") or {},
                "gate_reasons": result.get("gate_reasons") or [],
            }
        )

    data["strategies"] = strategies
    existing_validation = list(data.get("validation_runs") or [])
    existing_ids = {str(item.get("id") or "") for item in validation_records}
    data["validation_runs"] = (
        validation_records
        + [item for item in existing_validation if str(item.get("id") or "") not in existing_ids]
    )[:250]

    run_record = {
        "id": f"autonomous:{report.get('generated_at')}",
        "generated_at": report.get("generated_at"),
        "kind": "autonomous_research",
        "universe": report.get("universe") or {},
        "daily_lookback_days": report.get("daily_lookback_days"),
        "event_window_days": report.get("event_window_days"),
        "point_in_time_horizon_years": report.get("point_in_time_horizon_years"),
        "intraday_lookback_days": report.get("intraday_lookback_days"),
        "timeframe": report.get("timeframe"),
        "eligible_strategies": report.get("eligible_strategies"),
        "strategies_with_opportunities": report.get("strategies_with_opportunities"),
        "deep_strategies_tested": report.get("deep_strategies_tested"),
        "results": report.get("results") or [],
        "limitations": report.get("limitations") or [],
    }
    previous_runs = [
        item
        for item in data.get("research_runs") or []
        if str(item.get("id") or "") != str(run_record["id"])
    ]
    data["research_runs"] = [run_record, *previous_runs][:30]
    return data
