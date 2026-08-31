"""Cross-stock historical scorecards for market-feature detectors."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Callable

from market_feature_validation import (
    DEFAULT_HORIZONS,
    limit_rows_to_recent_market_sessions,
    run_detector_event_study,
    summarize_detector_events,
)
from youtube_strategy_engine import parse_symbols, split_safe_raw_research_rows


def _sample_quality(event_count: int, symbol_count: int, session_count: int) -> str:
    """Describe evidence breadth without claiming predictive validity."""
    if event_count < 8 or symbol_count < 2 or session_count < 4:
        return "SPARSE"
    if event_count < 20 or symbol_count < 3 or session_count < 8:
        return "LIMITED"
    if event_count < 50 or symbol_count < 5 or session_count < 15:
        return "MODERATE"
    return "BROAD"


def run_detector_scorecards(
    market: Any,
    symbols: list[str],
    *,
    start: Any,
    end: Any,
    timeframe: str = "1Min",
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    swing_radius: int = 3,
    detectors: list[str] | None = None,
    max_pages: int = 40,
    session_limit: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Replay detector events across multiple stocks using one batched bar request."""
    clean = parse_symbols(symbols)
    if not clean:
        return {
            "symbols_requested": 0,
            "symbols_with_data": 0,
            "bars_analyzed": 0,
            "events": [],
            "summary": {},
            "by_symbol": [],
        }

    if progress:
        progress(f"Loading historical {timeframe} bars for {len(clean)} stocks…")
    rows_by_symbol = market.bars(
        clean,
        start=start,
        end=end,
        timeframe=timeframe,
        adjustment="raw",
        max_pages=max_pages,
    )
    if not hasattr(market, "split_actions"):
        raise ValueError(
            "Detector scorecards require split metadata so raw historical "
            "price structure cannot cross an unhandled split boundary."
        )
    split_actions = market.split_actions(
        clean,
        start=start,
        end=end,
    )
    market_data_integrity_by_symbol: dict[str, dict[str, Any]] = {}
    for symbol in clean:
        safe_rows, integrity = split_safe_raw_research_rows(
            list((rows_by_symbol or {}).get(symbol) or []),
            split_actions,
            symbol,
        )
        rows_by_symbol[symbol] = safe_rows
        market_data_integrity_by_symbol[symbol] = integrity

    all_events: list[dict[str, Any]] = []
    by_symbol: list[dict[str, Any]] = []
    total_bars = 0
    total_sessions = 0
    observed_market_sessions: set[str] = set()

    for index, symbol in enumerate(clean, start=1):
        rows = list((rows_by_symbol or {}).get(symbol) or [])
        rows, selected_sessions = limit_rows_to_recent_market_sessions(
            rows,
            session_limit,
        )
        observed_market_sessions.update(
            session for session in selected_sessions if session != "session-0"
        )
        if not rows:
            by_symbol.append(
                {
                    "symbol": symbol,
                    "bars": 0,
                    "sessions": 0,
                    "market_sessions": selected_sessions,
                    "event_count": 0,
                    "detector_counts": {},
                    "market_data_integrity": market_data_integrity_by_symbol.get(symbol) or {},
                }
            )
            continue

        if progress:
            progress(f"Replaying detector history for {symbol} ({index}/{len(clean)})…")
        study = run_detector_event_study(
            rows,
            detectors=detectors,
            horizons=horizons,
            swing_radius=swing_radius,
        )
        symbol_events = []
        for event in study.get("events") or []:
            enriched = dict(event)
            enriched["symbol"] = symbol
            symbol_events.append(enriched)
        all_events.extend(symbol_events)

        detector_counts = Counter(str(event.get("detector") or "unknown") for event in symbol_events)
        bars = int(study.get("bars_analyzed") or 0)
        sessions = int(study.get("sessions_analyzed") or 0)
        total_bars += bars
        total_sessions += sessions
        by_symbol.append(
            {
                "symbol": symbol,
                "bars": bars,
                "sessions": sessions,
                "market_sessions": selected_sessions,
                "event_count": len(symbol_events),
                "detector_counts": dict(detector_counts),
                "market_data_integrity": market_data_integrity_by_symbol.get(symbol) or {},
            }
        )

    summary = summarize_detector_events(all_events, horizons=horizons)
    symbols_by_detector: dict[str, set[str]] = defaultdict(set)
    sessions_by_detector: dict[str, set[tuple[str, str]]] = defaultdict(set)
    market_days_by_detector: dict[str, set[str]] = defaultdict(set)
    symbol_event_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for event in all_events:
        detector = str(event.get("detector") or "unknown")
        symbol = str(event.get("symbol") or "")
        session = str(event.get("session") or "")
        if symbol:
            symbols_by_detector[detector].add(symbol)
            symbol_event_counts[detector][symbol] += 1
        if symbol or session:
            sessions_by_detector[detector].add((symbol, session))
        if session:
            market_days_by_detector[detector].add(session)

    for detector, item in summary.items():
        symbol_count = len(symbols_by_detector.get(detector) or set())
        session_count = len(sessions_by_detector.get(detector) or set())
        market_day_count = len(market_days_by_detector.get(detector) or set())
        event_count = int(item.get("event_count") or 0)
        max_symbol_events = max((symbol_event_counts.get(detector) or {}).values(), default=0)
        max_symbol_share = (
            max_symbol_events / event_count * 100.0
            if event_count > 0
            else 0.0
        )
        item["symbols_with_events"] = symbol_count
        item["sessions_with_events"] = session_count
        item["unique_market_days"] = market_day_count
        item["max_symbol_event_share_pct"] = max_symbol_share
        item["sample_quality"] = _sample_quality(event_count, symbol_count, session_count)

    return {
        "market_data_integrity_contract": "split_safe_raw_v1",
        "market_data_integrity_by_symbol": market_data_integrity_by_symbol,
        "symbols_requested": len(clean),
        "symbols_with_data": sum(1 for item in by_symbol if int(item.get("bars") or 0) > 0),
        "bars_analyzed": total_bars,
        "sessions_analyzed": total_sessions,
        "market_sessions_requested": (
            max(1, int(session_limit)) if session_limit is not None else None
        ),
        "market_sessions_observed": len(observed_market_sessions),
        "market_session_dates": sorted(observed_market_sessions),
        "timeframe": timeframe,
        "horizons": list(sorted({max(1, int(value)) for value in horizons})),
        "events": all_events,
        "summary": summary,
        "by_symbol": by_symbol,
        "note": (
            "Scorecards use actual raw historical prices and restart each symbol at its latest split boundary. "
            "They summarize detector behavior across stocks and sessions. Sample-quality labels "
            "describe breadth only; they do not certify profitability or make a detector eligible for "
            "live scoring."
        ),
    }
