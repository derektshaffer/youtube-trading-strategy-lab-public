"""Cloud whole-market latent prescreen for the standalone Explosive Stock Lab."""

from __future__ import annotations

from datetime import datetime, time, timedelta
import os
from typing import Any

from explosive_stock_core import rank_latent_daily_candidates
from explosive_stock_storage import (
    DEFAULT_EXPLOSIVE_BACKUP_PATH,
    build_explosive_store,
)
from youtube_strategy_engine import (
    ET,
    AlpacaMarketData,
    AppError,
    parse_symbols,
    safe_float,
    utc_now,
)

DEFAULT_BACKUP_REPOSITORY = "derektshaffer/derektshaffer-youtube-trading-strategy-lab"
DEFAULT_BATCH_SIZE = 80
DEFAULT_TOP_N = 250
DEFAULT_HISTORY_DAYS = 100
DEFAULT_MAX_UNIVERSE = 12_000


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default)).strip()


def _int_env(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(_env(name, str(default)))
    except ValueError:
        value = int(default)
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _float_env(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return float(default)


def _batches(symbols: list[str], size: int) -> list[list[str]]:
    clean = parse_symbols(symbols)
    return [clean[index : index + size] for index in range(0, len(clean), size)]


def _daily_bar_is_completed(row: dict[str, Any], reference: datetime) -> bool:
    raw = str(row.get("t") or "")
    if not raw:
        return False
    try:
        timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=ET)
    row_day = timestamp.astimezone(ET).date()
    current = reference.astimezone(ET)
    if row_day < current.date():
        return True
    if row_day > current.date() or current.weekday() >= 5:
        return False
    # Give the daily bar time to settle after the regular close.
    return current.time() >= time(16, 15)


def _filter_completed_daily_rows(
    rows: list[dict[str, Any]],
    reference: datetime,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows or []
        if isinstance(row, dict) and _daily_bar_is_completed(row, reference)
    ]


def _snapshot_price(snapshot: dict[str, Any]) -> float | None:
    trade = snapshot.get("latestTrade") or snapshot.get("latest_trade") or {}
    daily = snapshot.get("dailyBar") or snapshot.get("daily_bar") or {}
    return safe_float(trade.get("p")) or safe_float(daily.get("c"))


def run_prescreen() -> dict[str, Any]:
    api_key = _env("ALPACA_API_KEY")
    secret_key = _env("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise AppError("Explosive prescreen needs ALPACA_API_KEY and ALPACA_SECRET_KEY.")

    market = AlpacaMarketData(
        api_key,
        secret_key,
        _env("ALPACA_LIVE_FEED", "iex"),
        _env("ALPACA_HISTORICAL_FEED", "sip"),
    )
    reference = utc_now()
    batch_size = _int_env("EXPLOSIVE_PRESCREEN_BATCH_SIZE", DEFAULT_BATCH_SIZE, maximum=100)
    top_n = _int_env("EXPLOSIVE_PRESCREEN_TOP_N", DEFAULT_TOP_N, maximum=1000)
    history_days = _int_env("EXPLOSIVE_PRESCREEN_HISTORY_DAYS", DEFAULT_HISTORY_DAYS, minimum=35, maximum=365)
    max_universe = _int_env("EXPLOSIVE_PRESCREEN_MAX_UNIVERSE", DEFAULT_MAX_UNIVERSE, maximum=20_000)
    min_price = _float_env("EXPLOSIVE_PRESCREEN_MIN_PRICE", 0.20)
    max_price = _float_env("EXPLOSIVE_PRESCREEN_MAX_PRICE", 30.0)
    min_dollar_volume = _float_env("EXPLOSIVE_PRESCREEN_MIN_AVG_DOLLAR_VOLUME", 100_000.0)

    all_symbols = market.active_equities()[:max_universe]
    if not all_symbols:
        raise AppError("Alpaca returned no active U.S. equities for the explosive prescreen.")

    # Price-only snapshot pass is cheap relative to downloading 100 daily bars
    # for every listed equity. Do not filter on current momentum; quiet names
    # must remain eligible for latent discovery.
    eligible: list[str] = []
    snapshot_failures = 0
    for batch in _batches(all_symbols, batch_size):
        try:
            snapshots = market.snapshots(batch)
        except AppError:
            snapshot_failures += 1
            continue
        for symbol in batch:
            price = _snapshot_price(snapshots.get(symbol) or {})
            if price is not None and min_price <= price <= max_price:
                eligible.append(symbol)

    history_start = reference - timedelta(days=history_days)
    daily_by_symbol: dict[str, list[dict[str, Any]]] = {}
    history_failures = 0
    for batch in _batches(eligible, batch_size):
        try:
            raw = market.bars(
                batch,
                start=history_start,
                end=reference,
                timeframe="1Day",
                feed=market.historical_feed,
                adjustment="raw",
                max_pages=20,
            )
        except AppError:
            history_failures += 1
            continue
        for symbol in batch:
            completed = _filter_completed_daily_rows(raw.get(symbol, []), reference)
            if completed:
                daily_by_symbol[symbol] = completed

    ranked = rank_latent_daily_candidates(
        daily_by_symbol,
        top_n=top_n,
        min_price=min_price,
        max_price=max_price,
        min_average_dollar_volume=min_dollar_volume,
    )

    repository = (
        _env("EXPLOSIVE_STOCK_BACKUP_REPOSITORY")
        or _env("GITHUB_BACKUP_REPOSITORY")
        or DEFAULT_BACKUP_REPOSITORY
    )
    token = (
        _env("EXPLOSIVE_STOCK_BACKUP_TOKEN")
        or _env("GITHUB_BACKUP_TOKEN")
        or _env("GITHUB_TOKEN")
    )
    store = build_explosive_store(
        repository,
        token,
        branch=_env("EXPLOSIVE_STOCK_BACKUP_BRANCH") or _env("GITHUB_BACKUP_BRANCH"),
        path=_env("EXPLOSIVE_STOCK_BACKUP_PATH", DEFAULT_EXPLOSIVE_BACKUP_PATH),
        directory=_env("EXPLOSIVE_STOCK_DATA_DIR", ".cloud_explosive_stock_lab"),
    )
    data = store.load_latest()
    research_system = data.setdefault("research_system", {})
    completed_at = utc_now().isoformat()
    prescreen = {
        "generated_at": completed_at,
        "source": "whole_market_completed_daily_cloud_prescreen",
        "universe_count": len(all_symbols),
        "price_eligible_count": len(eligible),
        "history_covered_count": len(daily_by_symbol),
        "candidate_count": len(ranked),
        "snapshot_failed_batches": snapshot_failures,
        "history_failed_batches": history_failures,
        "history_days": history_days,
        "min_price": min_price,
        "max_price": max_price,
        "min_average_dollar_volume": min_dollar_volume,
        "candidates": ranked,
        "score_is_probability": False,
        "validation_status": "experimental_unvalidated",
    }
    research_system["explosive_prescreen"] = prescreen
    prior_runs = [
        dict(item)
        for item in research_system.get("explosive_prescreen_runs") or []
        if isinstance(item, dict)
    ]
    run_summary = {key: value for key, value in prescreen.items() if key != "candidates"}
    research_system["explosive_prescreen_runs"] = [run_summary, *prior_runs][:14]
    data["research_system"] = research_system
    store.save(data)
    return prescreen


def main() -> int:
    prescreen = run_prescreen()
    print(
        "Explosive prescreen complete: "
        f"{prescreen['universe_count']} active -> "
        f"{prescreen['price_eligible_count']} price-eligible -> "
        f"{prescreen['history_covered_count']} with history -> "
        f"{prescreen['candidate_count']} latent candidates."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
