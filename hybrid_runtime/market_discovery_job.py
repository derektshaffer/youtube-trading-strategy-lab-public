"""Desktop adapter for the web app's strategy-to-stock Market Discovery flow."""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Mapping


ProgressCallback = Callable[[float, str, str], None]
CancellationCheck = Callable[[], bool]


class MarketDiscoveryCancelled(RuntimeError):
    pass


def _check_cancelled(cancelled: CancellationCheck) -> None:
    if cancelled():
        raise MarketDiscoveryCancelled("Market Discovery was cancelled")


def _bounded_count(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = 50
    return max(5, min(200, parsed))


def _faithful_strategies(
    library: Mapping[str, Any],
    *,
    include_research: bool,
    strategy_id: str,
) -> tuple[list[dict[str, Any]], int]:
    from trading_intelligence_core import strategy_integrity_report

    usable: list[dict[str, Any]] = []
    blocked = 0
    wanted = str(strategy_id or "").strip()
    for raw in library.get("strategies") or []:
        if not isinstance(raw, Mapping):
            continue
        strategy = dict(raw)
        current_id = str(strategy.get("id") or "").strip()
        if not current_id or (wanted and current_id != wanted):
            continue
        report = strategy_integrity_report(strategy)
        if str(report.get("status") or "").strip().lower() != "faithful":
            blocked += 1
            continue
        validation = str(
            strategy.get("validation_status") or "research_only"
        ).strip().lower()
        if not include_research and validation != "validated":
            continue
        usable.append(strategy)
    return usable, blocked


def _scan_fraction(message: str, *, base: float = 0.38, span: float = 0.54) -> float:
    text = str(message or "")
    match = re.search(r"Batch\s+(\d+)/(\d+)", text, flags=re.IGNORECASE)
    batch_index = 1
    batch_total = 1
    if match:
        batch_index = max(1, int(match.group(1)))
        batch_total = max(batch_index, int(match.group(2)))
    lowered = text.casefold()
    stage = 0.10
    if "relative-volume" in lowered or "baseline" in lowered:
        stage = 0.35
    elif "catalyst" in lowered:
        stage = 0.58
    elif "intraday" in lowered:
        stage = 0.80
    elif "snapshot" in lowered:
        stage = 0.18
    completed = max(0, batch_index - 1)
    return min(0.93, base + span * ((completed + stage) / batch_total))


def run_market_discovery(
    payload: Mapping[str, Any],
    *,
    data_dir: str,
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> dict[str, Any]:
    """Scan the current market against faithful strategy rules.

    This is the same direction as the web app's Market Discovery page:
    strategy rules -> matching stocks. It is intentionally separate from the
    Stock Strategy Finder, which searches stock -> strategy.
    """

    from .desktop_settings import load_desktop_settings
    from .library_source import load_library_for_job
    from .market_cache import load_alpaca_credentials
    from trading_market_discovery import (
        merge_momentum_candidate_universe,
        scan_market_strategies,
    )
    from youtube_strategy_engine import AlpacaMarketData, parse_symbols

    progress(0.05, "downloading_data", "Loading the authoritative strategy library")
    loaded = load_library_for_job(payload, data_dir=data_dir)
    _check_cancelled(cancelled)

    include_research = bool(payload.get("include_research", True))
    selected_strategy_id = str(payload.get("strategy_id") or "").strip()
    strategies, blocked_count = _faithful_strategies(
        loaded.library,
        include_research=include_research,
        strategy_id=selected_strategy_id,
    )
    if not strategies:
        scope = "the selected strategy" if selected_strategy_id else "the current library"
        qualifier = "validated " if not include_research else ""
        raise RuntimeError(
            f"No faithful {qualifier}strategy rules are available in {scope}."
        )
    progress(
        0.18,
        "preparing_features",
        f"Prepared {len(strategies):,} faithful strategy families",
    )
    _check_cancelled(cancelled)

    settings = load_desktop_settings(data_dir)
    api_key, secret_key = load_alpaca_credentials()
    feed = str(payload.get("feed") or settings.market_feed or "sip").strip().lower()
    market = AlpacaMarketData(
        api_key,
        secret_key,
        live_feed=feed,
        historical_feed=feed,
    )

    universe = str(payload.get("universe") or "momentum").strip().lower()
    count = _bounded_count(payload.get("candidate_count"))
    progress(0.26, "downloading_data", "Building the current stock universe")
    _check_cancelled(cancelled)
    if universe == "momentum":
        gainers = market.movers(top=min(50, count))
        active = market.most_active(top=min(100, count))
        symbols = merge_momentum_candidate_universe(gainers, active, limit=count)
        universe_label = "Momentum universe"
    elif universe == "gainers":
        symbols = market.movers(top=min(50, count))
        universe_label = "Top gainers"
    elif universe == "active":
        symbols = market.most_active(top=min(100, count))
        universe_label = "Most active"
    elif universe == "custom":
        custom = payload.get("custom_symbols") or []
        if isinstance(custom, str):
            custom = custom.replace(",", " ").split()
        symbols = parse_symbols(custom)[:count]
        universe_label = "Custom watchlist"
    else:
        raise RuntimeError("Choose Momentum universe, Top gainers, Most active, or Custom watchlist.")
    if not symbols:
        raise RuntimeError("No valid stocks were available for this Market Discovery scan.")

    def scan_progress(message: str) -> None:
        _check_cancelled(cancelled)
        progress(
            _scan_fraction(message),
            "preparing_features",
            str(message or "Comparing stocks with strategy rules"),
        )

    results = scan_market_strategies(
        market,
        symbols,
        strategies,
        progress=scan_progress,
    )
    _check_cancelled(cancelled)
    progress(0.95, "saving", "Preparing ranked stock matches")
    matches = [
        item
        for item in results
        if str(item.get("status") or "").strip().upper() == "MATCH"
    ]
    validated_matches = [
        item
        for item in matches
        if str(item.get("validation_status") or "").strip().lower() == "validated"
    ]
    return {
        "status": "ok",
        "universe": universe,
        "universe_label": universe_label,
        "candidate_symbols": list(symbols),
        "candidate_count": len(symbols),
        "strategy_count": len(strategies),
        "selected_strategy_id": selected_strategy_id,
        "include_research": include_research,
        "integrity_blocked_count": blocked_count,
        "results": results,
        "match_count": len(matches),
        "validated_match_count": len(validated_matches),
        "library": dict(loaded.metadata),
        "feed": feed,
        "research_only": True,
        "affects_live_ranking": False,
        "affects_execution": False,
        "source": "desktop_market_discovery_v1",
    }


def desktop_market_discovery_handler(
    payload: Mapping[str, Any],
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> Mapping[str, Any]:
    data_dir = str(
        os.environ.get("TRADING_INTELLIGENCE_DESKTOP_DATA_DIR") or ""
    ).strip()
    if not data_dir:
        raise RuntimeError("The desktop data directory is unavailable")
    return run_market_discovery(
        payload,
        data_dir=data_dir,
        progress=progress,
        cancelled=cancelled,
    )


__all__ = [
    "MarketDiscoveryCancelled",
    "desktop_market_discovery_handler",
    "run_market_discovery",
]
